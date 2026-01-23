"""
Kinetic Parameter Schema and Validation for mADM1.

This module provides the MADM1_KINETIC_SCHEMA with 80+ kinetic parameters
extracted from our custom ModifiedADM1.__new__() implementation.

Usage:
    from core.kinetic_params import validate_kinetic_params, MADM1_KINETIC_SCHEMA

    validated, warnings = validate_kinetic_params({"k_ac": 8.0, "K_ac": 0.15})
"""

from typing import Dict, List, Tuple, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Molecular weights for unit reference
S_mw = 32.065  # Sulfur
Fe_mw = 55.845  # Iron


# =============================================================================
# mADM1 Kinetic Parameter Schema
# Extracted from models/madm1.py ModifiedADM1.__new__() (lines 1265-1294)
# =============================================================================

MADM1_KINETIC_SCHEMA: Dict[str, Dict[str, Any]] = {
    # ==================== STOICHIOMETRIC FRACTIONS ====================
    # Biomass decay fractions
    "f_ch_xb": {"default": 0.275, "range": (0.0, 1.0), "units": "-", "description": "Fraction to carbohydrates from biomass decay"},
    "f_pr_xb": {"default": 0.275, "range": (0.0, 1.0), "units": "-", "description": "Fraction to proteins from biomass decay"},
    "f_li_xb": {"default": 0.35, "range": (0.0, 1.0), "units": "-", "description": "Fraction to lipids from biomass decay"},
    "f_xI_xb": {"default": 0.1, "range": (0.0, 1.0), "units": "-", "description": "Fraction to particulate inerts from biomass decay"},

    # Lipid hydrolysis
    "f_fa_li": {"default": 0.95, "range": (0.0, 1.0), "units": "-", "description": "Fatty acids from lipid hydrolysis"},

    # Sugar uptake fractions
    "f_bu_su": {"default": 0.1328, "range": (0.0, 1.0), "units": "-", "description": "Butyrate from sugar uptake"},
    "f_pro_su": {"default": 0.2691, "range": (0.0, 1.0), "units": "-", "description": "Propionate from sugar uptake"},
    "f_ac_su": {"default": 0.4076, "range": (0.0, 1.0), "units": "-", "description": "Acetate from sugar uptake"},

    # Amino acid uptake fractions
    "f_va_aa": {"default": 0.23, "range": (0.0, 1.0), "units": "-", "description": "Valerate from amino acid uptake"},
    "f_bu_aa": {"default": 0.26, "range": (0.0, 1.0), "units": "-", "description": "Butyrate from amino acid uptake"},
    "f_pro_aa": {"default": 0.05, "range": (0.0, 1.0), "units": "-", "description": "Propionate from amino acid uptake"},
    "f_ac_aa": {"default": 0.4, "range": (0.0, 1.0), "units": "-", "description": "Acetate from amino acid uptake"},

    # VFA uptake fractions
    "f_ac_fa": {"default": 0.7, "range": (0.0, 1.0), "units": "-", "description": "Acetate from fatty acid uptake"},
    "f_pro_va": {"default": 0.54, "range": (0.0, 1.0), "units": "-", "description": "Propionate from valerate uptake"},
    "f_ac_va": {"default": 0.31, "range": (0.0, 1.0), "units": "-", "description": "Acetate from valerate uptake"},
    "f_ac_bu": {"default": 0.8, "range": (0.0, 1.0), "units": "-", "description": "Acetate from butyrate uptake"},
    "f_ac_pro": {"default": 0.57, "range": (0.0, 1.0), "units": "-", "description": "Acetate from propionate uptake"},

    # PHA uptake fractions
    "f_va_pha": {"default": 0.1, "range": (0.0, 1.0), "units": "-", "description": "Valerate from PHA uptake"},
    "f_bu_pha": {"default": 0.1, "range": (0.0, 1.0), "units": "-", "description": "Butyrate from PHA uptake"},
    "f_pro_pha": {"default": 0.4, "range": (0.0, 1.0), "units": "-", "description": "Propionate from PHA uptake"},

    # ==================== YIELD COEFFICIENTS ====================
    # ADM1 biomass yields
    "Y_su": {"default": 0.1, "range": (0.01, 0.3), "units": "kg COD/kg COD", "description": "Yield of sugar degraders"},
    "Y_aa": {"default": 0.08, "range": (0.01, 0.2), "units": "kg COD/kg COD", "description": "Yield of amino acid degraders"},
    "Y_fa": {"default": 0.06, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of LCFA degraders"},
    "Y_c4": {"default": 0.06, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of C4 (butyrate/valerate) degraders"},
    "Y_pro": {"default": 0.04, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of propionate degraders"},
    "Y_ac": {"default": 0.05, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of acetate degraders (acetoclastic methanogens)"},
    "Y_h2": {"default": 0.06, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of hydrogen degraders (hydrogenotrophic methanogens)"},

    # PAO/SRB yields
    "Y_PO4": {"default": 0.4, "range": (0.1, 0.8), "units": "kg P/kg COD", "description": "Yield of phosphate from PAO"},
    "Y_hSRB": {"default": 0.05, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of hydrogen-utilizing SRB"},
    "Y_aSRB": {"default": 0.05, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of acetate-utilizing SRB"},
    "Y_pSRB": {"default": 0.04, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of propionate-utilizing SRB"},
    "Y_c4SRB": {"default": 0.06, "range": (0.01, 0.15), "units": "kg COD/kg COD", "description": "Yield of C4-utilizing SRB"},

    # ==================== HYDROLYSIS RATE CONSTANTS ====================
    "q_ch_hyd": {"default": 10.0, "range": (1.0, 50.0), "units": "d^-1", "description": "Carbohydrate hydrolysis rate"},
    "q_pr_hyd": {"default": 10.0, "range": (1.0, 50.0), "units": "d^-1", "description": "Protein hydrolysis rate"},
    "q_li_hyd": {"default": 10.0, "range": (1.0, 50.0), "units": "d^-1", "description": "Lipid hydrolysis rate"},

    # ==================== UPTAKE RATE CONSTANTS ====================
    "k_su": {"default": 30.0, "range": (5.0, 100.0), "units": "d^-1", "description": "Sugar maximum uptake rate"},
    "k_aa": {"default": 50.0, "range": (10.0, 150.0), "units": "d^-1", "description": "Amino acid maximum uptake rate"},
    "k_fa": {"default": 6.0, "range": (1.0, 30.0), "units": "d^-1", "description": "LCFA maximum uptake rate"},
    "k_c4": {"default": 20.0, "range": (5.0, 80.0), "units": "d^-1", "description": "Butyrate/valerate maximum uptake rate"},
    "k_pro": {"default": 13.0, "range": (3.0, 50.0), "units": "d^-1", "description": "Propionate maximum uptake rate"},
    "k_ac": {"default": 8.0, "range": (2.0, 30.0), "units": "d^-1", "description": "Acetate maximum uptake rate"},
    "k_h2": {"default": 35.0, "range": (10.0, 100.0), "units": "d^-1", "description": "Hydrogen maximum uptake rate"},

    # ==================== HALF-SATURATION CONSTANTS ====================
    "K_su": {"default": 0.5, "range": (0.05, 2.0), "units": "kg COD/m3", "description": "Sugar half-saturation constant"},
    "K_aa": {"default": 0.3, "range": (0.05, 1.0), "units": "kg COD/m3", "description": "Amino acid half-saturation constant"},
    "K_fa": {"default": 0.4, "range": (0.05, 1.5), "units": "kg COD/m3", "description": "LCFA half-saturation constant"},
    "K_c4": {"default": 0.2, "range": (0.02, 0.8), "units": "kg COD/m3", "description": "Butyrate/valerate half-saturation constant"},
    "K_pro": {"default": 0.1, "range": (0.01, 0.5), "units": "kg COD/m3", "description": "Propionate half-saturation constant"},
    "K_ac": {"default": 0.15, "range": (0.02, 0.5), "units": "kg COD/m3", "description": "Acetate half-saturation constant"},
    "K_h2": {"default": 7e-6, "range": (1e-7, 1e-4), "units": "kg COD/m3", "description": "Hydrogen half-saturation constant"},

    # ==================== DECAY RATE CONSTANTS ====================
    "b_su": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Sugar degrader decay rate"},
    "b_aa": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Amino acid degrader decay rate"},
    "b_fa": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "LCFA degrader decay rate"},
    "b_c4": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "C4 degrader decay rate"},
    "b_pro": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Propionate degrader decay rate"},
    "b_ac": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Acetate degrader decay rate"},
    "b_h2": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Hydrogen degrader decay rate"},

    # ==================== PAO/EBPR PARAMETERS ====================
    "q_pha": {"default": 3.0, "range": (0.5, 10.0), "units": "d^-1", "description": "PHA storage rate"},
    "b_pao": {"default": 0.2, "range": (0.05, 0.5), "units": "d^-1", "description": "PAO decay rate"},
    "b_pp": {"default": 0.2, "range": (0.05, 0.5), "units": "d^-1", "description": "Polyphosphate decay rate"},
    "b_pha": {"default": 0.2, "range": (0.05, 0.5), "units": "d^-1", "description": "PHA decay rate"},
    "K_A": {"default": 4e-3, "range": (1e-4, 0.1), "units": "kg COD/m3", "description": "Acetate half-saturation for PAO"},
    "K_PP": {"default": 0.01, "range": (0.001, 0.1), "units": "kg P/m3", "description": "Polyphosphate half-saturation"},

    # ==================== SRB PARAMETERS (SULFATE REDUCTION) ====================
    "k_hSRB": {"default": 41.125, "range": (10.0, 100.0), "units": "d^-1", "description": "H2-utilizing SRB maximum uptake rate"},
    "k_aSRB": {"default": 10.0, "range": (2.0, 50.0), "units": "d^-1", "description": "Acetate-utilizing SRB maximum uptake rate"},
    "k_pSRB": {"default": 16.25, "range": (3.0, 50.0), "units": "d^-1", "description": "Propionate-utilizing SRB maximum uptake rate"},
    "k_c4SRB": {"default": 23.0, "range": (5.0, 80.0), "units": "d^-1", "description": "C4-utilizing SRB maximum uptake rate"},

    "b_hSRB": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "H2-utilizing SRB decay rate"},
    "b_aSRB": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Acetate-utilizing SRB decay rate"},
    "b_pSRB": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "Propionate-utilizing SRB decay rate"},
    "b_c4SRB": {"default": 0.02, "range": (0.005, 0.1), "units": "d^-1", "description": "C4-utilizing SRB decay rate"},

    "K_hSRB": {"default": 5.96e-6, "range": (1e-7, 1e-4), "units": "kg COD/m3", "description": "H2 half-saturation for SRB"},
    "K_aSRB": {"default": 0.176, "range": (0.02, 0.8), "units": "kg COD/m3", "description": "Acetate half-saturation for SRB"},
    "K_pSRB": {"default": 0.088, "range": (0.01, 0.4), "units": "kg COD/m3", "description": "Propionate half-saturation for SRB"},
    "K_c4SRB": {"default": 0.1739, "range": (0.02, 0.8), "units": "kg COD/m3", "description": "C4 half-saturation for SRB"},

    # Sulfate half-saturation (uses S_mw factor)
    "K_so4_hSRB": {"default": 1.04e-4 * S_mw, "range": (1e-4, 0.1), "units": "kg S/m3", "description": "Sulfate half-saturation for H2 SRB"},
    "K_so4_aSRB": {"default": 2e-4 * S_mw, "range": (1e-4, 0.1), "units": "kg S/m3", "description": "Sulfate half-saturation for acetate SRB"},
    "K_so4_pSRB": {"default": 2e-4 * S_mw, "range": (1e-4, 0.1), "units": "kg S/m3", "description": "Sulfate half-saturation for propionate SRB"},
    "K_so4_c4SRB": {"default": 2e-4 * S_mw, "range": (1e-4, 0.1), "units": "kg S/m3", "description": "Sulfate half-saturation for C4 SRB"},

    # ==================== IRON REDUCTION PARAMETERS ====================
    "k_Fe3t2_h2": {"default": 1e9 / Fe_mw, "range": (1e6, 1e12), "units": "d^-1", "description": "Fe3+ reduction by H2 rate"},
    "k_Fe3t2_is": {"default": 1e9 / Fe_mw, "range": (1e6, 1e12), "units": "d^-1", "description": "Fe3+ reduction by H2S rate"},

    # ==================== HFO (HYDROUS FERRIC OXIDE) PARAMETERS ====================
    "q_aging_H": {"default": 450.0, "range": (10.0, 1000.0), "units": "d^-1", "description": "HFO-H aging rate"},
    "q_aging_L": {"default": 0.1, "range": (0.01, 10.0), "units": "d^-1", "description": "HFO-L aging rate"},
    "q_Pcoprec": {"default": 360.0, "range": (10.0, 1000.0), "units": "d^-1", "description": "P co-precipitation rate"},
    "q_Pbinding": {"default": 0.3, "range": (0.01, 10.0), "units": "d^-1", "description": "P binding rate"},
    "q_diss_H": {"default": 36.0, "range": (1.0, 100.0), "units": "d^-1", "description": "HFO-HP dissolution rate"},
    "q_diss_L": {"default": 36.0, "range": (1.0, 100.0), "units": "d^-1", "description": "HFO-LP dissolution rate"},
    "K_Pbind": {"default": 37.2, "range": (1.0, 100.0), "units": "kg P/m3", "description": "P binding half-saturation"},
    "K_Pdiss": {"default": 0.93, "range": (0.01, 10.0), "units": "kg P/m3", "description": "P dissolution half-saturation"},

    # ==================== INHIBITION PARAMETERS ====================
    # H2 inhibition
    "KI_h2_fa": {"default": 5e-6, "range": (1e-7, 1e-4), "units": "kg COD/m3", "description": "H2 inhibition on LCFA degraders"},
    "KI_h2_c4": {"default": 1e-5, "range": (1e-7, 1e-4), "units": "kg COD/m3", "description": "H2 inhibition on C4 degraders"},
    "KI_h2_pro": {"default": 3.5e-6, "range": (1e-7, 1e-4), "units": "kg COD/m3", "description": "H2 inhibition on propionate degraders"},

    # NH3 inhibition
    "KI_nh3": {"default": 1.8e-3, "range": (1e-4, 0.01), "units": "M", "description": "Free ammonia inhibition constant"},

    # Nutrient requirements
    "KS_IN": {"default": 1e-4, "range": (1e-6, 1e-3), "units": "M", "description": "Nitrogen substrate requirement"},
    "KS_IP": {"default": 2e-5, "range": (1e-7, 1e-4), "units": "M", "description": "Phosphorus substrate requirement"},

    # H2S inhibition (methanogens)
    "KI_h2s_c4": {"default": 0.481, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on C4 degraders"},
    "KI_h2s_pro": {"default": 0.481, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on propionate degraders"},
    "KI_h2s_ac": {"default": 0.460, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on acetoclastic methanogens"},
    "KI_h2s_h2": {"default": 0.400, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on hydrogenotrophic methanogens"},

    # H2S inhibition (SRB)
    "KI_h2s_c4SRB": {"default": 0.520, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on C4 SRB"},
    "KI_h2s_pSRB": {"default": 0.520, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on propionate SRB"},
    "KI_h2s_aSRB": {"default": 0.499, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on acetate SRB"},
    "KI_h2s_hSRB": {"default": 0.499, "range": (0.1, 1.0), "units": "kg S/m3", "description": "H2S inhibition on H2 SRB"},

    # ==================== pH LIMITS ====================
    # pH inhibition ranges (lower_limit, upper_limit)
    "pH_limits_aa": {"default": (4, 5.5), "range": None, "units": "-", "description": "pH limits for amino acid degraders"},
    "pH_limits_ac": {"default": (6, 7), "range": None, "units": "-", "description": "pH limits for acetoclastic methanogens"},
    "pH_limits_h2": {"default": (5, 6), "range": None, "units": "-", "description": "pH limits for hydrogenotrophic methanogens"},
    "pH_limits_aa_SRB": {"default": (6, 7), "range": None, "units": "-", "description": "pH limits for amino acid SRB"},
    "pH_limits_ac_SRB": {"default": (6, 7), "range": None, "units": "-", "description": "pH limits for acetate SRB"},
    "pH_limits_h2_SRB": {"default": (5, 6), "range": None, "units": "-", "description": "pH limits for H2 SRB"},

    # ==================== MASS TRANSFER ====================
    "kLa": {"default": 200.0, "range": (10.0, 1000.0), "units": "d^-1", "description": "Gas-liquid mass transfer coefficient"},

    # ==================== ACID-BASE EQUILIBRIUM ====================
    # pKa values (list format)
    "pKa_base": {"default": [14, 9.25, 6.35, 4.76, 4.88, 4.82, 4.86], "range": None, "units": "-",
                 "description": "pKa values at reference T [Kw, NH4, CO2, HAc, HPro, HBu, HVa]"},
    "Ka_dH": {"default": [55900, 51965, 7646, 0, 0, 0, 0], "range": None, "units": "J/mol",
              "description": "Enthalpy of dissociation for Ka temperature correction"},

    # ==================== CRYSTALLIZATION ====================
    "k_cryst": {"default": [0.35, 1e-3, 3.0, 1e-3, 2.0, 0.76, 5.0, 1e-3, 1e-3, 1e-3, 1e2, 1e-3, 1e-3],
                "range": None, "units": "d^-1", "description": "Crystallization rate constants for 13 minerals"},
    "n_cryst": {"default": [2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2],
                "range": None, "units": "-", "description": "Crystallization reaction orders for 13 minerals"},
}


def validate_kinetic_params(
    params: Dict[str, Any],
    schema: Dict[str, Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validate and merge kinetic parameters with defaults.

    Args:
        params: User-provided kinetic parameters
        schema: Parameter schema (defaults to MADM1_KINETIC_SCHEMA)

    Returns:
        Tuple of (validated_params, warnings)
        - validated_params: Merged dict with defaults filled in
        - warnings: List of warning messages for out-of-range values
    """
    if schema is None:
        schema = MADM1_KINETIC_SCHEMA

    validated = {}
    warnings = []

    # Start with all defaults
    for param_name, spec in schema.items():
        validated[param_name] = spec["default"]

    # Override with user-provided values
    for param_name, value in params.items():
        if param_name not in schema:
            warnings.append(f"Unknown kinetic parameter '{param_name}' - passing through to model")
            validated[param_name] = value
            continue

        spec = schema[param_name]

        # Type checking for scalar parameters
        if spec["range"] is not None:
            if not isinstance(value, (int, float)):
                warnings.append(f"Parameter '{param_name}' should be numeric, got {type(value).__name__}")
                continue

            # Range checking
            min_val, max_val = spec["range"]
            if value < min_val or value > max_val:
                warnings.append(
                    f"Parameter '{param_name}' = {value} is outside typical range "
                    f"[{min_val}, {max_val}] {spec['units']}"
                )

        validated[param_name] = value

    return validated, warnings


def get_kinetic_param_docs() -> str:
    """
    Generate documentation string for all kinetic parameters.

    Returns:
        Formatted documentation string
    """
    lines = ["mADM1 Kinetic Parameters", "=" * 50, ""]

    # Group by category (based on naming conventions)
    categories = {
        "Stoichiometric Fractions": ["f_"],
        "Yield Coefficients": ["Y_"],
        "Hydrolysis Rates": ["q_ch_hyd", "q_pr_hyd", "q_li_hyd"],
        "Uptake Rate Constants": ["k_su", "k_aa", "k_fa", "k_c4", "k_pro", "k_ac", "k_h2",
                                  "k_hSRB", "k_aSRB", "k_pSRB", "k_c4SRB", "k_Fe"],
        "Half-Saturation Constants": ["K_su", "K_aa", "K_fa", "K_c4", "K_pro", "K_ac", "K_h2",
                                      "K_hSRB", "K_aSRB", "K_pSRB", "K_c4SRB", "K_so4", "K_A", "K_PP", "K_P"],
        "Decay Rates": ["b_"],
        "PAO/EBPR": ["q_pha", "b_pao", "b_pp", "b_pha"],
        "HFO": ["q_aging", "q_Pcoprec", "q_Pbinding", "q_diss"],
        "Inhibition": ["KI_", "KS_"],
        "pH Limits": ["pH_limits"],
        "Mass Transfer": ["kLa"],
        "Equilibrium": ["pKa", "Ka_dH"],
        "Crystallization": ["k_cryst", "n_cryst"],
    }

    used_params = set()

    for cat_name, prefixes in categories.items():
        cat_params = []
        for param_name, spec in MADM1_KINETIC_SCHEMA.items():
            if any(param_name.startswith(p) or param_name == p for p in prefixes):
                if param_name not in used_params:
                    cat_params.append((param_name, spec))
                    used_params.add(param_name)

        if cat_params:
            lines.append(f"\n{cat_name}")
            lines.append("-" * len(cat_name))
            for param_name, spec in sorted(cat_params):
                default = spec["default"]
                if isinstance(default, (list, tuple)):
                    default_str = str(default)[:40] + "..." if len(str(default)) > 40 else str(default)
                else:
                    default_str = f"{default}"
                lines.append(f"  {param_name}: {default_str} {spec['units']}")
                lines.append(f"      {spec['description']}")

    # Any remaining parameters
    remaining = [(p, s) for p, s in MADM1_KINETIC_SCHEMA.items() if p not in used_params]
    if remaining:
        lines.append("\nOther Parameters")
        lines.append("-" * 15)
        for param_name, spec in remaining:
            lines.append(f"  {param_name}: {spec['default']} {spec['units']}")
            lines.append(f"      {spec['description']}")

    return "\n".join(lines)


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'MADM1_KINETIC_SCHEMA',
    'validate_kinetic_params',
    'get_kinetic_param_docs',
]
