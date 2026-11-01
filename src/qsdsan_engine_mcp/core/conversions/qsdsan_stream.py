"""Adapters between canonical StreamState and QSDsan WasteStream objects."""

from __future__ import annotations

from typing import Any

from qsdsan_engine_mcp.core.components.catalog import get_component_ids, get_concentration_units, normalize_model_type
from qsdsan_engine_mcp.core.contracts.stream_state import Phase, StreamState
from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat


class ContractViolationError(ValueError):
    """Raised when an adapter input does not satisfy the public contract."""


_STREAM_COMPONENT_IDS_BY_OBJECT_ID: dict[int, tuple[str, ...]] = {}


def _components_for_model(model_type: str) -> Any:
    apply_runtime_compat()

    import qsdsan as qs
    from qsdsan import process_models as pc

    if model_type == "mADM1":
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps

        cmps = create_madm1_cmps()
    elif model_type == "ADM1":
        cmps = pc.create_adm1_cmps()
    elif model_type == "ASM2d":
        cmps = pc.create_asm2d_cmps()
    elif model_type == "ASM1":
        cmps = pc.create_asm1_cmps()
    elif model_type == "mASM2d":
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_components

        cmps = create_masm2d_components()
    elif model_type == "mASM2d2N":
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_2n_components

        cmps = create_masm2d_2n_components()
    elif model_type == "mASM2d2N_N2O":
        from qsdsan_engine_mcp.models.masm2d_n2o import create_masm2d_2n_n2o_components

        cmps = create_masm2d_2n_n2o_components()
    else:
        raise ContractViolationError(f"Unsupported StreamState model_type: {model_type}")

    qs.set_thermo(cmps)
    return cmps


def _to_qsdsan_mg_l(stream_state: StreamState) -> dict[str, float]:
    scale = 1000.0 if stream_state.concentration_units == "kg/m3" else 1.0
    return {
        component_id: float(value) * scale
        for component_id, value in stream_state.concentrations.items()
    }


def to_qsdsan_stream(stream_state: StreamState):
    """
    Create a QSDsan WasteStream from a validated canonical StreamState.

    QSDsan's concentration setter accepts mg/L. The public contract keeps
    anaerobic mADM1 values in kg/m3, so those are converted before assignment.
    """

    if not isinstance(stream_state, StreamState):
        raise ContractViolationError("to_qsdsan_stream requires a StreamState instance")

    model_type = normalize_model_type(stream_state.model_type)
    cmps = _components_for_model(model_type)
    unsupported = sorted(set(stream_state.concentrations) - set(cmps.IDs))
    if unsupported:
        raise ContractViolationError(
            "Cannot create QSDsan stream with handoff-only or unsupported components "
            f"for {model_type}: {unsupported}"
        )

    from qsdsan import WasteStream

    stream = WasteStream(
        ID=stream_state.stream_id or "",
        T=stream_state.temperature_K,
    )
    concentrations_mg_l = _to_qsdsan_mg_l(stream_state)

    if stream_state.flow_m3_d > 0 and concentrations_mg_l:
        stream.set_flow_by_concentration(
            flow_tot=stream_state.flow_m3_d,
            concentrations=concentrations_mg_l,
            units=("m3/d", "mg/L"),
        )
    elif stream_state.flow_m3_d > 0:
        stream.set_total_flow(stream_state.flow_m3_d, "m3/d")

    _STREAM_COMPONENT_IDS_BY_OBJECT_ID[id(stream)] = tuple(stream_state.concentrations)
    return stream


def _phase_from_qsdsan(value: Any) -> Phase:
    phase_text = str(value or "l").lower()
    if phase_text in {"g", "gas"}:
        return "gas"
    if phase_text in {"s", "solid"}:
        return "solid"
    if phase_text in {"mixed", "multi"}:
        return "mixed"
    return "liquid"


def _get_concentration_mg_l(qsdsan_stream: Any, component_id: str) -> float:
    getter = getattr(qsdsan_stream, "get_concentration", None)
    if callable(getter):
        try:
            return float(getter(component_id, units="mg/L"))
        except Exception:
            pass
    iconc = getattr(qsdsan_stream, "iconc", None)
    try:
        return float(iconc[component_id])
    except Exception:
        return 0.0


def from_qsdsan_stream(qsdsan_stream: Any, model_type: str) -> StreamState:
    """Extract a canonical StreamState from a QSDsan stream object.

    Canonical export units are catalog-defined: mADM1/ADM1 values are emitted
    as kg/m3 after converting QSDsan's mg/L concentration API.
    """

    canonical_model = normalize_model_type(model_type)
    units = get_concentration_units(canonical_model)
    tracked_component_ids = _STREAM_COMPONENT_IDS_BY_OBJECT_ID.get(id(qsdsan_stream))
    component_ids = tracked_component_ids or get_component_ids(canonical_model)

    scale = 1000.0 if units == "kg/m3" else 1.0
    concentrations: dict[str, float] = {}
    for component_id in component_ids:
        value_mg_l = _get_concentration_mg_l(qsdsan_stream, component_id)
        value = value_mg_l / scale
        if tracked_component_ids is not None or value > 0.0:
            concentrations[component_id] = value

    flow_m3_d = float(getattr(qsdsan_stream, "F_vol", 0.0) or 0.0) * 24.0
    return StreamState.from_concentrations(
        stream_id=str(getattr(qsdsan_stream, "ID", "") or ""),
        model_type=canonical_model,
        phase=_phase_from_qsdsan(getattr(qsdsan_stream, "phase", None)),
        flow_m3_d=flow_m3_d,
        temperature_K=float(getattr(qsdsan_stream, "T", 298.15)),
        concentration_units=units,
        concentrations=concentrations,
    )


__all__ = ["ContractViolationError", "from_qsdsan_stream", "to_qsdsan_stream"]
