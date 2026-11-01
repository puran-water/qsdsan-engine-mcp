"""
QSDsan simulation module for mADM1 (Modified ADM1, full Flores-Alsina P/S/Fe + Na/trace-element model).

This module provides simulation functions for anaerobic digester design using
QSDsan's ModifiedADM1 process model with complete sulfur/phosphorus/iron extensions.

Key features:
- full mADM1 system (full Flores-Alsina model)
- Built-in SRB processes (X_hSRB, X_aSRB, X_pSRB, X_c4SRB)
- Built-in H2S inhibition on methanogens
- EBPR extension (PAO, polyphosphate)
- Metal/mineral precipitation (Fe, Al, Ca, Mg)
- Proper dynamic simulation setup with set_dynamic_tracker
- Early-stop convergence checking for pseudo-steady-state
- Dual-HRT validation for design robustness

Based on QSDsan's published mADM1 implementation.
"""

import numpy as np
from qsdsan_engine_mcp.core.dynamic_solver_policy import madm1_bdf_options
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()
from qsdsan import unit_operations as su, WasteStream, System
from qsdsan.utils import ospath
import logging

# ROOT CAUSE FIX: Use ModifiedADM1 directly instead of extend_adm1_with_sulfate_and_inhibition
# ModifiedADM1 already contains complete sulfur biology (SRB processes, H2S inhibition, etc.)
from qsdsan_engine_mcp.models.madm1 import ModifiedADM1, create_madm1_cmps

# CODEX FIX: Use custom reactor that supports mADM1's 4 biogas species (CH4, CO2, H2, H2S)
from qsdsan_engine_mcp.models.reactors import AnaerobicCSTRmADM1

# Import inoculum generator for CSTR startup
from qsdsan_engine_mcp.utils.inoculum_generator import generate_inoculum_state

# Phase 10: Native pH calculation using our custom pcm() function
# Replaces external calculate_ph_and_alkalinity_fixed module
def effective_pcm_params(params, cmps, T_op):
    """Build the params dict for a pcm()/alkalinity extraction consistent with the dynamics.

    When ``params`` is the reactor's live ``rate_function._params`` it carries the model_reduction flags
    (e.g. the reduced observer's ``exclude_sip_from_charge``) + the exact Ka, so the exported chemistry
    matches diagnostics.pH_model. It is shallow-copied and the Ka cache dropped so pcm's temperature
    recompute cannot leak into the reactor's live dict. When ``params`` is None a minimal full-model
    default is reconstructed (back-compat for callers with no reactor handle)."""
    if params is not None:
        p = dict(params)
        p['T_op'] = T_op
        p['components'] = cmps
        p.pop('_Ka_T_op', None)
        return p
    pKa_base = [14.0, 9.25, 6.35, 4.76, 4.88, 4.82, 4.86]  # 25 C: [Kw, NH4, CO2, HAc, HPro, HBu, HVa]
    return {
        'Ka_base': np.array([10 ** (-pKa) for pKa in pKa_base]),
        'Ka_dH': np.array([55900, 51965, 7646, 0, 0, 0, 0]),  # J/mol
        'T_base': 298.15,
        'T_op': T_op,
        'components': cmps,
    }


def update_ph_and_alkalinity(stream, T_op=None, params=None):
    """
    Calculate pH and alkalinity using our custom pcm() function from mADM1.

    This uses the production-grade PCM equilibrium model with:
    - Temperature-corrected Ka values (Van't Hoff equation)
    - Full acid-base equilibrium (VFAs, ammonia, carbonate, sulfide)
    - Charge balance using Brent's method root-finding
    - Explicit handling of Fe3+/Al3+ for dosing scenarios (custom extension)

    Parameters
    ----------
    stream : WasteStream
        Stream to update pH and alkalinity
    T_op : float, optional
        Operating temperature in K. If None, uses stream temperature.
    params : dict, optional
        The reactor's live ``rate_function._params``. When provided, the exported pH/alkalinity are
        computed with the SAME chemistry the dynamics used — critically its ``model_reduction`` flags
        (e.g. the reduced observer's ``exclude_sip_from_charge``), so the reported pH matches
        diagnostics.pH_model instead of reverting to full-model phosphate charge. When None, a minimal
        default (full-model) params dict is reconstructed for back-compat callers with no reactor handle.

    Returns
    -------
    WasteStream
        Updated stream with _pH and _SAlk properties set
    """
    try:
        from qsdsan_engine_mcp.models.madm1 import pcm, total_alkalinity_meq_L

        cmps = stream.components
        n_cmps = len(cmps)

        # Build state array from stream concentrations (kg/m3)
        # pcm() expects concentrations in kg/m3
        if stream.F_vol > 0:
            state_arr = np.array([stream.imass[cmp.ID] / stream.F_vol for cmp in cmps])
        else:
            # Zero flow — the stream has no liquid to speciate. Emit NaN (H11 fail-closed) so the
            # physicality gate REJECTS it, rather than a plausible pH 7.0 / 2.5 meq/L that laundered a
            # diverged/empty state into a "healthy" reading.
            if hasattr(stream, '_pH'):
                stream._pH = float('nan')
            if hasattr(stream, '_SAlk'):
                stream._SAlk = float('nan')
            return stream

        # Get temperature
        if T_op is None:
            T_op = stream.T if hasattr(stream, 'T') else 308.15

        params = effective_pcm_params(params, cmps, T_op)

        # Calculate pH using pcm()
        pH, nh3, co2, activities = pcm(state_arr, params)

        # Set pH
        if hasattr(stream, '_pH'):
            stream._pH = float(pH)

        # LIKE-BASIS total alkalinity (codex 2026-07-11 H5): sum HCO3- + NH3 + HPO4-- + VFA- + HS- + water
        # on one molar-equivalent basis, NOT a bicarbonate-only surrogate (which is not comparable to a lab
        # total-alkalinity titration). Downstream multiplies _SAlk (meq/L) by 50.04 mg/meq -> mg/L CaCO3.
        if hasattr(stream, '_SAlk'):
            stream._SAlk = float(total_alkalinity_meq_L(state_arr, params, pH=pH))

        return stream

    except Exception as e:
        # H11 fail-closed: a charge-solver / speciation blow-up must NOT be laundered into a plausible
        # pH 7.0 / 2.5 meq/L. Emit NaN so the physicality gate rejects the row and the caller falls back
        # to a measured-only reading instead of presenting a diverged state as healthy.
        logging.warning(f"pH calculation failed: {e}. Emitting NaN (fail-closed).")
        if hasattr(stream, '_pH'):
            stream._pH = float('nan')
        if hasattr(stream, '_SAlk'):
            stream._SAlk = float('nan')
        return stream

# Constants
from chemicals.elements import molecular_weight as get_mw
C_mw = get_mw({'C': 1})
N_mw = get_mw({'N': 1})

logger = logging.getLogger(__name__)

# Module-level component set (initialized once)
import threading as _threading
_MADM1_CMPS = None
_THERMO_SET = False
_MADM1_THERMO = None               # the Thermo object we last set — identity guard for the cheap re-set
_THERMO_LOCK = _threading.Lock()   # H9: make the global-thermo check-and-set atomic across threads

# Expected mADM1 component order (critical for kinetics)
# Based on qsdsan_madm1.py:212-223 and verified against legacy mADM1 results.
# Canonical mADM1 component order — SINGLE SOURCE OF TRUTH in core.components.catalog
# (catalog -> model_registry -> junction_components all derive from the same tuple; the
# create_madm1_cmps() builder is cross-checked against it in verify_component_ordering below).
from qsdsan_engine_mcp.core.components.catalog import get_component_ids as _get_component_ids, TRACE_METAL_IDS

#: In-reactor phases that must NEVER default into the influent: the 13 mineral
#: precipitates plus the 7 HFO pools (fix/madm1-mineral-subsystem). Nucleation
#: from a supersaturated liquid is the rate law's job (mineral_seed_kg_m3).
MINERAL_AND_HFO_PHASE_IDS = frozenset((
    'X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP', 'X_struv',
    'X_newb', 'X_magn', 'X_kstruv', 'X_FeS', 'X_Mackinawite',
    'X_Fe3PO42', 'X_Siderite', 'X_AlPO4',
    'X_HFO_H', 'X_HFO_L', 'X_HFO_old', 'X_HFO_HP', 'X_HFO_LP',
    'X_HFO_HP_old', 'X_HFO_LP_old',
))
EXPECTED_COMPONENT_ORDER = list(_get_component_ids('mADM1'))

#: The opt-in organic-sulfur extension's components (X_Sorg). A MISSING one defaults to 0 in the influent, never the
#: 1e-6 floor: absence means no organic sulfur, and a floor would fabricate sulfur in every feed.
from puran_mcp_support.schema.madm1_organic_sulfur import MADM1_ORGANIC_SULFUR_EXTENSION as _ORGANIC_SULFUR_EXTENSION
ORGANIC_SULFUR_COMPONENT_IDS = frozenset(_ORGANIC_SULFUR_EXTENSION.component_ids)
_MADM1_CMPS_ORGANIC_S = None       # the opt-in organic-sulfur component set (built only when asked for)
_MADM1_ORGANIC_S_THERMO = None     # its Thermo, the identity guard of ensure_madm1_thermo(organic_sulfur=True)

