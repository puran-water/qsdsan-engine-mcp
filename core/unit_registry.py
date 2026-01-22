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
        compatible_models=["ASM2d", "mASM2d", "ASM1"],  # Primary: ASM2d
        required_params={},  # V_max is optional with default 1000
        optional_params={
            "V_max": 1000.0,  # Default from QSDsan
            "aeration": 2.0,  # Default aeration rate from QSDsan
            "DO_ID": "S_O2",
            "suspended_growth_model": None,
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
        n_outs=2,  # liquid + biogas
    ),
    "AnaerobicCSTRmADM1": UnitSpec(
        unit_type="AnaerobicCSTRmADM1",
        category=UnitCategory.REACTOR,
        description="Anaerobic CSTR with mADM1 (63 components, 4 biogas species)",
        compatible_models=["mADM1"],
        required_params={"V_liq": float},
        optional_params={
            "V_gas": 300.0,
            "T": 308.15,
            "headspace_P": 1.013,
            "external_P": 1.013,
        },
        qsdsan_class="models.reactors.AnaerobicCSTRmADM1",
        is_dynamic=True,
        n_ins=1,
        n_outs=2,  # liquid + biogas
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
    ),

    # ==================== MEMBRANE BIOREACTORS ====================
    "CompletelyMixedMBR": UnitSpec(
        unit_type="CompletelyMixedMBR",
        category=UnitCategory.SEPARATOR,
        description="MBR with ideal membrane separation (inherits from CSTR)",
        compatible_models=["ASM2d", "mASM2d", "ASM1"],  # Primary: ASM2d
        required_params={},  # Inherits CSTR defaults
        optional_params={
            "V_max": 1000.0,  # Default from CSTR
            "solids_capture_rate": 0.999,
            "pumped_flow": 50.0,  # Default permeate flow (m3/d)
            "aeration": 2.0,  # Default from CSTR
            "DO_ID": "S_O2",
            "gas_stripping": False,
            "crossflow_air": None,  # For crossflow aeration
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
        optional_params={"pump_type": "centrifugal"},
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

    # ==================== JUNCTIONS (STATE CONVERTERS) ====================
    "ASM2dtoADM1": UnitSpec(
        unit_type="ASM2dtoADM1",
        category=UnitCategory.JUNCTION,
        description="Convert ASM2d state to ADM1 for digester feed",
        compatible_models=["ASM2d", "ADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ASM2dtoADM1",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "ADM1toASM2d": UnitSpec(
        unit_type="ADM1toASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert ADM1 state to ASM2d for sidestream return",
        compatible_models=["ADM1", "ASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ADM1toASM2d",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "mADM1toASM2d": UnitSpec(
        unit_type="mADM1toASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert mADM1 state to ASM2d",
        compatible_models=["mADM1", "ASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.mADM1toASM2d",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "ASM2dtomADM1": UnitSpec(
        unit_type="ASM2dtomADM1",
        category=UnitCategory.JUNCTION,
        description="Convert ASM2d state to mADM1",
        compatible_models=["ASM2d", "mADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ASM2dtomADM1",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),

    # ==================== UTILITY UNITS ====================
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
    ),

    # ==================== ADDITIONAL CLARIFIERS (BSM2) ====================
    "PrimaryClarifierBSM2": UnitSpec(
        unit_type="PrimaryClarifierBSM2",
        category=UnitCategory.CLARIFIER,
        description="Primary clarifier with BSM2 settling model",
        compatible_models=["ASM2d", "mASM2d", "ASM1"],
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
        compatible_models=["ASM2d", "mASM2d", "ASM1"],
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
    ),

    # ==================== MEMBRANE PROCESSES ====================
    "MembraneDistillation": UnitSpec(
        unit_type="MembraneDistillation",
        category=UnitCategory.SEPARATOR,
        description="Membrane distillation for water recovery",
        compatible_models=[],
        required_params={},
        optional_params={
            "membrane_area": 10.0,  # m²
            "recovery": 0.7,
        },
        qsdsan_class="qsdsan.sanunits.MembraneDistillation",
        is_dynamic=False,
        n_ins=1,
        n_outs=2,  # permeate, concentrate
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
    ),

    # ==================== ADDITIONAL JUNCTIONS (ADM1p/mASM2d) ====================
    "ADM1ptomASM2d": UnitSpec(
        unit_type="ADM1ptomASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert ADM1-P extension state to mASM2d",
        compatible_models=["ADM1", "mASM2d"],
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
        description="Convert mASM2d state to ADM1-P extension",
        compatible_models=["mASM2d", "ADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.mASM2dtoADM1p",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "ASMtoADM": UnitSpec(
        unit_type="ASMtoADM",
        category=UnitCategory.JUNCTION,
        description="Generic ASM to ADM interface (base class)",
        compatible_models=["ASM1", "ASM2d", "ADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ASMtoADM",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    "ADMtoASM": UnitSpec(
        unit_type="ADMtoASM",
        category=UnitCategory.JUNCTION,
        description="Generic ADM to ASM interface (base class)",
        compatible_models=["ADM1", "ASM1", "ASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="qsdsan.sanunits.ADMtoASM",
        is_dynamic=True,
        n_ins=1,
        n_outs=1,
    ),
    # NOTE: Disinfection removed - not in QSDsan sanunits
    # May be implemented in future QSDsan versions
}


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

# Model name aliases (QSDsan uses ADM1_p_extension/ADM1p, we use mADM1 internally)
MODEL_ALIASES: Dict[str, set] = {
    "mADM1": {"mADM1", "ADM1p", "ADM1_p_extension"},
    "ADM1p": {"mADM1", "ADM1p", "ADM1_p_extension"},
    "ADM1_p_extension": {"mADM1", "ADM1p", "ADM1_p_extension"},
    "mASM2d": {"mASM2d"},
    "ASM2d": {"ASM2d"},
    "ASM1": {"ASM1"},
    "ADM1": {"ADM1"},
}


def normalize_model_name(model: str) -> str:
    """
    Normalize model name to internal convention.

    Args:
        model: Model name (e.g., "ADM1p", "ADM1_p_extension", "mADM1")

    Returns:
        Normalized model name (e.g., "mADM1" for all ADM1 with P/S/Fe extensions)
    """
    if model in ("ADM1p", "ADM1_p_extension"):
        return "mADM1"  # Our internal name for ADM1 with P/S/Fe extensions
    return model


# Junction transforms: (input_model, output_model)
# Note: "mADM1" = "ADM1p/ADM1_p_extension" in upstream QSDsan
JUNCTION_MODEL_TRANSFORMS: Dict[str, Tuple[str, str]] = {
    "ASM2dtomADM1": ("ASM2d", "mADM1"),      # ASM2d -> mADM1 (63 components)
    "mADM1toASM2d": ("mADM1", "ASM2d"),      # mADM1 -> ASM2d
    "ASM2dtoADM1": ("ASM2d", "ADM1"),        # ASM2d -> ADM1 (35 components)
    "ADM1toASM2d": ("ADM1", "ASM2d"),        # ADM1 -> ASM2d
    "ADM1ptomASM2d": ("mADM1", "mASM2d"),    # mADM1/ADM1p -> mASM2d
    "mASM2dtoADM1p": ("mASM2d", "mADM1"),    # mASM2d -> mADM1/ADM1p
    "ASMtoADM": ("ASM1", "ADM1"),            # Generic ASM1 -> ADM1
    "ADMtoASM": ("ADM1", "ASM1"),            # Generic ADM1 -> ASM1
}


def get_junction_output_model(unit_type: str) -> Optional[Tuple[str, str]]:
    """
    Return (input_model, output_model) for junction unit types.

    Args:
        unit_type: Unit type identifier (e.g., "ASM2dtomADM1")

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
        for junction, (inp, out) in JUNCTION_MODEL_TRANSFORMS.items():
            if normalize_model_name(inp) == from_norm and normalize_model_name(out) == to_norm:
                return f"Add '{junction}' before this unit to convert from {from_model} to {to_model}"
    return None


def list_available_units(
    model_type: Optional[str] = None,
    category: Optional[str] = None,
    dynamic_only: bool = False,
) -> List[Dict[str, Any]]:
    """
    List available unit types with optional filtering.

    Args:
        model_type: Filter by compatible process model
        category: Filter by unit category
        dynamic_only: If True, only return units supporting dynamic simulation

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

        results.append({
            "unit_type": spec.unit_type,
            "category": spec.category.value,
            "description": spec.description,
            "compatible_models": spec.compatible_models or ["all"],
            "required_params": list(spec.required_params.keys()),
            "optional_params": list(spec.optional_params.keys()),
            "is_dynamic": spec.is_dynamic,
            "n_ins": spec.n_ins,
            "n_outs": spec.n_outs,
        })

    return results


def get_units_by_category() -> Dict[str, List[str]]:
    """
    Get unit types organized by category.

    Returns:
        Dict of category -> list of unit types
    """
    result: Dict[str, List[str]] = {}

    for unit_type, spec in UNIT_REGISTRY.items():
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
    'UnitSpec',
    'UNIT_REGISTRY',
    'get_unit_spec',
    'validate_unit_params',
    'validate_model_compatibility',
    'list_available_units',
    'get_units_by_category',
    # Phase 9: Junction model transforms
    'MODEL_ALIASES',
    'JUNCTION_MODEL_TRANSFORMS',
    'normalize_model_name',
    'get_junction_output_model',
    'models_compatible',
    'suggest_junction_for_conversion',
]
