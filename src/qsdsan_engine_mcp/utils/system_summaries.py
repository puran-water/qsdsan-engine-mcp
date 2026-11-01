"""System-level summary extractors for session-built flowsheets."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional
import logging

from qsdsan_engine_mcp.core.model_registry import ASM2D_COMPONENTS, MADM1_COMPONENTS, MASM2D_COMPONENTS
from qsdsan_engine_mcp.core.results.gas import methane_flow_Nm3_d
from qsdsan_engine_mcp.utils.analysis.aerobic import analyze_aerobic_performance, calculate_hrt, calculate_nitrogen_removal, calculate_phosphorus_removal, calculate_srt
from qsdsan_engine_mcp.utils.analysis.anaerobic import analyze_biomass_yields, calculate_sulfur_metrics
from qsdsan_engine_mcp.utils.stream_analysis import analyze_gas_stream
from qsdsan_engine_mcp.utils.stream_roles import get_streams_by_role

logger = logging.getLogger(__name__)

_ASM_FAMILY_COMPONENTS = set(ASM2D_COMPONENTS) | set(MASM2D_COMPONENTS)
_MADM1_COMPONENTS = set(MADM1_COMPONENTS)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _stream_id(stream: Any) -> str:
    return str(getattr(stream, "ID", "") or "")


def _stream_flow_m3_d(stream: Any) -> float:
    f_vol = _as_float(getattr(stream, "F_vol", None))
    if f_vol is None:
        return 0.0
    return f_vol * 24.0


def _stream_component_ids(stream: Any) -> set[str]:
    components = getattr(stream, "components", None)
    return set(getattr(components, "IDs", ()) or ())


def _find_stream(system: Any, stream_id: Optional[str]) -> Any:
    if not stream_id:
        return None
    for stream in getattr(system, "streams", ()) or ():
        if stream is not None and _stream_id(stream) == stream_id:
            return stream
    return None


def _safe_get_tss(stream: Any) -> Optional[float]:
    if stream is None:
        return None
    getter = getattr(stream, "get_TSS", None)
    if callable(getter):
        value = _as_float(getter())
        if value is not None:
            return value
    return _as_float(getattr(stream, "TSS", None))


def _safe_get_cod(stream: Any) -> Optional[float]:
    return _as_float(getattr(stream, "COD", None))


def _unit_liquid_volume(unit: Any) -> Optional[float]:
    for attr_name in ("V_liq", "V_max"):
        value = _as_float(getattr(unit, attr_name, None))
        if value is not None:
            return value
    return None


def _unit_gas_volume(unit: Any) -> Optional[float]:
    return _as_float(getattr(unit, "V_gas", None))


def _unit_liquid_outlet(unit: Any) -> Any:
    outs = [stream for stream in (getattr(unit, "outs", None) or ()) if stream is not None]
    if not outs:
        return None
    if type(unit).__name__ == "CompletelyMixedMBR" and len(outs) > 1:
        return outs[1]
    for stream in outs:
        if getattr(stream, "phase", "l") == "l":
            return stream
    return outs[0]


def _stream_looks_asm(stream: Any) -> bool:
    return bool(_stream_component_ids(stream) & _ASM_FAMILY_COMPONENTS)


def _stream_looks_madm1(stream: Any) -> bool:
    return bool(_stream_component_ids(stream) & _MADM1_COMPONENTS)


def _first_role_stream(system: Any, role: str) -> Any:
    streams = get_streams_by_role(system, role)
    return streams[0] if streams else None


def _detect_wastage_stream(system: Any) -> Any:
    stream = _first_role_stream(system, "was")
    if stream is not None:
        return stream
    for candidate in getattr(system, "streams", ()) or ():
        if candidate is None:
            continue
        name = _stream_id(candidate).lower()
        if "was" in name or "waste" in name:
            return candidate
    return None


def _calculate_madm1_performance_metrics(influent: Any, effluent: Any, biogas: Any, geometry: Dict[str, Any]) -> Dict[str, Any]:
    cod_in = _safe_get_cod(influent) or 0.0
    cod_out = _safe_get_cod(effluent) or 0.0
    cod_removed = max(cod_in - cod_out, 0.0)
    cod_removal_pct = (cod_removed / cod_in * 100.0) if cod_in > 0 else 0.0

    biogas_m3_d = _stream_flow_m3_d(biogas) if biogas is not None else 0.0
    ch4_flow = 0.0
    if biogas is not None:
        ch4_flow, _ = methane_flow_Nm3_d(biogas)

    influent_flow = _stream_flow_m3_d(influent)
    total_liquid_volume = float(geometry.get("total_liquid_volume_m3", 0.0) or 0.0)
    hrt_days = (total_liquid_volume / influent_flow) if influent_flow > 0 else None
    cod_removed_kg_d = cod_removed * influent_flow / 1000.0 if influent_flow > 0 else 0.0

    return {
        "COD_in_mg_L": cod_in,
        "COD_out_mg_L": cod_out,
        "COD_removed_mg_L": cod_removed,
        "COD_removal_pct": cod_removal_pct,
        "biogas_m3_d": biogas_m3_d,
        "methane_flow_Nm3_d": ch4_flow,
        "specific_CH4_yield_m3_kg_COD": (ch4_flow / cod_removed_kg_d) if cod_removed_kg_d > 0 else 0.0,
        "HRT_days": hrt_days,
    }


def extract_reactor_geometry(system) -> Dict[str, Any]:
    """Return per-unit geometry and residence-time hints."""
    units = []
    total_liquid = 0.0
    total_gas = 0.0

    for unit in getattr(system, "units", ()) or ():
        v_liq = _unit_liquid_volume(unit)
        v_gas = _unit_gas_volume(unit)
        inlet_flow = sum(
            _stream_flow_m3_d(stream)
            for stream in (getattr(unit, "ins", None) or ())
            if stream is not None and getattr(stream, "phase", "l") != "g"
        )
        hrt_h = (v_liq / inlet_flow * 24.0) if v_liq is not None and inlet_flow > 0 else None
        units.append(
            {
                "unit_id": getattr(unit, "ID", ""),
                "unit_type": type(unit).__name__,
                "V_liq_m3": v_liq,
                "V_gas_m3": v_gas,
                "HRT_h": hrt_h,
            }
        )
        total_liquid += float(v_liq or 0.0)
        total_gas += float(v_gas or 0.0)

    return {
        "units": units,
        "total_liquid_volume_m3": total_liquid,
        "total_gas_volume_m3": total_gas,
    }


def extract_asm2d_performance(system, *, influent_id, effluent_id) -> Dict[str, Any]:
    """Summarize aerobic-biological performance without fixed-zone assumptions."""
    influent = _find_stream(system, influent_id)
    effluent = _find_stream(system, effluent_id)
    wastage = _detect_wastage_stream(system)
    geometry = extract_reactor_geometry(system)

    mlss_rows = []
    mlss_values = []
    for unit in getattr(system, "units", ()) or ():
        liquor_stream = _unit_liquid_outlet(unit)
        if liquor_stream is None or not _stream_looks_asm(liquor_stream):
            continue
        mlss = _safe_get_tss(liquor_stream)
        row = {
            "unit_id": getattr(unit, "ID", ""),
            "unit_type": type(unit).__name__,
            "mixed_liquor_stream_id": _stream_id(liquor_stream),
            "mlss_mg_L": mlss,
        }
        mlss_rows.append(row)
        if mlss is not None:
            mlss_values.append(mlss)

    srt = calculate_srt(system, wastage) if wastage is not None else {"success": False, "error": "No wastage stream detected"}
    hrt = calculate_hrt(system, influent) if influent is not None else {"success": False, "error": "No influent stream detected"}
    nitrogen = (
        calculate_nitrogen_removal(influent, effluent)
        if influent is not None and effluent is not None
        else {"success": False, "error": "Influent or effluent stream missing"}
    )
    phosphorus = (
        calculate_phosphorus_removal(influent, effluent)
        if influent is not None and effluent is not None
        else {"success": False, "error": "Influent or effluent stream missing"}
    )
    performance = (
        analyze_aerobic_performance(influent, effluent, system=system, was_stream=wastage)
        if influent is not None and effluent is not None
        else {"success": False, "error": "Influent or effluent stream missing"}
    )

    return {
        "geometry": geometry,
        "mlss_by_unit": mlss_rows,
        "mean_mlss_mg_L": (sum(mlss_values) / len(mlss_values)) if mlss_values else None,
        "representative_mlss_mg_L": max(mlss_values) if mlss_values else None,
        "srt": srt,
        "hrt": hrt,
        "nitrogen": nitrogen,
        "phosphorus": phosphorus,
        "performance": performance,
    }


def extract_madm1_performance(system, *, influent_id, effluent_id, biogas_id) -> Dict[str, Any]:
    """Summarize anaerobic-biological performance for mADM1 systems."""
    influent = _find_stream(system, influent_id)
    effluent = _find_stream(system, effluent_id)
    biogas = _find_stream(system, biogas_id)
    geometry = extract_reactor_geometry(system)

    biomass_yields = (
        analyze_biomass_yields(influent, effluent, system=system)
        if influent is not None and effluent is not None
        else {"success": False, "error": "Influent or effluent stream missing"}
    )
    biogas_analysis = (
        analyze_gas_stream(biogas, inf_stream=influent, eff_stream=effluent)
        if biogas is not None
        else {"success": False, "error": "Biogas stream missing"}
    )
    sulfur = (
        calculate_sulfur_metrics(influent, effluent, biogas)
        if influent is not None and effluent is not None and biogas is not None
        else {"success": False, "error": "Influent, effluent, or biogas stream missing"}
    )

    return {
        "geometry": geometry,
        "biogas": biogas_analysis,
        "biomass_yields": biomass_yields,
        "sulfur": sulfur,
        "performance": _calculate_madm1_performance_metrics(influent, effluent, biogas, geometry),
    }


def detect_system_engine_families(system) -> Dict[str, bool]:
    """Report whether the compiled system carries ASM-family and/or mADM1 units."""
    has_asm = False
    has_madm1 = False
    for unit in getattr(system, "units", ()) or ():
        liquor_stream = _unit_liquid_outlet(unit)
        if liquor_stream is None:
            continue
        has_asm = has_asm or _stream_looks_asm(liquor_stream)
        has_madm1 = has_madm1 or _stream_looks_madm1(liquor_stream)
    return {"has_asm2d": has_asm, "has_madm1": has_madm1}


__all__ = [
    "detect_system_engine_families",
    "extract_asm2d_performance",
    "extract_madm1_performance",
    "extract_reactor_geometry",
]