#: The opt-in micro-aeration extension's liquid component (S_O2). A MISSING one defaults to 0 in the influent, never
#: the 1e-6 floor: absence means no dissolved oxygen, and a floor would dose oxygen into every feed (and break the
#: zero-dose identity with the organic-sulfur model).
from puran_mcp_support.schema.madm1_microaeration import MADM1_MICROAERATION_EXTENSION as _MICROAERATION_EXTENSION
MICROAERATION_COMPONENT_IDS = frozenset(_MICROAERATION_EXTENSION.component_ids)
_MADM1_CMPS_MICROAERATION = None       # the opt-in micro-aeration component set (organic S + S_O2; built when asked)
_MADM1_MICROAERATION_THERMO = None     # its Thermo, the identity guard of ensure_madm1_thermo(microaeration=True)


def _require_organic_sulfur_for_microaeration(organic_sulfur, microaeration):
    """The micro-aeration set stacks on the organic-sulfur set (the kernel's statement): asking for it alone is
    refused rather than implied, here as in ``create_madm1_cmps`` and ``ModifiedADM1``."""
    if microaeration and not organic_sulfur:
        raise ValueError("the micro-aeration component set stacks on the organic-sulfur set: ask for both "
                         "(organic_sulfur=True, microaeration=True)")


def verify_component_ordering(cmps, organic_sulfur=False, microaeration=False):
    """
    Verify that mADM1 component ordering is correct.

    CRITICAL: mADM1 kinetics depend on specific state vector positions.
    If ordering is wrong, the tracked COD proxy (S_ac) can map to the wrong
    state variable, causing oscillations while biomass appears stable.

    Parameters
    ----------
    cmps : CompiledComponents
        QSDsan component set to verify

    Returns
    -------
    bool
        True if ordering is correct

    Raises
    ------
    RuntimeError
        If component ordering is incorrect
    """
    if organic_sulfur or microaeration:
        from qsdsan_engine_mcp.core.components.catalog import madm1_component_ids
        # the catalog refuses micro-aeration without organic sulfur (the extension stacks on it)
        expected_order = list(madm1_component_ids(organic_sulfur=bool(organic_sulfur),
                                                  microaeration=bool(microaeration)))
    else:
        expected_order = EXPECTED_COMPONENT_ORDER
    if len(cmps) != len(expected_order):
        raise RuntimeError(
            f"Expected {len(expected_order)} mADM1 components, got {len(cmps)}")

    # Verify every canonical position
    for idx, expected_id in enumerate(expected_order):
        actual_id = cmps.IDs[idx]
        if actual_id != expected_id:
            raise RuntimeError(
                f"mADM1 component ordering broken at position {idx}: "
                f"found '{actual_id}', expected '{expected_id}'. "
                f"This will cause state variable misalignment and simulation failures."
            )

    logger.info(f"mADM1 component ordering verified: all {len(expected_order)} components in correct positions")
    return True


def _organic_sulfur_validated_components():
    """The opt-in organic-sulfur component set (create_madm1_cmps(organic_sulfur=True)), built and order-verified
    once and cached BESIDE the default set. It does not set the global thermo: a build that asks for organic sulfur
    activates it through ``ensure_madm1_thermo(organic_sulfur=True)`` at its entry, as the default build does."""
    global _MADM1_CMPS_ORGANIC_S
    if _MADM1_CMPS_ORGANIC_S is None:
        with _THERMO_LOCK:
            if _MADM1_CMPS_ORGANIC_S is None:
                cmps = create_madm1_cmps(set_thermo=False, organic_sulfur=True)
                verify_component_ordering(cmps, organic_sulfur=True)
                _MADM1_CMPS_ORGANIC_S = cmps
    return _MADM1_CMPS_ORGANIC_S


def _microaeration_validated_components():
    """The opt-in micro-aeration component set (create_madm1_cmps(organic_sulfur=True, microaeration=True)), built
    and order-verified once and cached BESIDE the default and organic-sulfur sets. It does not set the global
    thermo: a build activates it through ``ensure_madm1_thermo(organic_sulfur=True, microaeration=True)``."""
    global _MADM1_CMPS_MICROAERATION
    if _MADM1_CMPS_MICROAERATION is None:
        with _THERMO_LOCK:
            if _MADM1_CMPS_MICROAERATION is None:
                cmps = create_madm1_cmps(set_thermo=False, organic_sulfur=True, microaeration=True)
                verify_component_ordering(cmps, organic_sulfur=True, microaeration=True)
                _MADM1_CMPS_MICROAERATION = cmps
    return _MADM1_CMPS_MICROAERATION


def get_validated_components(organic_sulfur=False, microaeration=False):
    """
    Get validated mADM1 component set (creates once, validates, sets thermo).

    This ensures component ordering is correct before any simulation runs.
    Component ordering mismatches cause COD tracking to reference wrong state
    variables, leading to oscillations and convergence failures.

    ``organic_sulfur=True`` returns the opt-in organic-sulfur set instead (68 + X_Sorg before H2O); it is cached
    separately and leaves the global thermo to ``ensure_madm1_thermo(organic_sulfur=True)``. With
    ``microaeration=True`` as well it returns the opt-in micro-aeration set (+ S_O2 after X_Sorg), cached the same
    way; micro-aeration without organic sulfur is refused.

    Returns
    -------
    CompiledComponents
        Validated mADM1 component set
    """
    global _MADM1_CMPS, _THERMO_SET, _MADM1_THERMO
    import qsdsan as qs

    _require_organic_sulfur_for_microaeration(organic_sulfur, microaeration)
    if microaeration:
        return _microaeration_validated_components()
    if organic_sulfur:
        return _organic_sulfur_validated_components()

    if _MADM1_CMPS is None:
        # H9: atomic first-build + first thermo-set under the lock; two threads racing the first call
        # must not both build, nor see _MADM1_CMPS before its thermo is set.
        with _THERMO_LOCK:
            if _MADM1_CMPS is None:                     # double-checked under the lock
                logger.info("Initializing mADM1 component set (first call)...")
                cmps = create_madm1_cmps(set_thermo=False)
                verify_component_ordering(cmps)          # verify ordering BEFORE setting thermo
                qs.set_thermo(cmps)                      # set once
                _MADM1_CMPS = cmps                       # publish only AFTER thermo is set (no torn read)
                _THERMO_SET = True
                try:
                    _MADM1_THERMO = qs.get_thermo()
                except Exception:
                    _MADM1_THERMO = None
                logger.info("mADM1 thermo set successfully")

    return _MADM1_CMPS


def ensure_madm1_thermo(organic_sulfur=False, microaeration=False):
    """H9 (codex P0-2): (re)establish mADM1 as the process-global ThermoSteam BEFORE a build constructs
    streams — another model family (ASM / build_system) may have changed the single global thermo slot
    since we cached the components. Call this ONCE at each twin build entry (NOT per component fetch:
    set_thermo rebuilds the property package and is far too costly to call in the hot path). The identity
    guard makes the common case (thermo already ours) ~free.

    ``organic_sulfur=True`` establishes the opt-in organic-sulfur set instead, with its own identity guard; a later
    default build re-establishes the default set through the guard below (the two never share a slot).
    ``microaeration=True`` (with organic sulfur) establishes the opt-in micro-aeration set the same way."""
    import qsdsan as qs
    global _MADM1_THERMO, _MADM1_ORGANIC_S_THERMO, _MADM1_MICROAERATION_THERMO
    if microaeration:
        # get_validated_components refuses micro-aeration without organic sulfur
        cmps = get_validated_components(organic_sulfur=organic_sulfur, microaeration=True)
        try:
            _current = qs.get_thermo()
        except Exception:
            _current = None
        if _MADM1_MICROAERATION_THERMO is None or _current is not _MADM1_MICROAERATION_THERMO:
            qs.set_thermo(cmps)
            try:
                _MADM1_MICROAERATION_THERMO = qs.get_thermo()
            except Exception:
                _MADM1_MICROAERATION_THERMO = None
        return cmps
    if organic_sulfur:
        cmps = get_validated_components(organic_sulfur=True)
        try:
            _current = qs.get_thermo()
        except Exception:
            _current = None
        if _MADM1_ORGANIC_S_THERMO is None or _current is not _MADM1_ORGANIC_S_THERMO:
            qs.set_thermo(cmps)
            try:
                _MADM1_ORGANIC_S_THERMO = qs.get_thermo()
            except Exception:
                _MADM1_ORGANIC_S_THERMO = None
        return cmps
    cmps = get_validated_components()
    try:
        _current = qs.get_thermo()
    except Exception:
        _current = None
    if _MADM1_THERMO is None or _current is not _MADM1_THERMO:
        qs.set_thermo(cmps)
        try:
            _MADM1_THERMO = qs.get_thermo()
        except Exception:
            _MADM1_THERMO = None
    return cmps


