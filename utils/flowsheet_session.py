"""
Flowsheet Session Manager - Session-based state management for dynamic flowsheet construction.

This module provides persistent session management for flowsheet construction,
storing configuration in `jobs/flowsheets/{session_id}/` for MCP reconnection survival.

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

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Literal
from datetime import datetime
import json
import uuid
import logging
import os

logger = logging.getLogger(__name__)


@dataclass
class StreamConfig:
    """
    Configuration for a WasteStream.

    Attributes:
        stream_id: Unique stream identifier (e.g., "influent", "RAS")
        flow_m3_d: Flow rate in m³/day
        temperature_K: Temperature in Kelvin
        concentrations: Component ID → concentration (mg/L)
        stream_type: One of "influent", "recycle", "intermediate"
        model_type: Process model for this stream (e.g., "ASM2d", "mADM1")
    """
    stream_id: str
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
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
    """
    unit_id: str
    unit_type: str
    params: Dict[str, Any]
    inputs: List[str]  # Port notations or stream IDs
    outputs: Optional[List[str]] = None
    model_type: Optional[str] = None  # None = inherit from session


@dataclass
class ConnectionConfig:
    """
    Deferred connection between units.

    Used for recycle streams that can't be specified during unit creation.

    Attributes:
        from_port: Source port notation (e.g., "SP-0", "C1-1", "U1-U2")
        to_port: Destination port notation (e.g., "A1-1"). Optional for direct notation.
        stream_id: Optional stream ID for the connection
    """
    from_port: str  # e.g., "C1-1" or direct "U1-U2"
    to_port: Optional[str] = None  # Optional for direct notation (U1-U2 or U1-0-1-U2)
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
        streams: stream_id → StreamConfig
        units: unit_id → UnitConfig
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
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "building"  # "building", "compiled", "failed"

    def __post_init__(self):
        """Ensure model_types includes primary."""
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
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
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            status=data.get("status", "building"),
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
        """Get directory for a session."""
        return self.flowsheets_dir / session_id

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

        # Track model type
        if config.model_type:
            session.model_types.add(config.model_type)

        # Add stream
        session.streams[config.stream_id] = config

        # Save
        self._save_session(session)

        logger.info(f"Added stream '{config.stream_id}' to session {session_id}")
        return {
            "stream_id": config.stream_id,
            "status": "added",
            "model_type": config.model_type,
        }

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

        # Track model type
        if config.model_type:
            session.model_types.add(config.model_type)

        # Add unit
        session.units[config.unit_id] = config

        # Save
        self._save_session(session)

        logger.info(f"Added unit '{config.unit_id}' ({config.unit_type}) to session {session_id}")
        return {
            "unit_id": config.unit_id,
            "unit_type": config.unit_type,
            "status": "added",
            "model_type": config.model_type,
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

        # Add connection
        session.connections.append(config)

        # Save
        self._save_session(session)

        logger.info(
            f"Added connection {config.from_port} → {config.to_port} "
            f"to session {session_id}"
        )
        return {
            "from": config.from_port,
            "to": config.to_port,
            "stream_id": config.stream_id,
            "status": "added",
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
        Get a summary of a session.

        Args:
            session_id: Session identifier

        Returns:
            Dict with session summary including units and streams
        """
        session = self.get_session(session_id)

        return {
            "session_id": session.session_id,
            "primary_model_type": session.primary_model_type,
            "model_types": list(session.model_types),
            "status": session.status,
            "streams": list(session.streams.keys()),
            "units": {
                uid: {
                    "type": uconfig.unit_type,
                    "inputs": uconfig.inputs,
                    "outputs": uconfig.outputs,
                }
                for uid, uconfig in session.units.items()
            },
            "connections": [
                {"from": c.from_port, "to": c.to_port}
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
    'FlowsheetSession',
    'FlowsheetSessionManager',
]
