"""Compile shared ``SimulationSeed`` payloads into session build instructions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from qsdsan_engine_mcp.core.engineering_utils_bridge import ensure_engineering_utils_importable

ensure_engineering_utils_importable()

from puran_mcp_support.engineering.models.simulation_seed import SimulationSeed
from puran_mcp_support.engineering.topology import ResolvedSeedConnection, resolve_seed_connections

from qsdsan_engine_mcp.core.model_registry import normalize_model_name
from qsdsan_engine_mcp.core.unit_registry import get_unit_spec
from qsdsan_engine_mcp.utils.flowsheet_session import ConnectionConfig
from qsdsan_engine_mcp.utils.stream_roles import add_stream_roles, invert_stream_roles


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_PLACEHOLDER_COMPONENTS = {
    "ASM1": {"S_S": 0.0},
    "ASM2d": {"S_F": 0.0},
    "mASM2d": {"S_F": 0.0},
    "ADM1": {"S_su": 0.0},
    "mADM1": {"S_su": 0.0},
}

_WATERTAP_DYNAMIC_SEED_UNIT_MAP = {
    "CSTR": "CSTR",
    "CSTR_Injection": "CSTR",
    "Separator": "Splitter",
    "MBRSeparator": "CompletelyMixedMBR",
    "SecondaryClarifierASM": "FlatBottomCircularClarifier",
    "Mixer": "Mixer",
}

_QSDSAN_CLASS_BY_UNIT_TYPE = {
    "CSTR": "qsdsan.sanunits.CSTR",
    "Splitter": "qsdsan.sanunits.Splitter",
    "CompletelyMixedMBR": "qsdsan.sanunits.CompletelyMixedMBR",
    "FlatBottomCircularClarifier": "qsdsan.sanunits.FlatBottomCircularClarifier",
    "Mixer": "qsdsan.sanunits.Mixer",
}

_DIRECT_PORT_ROLE_MAP: Dict[str, Tuple[str, ...]] = {
    "effluent": ("effluent",),
    "permeate": ("permeate",),
    "concentrate": ("concentrate",),
    "distillate": ("distillate",),
    "brine": ("brine",),
    "filtrate": ("filtrate",),
    "biogas": ("biogas",),
    "regen_waste": ("regen_waste",),
    "vent": ("vent",),
}
_EXTERNAL_INLET_ROLE_MAP: Dict[str, Tuple[str, ...]] = {
    "feed": ("influent",),
    "makeup": ("makeup", "methanol_feed", "external_carbon"),
}
_SOLIDS_RETURN_UNIT_TYPES = {
    "FlatBottomCircularClarifier",
}
_PRIMARY_SLUDGE_UNIT_TYPES = {
    "PrimaryClarifier",
}


class SeedRoleAssignmentError(ValueError):
    """Raised when v2 seed port roles cannot be mapped to system roles."""


def _safe_id(value: str) -> str:
    cleaned = _SAFE_ID_RE.sub("_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "seed"


def infer_seed_model_type(seed: SimulationSeed) -> str:
    """Resolve the engine model family encoded in a simulation seed."""
    if seed.engine_key in {"WaterTAP-ASM2D", "WaterTAP-ModifiedASM2D"}:
        return "mASM2d"
    if seed.engine_key == "WaterTAP-ASM1":
        return "ASM1"
    if seed.engine_key in {"WaterTAP-ADM1", "WaterTAP-ModifiedADM1"}:
        return "mADM1"

    if seed.kinetic_model:
        return normalize_model_name(seed.kinetic_model)

    family = seed.influent_component_hints.get("qsdsan_component_family")
    if family:
        return normalize_model_name(str(family))

    if seed.engine_key == "ADM1":
        return "ADM1"
    return "ASM2d"


def _bridgeable_watertap_seed(seed: SimulationSeed) -> bool:
    return (
        str(seed.engine_key).startswith("WaterTAP-")
        and seed.engine_key in {
            "WaterTAP-ASM1",
            "WaterTAP-ASM2D",
            "WaterTAP-ModifiedASM2D",
            "WaterTAP-ADM1",
            "WaterTAP-ModifiedADM1",
        }
        and (seed.unit_type or "") in _WATERTAP_DYNAMIC_SEED_UNIT_MAP
    )


def _dynamic_engine_key_for_seed(seed: SimulationSeed) -> str:
    if seed.engine_key in {"WaterTAP-ADM1", "WaterTAP-ModifiedADM1"}:
        return "ADM1"
    return "ASM2d"


def _dynamic_engineering_inputs(seed: SimulationSeed, unit_type: str, model_type: str) -> Dict[str, Any]:
    raw = dict(seed.engineering_inputs or {})
    raw.update({k: v for k, v in (seed.constructor_kwargs or {}).items() if k not in raw})
    converted: Dict[str, Any] = {}

    def copy_if_present(src: str, dest: str | None = None) -> None:
        if src in raw and raw[src] is not None:
            converted[dest or src] = raw[src]

    if unit_type == "CSTR":
        copy_if_present("V_max")
        copy_if_present("volume_m3", "V_max")
        copy_if_present("V", "V_max")
        if seed.unit_type == "CSTR_Injection" or seed.influent_component_hints.get("aerated"):
            converted["aeration"] = float(raw.get("do_mg_l") or raw.get("DO") or 2.0)
            converted["DO_ID"] = "S_O2" if model_type != "ASM1" else "S_O"
    elif unit_type == "CompletelyMixedMBR":
        copy_if_present("V_max")
        copy_if_present("volume_m3", "V_max")
        copy_if_present("V", "V_max")
        copy_if_present("solids_capture_rate")
        copy_if_present("pumped_flow")
        copy_if_present("pumped_flow_m3_d", "pumped_flow")
        converted.setdefault("aeration", 2.0)
        converted.setdefault("DO_ID", "S_O2" if model_type != "ASM1" else "S_O")
        converted.setdefault("gas_stripping", False)
    elif unit_type == "Splitter":
        copy_if_present("split")
    elif unit_type == "FlatBottomCircularClarifier":
        for key in (
            "surface_area",
            "height",
            "underflow",
            "wastage",
            "X_threshold",
            "v_max",
            "v_max_practical",
            "N_layer",
            "feed_layer",
        ):
            copy_if_present(key)
    elif unit_type == "Mixer":
        converted = {}

    # Preserve seed-only operational metadata for SRT control and audit without
    # feeding it into QSDsan constructors; unknown public inputs are ignored by
    # the registry but remain available on UnitConfig.engineering_inputs.
    for key in ("target_srt_days", "target_mlss_mg_l", "q_was_m3_d", "return_fraction", "internal_recycle_fraction"):
        if key in seed.engineering_inputs:
            converted[key] = seed.engineering_inputs[key]
    return converted


def _adapt_seed_for_qsdsan(seed: SimulationSeed) -> SimulationSeed | None:
    if seed.qsdsan_class is not None:
        return seed
    if not _bridgeable_watertap_seed(seed):
        return None

    unit_type = _WATERTAP_DYNAMIC_SEED_UNIT_MAP[str(seed.unit_type)]
    model_type = infer_seed_model_type(seed)
    hints = dict(seed.influent_component_hints or {})
    hints["qsdsan_component_family"] = model_type
    return seed.model_copy(
        update={
            "engine_key": _dynamic_engine_key_for_seed(seed),
            "unit_type": unit_type,
            "qsdsan_class": _QSDSAN_CLASS_BY_UNIT_TYPE[unit_type],
            "kinetic_model": model_type,
            "engineering_inputs": _dynamic_engineering_inputs(seed, unit_type, model_type),
            "constructor_kwargs": {},
            "influent_component_hints": hints,
        }
    )


def _placeholder_concentrations(model_type: str) -> Dict[str, float]:
    return dict(_PLACEHOLDER_COMPONENTS.get(model_type, _PLACEHOLDER_COMPONENTS["ASM2d"]))


def _port_role(port: Any, *, seed_id: str, stream_id: str) -> str:
    role = getattr(port, "role", None)
    if not isinstance(role, str) or not role.strip():
        raise SeedRoleAssignmentError(
            f"simulation seed {seed_id!r} port {stream_id!r} is missing "
            "SimulationSeedPort.role; v2 seed consumers require typed roles."
        )
    return role.strip()


def _producer_has_inlet_role(seed: SimulationSeed, *roles: str) -> bool:
    wanted = set(roles)
    return any(inlet.role in wanted for inlet in seed.stream_topology.inlets)


def _is_secondary_solids_split_seed(seed: SimulationSeed) -> bool:
    """Identify WaterTAP-derived MBR/clarifier solids splitters after adaptation."""

    if seed.unit_type != "Splitter":
        return False
    if "sludge_splitter" in seed.seed_id:
        return True
    has_sludge_outlet = any(outlet.role == "sludge" for outlet in seed.stream_topology.outlets)
    if has_sludge_outlet and seed.engineering_inputs.get("q_was_m3_d") is not None:
        return True
    return any(":concentrate" in inlet.stream_id for inlet in seed.stream_topology.inlets)


def _connection_target_indices(
    *,
    seed: SimulationSeed,
    outlet: Any,
    producer_index: int,
    seed_index: Mapping[str, int],
    resolved_connections_by_outlet: Mapping[Tuple[str, str], Sequence[ResolvedSeedConnection]],
) -> list[Tuple[int, str, bool]]:
    targets: list[Tuple[int, str, bool]] = []
    for connection in resolved_connections_by_outlet.get((seed.seed_id, outlet.stream_id), ()):
        downstream_seed_id = connection.sink_seed_id
        if downstream_seed_id not in seed_index:
            raise SeedRoleAssignmentError(
                f"simulation seed {seed.seed_id!r} outlet {outlet.stream_id!r} "
                f"connects_to unknown downstream seed {downstream_seed_id!r}."
            )
        consumer_index = seed_index[downstream_seed_id]
        targets.append(
            (
                consumer_index,
                connection.downstream_inlet_role,
                bool(connection.tear_stream) or consumer_index <= producer_index,
            )
        )
    return targets


def _system_roles_for_return_port(
    *,
    seed: SimulationSeed,
    outlet: Any,
    producer_index: int,
    seed_index: Mapping[str, int],
    resolved_connections_by_outlet: Mapping[Tuple[str, str], Sequence[ResolvedSeedConnection]],
) -> Tuple[str, ...]:
    targets = _connection_target_indices(
        seed=seed,
        outlet=outlet,
        producer_index=producer_index,
        seed_index=seed_index,
        resolved_connections_by_outlet=resolved_connections_by_outlet,
    )
    has_recycle_target = any(is_recycle for _, _, is_recycle in targets)
    if not has_recycle_target:
        raise SeedRoleAssignmentError(
            f"simulation seed {seed.seed_id!r} return outlet {outlet.stream_id!r} "
            "does not target an upstream inlet or declare connects_to.tear_stream=True."
        )

    if (
        seed.unit_type in _SOLIDS_RETURN_UNIT_TYPES
        or _producer_has_inlet_role(
        seed,
        "concentrate",
        "sludge",
        )
        or _is_secondary_solids_split_seed(seed)
    ):
        return ("ras",)

    if any(inlet_role == "return" and is_recycle for _, inlet_role, is_recycle in targets):
        return ("internal_recycle",)

    raise SeedRoleAssignmentError(
        f"simulation seed {seed.seed_id!r} return outlet {outlet.stream_id!r} "
        "cannot be classified as ras or internal_recycle from v2 port context."
    )


def _system_roles_for_sludge_port(
    seed: SimulationSeed,
    outlet: Any,
    resolved_connections_by_outlet: Mapping[Tuple[str, str], Sequence[ResolvedSeedConnection]],
) -> Tuple[str, ...]:
    connection = getattr(outlet, "connects_to", None)
    downstream_seed_id = getattr(connection, "downstream_seed_id", "") if connection else ""
    materialized_downstream_ids = {
        edge.sink_seed_id
        for edge in resolved_connections_by_outlet.get((seed.seed_id, outlet.stream_id), ())
    }

    if seed.unit_type in _PRIMARY_SLUDGE_UNIT_TYPES:
        return ("primary_sludge", "sludge")
    if (
        seed.unit_type in _SOLIDS_RETURN_UNIT_TYPES
        or _producer_has_inlet_role(seed, "concentrate")
        or _is_secondary_solids_split_seed(seed)
    ):
        return ("was", "sludge")
    if isinstance(downstream_seed_id, str) and downstream_seed_id.startswith("sludge_handling."):
        return ("was", "sludge")
    if any(seed_id.startswith("sludge_handling.") for seed_id in materialized_downstream_ids):
        return ("was", "sludge")
    if seed.seed_id.startswith("tertiary.") or seed.seed_id.startswith("tertiary_"):
        return ("tertiary_sludge", "sludge")
    return ("sludge",)


def _system_roles_for_port(
    *,
    seed: SimulationSeed,
    port: Any,
    producer_index: int,
    seed_index: Mapping[str, int],
    resolved_connections_by_outlet: Mapping[Tuple[str, str], Sequence[ResolvedSeedConnection]],
    external_inlet: bool = False,
) -> List[str]:
    """
    Map SimulationSeedPort.role to runtime system roles from the v2 contract.

    Context-sensitive rows:
    - return + recycle target from a solids separator -> ras
    - return + recycle target from a process-liquid splitter -> internal_recycle
    - sludge from secondary solids separation -> was
    - sludge from primary clarification -> primary_sludge
    """

    role = _port_role(port, seed_id=seed.seed_id, stream_id=port.stream_id)
    if external_inlet:
        return list(_EXTERNAL_INLET_ROLE_MAP.get(role, (role,)))
    if role == "return":
        return list(
            _system_roles_for_return_port(
                seed=seed,
                outlet=port,
                producer_index=producer_index,
                seed_index=seed_index,
                resolved_connections_by_outlet=resolved_connections_by_outlet,
            )
        )
    if role == "sludge":
        return list(
            _system_roles_for_sludge_port(
                seed,
                port,
                resolved_connections_by_outlet,
            )
        )
    if role in _DIRECT_PORT_ROLE_MAP:
        return list(_DIRECT_PORT_ROLE_MAP[role])
    raise SeedRoleAssignmentError(
        f"simulation seed {seed.seed_id!r} port {port.stream_id!r} has role "
        f"{role!r}, which is not valid for an outlet system role."
    )


@dataclass
class ExternalStreamPlan:
    source_name: str
    stream_id: str
    model_type: str
    roles: List[str]
    flow_m3_d: float = 0.0
    temperature_K: float = 293.15
    concentrations: Dict[str, float] = field(default_factory=dict)


@dataclass
class UnitBuildPlan:
    seed_id: str
    unit_id: str
    unit_type: str
    model_type: str
    engineering_inputs: Dict[str, Any]
    inputs: List[str]
    outputs: List[str]
    stream_topology: Dict[str, Any] | None = None


@dataclass
class SimulationSeedBuildPlan:
    primary_model_type: str
    seeds: List[SimulationSeed]
    units: List[UnitBuildPlan]
    external_streams: List[ExternalStreamPlan]
    connections: List[ConnectionConfig]
    stream_roles: Dict[str, List[str]]
    recycle_stream_ids: List[str]

    @property
    def roles_by_name(self) -> Dict[str, List[str]]:
        return invert_stream_roles(self.stream_roles)


def _normalize_bfd_edges(
    bfd_edges: Sequence[Mapping[str, Any]] | None,
) -> Dict[Tuple[str, str], Mapping[str, Any]]:
    edges: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for edge in bfd_edges or ():
        to_seed_id = str(edge.get("to_seed_id") or "").strip()
        to_inlet = str(edge.get("to_inlet") or "").strip()
        if not to_seed_id or not to_inlet:
            continue
        edges[(to_seed_id, to_inlet)] = edge
    return edges


def _allocate_unit_ids(seeds: Sequence[SimulationSeed]) -> Dict[str, str]:
    assigned: Dict[str, str] = {}
    used = set()
    for seed in seeds:
        base = _safe_id(seed.unit_id_hint or seed.seed_id)
        candidate = base
        counter = 2
        while candidate in used:
            candidate = f"{base}_{counter}"
            counter += 1
        used.add(candidate)
        assigned[seed.seed_id] = candidate
    return assigned


def _merged_engineering_inputs(seed: SimulationSeed) -> Dict[str, Any]:
    """
    Merge supported constructor kwargs into public engineering inputs.

    The shared seed contract still carries simulator-native constructor kwargs
    for auditability. The MCP bridge can only pass validated engineering
    inputs into ``create_unit``, so pull through the subset that the engine
    exposes publicly for the target unit type.
    """

    merged = dict(seed.engineering_inputs)
    if not seed.unit_type or not seed.constructor_kwargs:
        return merged

    try:
        spec = get_unit_spec(seed.unit_type)
    except Exception:
        return merged

    allowed_keys = set(spec.required_params) | set(spec.optional_params)
    for key, value in seed.constructor_kwargs.items():
        if key in allowed_keys and key not in merged:
            merged[key] = value
    return merged


def _build_recycle_connections(
    *,
    seeds: Sequence[SimulationSeed],
    unit_ids: Mapping[str, str],
    recycle_stream_ids: Iterable[str],
) -> List[ConnectionConfig]:
    """Create deferred producer-output -> consumer-inlet wiring for recycles."""

    outlets_by_stream_id: Dict[str, Tuple[SimulationSeed, int]] = {}
    inlets_by_stream_id: Dict[str, List[Tuple[SimulationSeed, int]]] = defaultdict(list)

    for seed in seeds:
        for outlet_index, outlet in enumerate(seed.stream_topology.outlets):
            outlets_by_stream_id[outlet.stream_id] = (seed, outlet_index)
        for inlet_index, inlet in enumerate(seed.stream_topology.inlets):
            inlets_by_stream_id[inlet.stream_id].append((seed, inlet_index))

    connections: List[ConnectionConfig] = []
    for stream_id in sorted(set(recycle_stream_ids)):
        producer = outlets_by_stream_id.get(stream_id)
        if producer is None:
            raise ValueError(
                f"Recycle stream {stream_id!r} is not produced by any simulation seed outlet."
            )

        consumers = inlets_by_stream_id.get(stream_id, [])
        if not consumers:
            raise ValueError(
                f"Recycle stream {stream_id!r} is not consumed by any simulation seed inlet."
            )

        producer_seed, outlet_index = producer
        producer_unit_id = unit_ids[producer_seed.seed_id]
        for consumer_seed, inlet_index in consumers:
            consumer_unit_id = unit_ids[consumer_seed.seed_id]
            connections.append(
                ConnectionConfig(
                    from_port=f"{producer_unit_id}-{outlet_index}",
                    to_port=f"{inlet_index}-{consumer_unit_id}",
                    stream_id=stream_id,
                )
            )

    return connections


def build_plan_from_simulation_seeds(
    simulation_seeds: Sequence[Mapping[str, Any] | SimulationSeed],
    *,
    bfd_edges: Sequence[Mapping[str, Any]] | None = None,
    model_type: str | None = None,
) -> SimulationSeedBuildPlan:
    """Convert raw simulation seeds into a deterministic session build plan."""

    parsed_seeds = [
        seed if isinstance(seed, SimulationSeed) else SimulationSeed(**seed)
        for seed in simulation_seeds
    ]
    seeds = [
        adapted
        for seed in parsed_seeds
        if (adapted := _adapt_seed_for_qsdsan(seed)) is not None
    ]
    if not seeds:
        raise ValueError("No QSDsan-compatible dynamic simulation seeds were provided.")

    unit_ids = _allocate_unit_ids(seeds)
    used_stream_ids = set()
    stream_roles: Dict[str, List[str]] = {}
    external_streams: Dict[str, ExternalStreamPlan] = {}
    seed_index = {seed.seed_id: index for index, seed in enumerate(seeds)}
    resolved_connections_by_outlet: Dict[
        Tuple[str, str],
        List[ResolvedSeedConnection],
    ] = defaultdict(list)
    for edge in resolve_seed_connections(seeds):
        resolved_connections_by_outlet[(edge.source_seed_id, edge.stream_id)].append(edge)
    _ = _normalize_bfd_edges(bfd_edges)

    for producer_index, seed in enumerate(seeds):
        topology = seed.stream_topology
        for outlet in topology.outlets:
            stream_id = outlet.stream_id
            if stream_id in used_stream_ids:
                raise ValueError(f"Duplicate outlet stream_id in simulation seeds: {stream_id!r}")
            used_stream_ids.add(stream_id)
            add_stream_roles(
                stream_roles,
                stream_id,
                _system_roles_for_port(
                    seed=seed,
                    port=outlet,
                    producer_index=producer_index,
                    seed_index=seed_index,
                    resolved_connections_by_outlet=resolved_connections_by_outlet,
                ),
            )

    units: List[UnitBuildPlan] = []
    recycle_stream_ids = set()

    for producer_index, seed in enumerate(seeds):
        topology = seed.stream_topology
        inputs: List[str] = []
        for inlet in topology.inlets:
            stream_id = inlet.stream_id
            if stream_id not in used_stream_ids:
                model_for_stream = infer_seed_model_type(seed)
                if stream_id not in external_streams:
                    external_roles = _system_roles_for_port(
                        seed=seed,
                        port=inlet,
                        producer_index=producer_index,
                        seed_index=seed_index,
                        resolved_connections_by_outlet=resolved_connections_by_outlet,
                        external_inlet=True,
                    )
                    external_streams[stream_id] = ExternalStreamPlan(
                        source_name=stream_id,
                        stream_id=stream_id,
                        model_type=model_for_stream,
                        roles=external_roles,
                        concentrations=_placeholder_concentrations(model_for_stream),
                    )
                ext = external_streams[stream_id]
                add_stream_roles(stream_roles, ext.stream_id, ext.roles)
            inputs.append(stream_id)

        output_ids = []
        for outlet in topology.outlets:
            stream_id = outlet.stream_id
            output_ids.append(stream_id)
            stream_role_set = set(stream_roles.get(stream_id, ()))
            if stream_role_set & {"ras", "internal_recycle", "sludge_return"}:
                recycle_stream_ids.add(stream_id)

        units.append(
            UnitBuildPlan(
                seed_id=seed.seed_id,
                unit_id=unit_ids[seed.seed_id],
                unit_type=seed.unit_type or "",
                model_type=infer_seed_model_type(seed),
                engineering_inputs=_merged_engineering_inputs(seed),
                inputs=inputs,
                outputs=output_ids,
                stream_topology=seed.stream_topology.model_dump(),
            )
        )

    primary_model_type = normalize_model_name(model_type or infer_seed_model_type(seeds[0]))
    connections = _build_recycle_connections(
        seeds=seeds,
        unit_ids=unit_ids,
        recycle_stream_ids=recycle_stream_ids,
    )

    return SimulationSeedBuildPlan(
        primary_model_type=primary_model_type,
        seeds=seeds,
        units=units,
        external_streams=list(external_streams.values()),
        connections=connections,
        stream_roles=stream_roles,
        recycle_stream_ids=sorted(recycle_stream_ids),
    )


__all__ = [
    "ExternalStreamPlan",
    "SimulationSeedBuildPlan",
    "SeedRoleAssignmentError",
    "UnitBuildPlan",
    "build_plan_from_simulation_seeds",
    "infer_seed_model_type",
]