def normalize_madm1_influent_conc(state, cmps):
    'Canonical mADM1 influent normalization (kg/m3), SHARED by cold creation\n    (``create_influent_stream_sulfur``) and the warm-start update (``liquid_digester_twin._set_influent_conc``)\n    so the commission feed and a resumed feed are IDENTICAL representations for the same state.'
    def _to_number(val):
        if isinstance(val, (list, tuple)) and val:
            try:
                return float(val[0])
            except Exception:
                return None
        if isinstance(val, dict):
            for key in ('value', 'val', 'amount'):
                if key in val:
                    try:
                        return float(val[key])
                    except Exception:
                        return None
            return None
        try:
            return float(val)
        except Exception:
            return None

    out = {}
    for cid in cmps.IDs:
        if cid == 'H2O':
            continue
        default = 0.0 if (cid in TRACE_METAL_IDS
                          or cid in MINERAL_AND_HFO_PHASE_IDS
                          or cid in ORGANIC_SULFUR_COMPONENT_IDS
                          or cid in MICROAERATION_COMPONENT_IDS) else 1e-6
        if state is None or cid not in state:
            out[cid] = default
            continue
        raw = state[cid]
        if isinstance(raw, (list, tuple)) and len(raw) >= 2 and isinstance(raw[1], str):
            num = _to_number(raw)
            if num is not None and raw[1].strip().lower() == 'kmol/m3':
                num = num * cmps[cid].chem_MW                 # kmol/m3 -> kg/m3 via component MW
        else:
            num = _to_number(raw)
        if num is None:
            out[cid] = default
        elif not np.isfinite(num) or num < 0.0:
            logger.warning("normalize_madm1_influent_conc: clamped non-physical %s=%r -> 0.0", cid, raw)
            out[cid] = 0.0
        else:
            out[cid] = num
    return out


def create_influent_stream_sulfur(Q, Temp, adm1_state_62, organic_sulfur=False, microaeration=False):
    """
    Create influent WasteStream from a (sparse or full) mADM1 component state.

    Parameters
    ----------
    Q : float
        Flow rate in m3/d
    Temp : float
        Temperature in K
    adm1_state_62 : dict
        Mapping of mADM1 component id -> concentration (kg/m3)
        Full mADM1 component set from Codex agent

    Returns
    -------
    WasteStream
        Influent stream with calculated pH and alkalinity

    Notes
    -----
    - Uses create_madm1_cmps() for the full mADM1 component set
    - Concentrations expected in kg/m3 (or kg COD/m3 for COD-measured components)
    - pH and alkalinity calculated based on acid-base equilibria
    - Codex agent generates disaggregated SRB biomass (X_hSRB, X_aSRB, X_pSRB, X_c4SRB)
    - ``organic_sulfur=True`` normalises against the opt-in organic-sulfur set (the caller has activated it
      with ``ensure_madm1_thermo(organic_sulfur=True)``), so a feed X_Sorg reaches the stream
    - ``microaeration=True`` (with organic sulfur) normalises against the opt-in micro-aeration set, so the
      stream carries S_O2 (0 unless the feed states dissolved oxygen)
    """
    try:
        # Use validated mADM1 components (the full canonical mADM1 set)
        # CRITICAL: Use get_validated_components() to ensure ordering is correct
        # and thermo is set consistently. Component ordering mismatches cause
        # state variable misalignment and convergence failures.
        madm1_cmps = get_validated_components(organic_sulfur=organic_sulfur, microaeration=microaeration)

        inf = WasteStream('Influent', T=Temp)

        # Component system is set via get_validated_components() which calls
        # qs.set_thermo(madm1_cmps) once on first call

        # Prepare concentrations (kg/m3) via the SHARED normalizer, so the commission feed and a
        # warm-start resume (liquid_digester_twin._set_influent_conc) are identical representations
        # for the same state (explicit 0.0 preserved; only a MISSING component defaults; kmol/m3
        # converted via MW; negatives / non-finite clamped to 0).
        concentrations = normalize_madm1_influent_conc(adm1_state_62, madm1_cmps)

        # Set flow by concentration
        inf.set_flow_by_concentration(
            Q,
            concentrations=concentrations,
            units=('m3/d', 'kg/m3')
        )

        # Calculate pH and alkalinity
        update_ph_and_alkalinity(inf)

        logger.info(f"Created influent stream: Q={Q} m3/d, T={Temp} K, pH={inf.pH:.2f}")

        return inf

    except Exception as e:
        raise RuntimeError(f"Error creating influent stream with sulfur: {e}")


def initialize_madm1_state(adm1_state_62, organic_sulfur=False, microaeration=False):
    """
    Ensure full mADM1 state has sensible defaults for sulfur species.

    CRITICAL: Starting with zero sulfate/SRB means inhibition logic stays dormant
    and SRB processes won't activate. This function ensures realistic initial values.

    Parameters
    ----------
    adm1_state_62 : dict
        Dictionary of component concentrations (kg/m3)

    Returns
    -------
    dict
        Validated state with sulfur species defaults applied

    Notes
    -----
    - S_SO4 default: 0.1 kg S/m3 = 100 mg S/L (typical wastewater)
    - S_IS default: 0.001 kg S/m3 = 1 mg S/L (low initial sulfide)
    - SRB biomass defaults (disaggregated):
      * X_hSRB: 0.005 kg COD/m3 = 5 mg COD/L (hydrogen-utilizing, dominant)
      * X_aSRB: 0.003 kg COD/m3 = 3 mg COD/L (acetate-utilizing)
      * X_pSRB: 0.001 kg COD/m3 = 1 mg COD/L (propionate-utilizing)
      * X_c4SRB: 0.001 kg COD/m3 = 1 mg COD/L (butyrate/valerate-utilizing)
      * Total: 0.01 kg COD/m3 = 10 mg COD/L (seed population)

    These defaults ensure:
    1. SRB processes can activate immediately with proper distribution
    2. H2S inhibition can be calculated
    3. Sulfur mass balance is meaningful
    """
    # CRITICAL: Use get_validated_components() to ensure consistent thermo/ordering
    # Creating new components with create_madm1_cmps() causes thermo flip-flopping
    # and state variable misalignment leading to convergence failures
    # (organic_sulfur=True aligns to the opt-in organic-sulfur set, so X_Sorg is kept, not dropped;
    # microaeration=True to the micro-aeration set, so S_O2 is kept and starts at 0 unless stated)
    madm1_cmps = get_validated_components(organic_sulfur=organic_sulfur, microaeration=microaeration)

    def _to_number(val):
        """Coerce input value to float; handle [value, unit, comment] lists."""
        if isinstance(val, (list, tuple)) and val:
            try:
                return float(val[0])
            except Exception:
                pass
        elif isinstance(val, dict):
            for key in ('value', 'val', 'amount'):
                if key in val:
                    try:
                        return float(val[key])
                    except Exception:
                        pass
        try:
            return float(val)
        except Exception:
            return None

    # Filter and align initialization against the mADM1 component set
    # This prevents KeyError when input state has extra components
    valid_ids = madm1_cmps.IDs
    init_conds = {}
    extras = []

    for comp_id in valid_ids:
        raw = adm1_state_62.get(comp_id)
        val = _to_number(raw) if raw is not None else 0.0
        init_conds[comp_id] = val

    # Log any components in input that aren't in our component set
    for comp_id in adm1_state_62:
        if comp_id not in valid_ids:
            extras.append(comp_id)
    if extras:
        logger.debug(f"Ignoring components not in mADM1 component set: {extras}")

    # Apply sulfur defaults (ensure SRB processes can activate)
    if init_conds.get('S_SO4', 0) < 1e-6:
        init_conds['S_SO4'] = 0.1  # 100 mg S/L
        logger.info("S_SO4 not specified or too low, using default: 0.1 kg S/m3 (100 mg S/L)")

    if init_conds.get('S_IS', 0) < 1e-9:
        init_conds['S_IS'] = 0.001  # 1 mg S/L
        logger.info("S_IS not specified or zero, using default: 0.001 kg S/m3 (1 mg S/L)")

    # mADM1 uses disaggregated SRB biomass instead of lumped X_SRB
    # Distribute seed population across all SRB types
    srb_seed_total = 0.01  # 10 mg COD/L total
    srb_components = {
        'X_hSRB': 0.005,  # Hydrogen-utilizing (dominant)
        'X_aSRB': 0.003,  # Acetate-utilizing
        'X_pSRB': 0.001,  # Propionate-utilizing
        'X_c4SRB': 0.001  # Butyrate/valerate-utilizing
    }

    needs_srb_default = False
    for srb_id, default_val in srb_components.items():
        if srb_id in valid_ids and init_conds.get(srb_id, 0) < 1e-6:
            init_conds[srb_id] = default_val
            needs_srb_default = True

    if needs_srb_default:
        logger.info(f"SRB biomass not specified, using defaults: X_hSRB=5, X_aSRB=3, X_pSRB=1, X_c4SRB=1 mg COD/L (total=10 mg COD/L)")

    return init_conds


