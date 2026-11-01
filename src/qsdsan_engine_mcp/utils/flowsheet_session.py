"""
Flowsheet Session Manager - Session-based state management for dynamic flowsheet construction.

This module provides persistent session management for flowsheet construction,
storing configuration in the configured session directory (default
`jobs/flowsheets/{session_id}/`, overrideable via
`QSDSAN_ENGINE_SESSIONS_DIR`) for MCP reconnection survival.

Usage:
    from utils.flowsheet_session import (
        FlowsheetSessionManager,
        FlowsheetSession,
        StreamConfig,
        UnitConfig,
        ConnectionConfig,
    )

    manager = FlowsheetSessionManager()

    # Create a new session
    session = manager.create_session(model_type="ASM2d")

    # Add components
    manager.add_stream(session.session_id, StreamConfig(...))
    manager.add_unit(session.session_id, UnitConfig(...))
    manager.add_connection(session.session_id, ConnectionConfig(...))

    # Load existing session
    session = manager.get_session(session_id)
"""

from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Literal
from datetime import datetime
import json
import uuid
import logging
import os

logger = logging.getLogger(__name__)


def _normalize_optional_model_name(model_type: Optional[str]) -> Optional[str]:
    """Normalize model names while preserving ``None``."""
    if model_type is None:
        return None

    from qsdsan_engine_mcp.core.model_registry import normalize_model_name

    return normalize_model_name(model_type)


def _normalize_output_names(unit_type: str, outputs: Optional[List[str]]) -> Optional[List[str]]:
    """Normalize user-facing output names for units with non-obvious outlet ordering."""
    if not outputs:
        return outputs

    if unit_type in {"AnaerobicCSTR", "AnaerobicCSTRmADM1"} and len(outputs) >= 2:
        first = outputs[0] or ""
        second = outputs[1] or ""
        first_lower = first.lower()
        second_lower = second.lower()
        gas_tokens = ("gas", "biogas", "methane")
        first_is_gas = any(token in first_lower for token in gas_tokens)
        second_is_gas = any(token in second_lower for token in gas_tokens)
        if not first_is_gas and second_is_gas:
            normalized = list(outputs)
            normalized[0], normalized[1] = normalized[1], normalized[0]
            return normalized

    return outputs


