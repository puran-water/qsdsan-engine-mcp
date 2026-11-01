"""Fail-closed validation for cross-engine handoff boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from qsdsan_engine_mcp.core.components.catalog import normalize_model_type
from qsdsan_engine_mcp.core.contracts.stream_state import StreamState, h2o_envelope_violation

from .profiles import HandoffProfile, RequiredAnalyte


@dataclass(frozen=True)
class ValidationResult:
    """Structured handoff validation outcome."""

    profile: str
    is_valid: bool
    missing_analytes: list[str] = field(default_factory=list)
    type_mismatches: list[str] = field(default_factory=list)
    unit_mismatches: list[str] = field(default_factory=list)
    value_violations: list[str] = field(default_factory=list)
    derived_analytes: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "is_valid": self.is_valid,
            "missing_analytes": list(self.missing_analytes),
            "type_mismatches": list(self.type_mismatches),
            "unit_mismatches": list(self.unit_mismatches),
            "value_violations": list(self.value_violations),
            "derived_analytes": {
                analyte: list(components)
                for analyte, components in self.derived_analytes.items()
            },
        }


class HandoffValidationError(ValueError):
    """Raised when a strict handoff profile cannot be satisfied."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        self.missing_analytes = list(result.missing_analytes)
        self.type_mismatches = list(result.type_mismatches)
        self.unit_mismatches = list(result.unit_mismatches)
        self.value_violations = list(result.value_violations)
        parts = [f"Handoff profile {result.profile} validation failed"]
        if self.missing_analytes:
            parts.append(f"missing_analytes={self.missing_analytes}")
        if self.type_mismatches:
            parts.append(f"type_mismatches={self.type_mismatches}")
        if self.unit_mismatches:
            parts.append(f"unit_mismatches={self.unit_mismatches}")
        if self.value_violations:
            parts.append(f"value_violations={self.value_violations}")
        super().__init__("; ".join(parts))


def _missing_derivation_inputs(
    stream_state: StreamState,
    analyte: RequiredAnalyte,
) -> list[str]:
    return [
        component_id
        for component_id in analyte.derivable_from
        if component_id not in stream_state.concentrations
    ]


def _analyte_is_present(stream_state: StreamState, analyte: RequiredAnalyte) -> bool:
    if analyte.source_key in stream_state.concentrations:
        return True
    if analyte.derivable_from and not _missing_derivation_inputs(stream_state, analyte):
        return True
    return False


def validate_handoff(stream_state: StreamState, profile: HandoffProfile) -> ValidationResult:
    """Validate a StreamState against one strict handoff profile."""

    type_mismatches: list[str] = []
    unit_mismatches: list[str] = []
    missing_analytes: list[str] = []
    value_violations: list[str] = []
    derived_analytes: dict[str, list[str]] = {}

    if not isinstance(stream_state, StreamState):
        type_mismatches.append(
            f"expected StreamState, got {type(stream_state).__name__}"
        )
        result = ValidationResult(
            profile=profile.name,
            is_valid=False,
            type_mismatches=type_mismatches,
        )
        raise HandoffValidationError(result)

    expected_model = normalize_model_type(profile.source_model_type)
    actual_model = normalize_model_type(stream_state.model_type)
    if actual_model != expected_model:
        type_mismatches.append(
            f"model_type expected {expected_model}, got {actual_model}"
        )

    for analyte in profile.required_analytes:
        if stream_state.concentration_units != analyte.units:
            unit_mismatches.append(
                f"{analyte.name} expected {analyte.units}, got {stream_state.concentration_units}"
            )
            continue
        if not _analyte_is_present(stream_state, analyte):
            missing_analytes.append(analyte.name)
            continue
        if analyte.source_key not in stream_state.concentrations and analyte.derivable_from:
            derived_analytes[analyte.name] = list(analyte.derivable_from)
        if analyte.source_key == "H2O":
            # A profile that requires H2O requires the actual solvent mass:
            # zero or a placeholder corrupts the downstream WaterTAP mass basis,
            # so it fails here rather than solving on fiction.
            violation = h2o_envelope_violation(
                float(stream_state.concentrations["H2O"]),
                stream_state.concentration_units,
                require_nonzero=True,
                context=f"profile {profile.name}",
            )
            if violation:
                value_violations.append(violation)

    result = ValidationResult(
        profile=profile.name,
        is_valid=not (
            missing_analytes or type_mismatches or unit_mismatches or value_violations
        ),
        missing_analytes=missing_analytes,
        type_mismatches=type_mismatches,
        unit_mismatches=unit_mismatches,
        value_violations=value_violations,
        derived_analytes=derived_analytes,
    )
    if not result.is_valid:
        raise HandoffValidationError(result)
    return result


__all__ = ["HandoffValidationError", "ValidationResult", "validate_handoff"]
