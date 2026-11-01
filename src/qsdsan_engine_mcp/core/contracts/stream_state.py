"""Canonical stream-state contract used at MCP boundaries."""

from __future__ import annotations

from typing import Dict, Literal, Optional

from pydantic import Field, ValidationInfo, field_validator, model_validator

from qsdsan_engine_mcp.core.components.catalog import ComponentBasis, basis_map_for, get_stream_state_component_ids, normalize_model_type

from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel

ConcentrationUnits = Literal["mg/L", "kg/m3"]
Phase = Literal["liquid", "gas", "solid", "mixed"]


class StreamState(StrictFiniteBaseModel):
    """Strict stream state with explicit units and model-specific components."""

    stream_id: Optional[str] = None
    model_type: str
    phase: Phase = "liquid"
    flow_m3_d: float = Field(ge=0.0)
    temperature_K: float = Field(ge=273.15, le=373.15)
    concentration_units: ConcentrationUnits
    concentrations: Dict[str, float]
    component_basis: Dict[str, ComponentBasis]

    @field_validator("model_type", mode="before")
    @classmethod
    def _normalize_model_type(cls, value: str) -> str:
        return normalize_model_type(value)

    @field_validator("concentrations")
    @classmethod
    def _validate_concentrations(cls, value: Dict[str, float], info: ValidationInfo) -> Dict[str, float]:
        model_type = info.data.get("model_type")
        if not model_type:
            return value
        allowed = set(get_stream_state_component_ids(model_type))
        extra = sorted(set(value) - allowed)
        if extra:
            raise ValueError(f"Unknown components for {model_type}: {extra}")
        negative = sorted(component_id for component_id, concentration in value.items() if concentration < 0)
        if negative:
            raise ValueError(f"Negative concentrations are not allowed: {negative}")
        return value

    @field_validator("component_basis")
    @classmethod
    def _validate_component_basis(cls, value: Dict[str, ComponentBasis], info: ValidationInfo) -> Dict[str, ComponentBasis]:
        concentrations = info.data.get("concentrations") or {}
        concentration_ids = set(concentrations)
        basis_ids = set(value)
        missing = sorted(concentration_ids - basis_ids)
        extra = sorted(basis_ids - concentration_ids)
        if missing or extra:
            raise ValueError(
                "component_basis keys must match concentrations "
                f"(missing={missing}, extra={extra})"
            )
        return value

    @model_validator(mode="after")
    def _validate_units_and_basis(self) -> "StreamState":
        if self.model_type == "mADM1" and self.concentration_units == "mg/L":
            raise ValueError("mADM1 StreamState requires kg/m3")

        validate_concentration_bounds(
            self.concentrations,
            self.model_type,
            self.concentration_units,
            phase=self.phase,
        )

        expected_basis = basis_map_for(self.model_type, self.concentrations)
        mismatches = {
            component_id: {"expected": expected, "provided": self.component_basis.get(component_id)}
            for component_id, expected in expected_basis.items()
            if self.component_basis.get(component_id) != expected
        }
        if mismatches:
            raise ValueError(f"component_basis does not match catalog: {mismatches}")
        return self

    @classmethod
    def from_concentrations(
        cls,
        *,
        model_type: str,
        flow_m3_d: float,
        temperature_K: float,
        concentration_units: ConcentrationUnits,
        concentrations: Dict[str, float],
        stream_id: Optional[str] = None,
        phase: Phase = "liquid",
    ) -> "StreamState":
        canonical_model = normalize_model_type(model_type)
        return cls(
            stream_id=stream_id,
            model_type=canonical_model,
            phase=phase,
            flow_m3_d=flow_m3_d,
            temperature_K=temperature_K,
            concentration_units=concentration_units,
            concentrations=concentrations,
            component_basis=basis_map_for(canonical_model, concentrations),
        )


