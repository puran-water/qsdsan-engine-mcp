"""
Model Registry - Component and parameter management for QSDsan models.

Maps model types to their required components, parameters, and validation rules.
This enables the engine to work with multiple biological models (mADM1, ASM2d, etc.)
while maintaining strict component consistency.
"""

from typing import Dict, List, Set, Tuple, Any
from core.plant_state import ModelType


# =============================================================================
# mADM1 - Modified Anaerobic Digestion Model No. 1 (63 components)
# =============================================================================
# Component indices from qsdsan_madm1.py
MADM1_COMPONENTS: Tuple[str, ...] = (
    # Soluble substrates (0-12)
    'S_su',    # 0: Sugars
    'S_aa',    # 1: Amino acids
    'S_fa',    # 2: Long-chain fatty acids
    'S_va',    # 3: Valerate
    'S_bu',    # 4: Butyrate
    'S_pro',   # 5: Propionate
    'S_ac',    # 6: Acetate
    'S_h2',    # 7: Hydrogen
    'S_ch4',   # 8: Methane
    'S_IC',    # 9: Inorganic carbon
    'S_IN',    # 10: Inorganic nitrogen
    'S_IP',    # 11: Inorganic phosphorus
    'S_I',     # 12: Soluble inerts

    # Particulate substrates (13-15)
    'X_ch',    # 13: Carbohydrates
    'X_pr',    # 14: Proteins
    'X_li',    # 15: Lipids

    # Biomass (16-22)
    'X_su',    # 16: Sugar degraders
    'X_aa',    # 17: Amino acid degraders
    'X_fa',    # 18: LCFA degraders
    'X_c4',    # 19: Valerate/butyrate degraders
    'X_pro',   # 20: Propionate degraders
    'X_ac',    # 21: Acetoclastic methanogens
    'X_h2',    # 22: Hydrogenotrophic methanogens
    'X_I',     # 23: Particulate inerts

    # PAO and storage (24-26)
    'X_PHA',   # 24: Poly-hydroxy-alkanoates
    'X_PP',    # 25: Poly-phosphate
    'X_PAO',   # 26: Phosphorus accumulating organisms

    # Ions (27-28)
    'S_K',     # 27: Potassium
    'S_Mg',    # 28: Magnesium

    # Sulfur cycle (29-34)
    'S_SO4',   # 29: Sulfate
    'S_IS',    # 30: Total dissolved sulfide (H2S + HS-)
    'X_hSRB',  # 31: H2-utilizing SRB
    'X_aSRB',  # 32: Acetate-utilizing SRB
    'X_pSRB',  # 33: Propionate-utilizing SRB
    'X_c4SRB', # 34: Butyrate-utilizing SRB

    # Iron cycle (35-44)
    'S_S0',    # 35: Elemental sulfur
    'S_Fe3',   # 36: Fe(III)
    'S_Fe2',   # 37: Fe(II)
    'X_HFO_H', # 38: HFO high surface
    'X_HFO_L', # 39: HFO low surface
    'X_HFO_old',    # 40: Old HFO
    'X_HFO_HP',     # 41: HFO-P high surface
    'X_HFO_LP',     # 42: HFO-P low surface
    'X_HFO_HP_old', # 43: Old HFO-P high
    'X_HFO_LP_old', # 44: Old HFO-P low

    # Calcium/Aluminum (45-46)
    'S_Ca',    # 45: Calcium
    'S_Al',    # 46: Aluminum

    # Minerals (47-59)
    'X_CCM',   # 47: Calcite
    'X_ACC',   # 48: Aragonite
    'X_ACP',   # 49: Amorphous calcium phosphate
    'X_HAP',   # 50: Hydroxylapatite
    'X_DCPD',  # 51: Dicalcium phosphate
    'X_OCP',   # 52: Octacalcium phosphate
    'X_struv', # 53: Struvite
    'X_newb',  # 54: Newberyite
    'X_magn',  # 55: Magnesite
    'X_kstruv',# 56: K-struvite
    'X_FeS',   # 57: Iron sulfide
    'X_Fe3PO42', # 58: Ferrous phosphate
    'X_AlPO4', # 59: Aluminum phosphate

    # Charge balance (60-61)
    'S_Na',    # 60: Sodium
    'S_Cl',    # 61: Chloride

    # Water (62)
    'H2O',     # 62: Water
)

MADM1_BIOGAS_IDS: Tuple[str, ...] = ('S_h2', 'S_ch4', 'S_IC', 'S_IS')
MADM1_BIOMASS_IDS: Tuple[str, ...] = (
    'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',
    'X_PAO', 'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB'
)

