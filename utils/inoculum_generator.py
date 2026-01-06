"""
Dynamic Inoculum Generator for CSTR Anaerobic Digesters

This module generates realistic inoculum states by scaling biomass components
based on feedstock COD. Solves the "pickling" problem where reactors initialized
with feedstock composition (~1 kg/m³ biomass) experience catastrophic failure due
to extreme F/M overload (100-500× normal loading).

Theory:
- Healthy digesters: 10-15 kg/m³ biomass, F/M ratio 0.1-0.5 kg COD/kg biomass/day
- Feedstock alone: ~1 kg/m³ biomass, F/M ratio 50 kg COD/kg biomass/day (overload!)
- Solution: Scale biomass to 15-25% of feedstock COD before starting CSTR

Author: Generated via Claude Code
Date: 2025-10-29
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Biomass components in mADM1 (all X_* except inerts)
BIOMASS_COMPONENTS = [
    "X_su",      # Sugar degraders
    "X_aa",      # Amino acid degraders
    "X_fa",      # LCFA degraders
    "X_c4",      # Valerate and butyrate degraders
    "X_pro",     # Propionate degraders
    "X_ac",      # Acetoclastic methanogens (CRITICAL)
    "X_h2",      # Hydrogenotrophic methanogens (CRITICAL)
    "X_PAO",     # Phosphorus accumulating organisms
    "X_hSRB",    # H2-utilizing sulfate reducing bacteria
    "X_aSRB",    # Acetate-utilizing SRB
    "X_pSRB",    # Propionate-utilizing SRB
    "X_c4SRB",   # C4-utilizing SRB
]

# Organic substrate components (for COD calculation)
ORGANIC_SUBSTRATES = [
    "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac",  # Solubles
    "X_ch", "X_pr", "X_li",  # Particulate organics
]

# NOTE: In mADM1/QSDsan, ALL state variables (both S_* and X_*) are already
# expressed in COD units (kg COD/m³), NOT VSS. Therefore, we do NOT apply
# a COD conversion factor when calculating COD from biomass components.
# See: docs/architecture/MADM1_CONTEXT_FOR_CODEX.md:142


def calculate_cod_from_organics(state: Dict[str, float]) -> float:
    """
    Calculate total COD from organic components in ADM1 state.

    NOTE: In mADM1/QSDsan, ALL components are already expressed in COD units (kg COD/m³).
    No conversion factor is needed.

    Args:
        state: ADM1 state dictionary (component name -> concentration in kg COD/m³)

    Returns:
        Total organic COD in kg COD/m³
    """
    cod_kg_m3 = 0.0

    # Sum all organic substrates (already in COD units)
    for component in ORGANIC_SUBSTRATES:
        if component in state:
            cod_kg_m3 += state[component]

    # Add biomass COD (already in COD units - NO conversion needed)
    for component in BIOMASS_COMPONENTS:
        if component in state:
            cod_kg_m3 += state[component]

    # Add particulate inerts as COD
    if "X_I" in state:
        cod_kg_m3 += state["X_I"]

    # Add soluble inerts as COD (affects F/M ratio)
    if "S_I" in state:
        cod_kg_m3 += state["S_I"]

    return cod_kg_m3


def calculate_current_biomass_cod(state: Dict[str, float]) -> float:
    """
    Calculate current biomass COD from all X_* components.

    NOTE: In mADM1/QSDsan, biomass components are already in COD units.

    Args:
        state: ADM1 state dictionary (component name -> concentration in kg COD/m³)

    Returns:
        Total biomass COD in kg COD/m³
    """
    biomass_cod_kg_m3 = 0.0

    for component in BIOMASS_COMPONENTS:
        if component in state:
            biomass_cod_kg_m3 += state[component]

    return biomass_cod_kg_m3


def generate_inoculum_state(
    feedstock_state: Dict[str, float],
    target_biomass_cod_ratio: float = 0.20,
    methanogen_boost_factor: float = 6.0,
    target_alkalinity_meq_l: float = 200.0
) -> Dict[str, float]:
    """
    Generate realistic inoculum by scaling biomass components based on feedstock COD.

    This function solves the "pickling" problem where CSTR reactors initialized with
    feedstock composition experience catastrophic failure due to insufficient biomass
    and alkalinity buffering.

    Algorithm:
    1. Calculate total feedstock COD from organics
    2. Calculate current biomass COD
    3. Determine target biomass COD = feedstock_COD × target_ratio
    4. Scale all biomass components proportionally
    5. Apply additional boost to methanogens (X_ac, X_h2)
    6. Boost S_IC (alkalinity) to handle VFA generation
    7. Keep substrates (S_*), inerts (X_I), and most ions unchanged

    Args:
        feedstock_state: ADM1 state from Codex (feedstock composition, ~1 kg/m³ biomass)
        target_biomass_cod_ratio: Target biomass as fraction of total COD (default: 0.20)
                                  Range: 0.15-0.30 (15-30% of COD as biomass)
        methanogen_boost_factor: Additional scaling for X_ac and X_h2 (default: 6.0×)
                                Prevents immediate VFA accumulation during startup
        target_alkalinity_meq_l: Target alkalinity in meq/L (default: 200)
                                Provides buffering for VFA generation
                                Typical: 3-4 meq/L per kg COD/m³

    Returns:
        Modified ADM1 state with scaled biomass and boosted alkalinity

    Example:
        >>> feedstock = load_adm1_state("adm1_state.json")  # 50 kg COD/m³, 1 kg biomass/m³
        >>> inoculum = generate_inoculum_state(feedstock, target_ratio=0.20)
        >>> # Result: 50 kg COD/m³, 10.6 kg biomass/m³ (10.6× scale-up)
        >>> # X_ac, X_h2: 63.6× scale-up (6× methanogen boost)
        >>> # S_IC: 2.4 kg/m³ (200 meq/L alkalinity)

    Notes:
        - For CSTR systems, no SRT adjustment needed (steady-state determined by HRT)
        - Typical scale-up: 10-15× increase in biomass concentration
        - X_ac, X_h2 (methanogens) scaled 6× more to prevent immediate pickling
        - S_IC boosted to provide adequate buffering during startup
        - This matches real-world startup: 30-50% inoculum + 50-70% substrate
    """
    # Validate inputs
    if not 0.10 <= target_biomass_cod_ratio <= 0.35:
        logger.warning(
            f"target_biomass_cod_ratio={target_biomass_cod_ratio:.2f} is outside "
            f"recommended range [0.15, 0.30]. Proceeding anyway."
        )

    # Calculate feedstock COD
    feedstock_cod_kg_m3 = calculate_cod_from_organics(feedstock_state)

    # Guard against zero or near-zero COD feedstocks
    if feedstock_cod_kg_m3 < 0.01:
        logger.error(f"Feedstock COD is near zero ({feedstock_cod_kg_m3:.4f} kg/m³) - cannot generate inoculum.")
        raise ValueError(
            f"Feedstock COD too low ({feedstock_cod_kg_m3:.4f} kg/m³). "
            f"Check that feedstock_state contains organic substrates."
        )

    logger.info(f"Feedstock COD: {feedstock_cod_kg_m3:.1f} kg/m³")

    # Calculate current biomass COD
    current_biomass_cod_kg_m3 = calculate_current_biomass_cod(feedstock_state)
    biomass_percent = (current_biomass_cod_kg_m3/feedstock_cod_kg_m3*100) if feedstock_cod_kg_m3 > 0.01 else 0.0
    logger.info(f"Current biomass COD: {current_biomass_cod_kg_m3:.1f} kg/m³ "
                f"({biomass_percent:.1f}% of total COD)")

    # Calculate target biomass COD
    target_biomass_cod_kg_m3 = feedstock_cod_kg_m3 * target_biomass_cod_ratio
    logger.info(f"Target biomass COD: {target_biomass_cod_kg_m3:.1f} kg/m³ "
                f"({target_biomass_cod_ratio*100:.1f}% of total COD)")

    # Calculate scaling factor
    if current_biomass_cod_kg_m3 < 0.01:
        logger.error("Current biomass COD is near zero - cannot scale. "
                     "Feedstock state may be invalid.")
        raise ValueError(
            f"Current biomass COD too low ({current_biomass_cod_kg_m3:.4f} kg/m³). "
            f"Check that feedstock_state contains biomass components."
        )

    scaling_factor = target_biomass_cod_kg_m3 / current_biomass_cod_kg_m3
    logger.info(f"Biomass scaling factor: {scaling_factor:.2f}× increase")

    # Create inoculum state by scaling biomass
    inoculum_state = feedstock_state.copy()

    for component in BIOMASS_COMPONENTS:
        if component in inoculum_state:
            original_value = inoculum_state[component]

            # Apply base scaling to all biomass
            scaled_value = original_value * scaling_factor

            # Apply additional boost to methanogens (X_ac, X_h2)
            if component in ["X_ac", "X_h2"]:
                scaled_value *= methanogen_boost_factor
                logger.debug(
                    f"  {component}: {original_value:.4f} → {scaled_value:.4f} kg/m³ "
                    f"({scaling_factor * methanogen_boost_factor:.2f}× with methanogen boost)"
                )
            else:
                logger.debug(
                    f"  {component}: {original_value:.4f} → {scaled_value:.4f} kg/m³ "
                    f"({scaling_factor:.2f}×)"
                )

            inoculum_state[component] = scaled_value

    # Boost S_IC (inorganic carbon / alkalinity) to provide buffering
    original_s_ic = inoculum_state.get("S_IC", 0.90)
    # Convert alkalinity from meq/L to kg/m³ of inorganic carbon
    # 1 meq/L alkalinity ≈ 0.012 kg/m³ S_IC (as carbon)
    target_s_ic_kg_m3 = target_alkalinity_meq_l * 0.012
    inoculum_state["S_IC"] = target_s_ic_kg_m3

    # Balance alkalinity boost with S_Na to maintain electroneutrality
    # When we add HCO3- (alkalinity), we must add equivalent cations (Na+)
    # Alkalinity increase in meq/L = cation increase in meq/L
    original_alkalinity_meq_l = original_s_ic / 0.012
    alkalinity_increase_meq_l = target_alkalinity_meq_l - original_alkalinity_meq_l

    # Convert meq/L Na+ to kg/m³ Na
    # 1 meq/L Na+ = 0.023 g/L Na = 0.000023 kg/L = 0.023 kg/m³
    na_increase_kg_m3 = alkalinity_increase_meq_l * 0.023
    original_s_na = inoculum_state.get("S_Na", 0.20)
    inoculum_state["S_Na"] = original_s_na + na_increase_kg_m3

    logger.info("="*80)
    logger.info("ALKALINITY BOOST & ION BALANCE")
    logger.info(f"  S_IC (inorganic carbon): {original_s_ic:.2f} → {target_s_ic_kg_m3:.2f} kg/m³")
    logger.info(f"  Equivalent alkalinity: {original_alkalinity_meq_l:.0f} → {target_alkalinity_meq_l:.0f} meq/L")
    alkalinity_boost_factor = (target_s_ic_kg_m3/original_s_ic) if original_s_ic > 0.01 else float('inf')
    logger.info(f"  Alkalinity boost: {alkalinity_boost_factor:.1f}× increase" if alkalinity_boost_factor < 1000 else f"  Alkalinity boost: Initial alkalinity near zero, set to {target_s_ic_kg_m3:.2f} kg/m³")
    logger.info(f"  S_Na (sodium cation): {original_s_na:.3f} → {inoculum_state['S_Na']:.3f} kg/m³")
    logger.info(f"  Na+ added: {na_increase_kg_m3:.3f} kg/m³ ({alkalinity_increase_meq_l:.0f} meq/L)")
    logger.info(f"  Ion balance: HCO3- increase = Na+ increase (electroneutrality maintained)")
    logger.info("="*80)

    # Log critical methanogen components
    logger.info(f"Critical methanogen concentrations (with {methanogen_boost_factor:.1f}× boost):")
    logger.info(f"  X_ac (acetoclastic): {inoculum_state.get('X_ac', 0):.2f} kg/m³ "
                f"(was {feedstock_state.get('X_ac', 0):.2f}, {inoculum_state.get('X_ac', 0)/feedstock_state.get('X_ac', 1):.1f}×)")
    logger.info(f"  X_h2 (hydrogenotrophic): {inoculum_state.get('X_h2', 0):.2f} kg/m³ "
                f"(was {feedstock_state.get('X_h2', 0):.2f}, {inoculum_state.get('X_h2', 0)/feedstock_state.get('X_h2', 1):.1f}×)")

    # Calculate total biomass COD for reporting
    # NOTE: In mADM1, biomass components are in COD units, NOT VSS
    # To convert to VSS: divide by 1.42 g COD/g VSS
    total_biomass_cod = sum([
        inoculum_state.get(comp, 0) for comp in BIOMASS_COMPONENTS
    ])
    total_biomass_vss_kg_m3 = total_biomass_cod / 1.42  # Convert COD to VSS
    logger.info(f"Total inoculum biomass: {total_biomass_cod:.2f} kg COD/m³ "
                f"= {total_biomass_vss_kg_m3:.2f} kg VSS/m³ "
                f"= {total_biomass_vss_kg_m3 * 1000:.0f} mg VSS/L")

    # Verify target was achieved
    final_biomass_cod = calculate_current_biomass_cod(inoculum_state)
    logger.info(f"Verification: Final biomass COD = {final_biomass_cod:.1f} kg/m³ "
                f"(target was {target_biomass_cod_kg_m3:.1f} kg/m³)")

    return inoculum_state


if __name__ == "__main__":
    # Simple test with example feedstock
    import json

    logging.basicConfig(level=logging.INFO,
                       format='%(levelname)s: %(message)s')

    # Load actual feedstock state if available
    try:
        with open("adm1_state.json", "r") as f:
            feedstock = json.load(f)

        print("\n" + "="*80)
        print("INOCULUM GENERATOR TEST")
        print("="*80)

        # Generate inoculum
        inoculum = generate_inoculum_state(feedstock, target_biomass_cod_ratio=0.20)

        print("\n" + "="*80)
        print("SUCCESS - Inoculum generated")
        print("="*80)
        print(f"Total components: {len(inoculum)}")
        print(f"Biomass components scaled: {len(BIOMASS_COMPONENTS)}")

    except FileNotFoundError:
        print("ERROR: adm1_state.json not found. Run from project root.")
