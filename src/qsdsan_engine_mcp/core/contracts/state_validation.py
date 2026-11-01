"""Pydantic reducer for state validation tool results."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, computed_field

from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel


class StateValidationCheck(StrictFiniteBaseModel):
    """One primitive validation outcome."""

    name: str
    passed: bool
    severity: Literal["error", "warning", "info"] = "error"
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StateValidationReport(StrictFiniteBaseModel):
    """Single reducer for validate_state output booleans and messages."""

    model_type: str
    checks: list[StateValidationCheck] = Field(default_factory=list)
    charge_balance: dict[str, Any] | None = None
    mass_balance_payload: dict[str, Any] | None = None
    missing_components: list[str] = Field(default_factory=list)
    extra_components: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def errors(self) -> list[str]:
        return [
            check.message or check.name
            for check in self.checks
            if not check.passed and check.severity == "error"
        ]

    @computed_field
    @property
    def warnings(self) -> list[str]:
        messages = [
            check.message or check.name
            for check in self.checks
            if (not check.passed and check.severity == "warning")
        ]
        if self.mass_balance_payload:
            messages.extend(str(item) for item in self.mass_balance_payload.get("warnings", []))
        return messages

    @computed_field
    @property
    def is_valid(self) -> bool:
        return not any(not check.passed and check.severity == "error" for check in self.checks)

    @computed_field
    @property
    def mass_balance(self) -> dict[str, Any] | None:
        if self.mass_balance_payload is None:
            return None
        payload = dict(self.mass_balance_payload)
        payload["passed"] = self.is_valid
        return payload


__all__ = ["StateValidationCheck", "StateValidationReport"]
