"""
Exact projection helpers between repo-native states and upstream ADM1p/mASM2d.

These helpers deliberately separate:
1. The upstream-compatible core state used by QSDsan A1 junction units.
2. Carried residual species that exist in the repo's richer state spaces but
   do not have exact counterparts in the upstream core basis.

Residuals are preserved explicitly in conversion metadata instead of being
dropped or proxied into approximate buckets.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Tuple

from qsdsan_engine_mcp.core.contracts.stream_state import StreamState
from qsdsan_engine_mcp.core.model_registry import ASM2D_COMPONENTS, MADM1_COMPONENTS, MASM2D_COMPONENTS
from qsdsan_engine_mcp.core.model_types import ModelType, model_type_value
from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_qsdsan_processes_load_from_file


EPS = 1e-12


def _nonzero(concentrations: Dict[str, float]) -> Dict[str, float]:
    return {
        component_id: float(value)
        for component_id, value in concentrations.items()
        if abs(float(value)) > EPS
    }


def _residual_pack(
    *,
    phase: str,
    model_type: str,
    units: str,
    concentrations: Dict[str, float],
    note: str,
) -> Dict[str, Any]:
    nonzero = _nonzero(concentrations)
    return {
        "phase": phase,
        "model_type": model_type,
        "units": units,
        "component_count": len(nonzero),
        "concentrations": nonzero,
        "note": note,
    }


@lru_cache(maxsize=1)
def get_adm1p_component_ids() -> Tuple[str, ...]:
    apply_runtime_compat()
    ensure_qsdsan_processes_load_from_file()
    import qsdsan.process_models as pc

    return tuple(pc.create_adm1p_cmps(set_thermo=False).IDs)


@lru_cache(maxsize=1)
def get_masm2d_component_ids() -> Tuple[str, ...]:
    apply_runtime_compat()
    ensure_qsdsan_processes_load_from_file()
    import qsdsan.process_models as pc

    return tuple(pc.create_masm2d_cmps(set_thermo=False).IDs)


@lru_cache(maxsize=1)
def get_madm1_adm1p_shared_ids() -> Tuple[str, ...]:
    adm1p_ids = set(get_adm1p_component_ids())
    return tuple(component_id for component_id in MADM1_COMPONENTS if component_id in adm1p_ids)


@lru_cache(maxsize=1)
def get_madm1_only_ids() -> Tuple[str, ...]:
    adm1p_ids = set(get_adm1p_component_ids())
    return tuple(component_id for component_id in MADM1_COMPONENTS if component_id not in adm1p_ids)


@lru_cache(maxsize=1)
def get_adm1p_only_ids() -> Tuple[str, ...]:
    madm1_ids = set(MADM1_COMPONENTS)
    return tuple(component_id for component_id in get_adm1p_component_ids() if component_id not in madm1_ids)


def project_madm1_to_adm1p_core(input_state: StreamState) -> Dict[str, Any]:
    """Project full mADM1 onto the exact upstream ADM1p shared core plus residuals."""
    if model_type_value(input_state.model_type) != ModelType.MADM1.value:
        raise ValueError(f"Expected mADM1 state, got {input_state.model_type}")

    adm1p_ids = get_adm1p_component_ids()
    shared_ids = set(get_madm1_adm1p_shared_ids())
    core = {component_id: 0.0 for component_id in adm1p_ids}

    for component_id in shared_ids:
        core[component_id] = float(input_state.concentrations.get(component_id, 0.0) or 0.0) * 1000.0

    residuals = {
        component_id: float(input_state.concentrations.get(component_id, 0.0) or 0.0)
        for component_id in get_madm1_only_ids()
    }

    return {
        "core_concentrations_mg_L": core,
        "carried_residuals": _residual_pack(
            phase="source_projection",
            model_type=ModelType.MADM1.value,
            units="kg/m3",
            concentrations=residuals,
            note="Repo-only mADM1 species not present in upstream ADM1p were preserved as sidecar residuals.",
        ),
        "shared_component_count": len(shared_ids),
    }


def reconstruct_madm1_from_adm1p_core(
    core_concentrations_mg_L: Dict[str, float],
    *,
    flow_m3_d: float,
    temperature_K: float,
    carried_source_residuals: Dict[str, float] | None = None,
) -> Tuple[StreamState, List[Dict[str, Any]]]:
    """
    Reconstruct full mADM1 from the ADM1p shared core plus carried residuals.

    Any upstream ADM1p-only species remain explicit metadata residuals because the
    repo's mADM1 state space has no exact component slots for them.
    """
    concentrations = {component_id: 0.0 for component_id in MADM1_COMPONENTS}
    shared_ids = set(get_madm1_adm1p_shared_ids())

    for component_id in shared_ids:
        concentrations[component_id] = float(core_concentrations_mg_L.get(component_id, 0.0) or 0.0) / 1000.0

    if carried_source_residuals:
        for component_id, value in carried_source_residuals.items():
            if component_id in concentrations:
                concentrations[component_id] = float(value)

    residual_packs: List[Dict[str, Any]] = []

    adm1p_only = {
        component_id: float(core_concentrations_mg_L.get(component_id, 0.0) or 0.0) / 1000.0
        for component_id in get_adm1p_only_ids()
    }
    pack = _residual_pack(
        phase="target_reconstruction",
        model_type="ADM1p",
        units="kg/m3",
        concentrations=adm1p_only,
        note="Upstream ADM1p-only species were preserved as sidecar residuals because full mADM1 has no exact component slots for them.",
    )
    if pack["component_count"] > 0:
        residual_packs.append(pack)

    return (
        StreamState.from_concentrations(
            model_type=ModelType.MADM1.value,
            concentrations=concentrations,
            flow_m3_d=flow_m3_d,
            temperature_K=temperature_K,
            concentration_units="kg/m3",
        ),
        residual_packs,
    )


def project_asm_family_to_masm2d_core(input_state: StreamState) -> Dict[str, Any]:
    """Project ASM2d or mASM2d onto the exact upstream mASM2d core basis."""
    model_type = model_type_value(input_state.model_type)
    if model_type not in (ModelType.ASM2D.value, ModelType.MASM2D.value):
        raise ValueError(f"Expected ASM2d or mASM2d state, got {input_state.model_type}")

    core = {component_id: 0.0 for component_id in MASM2D_COMPONENTS}
    basis_mappings: Dict[str, str] = {}

    if model_type == ModelType.MASM2D.value:
        for component_id in MASM2D_COMPONENTS:
            core[component_id] = float(input_state.concentrations.get(component_id, 0.0) or 0.0)
        residuals = {}
    else:
        shared_ids = set(ASM2D_COMPONENTS) & set(MASM2D_COMPONENTS)
        for component_id in shared_ids:
            core[component_id] = float(input_state.concentrations.get(component_id, 0.0) or 0.0)

        core["S_IC"] = float(input_state.concentrations.get("S_ALK", 0.0) or 0.0)
        basis_mappings["S_ALK"] = "S_IC"

        residuals = {
            component_id: float(input_state.concentrations.get(component_id, 0.0) or 0.0)
            for component_id in ASM2D_COMPONENTS
            if component_id not in MASM2D_COMPONENTS and component_id != "S_ALK"
        }

    return {
        "core_concentrations_mg_L": core,
        "carried_residuals": _residual_pack(
            phase="source_projection",
            model_type=model_type,
            units="mg/L",
            concentrations=residuals,
            note="Source ASM-family species not representable in upstream mASM2d were preserved as sidecar residuals.",
        ),
        "basis_mappings": basis_mappings,
    }


def reconstruct_asm_family_from_masm2d_core(
    core_concentrations_mg_L: Dict[str, float],
    *,
    target_model: ModelType,
    flow_m3_d: float,
    temperature_K: float,
) -> Tuple[StreamState, List[Dict[str, Any]]]:
    """Reconstruct either mASM2d or ASM2d StreamState from upstream mASM2d core."""
    if target_model == ModelType.MASM2D:
        concentrations = {
            component_id: float(core_concentrations_mg_L.get(component_id, 0.0) or 0.0)
            for component_id in MASM2D_COMPONENTS
        }
        return (
            StreamState.from_concentrations(
                model_type=ModelType.MASM2D.value,
                concentrations=concentrations,
                flow_m3_d=flow_m3_d,
                temperature_K=temperature_K,
                concentration_units="mg/L",
            ),
            [],
        )

    if target_model != ModelType.ASM2D:
        raise ValueError(f"Unsupported ASM-family reconstruction target: {target_model}")

    concentrations = {component_id: 0.0 for component_id in ASM2D_COMPONENTS}
    for component_id in ASM2D_COMPONENTS:
        if component_id in {"S_ALK", "X_MeOH", "X_MeP"}:
            continue
        concentrations[component_id] = float(core_concentrations_mg_L.get(component_id, 0.0) or 0.0)

    concentrations["S_ALK"] = float(core_concentrations_mg_L.get("S_IC", 0.0) or 0.0)

    residuals = {
        component_id: float(core_concentrations_mg_L.get(component_id, 0.0) or 0.0)
        for component_id in MASM2D_COMPONENTS
        if component_id not in ASM2D_COMPONENTS and component_id != "S_IC"
    }
    residual_packs: List[Dict[str, Any]] = []
    pack = _residual_pack(
        phase="target_reconstruction",
        model_type=ModelType.MASM2D.value,
        units="mg/L",
        concentrations=residuals,
        note="mASM2d-only ions and precipitates were preserved as sidecar residuals because plain ASM2d has no exact component slots for them.",
    )
    if pack["component_count"] > 0:
        residual_packs.append(pack)

    return (
        StreamState.from_concentrations(
            model_type=ModelType.ASM2D.value,
            concentrations=concentrations,
            flow_m3_d=flow_m3_d,
            temperature_K=temperature_K,
            concentration_units="mg/L",
        ),
        residual_packs,
    )