# =============================================================================
# ASM2d - Activated Sludge Model 2d (with mASM2d extensions per Pune_Nanded)
# =============================================================================
ASM2D_COMPONENTS: Tuple[str, ...] = (
    # Soluble (0-12)
    'S_O2',    # Dissolved oxygen
    'S_F',     # Fermentable substrate
    'S_A',     # Fermentation products (acetate)
    'S_I',     # Inert soluble organic matter
    'S_NH4',   # Ammonium
    'S_N2',    # Dinitrogen
    'S_NO3',   # Nitrate
    'S_PO4',   # Phosphate
    'S_ALK',   # Alkalinity

    # Particulate (13-26)
    'X_I',     # Inert particulate organic matter
    'X_S',     # Slowly biodegradable substrate
    'X_H',     # Heterotrophic biomass
    'X_PAO',   # Phosphorus accumulating organisms
    'X_PP',    # Poly-phosphate
    'X_PHA',   # Poly-hydroxy-alkanoates
    'X_AUT',   # Autotrophic biomass
    'X_MeOH',  # Metal-hydroxides (chemical P removal precipitates)
    'X_MeP',   # Metal-phosphates (chemical P removal precipitates)

    # Water
    'H2O',
)

ASM2D_BIOMASS_IDS: Tuple[str, ...] = ('X_H', 'X_PAO', 'X_AUT', 'X_MeOH')

# =============================================================================
# Model Registry
# =============================================================================
MODEL_REGISTRY: Dict[ModelType, Dict[str, Any]] = {
    ModelType.MADM1: {
        'components': MADM1_COMPONENTS,
        'n_components': len(MADM1_COMPONENTS),
        'biogas_ids': MADM1_BIOGAS_IDS,
        'biomass_ids': MADM1_BIOMASS_IDS,
        'n_biogas': len(MADM1_BIOGAS_IDS),
        'description': 'Modified ADM1 with P/S/Fe extensions (63 components)',
        'default_temperature_K': 308.15,  # 35°C mesophilic
        'typical_hrt_days': (15, 30),
        'template': 'anaerobic/cstr',
    },
    ModelType.ADM1: {
        'components': None,  # Use upstream QSDsan create_adm1_cmps
        'n_components': 27,
        'biogas_ids': ('S_h2', 'S_ch4', 'S_IC'),
        'biomass_ids': ('X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2'),
        'n_biogas': 3,
        'description': 'Standard ADM1 (upstream QSDsan)',
        'default_temperature_K': 308.15,
        'typical_hrt_days': (15, 30),
        'template': 'anaerobic/cstr_standard',
    },
    ModelType.ASM2D: {
        'components': ASM2D_COMPONENTS,
        'n_components': len(ASM2D_COMPONENTS),
        'biogas_ids': None,
        'biomass_ids': ASM2D_BIOMASS_IDS,
        'n_biogas': 0,
        'description': 'Activated Sludge Model 2d',
        'default_temperature_K': 293.15,  # 20°C
        'typical_hrt_days': (0.2, 1.0),  # 5-24 hours
        'template': 'aerobic/mle_mbr',
    },
    ModelType.MASM2D: {
        'components': None,  # Use upstream QSDsan create_masm2d_cmps
        'n_components': None,
        'biogas_ids': None,
        'biomass_ids': ('X_H', 'X_PAO', 'X_AUT'),
        'n_biogas': 0,
        'description': 'Modified ASM2d with sulfur/iron extensions',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 1.0),
        'template': 'aerobic/mle_mbr',
    },
    ModelType.ASM1: {
        'components': None,  # Use upstream QSDsan
        'n_components': 13,
        'biogas_ids': None,
        'biomass_ids': ('X_BH', 'X_BA'),
        'n_biogas': 0,
        'description': 'Activated Sludge Model 1',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 0.5),
        'template': 'aerobic/mle_mbr',
    },
}


def get_model_info(model_type: ModelType) -> Dict[str, Any]:
    """Get model configuration info."""
    if model_type not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model type: {model_type}")
    return MODEL_REGISTRY[model_type]


def get_required_components(model_type: ModelType) -> Set[str]:
    """Get required component IDs for a model."""
    info = get_model_info(model_type)
    components = info.get('components')
    if components is None:
        raise ValueError(f"Component list not defined for {model_type}. Use QSDsan directly.")
    return set(components)


def get_component_index(model_type: ModelType, component_id: str) -> int:
    """Get index of a component in the state array."""
    info = get_model_info(model_type)
    components = info.get('components')
    if components is None:
        raise ValueError(f"Component list not defined for {model_type}")
    if component_id not in components:
        raise ValueError(f"Component {component_id} not in {model_type}")
    return components.index(component_id)


def validate_components(model_type: ModelType, provided_components: Set[str]) -> Tuple[List[str], List[str]]:
    """
    Validate provided components against model requirements.

    Returns:
        Tuple of (missing_components, extra_components)
    """
    required = get_required_components(model_type)
    missing = list(required - provided_components)
    extra = list(provided_components - required)
    return missing, extra


def list_available_models() -> List[Dict[str, Any]]:
    """List all available models with their descriptions."""
    return [
        {
            'model_type': mt.value,
            'description': info['description'],
            'n_components': info['n_components'],
            'default_temperature_K': info['default_temperature_K'],
            'template': info['template'],
        }
        for mt, info in MODEL_REGISTRY.items()
    ]
