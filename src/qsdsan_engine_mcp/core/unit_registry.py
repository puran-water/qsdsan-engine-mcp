"""
Unit Registry - Comprehensive SanUnit type definitions and validation.

This module provides the UNIT_REGISTRY with 35+ unit types from QSDsan,
categorized by function and validated against model compatibility.

Usage:
    from core.unit_registry import (
        UNIT_REGISTRY,
        get_unit_spec,
        validate_unit_params,
        list_available_units,
    )

    # Get specification for a unit type
    spec = get_unit_spec("CSTR")

    # Validate parameters
    errors, warnings = validate_unit_params("CSTR", {"V_max": 1000})

    # List units compatible with ASM2d
    units = list_available_units(model_type="ASM2d")
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from qsdsan_engine_mcp.core.model_registry import MODEL_ALIASES, normalize_model_name

from puran_mcp_support.engineering.unit_op_type import UNIT_OP_TYPE_VALUES, UnitOpType, normalize_unit_op_type, unit_op_type_values


class UnitCategory(str, Enum):
    """Categories of SanUnit types."""
    REACTOR = "reactor"
    SEPARATOR = "separator"
    CLARIFIER = "clarifier"
    SLUDGE = "sludge"
    PUMP = "pump"
    JUNCTION = "junction"
    UTILITY = "utility"
    PRETREATMENT = "pretreatment"


QSDSAN_CATEGORY_TO_UNIT_OP_TYPE: Dict[str, UnitOpType] = {
    UnitCategory.REACTOR.value: UnitOpType.REACTOR,
    UnitCategory.SEPARATOR.value: UnitOpType.SEPARATOR,
    UnitCategory.CLARIFIER.value: UnitOpType.CLARIFIER,
    UnitCategory.SLUDGE.value: UnitOpType.SLUDGE,
    UnitCategory.PUMP.value: UnitOpType.PUMP,
    UnitCategory.JUNCTION.value: UnitOpType.JUNCTION,
    UnitCategory.UTILITY.value: UnitOpType.UTILITY,
    UnitCategory.PRETREATMENT.value: UnitOpType.PRETREATMENT,
}


def qsdsan_category_to_unit_op_type(category: Any) -> UnitOpType:
    """Map a QSDsan registry category onto the shared canonical enum."""

    key = str(getattr(category, "value", category)).strip().lower()
    try:
        return QSDSAN_CATEGORY_TO_UNIT_OP_TYPE[key]
    except KeyError as exc:
        valid = ", ".join(sorted(QSDSAN_CATEGORY_TO_UNIT_OP_TYPE))
        raise ValueError(f"Unknown QSDsan unit category {category!r}. Valid: {valid}") from exc


_ENGINEERING_INPUT_CATEGORIES = (
    "feed_state",
    "operating_condition",
    "design_size",
    "material_or_equipment_property",
    "performance_assumption",
    "chemistry",
)


@dataclass
class EngineeringInputSpec:
    """Public process-engineering input description for one unit/profile field."""

    name: str
    type_name: str
    category: str
    target: str
    required: bool = False
    default: Any = None
    description: Optional[str] = None

    def serialize(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "type": self.type_name,
            "required": self.required,
            "target": self.target,
        }
        if self.default is not None:
            payload["default"] = self.default
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass
class CostingSpec:
    """Strict public costing support metadata."""

    costing_supported: bool = False
    costing_package: Optional[str] = None
    costing_basis: str = "not_available"
    cost_certified: bool = False
    required_economic_inputs: List[str] = field(default_factory=list)
    support_note: Optional[str] = None

    def serialize(self) -> Dict[str, Any]:
        payload = {
            "costing_supported": self.costing_supported,
            "costing_package": self.costing_package,
            "costing_basis": self.costing_basis,
            "cost_certified": self.cost_certified,
            "required_economic_inputs": list(self.required_economic_inputs),
        }
        if self.support_note:
            payload["support_note"] = self.support_note
        return payload


@dataclass
class UnitSpec:
    """
    Specification for a SanUnit type.

    Attributes:
        unit_type: Unit type identifier (e.g., "CSTR", "Splitter")
        category: Functional category
        description: Human-readable description
        compatible_models: List of compatible process models (empty = model-agnostic)
        required_params: Dict of required parameter names to types
        optional_params: Dict of optional parameter names to default values
        qsdsan_class: Full path to QSDsan class
        is_dynamic: Whether unit supports dynamic simulation
        n_ins: Default number of input ports (-1 = variable)
        n_outs: Default number of output ports (-1 = variable)
    """
    unit_type: str
    category: UnitCategory
    description: str
    compatible_models: List[str] = field(default_factory=list)
    required_params: Dict[str, type] = field(default_factory=dict)
    optional_params: Dict[str, Any] = field(default_factory=dict)
    qsdsan_class: str = ""
    is_dynamic: bool = True
    n_ins: int = 1
    n_outs: int = 1
    solve_supported: bool = True
    publicly_supported: bool = True
    support_note: Optional[str] = None
    zone_family: str = "model_native"
    component_profile: str = "stream_native"
    output_roles: List[str] = field(default_factory=list)
    aux_schema: Dict[str, Any] = field(default_factory=dict)
    bootstrap_policy: str = "native"
    engine_fit: str = "dynamic_primary"
    recommended_use_cases: List[str] = field(default_factory=list)
    handoff_ready_profiles: List[str] = field(default_factory=list)
    engineering_profiles: List[Dict[str, Any]] = field(default_factory=list)
    engineering_inputs: Dict[str, List[EngineeringInputSpec]] = field(default_factory=dict)
    applied_profile_defaults: Dict[str, Any] = field(default_factory=dict)
    costing: CostingSpec = field(default_factory=CostingSpec)

    @property
    def canonical_unit_op_type(self) -> UnitOpType:
        return qsdsan_category_to_unit_op_type(self.category)


def _type_name(value: Any) -> str:
    if isinstance(value, type):
        return value.__name__
    return str(value)


def serialize_unit_spec(spec: UnitSpec) -> Dict[str, Any]:
    """Convert a UnitSpec into a stable adapter payload."""

    return {
        "unit_type": spec.unit_type,
        "category": spec.category.value,
        "canonical_unit_op_type": spec.canonical_unit_op_type.value,
        "description": spec.description,
        "compatible_models": spec.compatible_models or ["all"],
        "is_dynamic": spec.is_dynamic,
        "n_ins": spec.n_ins,
        "n_outs": spec.n_outs,
        "solve_supported": spec.solve_supported,
        "publicly_supported": spec.publicly_supported,
        "support_note": spec.support_note,
        "zone_family": spec.zone_family,
        "component_profile": spec.component_profile,
        "output_roles": spec.output_roles,
        "aux_schema": spec.aux_schema,
        "bootstrap_policy": spec.bootstrap_policy,
        "engine_fit": spec.engine_fit,
        "recommended_use_cases": spec.recommended_use_cases,
        "handoff_ready_profiles": spec.handoff_ready_profiles,
        "engineering_profiles": spec.engineering_profiles,
        "engineering_inputs": {
            category: [field.serialize() for field in spec.engineering_inputs.get(category, [])]
            for category in _ENGINEERING_INPUT_CATEGORIES
            if spec.engineering_inputs.get(category)
        },
        "applied_profile_defaults": spec.applied_profile_defaults,
        "qsdsan_class": spec.qsdsan_class,
        **spec.costing.serialize(),
    }


def _infer_engineering_category(name: str, target: str) -> str:
    lower = name.lower()
    if target == "source_spec":
        return "feed_state"
    if "ph" in lower or lower.startswith("do_"):
        return "chemistry"
    if any(token in lower for token in ("temperature", "combust", "air", "recovery", "removal", "capture", "efficiency", "extract", "moisture")):
        return "performance_assumption"
    if any(token in lower for token in ("v_", "volume", "tau", "surface", "height", "area", "diameter", "length", "compartment")):
        return "design_size"
    if any(token in lower for token in ("material", "vessel", "membrane", "kwh", "kw_", "pump", "blower")):
        return "material_or_equipment_property"
    return "operating_condition"


def _append_engineering_input(
    grouped: Dict[str, List[EngineeringInputSpec]],
    defaults: Dict[str, Any],
    *,
    name: str,
    type_name: str,
    target: str,
    required: bool,
    default: Any = None,
    description: Optional[str] = None,
) -> None:
    category = _infer_engineering_category(name, target)
    spec = EngineeringInputSpec(
        name=name,
        type_name=type_name,
        category=category,
        target=target,
        required=required,
        default=default,
        description=description,
    )
    grouped.setdefault(category, []).append(spec)
    if default is not None:
        defaults[name] = default


def _derive_engineering_contract(spec: UnitSpec) -> Tuple[Dict[str, List[EngineeringInputSpec]], Dict[str, Any]]:
    grouped: Dict[str, List[EngineeringInputSpec]] = {}
    defaults: Dict[str, Any] = {}

    for param_name, param_type in spec.required_params.items():
        _append_engineering_input(
            grouped,
            defaults,
            name=param_name,
            type_name=_type_name(param_type),
            target=f"params.{param_name}",
            required=True,
        )

    for param_name, default in spec.optional_params.items():
        type_name = _type_name(type(default)) if default is not None else "Any"
        _append_engineering_input(
            grouped,
            defaults,
            name=param_name,
            type_name=type_name,
            target=f"params.{param_name}",
            required=False,
            default=default,
        )

    required_aux = spec.aux_schema.get("required", [])
    optional_aux = spec.aux_schema.get("optional", [])
    for key in required_aux:
        _append_engineering_input(
            grouped,
            defaults,
            name=key,
            type_name="Any",
            target=f"aux_config.{key}",
            required=True,
        )
    for key in optional_aux:
        _append_engineering_input(
            grouped,
            defaults,
            name=key,
            type_name="Any",
            target=f"aux_config.{key}",
            required=False,
        )

    if spec.bootstrap_policy == "requires_source_spec":
        _append_engineering_input(
            grouped,
            defaults,
            name="source_spec",
            type_name="object",
            target="source_spec",
            required=True,
        )

    return grouped, defaults


def _compile_public_engineering_inputs(
    spec: UnitSpec,
    engineering_inputs: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any], List[str], List[str]]:
    """Compile public engineering inputs into internal params/aux/source fields."""
    provided = dict(engineering_inputs or {})
    params: Dict[str, Any] = {}
    aux_config: Dict[str, Any] = {}
    source_spec: Optional[Dict[str, Any]] = None
    applied_defaults: Dict[str, Any] = {}
    errors: List[str] = []
    warnings: List[str] = []

    field_map = {
        field.name: field
        for category in spec.engineering_inputs.values()
        for field in category
    }

    unknown = sorted(set(provided) - set(field_map))
    if unknown:
        warnings.append(f"Unknown engineering inputs ignored: {unknown}")

    for name, field_spec in field_map.items():
        if name in provided:
            value = provided[name]
        elif field_spec.default is not None:
            value = field_spec.default
            applied_defaults[name] = field_spec.default
        else:
            value = None

        if value is None:
            if field_spec.required:
                errors.append(f"Missing required engineering input: {name}")
            continue

        if field_spec.target == "source_spec":
            if not isinstance(value, dict):
                errors.append("engineering input 'source_spec' must be an object")
                continue
            source_spec = value
            continue

        target_group, _, target_name = field_spec.target.partition(".")
        if target_group == "params":
            params[target_name] = value
        elif target_group == "aux_config":
            aux_config[target_name] = value
        else:
            warnings.append(f"Unhandled engineering input target '{field_spec.target}' for '{name}'")

    return params, aux_config, source_spec, applied_defaults, errors, warnings


# =============================================================================
# COMPREHENSIVE UNIT REGISTRY (35+ units from QSDsan sanunits module)
# =============================================================================
UNIT_REGISTRY: Dict[str, UnitSpec] = {
    # ==================== REACTORS ====================
    "CSTR": UnitSpec(
        unit_type="CSTR",
        category=UnitCategory.REACTOR,
        # Note: CSTR has _cost() method but it's empty/pass (no equipment costing).
        # TEA tools use heuristic estimation for CSTR units.
        description="Continuous stirred-tank reactor with optional aeration. Note: lacks equipment costing.",
        compatible_models=["ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"],  # Primary: ASM2d
        required_params={},  # V_max is optional with default 1000
        optional_params={
            "V_max": 1000.0,  # Default from QSDsan
            "aeration": 2.0,  # Default aeration rate from QSDsan
            "DO_ID": "S_O2",
            "suspended_growth_model": None,
            # Operating temperature (K) for models that speciate at CONSTRUCTION
            # (mASM2d2N, mASM2d2N_N2O). Consumed by the builder, never forwarded to
            # QSDsan. Omitting it pins the acid-base basis at 25 C, which at 35 C
            # understates free ammonia ~2x (pKa 9.25 vs 8.94) and NOB inhibition with it.
            "T_op_K": None,
        },
        qsdsan_class="qsdsan.sanunits.CSTR",
        is_dynamic=True,
        n_ins=-1,  # Variable inputs
        n_outs=1,
    ),
    "AnaerobicCSTR": UnitSpec(
        unit_type="AnaerobicCSTR",
        category=UnitCategory.REACTOR,
        description="Anaerobic CSTR with biogas headspace (standard ADM1)",
        compatible_models=["ADM1"],  # For standard ADM1 only
        required_params={"V_liq": float},
        optional_params={
            "V_gas": 300.0,
            "T": 308.15,
            "headspace_P": 1.013,
            "external_P": 1.013,
        },
        qsdsan_class="qsdsan.sanunits.AnaerobicCSTR",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # biogas + liquid effluent
    ),
    "AnaerobicCSTRmADM1": UnitSpec(
        unit_type="AnaerobicCSTRmADM1",
        category=UnitCategory.REACTOR,
        description="Anaerobic CSTR with the full mADM1 component set (4 biogas species)",
        compatible_models=["mADM1"],
        required_params={"V_liq": float},
        optional_params={
            "V_gas": 300.0,
            "T": 308.15,
            "headspace_P": 1.013,
            "external_P": 1.013,
        },
        qsdsan_class='qsdsan_engine_mcp.models.reactors.AnaerobicCSTRmADM1',
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # biogas + liquid effluent
    ),
    # NOTE: SBR removed - commented out in QSDsan, not currently available
    # Use CSTR or CompletelyMixedMBR as alternatives
    "AnaerobicBaffledReactor": UnitSpec(
        unit_type="AnaerobicBaffledReactor",
        category=UnitCategory.REACTOR,
        description="Anaerobic baffled reactor with compartments",
        compatible_models=["ADM1", "mADM1"],  # Both ADM1 variants
        required_params={"V_liq": float},
        optional_params={"n_compartments": 4},
        qsdsan_class="qsdsan.sanunits.AnaerobicBaffledReactor",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation fails on minimal ADM flowsheets in this stack.",
    ),
    "InternalCirculationRx": UnitSpec(
        unit_type="InternalCirculationRx",
        category=UnitCategory.REACTOR,
        description="Two-stage anaerobic reactor with internal circulation",
        compatible_models=["ADM1", "mADM1"],  # Both ADM1 variants
        required_params={"V_liq": float},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.InternalCirculationRx",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream constructor is not currently stable in this stack.",
    ),
    "MixTank": UnitSpec(
        unit_type="MixTank",
        category=UnitCategory.REACTOR,
        description="Mixing tank with retention time",
        compatible_models=[],
        required_params={},  # tau and V_wf are optional with None defaults
        optional_params={
            "tau": None,  # Retention time (hr) - None = auto-calculate
            "V_wf": None,  # Working fraction - None = auto-calculate
            "vessel_type": None,
            "vessel_material": None,
            "kW_per_m3": 0.0,
        },
        qsdsan_class="qsdsan.sanunits.MixTank",
        is_dynamic=True,
        n_ins=-1,
        n_outs=1,
    ),
    # NOTE: UASB removed - no class in QSDsan sanunits
    # Use AnaerobicCSTR or InternalCirculationRx as alternatives
    "PFR": UnitSpec(
        unit_type="PFR",
        category=UnitCategory.REACTOR,
        description="Plug flow reactor",
        compatible_models=[],  # Model-agnostic
        required_params={},
        optional_params={
            "N_tanks_in_series": 5,
        },
        qsdsan_class="qsdsan.sanunits.PFR",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation is not stable on minimal flowsheets in this stack.",
    ),
    "Lagoon": UnitSpec(
        unit_type="Lagoon",
        category=UnitCategory.REACTOR,
        description="Lagoon for wastewater treatment (batch/continuous)",
        compatible_models=[],  # Model-agnostic
        required_params={},
        optional_params={
            "design_type": "anaerobic",  # "anaerobic", "facultative", "aerobic"
        },
        qsdsan_class="qsdsan.sanunits.Lagoon",
        is_dynamic=False,
        n_ins=1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation fails on minimal wastewater states in this stack.",
    ),

    # ==================== MEMBRANE BIOREACTORS ====================
    "CompletelyMixedMBR": UnitSpec(
        unit_type="CompletelyMixedMBR",
        category=UnitCategory.SEPARATOR,
        description="MBR with ideal membrane separation (inherits from CSTR)",
        compatible_models=["ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"],  # Primary: ASM2d
        required_params={},  # Inherits CSTR defaults
        optional_params={
            "V_max": 1000.0,  # Default from CSTR
            "solids_capture_rate": 0.999,
            "pumped_flow": 50.0,  # Retentate/pumped flow (m3/d)
            "aeration": 2.0,  # Default from CSTR
            "DO_ID": "S_O2",
            "gas_stripping": False,
            "crossflow_air": None,  # For crossflow aeration
            # See the CSTR entry: operating temperature (K) for construction-time
            # speciated models. Builder-only; never forwarded to QSDsan.
            "T_op_K": None,
        },
        qsdsan_class="qsdsan.sanunits.CompletelyMixedMBR",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # permeate + retentate
    ),
    "AnMBR": UnitSpec(
        unit_type="AnMBR",
        category=UnitCategory.SEPARATOR,
        description="Anaerobic membrane bioreactor (economic model)",
        compatible_models=["ADM1", "mADM1"],  # Both ADM1 variants
        required_params={},  # No required params - uses defaults
        optional_params={
            "reactor_type": "CSTR",
            "N_train": 2,
            "membrane_configuration": "cross-flow",
            "membrane_type": "multi-tube",
            "membrane_material": "ceramic",
            "Y_biogas": 0.86,
            "Y_biomass": 0.05,
            "biodegradability": 1.0,
            "solids_conc": 10.5,
            "T": 308.15,
        },
        qsdsan_class="qsdsan.sanunits.AnMBR",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream constructor is not currently stable in this stack.",
    ),

    # ==================== CLARIFIERS ====================
    "FlatBottomCircularClarifier": UnitSpec(
        unit_type="FlatBottomCircularClarifier",
        category=UnitCategory.CLARIFIER,
        description="Flat-bottom circular clarifier with layered settling",
        compatible_models=[],
        required_params={},  # All have defaults in QSDsan
        optional_params={
            "surface_area": 1500.0,  # Default from QSDsan
            "height": 4.0,  # Default from QSDsan
            "N_layer": 10,
            "feed_layer": 4,
            "underflow": 2000.0,
            "wastage": 385.0,
            "X_threshold": 3000.0,
            "v_max": 474.0,
            "v_max_practical": 250.0,
        },
        qsdsan_class="qsdsan.sanunits.FlatBottomCircularClarifier",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # effluent + underflow
    ),
    "PrimaryClarifier": UnitSpec(
        unit_type="PrimaryClarifier",
        category=UnitCategory.CLARIFIER,
        description="Primary clarifier optimized for primary treatment",
        compatible_models=[],
        required_params={"surface_area": float},
        optional_params={"height": 4.0},
        qsdsan_class="qsdsan.sanunits.PrimaryClarifier",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
    ),
    "IdealClarifier": UnitSpec(
        unit_type="IdealClarifier",
        category=UnitCategory.CLARIFIER,
        description="Simplified clarifier with specified removal efficiency",
        compatible_models=[],
        required_params={},
        optional_params={
            "sludge_flow_rate": 2000,
            "solids_removal_efficiency": 0.995,
        },
        qsdsan_class="qsdsan.sanunits.IdealClarifier",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
    ),
    "Sedimentation": UnitSpec(
        unit_type="Sedimentation",
        category=UnitCategory.CLARIFIER,
        description="General sedimentation unit",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Sedimentation",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation fails on minimal wastewater states in this stack.",
    ),
    # NOTE: SecondarySettler removed - not in QSDsan sanunits
    # Use FlatBottomCircularClarifier or Sedimentation instead

    # ==================== SLUDGE TREATMENT ====================
    "Thickener": UnitSpec(
        unit_type="Thickener",
        category=UnitCategory.SLUDGE,
        description="Sludge thickening unit (BSM2 layout)",
        compatible_models=[],
        required_params={},
        optional_params={
            "thickener_perc": 7.0,
            "TSS_removal_perc": 98.0,
        },
        qsdsan_class="qsdsan.sanunits.Thickener",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,  # thickened + overflow
    ),
    "Centrifuge": UnitSpec(
        unit_type="Centrifuge",
        category=UnitCategory.SLUDGE,
        description="Mechanical sludge dewatering",
        compatible_models=[],
        required_params={},
        optional_params={
            "thickener_perc": 20.0,
            "TSS_removal_perc": 98.0,
        },
        qsdsan_class="qsdsan.sanunits.Centrifuge",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
    ),
    "BeltThickener": UnitSpec(
        unit_type="BeltThickener",
        category=UnitCategory.SLUDGE,
        description="Belt thickener for sludge",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.BeltThickener",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
    ),
    "SludgeDigester": UnitSpec(
        unit_type="SludgeDigester",
        category=UnitCategory.SLUDGE,
        description="Sludge digestion unit",
        compatible_models=["ADM1", "mADM1"],  # Both ADM1 variants
        required_params={"V_liq": float},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.SludgeDigester",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation expects sludge-specific state fields that are not robust on generic ADM influent in this stack.",
    ),
    "AerobicDigester": UnitSpec(
        unit_type="AerobicDigester",
        category=UnitCategory.REACTOR,
        description="Aerobic sludge stabilization digester",
        compatible_models=["ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"],
        required_params={},
        optional_params={},
        qsdsan_class='qsdsan_engine_mcp.core.public_units.AerobicDigester',
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "Incinerator": UnitSpec(
        unit_type="Incinerator",
        category=UnitCategory.SLUDGE,
        description="Thermal sludge treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Incinerator",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation requires gas-phase auxiliary streams that are not safely auto-constructed in this stack.",
    ),

    # ==================== PRETREATMENT ====================
    "Screening": UnitSpec(
        unit_type="Screening",
        category=UnitCategory.PRETREATMENT,
        description="Screening for preliminary treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Screening",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
    ),
    "SepticTank": UnitSpec(
        unit_type="SepticTank",
        category=UnitCategory.PRETREATMENT,
        description="Septic tank for primary treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.SepticTank",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation fails on minimal wastewater states in this stack.",
    ),
    # NOTE: GritChamber removed - not in QSDsan sanunits
    # Use Screening or custom implementation if needed

    # ==================== PUMPING & HYDRAULICS ====================
    "Pump": UnitSpec(
        unit_type="Pump",
        category=UnitCategory.PUMP,
        description="Generic pump for fluid transport",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Pump",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "WWTpump": UnitSpec(
        unit_type="WWTpump",
        category=UnitCategory.PUMP,
        description="Wastewater treatment pump with design correlations",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.WWTpump",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "SludgePump": UnitSpec(
        unit_type="SludgePump",
        category=UnitCategory.PUMP,
        description="Pump optimized for high solids content",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.SludgePump",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "HydraulicDelay": UnitSpec(
        unit_type="HydraulicDelay",
        category=UnitCategory.PUMP,
        description="First-order hydraulic residence time delay",
        compatible_models=[],
        required_params={"tau": float},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.HydraulicDelay",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),

    # ==================== JUNCTIONS / UTILITY UNITS ====================
    "Junction": UnitSpec(
        unit_type="Junction",
        category=UnitCategory.UTILITY,
        description="Generic junction for mixing/splitting streams",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Junction",
        is_dynamic=True,
        n_ins=-1,  # Variable inputs
        n_outs=-1,  # Variable outputs
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: generic junction is not validated for robust dynamic MCP flowsheet construction.",
    ),
    "DynamicInfluent": UnitSpec(
        unit_type="DynamicInfluent",
        category=UnitCategory.UTILITY,
        description="Generate time-varying influent stream (synthetic or real data)",
        compatible_models=[],
        required_params={},
        optional_params={
            "data_file": None,  # Path to CSV/Excel with time series
            "interpolate": True,
        },
        qsdsan_class="qsdsan.sanunits.DynamicInfluent",
        is_dynamic=True,
        n_ins=0,  # Source unit - no inputs
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: requires external data/model-specific setup that is not yet validated for dynamic MCP use.",
    ),
    "Splitter": UnitSpec(
        unit_type="Splitter",
        category=UnitCategory.UTILITY,
        description="Flow splitter with configurable split ratio",
        compatible_models=[],
        # split can be float, list, or dict per BioSTEAM/QSDsan
        # float: single split ratio (0.8 = 80% to out[0], 20% to out[1])
        # list: [0.7, 0.3] for proportions to each outlet
        # dict: {"out1": 0.7, "out2": 0.3} for named outputs
        required_params={},  # Not required, defaults to equal split
        optional_params={"split": 0.5},  # Default 50/50 split
        qsdsan_class="qsdsan.sanunits.Splitter",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,
    ),
    "Mixer": UnitSpec(
        unit_type="Mixer",
        category=UnitCategory.UTILITY,
        # Note: Mixer uses BioSTEAM's variable fan-in pattern via ins=(s1, s2, ...) tuple.
        # The flowsheet builder creates Mixer with ins=(tuple_of_streams) not n_ins=-1.
        # Additional inputs (e.g., recycles) are wired to empty input slots dynamically.
        description="Stream mixer combining multiple inputs. Uses ins=(...) tuple pattern for variable fan-in.",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.Mixer",
        is_dynamic=True,
        n_ins=-1,  # Variable inputs (implementation uses ins=(tuple) pattern)
        n_outs=1,
    ),
    "ComponentSplitter": UnitSpec(
        unit_type="ComponentSplitter",
        category=UnitCategory.UTILITY,
        description="Split streams based on specific components",
        compatible_models=[],
        required_params={"split_keys": list},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ComponentSplitter",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
    ),
    "Tank": UnitSpec(
        unit_type="Tank",
        category=UnitCategory.UTILITY,
        description="General storage/equalization tank",
        compatible_models=[],
        required_params={},  # tau and V_wf are optional with None defaults
        optional_params={
            "tau": None,  # Retention time (hr) - None = auto-calculate
            "V_wf": None,  # Working fraction - None = auto-calculate
            "vessel_type": None,
            "vessel_material": None,
            "kW_per_m3": 0.0,
        },
        qsdsan_class="qsdsan.sanunits.Tank",
        is_dynamic=True,
        n_ins=-1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream Tank constructor is not currently stable in this stack.",
    ),
    "StorageTank": UnitSpec(
        unit_type="StorageTank",
        category=UnitCategory.UTILITY,
        description="Storage tank for holding streams",
        compatible_models=[],
        required_params={},  # tau and V_wf are optional with None defaults
        optional_params={
            "tau": None,
            "V_wf": None,
            "vessel_type": None,
            "vessel_material": None,
            "kW_per_m3": 0.0,
            "length_to_diameter": 2,
        },
        qsdsan_class="qsdsan.sanunits.StorageTank",
        is_dynamic=False,
        n_ins=1,
        n_outs=1,
    ),
    "ProcessWaterCenter": UnitSpec(
        unit_type="ProcessWaterCenter",
        category=UnitCategory.UTILITY,
        description="Utility water mixing and redistribution center",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class='qsdsan_engine_mcp.core.public_units.ProcessWaterCenter',
        is_dynamic=True,
        n_ins=2,
        n_outs=1,
    ),
    "BiogasCombustion": UnitSpec(
        unit_type="BiogasCombustion",
        category=UnitCategory.UTILITY,
        description="Combust biogas to treated exhaust gas",
        compatible_models=["ADM1", "mADM1"],
        required_params={},
        optional_params={"combustion_efficiency": 0.95},
        qsdsan_class='qsdsan_engine_mcp.core.public_units.BiogasCombustion',
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "CombinedHeatPower": UnitSpec(
        unit_type="CombinedHeatPower",
        category=UnitCategory.UTILITY,
        description="Combined heat and power conversion for digester biogas",
        compatible_models=["ADM1", "mADM1"],
        required_params={},
        optional_params={"combustion_efficiency": 0.98},
        qsdsan_class='qsdsan_engine_mcp.core.public_units.CombinedHeatPower',
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),

    # ==================== TERTIARY TREATMENT ====================
    "PolishingFilter": UnitSpec(
        unit_type="PolishingFilter",
        category=UnitCategory.SEPARATOR,
        description="Remove residual contaminants from treated effluent",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.PolishingFilter",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream constructor is not currently stable in this stack.",
    ),
    "AmmoniaSteamStripper": UnitSpec(
        unit_type="AmmoniaSteamStripper",
        category=UnitCategory.SEPARATOR,
        description=(
            "Counter-current direct-steam ammonia stripper (equilibrium-stage cascade "
            "with Murphree efficiency, per-stage pH/speciation, CO2 co-stripping, and "
            "an energy balance). ins=(digestate feed, live steam); outs=(stripped "
            "bottoms, overhead vapour). The steam inlet may be left unconnected, in "
            "which case steam_to_feed_kg_m3 synthesises the steam stream."
        ),
        compatible_models=["mASM2d", "mASM2d2N", "mASM2d2N_N2O"],
        required_params={},
        optional_params={
            "n_stages": 30,
            "E_murphree": 1.0,
            # Defaults to E_murphree when None. A separate CO2 efficiency is a
            # modelling CHOICE the caller makes explicitly (HCO3- dehydration is
            # rate-limited; see the class docstring) -- there is no silent cap.
            "E_murphree_co2": None,
            "P_atm": 1.0,
            # None = saturation temperature at column pressure (a direct-steam
            # column boils; it does not get to choose).
            "T_column_K": None,
            "steam_to_feed_kg_m3": 110.0,
            "naoh_dose_mol_m3": 0.0,
            "heat_loss_fraction": 0.02,
        },
        qsdsan_class='qsdsan_engine_mcp.core.ammonia_stripper.AmmoniaSteamStripper',
        is_dynamic=True,
        n_ins=2,
        n_outs=2,  # stripped bottoms + overhead vapour
        support_note=(
            "Bracketing-grade: equilibrium-stage + Murphree, not rate-based; >50 C "
            "acid-base constants are extrapolated and carbamate is not modelled. "
            "Calibrate E_murphree against pilot data before quoting removal above "
            "screening confidence."
        ),
    ),

    # ==================== ADDITIONAL CLARIFIERS (BSM2) ====================
    "PrimaryClarifierBSM2": UnitSpec(
        unit_type="PrimaryClarifierBSM2",
        category=UnitCategory.CLARIFIER,
        description="Primary clarifier with BSM2 settling model",
        compatible_models=["ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"],
        required_params={},
        optional_params={
            "surface_area": 1500.0,
            "height": 4.0,
            "f_corr": 0.65,  # BSM2 correction factor
            "X_t": 3000.0,   # Threshold concentration
        },
        qsdsan_class="qsdsan.sanunits.PrimaryClarifierBSM2",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # overflow, sludge
    ),

    # ==================== ADDITIONAL REACTORS ====================
    "ActivatedSludgeProcess": UnitSpec(
        unit_type="ActivatedSludgeProcess",
        category=UnitCategory.REACTOR,
        description="BSM1-style activated sludge process wrapper",
        compatible_models=["ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"],
        required_params={},
        optional_params={
            "V_max": 1000.0,
            "N_tanks": 5,
            "aeration_profile": None,
        },
        qsdsan_class="qsdsan.sanunits.ActivatedSludgeProcess",
        is_dynamic=True,
        n_ins=-1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation expects additional sludge state variables that are not robust on minimal server-built flowsheets in this stack.",
    ),
    "AnaerobicDigestion": UnitSpec(
        unit_type="AnaerobicDigestion",
        category=UnitCategory.REACTOR,
        description="Simplified anaerobic digestion reactor",
        compatible_models=["ADM1", "mADM1"],
        required_params={"V_liq": float},
        optional_params={
            "V_gas": 300.0,
            "T": 308.15,
        },
        qsdsan_class="qsdsan.sanunits.AnaerobicDigestion",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # effluent, biogas
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation fails on minimal ADM wastewater states in this stack.",
    ),

    # ==================== ADDITIONAL SLUDGE TREATMENT ====================
    "SludgePasteurization": UnitSpec(
        unit_type="SludgePasteurization",
        category=UnitCategory.SLUDGE,
        description="Thermal sludge pasteurization for pathogen reduction",
        compatible_models=[],
        required_params={},
        optional_params={
            "T_pasteurization": 343.15,  # 70°C
            "retention_time": 0.5,  # hours
        },
        qsdsan_class="qsdsan.sanunits.SludgePasteurization",
        is_dynamic=False,
        n_ins=1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation requires auxiliary gas species not present in minimal flowsheets in this stack.",
    ),
    "DryingBed": UnitSpec(
        unit_type="DryingBed",
        category=UnitCategory.SLUDGE,
        description="Sludge drying bed for dewatering",
        compatible_models=[],
        required_params={},
        optional_params={
            "design_loading": 100.0,  # kg/m²
            "drying_time": 20.0,  # days
        },
        qsdsan_class="qsdsan.sanunits.DryingBed",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,  # dried sludge, leachate
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation is not stable with default design state in this stack.",
    ),
    "LiquidTreatmentBed": UnitSpec(
        unit_type="LiquidTreatmentBed",
        category=UnitCategory.SLUDGE,
        description="Treatment bed for liquid waste stabilization",
        compatible_models=[],
        required_params={},
        optional_params={
            "depth": 0.5,  # m
            "HRT": 5.0,  # days
        },
        qsdsan_class="qsdsan.sanunits.LiquidTreatmentBed",
        is_dynamic=False,
        n_ins=1,
        n_outs=1,
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation is not stable with default design state in this stack.",
    ),

    # ==================== MEMBRANE PROCESSES ====================
    "MembraneDistillation": UnitSpec(
        unit_type="MembraneDistillation",
        category=UnitCategory.SEPARATOR,
        description="Membrane distillation for water recovery",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.MembraneDistillation",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,  # permeate, concentrate
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: upstream simulation requires additional membrane-side state initialization not provided by minimal server-built flowsheets.",
    ),
    "MembraneGasExtraction": UnitSpec(
        unit_type="MembraneGasExtraction",
        category=UnitCategory.SEPARATOR,
        description="Membrane contactor for dissolved gas extraction (e.g., CH4, CO2)",
        compatible_models=["mADM1", "ADM1"],
        required_params={},
        optional_params={
            "SurfArea": 0.1199,  # Surface area in m² (QSDsan default)
            "GasID": ["H2", "O2", "N2", "CO2", "CH4", "H2O"],  # Target gas IDs (list)
        },
        qsdsan_class="qsdsan.sanunits.MembraneGasExtraction",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # liquid, gas
        publicly_supported=False,
        support_note="Hidden from MCP/CLI flowsheet discovery: default gas set is incompatible with current ADM thermodynamics in this stack.",
    ),

    # ==================== ADDITIONAL JUNCTIONS (ADM1p/mASM2d) ====================
    "ADM1ptomASM2d": UnitSpec(
        unit_type="ADM1ptomASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert mADM1/ADM1p state to mASM2d via the upstream A1 junction",
        compatible_models=["mADM1", "mASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ADM1ptomASM2d",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "mASM2dtoADM1p": UnitSpec(
        unit_type="mASM2dtoADM1p",
        category=UnitCategory.JUNCTION,
        description="Convert mASM2d state to mADM1/ADM1p via the upstream A1 junction",
        compatible_models=["mASM2d", "mADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.mASM2dtoADM1p",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
        publicly_supported=True,
    ),
    # NOTE: Disinfection removed - not in QSDsan sanunits
    # May be implemented in future QSDsan versions
}


UNIT_METADATA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    # Core public surface metadata
    "CSTR": {
        "zone_family": "waterline_asm",
        "output_roles": ["effluent"],
    },
    "AnaerobicCSTR": {
        "zone_family": "waterline_adm",
        "output_roles": ["biogas", "effluent"],
    },
    "AnaerobicCSTRmADM1": {
        "zone_family": "waterline_adm",
        "output_roles": ["biogas", "effluent"],
    },
    "CompletelyMixedMBR": {
        "zone_family": "waterline_asm",
        "output_roles": ["effluent", "retentate"],
    },
    "Splitter": {
        "zone_family": "utility",
        "output_roles": ["primary", "secondary"],
    },
    "Mixer": {
        "zone_family": "utility",
        "output_roles": ["mixed_outlet"],
    },
    "ADM1ptomASM2d": {
        "zone_family": "adapter",
        "component_profile": "canonical_a1",
        "output_roles": ["converted_stream"],
    },
    "mASM2dtoADM1p": {
        "zone_family": "adapter",
        "component_profile": "canonical_a1",
        "output_roles": ["converted_stream"],
        "publicly_supported": True,
        "support_note": None,
    },
    # Productized wrapper surface for previously hidden units
    "AnaerobicBaffledReactor": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.AnaerobicBaffledReactor',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "biogas"],
    },
    "InternalCirculationRx": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.InternalCirculationRx',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "waterline_adm",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "biogas"],
    },
    "PFR": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.PFR',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "waterline_asm",
        "component_profile": "stream_native",
        "output_roles": ["effluent"],
    },
    "Lagoon": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.Lagoon',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent"],
    },
    "AnMBR": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.AnMBR',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "waterline_adm",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "retentate"],
    },
    "Sedimentation": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.Sedimentation',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "sludge"],
    },
    "SludgeDigester": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.SludgeDigester',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["digestate", "biogas"],
    },
    "Incinerator": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.Incinerator',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "thermal_gas",
        "component_profile": "stream_native",
        "output_roles": ["exhaust_gas", "ash"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["air_flow_factor", "fuel_factor", "volatile_fraction"],
            "auto_generated": ["air", "fuel"],
        },
    },
    "SepticTank": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.SepticTank',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "sludge"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["solids_capture", "water_to_sludge"],
            "auto_generated": ["mgoh2"],
        },
    },
    "Junction": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.Junction',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "utility",
        "component_profile": "stream_native",
        "output_roles": ["outlet"],
        "n_ins": 1,
        "n_outs": 1,
    },
    "DynamicInfluent": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.DynamicInfluent',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "utility",
        "component_profile": "stream_native",
        "output_roles": ["influent"],
        "bootstrap_policy": "requires_source_spec",
    },
    "Tank": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.Tank',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "utility",
        "component_profile": "stream_native",
        "output_roles": ["mixed_outlet"],
    },
    "ProcessWaterCenter": {
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "utility",
        "component_profile": "stream_native",
        "output_roles": ["process_water"],
    },
    "PolishingFilter": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.PolishingFilter',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "sludge"],
    },
    "ActivatedSludgeProcess": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.ActivatedSludgeProcess',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "waterline_asm",
        "component_profile": "stream_native",
        "output_roles": ["effluent"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["air_factor", "soluble_removal", "particulate_removal"],
            "auto_generated": ["air"],
        },
    },
    "AerobicDigester": {
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["stabilized_sludge"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["air_factor"],
            "auto_generated": ["air"],
        },
    },
    "AnaerobicDigestion": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.AnaerobicDigestion',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "waterline_adm",
        "component_profile": "stream_native",
        "output_roles": ["effluent", "biogas"],
    },
    "BiogasCombustion": {
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "thermal_gas",
        "component_profile": "stream_native",
        "output_roles": ["exhaust_gas"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["combustion_efficiency", "air_factor"],
            "auto_generated": ["air"],
        },
    },
    "CombinedHeatPower": {
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "thermal_gas",
        "component_profile": "stream_native",
        "output_roles": ["exhaust_gas"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["combustion_efficiency", "power_efficiency", "heat_efficiency"],
            "auto_generated": ["air"],
        },
    },
    "SludgePasteurization": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.SludgePasteurization',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "thermal_gas",
        "component_profile": "stream_native",
        "output_roles": ["pasteurized_sludge"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["target_moisture", "heat_loss"],
            "auto_generated": ["air", "lpg"],
        },
    },
    "DryingBed": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.DryingBed',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["dried_sludge", "leachate"],
    },
    "LiquidTreatmentBed": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.LiquidTreatmentBed',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "sanitation_sludge",
        "component_profile": "stream_native",
        "output_roles": ["effluent"],
    },
    "MembraneDistillation": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.MembraneDistillation',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "membrane_recovery",
        "component_profile": "stream_native",
        "output_roles": ["permeate", "concentrate"],
        "bootstrap_policy": "requires_aux_config",
        "aux_schema": {
            "required": ["target_pH", "water_recovery"],
            "optional": ["ammonia_recovery", "acid_strength", "base_strength"],
        },
    },
    "MembraneGasExtraction": {
        "qsdsan_class": 'qsdsan_engine_mcp.core.public_units.MembraneGasExtraction',
        "publicly_supported": True,
        "support_note": None,
        "zone_family": "membrane_recovery",
        "component_profile": "stream_native",
        "output_roles": ["liquid", "gas"],
        "bootstrap_policy": "auto_aux",
        "aux_schema": {
            "optional": ["gas_ids", "extraction_fraction", "vacuum_pressure"],
        },
    },
}

for _unit_type, _overrides in UNIT_METADATA_OVERRIDES.items():
    if _unit_type not in UNIT_REGISTRY:
        continue
    _spec = UNIT_REGISTRY[_unit_type]
    for _field_name, _field_value in _overrides.items():
        setattr(_spec, _field_name, _field_value)


_AVAILABLE_IN_BOTH = {
    "Mixer",
    "Pump",
    "Splitter",
    "Tank",
    "StorageTank",
    "MembraneDistillation",
    "ProcessWaterCenter",
}
_LIQUID_HANDOFF_ROLES = {
    "effluent",
    "permeate",
    "liquid",
    "mixed_outlet",
    "process_water",
    "converted_stream",
    "outlet",
}
_MCAS_HANDOFF_MODELS = {"mASM2d", "mADM1"}
_STRICT_COST_SUPPORTED_UNITS = {
    "Pump",
    "WWTpump",
    "SludgePump",
    "StorageTank",
    "MixTank",
    "PrimaryClarifier",
    "BeltThickener",
    "HydraulicDelay",
    "Screening",
}


def _default_use_cases_for_zone(zone_family: str) -> List[str]:
    if zone_family == "waterline_asm":
        return ["dynamic biological treatment", "recycle-rich activated sludge", "front-end wastewater treatment"]
    if zone_family == "waterline_adm":
        return ["dynamic anaerobic treatment", "industrial pretreatment", "biogas-generating workflows"]
    if zone_family == "thermal_gas":
        return ["sludge thermal treatment", "biogas energy recovery"]
    if zone_family == "membrane_recovery":
        return ["sidestream recovery", "membrane-based polishing"]
    if zone_family == "sanitation_sludge":
        return ["sludge handling", "stabilization and dewatering"]
    return ["flowsheet construction", "supporting process operations"]


for _spec in UNIT_REGISTRY.values():
    if not _spec.publicly_supported:
        _spec.solve_supported = False

    if not _spec.recommended_use_cases:
        _spec.recommended_use_cases = _default_use_cases_for_zone(_spec.zone_family)

    if _spec.unit_type in _AVAILABLE_IN_BOTH:
        _spec.engine_fit = "available_in_both"

    if not _spec.handoff_ready_profiles:
        roles = set(_spec.output_roles)
        profiles: List[str] = []
        if roles & _LIQUID_HANDOFF_ROLES and set(_spec.compatible_models) & _MCAS_HANDOFF_MODELS:
            profiles.append("MCAS_BRACKISH_5ION")
        _spec.handoff_ready_profiles = profiles

    if not _spec.engineering_profiles:
        _spec.engineering_profiles = [{"id": "default", "label": "Default"}]

    if not _spec.engineering_inputs:
        grouped, defaults = _derive_engineering_contract(_spec)
        _spec.engineering_inputs = grouped
        _spec.applied_profile_defaults = defaults
    elif not _spec.applied_profile_defaults:
        _spec.applied_profile_defaults = {
            field.name: field.default
            for fields in _spec.engineering_inputs.values()
            for field in fields
            if field.default is not None
        }

    if _spec.unit_type in _STRICT_COST_SUPPORTED_UNITS:
        _spec.costing = CostingSpec(
            costing_supported=True,
            costing_package="qsdsan_simpletea",
            costing_basis="upstream_qsdsan_biosteam",
            cost_certified=True,
            required_economic_inputs=[
                "discount_rate",
                "lifetime_years",
                "uptime_ratio",
                "annual_labor",
                "annual_maintenance_factor",
                "electricity_price",
            ],
        )
    elif not _spec.costing.costing_supported:
        _spec.costing = CostingSpec(
            costing_supported=False,
            costing_package="qsdsan_simpletea",
            costing_basis="solve_only",
            cost_certified=False,
            required_economic_inputs=[
                "discount_rate",
                "lifetime_years",
                "uptime_ratio",
                "annual_labor",
                "annual_maintenance_factor",
                "electricity_price",
            ],
            support_note="This public unit is solve-supported, but strict upstream-backed costing is not certified.",
        )


# =============================================================================
# Validation and Query Functions
# =============================================================================

def get_unit_spec(unit_type: str) -> UnitSpec:
    """
    Get unit specification by type name.

    Args:
        unit_type: Unit type identifier (e.g., "CSTR")

    Returns:
        UnitSpec for the requested unit type

    Raises:
        ValueError: If unit type not found in registry
    """
    if unit_type not in UNIT_REGISTRY:
        available = ", ".join(sorted(UNIT_REGISTRY.keys()))
        raise ValueError(
            f"Unknown unit type '{unit_type}'. "
            f"Available types: {available}"
        )
    return UNIT_REGISTRY[unit_type]


def validate_unit_params(
    unit_type: str,
    params: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """
    Validate parameters for a unit type.

    Args:
        unit_type: Unit type identifier
        params: Dictionary of parameter name -> value

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    try:
        spec = get_unit_spec(unit_type)
    except ValueError as e:
        return [str(e)], []

    # Check required parameters
    for param_name, param_type in spec.required_params.items():
        if param_name not in params:
            errors.append(f"Missing required parameter: {param_name}")
        elif params[param_name] is not None:
            # Type check
            value = params[param_name]
            if param_type == float and not isinstance(value, (int, float)):
                errors.append(
                    f"Parameter '{param_name}' must be numeric, got {type(value).__name__}"
                )
            elif param_type == list and not isinstance(value, list):
                errors.append(
                    f"Parameter '{param_name}' must be a list, got {type(value).__name__}"
                )

    # Check for unknown parameters
    all_known = set(spec.required_params.keys()) | set(spec.optional_params.keys())
    unknown = set(params.keys()) - all_known
    if unknown:
        warnings.append(f"Unknown parameters (will be passed to QSDsan): {unknown}")

    return errors, warnings