def _validate_unit_side_config(aux_config: Optional[Dict[str, Any]], source_spec: Optional[Dict[str, Any]]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Validate unit-side wrapper configuration persisted in the session."""
    if aux_config is None:
        aux_config = {}
    if not isinstance(aux_config, dict):
        raise ValueError("aux_config must be a dict")
    if source_spec is not None and not isinstance(source_spec, dict):
        raise ValueError("source_spec must be a dict or null")
    return aux_config, source_spec


@dataclass
class CostingConfig:
    """Session-level strict costing configuration."""

    enabled: bool = False
    tea_profile: str = "qsdsan_simpletea"
    economic_assumptions: Dict[str, Any] = field(default_factory=dict)
    costed_units: List[str] = field(default_factory=list)


@dataclass
class StreamConfig:
    """
    Configuration for a WasteStream.

    Attributes:
        stream_id: Unique stream identifier (e.g., "influent", "RAS")
        flow_m3_d: Flow rate in m³/day
        temperature_K: Temperature in Kelvin
        concentrations: Component ID -> concentration (in concentration_units)
        concentration_units: "mg/L" (default) or "kg/m3"
        stream_type: One of "influent", "recycle", "intermediate"
        model_type: Process model for this stream (e.g., "ASM2d", "mADM1")
    """
    stream_id: str
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
    concentration_units: str = "mg/L"  # "mg/L" (default, practitioner standard) or "kg/m3"
    stream_type: str = "influent"  # "influent", "recycle", "intermediate"
    model_type: Optional[str] = None  # None = inherit from session


@dataclass
class UnitConfig:
    """
    Configuration for a SanUnit.

    Attributes:
        unit_id: Unique unit identifier (e.g., "A1", "O1", "MBR")
        unit_type: Unit type from UNIT_REGISTRY (e.g., "CSTR", "Splitter")
        params: Unit-specific parameters (e.g., {"V_max": 1000})
        inputs: List of input sources (stream IDs or pipe notation)
        outputs: Optional list of output stream names
        model_type: Process model for this unit (e.g., "ASM2d", "mADM1")
        engineering_profile: Public engineering profile identifier
        engineering_inputs: Public process-engineering inputs supplied by the client
        aux_config: Explicit unit-side auxiliary configuration for productized wrappers
        source_spec: Optional source specification for source-like units (e.g., DynamicInfluent)
        auto_inserted: Phase 10: True if unit was auto-inserted by engine (e.g., junction for model mismatch)
    """
    unit_id: str
    unit_type: str
    params: Dict[str, Any]
    inputs: List[str]  # Port notations or stream IDs
    outputs: Optional[List[str]] = None
    model_type: Optional[str] = None  # None = inherit from session
    engineering_profile: str = "default"
    engineering_inputs: Dict[str, Any] = field(default_factory=dict)
    aux_config: Dict[str, Any] = field(default_factory=dict)
    source_spec: Optional[Dict[str, Any]] = None
    auto_inserted: bool = False  # Phase 10: Track auto-inserted junctions


@dataclass
class ConnectionConfig:
    """
    Deferred connection between units.

    Used for recycle streams that can't be specified during unit creation.

    Attributes:
        from_port: Source port notation (e.g., "SP-0", "C1-1")
        to_port: Destination port notation (e.g., "1-A1" or "A1")
        stream_id: Optional stream ID for the connection
    """
    from_port: str  # e.g., "C1-1"
    to_port: str
    stream_id: Optional[str] = None  # Optional name for the connection stream


@dataclass
class FlowsheetSession:
    """
    Flowsheet construction session.

    Stores all configuration for building a QSDsan System.

    Attributes:
        session_id: Unique session identifier
        primary_model_type: Default model for new units/streams
        model_types: Set of all models used in session (auto-tracked)
        streams: stream_id -> StreamConfig
        units: unit_id -> UnitConfig
        connections: List of deferred connections
        created_at: ISO timestamp of creation
        updated_at: ISO timestamp of last update
        status: Session status
    """
    session_id: str
    primary_model_type: str
    model_types: Set[str] = field(default_factory=set)
    streams: Dict[str, StreamConfig] = field(default_factory=dict)
    units: Dict[str, UnitConfig] = field(default_factory=dict)
    connections: List[ConnectionConfig] = field(default_factory=list)
    stream_roles: Dict[str, List[str]] = field(default_factory=dict)
    startup_metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "building"  # "building", "compiled", "failed"
    costing: CostingConfig = field(default_factory=CostingConfig)

    def __post_init__(self):
        """Ensure model_types includes primary."""
        self.primary_model_type = _normalize_optional_model_name(self.primary_model_type) or self.primary_model_type
        self.model_types = {
            _normalize_optional_model_name(model) or model
            for model in self.model_types
        }
        if self.primary_model_type:
            self.model_types.add(self.primary_model_type)

    def save(self, path: Path) -> None:
        """Save session to JSON file."""
        data = self._to_dict()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Saved session {self.session_id} to {path}")

    def _to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "primary_model_type": self.primary_model_type,
            "model_types": list(self.model_types),
            "streams": {k: asdict(v) for k, v in self.streams.items()},
            "units": {k: asdict(v) for k, v in self.units.items()},
            "connections": [asdict(c) for c in self.connections],
            "stream_roles": self.stream_roles,
            "startup_metadata": self.startup_metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "costing": asdict(self.costing),
        }

    @classmethod
    def load(cls, path: Path) -> "FlowsheetSession":
        """Load session from JSON file."""
        with open(path) as f:
            data = json.load(f)

        # Reconstruct nested dataclasses
        streams = {
            k: StreamConfig(**v) for k, v in data.get("streams", {}).items()
        }
        units = {
            k: UnitConfig(**v) for k, v in data.get("units", {}).items()
        }
        connections = [
            ConnectionConfig(**c) for c in data.get("connections", [])
        ]

        return cls(
            session_id=data["session_id"],
            primary_model_type=data["primary_model_type"],
            model_types=set(data.get("model_types", [])),
            streams=streams,
            units=units,
            connections=connections,
            stream_roles=data.get("stream_roles", {}),
            startup_metadata=data.get("startup_metadata", {}),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            status=data.get("status", "building"),
            costing=CostingConfig(**data.get("costing", {})),
        )


class FlowsheetSessionManager:
    """
    Manage flowsheet construction sessions.

    Sessions are persisted to disk in `{sessions_dir}/flowsheets/{session_id}/`.
    """

    def __init__(self, sessions_dir: Path = None):
        """
        Initialize session manager.

        Args:
            sessions_dir: Base directory for sessions. Defaults to QSDSAN_ENGINE_SESSIONS_DIR
                          environment variable, or ./jobs if not set.
        """
        if sessions_dir is None:
            # Honor QSDSAN_ENGINE_SESSIONS_DIR environment variable (used by tests)
            env_dir = os.environ.get("QSDSAN_ENGINE_SESSIONS_DIR")
            sessions_dir = Path(env_dir) if env_dir else Path("jobs")
        self.sessions_dir = Path(sessions_dir)
        self.flowsheets_dir = self.sessions_dir / "flowsheets"
        self.flowsheets_dir.mkdir(parents=True, exist_ok=True)

    def _get_session_dir(self, session_id: str) -> Path:
        """Get directory for a session with ID and path traversal protection."""
        from qsdsan_engine_mcp.utils.path_utils import validate_safe_path, validate_id
        # First validate ID contains only safe characters
        validate_id(session_id, "session_id")
        # Then validate path doesn't escape base directory
        return validate_safe_path(self.flowsheets_dir, session_id, "session_id")

    def _get_session_file(self, session_id: str) -> Path:
        """Get session.json path for a session."""
        return self._get_session_dir(session_id) / "session.json"

    def create_session(
        self,
        model_type: str,
        session_id: Optional[str] = None,
    ) -> FlowsheetSession:
        """
        Create a new flowsheet construction session.

        Args:
            model_type: Primary process model (e.g., "ASM2d", "mADM1")
            session_id: Optional custom session ID. Auto-generates if not provided.

        Returns:
            New FlowsheetSession

        Raises:
            ValueError: If session_id already exists
        """
        model_type = _normalize_optional_model_name(model_type) or model_type

        # Generate session ID if not provided
        if session_id is None:
            session_id = str(uuid.uuid4())[:8]

        # Check for existing session
        session_dir = self._get_session_dir(session_id)
        if session_dir.exists():
            raise ValueError(
                f"Session '{session_id}' already exists. "
                "Use get_session() to load or choose a different ID."
            )

        # Create session directory
        session_dir.mkdir(parents=True, exist_ok=True)

        # Create session
        session = FlowsheetSession(
            session_id=session_id,
            primary_model_type=model_type,
        )

        # Save to disk
        session.save(self._get_session_file(session_id))

        logger.info(f"Created flowsheet session {session_id} with model {model_type}")
        return session

    def get_session(self, session_id: str) -> FlowsheetSession:
        """
        Load an existing session.

        Args:
            session_id: Session identifier

        Returns:
            FlowsheetSession

        Raises:
            ValueError: If session not found
        """
        session_file = self._get_session_file(session_id)

        if not session_file.exists():
            available = self.list_sessions()
            raise ValueError(
                f"Session '{session_id}' not found. "
                f"Available sessions: {[s['session_id'] for s in available]}"
            )

        return FlowsheetSession.load(session_file)

    def _save_session(self, session: FlowsheetSession) -> None:
        """Save session to disk with updated timestamp."""
        session.updated_at = datetime.now().isoformat()
        session.save(self._get_session_file(session.session_id))

    def _reset_status_after_mutation(self, session: FlowsheetSession) -> Dict[str, bool]:
        """
        Reset mutable sessions back to ``building`` after any successful edit.

        A failed compile should not trap the user in ``failed`` after they size,
        rewire, or otherwise modify the flowsheet.
        """
        status_before = session.status
        if status_before in {"compiled", "failed"}:
            session.status = "building"
            build_config_path = self._get_build_config_file(session.session_id)
            if build_config_path.exists():
                try:
                    with open(build_config_path) as f:
                        build_config = json.load(f)
                    build_config["status"] = "building"
                    build_config["invalidated_at"] = datetime.now().isoformat()
                    with open(build_config_path, "w") as f:
                        json.dump(build_config, f, indent=2)
                except (OSError, json.JSONDecodeError):
                    logger.warning(
                        "Failed to invalidate build_config.json for session %s",
                        session.session_id,
                    )
        return {
            "was_compiled": status_before == "compiled",
            "was_failed": status_before == "failed",
        }

    def _get_build_config_file(self, session_id: str) -> Path:
        """Return the build configuration file for a session."""
        return self._get_session_dir(session_id) / "build_config.json"

    def _get_system_result_file(self, session_id: str) -> Path:
        """Return the detailed build result file for a session."""
        return self._get_session_dir(session_id) / "system_result.json"

    def _get_build_error_file(self, session_id: str) -> Path:
        """Return the persisted build error metadata file for a session."""
        return self._get_session_dir(session_id) / "build_error.json"

    def _compile_engineering_inputs(self, config: UnitConfig) -> Dict[str, Any]:
        """Compile public engineering inputs into internal unit fields."""
        from qsdsan_engine_mcp.core.unit_registry import compile_engineering_inputs

        if not config.engineering_inputs:
            return {
                "params": dict(config.params),
                "aux_config": dict(config.aux_config),
                "source_spec": config.source_spec,
                "applied_defaults": {},
                "errors": [],
                "warnings": [],
            }

        compiled = compile_engineering_inputs(config.unit_type, config.engineering_inputs)
        if compiled["errors"]:
            raise ValueError("; ".join(compiled["errors"]))

        config.params = compiled["params"]
        config.aux_config = compiled["aux_config"]
        config.source_spec = compiled["source_spec"]
        return compiled

    def save_build_metadata(
        self,
        session_id: str,
        build_config: Optional[Dict[str, Any]] = None,
        build_result: Optional[Dict[str, Any]] = None,
        build_error: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Persist build metadata for later inspection and recovery.

        Successful builds clear the stale build-error file. Failed builds keep
        the attempted configuration plus the last compiler error.
        """
        if build_config is not None:
            with open(self._get_build_config_file(session_id), "w") as f:
                json.dump(build_config, f, indent=2)

        if build_result is not None:
            with open(self._get_system_result_file(session_id), "w") as f:
                json.dump(build_result, f, indent=2)

        build_error_file = self._get_build_error_file(session_id)
        if build_error is not None:
            with open(build_error_file, "w") as f:
                json.dump(build_error, f, indent=2)
        elif build_error_file.exists():
            build_error_file.unlink()

    def record_last_job(self, session_id: str, job_id: str) -> None:
        """Persist the most recent simulation job ID for session-scoped lookups."""
        build_config_path = self._get_build_config_file(session_id)
        if not build_config_path.exists():
            return
        try:
            with open(build_config_path) as f:
                build_config = json.load(f)
            build_config["last_job_id"] = job_id
            build_config["last_job_recorded_at"] = datetime.now().isoformat()
            with open(build_config_path, "w") as f:
                json.dump(build_config, f, indent=2)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to record last job %s for session %s", job_id, session_id)

    def get_last_job_id(self, session_id: str) -> Optional[str]:
        """Return the last recorded simulation job ID for a session, if any."""
        build_config_path = self._get_build_config_file(session_id)
        if not build_config_path.exists():
            return None
        try:
            with open(build_config_path) as f:
                build_config = json.load(f)
            return build_config.get("last_job_id")
        except (OSError, json.JSONDecodeError):
            return None

    def enable_costing(
        self,
        session_id: str,
        tea_profile: str = "qsdsan_simpletea",
        economic_assumptions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Enable strict costing for a flowsheet session."""
        session = self.get_session(session_id)
        session.costing = CostingConfig(
            enabled=True,
            tea_profile=tea_profile,
            economic_assumptions=economic_assumptions or {},
            costed_units=list(session.costing.costed_units),
        )
        self._save_session(session)
        return {
            "session_id": session_id,
            "enabled": True,
            "tea_profile": tea_profile,
            "economic_assumptions": session.costing.economic_assumptions,
            "costed_units": list(session.costing.costed_units),
        }

    def add_unit_costing(self, session_id: str, unit_id: str) -> Dict[str, Any]:
        """Add one unit to the strict public costing set."""
        from qsdsan_engine_mcp.core.unit_registry import get_unit_spec

        session = self.get_session(session_id)
        if not session.costing.enabled:
            raise ValueError("Flowsheet costing not enabled. Call enable_costing() first.")
        if unit_id not in session.units:
            raise ValueError(f"Unit '{unit_id}' not found in session")

        spec = get_unit_spec(session.units[unit_id].unit_type)
        if not spec.costing.costing_supported:
            raise ValueError(
                f"Unit '{unit_id}' ({spec.unit_type}) is not cost-supported on the strict public surface."
            )

        if unit_id not in session.costing.costed_units:
            session.costing.costed_units.append(unit_id)
        self._save_session(session)
        return {
            "session_id": session_id,
            "unit_id": unit_id,
            "unit_type": spec.unit_type,
            "costing_supported": True,
            "costing_package": spec.costing.costing_package,
            "costing_basis": spec.costing.costing_basis,
            "costed_units": list(session.costing.costed_units),
        }

    def list_costed_units(self, session_id: str) -> Dict[str, Any]:
        """Return the currently selected costed units for a session."""
        from qsdsan_engine_mcp.core.unit_registry import get_unit_spec

        session = self.get_session(session_id)
        units = []
        for unit_id in session.costing.costed_units:
            if unit_id not in session.units:
                continue
            unit_config = session.units[unit_id]
            spec = get_unit_spec(unit_config.unit_type)
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_type": unit_config.unit_type,
                    "costing_supported": spec.costing.costing_supported,
                    "costing_basis": spec.costing.costing_basis,
                }
            )
        return {
            "session_id": session_id,
            "enabled": session.costing.enabled,
            "tea_profile": session.costing.tea_profile,
            "economic_assumptions": session.costing.economic_assumptions,
            "costed_units": units,
        }

    def find_session_by_system_id(self, system_id: str) -> Optional[str]:
        """Return the compiled session ID that owns the given ``system_id``."""
        for session_dir in self.flowsheets_dir.iterdir():
            if not session_dir.is_dir():
                continue
            build_config_path = session_dir / "build_config.json"
            if not build_config_path.exists():
                continue
            try:
                with open(build_config_path) as f:
                    build_config = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if build_config.get("system_id") == system_id:
                return session_dir.name
        return None

    def add_stream(
        self,
        session_id: str,
        config: StreamConfig,
    ) -> Dict[str, Any]:
        """
        Add a stream to the session.

        Args:
            session_id: Session identifier
            config: Stream configuration

        Returns:
            Dict with stream_id and validation status
        """
        session = self.get_session(session_id)

        # Check for duplicate
        if config.stream_id in session.streams:
            raise ValueError(
                f"Stream '{config.stream_id}' already exists in session"
            )

        # Set model type if not specified
        if config.model_type is None:
            config.model_type = session.primary_model_type
        else:
            config.model_type = _normalize_optional_model_name(config.model_type)

        # Track model type
        if config.model_type:
            session.model_types.add(config.model_type)

        # Add stream
        session.streams[config.stream_id] = config
        status_reset = self._reset_status_after_mutation(session)

        # Save
        self._save_session(session)

        logger.info(f"Added stream '{config.stream_id}' to session {session_id}")
        return {
            "stream_id": config.stream_id,
            "status": "added",
            "model_type": config.model_type,
            "session_status": session.status,
            **status_reset,
        }

    def set_stream_roles(
        self,
        session_id: str,
        stream_id: str,
        roles: List[str],
    ) -> Dict[str, Any]:
        """Persist semantic roles for a named stream or unit output."""
        from qsdsan_engine_mcp.utils.stream_roles import add_stream_roles

        session = self.get_session(session_id)
        add_stream_roles(session.stream_roles, stream_id, roles)
        status_reset = self._reset_status_after_mutation(session)
        self._save_session(session)
        return {
            "stream_id": stream_id,
            "roles": list(session.stream_roles.get(stream_id, [])),
            "status": "updated",
            "session_status": session.status,
            **status_reset,
        }

    def set_startup_state(
        self,
        session_id: str,
        startup_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist startup policy and per-unit initialization metadata."""
        if not isinstance(startup_metadata, dict):
            raise ValueError("startup_metadata must be a dict")

        session = self.get_session(session_id)
        session.startup_metadata = deepcopy(startup_metadata)
        status_reset = self._reset_status_after_mutation(session)
        self._save_session(session)
        return {
            "session_id": session_id,
            "startup_metadata": deepcopy(session.startup_metadata),
            "session_status": session.status,
            **status_reset,
        }

    def get_startup_state(self, session_id: str) -> Dict[str, Any]:
        """Return persisted startup metadata for a session."""
        session = self.get_session(session_id)
        return deepcopy(session.startup_metadata)

    def set_dynamic_checkpoint(
        self,
        session_id: str,
        checkpoint: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist a full-state dynamic checkpoint WITHOUT invalidating the build.

        A converged dynamic twin can write its checkpoint back to the session so
        a later process can resume the transient with no re-equilibration. Unlike
        :meth:`set_startup_state`, this is a *runtime* write: it stores the
        checkpoint under ``startup_metadata['dynamic_checkpoint']`` and does NOT
        call ``_reset_status_after_mutation`` -- the compiled system stays valid
        (writing a checkpoint is not a flowsheet edit).
        """
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be a dict")

        session = self.get_session(session_id)
        session.startup_metadata = dict(session.startup_metadata or {})
        session.startup_metadata["dynamic_checkpoint"] = deepcopy(checkpoint)
        # NOTE: deliberately no _reset_status_after_mutation -- preserve build.
        self._save_session(session)
        return {
            "session_id": session_id,
            "checkpoint_time_days": checkpoint.get("time_days"),
            "schema_version": checkpoint.get("schema_version"),
            "session_status": session.status,
            "build_invalidated": False,
        }

    def get_dynamic_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the persisted dynamic checkpoint for a session, if any."""
        session = self.get_session(session_id)
        checkpoint = (session.startup_metadata or {}).get("dynamic_checkpoint")
        return deepcopy(checkpoint) if checkpoint is not None else None

    def add_unit(
        self,
        session_id: str,
        config: UnitConfig,
    ) -> Dict[str, Any]:
        """
        Add a unit to the session.

        Args:
            session_id: Session identifier
            config: Unit configuration

        Returns:
            Dict with unit_id and validation status
        """
        session = self.get_session(session_id)

        # Check for duplicate
        if config.unit_id in session.units:
            raise ValueError(
                f"Unit '{config.unit_id}' already exists in session"
            )

        # Set model type if not specified
        if config.model_type is None:
            config.model_type = session.primary_model_type
        else:
            config.model_type = _normalize_optional_model_name(config.model_type)

        # Track model type
        if config.model_type:
            session.model_types.add(config.model_type)

        from qsdsan_engine_mcp.core.unit_registry import validate_unit_bootstrap

        compiled = self._compile_engineering_inputs(config)
        config.aux_config, config.source_spec = _validate_unit_side_config(
            config.aux_config,
            config.source_spec,
        )
        bootstrap_errors, _ = validate_unit_bootstrap(
            config.unit_type,
            aux_config=config.aux_config,
            source_spec=config.source_spec,
        )
        if bootstrap_errors:
            raise ValueError("; ".join(bootstrap_errors))
        config.outputs = _normalize_output_names(config.unit_type, config.outputs)

        # Add unit
        session.units[config.unit_id] = config
        status_reset = self._reset_status_after_mutation(session)

        # Save
        self._save_session(session)

        logger.info(f"Added unit '{config.unit_id}' ({config.unit_type}) to session {session_id}")
        return {
            "unit_id": config.unit_id,
            "unit_type": config.unit_type,
            "status": "added",
            "model_type": config.model_type,
            "engineering_profile": config.engineering_profile,
            "engineering_inputs": config.engineering_inputs,
            "applied_engineering_defaults": compiled.get("applied_defaults", {}),
            "warnings": compiled.get("warnings", []),
            "has_aux_config": bool(config.aux_config),
            "has_source_spec": config.source_spec is not None,
            "session_status": session.status,
            **status_reset,
        }

    def add_connection(
        self,
        session_id: str,
        config: ConnectionConfig,
    ) -> Dict[str, Any]:
        """
        Add a deferred connection to the session.

        Args:
            session_id: Session identifier
            config: Connection configuration

        Returns:
            Dict with connection info and validation status
        """
        session = self.get_session(session_id)

        if not config.stream_id:
            from qsdsan_engine_mcp.utils.flowsheet_refs import infer_connection_stream_id

            inferred_stream_id = infer_connection_stream_id(session.units, config.from_port)
            if inferred_stream_id:
                config.stream_id = inferred_stream_id

        # Add connection
        session.connections.append(config)
        status_reset = self._reset_status_after_mutation(session)

        # Save
        self._save_session(session)

        logger.info(
            f"Added connection {config.from_port} -> {config.to_port} "
            f"to session {session_id}"
        )
        return {
            "from": config.from_port,
            "to": config.to_port,
            "stream_id": config.stream_id,
            "status": "added",
            "session_status": session.status,
            **status_reset,
        }

    def update_stream(
        self,
        session_id: str,
        stream_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update a stream in the session (patch-style).

        Modifying a stream resets session status to 'building' if it was 'compiled'.

        Args:
            session_id: Session identifier
            stream_id: Stream to update
            updates: Dict of fields to update. Valid fields:
                     flow_m3_d, temperature_K, concentrations, stream_type, model_type

        Returns:
            Dict with stream_id, updated fields, and session status

        Raises:
            ValueError: If stream not found or invalid fields
        """
        session = self.get_session(session_id)

        if stream_id not in session.streams:
            raise ValueError(f"Stream '{stream_id}' not found in session")

        config = session.streams[stream_id]
        valid_fields = {
            'flow_m3_d',
            'temperature_K',
            'concentrations',
            'concentration_units',
            'stream_type',
            'model_type',
        }
        updated_fields = []

        for key, value in updates.items():
            if key not in valid_fields:
                raise ValueError(f"Invalid field '{key}'. Valid fields: {valid_fields}")

            if key == 'concentrations':
                # Merge concentrations rather than replace
                if not isinstance(value, dict):
                    raise ValueError("concentrations must be a dict")
                config.concentrations.update(value)
            else:
                setattr(config, key, value)
            updated_fields.append(key)

        if "model_type" in updates:
            config.model_type = _normalize_optional_model_name(config.model_type)

        status_reset = self._reset_status_after_mutation(session)

        self._save_session(session)

        logger.info(f"Updated stream '{stream_id}' in session {session_id}: {updated_fields}")
        return {
            "stream_id": stream_id,
            "status": "updated",
            "updated_fields": updated_fields,
            "session_status": session.status,
            **status_reset,
        }

    def update_unit(
        self,
        session_id: str,
        unit_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update a unit in the session (patch-style).

        Modifying a unit resets session status to 'building' if it was 'compiled'.

        Args:
            session_id: Session identifier
            unit_id: Unit to update
            updates: Dict of fields to update. Valid fields:
                     params (merged), inputs, outputs, model_type

        Returns:
            Dict with unit_id, updated fields, and session status

        Raises:
            ValueError: If unit not found or invalid fields
        """
        session = self.get_session(session_id)

        if unit_id not in session.units:
            raise ValueError(f"Unit '{unit_id}' not found in session")

        config = session.units[unit_id]
        valid_fields = {
            'params',
            'inputs',
            'outputs',
            'model_type',
            'aux_config',
            'source_spec',
            'engineering_profile',
            'engineering_inputs',
        }
        updated_fields = []

        for key, value in updates.items():
            if key not in valid_fields:
                raise ValueError(f"Invalid field '{key}'. Valid fields: {valid_fields}")

            if key == 'params':
                # Merge params rather than replace
                if not isinstance(value, dict):
                    raise ValueError("params must be a dict")
                config.params.update(value)
            elif key == 'engineering_inputs':
                if value is None:
                    config.engineering_inputs = {}
                elif not isinstance(value, dict):
                    raise ValueError("engineering_inputs must be a dict")
                else:
                    config.engineering_inputs.update(value)
            elif key == 'aux_config':
                if value is None:
                    config.aux_config = {}
                elif not isinstance(value, dict):
                    raise ValueError("aux_config must be a dict")
                else:
                    config.aux_config.update(value)
            else:
                setattr(config, key, value)
            updated_fields.append(key)

        if "model_type" in updates:
            config.model_type = _normalize_optional_model_name(config.model_type)
        from qsdsan_engine_mcp.core.unit_registry import validate_unit_bootstrap
        compiled = self._compile_engineering_inputs(config)
        config.aux_config, config.source_spec = _validate_unit_side_config(
            config.aux_config,
            config.source_spec,
        )
        bootstrap_errors, _ = validate_unit_bootstrap(
            config.unit_type,
            aux_config=config.aux_config,
            source_spec=config.source_spec,
        )
        if bootstrap_errors:
            raise ValueError("; ".join(bootstrap_errors))

        status_reset = self._reset_status_after_mutation(session)

        self._save_session(session)

        logger.info(f"Updated unit '{unit_id}' in session {session_id}: {updated_fields}")
        return {
            "unit_id": unit_id,
            "status": "updated",
            "updated_fields": updated_fields,
            "engineering_profile": config.engineering_profile,
            "engineering_inputs": config.engineering_inputs,
            "applied_engineering_defaults": compiled.get("applied_defaults", {}),
            "warnings": compiled.get("warnings", []),
            "has_aux_config": bool(config.aux_config),
            "has_source_spec": config.source_spec is not None,
            "session_status": session.status,
            **status_reset,
        }

    def delete_stream(
        self,
        session_id: str,
        stream_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete a stream from the session.

        By default, fails if any units reference this stream in their inputs.
        Use force=True to delete anyway (removes stream from unit inputs too).

        Args:
            session_id: Session identifier
            stream_id: Stream to delete
            force: If True, also remove from unit inputs

        Returns:
            Dict with deletion status

        Raises:
            ValueError: If stream not found or referenced by units (unless force=True)
        """
        session = self.get_session(session_id)

        if stream_id not in session.streams:
            raise ValueError(f"Stream '{stream_id}' not found in session")

        # Check for references in unit inputs
        referencing_units = []
        for unit_id, config in session.units.items():
            if stream_id in config.inputs:
                referencing_units.append(unit_id)

        if referencing_units and not force:
            raise ValueError(
                f"Stream '{stream_id}' is referenced by units: {referencing_units}. "
                f"Use force=True to delete anyway."
            )

        # Remove from unit inputs if force
        if force and referencing_units:
            for unit_id in referencing_units:
                config = session.units[unit_id]
                config.inputs = [i for i in config.inputs if i != stream_id]

        # Delete the stream
        del session.streams[stream_id]
        session.stream_roles.pop(stream_id, None)

        status_reset = self._reset_status_after_mutation(session)

        self._save_session(session)

        logger.info(f"Deleted stream '{stream_id}' from session {session_id}")
        return {
            "stream_id": stream_id,
            "status": "deleted",
            "removed_from_units": referencing_units if force else [],
            "session_status": session.status,
            **status_reset,
        }

    def delete_unit(
        self,
        session_id: str,
        unit_id: str,
    ) -> Dict[str, Any]:
        """
        Delete a unit from the session.

        Also removes any connections referencing this unit.

        Args:
            session_id: Session identifier
            unit_id: Unit to delete

        Returns:
            Dict with deletion status and removed connections

        Raises:
            ValueError: If unit not found
        """
        session = self.get_session(session_id)

        if unit_id not in session.units:
            raise ValueError(f"Unit '{unit_id}' not found in session")

        # Remove connections referencing this unit
        removed_connections = []
        remaining_connections = []
        for conn in session.connections:
            # Check if connection references this unit
            from_unit = conn.from_port.split("-")[0]
            to_unit = conn.to_port.split("-")[-1] if conn.to_port else None

            if from_unit == unit_id or to_unit == unit_id:
                removed_connections.append({
                    "from": conn.from_port,
                    "to": conn.to_port,
                })
            else:
                remaining_connections.append(conn)

        session.connections = remaining_connections

        # Delete the unit
        for output_name in session.units[unit_id].outputs or []:
            session.stream_roles.pop(output_name, None)
        del session.units[unit_id]

        status_reset = self._reset_status_after_mutation(session)

        self._save_session(session)

        logger.info(f"Deleted unit '{unit_id}' from session {session_id}")
        return {
            "unit_id": unit_id,
            "status": "deleted",
            "removed_connections": removed_connections,
            "session_status": session.status,
            **status_reset,
        }

    def delete_connection(
        self,
        session_id: str,
        from_port: str,
        to_port: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Delete a specific connection from the session.

        Args:
            session_id: Session identifier
            from_port: Source port of connection to delete
            to_port: Destination port

        Returns:
            Dict with deletion status

        Raises:
            ValueError: If connection not found
        """
        session = self.get_session(session_id)

        # Find and remove the connection
        found = False
        remaining = []
        for conn in session.connections:
            if conn.from_port == from_port and (to_port is None or conn.to_port == to_port):
                found = True
            else:
                remaining.append(conn)

        if not found:
            raise ValueError(
                f"Connection from '{from_port}' to '{to_port}' not found in session"
            )

        session.connections = remaining

        status_reset = self._reset_status_after_mutation(session)

        self._save_session(session)

        logger.info(f"Deleted connection {from_port} -> {to_port} from session {session_id}")
        return {
            "from": from_port,
            "to": to_port,
            "status": "deleted",
            "session_status": session.status,
            **status_reset,
        }

    def clone_session(
        self,
        source_session_id: str,
        new_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Clone a session for experimentation.

        Creates a copy of the session with a new ID, reset to 'building' status.
        Only copies session.json, not build artifacts.

        Args:
            source_session_id: Session to clone
            new_session_id: Optional custom ID for new session

        Returns:
            Dict with new session info

        Raises:
            ValueError: If source not found or new_session_id exists
        """
        source = self.get_session(source_session_id)

        # Generate new session ID
        if new_session_id is None:
            new_session_id = str(uuid.uuid4())[:8]

        # Check new session doesn't exist
        new_dir = self._get_session_dir(new_session_id)
        if new_dir.exists():
            raise ValueError(f"Session '{new_session_id}' already exists")

        # Create new session directory
        new_dir.mkdir(parents=True, exist_ok=True)

        # Clone session data (deep copy via dict round-trip)
        new_session = FlowsheetSession(
            session_id=new_session_id,
            primary_model_type=source.primary_model_type,
            model_types=set(source.model_types),
            streams={
                k: StreamConfig(**asdict(v)) for k, v in source.streams.items()
            },
            units={
                k: UnitConfig(**asdict(v)) for k, v in source.units.items()
            },
            connections=[
                ConnectionConfig(**asdict(c)) for c in source.connections
            ],
            stream_roles=dict(source.stream_roles),
            startup_metadata=deepcopy(source.startup_metadata),
            status="building",  # Always reset to building
            costing=CostingConfig(**asdict(source.costing)),
        )

        new_session.save(self._get_session_file(new_session_id))

        logger.info(f"Cloned session {source_session_id} to {new_session_id}")
        return {
            "source_session_id": source_session_id,
            "new_session_id": new_session_id,
            "status": "cloned",
            "n_streams": len(new_session.streams),
            "n_units": len(new_session.units),
            "n_connections": len(new_session.connections),
        }

    def update_session_status(
        self,
        session_id: str,
        status: Literal["building", "compiled", "failed"],
    ) -> None:
        """Update session status."""
        session = self.get_session(session_id)
        session.status = status
        self._save_session(session)

    def list_sessions(
        self,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all flowsheet sessions.

        Args:
            status_filter: Optional filter by status

        Returns:
            List of session summaries
        """
        sessions = []

        for session_dir in self.flowsheets_dir.iterdir():
            if not session_dir.is_dir():
                continue

            session_file = session_dir / "session.json"
            if not session_file.exists():
                continue

            try:
                session = FlowsheetSession.load(session_file)

                if status_filter and session.status != status_filter:
                    continue

                sessions.append({
                    "session_id": session.session_id,
                    "primary_model_type": session.primary_model_type,
                    "model_types": list(session.model_types),
                    "n_streams": len(session.streams),
                    "n_units": len(session.units),
                    "n_connections": len(session.connections),
                    "has_startup_metadata": bool(session.startup_metadata),
                    "status": session.status,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                })
            except Exception as e:
                logger.warning(f"Error loading session {session_dir.name}: {e}")

        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted, False if not found
        """
        import shutil

        session_dir = self._get_session_dir(session_id)
        if not session_dir.exists():
            return False

        shutil.rmtree(session_dir)
        logger.info(f"Deleted session {session_id}")
        return True

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """
        Get a detailed summary of a session.

        Returns full stream and unit configurations for deep introspection
        by autonomous agents.

        Args:
            session_id: Session identifier

        Returns:
            Dict with session summary including full stream/unit details:
            - streams: Dict with flow, temperature, concentrations, stream_type
            - units: Dict with unit_type, params, inputs, outputs, model_type
            - connections: List with from, to, stream_id
        """
        session = self.get_session(session_id)

        return {
            "session_id": session.session_id,
            "primary_model_type": session.primary_model_type,
            "model_types": list(session.model_types),
            "status": session.status,
            "costing": asdict(session.costing),
            "stream_roles": dict(session.stream_roles),
            "startup_metadata": deepcopy(session.startup_metadata),
            "streams": {
                sid: {
                    "flow_m3_d": sconfig.flow_m3_d,
                    "temperature_K": sconfig.temperature_K,
                    "concentrations": sconfig.concentrations,
                    "concentration_units": getattr(sconfig, 'concentration_units', 'mg/L'),
                    "stream_type": sconfig.stream_type,
                    "model_type": sconfig.model_type,
                }
                for sid, sconfig in session.streams.items()
            },
            "units": {
                uid: {
                    "unit_type": uconfig.unit_type,
                    "params": uconfig.params,
                    "inputs": uconfig.inputs,
                    "outputs": uconfig.outputs,
                    "model_type": uconfig.model_type,
                    "engineering_profile": uconfig.engineering_profile,
                    "engineering_inputs": uconfig.engineering_inputs,
                    "auto_inserted": getattr(uconfig, 'auto_inserted', False),  # Phase 10
                }
                for uid, uconfig in session.units.items()
            },
            "connections": [
                {"from": c.from_port, "to": c.to_port, "stream_id": c.stream_id}
                for c in session.connections
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'StreamConfig',
    'UnitConfig',
    'ConnectionConfig',
    'CostingConfig',
    'FlowsheetSession',
    'FlowsheetSessionManager',
]
