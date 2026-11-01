"""Gas-result extraction and biogas metric helpers."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from .normalizer import normalized_results_dict

logger = logging.getLogger(__name__)

_HOURS_PER_DAY = 24.0
_STP_M3_PER_KMOL = 22.414
_CH4_MW_G_PER_MOL = 16.0
_THEORETICAL_CH4_NM3_KG_COD = 0.35
_CALIBRATED_CH4_YIELD_RANGE = (0.20, 0.45)


def _safe_get_indexed(container: Any, key: str, default: float = 0.0) -> float:
    try:
        return float(container[key])
    except Exception:
        return default


def _component_ids(stream: Any) -> tuple[str, ...]:
    components = getattr(stream, "components", None)
    return tuple(getattr(components, "IDs", ()) or ())


def _component_mw_kg_per_kmol(stream: Any, component_id: str) -> float | None:
    components = getattr(stream, "components", None) or getattr(stream, "chemicals", None)
    try:
        return float(components[component_id].MW)
    except Exception:
        return None


def infer_imol_basis(stream: Any, component_id: str = "S_ch4") -> str | None:
    """Infer QSDsan ``imol`` basis from mass flow and component molecular weight.

    In the current QSDsan/ThermoSTEAM stack, stream component flows are stored
    as kg/hr and kmol/hr. Older result code treated this too loosely. The
    inference is kept explicit so tests lock the active environment's basis.
    """

    mass_kg_hr = _safe_get_indexed(getattr(stream, "imass", None), component_id)
    imol_value = _safe_get_indexed(getattr(stream, "imol", None), component_id)
    mw = _component_mw_kg_per_kmol(stream, component_id)
    if mass_kg_hr <= 0 or imol_value <= 0 or not mw:
        return None

    ratio = mass_kg_hr / imol_value
    candidates = {
        "kmol/hr": mw,
        "mol/hr": mw / 1000.0,
        "kmol/d": mw / _HOURS_PER_DAY,
        "mol/d": mw / (1000.0 * _HOURS_PER_DAY),
    }
    for basis, expected in candidates.items():
        if expected and abs(ratio - expected) / expected < 1e-3:
            return basis
    return None


def imol_to_Nm3_d(value: float, *, basis: str = "kmol/hr") -> float:
    """Convert molar flow to Nm3/d at STP for the documented imol basis."""

    if value <= 0:
        return 0.0
    if basis == "kmol/hr":
        kmol_d = value * _HOURS_PER_DAY
    elif basis == "mol/hr":
        kmol_d = value * _HOURS_PER_DAY / 1000.0
    elif basis == "kmol/d":
        kmol_d = value
    elif basis == "mol/d":
        kmol_d = value / 1000.0
    else:
        raise ValueError(f"Unsupported molar-flow basis: {basis}")
    return kmol_d * _STP_M3_PER_KMOL


def _stream_flow_m3_d(stream: Any) -> float:
    try:
        return max(float(getattr(stream, "F_vol", 0.0) or 0.0) * _HOURS_PER_DAY, 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mole_fraction(stream: Any, component_id: str) -> float:
    total = getattr(stream, "F_mol", 0.0)
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0:
        return 0.0
    return max(_safe_get_indexed(getattr(stream, "imol", None), component_id) / total, 0.0)


def _component_flow_from_f_vol(stream: Any, component_id: str) -> float:
    total_flow = _stream_flow_m3_d(stream)
    if total_flow <= 0:
        return 0.0
    return total_flow * _mole_fraction(stream, component_id)


def _component_flow_from_mass(stream: Any, component_id: str) -> float:
    if component_id != "S_ch4":
        return 0.0
    kg_hr = _safe_get_indexed(getattr(stream, "imass", None), component_id)
    if kg_hr <= 0:
        return 0.0
    g_d = kg_hr * 1000.0 * _HOURS_PER_DAY
    mol_d = g_d / _CH4_MW_G_PER_MOL
    return mol_d * _STP_M3_PER_KMOL / 1000.0


def _component_flow_from_imol(stream: Any, component_id: str) -> tuple[float, str | None]:
    value = _safe_get_indexed(getattr(stream, "imol", None), component_id)
    if value <= 0:
        return 0.0, None
    basis = infer_imol_basis(stream, component_id) or "kmol/hr"
    return imol_to_Nm3_d(value, basis=basis), basis


def component_flow_Nm3_d(stream: Any, component_id: str) -> tuple[float, str]:
    """Return component gas flow, preferring stream.F_vol and mole fraction."""

    from_f_vol = _component_flow_from_f_vol(stream, component_id)
    if from_f_vol > 0:
        return from_f_vol, "F_vol*mole_fraction"

    from_mass = _component_flow_from_mass(stream, component_id)
    if from_mass > 0:
        return from_mass, "imass_CH4_g_d"

    from_imol, basis = _component_flow_from_imol(stream, component_id)
    if from_imol > 0:
        return from_imol, f"imol_{basis}"

    return 0.0, "unavailable"


def methane_flow_Nm3_d(stream: Any) -> tuple[float, str]:
    """Return CH4 flow in Nm3/d with the same basis priority as analysis."""

    return component_flow_Nm3_d(stream, "S_ch4")


def calculate_h2s_gas_ppm(gas_stream: Any) -> float:
    """Calculate H2S concentration in biogas (ppmv) from molar fraction."""

    try:
        if "S_IS" not in _component_ids(gas_stream):
            return 0.0
        fraction = _mole_fraction(gas_stream, "S_IS")
        return fraction * 1e6 if fraction > 0 else 0.0
    except Exception as exc:
        logger.warning("Error calculating H2S in biogas: %s", exc)
        return 0.0


def _cod_removed_kg_d(inf_stream: Any, eff_stream: Any) -> float:
    try:
        cod_removed_mg_l = max(float(inf_stream.COD) - float(eff_stream.COD), 0.0)
        flow_m3_d = _stream_flow_m3_d(inf_stream)
    except Exception:
        return 0.0
    # mg/L * m3/d = g/d after the 1000 L/m3 conversion; divide by 1000 for kg/d.
    return cod_removed_mg_l * flow_m3_d / 1000.0 if flow_m3_d > 0 else 0.0


def analyze_gas_stream(stream: Any, inf_stream: Any = None, eff_stream: Any = None) -> dict[str, Any]:
    """Analyze biogas stream for mADM1 outputs with physically bounded CH4 yield."""

    try:
        ch4_flow, ch4_basis = methane_flow_Nm3_d(stream)
        co2_flow, co2_basis = component_flow_Nm3_d(stream, "S_IC")
        h2_flow, h2_basis = component_flow_Nm3_d(stream, "S_h2")
        total_flow = _stream_flow_m3_d(stream)
        if total_flow <= 0:
            total_flow = ch4_flow + co2_flow + h2_flow

        ch4_pct = _mole_fraction(stream, "S_ch4") * 100.0
        co2_pct = _mole_fraction(stream, "S_IC") * 100.0
        h2_pct = _mole_fraction(stream, "S_h2") * 100.0

        result: dict[str, Any] = {
            "success": True,
            "flow_total_Nm3_d": total_flow,
            "flow_total_basis": "F_vol" if _stream_flow_m3_d(stream) > 0 else "component_sum",
            "methane_flow_Nm3_d": ch4_flow,
            "methane_flow_basis": ch4_basis,
            "methane_percent": ch4_pct,
            "co2_flow_Nm3_d": co2_flow,
            "co2_flow_basis": co2_basis,
            "co2_percent": co2_pct,
            "h2_flow_Nm3_d": h2_flow,
            "h2_flow_basis": h2_basis,
            "h2_percent": h2_pct,
            "h2s_ppm": calculate_h2s_gas_ppm(stream),
        }

        if inf_stream is not None and eff_stream is not None:
            cod_removed = _cod_removed_kg_d(inf_stream, eff_stream)
            result["COD_removed_kg_d"] = cod_removed
            if cod_removed > 0:
                methane_yield = ch4_flow / cod_removed
                result["methane_yield_Nm3_kg_COD"] = methane_yield
                result["CH4_Nm3_per_kg_COD_removed"] = methane_yield
                result["methane_yield_efficiency_pct"] = (
                    methane_yield / _THEORETICAL_CH4_NM3_KG_COD * 100.0
                )
                lo, hi = _CALIBRATED_CH4_YIELD_RANGE
                if methane_yield < lo or methane_yield > hi:
                    message = (
                        "Methane yield "
                        f"{methane_yield:.3g} Nm3/kg COD removed outside calibrated AD range "
                        f"[{lo:.2f}, {hi:.2f}]"
                    )
                    result.setdefault("warnings", []).append(message)
                    logger.warning(message)

        return result

    except Exception as exc:
        logger.error("Error analyzing gas stream: %s", exc)
        return {"success": False, "error": str(exc)}


def extract_gas_results(results: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the normalized biogas block, including per-engine fallback."""

    normalized = normalized_results_dict(results)
    direct = normalized.get("biogas")
    if isinstance(direct, dict):
        return direct

    per_engine = normalized.get("per_engine_summaries")
    if isinstance(per_engine, dict):
        madm1 = per_engine.get("madm1") or per_engine.get("adm1")
        if isinstance(madm1, dict) and isinstance(madm1.get("biogas"), dict):
            return dict(madm1["biogas"])
    return {}


__all__ = [
    "analyze_gas_stream",
    "calculate_h2s_gas_ppm",
    "component_flow_Nm3_d",
    "extract_gas_results",
    "imol_to_Nm3_d",
    "infer_imol_basis",
    "methane_flow_Nm3_d",
]