def validate_unit_bootstrap(
    unit_type: str,
    aux_config: Optional[Dict[str, Any]] = None,
    source_spec: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Validate public wrapper bootstrap requirements for a unit.

    ``aux_config`` is explicit unit-side configuration for specialized wrappers.
    ``source_spec`` is only used by source-like units such as DynamicInfluent.
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        spec = get_unit_spec(unit_type)
    except ValueError as e:
        return [str(e)], []

    aux_config = aux_config or {}
    source_spec = source_spec or None

    if aux_config and not isinstance(aux_config, dict):
        errors.append("aux_config must be a JSON object")
        aux_config = {}
    if source_spec is not None and not isinstance(source_spec, dict):
        errors.append("source_spec must be a JSON object")
        source_spec = None

    if spec.bootstrap_policy == "requires_aux_config" and not aux_config:
        errors.append(f"Unit '{unit_type}' requires aux_config")
    if spec.bootstrap_policy == "requires_source_spec" and source_spec is None:
        errors.append(f"Unit '{unit_type}' requires source_spec")
    if spec.bootstrap_policy == "requires_source_spec" and source_spec is not None:
        mode = source_spec.get("mode", "constant")
        if mode == "constant":
            if "flow_m3_d" not in source_spec and "Q" not in source_spec:
                errors.append(
                    f"Unit '{unit_type}' constant source_spec requires flow_m3_d or Q"
                )
            if not isinstance(source_spec.get("concentrations", {}), dict):
                errors.append(
                    f"Unit '{unit_type}' constant source_spec requires concentrations as an object"
                )
        elif mode == "timeseries":
            points = source_spec.get("points")
            if not isinstance(points, list) or len(points) < 2:
                errors.append(
                    f"Unit '{unit_type}' timeseries source_spec requires at least 2 points"
                )
        else:
            errors.append(
                f"Unit '{unit_type}' source_spec mode must be 'constant' or 'timeseries'"
            )

    required_aux = spec.aux_schema.get("required", [])
    missing_aux = [key for key in required_aux if key not in aux_config]
    if missing_aux:
        errors.append(
            f"Unit '{unit_type}' is missing required aux_config keys: {missing_aux}"
        )

    unknown_aux = sorted(set(aux_config) - set(required_aux) - set(spec.aux_schema.get("optional", [])))
    if unknown_aux:
        warnings.append(f"Unknown aux_config keys (passed through to wrapper): {unknown_aux}")

    return errors, warnings


def compile_engineering_inputs(
    unit_type: str,
    engineering_inputs: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compile public engineering inputs into internal fields used by wrappers/builders.

    Returns a payload containing ``params``, ``aux_config``, ``source_spec``,
    ``applied_defaults``, ``errors``, and ``warnings``.
    """
    try:
        spec = get_unit_spec(unit_type)
    except ValueError as e:
        return {
            "params": {},
            "aux_config": {},
            "source_spec": None,
            "applied_defaults": {},
            "errors": [str(e)],
            "warnings": [],
        }

    params, aux_config, source_spec, applied_defaults, errors, warnings = _compile_public_engineering_inputs(
        spec,
        engineering_inputs,
    )

    param_errors, param_warnings = validate_unit_params(unit_type, params)
    bootstrap_errors, bootstrap_warnings = validate_unit_bootstrap(
        unit_type,
        aux_config=aux_config,
        source_spec=source_spec,
    )

    return {
        "params": params,
        "aux_config": aux_config,
        "source_spec": source_spec,
        "applied_defaults": applied_defaults,
        "errors": errors + param_errors + bootstrap_errors,
        "warnings": warnings + param_warnings + bootstrap_warnings,
    }


def summarize_engineering_inputs(unit_type: str) -> Dict[str, Any]:
    """Return the public grouped engineering-input schema for one unit."""
    spec = get_unit_spec(unit_type)
    return {
        category: [field.serialize() for field in spec.engineering_inputs.get(category, [])]
        for category in _ENGINEERING_INPUT_CATEGORIES
        if spec.engineering_inputs.get(category)
    }


def validate_model_compatibility(
    unit_type: str,
    model_type: str,
) -> Tuple[bool, Optional[str]]:
    """
    Check if a unit type is compatible with a process model.

    Args:
        unit_type: Unit type identifier
        model_type: Process model (e.g., "ASM2d", "mADM1")

    Returns:
        Tuple of (is_compatible, error_message or None)
    """
    try:
        spec = get_unit_spec(unit_type)
    except ValueError as e:
        return False, str(e)

    # Empty compatible_models means model-agnostic
    if not spec.compatible_models:
        return True, None

    # Normalize model name for comparison
    normalized_model = normalize_model_name(model_type)

    # Check compatibility with normalized names
    for compat_model in spec.compatible_models:
        if normalize_model_name(compat_model) == normalized_model:
            return True, None

    return False, (
        f"Unit '{unit_type}' is not compatible with model '{model_type}'. "
        f"Compatible models: {spec.compatible_models}"
    )


# =============================================================================
# Junction Model Transform Registry (Phase 9)
# =============================================================================

# Junction transforms: (input_model, output_model)
# Note: "mADM1" = "ADM1p/ADM1_p_extension" in upstream QSDsan
JUNCTION_MODEL_TRANSFORMS: Dict[str, Tuple[str, str]] = {
    "ADM1ptomASM2d": ("mADM1", "mASM2d"),    # mADM1/ADM1p -> mASM2d
    "mASM2dtoADM1p": ("mASM2d", "mADM1"),    # mASM2d -> mADM1/ADM1p
}


def _public_junction_model_transforms() -> Dict[str, Tuple[str, str]]:
    """Return only junction transforms still supported on the public surface."""
    return {
        unit_type: transform
        for unit_type, transform in JUNCTION_MODEL_TRANSFORMS.items()
        if unit_type in UNIT_REGISTRY
        and UNIT_REGISTRY[unit_type].publicly_supported
    }


def get_junction_output_model(unit_type: str) -> Optional[Tuple[str, str]]:
    """
    Return (input_model, output_model) for junction unit types.

    Args:
        unit_type: Unit type identifier (e.g., "mASM2dtoADM1p")

    Returns:
        Tuple of (input_model, output_model) if unit is a junction, None otherwise
    """
    return JUNCTION_MODEL_TRANSFORMS.get(unit_type)


def models_compatible(model_a: str, model_b: str) -> bool:
    """
    Check if two model names refer to the same model (accounting for aliases).

    Args:
        model_a: First model name
        model_b: Second model name

    Returns:
        True if models are equivalent (considering aliases)
    """
    norm_a = normalize_model_name(model_a)
    norm_b = normalize_model_name(model_b)
    return norm_a == norm_b


def suggest_junction_for_conversion(from_model: str, to_models: List[str]) -> Optional[str]:
    """
    Suggest junction unit type to convert from one model to another.

    Args:
        from_model: Current model type (e.g., "ASM2d")
        to_models: List of target model types the unit supports

    Returns:
        Suggestion string with junction name, or None if no conversion available
    """
    from_norm = normalize_model_name(from_model)
    for to_model in to_models:
        to_norm = normalize_model_name(to_model)
        for junction, (inp, out) in _public_junction_model_transforms().items():
            if normalize_model_name(inp) == from_norm and normalize_model_name(out) == to_norm:
                return f"Add '{junction}' before this unit to convert from {from_model} to {to_model}"
    return None


def find_junction_for_conversion(from_model: str, to_model: str) -> Optional[str]:
    """
    Find junction unit type that converts from_model to to_model.

    Phase 10: Used by server.py for auto-inserting junctions on fan-in mismatch.

    NOTE: The public transform registry now covers only the canonical A1
    junction family used by the supported mixed-model path.

    Args:
        from_model: Source model type (e.g., "mADM1", "mASM2d")
        to_model: Target model type (e.g., "mASM2d", "mADM1")

    Returns:
        Junction unit type name if available, None otherwise.

    Example:
        >>> find_junction_for_conversion("mADM1", "mASM2d")
        "ADM1ptomASM2d"
        >>> find_junction_for_conversion("mASM2d", "mADM1")
        "mASM2dtoADM1p"
    """
    from_norm = normalize_model_name(from_model)
    to_norm = normalize_model_name(to_model)

    for junction, (inp, out) in _public_junction_model_transforms().items():
        if normalize_model_name(inp) == from_norm and normalize_model_name(out) == to_norm:
            return junction
    return None


def get_public_support_error(unit_type: str) -> Optional[str]:
    """
    Return a public-surface error for unsupported units, if any.

    The MCP/CLI adapters only expose units proven to build and simulate through
    the dynamic flowsheet path.
    """
    spec = get_unit_spec(unit_type)
    if spec.publicly_supported:
        return None

    prefix = (
        f"Unit '{unit_type}' is not supported for public MCP/CLI flowsheet "
        "construction."
    )
    if spec.support_note:
        prefix += f" {spec.support_note}"
    return prefix


def list_available_units(
    model_type: Optional[str] = None,
    category: Optional[str] = None,
    dynamic_only: bool = False,
    public_only: bool = False,
) -> List[Dict[str, Any]]:
    """
    List available unit types with optional filtering.

    Args:
        model_type: Filter by compatible process model
        category: Filter by unit category
        dynamic_only: If True, only return units supporting dynamic simulation
        public_only: If True, only return units currently supported for MCP/CLI
            dynamic flowsheet construction.

    Returns:
        List of unit specifications as dictionaries
    """
    results = []

    for unit_type, spec in UNIT_REGISTRY.items():
        # Filter by model compatibility
        if model_type:
            if spec.compatible_models and model_type not in spec.compatible_models:
                continue

        # Filter by category
        if category:
            if spec.category.value != category:
                continue

        # Filter by dynamic capability
        if dynamic_only and not spec.is_dynamic:
            continue

        # Filter to publicly supported units when requested
        if public_only and not spec.publicly_supported:
            continue

        payload = serialize_unit_spec(spec)
        payload["engineering_input_names"] = sorted(
            field["name"]
            for fields in payload.get("engineering_inputs", {}).values()
            for field in fields
        )
        results.append(payload)

    return results


def get_units_by_category(public_only: bool = False) -> Dict[str, List[str]]:
    """
    Get unit types organized by category.

    Returns:
        Dict of category -> list of unit types
    """
    result: Dict[str, List[str]] = {}

    for unit_type, spec in UNIT_REGISTRY.items():
        if public_only and not spec.publicly_supported:
            continue
        cat = spec.category.value
        if cat not in result:
            result[cat] = []
        result[cat].append(unit_type)

    # Sort each category
    for cat in result:
        result[cat].sort()

    return result


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'UnitCategory',
    'UNIT_OP_TYPE_VALUES',
    'EngineeringInputSpec',
    'CostingSpec',
    'QSDSAN_CATEGORY_TO_UNIT_OP_TYPE',
    'UnitSpec',
    'UnitOpType',
    'UNIT_REGISTRY',
    'get_unit_spec',
    'validate_unit_params',
    'validate_unit_bootstrap',
    'compile_engineering_inputs',
    'summarize_engineering_inputs',
    'validate_model_compatibility',
    'list_available_units',
    'serialize_unit_spec',
    'get_units_by_category',
    'normalize_unit_op_type',
    'qsdsan_category_to_unit_op_type',
    # Phase 9: Junction model transforms
    'MODEL_ALIASES',
    'JUNCTION_MODEL_TRANSFORMS',
    'normalize_model_name',
    'get_junction_output_model',
    'models_compatible',
    'suggest_junction_for_conversion',
    # Phase 10: Auto-insert junctions
    'find_junction_for_conversion',
    'get_public_support_error',
    'unit_op_type_values',
]