def check_steady_state(eff, gas, window=5, tolerance=1e-3):
    """
    Check instantaneous pseudo-steady-state (absolute |dC/dt| test).

    Examines the last 'window' time points and computes the max |dC/dt| over a broad slow-response basis;
    converged when it is below the absolute tolerance. This is a LOOSE "am I close?" heuristic used by the
    open-ended settle loop and the legacy dual-SRT caller — the rigorous mature-commission gate is the
    two-snapshot output-plateau confirmation in ``run_simulation_shadow_confirm`` (biogas + COD + biomass).

    Parameters
    ----------
    eff : WasteStream
        Effluent stream with dynamic tracking enabled
    gas : WasteStream
        Biogas stream with dynamic tracking enabled
    window : int, optional
        Number of recent time points to check (default 5)
    tolerance : float, optional
        Absolute tolerance for |dC/dt| in kg/m3/d (default 1e-3). Must be finite and > 0 or the check
        fails closed.

    Returns
    -------
    bool
        True if the instantaneous state is (loosely) steady

    Notes
    -----
    - Basis: VFAs (S_va/S_bu/S_pro/S_ac), biomass guilds (X_su/X_aa/X_fa/X_c4/X_pro/X_ac/X_h2), inorganic
      C/N (S_IN/S_IC).
    - Record is mg/L; converted to kg/m3 (÷1000) to compare against the kg/m3/d tolerance.
    - Fails closed on a non-positive/non-finite tolerance or a non-finite slope (never a passing 0).
    """
    try:
        # Check if dynamic tracking is available
        if not hasattr(eff, 'scope') or not hasattr(eff.scope, 'record'):
            logger.warning("Effluent stream missing dynamic tracking data")
            return False

        # Get time series data (FIXED: use time_series not t_arr per QSDsan API)
        time_arr = eff.scope.time_series

        if len(time_arr) < window + 1:
            # Not enough data points yet
            return False

        # Get last 'window' + 1 points for numerical differentiation
        recent_indices = slice(-window-1, None)
        t_recent = time_arr[recent_indices]

        # Guard: a non-positive / non-finite tolerance must FAIL CLOSED (the old absolute test with
        # tolerance<=0 always returned False; preserve that).
        if not (np.isfinite(tolerance) and tolerance > 0):
            logger.warning(f"check_steady_state: invalid tolerance ({tolerance}) -> not converged")
            return False

        # Broaden the basis beyond S_ac/X_ac: a not-yet-settled state can be flat in acetate while a slower
        # syntroph VFA / biomass guild / DIC-TAN pool is still drifting. Max |dC/dt| over the VFAs +
        # acidogen/methanogen biomass + inorganic C/N pools. QSDsan records mg/L; the tolerance is kg/m3/d,
        # so convert (÷1000). This is the LOOSE settle heuristic (the rigorous gate is the shadow confirm).
        from scipy.ndimage import uniform_filter1d
        steady_ids = ("S_va", "S_bu", "S_pro", "S_ac", "S_IN", "S_IC",
                      "X_su", "X_aa", "X_fa", "X_c4", "X_pro", "X_ac", "X_h2")
        record = eff.scope.record
        max_slope = 0.0
        worst_id = None
        n_checked = 0
        for cid in steady_ids:
            try:
                idx = eff.components.index(cid)
            except ValueError:
                continue
            series = record[recent_indices, idx] / 1000.0   # mg/L -> kg/m3
            if len(series) >= 3:
                series = uniform_filter1d(series, size=3, mode='nearest')
            slope = float(np.max(np.abs(np.gradient(series, t_recent))))
            if not np.isfinite(slope):
                logger.warning(f"check_steady_state: non-finite slope for {cid} -> not converged")
                return False
            if slope > max_slope:
                max_slope, worst_id = slope, cid
            n_checked += 1

        if n_checked == 0:
            logger.warning("Required components for convergence check not found")
            return False

        converged = max_slope < tolerance
        logger.info(f"Convergence check: max|dC/dt|={max_slope:.6f} kg/m³/d (worst={worst_id}) over "
                    f"{n_checked} slow states, tolerance={tolerance} kg/m³/d -> "
                    f"{'converged' if converged else f'not yet ({max_slope/tolerance:.1f}x)'}")
        return converged

    except Exception as e:
        logger.warning(f"Error checking steady state: {e}")
        return False


def assert_solver_ok(sys, t_end, *, endpoint_atol_days=1e-3):
    """Fail-closed guard on the last BDF integration (codex 2026-07-30).

    biosteam's ``System.simulate`` stores the raw ``solve_ivp`` result on ``sys.scope.sol`` but NEVER
    inspects ``success``/``status`` (biosteam ``_system.py`` only *prints* the message when ``print_msg``
    is set) and unconditionally calls ``_write_state``. So a NON-CONVERGED solve (``status=-1``,
    ``success=False``, or one that stopped short of ``t_end``) silently "completes", persists a partial
    state, and the caller then advances logical time on a failed integration. Raise here so the caller's
    existing error handling (``run_simulation_to_steady_state``'s try/except -> RuntimeError, or the
    workflow's advance/commission -> measured-only fallback) fires instead of trusting garbage.

    STRICT (codex nit): called immediately after every BDF ``sys.simulate``, so a MISSING solver result
    (``sys.scope.sol is None``) is itself a failure — raise (do not silently pass). Only fires on a
    genuine failure: a normal successful BDF run has ``success=True``, ``status=0`` and ``t[-1] == t_end``
    (t_eval is clamped to end exactly at t_end). The endpoint tolerance is a small ABSOLUTE day floor
    (not scaled by absolute model time, so it can't permit a multi-day short endpoint at large t).
    """
    sol = getattr(getattr(sys, "scope", None), "sol", None)
    success = bool(getattr(sol, "success", False)) if sol is not None else False
    # NB: status 0 == success; do NOT use `x or -1` (0 is falsy -> would flag every good solve).
    _status_raw = getattr(sol, "status", None) if sol is not None else None
    status = int(_status_raw) if _status_raw is not None else -1
    t_arr = getattr(sol, "t", None) if sol is not None else None
    t_last = float(t_arr[-1]) if (t_arr is not None and len(t_arr) > 0) else None
    reached = (t_last is not None) and (t_last >= float(t_end) - float(endpoint_atol_days))
    if (sol is None) or (not success) or (status != 0) or (not reached):
        msg = (getattr(sol, "message", "") or "") if sol is not None else "no solver result on sys.scope.sol"
        raise RuntimeError(
            f"BDF integration did not complete to t={float(t_end):.3f} d "
            f"(sol={'present' if sol is not None else 'MISSING'}, success={success}, status={status}, "
            f"t_last={t_last}): {msg}")


def run_simulation_to_steady_state(sys, eff, gas, check_interval=2, t_step=0.1, tolerance=1e-3,
                                   max_days=None):
    """
    Run simulation to steady state, bounded by an optional backstop.

    Performs dynamic simulation in intervals, checking for convergence (``check_steady_state``, hybrid
    abs/rel criterion) after each. Runs until convergence OR until the ``max_days`` backstop is hit.

    Parameters
    ----------
    sys : System
        QSDsan System object with AnaerobicCSTR
    eff : WasteStream
        Effluent stream (must have dynamic tracking)
    gas : WasteStream
        Biogas stream (must have dynamic tracking)
    check_interval : float, optional
        Days between convergence checks (default 2)
    t_step : float, optional
        Time step for output in days (default 0.1)
    tolerance : float, optional
        Absolute convergence floor in kg/m3/d (default 1e-3)
    max_days : float or None, optional
        Backstop: if set, the last integration ends EXACTLY at ``max_days`` (no overshoot) and the
        function returns ``(t, 'capped')`` if convergence is not reached by then. None = no backstop.

    Returns
    -------
    tuple
        (converged_at, status) where status is 'converged' (criterion met) or 'capped' (backstop hit
        without convergence — the caller flags steady_state_ok=False and fails closed).

    Notes
    -----
    - Uses BDF method (recommended for stiff ODEs in ADM1); checks convergence every check_interval days.
    - Converging to true steady state (dC/dt ≈ 0) keeps the methane-yield calc valid
      (Y_CH4 = CH4_flow / (COD_in - COD_out)) by making biomass-inventory accumulation negligible.
    - Typical convergence: 50-150 days for standard ADM1; a high-particulate mADM1 digester may need
      3-5 HRT for the biomass to equilibrate (~250+ days at HRT 47) — hence the backstop, not an
      unbounded loop.
    """
    # Validate (codex): a bad interval/backstop/lookback must raise, not silently loop or overshoot.
    if not (np.isfinite(check_interval) and check_interval > 0):
        raise ValueError(f"check_interval must be positive finite, got {check_interval}")
    if max_days is not None and not (np.isfinite(max_days) and max_days > 0):
        raise ValueError(f"max_days must be positive finite or None, got {max_days}")

    t_current = 0

    logger.info(f"Starting simulation to steady state (backstop={max_days} d, checking every "
                f"{check_interval} days, abs_tol={tolerance})")

    # CRITICAL FIX per Codex: Reset caches ONCE before loop, not every iteration
    # Resetting every iteration causes the simulation to replay the same 2 days forever
    # because all accumulated state and tracking data gets cleared
    logger.debug("Resetting system caches once before simulation loop")
    sys.reset_cache()
    eff.scope.reset_cache()
    gas.scope.reset_cache()

    while True:
        # Clamp so the last integration ends EXACTLY at the backstop — never overshoot it (codex).
        t_next = t_current + check_interval
        if max_days is not None:
            t_next = min(t_next, max_days)

        logger.debug(f"Simulating from t={t_current} to t={t_next} days")

        # Endpoint-safe output grid: never exceed the clamped t_next (codex).
        t_eval = np.arange(t_current, t_next, t_step)
        if t_eval.size == 0 or t_eval[-1] < t_next:
            t_eval = np.append(t_eval, t_next)
        try:
            sys.simulate(
                state_reset_hook=None,  # FIXED: Don't reset - keep accumulated state/data
                t_span=(t_current, t_next),
                t_eval=t_eval,
                **madm1_bdf_options(),
            )
        except Exception as e:
            logger.error(f"Simulation failed at t={t_current}: {e}")
            raise RuntimeError(f"Simulation failed: {e}")

        assert_solver_ok(sys, t_next)   # fail-closed on a silent solve_ivp non-convergence

        t_current = t_next

        # Loose "am I close?" heuristic (the rigorous mature-commission gate is run_simulation_shadow_confirm)
        if check_steady_state(eff, gas, tolerance=tolerance):
            logger.info(f"Converged to steady state at t={t_current} days")
            return t_current, 'converged'

        # Backstop: a slow-equilibrating pool (high-particulate biomass) must not run away. Beyond
        # max_days, return 'capped' so the caller flags steady_state_ok=False and fails closed rather
        # than integrating for thousands of days.
        if max_days is not None and t_current >= max_days:
            logger.warning(f"run_simulation_to_steady_state: hit max_days backstop ({max_days} d) "
                           f"without convergence — returning 'capped'")
            return t_current, 'capped'

        # Log progress every 100 days to show we're still working
        if t_current % 100 == 0:
            logger.info(f"Progress: t={t_current} days, still approaching steady state...")