def validate_concentration_bounds(
    concentrations: Dict[str, float],
    model_type: str,
    units: ConcentrationUnits = "mg/L",
    *,
    phase: Phase = "liquid",
) -> list[str]:
    """Return warnings for suspicious concentrations; raise on physical bounds.

    ASM2d/mASM2d ceilings are intentionally high engineering guardrails, not
    domestic design targets. They are based on concentrated landfill leachate
    and industrial wastewater ranges for NH4/PO4/ions, oxygen solubility above
    pure-O2 saturation near 20 C, and high-MLSS biological solids regimes.

    H2O on a liquid-phase state must be the solvent mass: nonzero values
    outside :data:`H2O_SOLVENT_ENVELOPE` hard-fail. Absent or exactly-0.0 H2O
    stays legal at this layer (handoff profiles that require H2O enforce
    nonzero separately). Gas/solid/mixed phases carry water vapor or moisture,
    not a bulk solvent, so the envelope does not apply there.
    """

    warnings: list[str] = []
    canonical_model = normalize_model_type(model_type)

    if phase == "liquid":
        h2o = concentrations.get("H2O")
        if h2o is not None:
            h2o_violation = h2o_envelope_violation(float(h2o), units)
            if h2o_violation:
                raise ValueError(h2o_violation)

    if canonical_model in ("ASM2d", "mASM2d") and units == "mg/L":
        for comp, ceiling in PHYSICAL_CONCENTRATION_CEILINGS_MG_L.items():
            if comp not in concentrations:
                continue
            value = concentrations[comp]
            if comp in PHYSICAL_WARNING_ONLY_COMPONENTS:
                if value >= 0.8 * ceiling:
                    warnings.append(
                        f"{comp}={value} mg/L is above 80% of the screening ceiling "
                        f"for {canonical_model} ({ceiling} mg/L)"
                    )
                continue
            if value > ceiling:
                raise ValueError(
                    f"{comp}={value} mg/L exceeds physical concentration ceiling "
                    f"for {canonical_model} ({ceiling} mg/L)"
                )
            if value >= 0.8 * ceiling:
                warnings.append(
                    f"{comp}={value} mg/L is above 80% of the physical ceiling "
                    f"for {canonical_model} ({ceiling} mg/L)"
                )

    if canonical_model in ("ASM2d", "ASM1", "mASM2d") and units == "mg/L":
        for comp, val in concentrations.items():
            if comp == "H2O":
                # The solvent legitimately sits near 1e6 mg/L; it is screened
                # by the hard H2O envelope above, not the solute heuristics.
                continue
            if 0 < val < 0.1:
                warnings.append(
                    f"{comp}={val} mg/L is suspiciously low "
                    f"(did you mean kg/m3?)"
                )
            if val > 50000:
                warnings.append(f"{comp}={val} mg/L is suspiciously high")

    elif canonical_model in ("mADM1", "ADM1") and units == "kg/m3":
        for comp, val in concentrations.items():
            if comp == "H2O":
                # ~1000 kg/m3 is the correct solvent mass on this basis.
                continue
            if val > 100:
                warnings.append(
                    f"{comp}={val} kg/m3 is suspiciously high "
                    f"(did you mean mg/L?)"
                )

    return warnings


#: Physical envelope for H2O as the solvent mass, per concentration basis.
#: The lower edge admits concentrated sludges/brines where solids displace
#: water; the upper edge is just above pure water density.
H2O_SOLVENT_ENVELOPE: dict[str, tuple[float, float]] = {
    "mg/L": (8.0e5, 1.05e6),
    "kg/m3": (800.0, 1050.0),
}


def h2o_envelope_violation(
    value: float,
    units: ConcentrationUnits,
    *,
    require_nonzero: bool = False,
    context: str = "",
) -> Optional[str]:
    """Return an error message when H2O is not a plausible solvent mass.

    ``value == 0.0`` is legal unless ``require_nonzero`` is set (handoff
    profiles that list H2O as a required analyte set it). Returns ``None``
    when the value is acceptable.
    """

    lower, upper = H2O_SOLVENT_ENVELOPE[units]
    prefix = f"{context}: " if context else ""
    remediation = (
        "H2O must be the solvent mass (~1e6 mg/L); a placeholder like 1000 corrupts "
        "downstream WaterTAP handoffs. Set H2O to ~1e6 on a mg/L basis or ~1000 on a "
        "kg/m3 basis"
    )
    if value == 0.0:
        if not require_nonzero:
            return None
        return (
            f"{prefix}H2O=0.0 {units} but H2O is a required analyte here. "
            f"{remediation}, inside [{lower:g}, {upper:g}] {units}."
        )
    if value < lower or value > upper:
        return (
            f"{prefix}H2O={value:g} {units} is outside the solvent envelope "
            f"[{lower:g}, {upper:g}] {units}. {remediation}; omit H2O or set it to 0.0 "
            "only where the state intentionally carries no solvent accounting."
        )
    return None


PHYSICAL_CONCENTRATION_CEILINGS_MG_L: dict[str, float] = {
    "S_NH4": 5000.0,
    "S_PO4": 1000.0,
    "S_O2": 60.0,
    "S_Mg": 5000.0,
    "S_F": 100.0,
    "S_K": 5000.0,
    "X_PAO": 50000.0,
    "X_PHA": 50000.0,
    "X_PP": 50000.0,
}

# The requested S_F screen is warning-only because QSDsan ASM2d/mASM2d uses
# S_F for fermentable soluble COD, not fluoride; domestic/examples commonly
# sit near or above 100 mgCOD/L.
PHYSICAL_WARNING_ONLY_COMPONENTS = {"S_F"}


__all__ = [
    "ConcentrationUnits",
    "H2O_SOLVENT_ENVELOPE",
    "PHYSICAL_CONCENTRATION_CEILINGS_MG_L",
    "PHYSICAL_WARNING_ONLY_COMPONENTS",
    "Phase",
    "StreamState",
    "h2o_envelope_violation",
    "validate_concentration_bounds",
]
