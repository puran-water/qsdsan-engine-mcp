"""
Model Registry - Component and parameter management for QSDsan models.

Maps model types to their required components, parameters, and validation rules.
This enables the engine to work with multiple biological models (mADM1, ASM2d, etc.)
while maintaining strict component consistency.
"""

from typing import Dict, List, Set, Tuple, Any
from qsdsan_engine_mcp.core.components.catalog import MODEL_BIOGAS_COMPONENTS as CATALOG_BIOGAS_COMPONENTS, MODEL_BIOMASS_COMPONENTS as CATALOG_BIOMASS_COMPONENTS, MODEL_COMPONENTS as CATALOG_MODEL_COMPONENTS
from qsdsan_engine_mcp.core.model_types import ModelType


# =============================================================================
# Model Name Normalization
# =============================================================================

# Preserve the previous alias surface from core.unit_registry while also
# accepting common case-insensitive user input from CLI and MCP clients.
MODEL_ALIASES: Dict[str, Set[str]] = {
    "mADM1": {"mADM1", "MADM1", "madm1", "ADM1p", "adm1p", "ADM1_p_extension", "adm1_p_extension"},
    "ADM1p": {"mADM1", "MADM1", "madm1", "ADM1p", "adm1p", "ADM1_p_extension", "adm1_p_extension"},
    "ADM1_p_extension": {"mADM1", "MADM1", "madm1", "ADM1p", "adm1p", "ADM1_p_extension", "adm1_p_extension"},
    "ADM1": {"ADM1", "adm1"},
    "ASM2d": {"ASM2d", "ASM2D", "asm2d"},
    "mASM2d": {"mASM2d", "MASM2D", "masm2d"},
    "mASM2d2N": {"mASM2d2N", "MASM2D2N", "masm2d2n", "mASM2d_2n", "masm2d_2n"},
    "mASM2d2N_N2O": {"mASM2d2N_N2O", "MASM2D2N_N2O", "masm2d2n_n2o", "mASM2d2N_n2o",
                     "mASM2d_2n_n2o", "masm2d_2n_n2o"},
    "ASM1": {"ASM1", "asm1"},
}

_MODEL_CANONICAL_NAMES: Dict[str, str] = {
    "madm1": ModelType.MADM1.value,
    "adm1p": ModelType.MADM1.value,
    "adm1_p_extension": ModelType.MADM1.value,
    "adm1": ModelType.ADM1.value,
    "asm2d": ModelType.ASM2D.value,
    "masm2d": ModelType.MASM2D.value,
    "masm2d2n": ModelType.MASM2D_2N.value,
    "masm2d_2n": ModelType.MASM2D_2N.value,
    "masm2d2n_n2o": ModelType.MASM2D_2N_N2O.value,
    "masm2d_2n_n2o": ModelType.MASM2D_2N_N2O.value,
    "asm1": ModelType.ASM1.value,
}


def normalize_model_name(model: str) -> str:
    """
    Normalize a user-provided model name to the canonical internal value.

    Accepts common aliases and case variants such as ``ASM2D`` and ``adm1p``.
    """
    if not isinstance(model, str):
        raise TypeError(f"Model name must be a string, got {type(model).__name__}")

    candidate = model.strip()
    if not candidate:
        raise ValueError("Model name cannot be empty")

    canonical = _MODEL_CANONICAL_NAMES.get(candidate)
    if canonical:
        return canonical

    canonical = _MODEL_CANONICAL_NAMES.get(candidate.lower())
    if canonical:
        return canonical

    valid = ", ".join(mt.value for mt in ModelType)
    raise ValueError(f"Unknown model type: {model}. Valid: {valid}")

# =============================================================================
# Model Registry
# =============================================================================
MADM1_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.MADM1.value]
MADM1_BIOGAS_IDS = CATALOG_BIOGAS_COMPONENTS[ModelType.MADM1.value] or ()
MADM1_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.MADM1.value]
ASM2D_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.ASM2D.value]
ASM2D_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.ASM2D.value]
ASM1_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.ASM1.value]
ASM1_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.ASM1.value]
MASM2D_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.MASM2D.value]
MASM2D_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.MASM2D.value]
MASM2D_2N_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.MASM2D_2N.value]
MASM2D_2N_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.MASM2D_2N.value]
MASM2D_2N_N2O_COMPONENTS = CATALOG_MODEL_COMPONENTS[ModelType.MASM2D_2N_N2O.value]
MASM2D_2N_N2O_BIOMASS_IDS = CATALOG_BIOMASS_COMPONENTS[ModelType.MASM2D_2N_N2O.value]

