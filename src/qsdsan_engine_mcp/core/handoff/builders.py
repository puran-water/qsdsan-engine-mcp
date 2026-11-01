"""Pure builders for strict handoff profiles."""

from __future__ import annotations

from typing import Any

from qsdsan_engine_mcp.core.contracts.stream_state import StreamState

from .profiles import (
    ASM2D_X_TSS_PARTICULATES,
    MADM1_X_TSS_PARTICULATES,
    get_handoff_profile,
)
from .validators import validate_handoff

from puran_mcp_support.engineering.converters.watertap_mcas import eng_mcas_to_component_charges, eng_mcas_to_watertap_components

_MW_SO4_G_MOL = 96.062
_MW_S_G_MOL = 32.065
_MW_HCO3_G_MOL = 61.017
_MW_C_G_MOL = 12.011


def derive_x_tss(concentrations: dict[str, float], *, model_type: str) -> float:
    """Derive X_TSS when the downstream profile requires an explicit solid aggregate."""

    if "X_TSS" in concentrations:
        return float(concentrations["X_TSS"])
    if model_type == "ASM2d":
        component_ids = ASM2D_X_TSS_PARTICULATES
    elif model_type == "mADM1":
        component_ids = MADM1_X_TSS_PARTICULATES
    else:
        return 0.0
    return sum(float(concentrations[component_id]) for component_id in component_ids)


def build_masm2d_to_mcas(stream_state: StreamState) -> dict[str, Any]:
    """Build an engineering-utils MCAS dict without silently filling sulfate."""

    profile = get_handoff_profile("watertap_mcas", source_model_type=stream_state.model_type)
    validate_handoff(stream_state, profile)
    c = stream_state.concentrations
    return {
        "Na_mg_l": float(c["S_Na"]),
        "K_mg_l": float(c.get("S_K", 0.0)),
        "Ca_mg_l": float(c["S_Ca"]),
        "Mg_mg_l": float(c["S_Mg"]),
        "Cl_mg_l": float(c["S_Cl"]),
        "NH4_N_mg_l": float(c.get("S_NH4", 0.0)),
        "NO3_N_mg_l": float(c.get("S_NO3", 0.0)),
        "NO2_N_mg_l": 0.0,
        "PO4_P_mg_l": float(c.get("S_PO4", 0.0)),
        "HCO3_mg_l": float(c.get("S_IC", 0.0)) * _MW_HCO3_G_MOL / _MW_C_G_MOL,
        "SO4_mg_l": float(c["S_SO4"]) * _MW_SO4_G_MOL / _MW_S_G_MOL,
        "SiO2_mg_l": 0.0,
        "Fe_mg_l": 0.0,
        "Al_mg_l": 0.0,
        "temp": stream_state.temperature_K - 273.15,
    }


def build_watertap_mcas_components(stream_state: StreamState) -> tuple[dict[str, float], dict[str, int]]:
    """Return WaterTAP MCAS component concentrations and charges."""

    mcas = build_masm2d_to_mcas(stream_state)
    return (
        eng_mcas_to_watertap_components(mcas, include_zero=True),
        eng_mcas_to_component_charges(mcas, include_zero=True),
    )


def build_watertap_asm2d_state(stream_state: StreamState) -> dict[str, Any]:
    """Build a WaterTAP ASM2d component dict with explicit X_TSS policy."""

    profile = get_handoff_profile("watertap_asm2d", source_model_type=stream_state.model_type)
    validation = validate_handoff(stream_state, profile)
    components = dict(stream_state.concentrations)
    components["X_TSS"] = derive_x_tss(components, model_type="ASM2d")
    return {
        "model_type": "ASM2d",
        "concentration_units": stream_state.concentration_units,
        "components": components,
        "validation": validation.to_dict(),
    }


def build_watertap_madm1_state(stream_state: StreamState) -> dict[str, Any]:
    """Build a WaterTAP ADM1 component dict and expose derived X_TSS."""

    profile = get_handoff_profile("watertap_madm1", source_model_type=stream_state.model_type)
    validation = validate_handoff(stream_state, profile)
    c = stream_state.concentrations
    components = {
        "H2O": float(c["H2O"]),
        "S_su": float(c["S_su"]),
        "S_aa": float(c["S_aa"]),
        "S_fa": float(c["S_fa"]),
        "S_va": float(c["S_va"]),
        "S_bu": float(c["S_bu"]),
        "S_pro": float(c["S_pro"]),
        "S_ac": float(c["S_ac"]),
        "S_h2": float(c["S_h2"]),
        "S_ch4": float(c["S_ch4"]),
        "S_IC": float(c["S_IC"]),
        "S_IN": float(c["S_IN"]),
        "S_I": float(c["S_I"]),
        "X_c": float(c["X_ch"]) + float(c["X_pr"]) + float(c["X_li"]),
        "X_ch": float(c["X_ch"]),
        "X_pr": float(c["X_pr"]),
        "X_li": float(c["X_li"]),
        "X_su": float(c["X_su"]),
        "X_aa": float(c["X_aa"]),
        "X_fa": float(c["X_fa"]),
        "X_c4": float(c["X_c4"]),
        "X_pro": float(c["X_pro"]),
        "X_ac": float(c["X_ac"]),
        "X_h2": float(c["X_h2"]),
        "X_I": float(c["X_I"]),
    }
    return {
        "model_type": "ADM1",
        "concentration_units": stream_state.concentration_units,
        "components": components,
        "derived_metrics": {
            "X_TSS": derive_x_tss(dict(c), model_type="mADM1"),
        },
        "validation": validation.to_dict(),
    }


def build_masm2d_to_madm1_state(stream_state: StreamState) -> dict[str, Any]:
    """Return a validated source payload for the internal mASM2d -> mADM1 path."""

    profile = get_handoff_profile("masm2d_to_madm1", source_model_type=stream_state.model_type)
    validation = validate_handoff(stream_state, profile)
    return {
        "model_type": "mASM2d",
        "target_model_type": "mADM1",
        "concentration_units": stream_state.concentration_units,
        "components": dict(stream_state.concentrations),
        "validation": validation.to_dict(),
    }


__all__ = [
    "build_masm2d_to_madm1_state",
    "build_masm2d_to_mcas",
    "build_watertap_asm2d_state",
    "build_watertap_madm1_state",
    "build_watertap_mcas_components",
    "derive_x_tss",
]
