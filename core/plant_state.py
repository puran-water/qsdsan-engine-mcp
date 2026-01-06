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
    All concentrations are in kg/m³ (COD basis for organic components).

    Attributes:
        model_type: Biological process model (mADM1, ASM2d, etc.)
        flow_m3_d: Volumetric flow rate in m³/day
        temperature_K: Temperature in Kelvin
        concentrations: Component ID → concentration (kg/m³)
        reactor_config: Reactor parameters (V_liq, HRT, SRT, recycles)
        metadata: Optional provenance and tracking info

    Example:
        >>> state = PlantState(
        ...     model_type=ModelType.MADM1,
        ...     flow_m3_d=1000.0,
        ...     temperature_K=308.15,
        ...     concentrations={
        ...         "S_su": 0.010,   # 10 g/m³ sugars
        ...         "S_aa": 0.005,   # 5 g/m³ amino acids
        ...         "X_c": 0.100,    # 100 g/m³ composite
        ...         # ... 60 more components
        ...     }
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
