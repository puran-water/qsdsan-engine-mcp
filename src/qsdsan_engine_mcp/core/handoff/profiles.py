"""Strict cross-engine handoff profile definitions."""

from __future__ import annotations

from dataclasses import dataclass

from qsdsan_engine_mcp.core.components.catalog import normalize_model_type
from qsdsan_engine_mcp.core.contracts.stream_state import ConcentrationUnits


@dataclass(frozen=True)
class RequiredAnalyte:
    """One source analyte required before a handoff builder can run."""

    name: str
    units: ConcentrationUnits
    source_component: str | None = None
    derivable_from: tuple[str, ...] = ()
    basis: str = "mass"

    @property
    def source_key(self) -> str:
        return self.source_component or self.name


@dataclass(frozen=True)
class HandoffProfile:
    """Fail-closed contract for one source/target model boundary."""

    name: str
    target_engine: str
    source_model_type: str
    target_model_type: str
    required_analytes: tuple[RequiredAnalyte, ...]
    downstream_profile: str | None = None
    strict: bool = True
    description: str = ""


WATERTAP_MCAS_REQUIRED_ANALYTES = (
    RequiredAnalyte("Na_+", source_component="S_Na", units="mg/L"),
    RequiredAnalyte("Ca_2+", source_component="S_Ca", units="mg/L"),
    RequiredAnalyte("Mg_2+", source_component="S_Mg", units="mg/L"),
    RequiredAnalyte("Cl_-", source_component="S_Cl", units="mg/L"),
    RequiredAnalyte("SO4_2-", source_component="S_SO4", units="mg/L", basis="S"),
)

