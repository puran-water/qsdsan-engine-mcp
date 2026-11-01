"""Startup-policy helpers for session-built flowsheets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Optional
import logging

from qsdsan_engine_mcp.core.model_registry import normalize_model_name
from qsdsan_engine_mcp.utils.aerobic_inoculum_generator import generate_aerobic_inoculum
from qsdsan_engine_mcp.utils.flowsheet_session import FlowsheetSessionManager
from qsdsan_engine_mcp.utils.inoculum_generator import generate_inoculum_state

logger = logging.getLogger(__name__)

_AEROBIC_MODELS = {"ASM1", "ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O"}
_ANAEROBIC_MODELS = {"ADM1", "mADM1"}

_ASM_COD_COMPONENTS = (
    "S_F",
    "S_A",
    "S_I",
    "X_I",
    "X_S",
    "X_H",
    "X_PAO",
    "X_PP",
    "X_PHA",
    "X_AUT",
    "X_MeOH",
    "X_MeP",
    "S_IC",
)
_ASM_BOD5_WEIGHTS = {
    "S_F": 0.60,
    "S_A": 0.40,
    "X_S": 0.25,
}
_ASM_TSS_COMPONENTS = (
    "X_I",
    "X_S",
    "X_H",
    "X_PAO",
    "X_PP",
    "X_PHA",
    "X_AUT",
    "X_MeOH",
    "X_MeP",
)
_MADM1_REQUIRED_ORGANICS = (
    "S_su",
    "S_aa",
    "S_fa",
    "S_va",
    "S_bu",
    "S_pro",
    "S_ac",
    "X_ch",
    "X_pr",
    "X_li",
)


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _stream_units(stream_cfg) -> str:
    return str(getattr(stream_cfg, "concentration_units", "mg/L") or "mg/L")


def _component_conc_mg_l(stream_cfg, component_id: str) -> float:
    raw = _coerce_float((getattr(stream_cfg, "concentrations", {}) or {}).get(component_id))
    if raw is None:
        return 0.0
    return raw * 1000.0 if _stream_units(stream_cfg) == "kg/m3" else raw


def _component_conc_kg_m3(stream_cfg, component_id: str) -> float:
    raw = _coerce_float((getattr(stream_cfg, "concentrations", {}) or {}).get(component_id))
    if raw is None:
        return 0.0
    return raw / 1000.0 if _stream_units(stream_cfg) == "mg/L" else raw


def _sum_components_mg_l(stream_cfg, component_ids: Iterable[str]) -> float:
    return sum(_component_conc_mg_l(stream_cfg, component_id) for component_id in component_ids)


def _first_influent_stream(session):
    for stream_id, stream_cfg in getattr(session, "streams", {}).items():
        if getattr(stream_cfg, "stream_type", "") == "influent":
            return stream_id, stream_cfg
    for stream_id, stream_cfg in getattr(session, "streams", {}).items():
        roles = set((getattr(session, "stream_roles", {}) or {}).get(stream_id, ()))
        if "influent" in roles:
            return stream_id, stream_cfg
    for stream_id, stream_cfg in getattr(session, "streams", {}).items():
        return stream_id, stream_cfg
    return None, None


def _build_influent_basis(session, override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    _, influent_cfg = _first_influent_stream(session)
    override = override or {}

    direct_map = {
        "BOD5_mg_L": ("BOD5_mg_L", "BOD5", "bod5_mg_l", "bod5"),
        "COD_mg_L": ("COD_mg_L", "COD", "cod_mg_l", "cod"),
        "TSS_mg_L": ("TSS_mg_L", "TSS", "tss_mg_l", "tss"),
        "TKN_mg_L": ("TKN_mg_L", "TKN", "tkn_mg_l", "tkn"),
    }

    basis: Dict[str, Any] = {}
    for canonical_key, aliases in direct_map.items():
        explicit = next((_coerce_float(override.get(alias)) for alias in aliases if alias in override), None)
        if explicit is not None:
            basis[canonical_key] = explicit
            continue

        if influent_cfg is None:
            basis[canonical_key] = 0.0
            continue

        if canonical_key == "COD_mg_L":
            basis[canonical_key] = _sum_components_mg_l(influent_cfg, _ASM_COD_COMPONENTS)
        elif canonical_key == "BOD5_mg_L":
            basis[canonical_key] = sum(
                _component_conc_mg_l(influent_cfg, component_id) * factor
                for component_id, factor in _ASM_BOD5_WEIGHTS.items()
            )
        elif canonical_key == "TSS_mg_L":
            basis[canonical_key] = _sum_components_mg_l(influent_cfg, _ASM_TSS_COMPONENTS)
        elif canonical_key == "TKN_mg_L":
            basis[canonical_key] = _component_conc_mg_l(influent_cfg, "S_NH4")

    cod = max(float(basis.get("COD_mg_L", 0.0)), 0.0)
    bod5 = max(float(basis.get("BOD5_mg_L", 0.0)), 0.0)
    tkn = max(float(basis.get("TKN_mg_L", 0.0)), 0.0)
    basis["BOD5_COD_ratio"] = bod5 / cod if cod > 0 else 0.0
    basis["TKN_COD_ratio"] = tkn / cod if cod > 0 else 0.0
    return basis


def _eligible_aerobic_unit_ids(session) -> list[str]:
    unit_ids = []
    for unit_id, unit_cfg in getattr(session, "units", {}).items():
        model_type = normalize_model_name(getattr(unit_cfg, "model_type", None) or session.primary_model_type)
        if model_type not in _AEROBIC_MODELS:
            continue
        if unit_cfg.unit_type in {"CSTR", "CompletelyMixedMBR", "AerobicDigester"}:
            unit_ids.append(unit_id)
    return unit_ids


def _eligible_anaerobic_unit_ids(session) -> list[str]:
    unit_ids = []
    for unit_id, unit_cfg in getattr(session, "units", {}).items():
        model_type = normalize_model_name(getattr(unit_cfg, "model_type", None) or session.primary_model_type)
        if model_type not in _ANAEROBIC_MODELS:
            continue
        if unit_cfg.unit_type in {
            "AnaerobicCSTR",
            "AnaerobicCSTRmADM1",
            "AnaerobicBaffledReactor",
            "InternalCirculationRx",
            "AnMBR",
            "SludgeDigester",
        }:
            unit_ids.append(unit_id)
    return unit_ids


def _convert_aerobic_inoculum_for_model(model_type: str, inoculum: Dict[str, float]) -> Dict[str, float]:
    canonical = normalize_model_name(model_type)
    if canonical != "ASM1":
        return inoculum

    converted = {}
    if inoculum.get("X_H") is not None:
        converted["X_B_H"] = float(inoculum["X_H"])
    if inoculum.get("X_AUT") is not None:
        converted["X_B_A"] = float(inoculum["X_AUT"])
    if inoculum.get("S_O2") is not None:
        converted["S_O"] = float(inoculum["S_O2"])
    if inoculum.get("S_NH4") is not None:
        converted["S_NH"] = float(inoculum["S_NH4"])
    if inoculum.get("S_NO3") is not None:
        converted["S_NO"] = float(inoculum["S_NO3"])
    if inoculum.get("S_F") is not None:
        converted["S_S"] = float(inoculum["S_F"])
    if inoculum.get("X_S") is not None:
        converted["X_S"] = float(inoculum["X_S"])
    if inoculum.get("S_I") is not None:
        converted["S_I"] = float(inoculum["S_I"])
    if inoculum.get("X_I") is not None:
        converted["X_I"] = float(inoculum["X_I"])
    return converted


def _resolve_aerobic_inoculum(session, policy: Dict[str, Any]) -> Dict[str, Any]:
    model_type = normalize_model_name(session.primary_model_type)
    basis = _build_influent_basis(session, policy.get("influent_basis"))
    target_mlvss = _coerce_float(policy.get("target_mlvss_mg_L"))
    if target_mlvss is None:
        target_mlvss = 8000.0 if any(
            unit_cfg.unit_type == "CompletelyMixedMBR"
            for unit_cfg in getattr(session, "units", {}).values()
        ) else 3500.0

    po4_mg_l = 0.0
    _, influent_cfg = _first_influent_stream(session)
    if influent_cfg is not None:
        po4_mg_l = _component_conc_mg_l(influent_cfg, "S_PO4")

    x_aut_fraction = _coerce_float(policy.get("x_aut_fraction"))
    if x_aut_fraction is None:
        x_aut_fraction = min(max(0.03 + basis["TKN_COD_ratio"] * 0.25, 0.03), 0.08)

    x_pao_fraction = _coerce_float(policy.get("x_pao_fraction"))
    if x_pao_fraction is None:
        x_pao_fraction = 0.12 if po4_mg_l >= 5.0 else 0.02

    x_h_fraction = _coerce_float(policy.get("x_h_fraction"))
    if x_h_fraction is None:
        x_h_fraction = max(0.72, 0.95 - x_aut_fraction - x_pao_fraction)

    if model_type == "mASM2d":
        from qsdsan_engine_mcp.models.masm2d import generate_masm2d_inoculum

        raw_inoculum = generate_masm2d_inoculum(
            target_mlvss_mg_L=target_mlvss,
            x_aut_fraction=x_aut_fraction,
            x_pao_fraction=x_pao_fraction,
            x_h_fraction=x_h_fraction,
        )
    elif model_type in ("mASM2d2N", "mASM2d2N_N2O"):
        from qsdsan_engine_mcp.models.masm2d import generate_masm2d_inoculum

        raw_inoculum = generate_masm2d_inoculum(
            target_mlvss_mg_L=target_mlvss,
            x_aut_fraction=x_aut_fraction,
            x_pao_fraction=x_pao_fraction,
            x_h_fraction=x_h_fraction,
        )
        # Two-step models have no lumped X_AUT — split the autotroph seed 50/50 into AOB + NOB
        # so both nitrifier guilds are inoculated (else nitrification never starts). The N2O
        # variant's AOB electron-chain intermediates (S_NH2OH/S_NO/S_N2O) start at zero.
        x_aut = raw_inoculum.pop("X_AUT", 0.0)
        raw_inoculum["X_AOB"] = raw_inoculum.get("X_AOB", 0.0) + 0.5 * x_aut
        raw_inoculum["X_NOB"] = raw_inoculum.get("X_NOB", 0.0) + 0.5 * x_aut
    else:
        raw_inoculum = generate_aerobic_inoculum(
            target_mlvss_mg_L=target_mlvss,
            x_aut_fraction=x_aut_fraction,
            x_pao_fraction=x_pao_fraction,
            x_h_fraction=x_h_fraction,
        )

    inoculum = _convert_aerobic_inoculum_for_model(model_type, raw_inoculum)
    per_unit_overrides = deepcopy(policy.get("per_unit_overrides") or {})
    target_units = list(policy.get("target_units") or _eligible_aerobic_unit_ids(session))

    per_unit_init_conc = {
        unit_id: _deep_merge_dicts(inoculum, per_unit_overrides.get(unit_id, {}))
        for unit_id in target_units
    }

    resolved_policy = _deep_merge_dicts(
        policy,
        {
            "target_mlvss_mg_L": target_mlvss,
            "x_aut_fraction": x_aut_fraction,
            "x_pao_fraction": x_pao_fraction,
            "x_h_fraction": x_h_fraction,
            "target_units": target_units,
        },
    )

    return {
        "resolved_policy": resolved_policy,
        "influent_basis": basis,
        "per_unit_init_conc": per_unit_init_conc,
        "units_seeded": target_units,
        "components_seeded": sorted(inoculum.keys()),
    }


def _build_madm1_feedstock_state(session, override: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if override:
        return {key: float(value) for key, value in override.items()}

    _, influent_cfg = _first_influent_stream(session)
    if influent_cfg is None:
        raise ValueError("Cannot generate anaerobic startup state without at least one influent stream")

    feedstock = {}
    concentrations = getattr(influent_cfg, "concentrations", {}) or {}
    for component_id in concentrations:
        value = _component_conc_kg_m3(influent_cfg, component_id)
        if value > 0.0:
            feedstock[component_id] = value

    for component_id in _MADM1_REQUIRED_ORGANICS:
        feedstock.setdefault(component_id, 0.0)

    return feedstock


def _resolve_anaerobic_inoculum(session, policy: Dict[str, Any]) -> Dict[str, Any]:
    feedstock_state = _build_madm1_feedstock_state(session, policy.get("feedstock_state_kg_m3"))
    target_biomass_cod_ratio = _coerce_float(policy.get("target_biomass_cod_ratio")) or 0.20
    methanogen_boost_factor = _coerce_float(policy.get("methanogen_boost_factor")) or 6.0
    target_alkalinity_meq_l = _coerce_float(policy.get("target_alkalinity_meq_l")) or 200.0

    inoculum_kg_m3 = generate_inoculum_state(
        feedstock_state=feedstock_state,
        target_biomass_cod_ratio=target_biomass_cod_ratio,
        methanogen_boost_factor=methanogen_boost_factor,
        target_alkalinity_meq_l=target_alkalinity_meq_l,
    )
    inoculum_mg_l = {
        component_id: float(value) * 1000.0
        for component_id, value in inoculum_kg_m3.items()
    }

    per_unit_overrides = deepcopy(policy.get("per_unit_overrides") or {})
    target_units = list(policy.get("target_units") or _eligible_anaerobic_unit_ids(session))
    per_unit_init_conc = {
        unit_id: _deep_merge_dicts(inoculum_mg_l, per_unit_overrides.get(unit_id, {}))
        for unit_id in target_units
    }

    inflow_basis = _build_influent_basis(session, policy.get("influent_basis"))
    resolved_policy = _deep_merge_dicts(
        policy,
        {
            "target_biomass_cod_ratio": target_biomass_cod_ratio,
            "methanogen_boost_factor": methanogen_boost_factor,
            "target_alkalinity_meq_l": target_alkalinity_meq_l,
            "target_units": target_units,
        },
    )

    return {
        "resolved_policy": resolved_policy,
        "influent_basis": inflow_basis,
        "per_unit_init_conc": per_unit_init_conc,
        "units_seeded": target_units,
        "components_seeded": sorted(inoculum_mg_l.keys()),
    }


def apply_session_startup(
    session_id: str,
    *,
    inoculum_policy: Optional[Dict[str, Any]] = None,
    session_manager: Optional[FlowsheetSessionManager] = None,
) -> Dict[str, Any]:
    """Persist startup policy and unit initial concentrations for one session."""
    manager = session_manager or FlowsheetSessionManager()
    session = manager.get_session(session_id)
    existing_state = manager.get_startup_state(session_id) if session.startup_metadata else {}
    existing_policy = deepcopy(existing_state.get("inoculum_policy") or {})
    requested_policy = deepcopy(inoculum_policy or {})
    merged_policy = _deep_merge_dicts(existing_policy, requested_policy)

    model_type = normalize_model_name(session.primary_model_type)
    if model_type in _AEROBIC_MODELS:
        resolved = _resolve_aerobic_inoculum(session, merged_policy)
        engine_family = "asm"
    elif model_type in _ANAEROBIC_MODELS:
        resolved = _resolve_anaerobic_inoculum(session, merged_policy)
        engine_family = "adm"
    else:
        raise ValueError(f"Startup policy is not defined for model '{model_type}'")

    startup_metadata = _deep_merge_dicts(
        existing_state,
        {
            "engine_family": engine_family,
            "inoculum_policy": resolved["resolved_policy"],
            "influent_basis": resolved["influent_basis"],
            "per_unit_init_conc": resolved["per_unit_init_conc"],
            "units_seeded": resolved["units_seeded"],
            "components_seeded": resolved["components_seeded"],
        },
    )
    manager.set_startup_state(session_id, startup_metadata)

    logger.info(
        "Persisted startup policy for session %s across %d unit(s)",
        session_id,
        len(resolved["units_seeded"]),
    )
    return {
        "session_id": session_id,
        "model_type": model_type,
        "engine_family": engine_family,
        "influent_basis": resolved["influent_basis"],
        "units_seeded": resolved["units_seeded"],
        "components_seeded": resolved["components_seeded"],
        "startup_metadata": startup_metadata,
    }


__all__ = ["apply_session_startup"]