_SNAPSHOT_MIN_SAMPLES = 2   # a snapshot needs >= this many REAL integrated samples (never a single point)


def _output_snapshot(eff, gas, trail_days):
    """Trailing-mean of the digester OUTPUTS over the last ``trail_days`` of the record (a mean of REAL
    integrated samples, never interpolated). Returns a dict of:
      - ``cod``       : aggregate effluent COD Σ conc·max(i_COD,0)  (the yield DENOMINATOR)
      - ``ch4_prod``  : biogas CH4 PRODUCTION rate = mean(S_ch4_conc · Q_gas)  (the yield NUMERATOR — a
                        RATE, not a headspace concentration, so a stable conc with a drifting flow is caught)
      - ``biomass``   : methanogen biomass X_ac+X_h2  (the slow COD inventory)
      - ``vfa``       : acetate S_ac  (a real accumulation must be caught even when COD redistribution keeps
                        aggregate COD flat — gated with an ABSOLUTE tolerance downstream since it is small)
    All kg/m3 (COD kg-COD/m3; ch4_prod in conc·flow units — only its RELATIVE change is used). Returns None
    (caller resets the agreement chain) if the record is too short, has too few real samples, or non-finite."""
    def _mask(stream):
        t = np.asarray(stream.scope.time_series, dtype=float)
        if t.size < 1 or not np.all(np.isfinite(t)):
            return None
        m = t >= (t[-1] - trail_days)   # always includes the terminal point
        if int(np.count_nonzero(m)) < _SNAPSHOT_MIN_SAMPLES:
            return None   # too few REAL samples in the window (a big adaptive step) -> insufficient
        return m

    me = _mask(eff)
    mg = _mask(gas)
    if me is None or mg is None:
        return None
    rec_e, rec_g = eff.scope.record, gas.scope.record

    def _sum(rec, comps, mask, ids):
        tot = 0.0
        for cid in ids:
            try:
                idx = comps.index(cid)
            except ValueError:
                continue
            tot += float(np.mean(rec[mask, idx]))
        return tot / 1000.0   # mg/L -> kg/m3

    try:
        # scope.record carries a trailing FLOW column (n_comp+1 cols). Aggregate effluent COD mirrors
        # WasteStream.composite('COD'): Σ conc·i_COD over the component columns, with the CANONICAL mask
        # (i_COD >= 0) AND non-gas (1 - components.g) — dissolved S_h2/S_ch4 are gas-phase and excluded.
        comps = eff.components
        cod_coeff = np.asarray(comps.i_COD, dtype=float)
        cod_mask = (cod_coeff >= 0) * (1.0 - np.asarray(comps.g, dtype=float))
        cod = float(np.mean((rec_e[me, :len(comps)] * cod_coeff * cod_mask).sum(axis=1))) / 1000.0
    except Exception:
        cod = float("nan")

    # CH4 production RATE = S_ch4 headspace conc × gas volumetric flow (the record's trailing Q column),
    # AND the total biogas flow Q_gas as an INDEPENDENT metric (so a compensating conc-up/flow-down that
    # holds CH4 production flat is still caught — codex).
    try:
        ch4_idx = gas.components.index("S_ch4")
        q_col = len(gas.components)                       # the flow column follows the n_comp component cols
        ch4_prod = float(np.mean(rec_g[mg, ch4_idx] * rec_g[mg, q_col])) / 1000.0
        q_gas = float(np.mean(rec_g[mg, q_col]))
    except Exception:
        ch4_prod = q_gas = float("nan")

    snap = {
        "cod": cod,
        "ch4_prod": ch4_prod,
        "q_gas": q_gas,
        "biomass": _sum(rec_e, eff.components, me, ("X_ac", "X_h2")),
        # AGGREGATE VFA (S_va+S_bu+S_pro+S_ac): non-acetate redistribution can hold aggregate COD flat while
        # a syntroph VFA accumulates — acetate alone would miss it (codex).
        "vfa": _sum(rec_e, eff.components, me, ("S_va", "S_bu", "S_pro", "S_ac")),
    }
    if not all(np.isfinite(v) for v in snap.values()):
        return None
    return snap


def _snapshots_plateau(snaps, rel_tol, abs_floor):
    """True iff EVERY PAIR of output snapshots (not just consecutive — bounds the total span drift AND
    catches an intermediate oscillation peak) agrees. COD / CH4-production / biomass use the relative
    ``rel_tol``; acetate uses a generous relative OR absolute band (a small, jittery pool) so ±10 mg/L
    solver wobble passes while a real accumulation (e.g. 40→200 mg/L) fails."""
    # (metric, relative_tol, absolute_tol_kg_m3); absolute 0 => relative only
    checks = (("cod", rel_tol, 0.0), ("ch4_prod", rel_tol, 0.0), ("q_gas", rel_tol, 0.0),
              ("biomass", rel_tol, 0.0), ("vfa", max(rel_tol, 0.05), 0.03))
    n = len(snaps)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = snaps[i], snaps[j]
            for name, rt, at in checks:
                d = abs(b[name] - a[name])
                denom = max(abs(a[name]), abs(b[name]), abs_floor)
                if (d / denom) > rt and not (at > 0.0 and d <= at):
                    return False
    return True


