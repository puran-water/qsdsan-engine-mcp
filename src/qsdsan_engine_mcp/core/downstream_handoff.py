"""Prepare QSDsan states for downstream WaterTAP workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from qsdsan_engine_mcp.core.converters import convert_state
from qsdsan_engine_mcp.core.contracts.stream_state import StreamState
from qsdsan_engine_mcp.core.handoff.builders import build_masm2d_to_mcas
from qsdsan_engine_mcp.core.handoff.profiles import get_handoff_profile as get_strict_handoff_profile
from qsdsan_engine_mcp.core.handoff.validators import validate_handoff
from qsdsan_engine_mcp.core.model_types import ModelType, model_type_value

from puran_mcp_support.engineering.converters.masm2d_to_mcas import masm2d_to_mcas
from puran_mcp_support.engineering.converters.watertap_mcas import eng_mcas_to_component_charges, eng_mcas_to_ed_nacl_pair_state, eng_mcas_to_watertap_components
from puran_mcp_support.engineering.converters.watertap_thermal import eng_mcas_to_md_thermal_state, eng_mcas_to_nacl_thermal_state


@dataclass(frozen=True)
class DownstreamHandoffProfile:
    name: str
    target_engine: str
    session_preset: str
    property_package: str
    description: str
    supported_source_models: tuple[str, ...]
    required_analytes: tuple[str, ...] = ()
    strict_contract: bool = True
    estimated_by_design: bool = False
    public_supported: bool = True
    public_reason: Optional[str] = None


HANDOFF_PROFILES: Dict[str, DownstreamHandoffProfile] = {
    "MCAS_BRACKISH_5ION": DownstreamHandoffProfile(
        name="MCAS_BRACKISH_5ION",
        target_engine="watertap",
        session_preset="MCAS_BRACKISH_5ION",
        property_package="MCAS",
        description="Five-ion WaterTAP MCAS handoff for ED, RO, NF, and softening workflows.",
        supported_source_models=("mASM2d", "mADM1"),
        required_analytes=("Na_+", "Ca_2+", "Mg_2+", "Cl_-", "SO4_2-"),
    ),
    "ZERO_ORDER_WATER": DownstreamHandoffProfile(
        name="ZERO_ORDER_WATER",
        target_engine="watertap",
        session_preset="ZERO_ORDER_WATER",
        property_package="ZERO_ORDER",
        description="Bulk wastewater zero-order handoff for reuse and ZLD pretreatment trains.",
        supported_source_models=("ASM1", "ASM2d", "mASM2d", "ADM1", "mADM1"),
        strict_contract=False,
        estimated_by_design=True,
        public_reason=(
            "Approximate public profile that collapses biological plant states to a bulk "
            "zero-order wastewater constituent set."
        ),
    ),
    "NACL_BRINE_THERMAL": DownstreamHandoffProfile(
        name="NACL_BRINE_THERMAL",
        target_engine="watertap",
        session_preset="NACL_BRINE_THERMAL",
        property_package="CRYST_NACL",
        description="NaCl-equivalent thermal brine handoff for crystallizer and thermal concentration workflows.",
        supported_source_models=("mASM2d", "mADM1"),
        strict_contract=False,
        estimated_by_design=True,
        public_reason=(
            "Approximate public profile that collapses source chemistry to an NaCl-equivalent "
            "thermal-brine proxy."
        ),
    ),
    "MD_THERMAL": DownstreamHandoffProfile(
        name="MD_THERMAL",
        target_engine="watertap",
        session_preset="MD_THERMAL",
        property_package="SEAWATER",
        description="Seawater/TDS membrane-distillation handoff.",
        supported_source_models=("mASM2d", "mADM1"),
        strict_contract=False,
        estimated_by_design=True,
        public_reason=(
            "Approximate public profile that collapses source chemistry to a TDS-equivalent "
            "membrane-distillation proxy."
        ),
    ),
    "ED_NACL_PAIR": DownstreamHandoffProfile(
        name="ED_NACL_PAIR",
        target_engine="watertap",
        session_preset="ED_NACL_PAIR",
        property_package="MCAS",
        description="Approximate sodium/chloride-pair handoff for reduced-chemistry electrodialysis workflows.",
        supported_source_models=("mASM2d", "mADM1"),
        strict_contract=False,
        estimated_by_design=True,
        public_reason=(
            "Approximate public profile that reduces broader chemistry to an Na+/Cl- pair "
            "for the narrow WaterTAP electrodialysis public profile."
        ),
    ),
}

_MCAS_BRACKISH_COMPONENTS = ("Na_+", "Ca_2+", "Mg_2+", "Cl_-", "SO4_2-")
_ZERO_ORDER_PUBLIC_SOLUTES = (
    "viruses_enteric",
    "tss",
    "toc",
    "cryptosporidium",
    "total_coliforms_fecal_ecoli",
    "tds",
    "nonvolatile_toc",
    "ammonium_as_nitrogen",
    "nitrate",
    "phosphates",
    "magnesium",
    "calcium",
    "sulfate",
    "acetic_acid",
)

_ZERO_ORDER_GAS_IDS = {"s_ch4", "s_co2", "s_h2", "s_h2s", "s_n2", "s_nh3", "s_o2"}
_ZERO_ORDER_SOLID_IDS = {
    "ash",
    "inorganic_solid",
    "membrane",
    "organic_liquid",
    "organic_solid",
    "otherss",
    "struvite",
    "wwtsludge",
}
_ZERO_ORDER_SOLUBLE_ORGANIC_IDS = {
    "s_a",
    "s_aa",
    "s_ac",
    "s_bu",
    "s_f",
    "s_fa",
    "s_i",
    "s_pro",
    "s_s",
    "s_su",
    "s_va",
}
_ZERO_ORDER_NONVOLATILE_ORGANIC_IDS = {"s_aa", "s_f", "s_i", "s_s", "s_su"}


def list_public_profile_names() -> List[str]:
    return sorted(
        profile.name for profile in HANDOFF_PROFILES.values() if profile.public_supported
    )


def _stream_concentrations_mg_l(state: StreamState) -> Dict[str, float]:
    scale = 1000.0 if state.concentration_units == "kg/m3" else 1.0
    return {
        component_id: float(value) * scale
        for component_id, value in state.concentrations.items()
        if value is not None
    }


def _zero_order_sum(concentrations_mg_l: Dict[str, float], predicate) -> float:
    return sum(value for comp_id, value in concentrations_mg_l.items() if value > 0 and predicate(comp_id))


def _zero_order_to_output_units(value_mg_l: float, concentration_units: str) -> float:
    if concentration_units == "kg/m3":
        return value_mg_l / 1000.0
    if concentration_units == "mg/L":
        return value_mg_l
    raise ValueError(f"Unsupported concentration_units '{concentration_units}'")


def _stream_state_to_zero_order(
    state: StreamState,
    *,
    concentration_units: str = "kg/m3",
    solute_list: Optional[tuple[str, ...]] = None,
) -> Dict[str, Any]:
    concentrations_mg_l = _stream_concentrations_mg_l(state)

    def _lower(component_id: str) -> str:
        return component_id.lower()

    def _is_gas(component_id: str) -> bool:
        return _lower(component_id) in _ZERO_ORDER_GAS_IDS

    def _is_solid(component_id: str) -> bool:
        return component_id.startswith("X_") or _lower(component_id) in _ZERO_ORDER_SOLID_IDS

    def _is_soluble_organic(component_id: str) -> bool:
        return _lower(component_id) in _ZERO_ORDER_SOLUBLE_ORGANIC_IDS

    def _is_nonvolatile_organic(component_id: str) -> bool:
        return _lower(component_id) in _ZERO_ORDER_NONVOLATILE_ORGANIC_IDS

    estimated_fields: List[str] = []
    soluble_organics_mg_l = _zero_order_sum(concentrations_mg_l, _is_soluble_organic)
    nonvolatile_organics_mg_l = _zero_order_sum(concentrations_mg_l, _is_nonvolatile_organic)
    toc_mg_l = soluble_organics_mg_l * 0.375
    nonvolatile_toc_mg_l = nonvolatile_organics_mg_l * 0.375
    if toc_mg_l > 0:
        estimated_fields.append("toc")
    if nonvolatile_toc_mg_l > 0:
        estimated_fields.append("nonvolatile_toc")

    acetic_acid_mg_l = concentrations_mg_l.get("S_A", 0.0) + concentrations_mg_l.get("S_ac", 0.0)
    if acetic_acid_mg_l > 0:
        estimated_fields.append("acetic_acid")

    component_map_mg_l = {
        # H2O is the solvent (~1e6 mg/L on the canonical basis), not a
        # dissolved solid; including it would report a ~1,000,000 mg/L TDS.
        "tds": _zero_order_sum(
            concentrations_mg_l,
            lambda comp_id: comp_id != "H2O" and not _is_gas(comp_id) and not _is_solid(comp_id),
        ),
        "tss": _zero_order_sum(concentrations_mg_l, _is_solid),
        "toc": toc_mg_l,
        "nonvolatile_toc": nonvolatile_toc_mg_l,
        "ammonium_as_nitrogen": concentrations_mg_l.get("S_NH4", concentrations_mg_l.get("S_IN", 0.0)),
        "nitrate": concentrations_mg_l.get("S_NO3", 0.0),
        "phosphates": concentrations_mg_l.get("S_PO4", concentrations_mg_l.get("S_IP", 0.0)),
        "magnesium": concentrations_mg_l.get("S_Mg", 0.0),
        "calcium": concentrations_mg_l.get("S_Ca", 0.0),
        "sulfate": concentrations_mg_l.get("S_SO4", 0.0),
        "acetic_acid": acetic_acid_mg_l,
    }
    if solute_list is None:
        solute_list = tuple(name for name, value in component_map_mg_l.items() if value > 0)

    components = {
        name: _zero_order_to_output_units(component_map_mg_l.get(name, 0.0), concentration_units)
        for name in solute_list
        if component_map_mg_l.get(name, 0.0) > 0
    }
    return {
        "flow_vol_m3_hr": state.flow_m3_d / 24.0 if state.flow_m3_d > 0 else None,
        "temperature_C": _temperature_C(state),
        "pressure_bar": 1.013,
        "components": components,
        "solute_list": list(solute_list),
        "concentration_units": concentration_units,
        "concentration_basis": "mass",
        "estimated_fields": sorted(set(estimated_fields)),
        "missing_requested_fields": [name for name in solute_list if component_map_mg_l.get(name, 0.0) <= 0],
        "available_fields": sorted(name for name, value in component_map_mg_l.items() if value > 0),
    }


def list_handoff_profiles(public_only: bool = True) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for profile in HANDOFF_PROFILES.values():
        if public_only and not profile.public_supported:
            continue
        profiles.append(
            {
                "name": profile.name,
                "target_engine": profile.target_engine,
                "target_profile": profile.name,
                "session_preset": profile.session_preset,
                "property_package": profile.property_package,
                "description": profile.description,
                "supported_source_models": list(profile.supported_source_models),
                "required_analytes": list(profile.required_analytes),
                "estimated_by_design": profile.estimated_by_design,
                "strict_contract": profile.strict_contract,
            }
        )
    return profiles


def stream_state_from_payload(payload: Dict[str, Any]) -> StreamState:
    """Validate a public handoff payload as a strict StreamState."""

    return StreamState.model_validate(payload)


def load_handoff_state_from_job_dir(job_dir: Path, stream_id: Optional[str] = None) -> StreamState:
    """Load a StreamState artifact from one completed job directory."""

    if stream_id:
        stream_states_path = job_dir / "stream_states.json"
        if not stream_states_path.exists():
            raise ValueError(
                f"Job '{job_dir.name}' has no stream_states artifact. "
                "Re-run flowsheet simulation after this feature was introduced, or provide `state` directly."
            )
        with open(stream_states_path) as f:
            stream_states = json.load(f)
        if stream_id not in stream_states:
            available = ", ".join(sorted(stream_states)[:10])
            raise ValueError(
                f"Stream '{stream_id}' not found in job '{job_dir.name}'. "
                f"Available streams: {available}"
            )
        return stream_state_from_payload(stream_states[stream_id])

    for candidate in ("output_state.json", "output.json"):
        path = job_dir / candidate
        if not path.exists():
            continue
        with open(path) as f:
            payload = json.load(f)
        if "model_type" in payload:
            return stream_state_from_payload(payload)
        if "output_state" in payload and isinstance(payload["output_state"], dict):
            return stream_state_from_payload(payload["output_state"])

    raise ValueError(
        f"Job '{job_dir.name}' does not contain a directly exportable StreamState. "
        "Provide `state` explicitly or use `stream_id` for flowsheet jobs."
    )


def _normalize_profile(name: str) -> DownstreamHandoffProfile:
    key = name.upper()
    if key == "WATERTAP_MCAS":
        key = "MCAS_BRACKISH_5ION"
    if key not in HANDOFF_PROFILES:
        valid = ", ".join(list_public_profile_names())
        raise ValueError(
            f"Unknown downstream handoff profile '{name}'. Valid public profiles: {valid}"
        )
    return HANDOFF_PROFILES[key]


def _unsupported_public_profile_error(profile: DownstreamHandoffProfile) -> str:
    reason = profile.public_reason or "Profile is not ready for public downstream handoff."
    return (
        f"Profile {profile.name} is not public in this phase. {reason} "
        "The public QSDsan -> WaterTAP handoff surface currently supports only exact, "
        "non-estimated mappings."
    )


def _canonical_model_name(model_type: ModelType | str) -> str:
    return model_type_value(model_type)


def _temperature_C(state: StreamState) -> float:
    return state.temperature_K - 273.15


def _to_masm2d_for_mcas(state: StreamState) -> tuple[StreamState, List[Dict[str, Any]]]:
    model_name = _canonical_model_name(state.model_type)
    chain: List[Dict[str, Any]] = []

    if model_name == ModelType.MASM2D.value:
        return state, chain
    if model_name == ModelType.MADM1.value:
        converted, meta = convert_state(state, ModelType.MASM2D, validate=True)
        chain.append(
            {
                "step": "convert_state",
                "from_model": model_name,
                "to_model": ModelType.MASM2D.value,
                "metadata": meta,
            }
        )
        return converted, chain

    raise ValueError(
        f"MCAS handoff requires mASM2d or mADM1 source states, got {model_name}."
    )


def _assert_public_profile(profile: DownstreamHandoffProfile) -> None:
    if not profile.public_supported:
        raise ValueError(_unsupported_public_profile_error(profile))


def _validate_complete_mapping(
    profile: DownstreamHandoffProfile,
    *,
    missing_or_estimated_species: List[str],
) -> None:
    if profile.strict_contract and missing_or_estimated_species:
        raise ValueError(
            f"Profile {profile.name} cannot be prepared without estimates for: "
            f"{', '.join(sorted(missing_or_estimated_species))}. "
            "This boundary hard-fails rather than guessing."
        )


def _build_mcas_property_package_config() -> Dict[str, Any]:
    return {
        "solute_list": list(_MCAS_BRACKISH_COMPONENTS),
        "charge": {
            "Na_+": 1,
            "Ca_2+": 2,
            "Mg_2+": 2,
            "Cl_-": -1,
            "SO4_2-": -2,
        },
        "mw_data": {
            "Na_+": 0.02299,
            "Ca_2+": 0.040078,
            "Mg_2+": 0.024305,
            "Cl_-": 0.03545,
            "SO4_2-": 0.09606,
        },
        "stokes_radius_data": {
            "Na_+": 1.84e-10,
            "Ca_2+": 3.09e-10,
            "Mg_2+": 3.47e-10,
            "Cl_-": 1.21e-10,
            "SO4_2-": 2.30e-10,
        },
        "diffusivity_data": {
            "Na_+": 1.33e-09,
            "Ca_2+": 9.20e-10,
            "Mg_2+": 7.06e-10,
            "Cl_-": 2.03e-09,
            "SO4_2-": 1.06e-09,
        },
        "activity_coefficient_model": "davies",
        "density_calculation": "constant",
    }


def _build_contract(
    *,
    profile: DownstreamHandoffProfile,
    state_payload: Dict[str, Any],
    completeness: Dict[str, Any],
    missing_or_estimated_species: List[str],
    assumptions: List[str],
    conversion_chain: List[Dict[str, Any]],
    source_model_type: str,
) -> Dict[str, Any]:
    property_package_config = state_payload.pop("_property_package_config")
    return {
        "target_engine": profile.target_engine,
        "target_profile": profile.name,
        "session_preset": profile.session_preset,
        "property_package": profile.property_package,
        "property_package_config": property_package_config,
        "state_payload": state_payload,
        "completeness": completeness,
        "missing_or_estimated_species": missing_or_estimated_species,
        "assumptions": assumptions,
        "conversion_chain": conversion_chain,
        "source_model_type": source_model_type,
        "strict_contract": profile.strict_contract,
        "estimated_by_design": profile.estimated_by_design,
    }


def _build_mcas_handoff(state: StreamState, profile: DownstreamHandoffProfile) -> Dict[str, Any]:
    masm2d_state, chain = _to_masm2d_for_mcas(state)
    strict_profile = get_strict_handoff_profile(
        "watertap_mcas",
        source_model_type=masm2d_state.model_type,
    )
    validation = validate_handoff(masm2d_state, strict_profile)
    mcas = build_masm2d_to_mcas(masm2d_state)
    components = eng_mcas_to_watertap_components(mcas, include_zero=True)
    charges = eng_mcas_to_component_charges(mcas, include_zero=True)

    state_payload = {
        "_property_package_config": _build_mcas_property_package_config(),
        "flow_vol_m3_hr": masm2d_state.flow_m3_d / 24.0,
        "temperature_C": _temperature_C(masm2d_state),
        "pressure_bar": 1.013,
        "components": {
            comp_id: components[comp_id]
            for comp_id in _MCAS_BRACKISH_COMPONENTS
        },
        "concentration_units": "mg/L",
        "concentration_basis": "mass",
        "component_charges": {
            comp_id: charges[comp_id]
            for comp_id in _MCAS_BRACKISH_COMPONENTS
        },
    }
    assumptions = [
        "The biological state is projected onto the exact five-ion WaterTAP MCAS_BRACKISH_5ION profile.",
        "Non-target wastewater species are intentionally omitted because they are outside the downstream public property package.",
    ]
    missing_or_estimated_species: List[str] = []
    _validate_complete_mapping(profile, missing_or_estimated_species=missing_or_estimated_species)
    return _build_contract(
        profile=profile,
        state_payload=state_payload,
        completeness={
            "status": "complete",
            "required_analytes": list(profile.required_analytes),
            "handoff_validation": validation.to_dict(),
            "estimated_fields": [],
            "machine_ready": True,
        },
        missing_or_estimated_species=missing_or_estimated_species,
        assumptions=assumptions,
        conversion_chain=chain,
        source_model_type=_canonical_model_name(state.model_type),
    )


def _build_zero_order_handoff(state: StreamState, profile: DownstreamHandoffProfile) -> Dict[str, Any]:
    payload = _stream_state_to_zero_order(
        state,
        solute_list=_ZERO_ORDER_PUBLIC_SOLUTES,
    )
    for solute in payload["solute_list"]:
        payload["components"].setdefault(solute, 0.0)
    missing_or_estimated_species = sorted(
        set(payload["estimated_fields"]) | set(payload["missing_requested_fields"])
    )
    _validate_complete_mapping(profile, missing_or_estimated_species=missing_or_estimated_species)
    return _build_contract(
        profile=profile,
        state_payload={
            "_property_package_config": {
                "water_source": "municipal",
                "solute_list": payload["solute_list"],
            },
            "flow_vol_m3_hr": payload["flow_vol_m3_hr"],
            "temperature_C": payload["temperature_C"],
            "pressure_bar": payload["pressure_bar"],
            "components": payload["components"],
            "concentration_units": payload["concentration_units"],
            "concentration_basis": payload["concentration_basis"],
        },
        completeness={
            "status": "estimated" if payload["estimated_fields"] else "complete",
            "estimated_fields": payload["estimated_fields"],
            "missing_requested_fields": payload["missing_requested_fields"],
            "available_fields": payload["available_fields"],
            "machine_ready": True,
        },
        missing_or_estimated_species=missing_or_estimated_species,
        assumptions=[
            "ZERO_ORDER_WATER currently requires estimated bulk wastewater fields for biological plant states."
        ],
        conversion_chain=[],
        source_model_type=_canonical_model_name(state.model_type),
    )


def _build_thermal_handoff(state: StreamState, profile: DownstreamHandoffProfile) -> Dict[str, Any]:
    masm2d_state, chain = _to_masm2d_for_mcas(state)
    mcas = masm2d_to_mcas(
        masm2d_state.concentrations,
        charge_balance=True,
        temperature_c=_temperature_C(masm2d_state),
    )

    if profile.name == "NACL_BRINE_THERMAL":
        payload = eng_mcas_to_nacl_thermal_state(
            mcas,
            flow_m3_hr=masm2d_state.flow_m3_d / 24.0,
            temperature_c=_temperature_C(masm2d_state),
        )
    else:
        payload = eng_mcas_to_md_thermal_state(
            mcas,
            flow_m3_hr=masm2d_state.flow_m3_d / 24.0,
            temperature_c=_temperature_C(masm2d_state),
        )

    missing_or_estimated_species = sorted(set(payload["estimated_fields"]))
    _validate_complete_mapping(profile, missing_or_estimated_species=missing_or_estimated_species)
    return _build_contract(
        profile=profile,
        state_payload={
            "_property_package_config": {},
            "flow_vol_m3_hr": payload["flow_vol_m3_hr"],
            "temperature_C": payload["temperature_C"],
            "pressure_bar": payload["pressure_bar"],
            "components": payload["components"],
            "concentration_units": payload["concentration_units"],
            "concentration_basis": payload["concentration_basis"],
        },
        completeness={
            "status": "estimated",
            "estimated_fields": payload["estimated_fields"],
            "available_fields": payload["available_fields"],
            "source_breakdown_mg_l": payload["source_breakdown_mg_l"],
            "machine_ready": True,
        },
        missing_or_estimated_species=missing_or_estimated_species,
        assumptions=[
            "Thermal handoff currently collapses the source chemistry to a proxy thermal package representation."
        ],
        conversion_chain=chain,
        source_model_type=_canonical_model_name(state.model_type),
    )


def _build_ed_nacl_pair_handoff(state: StreamState, profile: DownstreamHandoffProfile) -> Dict[str, Any]:
    masm2d_state, chain = _to_masm2d_for_mcas(state)
    mcas = masm2d_to_mcas(
        masm2d_state.concentrations,
        charge_balance=True,
        temperature_c=_temperature_C(masm2d_state),
    )
    payload = eng_mcas_to_ed_nacl_pair_state(
        mcas,
        flow_m3_hr=masm2d_state.flow_m3_d / 24.0,
        temperature_c=_temperature_C(masm2d_state),
    )
    missing_or_estimated_species = sorted(
        {
            "Na_+",
            "Cl_-",
            "Ca_2+",
            "Mg_2+",
            "SO4_2-",
        }
    )
    _validate_complete_mapping(profile, missing_or_estimated_species=missing_or_estimated_species)
    return _build_contract(
        profile=profile,
        state_payload={
            "_property_package_config": {
                "solute_list": ["Na_+", "Cl_-"],
                "charge": {"Na_+": 1, "Cl_-": -1},
                "mw_data": {"Na_+": 0.02299, "Cl_-": 0.03545},
                "elec_mobility_data": {"Na_+": 5.19e-8, "Cl_-": 7.92e-8},
            },
            "flow_vol_m3_hr": payload["flow_vol_m3_hr"],
            "temperature_C": payload["temperature_C"],
            "pressure_bar": payload["pressure_bar"],
            "components": payload["components"],
            "concentration_units": payload["concentration_units"],
            "concentration_basis": payload["concentration_basis"],
            "component_charges": payload["component_charges"],
        },
        completeness={
            "status": "estimated",
            "estimated_fields": payload["estimated_fields"],
            "available_fields": payload["available_fields"],
            "source_breakdown_mg_l": payload["source_breakdown_mg_l"],
            "machine_ready": True,
        },
        missing_or_estimated_species=missing_or_estimated_species,
        assumptions=[
            "Electrodialysis ingress currently collapses broader chemistry to a sodium/chloride pair.",
            "The reduced-chemistry ED profile is approximate and should not be treated as an exact ion-by-ion boundary."
        ],
        conversion_chain=chain,
        source_model_type=_canonical_model_name(state.model_type),
    )


def prepare_downstream_handoff(state: StreamState, target_profile: str) -> Dict[str, Any]:
    profile = _normalize_profile(target_profile)
    _assert_public_profile(profile)
    model_name = _canonical_model_name(state.model_type)

    if model_name not in profile.supported_source_models:
        raise ValueError(
            f"Profile {profile.name} supports source models {profile.supported_source_models}, got {model_name}."
        )

    if profile.name == "MCAS_BRACKISH_5ION":
        return _build_mcas_handoff(state, profile)
    if profile.name == "ZERO_ORDER_WATER":
        return _build_zero_order_handoff(state, profile)
    if profile.name in {"NACL_BRINE_THERMAL", "MD_THERMAL"}:
        return _build_thermal_handoff(state, profile)
    if profile.name == "ED_NACL_PAIR":
        return _build_ed_nacl_pair_handoff(state, profile)
    raise ValueError(f"No builder registered for profile {profile.name}")


__all__ = [
    "HANDOFF_PROFILES",
    "DownstreamHandoffProfile",
    "list_handoff_profiles",
    "list_public_profile_names",
    "load_handoff_state_from_job_dir",
    "prepare_downstream_handoff",
    "stream_state_from_payload",
]