MODEL_REGISTRY: Dict[ModelType, Dict[str, Any]] = {
    ModelType.MADM1: {
        'components': MADM1_COMPONENTS,
        'n_components': len(MADM1_COMPONENTS),
        'biogas_ids': MADM1_BIOGAS_IDS,
        'biomass_ids': MADM1_BIOMASS_IDS,
        'n_biogas': len(MADM1_BIOGAS_IDS),
        'description': 'Modified ADM1 with P/S/Fe + Na/trace-element extensions (full mADM1 component set)',
        'status': 'stable',
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
        'status': 'stable',
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
        'status': 'stable',
        'default_temperature_K': 293.15,  # 20°C
        'typical_hrt_days': (0.2, 1.0),  # 5-24 hours
        'template': 'aerobic/mle_mbr',
    },
    ModelType.MASM2D: {
        'components': MASM2D_COMPONENTS,
        'n_components': len(MASM2D_COMPONENTS),
        'biogas_ids': None,
        'biomass_ids': MASM2D_BIOMASS_IDS,
        'n_biogas': 0,
        'description': 'Modified ASM2d with ions/precipitate extensions (31 components)',
        'status': 'stable',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 1.0),
        'template': 'aerobic/mle_mbr',
    },
    ModelType.MASM2D_2N: {
        'components': MASM2D_2N_COMPONENTS,
        'n_components': len(MASM2D_2N_COMPONENTS),
        'biogas_ids': None,
        'biomass_ids': MASM2D_2N_BIOMASS_IDS,
        'n_biogas': 0,
        'description': 'Two-step nitrification mASM2d (AOB/NOB + FA/FNA inhibition, S_NO2; 33 components)',
        'status': 'stable',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 1.0),
        'template': 'aerobic/mle_mbr',
    },
    ModelType.MASM2D_2N_N2O: {
        'components': MASM2D_2N_N2O_COMPONENTS,
        'n_components': len(MASM2D_2N_N2O_COMPONENTS),
        'biogas_ids': None,
        'biomass_ids': MASM2D_2N_N2O_BIOMASS_IDS,
        'n_biogas': 0,
        'description': 'Two-step nitrification + two-pathway AOB N2O (Pocquet 2016; S_NH2OH/S_NO/S_N2O; 36 components)',
        'status': 'stable',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 1.0),
        'template': 'aerobic/mle_mbr',
    },
    ModelType.ASM1: {
        'components': ASM1_COMPONENTS,
        'n_components': len(ASM1_COMPONENTS),
        'biogas_ids': None,
        'biomass_ids': ASM1_BIOMASS_IDS,
        'n_biogas': 0,
        'description': 'Activated Sludge Model 1 (13 components)',
        'default_temperature_K': 293.15,
        'typical_hrt_days': (0.2, 0.5),
        'template': None,
        'status': 'experimental',
        'notes': 'Metadata/validation only. No template-backed simulation is shipped yet.',
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


def _optional_components(model_type: ModelType) -> Set[str]:
    """IDs the catalog marks ``required=False`` (optional) for this model — e.g. the mADM1 trace
    micronutrient metals S_Co/S_Ni/S_Se. Absent ones are NEVER 'missing' (they default to 0)."""
    try:
        from qsdsan_engine_mcp.core.components.catalog import get_component_specs
        return {s.id for s in get_component_specs(model_type) if not s.required}
    except Exception:
        return set()


def validate_components(model_type: ModelType, provided_components: Set[str]) -> Tuple[List[str], List[str]]:
    """
    Validate provided components against model requirements.

    OPTIONAL components (``required=False`` in the catalog, e.g. the mADM1 trace metals) are not
    "missing" when absent (they default to 0) and not "extra" when present. Required-but-absent
    components are missing; genuinely-unknown components are extra.

    Returns:
        Tuple of (missing_components, extra_components)
    """
    all_components = get_required_components(model_type)        # full model component set
    optional = _optional_components(model_type)
    missing = list((all_components - optional) - provided_components)
    extra = list(provided_components - all_components)
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
            'status': info.get('status', 'stable'),
        }
        for mt, info in MODEL_REGISTRY.items()
    ]