def run_simulation_shadow_confirm(sys, eff, gas, *, confirm_days=30.0, min_settle_days=90.0,
                                  max_days=600.0, rel_tol=0.01, abs_floor=1e-3, t_step=0.1,
                                  n_agree=2, trail_days=5.0):
    """Rigorous steady-state gate (codex shadow-continuation) for the mature commission.

    Settle in ``confirm_days`` chunks; declare convergence only when the last ``n_agree``+1 OUTPUT snapshots
    — aggregate effluent COD, biogas CH4, methanogen biomass, acetate, each a trailing-mean over the chunk's
    last ``trail_days`` — agree pairwise within ``rel_tol``. This compares REAL, well-separated integrated
    states (no interpolation / smoothing / endpoint-differencing of a noisy trajectory), DIRECTLY gates the
    COD + biogas outputs the methane-yield depends on, and catches slow monotonic drift AND oscillation up
    to ~``n_agree``·``confirm_days``. Returns ``(t, 'converged'|'capped')``; fails closed at the backstop.
    """
    for nm, v in (("confirm_days", confirm_days), ("max_days", max_days), ("trail_days", trail_days),
                  ("t_step", t_step), ("rel_tol", rel_tol)):
        if not (np.isfinite(v) and v > 0):
            raise ValueError(f"{nm} must be positive finite, got {v}")
    if int(n_agree) < 1:
        raise ValueError(f"n_agree must be >= 1, got {n_agree}")
    n_agree = int(n_agree)

    sys.reset_cache()
    eff.scope.reset_cache()
    gas.scope.reset_cache()
    logger.info(f"Shadow-confirm settle: confirm_days={confirm_days}, n_agree={n_agree}, rel_tol={rel_tol}, "
                f"min_settle={min_settle_days}, backstop={max_days}")

    snaps = []
    t_current = 0.0
    while t_current < max_days:
        t_next = min(t_current + confirm_days, max_days)
        t_eval = np.arange(t_current, t_next, t_step)
        if t_eval.size == 0 or t_eval[-1] < t_next:
            t_eval = np.append(t_eval, t_next)
        try:
            sys.simulate(
                state_reset_hook=None,
                t_span=(t_current, t_next),
                t_eval=t_eval,
                **madm1_bdf_options(),
            )
        except Exception as e:
            logger.error(f"Shadow-confirm simulate failed at t={t_current}: {e}")
            raise RuntimeError(f"Simulation failed: {e}")
        assert_solver_ok(sys, t_next)   # fail-closed on a silent solve_ivp non-convergence
        t_current = t_next

        snap = _output_snapshot(eff, gas, trail_days)
        if snap is None:
            logger.warning(f"Shadow-confirm: null snapshot at t={t_current} d -> reset agreement chain")
            snaps = []
            continue
        snaps.append(snap)
        if t_current >= min_settle_days and len(snaps) >= (n_agree + 1):
            window = snaps[-(n_agree + 1):]
            if _snapshots_plateau(window, rel_tol, abs_floor):
                logger.info(f"Shadow-confirm CONVERGED at t={t_current} d (outputs {window[-1]})")
                return t_current, 'converged'

    logger.warning(f"Shadow-confirm hit backstop {max_days} d without an output plateau")
    return t_current, 'capped'


def extract_time_series(eff, gas):
    """
    Extract time series data from stream scopes for diagnostics.

    Parameters
    ----------
    eff : WasteStream
        Effluent stream with dynamic tracking
    gas : WasteStream
        Biogas stream with dynamic tracking

    Returns
    -------
    dict
        Time series data including:
        - time: Array of time points (days)
        - time_units: "days"
        - effluent_cod: COD trajectory (mg/L) **UNITS VERIFIED**
        - effluent_cod_units: "mg/L"
        - effluent_vfa: VFA (S_ac) trajectory (mg/L) **UNITS VERIFIED**
        - effluent_vfa_units: "mg/L"
        - effluent_biomass: Biomass (X_ac) trajectory (mg/L) **UNITS VERIFIED**
        - effluent_biomass_units: "mg/L"
        - biogas_ch4: CH4 concentration trajectory (mg/L in gas phase)
        - biogas_ch4_units: "mg/L"

    Notes
    -----
    **CRITICAL UNITS DOCUMENTATION**:
    All concentrations from eff.scope.record are in **mg/L**, NOT kg/m³.
    This was verified via Codex investigation of QSDsan v1.4.2 source code.
    Do NOT multiply by 1000 for unit conversion - values are already in mg/L.
    """
    time_series = {
        'success': False,
        'message': None,
        'time': [],
        'time_units': 'days',
        'effluent_cod': [],
        'effluent_cod_units': 'mg/L',
        'effluent_vfa': [],
        'effluent_vfa_units': 'mg/L',
        'effluent_biomass': [],
        'effluent_biomass_units': 'mg/L',
        'biogas_ch4': [],
        'biogas_ch4_units': 'mg/L'
    }

    try:
        # Check if tracking data exists
        if not hasattr(eff, 'scope') or not hasattr(eff.scope, 'record'):
            time_series['message'] = "No dynamic tracking data available"
            return time_series

        # Extract time array
        time_arr = eff.scope.time_series
        time_series['time'] = time_arr.tolist()

        # Extract effluent components
        record_eff = eff.scope.record
        components = eff.components

        # Get indices for key components
        try:
            idx_S_ac = components.index('S_ac')
            idx_X_ac = components.index('X_ac')

            # Extract trajectories - UNITS: mg/L (from eff.scope.record)
            # NO conversion needed - values are already in mg/L
            time_series['effluent_vfa'] = record_eff[:, idx_S_ac].tolist()  # mg/L
            time_series['effluent_biomass'] = record_eff[:, idx_X_ac].tolist()  # mg/L

            # Calculate COD at each time point
            # Mirror WasteStream.composite('COD'): record stores mg/L, skip gases/negatives
            # Per Codex investigation of QSDsan source:
            # - eff.scope.record contains concentrations in mg/L (not kg/m³)
            # - i_COD is dimensionless (kg COD/kg component)
            # - Result is mg COD/L (no conversion needed)
            # - Must exclude gas components (g=1) and negative i_COD
            cod_coeff = np.asarray(components.i_COD)
            cod_mask = (cod_coeff >= 0) * (1 - np.asarray(components.g))
            eff_conc = record_eff[:, :len(components)]  # mg/L for each component
            time_series['effluent_cod'] = (
                eff_conc * cod_coeff * cod_mask
            ).sum(axis=1).tolist()

        except ValueError as e:
            logger.warning(f"Some effluent components not found: {e}")

        # Extract biogas data
        if hasattr(gas, 'scope') and hasattr(gas.scope, 'record'):
            record_gas = gas.scope.record
            gas_components = gas.components

            try:
                idx_ch4 = gas_components.index('S_ch4')
                # Store CH4 concentration trajectory
                time_series['biogas_ch4'] = record_gas[:, idx_ch4].tolist()

            except ValueError:
                logger.warning("CH4 component not found in biogas")

        time_series['success'] = True
        time_series['message'] = f"Extracted {len(time_arr)} time points"
        logger.info(f"Time series extracted: {len(time_arr)} points from 0 to {time_arr[-1]:.1f} days")

    except Exception as e:
        time_series['message'] = f"Error extracting time series: {str(e)}"
        logger.error(f"Failed to extract time series: {e}")

    return time_series


