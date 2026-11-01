"""C-5: close the AD -> dewatering -> stripper -> BNR recycle as one consistent state.

**Topology** (and it matters, because each unit is a different nitrogen exit)::

    feed ->[AD]-> dewatering -> stripper -> split -> forward -> [aerobic] -> effluent
              ^      |cake N      |NH3 overhead  |R                |NO3 (not TKN)
              |______|____________|______________| recycle         |WAS N

* **Dewatering** removes captured particulate organic N with the cake. What reaches the
  stripper is centrate: soluble organic N plus the (1 - capture) solids carryover.
* **The stripper** removes TAN only -- and, co-incidentally but consequentially, ~95% of
  the inorganic carbon.
* **The aerobic polish** ammonifies the biodegradable organic N and nitrifies the
  result. Nitrate is not TKN, so conversion IS compliance under a TKN basis.

**A correction this revision carries.** An earlier version flagged the aerobic-INFLUENT
organic N against the EFFLUENT TKN basis -- the wrong compliance point, caught on review:
most of that organic N is centrate solids carryover plus biodegradable soluble N, and
ammonifying-then-nitrifying it is precisely the aerobic step's designed function. What
actually reaches the effluent as TKN is the residual TAN, the **refractory soluble
organic N** (rDON -- Maillard-type products of cooked, rendered, high-protein feeds,
which neither the stripper nor the basin touches), and the **nitrogen content of the
effluent solids** (~12.4% of VSS). Those two terms set the TKN margin, and both are
measured-feed-characterisation variables, not design constants.

**Alkalinity, corrected twice on review.** An earlier revision computed available
alkalinity as residual TIC at 1 meq/mmol -- which conflates TIC with ANC and understated
the supply ~2-3x. The charge balance says otherwise: CO2 is a neutral species, so
**CO2 stripping does not change total alkalinity** -- the ANC redistributes into
carbonate/hydroxide at elevated pH (conservative-expression framework: Wolf-Gladrow et
al., doi 10.1016/j.marchem.2007.01.006). What consumes alkalinity is **NH3 stripping**,
at 1 eq/mol N = 3.57 g CaCO3/g N (observed in ammonia-recovery practice: Vanotti et
al., doi 10.1016/j.watres.2017.01.045). The fork's own stripper confirms it: the solved
bottoms sit at pH ~10-11 carrying ~666 mg CaCO3/L of carbonate/water ANC (~948 counting
acetate/phosphate) despite 95% TIC removal.

So this ledger takes **total alkalinity as its own conserved input** -- measured bottoms
TA, or feed TA debited 3.57 g CaCO3 per g N stripped -- and REFUSES to run without one:
TIC is not alkalinity, and deriving TA from an assumed ion basis is how the earlier
error happened. Demand credits ammonification (+1 eq/mol: org-N -> NH4+ + OH-), so
organic N that ammonifies-then-nitrifies costs net 3.57, not 7.14.

**Low inorganic CARBON remains a separate question from alkalinity.** A stream can carry
ample ANC as hydroxide while having little CO2/HCO3- for autotroph growth (AOB
limitation below ~3 mmol C/L: Guisasola et al., doi 10.1016/j.watres.2006.10.030; the
fork's ``K_HCO3_AOB`` exists for this, default off). Heterotrophic respiration and
aeration ingassing resupply some -- a treatability measurement, not a design allowance.

The aerobic basin sits DOWNSTREAM of the recycle draw, so alkali dosed there does not
accumulate around the loop -- unlike caustic dosed at the stripper, which does."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

class RecycleClosureError(RuntimeError):
    """The loop did not close. Never downgrade to a warning: an unconverged recycle is
    the one case where every unit looks individually fine and the plant does not."""
I_N_BIOMASS = 0.124
ALK_NITRIFICATION = 7.14
ALK_DENITRIFICATION = 3.57

@dataclass
class LoopResult:
    converged: bool
    iterations: int
    recycle_ratio_nominal: float
    recycle_ratio_actual: float
    digester_tan_mgN_L: float
    digester_free_nh3_mgN_L: float
    cake_n_kg_d: float
    centrate_organic_n_mgN_L: float
    stripper_feed_tan_mgN_L: float
    stripper_bottoms_tan_mgN_L: float
    stripper_overhead_kg_N_d: float
    aerobic_tan_in_mgN_L: float
    aerobic_organic_n_in_mgN_L: float
    aerobic_tic_in_mgC_L: float
    effluent_tan_mgN_L: float
    effluent_rdon_mgN_L: float
    effluent_tss_n_mgN_L: float
    effluent_tkn_mgN_L: float
    effluent_no3_mgN_L: float
    alkalinity_available_mgCaCO3_L: float
    alkalinity_demand_mgCaCO3_L: float
    alkalinity_margin_mgCaCO3_L: float
    alkalinity_deficit_mgCaCO3_L: float
    sodium_mg_L: float
    amplification_actual: float
    amplification_naive: float
    caveats: List[str] = field(default_factory=list)
    closure: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)

def solve_loop(*, feed_flow_m3_d: float, feed_tan_mgN_L: float, feed_organic_n_mgN_L: float, stripper_removal: float, recycle_fraction: float, ammonification_in_digester: float=0.85, digester_tic_mgC_L: float=1500.0, digester_pH: float=7.6, digester_T_C: float=38.0, particulate_fraction_organic_n: float=0.7, solids_capture: float=0.95, steam_condensate_fraction: float=0.019, stripper_tic_removal: float=0.95, stripper_bottoms_total_alkalinity_mgCaCO3_L: Optional[float]=None, stripper_feed_total_alkalinity_mgCaCO3_L: Optional[float]=None, stripper_non_nh3_alkalinity_change_mgCaCO3_L: Optional[float]=None, naoh_dose_mol_m3: float=0.0, feed_sodium_mg_L: float=0.0, refractory_soluble_fraction: float=0.25, aerobic_ammonification: float=0.9, effluent_tan_mgN_L: float=2.0, effluent_tss_mg_L: float=30.0, denitrified_fraction: float=0.0, tkn_limit_mgN_L: Optional[float]=None, tss_limit_mg_L: Optional[float]=None, max_iter: int=200, tol: float=1e-10) -> LoopResult:
    """Solve the closed loop and evaluate compliance at the point it is measured.

    ``stripper_removal`` is meant to come from
    :meth:`core.ammonia_stripper.AmmoniaSteamStripper.solve_profile`, not a constant.
    ``refractory_soluble_fraction`` (the rDON share of soluble organic N) and
    ``solids_capture`` are the two numbers that end up setting the TKN margin, and both
    belong to the treatability program, not to this module -- the defaults are priors.

    Simplifications, stated: cake liquor volume is neglected (centrate flow = AD-out
    flow); TAN leaves only via the overhead (cake moisture TAN neglected); net biomass N
    incorporation in the digester is folded into ``ammonification_in_digester < 1``; the
    recycle's decarbonised water lowering AD buffering is flagged, not resolved -- that
    needs the digester model.
    """
    if not 0.0 <= recycle_fraction < 1.0:
        raise ValueError('recycle_fraction must be in [0, 1)')
    if not 0.0 <= stripper_removal <= 1.0:
        raise ValueError('stripper_removal must be in [0, 1]')
    have_bottoms_ta = stripper_bottoms_total_alkalinity_mgCaCO3_L is not None
    have_feed_ta = stripper_feed_total_alkalinity_mgCaCO3_L is not None
    if have_bottoms_ta == have_feed_ta:
        raise ValueError('provide exactly one of stripper_bottoms_total_alkalinity_mgCaCO3_L (measured/modelled) or stripper_feed_total_alkalinity_mgCaCO3_L. TIC is not alkalinity, and this ledger no longer guesses TA from an ion basis -- that is precisely how the retracted 616 mg/L deficit happened.')
    if have_feed_ta and stripper_non_nh3_alkalinity_change_mgCaCO3_L is None:
        raise ValueError('feed-TA mode requires stripper_non_nh3_alkalinity_change_mgCaCO3_L; pass 0.0 only when acid dosing, precipitation and bleed have been excluded.')
    caveats: List[str] = []
    R = float(recycle_fraction)
    eta = float(stripper_removal)
    pf = float(particulate_fraction_organic_n)
    cap = float(solids_capture)
    tan_r = org_r = na_r = 0.0
    recycle_flow = 0.0
    converged = False
    iterations = 0
    for iterations in range(1, max_iter + 1):
        total_in_flow = feed_flow_m3_d + recycle_flow
        n_in_tan = feed_flow_m3_d * feed_tan_mgN_L + recycle_flow * tan_r
        n_in_org = feed_flow_m3_d * feed_organic_n_mgN_L + recycle_flow * org_r
        ammonified = n_in_org * ammonification_in_digester
        dig_tan_load = n_in_tan + ammonified
        dig_org_load = n_in_org - ammonified
        dig_tan = dig_tan_load / total_in_flow
        cake_n_load = dig_org_load * pf * cap
        centrate_org_load = dig_org_load - cake_n_load
        centrate_flow = total_in_flow
        bottoms_flow = centrate_flow * (1.0 + steam_condensate_fraction)
        overhead_n_load = dig_tan_load * eta
        bottoms_tan_load = dig_tan_load - overhead_n_load
        bottoms_tan = bottoms_tan_load / bottoms_flow
        bottoms_org = centrate_org_load / bottoms_flow
        na_added = naoh_dose_mol_m3 * 22.99
        na_load = feed_flow_m3_d * feed_sodium_mg_L + recycle_flow * na_r + centrate_flow * na_added
        na_bottoms = na_load / bottoms_flow
        new_recycle_flow = R * bottoms_flow
        delta = max(abs(bottoms_tan - tan_r), abs(bottoms_org - org_r), abs(na_bottoms - na_r), abs(new_recycle_flow - recycle_flow))
        tan_r, org_r, na_r = (bottoms_tan, bottoms_org, na_bottoms)
        recycle_flow = new_recycle_flow
        if delta < tol * max(1.0, feed_tan_mgN_L):
            converged = True
            break
    if not converged:
        raise RecycleClosureError(f'recycle loop did not close in {max_iter} iterations (last change {delta:.3e}). At R={R:.2f} and eta={eta:.3f} the loop gain is {R * (1 - eta):.3f}; a gain at or above 1 has no steady state, which is itself the finding.')
    forward_flow = bottoms_flow - recycle_flow
    R_actual = recycle_flow / bottoms_flow if bottoms_flow else 0.0
    soluble_share = (1.0 - pf) / max(1.0 - pf + (1.0 - cap) * pf, 1e-12)
    rdon = refractory_soluble_fraction * soluble_share * bottoms_org
    biodeg_org = bottoms_org - rdon
    aerobic_ammonified = aerobic_ammonification * biodeg_org
    was_n = biodeg_org - aerobic_ammonified
    effluent_tss_n = I_N_BIOMASS * effluent_tss_mg_L
    nitrifiable = max(bottoms_tan + aerobic_ammonified - effluent_tan_mgN_L - effluent_tss_n, 0.0)
    denitrified = denitrified_fraction * nitrifiable
    effluent_no3 = nitrifiable - denitrified
    nitrified_from_tan = min(max(bottoms_tan - effluent_tan_mgN_L - effluent_tss_n, 0.0), nitrifiable)
    nitrified_from_ammonified = nitrifiable - nitrified_from_tan
    effluent_tkn = effluent_tan_mgN_L + rdon + effluent_tss_n
    tic_forward = digester_tic_mgC_L * (total_in_flow / bottoms_flow) * (1.0 - stripper_tic_removal)
    if have_bottoms_ta:
        alk_available = float(stripper_bottoms_total_alkalinity_mgCaCO3_L)
    else:
        alk_load = centrate_flow * float(stripper_feed_total_alkalinity_mgCaCO3_L)
        alk_load -= overhead_n_load * (50.0 / 14.0067)
        alk_load += centrate_flow * naoh_dose_mol_m3 * 50.0
        alk_load += centrate_flow * float(stripper_non_nh3_alkalinity_change_mgCaCO3_L)
        if alk_load < -1e-09:
            raise RecycleClosureError(f'derived stripper-bottoms alkalinity load is negative ({alk_load / centrate_flow:.0f} mg CaCO3/L equivalent): the supplied feed TA cannot support the nitrogen being stripped. Either the feed TA is understated, or the TAN entered as a strong-acid salt (NH4Cl-type) carrying no alkalinity -- measure, do not assume.')
        alk_available = max(alk_load, 0.0) / bottoms_flow
    alk_demand = ALK_NITRIFICATION * nitrified_from_tan + ALK_DENITRIFICATION * nitrified_from_ammonified - ALK_DENITRIFICATION * denitrified
    alk_margin = alk_available - alk_demand
    alk_deficit = max(-alk_margin, 0.0)
    free_nh3 = free_ammonia_mgN_L(dig_tan, digester_pH, digester_T_C + 273.15)
    naive = 1.0 / (1.0 - R * (1.0 - eta)) if R * (1.0 - eta) < 1.0 else float('inf')
    actual = dig_tan / feed_tan_mgN_L if feed_tan_mgN_L else float('nan')
    if rdon + effluent_tss_n > 0:
        caveats.append(f'tkn_margin_is_rdon_plus_solids: effluent TKN {effluent_tkn:.1f} mg N/L = TAN {effluent_tan_mgN_L:.1f} + rDON {rdon:.1f} + solids-N {effluent_tss_n:.1f}. The stripper percentage barely moves this; the rDON fraction and effluent solids capture do. Both are treatability measurements (DON before/after aerobic; capture at design polymer dose), not design constants.')
    if tss_limit_mg_L is not None and tkn_limit_mgN_L is not None:
        n_at_tss_limit = I_N_BIOMASS * tss_limit_mg_L
        if n_at_tss_limit > tkn_limit_mgN_L:
            caveats.append(f'tkn_binds_before_tss: biomass solids at the TSS allowance ({tss_limit_mg_L:.0f} mg/L x {I_N_BIOMASS:.3f} gN/g = {n_at_tss_limit:.0f} mg N/L) exceed the whole TKN basis ({tkn_limit_mgN_L:.0f}). The TSS headroom is not usable; effluent solids are governed by the nitrogen limit.')
    if tkn_limit_mgN_L is not None and effluent_tkn > tkn_limit_mgN_L:
        caveats.append(f'tkn_over_limit: {effluent_tkn:.1f} mg N/L against {tkn_limit_mgN_L:.0f}.')
    if alk_deficit > 0:
        caveats.append(f'alkalinity_deficit: nitrifying {nitrifiable:.0f} mg N/L needs {alk_demand:.0f} mg CaCO3/L against {alk_available:.0f} of {('measured bottoms' if have_bottoms_ta else 'feed-derived')} total alkalinity. Deficit {alk_deficit:.0f} mg CaCO3/L must be dosed in the basin -- downstream of the recycle draw, so it does not accumulate, but it is OPEX and it is Na or lime to the POTW.')
    else:
        caveats.append(f'alkalinity_residual: {alk_margin:.0f} mg CaCO3/L of margin at the basin, from total alkalinity surviving the stripper (CO2 stripping does not consume ANC; NH3 stripping costs 3.57 g CaCO3/g N). Theoretical until pilot-bottoms titrated TA confirms it -- keep standby dosing.')
    caveats.append(f'low_tic_aob_carbon: bottoms TIC ~{tic_forward:.0f} mg C/L after {100 * stripper_tic_removal:.0f}% stripping. SEPARATE from alkalinity: AOB need CO2/HCO3- as their growth substrate, with limitation reported below ~36 mg C/L (Guisasola 2007). Evaluate K_HCO3_AOB in ModifiedASM2d (default off); heterotrophic respiration and aeration ingassing resupply some carbon -- measure TIC and AOB response in the aerobic treatability test.')
    if naoh_dose_mol_m3 > 0:
        caveats.append(f'sodium_accumulates: {na_r:.0f} mg Na/L at steady state from a {naoh_dose_mol_m3:.0f} mol/m3 stripper dose ({na_added:.0f} mg/L per pass). Stripper-side sodium is upstream of the recycle draw and has no exit; it is cation-inhibitory to the methanogens.')
    caveats.append('recycle_decarbonisation_unresolved: the loop returns low-TIC water with lower carbonate buffer INTENSITY -- but neutral CO2 stripping does not remove total alkalinity, so the water is not ANC-free. The pH-rise/free-ammonia coupling in the digester needs a conserved TIC/TA/strong-ion state in mADM1, which this screening ledger does not carry.')
    if abs(actual - naive) / max(naive, 1e-09) > 0.05:
        caveats.append(f'screening_formula_diverges: closed-loop amplification {actual:.3f} vs 1/(1-R(1-eta)) = {naive:.3f}. The differences are the cake exit, the steam water, and where the organic N is accounted.')
    n_in = feed_flow_m3_d * (feed_tan_mgN_L + feed_organic_n_mgN_L)
    forward_n = forward_flow * (bottoms_tan + bottoms_org)
    n_out = cake_n_load + overhead_n_load + forward_n
    aerobic_residual = bottoms_tan + bottoms_org - (effluent_tkn + effluent_no3 + denitrified + was_n)
    closure = {'feed_kg_N_d': n_in / 1000.0, 'cake_kg_N_d': cake_n_load / 1000.0, 'overhead_kg_N_d': overhead_n_load / 1000.0, 'forward_kg_N_d': forward_n / 1000.0, 'aerobic_internal_residual_mgN_L': float(aerobic_residual), 'relative_error': float((n_in - n_out) / n_in) if n_in else 0.0}
    return LoopResult(converged=converged, iterations=iterations, recycle_ratio_nominal=R, recycle_ratio_actual=R_actual, digester_tan_mgN_L=dig_tan, digester_free_nh3_mgN_L=free_nh3, cake_n_kg_d=cake_n_load / 1000.0, centrate_organic_n_mgN_L=centrate_org_load / centrate_flow, stripper_feed_tan_mgN_L=dig_tan, stripper_bottoms_tan_mgN_L=bottoms_tan, stripper_overhead_kg_N_d=overhead_n_load / 1000.0, aerobic_tan_in_mgN_L=bottoms_tan, aerobic_organic_n_in_mgN_L=bottoms_org, aerobic_tic_in_mgC_L=tic_forward, effluent_tan_mgN_L=effluent_tan_mgN_L, effluent_rdon_mgN_L=rdon, effluent_tss_n_mgN_L=effluent_tss_n, effluent_tkn_mgN_L=effluent_tkn, effluent_no3_mgN_L=effluent_no3, alkalinity_available_mgCaCO3_L=alk_available, alkalinity_demand_mgCaCO3_L=alk_demand, alkalinity_margin_mgCaCO3_L=alk_margin, alkalinity_deficit_mgCaCO3_L=alk_deficit, sodium_mg_L=na_r, amplification_actual=actual, amplification_naive=naive, caveats=caveats, closure=closure)

def free_ammonia_mgN_L(tan_mgN_L: float, pH: float, T_K: float) -> float:
    """Free NH3 as N, on the same Emerson pKa the rest of the fork uses.

    Free ammonia, not TAN, is what inhibits methanogens -- and it is exponential in pH,
    roughly 2x per 0.3 units, which is why pH control dominates as a lever.
    """
    pKa = 0.09018 + 2729.92 / float(T_K)
    return float(tan_mgN_L) / (1.0 + 10 ** (pKa - float(pH)))