ASM2D_X_TSS_PARTICULATES = (
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

MADM1_X_TSS_PARTICULATES = (
    "X_ch",
    "X_pr",
    "X_li",
    "X_su",
    "X_aa",
    "X_fa",
    "X_c4",
    "X_pro",
    "X_ac",
    "X_h2",
    "X_I",
)

WATERTAP_ASM2D_REQUIRED_ANALYTES = (
    RequiredAnalyte("H2O", units="mg/L"),
    RequiredAnalyte("S_O2", units="mg/L"),
    RequiredAnalyte("S_F", units="mg/L", basis="COD"),
    RequiredAnalyte("S_A", units="mg/L", basis="COD"),
    RequiredAnalyte("S_NH4", units="mg/L", basis="N"),
    RequiredAnalyte("S_NO3", units="mg/L", basis="N"),
    RequiredAnalyte("S_PO4", units="mg/L", basis="P"),
    RequiredAnalyte("S_I", units="mg/L", basis="COD"),
    RequiredAnalyte("S_N2", units="mg/L", basis="N"),
    RequiredAnalyte("S_ALK", units="mg/L", basis="C"),
    RequiredAnalyte("X_I", units="mg/L", basis="COD"),
    RequiredAnalyte("X_S", units="mg/L", basis="COD"),
    RequiredAnalyte("X_H", units="mg/L", basis="COD"),
    RequiredAnalyte("X_PAO", units="mg/L", basis="COD"),
    RequiredAnalyte("X_PP", units="mg/L", basis="P"),
    RequiredAnalyte("X_PHA", units="mg/L", basis="COD"),
    RequiredAnalyte("X_AUT", units="mg/L", basis="COD"),
    RequiredAnalyte("X_MeOH", units="mg/L"),
    RequiredAnalyte("X_MeP", units="mg/L", basis="P"),
    RequiredAnalyte("X_TSS", units="mg/L", derivable_from=ASM2D_X_TSS_PARTICULATES),
)

WATERTAP_MADM1_REQUIRED_ANALYTES = (
    RequiredAnalyte("H2O", units="kg/m3"),
    RequiredAnalyte("S_su", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_aa", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_fa", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_va", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_bu", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_pro", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_ac", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_h2", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_ch4", units="kg/m3", basis="COD"),
    RequiredAnalyte("S_IC", units="kg/m3", basis="C"),
    RequiredAnalyte("S_IN", units="kg/m3", basis="N"),
    RequiredAnalyte("S_I", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_ch", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_pr", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_li", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_su", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_aa", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_fa", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_c4", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_pro", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_ac", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_h2", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_I", units="kg/m3", basis="COD"),
    RequiredAnalyte("X_TSS", units="kg/m3", derivable_from=MADM1_X_TSS_PARTICULATES),
)

HANDOFF_PROFILES: dict[str, HandoffProfile] = {
    "watertap_mcas": HandoffProfile(
        name="watertap_mcas",
        target_engine="watertap",
        downstream_profile="MCAS_BRACKISH_5ION",
        source_model_type="mASM2d",
        target_model_type="MCAS_BRACKISH_5ION",
        required_analytes=WATERTAP_MCAS_REQUIRED_ANALYTES,
        description="Strict mASM2d to WaterTAP MCAS five-ion handoff.",
    ),
    "watertap_asm2d": HandoffProfile(
        name="watertap_asm2d",
        target_engine="watertap",
        source_model_type="ASM2d",
        target_model_type="ASM2d",
        required_analytes=WATERTAP_ASM2D_REQUIRED_ANALYTES,
        description="Strict QSDsan ASM2d to WaterTAP ASM2d handoff with explicit X_TSS derivation.",
    ),
    "watertap_madm1": HandoffProfile(
        name="watertap_madm1",
        target_engine="watertap",
        source_model_type="mADM1",
        target_model_type="ADM1",
        required_analytes=WATERTAP_MADM1_REQUIRED_ANALYTES,
        description="Strict QSDsan mADM1 to WaterTAP ADM1 handoff with explicit X_TSS derivation policy.",
    ),
    "masm2d_to_madm1": HandoffProfile(
        name="masm2d_to_madm1",
        target_engine="qsdsan",
        source_model_type="mASM2d",
        target_model_type="mADM1",
        required_analytes=(
            RequiredAnalyte("S_F", units="mg/L", basis="COD"),
            RequiredAnalyte("S_A", units="mg/L", basis="COD"),
            RequiredAnalyte("S_NH4", units="mg/L", basis="N"),
            RequiredAnalyte("S_PO4", units="mg/L", basis="P"),
            RequiredAnalyte("X_S", units="mg/L", basis="COD"),
            RequiredAnalyte("X_I", units="mg/L", basis="COD"),
        ),
        description="Strict internal mASM2d to mADM1 conversion preflight.",
    ),
}

_PROFILE_ALIASES = {
    "MCAS_BRACKISH_5ION": "watertap_mcas",
    "WATERTAP_MCAS": "watertap_mcas",
}


def get_handoff_profile(name: str, *, source_model_type: str | None = None) -> HandoffProfile:
    """Return a strict handoff profile by public profile or internal name."""

    key = _PROFILE_ALIASES.get(name.upper(), name.lower())
    try:
        profile = HANDOFF_PROFILES[key]
    except KeyError as exc:
        valid = ", ".join(sorted(set(HANDOFF_PROFILES) | set(_PROFILE_ALIASES)))
        raise ValueError(f"Unknown handoff profile {name!r}. Valid: {valid}") from exc

    if source_model_type is not None:
        source = normalize_model_type(source_model_type)
        expected = normalize_model_type(profile.source_model_type)
        if source != expected:
            raise ValueError(
                f"Handoff profile {profile.name} requires source model {expected}, got {source}."
            )
    return profile


def list_strict_handoff_profiles() -> list[dict[str, object]]:
    """Serialize strict profile metadata for resources and tests."""

    return [
        {
            "name": profile.name,
            "target_engine": profile.target_engine,
            "source_model_type": profile.source_model_type,
            "target_model_type": profile.target_model_type,
            "downstream_profile": profile.downstream_profile,
            "required_analytes": [analyte.name for analyte in profile.required_analytes],
            "strict": profile.strict,
            "description": profile.description,
        }
        for profile in sorted(HANDOFF_PROFILES.values(), key=lambda item: item.name)
    ]


def required_source_components(profile: HandoffProfile) -> tuple[str, ...]:
    """Return all direct source components required by a profile."""

    components: list[str] = []
    for analyte in profile.required_analytes:
        if analyte.source_component:
            components.append(analyte.source_component)
        components.extend(analyte.derivable_from)
    return tuple(dict.fromkeys(components))


__all__ = [
    "ASM2D_X_TSS_PARTICULATES",
    "HANDOFF_PROFILES",
    "HandoffProfile",
    "MADM1_X_TSS_PARTICULATES",
    "RequiredAnalyte",
    "get_handoff_profile",
    "list_strict_handoff_profiles",
    "required_source_components",
]