def run_simulation_sulfur(basis, adm1_state_62, HRT, check_interval=2, tolerance=1e-3, pH_ctrl=None,
                          fixed_naoh_dose_m3_d=0.0, fixed_fecl3_dose_m3_d=0.0, fixed_na2co3_dose_m3_d=0.0,
                          naoh_conc_kg_m3=431.25, fecl3_conc_kg_m3=400.0, na2co3_conc_kg_m3=106.0,
                          kinetic_params=None):
    """
    Run single ADM1+sulfur simulation at specified HRT until TRUE steady state.

    Main simulation function that sets up and runs a dynamic anaerobic digester
    simulation with the extended ADM1+sulfur model. Runs indefinitely until
    convergence is detected (no arbitrary time limits).

    Parameters
    ----------
    basis : dict
        Basis of design containing:
        - 'Q': Flow rate (m3/d)
        - 'Temp': Temperature (K)
        - Other design parameters
    adm1_state_62 : dict
        full mADM1 state (kg/m3)
        Must include all ADM1 components + sulfur biology + P/S/Fe extensions
    HRT : float
        Hydraulic retention time in days
    check_interval : float, optional
        Days between convergence checks (default 2, for faster convergence detection)
    tolerance : float, optional
        Convergence tolerance in kg/m3/d (default 1e-3, relaxed from 5e-4 to avoid
        chasing numerical noise below BDF solver precision)
    pH_ctrl : float, optional
        If specified, fixes pH at this value (e.g., 7.0) to emulate perfect pH control.
        Used for rapid diagnostic testing to rule out alkalinity limitation
    fixed_naoh_dose_m3_d : float, optional
        Fixed NaOH dosing rate (m3/d), default 0.0
    fixed_fecl3_dose_m3_d : float, optional
        Fixed FeCl3 dosing rate (m3/d), default 0.0
    fixed_na2co3_dose_m3_d : float, optional
        Fixed Na2CO3 dosing rate (m3/d), default 0.0
    naoh_conc_kg_m3 : float, optional
        NaOH concentration as kg Na+/m³ (default 431.25 = 50% commercial NaOH)
    fecl3_conc_kg_m3 : float, optional
        FeCl3 concentration as kg Fe³⁺/m³ (default 400.0, uses S_Fe3 component)
    na2co3_conc_kg_m3 : float, optional
        Na2CO3 concentration as kg Na+/m³ (default 106.0)
    kinetic_params : dict, optional
        Kinetic parameter overrides for ModifiedADM1 (Phase 10).
        See core.kinetic_params.MADM1_KINETIC_SCHEMA for available parameters.
        Examples: {"k_ac": 8.0, "K_ac": 0.15, "k_hSRB": 41.0}

    Returns
    -------
    tuple
        (sys, inf, eff, gas, converged_at, status, time_series) where:
        - sys: QSDsan System object
        - inf: Influent WasteStream
        - eff: Effluent WasteStream
        - gas: Biogas WasteStream
        - converged_at: Time converged (days)
        - status: Always 'converged' (runs until steady state achieved)
        - time_series: Time series data dict (COD, VFA, biomass over time)

    Notes
    -----
    **CRITICAL SETUP REQUIREMENTS** (per Codex review):
    1. Must use set_dynamic_tracker(eff, gas) before simulate()
    2. Must use state_reset_hook='reset_cache' in simulate()
    3. Must initialize sulfur components with non-zero values
    4. Runs indefinitely until TRUE pseudo-steady-state (no time limits)

    **Refactored (2025-10-26)**: Eliminated arbitrary max_time parameter. Simulations
    now run until dCOD/dt ≈ 0, which eliminates need for complex COD inventory tracking
    in methane yield calculations: Y_CH4 = CH4_flow / (COD_in - COD_out)

    Progress is logged every 100 days to show simulation is still running.
    Convergence detection uses rolling average smoothing to reduce BDF solver jitter.

    Without these requirements, streams won't update and results will be wrong!
    """
    try:
        Q = basis['Q']
        Temp = basis.get('Temp', 308.15)  # Default 35°C

        logger.info(f"=== Starting mADM1 Simulation ===")
        logger.info(f"Q={Q} m3/d, T={Temp} K, HRT={HRT} days")

        # 1. Create mADM1 model (use ModifiedADM1 directly - no extend function needed)
        # ModifiedADM1 already has SRB processes, H2S inhibition, and all the mADM1 components
        # CRITICAL FIX: Generate proper inoculum from feedstock composition
        # Solves "pickling" problem where feedstock biomass (~1 kg/m³) is insufficient
        # for CSTR startup, causing F/M overload and immediate failure
        logger.info("="*80)
        logger.info("INOCULUM GENERATOR - Scaling biomass for CSTR startup")
        logger.info("="*80)
        reactor_init_state = generate_inoculum_state(
            feedstock_state=adm1_state_62,
            target_biomass_cod_ratio=0.20  # 20% of COD as biomass (typical healthy digester)
        )
        logger.info("="*80)

        logger.info("Creating mADM1 model (built-in sulfur biology)")
        # CRITICAL: Use get_validated_components() to ensure consistent thermo/ordering
        # Calling create_madm1_cmps() directly causes thermo flip-flopping and convergence failures
        madm1_cmps = get_validated_components()

        # Phase 10: Pass kinetic parameters to ModifiedADM1
        if kinetic_params:
            logger.info(f"Applying {len(kinetic_params)} kinetic parameter overrides")
            madm1_model = ModifiedADM1(components=madm1_cmps, **kinetic_params)
        else:
            madm1_model = ModifiedADM1(components=madm1_cmps)
        logger.info(f"mADM1 model created with {len(madm1_model)} processes")

        # 2. Create streams with the mADM1 components
        logger.info("Creating influent stream with mADM1 state")
        inf = create_influent_stream_sulfur(Q, Temp, adm1_state_62)
        eff = WasteStream('Effluent', T=Temp)
        gas = WasteStream('Biogas')

        # 2b. Create fixed dosing streams if any doses are specified
        dosing_streams = []
        if fixed_naoh_dose_m3_d > 0:
            naoh = WasteStream('NaOH_Dosing', T=Temp)
            naoh.set_flow_by_concentration(
                flow_tot=fixed_naoh_dose_m3_d,
                concentrations={'S_Na': naoh_conc_kg_m3 * 1000},  # kg/m³ to mg/L
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(naoh)
            logger.info(f"Created NaOH dosing stream: {naoh_conc_kg_m3:.2f} kg/m³ S_Na, flow = {fixed_naoh_dose_m3_d:.4f} m³/d")

        if fixed_fecl3_dose_m3_d > 0:
            fecl3 = WasteStream('FeCl3_Dosing', T=Temp)
            # Ferric enters as FRESH HFO (X_HFO_H, Fe basis) + 3 Cl- — codex
            # mineral-fix review: no Petersen process consumes soluble S_Fe3
            # (kinetically inert); the reduction pathway to Fe2+/S0/FeS/
            # vivianite starts from the HFO solids, and the chloride carries
            # the three acid equivalents of the hydrolysis.
            fe_kg_m3 = fecl3_conc_kg_m3 * (55.845 / 162.204)
            fecl3.set_flow_by_concentration(
                flow_tot=fixed_fecl3_dose_m3_d,
                concentrations={'X_HFO_H': fe_kg_m3 * 1000,
                                'S_Cl': fe_kg_m3 * (3 * 35.453 / 55.845) * 1000},
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(fecl3)
            logger.info(
                f"Created FeCl3 dosing stream: {fe_kg_m3:.2f} kg Fe/m³ as fresh "
                f"X_HFO_H (+Cl), flow = {fixed_fecl3_dose_m3_d:.4f} m³/d")

        if fixed_na2co3_dose_m3_d > 0:
            na2co3 = WasteStream('Na2CO3_Dosing', T=Temp)
            # Soda ash carries BOTH the sodium (2 Na per Na2CO3) and the
            # carbonate CARBON — the old stream injected Na+ only, so the
            # dose bought alkalinity purely through the SID while the carbon
            # inventory (and its pCO2/pH coupling) went missing.
            na_kg_m3 = na2co3_conc_kg_m3 * (2 * 22.99 / 105.99)
            c_kg_m3 = na2co3_conc_kg_m3 * (12.011 / 105.99)
            na2co3.set_flow_by_concentration(
                flow_tot=fixed_na2co3_dose_m3_d,
                concentrations={'S_Na': na_kg_m3 * 1000,
                                'S_IC': c_kg_m3 * 1000},   # kg/m³ to mg/L
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(na2co3)
            logger.info(
                f"Created Na2CO3 dosing stream: {na_kg_m3:.2f} kg Na/m³ + "
                f"{c_kg_m3:.2f} kg C/m³, flow = {fixed_na2co3_dose_m3_d:.4f} m³/d")

        # Prepare inlet streams list
        inlet_streams = [inf] + dosing_streams

        # 3. Create AnaerobicCSTRmADM1 reactor (supports 4 biogas species: CH4, CO2, H2, H2S)
        V_liq = Q * HRT
        V_gas = V_liq * 0.1  # 10% of liquid volume

        logger.info(f"Creating AnaerobicCSTRmADM1: V_liq={V_liq:.1f} m3, V_gas={V_gas:.1f} m3")
        logger.info(f"Biogas species: {madm1_model._biogas_IDs}")
        logger.info(f"Inlet streams: {len(inlet_streams)} (influent + {len(dosing_streams)} dosing)")

        AD = AnaerobicCSTRmADM1(
            'AD',
            ins=inlet_streams,  # Now includes dosing streams
            outs=(gas, eff),
            model=madm1_model,
            V_liq=V_liq,
            V_gas=V_gas,
            T=Temp,
            isdynamic=True,  # Enable dynamic simulation
            f_retain=0  # CRITICAL FIX: Set to 0 for true CSTR behavior (no retention)
        )
        # Use algebraic H2 for stability (per Codex recommendation and BSM2 pattern)
        AD.algebraic_h2 = True

        # Compile ODE with pH control if requested (rapid diagnostic test)
        if pH_ctrl is not None:
            logger.info(f"DIAGNOSTIC MODE: Fixing pH at {pH_ctrl} to emulate perfect pH control")
            AD._compile_ODE(algebraic_h2=True, pH_ctrl=pH_ctrl)

        logger.info(f"Reactor configured: algebraic_h2={AD.algebraic_h2}, pH_ctrl={pH_ctrl}, fixed dosing streams={len(dosing_streams)}")

        # 4. Initialize reactor with INOCULUM state (NOT feedstock!)
        # Use reactor_init_state (scaled biomass) instead of adm1_state_62 (feedstock)
        logger.info("Initializing reactor with INOCULUM state (scaled biomass)")
        init_conds_kg_m3 = initialize_madm1_state(reactor_init_state)

        # CRITICAL FIX: Convert kg/m³ to mg/L for set_init_conc()
        # QSDsan's set_init_conc() expects mg/L, but our state is in kg/m³
        # Without this conversion, initial concentrations are 1000x too low
        init_conds_mg_L = {k: v * 1000 for k, v in init_conds_kg_m3.items()}  # kg/m³ -> mg/L
        logger.info(f"Unit conversion applied: X_I = {init_conds_kg_m3.get('X_I', 0):.3f} kg/m³ -> {init_conds_mg_L.get('X_I', 0):.1f} mg/L")

        AD.set_init_conc(**init_conds_mg_L)
        logger.info(f"Initial S_SO4={init_conds_kg_m3['S_SO4']:.4f}, S_IS={init_conds_kg_m3['S_IS']:.6f}, X_hSRB={init_conds_kg_m3.get('X_hSRB', 0):.4f} (all kg/m³)")

        # 5. Set up dynamic system
        # CRITICAL FIX: Use None for ID to get unique auto-generated ID
        # This prevents ValueError on duplicate System IDs in dual-HRT runs
        logger.info("Setting up dynamic system with tracking")
        sys = System(None, path=(AD,))  # None = auto-generate unique ID

        # CRITICAL: Enable dynamic tracking for streams to update
        sys.set_dynamic_tracker(eff, gas)
        logger.info("Dynamic tracking enabled for effluent and biogas streams")

        # 6. Run simulation to steady state (no time limit)
        logger.info("Running simulation to TRUE steady-state (to avoid inventory tracking)...")
        converged_at, status = run_simulation_to_steady_state(
            sys, eff, gas,
            check_interval=check_interval,
            tolerance=tolerance
        )

        logger.info(f"Simulation complete: {status} at t={converged_at} days")

        # 7. Update pH and alkalinity for effluent
        logger.info("Calculating final pH and alkalinity")
        # CODEX BUG FIX #1: Removed the old pH calculator - it expects S_cat/S_an (lumped ions)
        # but our mADM1 model uses explicit ions (Na+, K+, Cl-, etc.)
        # The PCM already calculates correct pH during simulation - don't overwrite it
        # update_ph_and_alkalinity(eff)
        logger.info(f"Final effluent pH={eff.pH:.2f}, SAlk={eff.SAlk:.3f} meq/L")

        # 8. Log key results
        logger.info(f"=== Simulation Results ===")
        logger.info(f"COD in: {inf.COD:.1f} mg/L, COD out: {eff.COD:.1f} mg/L")
        logger.info(f"COD removal: {(1 - eff.COD/inf.COD)*100:.1f}%")
        logger.info(f"Biogas production: {gas.F_vol*24:.2f} m3/d")

        # Extract time series data for diagnostics
        time_series_data = extract_time_series(eff, gas)

        return sys, inf, eff, gas, converged_at, status, time_series_data

    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        raise RuntimeError(f"Error running ADM1+sulfur simulation: {e}")


def assess_robustness(results_design, results_check, threshold=10.0):
    """
    Compare performance at design HRT vs check HRT.

    Flags warnings if performance drops significantly (>threshold%) at check HRT,
    indicating the design may be sensitive to operational variations.

    Parameters
    ----------
    results_design : tuple
        (sys, inf, eff, gas, converged_at, status) at design HRT
    results_check : tuple
        (sys, inf, eff, gas, converged_at, status) at check HRT
    threshold : float, optional
        Performance drop threshold in % (default 10.0)

    Returns
    -------
    list
        List of warning messages if performance issues detected
    """
    warnings = []

    sys_d, inf_d, eff_d, gas_d, t_d, status_d, _time_series_d = results_design
    sys_c, inf_c, eff_c, gas_c, t_c, status_c, _time_series_c = results_check

    # COD removal comparison with zero guard
    if inf_d.COD > 1e-6 and inf_c.COD > 1e-6:
        cod_removal_design = (1 - eff_d.COD / inf_d.COD) * 100
        cod_removal_check = (1 - eff_c.COD / inf_c.COD) * 100
        cod_drop = cod_removal_design - cod_removal_check

        if cod_drop > threshold:
            warnings.append(
                f"COD removal drops {cod_drop:.1f}% at increased HRT "
                f"({cod_removal_design:.1f}% -> {cod_removal_check:.1f}%). "
                f"Design may be sensitive to HRT variations."
            )
    else:
        warnings.append(
            f"COD removal assessment skipped: influent COD near zero "
            f"(design: {inf_d.COD:.6f}, check: {inf_c.COD:.6f} mg/L)"
        )

    # Biogas production comparison with zero guard
    biogas_design = gas_d.F_vol * 24  # m3/d
    biogas_check = gas_c.F_vol * 24

    if biogas_design > 1e-6:
        biogas_change = abs(biogas_design - biogas_check) / biogas_design * 100

        if biogas_change > threshold:
            warnings.append(
                f"Biogas production changes {biogas_change:.1f}% at increased HRT "
                f"({biogas_design:.1f} -> {biogas_check:.1f} m3/d)."
            )
    else:
        warnings.append(
            f"Biogas production assessment skipped: design case produced near-zero biogas "
            f"({biogas_design:.6f} m3/d). System may have failed."
        )

    # Convergence comparison
    if status_d == 'converged' and status_c != 'converged':
        warnings.append(
            f"Design HRT converged at {t_d} days but check HRT did not converge. "
            f"System may be unstable at higher HRT."
        )

    return warnings


def run_dual_hrt_simulation(basis, adm1_state_62, heuristic_config, hrt_variation=0.2,
                            check_interval=2, tolerance=1e-3, pH_ctrl=None,
                            fixed_naoh_dose_m3_d=0.0, fixed_fecl3_dose_m3_d=0.0, fixed_na2co3_dose_m3_d=0.0,
                            naoh_conc_kg_m3=431.25, fecl3_conc_kg_m3=400.0, na2co3_conc_kg_m3=106.0,
                            kinetic_params=None):
    """
    Run simulation at design SRT and validation SRT.

    Performs two simulations:
    1. At design SRT from heuristic sizing
    2. At design SRT * (1 + hrt_variation) for robustness check

    Both simulations run until TRUE steady state (no time limit).

    Parameters
    ----------
    basis : dict
        Basis of design with Q, Temp, etc.
    adm1_state_62 : dict
        full mADM1 state
    heuristic_config : dict
        Heuristic sizing results containing digester SRT
    hrt_variation : float, optional
        Fractional SRT variation for check (default 0.2 = ±20%)
    check_interval : float, optional
        Days between convergence checks (default 2)
    tolerance : float, optional
        Convergence tolerance in kg/m3/d (default 1e-3)
    pH_ctrl : float, optional
        If specified, fixes pH at this value (e.g., 7.0) to emulate perfect pH control
    fixed_naoh_dose_m3_d : float, optional
        Fixed NaOH dosing flow rate in m³/d (default 0)
    fixed_fecl3_dose_m3_d : float, optional
        Fixed FeCl3 dosing flow rate in m³/d (default 0)
    fixed_na2co3_dose_m3_d : float, optional
        Fixed Na2CO3 dosing flow rate in m³/d (default 0)
    naoh_conc_kg_m3 : float, optional
        NaOH solution concentration in kg/m³ S_Na (default 431.25 = 50% NaOH)
    fecl3_conc_kg_m3 : float, optional
        FeCl3 solution concentration in kg/m³ S_Fe (default 100.0)
    na2co3_conc_kg_m3 : float, optional
        Na2CO3 solution concentration in kg/m³ S_Na (default 106.0)

    Returns
    -------
    tuple
        (results_design, results_check, warnings) where:
        - results_design: Tuple from run_simulation_sulfur at design SRT
        - results_check: Tuple from run_simulation_sulfur at check SRT
        - warnings: List of robustness warnings

    Notes
    -----
    For CSTR without MBR, SRT = HRT by definition (biomass leaves with liquid).
    This dual-SRT approach validates that the design isn't sitting on a performance cliff.

    Simulations run indefinitely until convergence - no arbitrary time limits.
    """
    SRT_design = heuristic_config['digester']['srt_days']
    SRT_check = SRT_design * (1 + hrt_variation)

    logger.info(f"=== Dual-SRT Validation ===")
    logger.info(f"Design SRT: {SRT_design} days (HRT = SRT for CSTR)")
    logger.info(f"Check SRT: {SRT_check} days (+{hrt_variation*100:.0f}%)")

    # Run at design SRT (runs until convergence, no time limit)
    logger.info("Running simulation at design SRT...")
    results_design = run_simulation_sulfur(basis, adm1_state_62, SRT_design,
                                          check_interval=check_interval,
                                          tolerance=tolerance,
                                          pH_ctrl=pH_ctrl,
                                          fixed_naoh_dose_m3_d=fixed_naoh_dose_m3_d,
                                          fixed_fecl3_dose_m3_d=fixed_fecl3_dose_m3_d,
                                          fixed_na2co3_dose_m3_d=fixed_na2co3_dose_m3_d,
                                          naoh_conc_kg_m3=naoh_conc_kg_m3,
                                          fecl3_conc_kg_m3=fecl3_conc_kg_m3,
                                          na2co3_conc_kg_m3=na2co3_conc_kg_m3,
                                          kinetic_params=kinetic_params)

    # Run at check SRT (runs until convergence, no time limit)
    logger.info("Running simulation at check SRT...")
    results_check = run_simulation_sulfur(basis, adm1_state_62, SRT_check,
                                         check_interval=check_interval,
                                         tolerance=tolerance,
                                         pH_ctrl=pH_ctrl,
                                         fixed_naoh_dose_m3_d=fixed_naoh_dose_m3_d,
                                         fixed_fecl3_dose_m3_d=fixed_fecl3_dose_m3_d,
                                         fixed_na2co3_dose_m3_d=fixed_na2co3_dose_m3_d,
                                         naoh_conc_kg_m3=naoh_conc_kg_m3,
                                         fecl3_conc_kg_m3=fecl3_conc_kg_m3,
                                         na2co3_conc_kg_m3=na2co3_conc_kg_m3,
                                         kinetic_params=kinetic_params)

    # Assess robustness
    logger.info("Assessing design robustness...")
    warnings = assess_robustness(results_design, results_check)

    if warnings:
        logger.warning(f"Robustness issues detected: {len(warnings)} warnings")
        for warning in warnings:
            logger.warning(f"  - {warning}")
    else:
        logger.info("Design appears robust to HRT variations")

    return results_design, results_check, warnings
