"""
PlantState - Standard state vector for plant-wide simulation.

This module defines the core data structures for explicit state passing
between simulation tools, enabling stateless operation and parallel
design exploration.

Key Design Decisions:
1. Explicit state passing (no global state)
2. Model-type discriminated union (mADM1, ASM2d, mASM2d, ASM1)
3. JSON-serializable for MCP/CLI transport
4. Pydantic validation for component consistency
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, Optional, Literal
from enum import Enum
import json


class ModelType(str, Enum):
    """Supported biological process models."""
    MADM1 = "mADM1"      # Modified ADM1 (63 components, 4 biogas species)
    ADM1 = "ADM1"        # Standard ADM1 (upstream QSDsan)
    ASM2D = "ASM2d"      # Activated Sludge Model 2d
    MASM2D = "mASM2d"    # Modified ASM2d
    ASM1 = "ASM1"        # Activated Sludge Model 1


@dataclass
class PlantState:
    """
    Standard state vector for plant-wide simulation.

    This is the primary data structure passed between simulation tools.

    **Concentration Units (MODEL-SPECIFIC):**
    - ASM2d/ASM1/mASM2d: mg/L (standard practitioner units)
    - mADM1/ADM1: kg/m³ (QSDsan convention for anaerobic models)

    Flow is in m³/day. Temperature is in Kelvin.

    Attributes:
        model_type: Biological process model (mADM1, ASM2d, etc.)
        flow_m3_d: Volumetric flow rate in m³/day
        temperature_K: Temperature in Kelvin
        concentrations: Component ID -> concentration (units depend on model_type)
        reactor_config: Reactor parameters (V_liq, HRT, SRT, recycles)
        metadata: Optional provenance and tracking info

    Example (ASM2d - mg/L):
        >>> state = PlantState(
        ...     model_type=ModelType.ASM2D,
        ...     flow_m3_d=4000.0,
        ...     temperature_K=293.15,
        ...     concentrations={"S_F": 75, "S_A": 20, "S_NH4": 17}  # mg/L
        ... )

    Example (mADM1 - kg/m³):
        >>> state = PlantState(
        ...     model_type=ModelType.MADM1,
        ...     flow_m3_d=200.0,
        ...     temperature_K=308.15,
        ...     concentrations={"S_su": 0.5, "S_aa": 0.8, "X_ch": 3.0}  # kg/m³
        ... )
    """
    model_type: ModelType
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
    reactor_config: Dict[str, Any] = field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        """Validate state after initialization."""
        if self.flow_m3_d <= 0:
            raise ValueError(f"flow_m3_d must be positive, got {self.flow_m3_d}")
        if self.temperature_K < 273.15 or self.temperature_K > 373.15:
            raise ValueError(f"temperature_K must be 273-373 K, got {self.temperature_K}")

        # Convert string model_type to enum if needed
        if isinstance(self.model_type, str):
            self.model_type = ModelType(self.model_type)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        d["model_type"] = self.model_type.value
        return d

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlantState":
        """Deserialize from dict."""
        data = data.copy()
        if "model_type" in data:
            data["model_type"] = ModelType(data["model_type"])
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "PlantState":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, path: str) -> "PlantState":
        """Load from JSON file."""
        with open(path, 'r') as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str) -> None:
        """Save to JSON file."""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    def get_concentration_mg_L(self, component_id: str) -> float:
        """Get concentration in mg/L (convenience method)."""
        return self.concentrations.get(component_id, 0.0) * 1000.0

    @property
    def temperature_C(self) -> float:
        """Temperature in Celsius."""
        return self.temperature_K - 273.15

    def get_concentration_units(self) -> str:
        """
        Return expected concentration units based on model type.

        Returns:
            "mg/L" for aerobic models (ASM2d, ASM1, mASM2d)
            "kg/m3" for anaerobic models (mADM1, ADM1)
        """
        model_value = getattr(self.model_type, 'value', self.model_type)
        if model_value in ("ASM2d", "ASM1", "mASM2d"):
            return "mg/L"
        elif model_value in ("mADM1", "ADM1"):
            return "kg/m3"
        return "mg/L"  # Default to practitioner units

    def validate_concentration_bounds(self) -> list:
        """
        Check concentrations for likely unit confusion.

        Returns warnings if values suggest 1000x unit error.

        Typical ranges:
        - ASM2d/ASM1 (mg/L): COD 200-1000, BOD5 100-300, NH4-N 15-40, TP 4-15
        - mADM1 (kg/m³): Digester feeds 1-20 kg/m³ for most components

        Returns:
            List of warning strings (empty if no issues detected)
        """
        warnings = []
        model_value = getattr(self.model_type, 'value', self.model_type)
        units = self.get_concentration_units()

        if model_value in ("ASM2d", "ASM1", "mASM2d") and units == "mg/L":
            for comp, val in self.concentrations.items():
                if 0 < val < 0.1:
                    warnings.append(
                        f"{comp}={val} mg/L is suspiciously low "
                        f"(did you mean kg/m³ for anaerobic model?)"
                    )
                if val > 50000:
                    warnings.append(
                        f"{comp}={val} mg/L is suspiciously high"
                    )

        elif model_value in ("mADM1", "ADM1") and units == "kg/m3":
            for comp, val in self.concentrations.items():
                if val > 100:
                    warnings.append(
                        f"{comp}={val} kg/m³ is suspiciously high "
                        f"(did you mean mg/L for aerobic model?)"
                    )

        return warnings


def validate_concentration_bounds(
    concentrations: Dict[str, float],
    model_type: str,
    units: str = "mg/L"
) -> list:
    """
    Standalone function to check concentrations for likely unit confusion.

    Args:
        concentrations: Component ID to concentration mapping
        model_type: Model type string (e.g., "ASM2d", "mADM1")
        units: Expected units ("mg/L" or "kg/m3")

    Returns:
        List of warning strings (empty if no issues detected)
    """
    warnings = []

    if model_type in ("ASM2d", "ASM1", "mASM2d") and units == "mg/L":
        for comp, val in concentrations.items():
            if 0 < val < 0.1:
                warnings.append(
                    f"{comp}={val} mg/L is suspiciously low "
                    f"(did you mean kg/m³?)"
                )
            if val > 50000:
                warnings.append(f"{comp}={val} mg/L is suspiciously high")

    elif model_type in ("mADM1", "ADM1") and units == "kg/m3":
        for comp, val in concentrations.items():
            if val > 100:
                warnings.append(
                    f"{comp}={val} kg/m³ is suspiciously high "
                    f"(did you mean mg/L?)"
                )

    return warnings


@dataclass
class SimulationResult:
    """
    Results from a QSDsan dynamic simulation.

    Attributes:
        job_id: Background job identifier
        status: Completion status
        effluent: Final effluent state
        biogas: Biogas composition and flow (anaerobic only)
        time_series: Optional time series data
        performance: Process metrics (yields, removal rates)
        inhibition: Inhibition analysis (anaerobic only)
        precipitation: Mineral precipitation (if modeled)
        convergence: Solver convergence info
    """
    job_id: str
    status: Literal["completed", "failed", "running"]
    duration_days: float
    timestep_hours: float

    # State outputs
    effluent: Optional[PlantState] = None
    biogas: Optional[Dict[str, float]] = None

    # Performance metrics
    performance: Dict[str, Any] = field(default_factory=dict)
    inhibition: Optional[Dict[str, Any]] = None
    precipitation: Optional[Dict[str, Any]] = None

    # Convergence info
    convergence: Dict[str, Any] = field(default_factory=dict)

    # Time series (excluded from MCP response by default)
    time_series_available: bool = False

    # Error info
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = {
            "job_id": self.job_id,
            "status": self.status,
            "duration_days": self.duration_days,
            "timestep_hours": self.timestep_hours,
            "performance": self.performance,
            "convergence": self.convergence,
            "time_series_available": self.time_series_available,
        }

        if self.effluent:
            d["effluent"] = self.effluent.to_dict()
        if self.biogas:
            d["biogas"] = self.biogas
        if self.inhibition:
            d["inhibition"] = self.inhibition
        if self.precipitation:
            d["precipitation"] = self.precipitation
        if self.error:
            d["error"] = self.error

        return d


@dataclass
class ValidationResult:
    """
    Results from state validation.

    Validates PlantState against model requirements:
    - Required components present
    - Charge balance (electroneutrality)
    - Mass balance (COD, TSS, TKN, TP)
    - Concentration bounds
    """
    is_valid: bool
    model_type: ModelType

    # Validation details
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    # Balance checks
    charge_balance: Optional[Dict[str, float]] = None
    mass_balance: Optional[Dict[str, float]] = None

    # Component coverage
    missing_components: list = field(default_factory=list)
    extra_components: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "is_valid": self.is_valid,
            "model_type": self.model_type.value,
            "errors": self.errors,
            "warnings": self.warnings,
            "charge_balance": self.charge_balance,
            "mass_balance": self.mass_balance,
            "missing_components": self.missing_components,
            "extra_components": self.extra_components,
        }


# Convenience type aliases
ModelTypeLiteral = Literal["mADM1", "ADM1", "ASM2d", "mASM2d", "ASM1"]
