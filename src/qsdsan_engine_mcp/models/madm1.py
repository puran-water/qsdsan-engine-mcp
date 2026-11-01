# -*- coding: utf-8 -*-
'''
QSDsan: Quantitative Sustainable Design for sanitation and resource recovery systems

This module is developed by:
    Joy Zhang <joycheung1994@gmail.com>

This module is under the University of Illinois/NCSA Open Source License.
Please refer to https://github.com/QSD-Group/QSDsan/blob/main/LICENSE.txt
for license details.
'''

from thermosteam.utils import chemicals_user
from thermosteam import settings
from chemicals.elements import molecular_weight as get_mw
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc, ensure_qsdsan_processes_load_from_file

ensure_biosteam_tictoc()
ensure_qsdsan_processes_load_from_file()
from qsdsan import Component, Components, Process, Processes, CompiledProcesses
import numpy as np, qsdsan.process_models as pc, qsdsan as qs
from collections.abc import Mapping
from qsdsan.utils import ospath, data_path
from qsdsan.process_models._adm1 import (
    R,
    create_adm1_cmps,
    ADM1,
    mass2mol_conversion,
    T_correction_factor,
    substr_inhibit,
    non_compet_inhibit,
    Hill_inhibit
    )
# Import thermodynamic functions for mineral precipitation
from .thermodynamics import calc_saturation_indices, mineral_nu_array
# The ONE mADM1 core parameter set (process_model_schema.madm1_biokinetics, plan 2026-10-02 section 5, Wave 2
# item 6): ModifiedADM1's defaults below are READ from it, so this twin and the WaterTAP kernel digester carry the
# same values; tests/test_madm1_shared_biokinetics.py proves rhos_madm1 equals the kernel's rate laws.
from puran_mcp_support.schema.madm1_biokinetics import MADM1_BIOKINETICS_KERNEL, SOLUBLE_INERT_UPTAKE, kernel_parameter_values
_K = kernel_parameter_values()

# is compiled into this twin ONLY when asked (``ModifiedADM1(soluble_inert_uptake=True)``, through

# it only by an owner-ruled migration, so the default model's rows are the TSV's, untouched (bit-identical).
# The S/Fe extension of the shared kernel (process_model_schema.madm1_sfe_kinetics, phase 3a, owner ruling 2026-10-07
# "After SRB in WaterTAP"): the SRB/HFO defaults below are READ from it, as the core's are from _K.
from puran_mcp_support.schema.madm1_sfe_kinetics import sfe_kernel_parameter_values
_KS = sfe_kernel_parameter_values()

# (X_Sorg -> S_IS at protein's q_pr_hyd). Compiled ONLY when asked (``create_madm1_cmps(organic_sulfur=True)`` +
# ``ModifiedADM1(organic_sulfur=True)``); the default model carries neither, so every running twin is unchanged.
from puran_mcp_support.schema.madm1_organic_sulfur import MADM1_ORGANIC_SULFUR_EXTENSION, ORGANIC_SULFUR_RATE_CONSTANT
# The OPT-IN micro-aeration extension (PUBLIC-RULE-6dce80fc160d, plan step 2): liquid S_O2, two
# headspace states (S_o2, S_n2), processes 76-85 and the O2-inhibition factor, all READ from
# process_model_schema.madm1_microaeration through models/madm1_microaeration.py (the twin's binding). Compiled ONLY
# when asked (``create_madm1_cmps(organic_sulfur=True, microaeration=True)`` + ``ModifiedADM1(organic_sulfur=True,
# microaeration=True)``); the default model carries none of it, so every running twin is unchanged.
from . import madm1_microaeration as _microaeration
# The ONE speciation model (owner ruling HOUSE-MADM1-DAVIES-BOTH-ENGINES-20261007, "Davies for both"): pcm() solves
# electroneutrality with Davies activities through the shared arithmetic the WaterTAP house Davies block is tested
# against (process_chemistry_reference.madm1_speciation).
from puran_mcp_support.chemistry.madm1_speciation import MadmLiquor, pair_alkalinity_M, solve_madm1_speciation, species_at

#: The acid constants and Davies coefficient the shared speciation reads from the kernel (the seven ADM1 base acids
#: come from the model's own Ka_base / Ka_dH, so a parameter override still reaches them).
_SPECIATION_KERNEL_KEYS = ("pKa_co3", "dH_Ka_co3", "pKa_h2po4", "dH_Ka_h2po4", "R_gas",
                           "davies_b")
_BASE_ACIDS = ("w", "nh4", "co2", "ac", "pro", "bu", "va")


def madm1_mass2mol(cmps):
    """kg/m3 -> mol/L per component, with S_IP on the ELEMENTAL phosphorus basis.

    qsdsan's mass2mol_conversion reads S_IP's formula from ASM2d's S_PO4 pseudo-species ``H3P2O8-3`` ("assumed 50%
    H2PO4-, 50% HPO4-2"), which carries 2 P per unit: it converted kg P to moles at 61.95 kg P/kmol, HALF the true
    phosphorus molarity. S_IP is measured as P, so it converts at the kernel's MW_P (30.973761 kg/kmol). Owner ruling
    HOUSE-MADM1-SIP-MOLARITY-FIX-20261007 (Hersh, 2026-10-07, AskUserQuestion), verbatim answer: "Fix with replay
    (Recommended)". Every other ion already converted on its element (S_IC 12.0107, S_IN 14.0067, S_IS 32.065)."""
    conv = np.array(mass2mol_conversion(cmps), dtype=float)
    if 'S_IP' in cmps.IDs:
        conv[cmps.index('S_IP')] = 1.0 / _K['MW_P']
    return conv


def _speciation_values(params):
    """The shared speciation's constants: the model's own seven ADM1 acids (Ka_base / Ka_dH) + the kernel's rest."""
    values = {k: _K[k] for k in _SPECIATION_KERNEL_KEYS}
    for acid, ka, dh in zip(_BASE_ACIDS, np.asarray(params['Ka_base'], dtype=float),
                            np.asarray(params['Ka_dH'], dtype=float)):
        values[f'pKa_{acid}'] = -np.log10(ka)
        values[f'dH_Ka_{acid}'] = float(dh)
    values['T_base'] = float(params['T_base'])
    return values
# from scipy.optimize import brenth
# from warnings import warn


__all__ = ('create_madm1_cmps', 'ModifiedADM1', 'pcm', 'total_alkalinity_meq_L',
           'titration_alkalinity_mg_caco3_L', 'fostac_like_basis',
           'MissingSaturationIndexError', 'require_saturation_indices')

_path = ospath.join(data_path, 'process_data/_madm1.tsv')

#%% components
# C_mw = get_mw({'C':1})
N_mw = get_mw({'N':1})
P_mw = get_mw({'P':1})
S_mw = get_mw({'S':1})
Fe_mw = get_mw({'Fe':1})
O_mw = get_mw({'O':1})

# Native donor-basis conversions used by the HFO-reduction rate law. S_h2 is
# kg COD/m3 (16 kg O2-equivalent per kmol H2); S_IS is now kg S/m3 and must
# therefore use sulfur's molar mass, not the historical 64 kg COD/kmol H2S.
H2_COD_KG_PER_KMOL = 16.0
S_KG_PER_KMOL = S_mw

# Literature-screening value only; it is not a plant-calibrated constant and
# cannot make a result recommendation-eligible. This replaces the former
# effectively instantaneous 1e9/Fe_mw default while preserving the original
# second-order units (m3 kmol-donor^-1 d^-1).
K_FE3_REDUCTION_SCREENING = 4.7e5 / Fe_mw

_SRB_SULFUR_PROCESSES = (
    'growth_SRB_h2',
    'growth_SRB_acetate',
    'growth_SRB_propionate',
    'growth_SRB_butyrate',
    'growth_SRB_valerate',
)

# The upstream mADM1 surface module carries separate fresh/aged and
# P-bound/unbound HFO inventories, but its four reduction rows destroy only
# fresh, unbound HFO.  These additional host-specific rows close that gap
# without changing the component/state vector (and therefore without inventing
# a checkpoint state migration).  Every row is on a kg-Fe reference basis.
_HFO_HOST_REDUCTION_SPECS = (
    # process id, host component, donor, kinetic-history class
    ('reduction_HFO_HP_h2', 'X_HFO_HP', 'h2', 'fresh_bound'),
    ('reduction_HFO_LP_h2', 'X_HFO_LP', 'h2', 'fresh_bound'),
    ('reduction_HFO_old_h2', 'X_HFO_old', 'h2', 'aged_unbound'),
    ('reduction_HFO_HP_old_h2', 'X_HFO_HP_old', 'h2', 'aged_bound'),
    ('reduction_HFO_LP_old_h2', 'X_HFO_LP_old', 'h2', 'aged_bound'),
    ('reduction_HFO_HP_IS', 'X_HFO_HP', 'sulfide', 'fresh_bound'),
    ('reduction_HFO_LP_IS', 'X_HFO_LP', 'sulfide', 'fresh_bound'),
    ('reduction_HFO_old_IS', 'X_HFO_old', 'sulfide', 'aged_unbound'),
    ('reduction_HFO_HP_old_IS', 'X_HFO_HP_old', 'sulfide', 'aged_bound'),
    ('reduction_HFO_LP_old_IS', 'X_HFO_LP_old', 'sulfide', 'aged_bound'),
)


class MissingSaturationIndexError(RuntimeError):
    """A required mineral SI was absent or non-finite in a rate evaluation."""


def require_saturation_indices(si_by_phase, mineral_names):
    """Return ordered, finite SIs or fail closed with phase-level detail."""

    missing = [name for name in mineral_names if name not in si_by_phase]
    nonfinite = [
        name
        for name in mineral_names
        if name in si_by_phase and not np.isfinite(float(si_by_phase[name]))
    ]
    if missing or nonfinite:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if nonfinite:
            details.append(f"nonfinite={nonfinite}")
        raise MissingSaturationIndexError(
            "mineral saturation-index calculation incomplete: " + "; ".join(details)
        )
    return np.asarray([float(si_by_phase[name]) for name in mineral_names], dtype=float)


def _correct_srb_sulfur_mass_basis(processes, components):
    """Convert SRB sulfur yields to an exact kg-S/electron-equivalent basis.

    S_IS was made measured_as='S' (sulfur basis) locally, but the installed _madm1.tsv process table
    kept COD-basis S_SO4/S_IS coefficients (~0.95). Sulfate-to-sulfide accepts
    eight mol e-/mol S and one kg COD represents 0.125 kmol e-. Derive the
    sulfur transfer from each row's actual net COD consumption rather than
    halving a rounded table coefficient. This closes both sulfur and electron
    equivalents for every SRB process.
    """
    so4_idx = components.index('S_SO4')
    sulfide_idx = components.index('S_IS')
    cod_indices = tuple(
        index
        for index, component in enumerate(components)
        if str(getattr(component, 'measured_as', '') or '') == 'COD'
    )
    kg_s_per_kg_cod = 0.125 * S_mw / 8.0
    for process_id in _SRB_SULFUR_PROCESSES:
        process = getattr(processes, process_id)
        # Rows are still symbolic here (yield parameters are substituted only
        # after compilation), so preserve the expression instead of forcing a
        # premature float conversion.
        net_cod_kg_per_reference = sum(
            process._stoichiometry[index] for index in cod_indices
        )
        sulfur_transfer = -net_cod_kg_per_reference * kg_s_per_kg_cod
        process._stoichiometry[so4_idx] = -sulfur_transfer
        process._stoichiometry[sulfide_idx] = sulfur_transfer


def _correct_hfo_reduction_mass_basis(processes, components):
    """Put HFO reduction on an explicit kg-Fe reference and finite donor basis.

    The upstream rows reference H2 or sulfide. After S_IS was converted to kg S,
    the symbolic sulfide row collapsed to ``S_IS -> S_S0`` and consumed no HFO
    while producing no Fe(II). The H2 row also paired one kg COD with one kg Fe,
    which violates electron equivalents. Rates 34:38 are calculated as kg Fe
    reduced/m3/d, so the process reference and stoichiometry must use kg Fe.

    Per mole H2 or HS-, two electron equivalents reduce two moles Fe(III).
    Protons/water remain in the PCM acid-base reservoir; the tracked donor,
    Fe, and S inventories close explicitly here.
    """

    fe_per_kmol = 2.0 * Fe_mw
    definitions = (
        (
            'reduction_HFO_H_h2', 'X_HFO_H',
            {'S_h2': -H2_COD_KG_PER_KMOL / fe_per_kmol,
             'S_Fe2': 1.0, 'X_HFO_H': -1.0},
        ),
        (
            'reduction_HFO_L_h2', 'X_HFO_L',
            {'S_h2': -H2_COD_KG_PER_KMOL / fe_per_kmol,
             'S_Fe2': 1.0, 'X_HFO_L': -1.0},
        ),
        (
            'reduction_HFO_H_IS', 'X_HFO_H',
            {'S_IS': -S_KG_PER_KMOL / fe_per_kmol,
             'S_S0': S_KG_PER_KMOL / fe_per_kmol,
             'S_Fe2': 1.0, 'X_HFO_H': -1.0},
        ),
        (
            'reduction_HFO_L_IS', 'X_HFO_L',
            {'S_IS': -S_KG_PER_KMOL / fe_per_kmol,
             'S_S0': S_KG_PER_KMOL / fe_per_kmol,
             'S_Fe2': 1.0, 'X_HFO_L': -1.0},
        ),
    )
    for process_id, reference_id, reaction in definitions:
        process = getattr(processes, process_id)
        row = [0.0] * len(components)
        for component_id, coefficient in reaction.items():
            row[components.index(component_id)] = coefficient
        process._reaction = dict(reaction)
        process._stoichiometry = row
        process._ref_component = reference_id
        process._conserved_for = ()


def _add_hfo_host_reduction_processes(processes, components):
    'Add finite-donor destruction rows for bound and aged HFO hosts.'

    fe_per_kmol = 2.0 * Fe_mw
    for process_id, host_id, donor_id, _history_class in _HFO_HOST_REDUCTION_SPECS:
        reaction = {
            host_id: -1.0,
            'S_Fe2': 1.0,
        }
        p_per_fe = float(getattr(components[host_id], 'i_P', 0.0) or 0.0)
        if p_per_fe:
            reaction['S_IP'] = p_per_fe
        if donor_id == 'h2':
            reaction['S_h2'] = -H2_COD_KG_PER_KMOL / fe_per_kmol
        elif donor_id == 'sulfide':
            reaction.update({
                'S_IS': -S_KG_PER_KMOL / fe_per_kmol,
                'S_S0': S_KG_PER_KMOL / fe_per_kmol,
            })
        else:  # pragma: no cover - immutable module definition guard
            raise RuntimeError(f'unknown HFO reduction donor {donor_id!r}')

        process = Process(
            process_id,
            reaction=reaction,
            ref_component=host_id,
            components=components,
            conserved_for=(),
            parameters=(),
        )
        # Keep the already basis-resolved coefficients verbatim.  In
        # particular, do not ask QSDsan to reinterpret kg-P/kg-Fe through a
        # molecular pseudo-formula after construction.
        row = [0.0] * len(components)
        for component_id, coefficient in reaction.items():
            row[components.index(component_id)] = coefficient
        process._reaction = dict(reaction)
        process._stoichiometry = row
        process._ref_component = host_id
        process._conserved_for = ()
        processes.append(process)


def _organic_sulfur_flag(value):
    '''The organic-sulfur opt-in, fail-closed: only ``True`` compiles the extension, ``None``/``False`` leave the
    model as it was; anything else (a string, a 1) is refused rather than guessed.'''
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise TypeError(f"organic_sulfur must be True or False, got {value!r}")


def _soluble_inert_uptake_flag(value):
    '''The kernel's acidogenic inert-soluble-product opt-in, fail-closed like the organic-sulfur flag: only ``True``
    compiles it, ``None``/``False`` leave the model's rows as the TSV states them; anything else is refused.'''
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise TypeError(f"soluble_inert_uptake must be True or False, got {value!r}")


#: The two uptake rows that carry the inert soluble product, each with its growth yield (the kernel names the rows).
_SOLUBLE_INERT_UPTAKE_YIELDS = {'uptake_sugars': 'Y_su', 'uptake_amino_acids': 'Y_aa'}


def _add_soluble_inert_uptake_product(processes):
    '''Give sugar and amino-acid uptake the kernel's inert soluble product (``SOLUBLE_INERT_UPTAKE``).

    Per kg COD taken up: S_I += Y_sI_upt, and every product the TSV states as ``(1-Y)*f`` becomes ``(1-Y-Y_sI_upt)*f``,
    so COD closes; the C, N and P carriers (the TSV's ``?``) are re-closed by QSDsan from the component contents when
    the reaction is reassigned. The number of rewritten products must equal the kernel row's, and the TSV must not
    already state S_I: a changed table is refused, never half-patched. tests/test_madm1_shared_biokinetics.py holds
    the compiled rows equal to the kernel's matrix.
    '''
    inert = SOLUBLE_INERT_UPTAKE.name
    rows = {p.process_id: p for p in MADM1_BIOKINETICS_KERNEL.processes}
    if set(_SOLUBLE_INERT_UPTAKE_YIELDS) != set(SOLUBLE_INERT_UPTAKE.processes):
        raise RuntimeError(f"the kernel puts {inert} on {SOLUBLE_INERT_UPTAKE.processes}; the twin binds "
                           f"{sorted(_SOLUBLE_INERT_UPTAKE_YIELDS)}")
    for process_id, yield_name in _SOLUBLE_INERT_UPTAKE_YIELDS.items():
        process = getattr(processes, process_id)
        kernel_row = rows[process_id]
        expected = sorted(c for c in kernel_row.stoichiometry
                          if c not in (kernel_row.reference_component, 'S_I') and c.startswith('S_'))
        reaction = dict(process._reaction)
        if 'S_I' in reaction:
            raise RuntimeError(f"{process_id}: the TSV already states S_I ({reaction['S_I']!r}); refusing to add it")
        old, new = f'(1-{yield_name})', f'(1-{yield_name}-{inert})'
        rewritten = []
        for cid, coefficient in reaction.items():
            text = str(coefficient).replace(' ', '')
            if old in text:
                reaction[cid] = text.replace(old, new)
                rewritten.append(cid)
        if sorted(rewritten) != expected:
            raise RuntimeError(f"{process_id}: rewrote {sorted(rewritten)}, the kernel row's products are {expected}")
        reaction['S_I'] = inert
        process.reaction = reaction


def _first_order_rate_component(process):
    '''The component an extension rate is first order in, read FROM the kernel's expression: it must be exactly
    ``ORGANIC_SULFUR_RATE_CONSTANT x <component>``. Any other shape is refused, so a changed kernel rate law cannot be
    evaluated here as something it no longer is.'''
    rate = process.rate_base
    names = [getattr(v, 'name', None) for v in getattr(rate, 'values', ())]
    if (getattr(rate, 'op', None) != 'multiply' or len(names) != 2 or None in names
            or names.count(ORGANIC_SULFUR_RATE_CONSTANT) != 1):
        raise RuntimeError(f"{process.process_id}: the twin evaluates only "
                           f"'{ORGANIC_SULFUR_RATE_CONSTANT} x component'; the kernel says {rate!r}")
    names.remove(ORGANIC_SULFUR_RATE_CONSTANT)
    return names[0]


def _organic_sulfur_components(sulfide_i_cod):
    '''The opt-in organic-sulfur extension's components, built from the extension's own contents: particulate,
    measured as S, no C/N/P/K/Mg. Its COD is the sulfide's per kg S in THIS engine's convention (``sulfide_i_cod``,
    S_IS's i_COD: H2S at S(-II), 2 kg O2/kg S), so process 75 (X_Sorg -> S_IS) conserves COD in the twin. Organic S is
    held at S(-II), protein S (cysteine, methionine); the sulfate-ester share (S(+VI)) is a stated limitation of the
    [ASSUMED] treatment (coordinator 2026-10-10). The shared kernel and WaterTAP carry sulfide and X_Sorg at no COD:
    a pre-existing cross-engine convention difference, named on the digester ModelCard.'''
    built = []
    for content in MADM1_ORGANIC_SULFUR_EXTENSION.contents:
        cmp = Component(content.component_id, formula='S', measured_as=content.measured_as,
                        description='Particulate organic sulfur (protein S + sulfate esters), released to '
                                    'sulfide on hydrolysis (madm1_organic_sulfur extension)',
                        particle_size='Particulate', degradability='Slowly', organic=True)
        for element in ('C', 'N', 'P', 'K', 'Mg'):
            setattr(cmp, f'i_{element}', content.content(element))
        cmp.i_COD = float(sulfide_i_cod)
        built.append(cmp)
    return built


def _microaeration_flag(value, organic_sulfur):
    '''The micro-aeration opt-in (fail-closed, as ``_organic_sulfur_flag``). The kernel's extension stacks on the
    organic-sulfur extension (its indices run on from 76; a feed with no X_Sorg leaves process 75 at zero), so asking
    for it without organic sulfur is refused rather than implied.'''
    requested = _microaeration.microaeration_flag(value)
    if requested and not _organic_sulfur_flag(organic_sulfur):
        raise ValueError("microaeration=True stacks on the organic-sulfur extension: pass organic_sulfur=True too "
                         "(a feed with no X_Sorg leaves process 75 at zero)")
    return requested


def create_madm1_cmps(set_thermo=True, ASF_L=0.31, ASF_H=1.2, organic_sulfur=False, microaeration=False):
    'Create a set of components for the modified ADM1.'

    # the opt-ins are read first, so a refused request builds nothing
    organic_sulfur = _organic_sulfur_flag(organic_sulfur)
    microaeration = _microaeration_flag(microaeration, organic_sulfur)

    # Components from the original ADM1
    # *********************************
    _cmps = create_adm1_cmps(False)
    S_aa = _cmps.S_aa
    X_pr = _cmps.X_pr
    S_aa.i_C = X_pr.i_C = 0.36890
    S_aa.i_N = X_pr.i_N = 0.11065
    S_aa.i_P = X_pr.i_P = 0.
    S_aa.i_mass = X_pr.i_mass = 1/1.35566

    S_fa = _cmps.S_fa
    S_fa.formula = 'C25H52O3'
    S_bu = _cmps.S_bu
    S_bu.formula = 'C4H8O2'
    S_pro = _cmps.S_pro
    S_pro.formula = 'C3H6O2'
    S_ac = _cmps.S_ac
    S_ac.formula = 'C2H4O2'

    S_I = _cmps.S_I
    X_I = _cmps.X_I
    S_I.i_C = X_I.i_C = 0.36178
    S_I.i_N = X_I.i_N = 0.06003
    S_I.i_P = X_I.i_P = 0.00649
    S_I.i_mass = X_I.i_mass = 1/1.54100

    X_ch = _cmps.X_ch
    X_ch.formula = 'C24H48O24'
    # _cmps.X_li.formula = 'C64H119O7.5P'
    X_li = X_pr.copy('X_li')
    X_li.i_C = 0.26311
    X_li.i_N = 0.
    X_li.i_P = 0.01067
    X_li.i_mass = 1/2.81254

    adm1_biomass = (_cmps.X_su, _cmps.X_aa, _cmps.X_fa, _cmps.X_c4, _cmps.X_pro, _cmps.X_ac, _cmps.X_h2)
    for bio in adm1_biomass:
        # bio.formula = 'C5H7O2NP0.113'
        bio.i_C = 0.36612
        bio.i_N = 0.08615
        bio.i_P = 0.02154
        bio.i_mass = 1/1.39300

    # P related components from ASM2d
    # *******************************
    asm_cmps = pc.create_asm2d_cmps(False)
    X_PHA = asm_cmps.X_PHA
    X_PHA.formula = '(C2H4O)n'
    # X_PHA.i_C = 0.3
    # X_PHA.i_mass = 0.55

    X_PAO = _cmps.X_su.copy('X_PAO')
    X_PAO.description = 'Phosphorus-accumulating organism biomass'

    # Additional components for P, S, Fe extensions
    # *********************************************
    S_IP = asm_cmps.S_PO4.copy('S_IP')

    ion_properties = dict(
        particle_size='Soluble',
        degradability='Undegradable',
        organic=False)
    S_K = Component.from_chemical('S_K', chemical='K+', description='Potassium ion',
                                  measured_as='K', **ion_properties)
    S_Mg = Component.from_chemical('S_Mg', chemical='Mg2+', description='Magnesium ion',
                                  measured_as='Mg',**ion_properties)
    S_SO4 = Component.from_chemical('S_SO4', chemical='SO4-2', description='Sulfate',
                                  measured_as='S', **ion_properties)
    # FIX #2b: S_IS must be measured_as='S' for proper chemistry/speciation (per Codex analysis 2025-10-24)
    # CRITICAL: COD basis is wrong for equilibrium constants and Henry's law
    # H2S/HS- speciation and gas transfer require true chemical (sulfur) basis
    S_IS = Component.from_chemical('S_IS', chemical='H2S',
                                  description='Hydrogen sulfide',
                                  measured_as='S',  # FIXED from 'COD' - needed for proper speciation
                                  particle_size='Soluble',
                                  degradability='Undegradable',
                                  organic=False)

    X_hSRB = X_PAO.copy('X_hSRB')
    X_hSRB.description = 'sulfate-reducing biomass, utilizing H2'
    X_aSRB = X_PAO.copy('X_aSRB')
    X_aSRB.description = 'sulfate-reducing biomass, utilizing acetate'
    X_pSRB = X_PAO.copy('X_pSRB')
    X_pSRB.description = 'sulfate-reducing biomass, utilizing propionate'
    X_c4SRB = X_PAO.copy('X_c4SRB')
    X_c4SRB.description = 'sulfate-reducing biomass, utilizing butyrate and valerate'

    # FIX #6: S_S0 must be measured_as='S' for proper chemistry
    # Elemental sulfur should NOT be counted as COD
    S_S0 = Component.from_chemical('S_S0', chemical='S',
                                  description='Elemental sulfur',
                                  measured_as='S',  # FIXED from 'COD'
                                  particle_size='Soluble',
                                  degradability='Undegradable',
                                  organic=False)
    S_Fe3 = Component.from_chemical('S_Fe3', chemical='Fe3+', description='Iron (III)',
                                  measured_as='Fe',**ion_properties)
    S_Fe2 = Component.from_chemical('S_Fe2', chemical='Fe2+', description='Iron (II)',
                                  measured_as='Fe',**ion_properties)
    # FIX #5: DO NOT count Fe2+ redox O2 demand as COD
    # Keep S_Fe2 on iron basis for correct COD accounting
    # If needed, compute redox O2 demand separately in reporting
    # REMOVED: S_Fe2.i_COD = 0.5*O_mw/Fe_mw
    # REMOVED: S_Fe2.measured_as = 'COD'

    # Multiple mineral precipitation
    # ******************************
    mineral_properties = dict(
        particle_size='Particulate',
        degradability='Undegradable',
        organic=False)

    X_HFO_H = Component('X_HFO_H', formula='FeO(OH)',
                        description='Hydrous ferric oxide with high number of active sites',
                        measured_as='Fe',**mineral_properties)
    X_HFO_L = X_HFO_H.copy('X_HFO_L')
    X_HFO_L.description = 'Hydrous ferric oxide with low number of active sites'

    X_HFO_old = X_HFO_H.copy('X_HFO_old')
    X_HFO_old.description = 'Inactive hydrous ferric oxide'

    X_HFO_HP = Component('X_HFO_HP', formula=f'FeO(OH)P{ASF_H}',
                         description='X_HFO_H with phosphorus-bounded adsorption sites',
                         measured_as='Fe', **mineral_properties)
    X_HFO_HP_old = X_HFO_HP.copy('X_HFO_HP_old')
    X_HFO_HP_old.description = 'Old ' + X_HFO_HP.description

    X_HFO_LP = Component('X_HFO_LP', formula=f'FeO(OH)P{ASF_L}',
                         description='X_HFO_L with phosphorus-bounded adsorption sites',
                         measured_as='Fe', **mineral_properties)
    X_HFO_LP_old = X_HFO_LP.copy('X_HFO_LP_old')
    X_HFO_LP_old.description = 'Old ' + X_HFO_LP.description

    X_CCM = Component.from_chemical('X_CCM', chemical='calcite', description='Calcite', **mineral_properties)
    # X_ACC is aragonite (CaCO3 polymorph, same formula as calcite)
    # Use direct Component creation to avoid CAS alias collision (both have CAS 471-34-1)
    X_ACC = Component('X_ACC', formula='CaCO3', description='Aragonite', **mineral_properties)
    X_ACP = Component.from_chemical('X_ACP', chemical='Ca3(PO4)2', description='Amorphous calcium phosphate', **mineral_properties)
    X_HAP = Component.from_chemical('X_HAP', chemical='hydroxylapatite', description='Hydroxylapatite', **mineral_properties)
    X_DCPD = Component.from_chemical('X_DCPD', chemical='CaHPO4', description='Dicalcium phosphate', **mineral_properties)
    X_OCP = Component('X_OCP', formula='Ca4HP3O12', description='Octacalcium phosphate', **mineral_properties)
    X_struv = Component.from_chemical('X_struv', chemical='MgNH4PO4', description='Struvite', **mineral_properties)
    X_newb = Component.from_chemical('X_newb', chemical='MgHPO4', description='Newberyite', **mineral_properties)
    X_magn = Component.from_chemical('X_magn', chemical='MgCO3', description='Magnesite', **mineral_properties)
    X_kstruv = Component('X_kstruv', formula='MgKPO4', description='K-struvite', **mineral_properties)
    X_FeS = Component.from_chemical(
        'X_FeS', chemical='FeS', description='Amorphous iron sulfide', **mineral_properties)
    # Keep amorphous FeS and mackinawite as distinct kinetic inventories.  They
    # have the same formula but different thermodynamic constants and histories.
    X_Mackinawite = X_FeS.copy('X_Mackinawite')
    X_Mackinawite.description = 'Mackinawite iron sulfide'
    # ThermoSteam parses ``Fe3(PO4)2`` incorrectly for this pseudo-component
    # (one P atom).  The parser-safe empirical formula preserves 3 Fe, 2 P and
    # 8 O atoms.  Crystal waters are excluded from the dry-solids mass basis.
    X_Fe3PO42 = Component(
        'X_Fe3PO42', formula='Fe3P2O8', description='Vivianite (dry formula basis)',
        **mineral_properties)
    X_Siderite = Component(
        'X_Siderite', formula='FeCO3', description='Siderite', **mineral_properties)
    X_AlPO4 = Component.from_chemical('X_AlPO4', chemical='AlPO4', description='Aluminum phosphate', **mineral_properties)

    S_Ca = Component.from_chemical('S_Ca', chemical='Ca2+', description='Calsium ion',
                                   measured_as='Ca', **ion_properties)
    S_Al = Component.from_chemical('S_Al', chemical='Al3+', description='Aluminum ion',
                                   measured_as='Al', **ion_properties)
    S_Na = Component.from_chemical('S_Na', chemical='Na+', description='Sodium ion',
                                   measured_as='Na', **ion_properties)
    S_Cl = Component.from_chemical('S_Cl', chemical='Cl-', description='Chloride',
                                   measured_as='Cl', **ion_properties)

    # Trace micronutrient metals (QSDsan-only): conservative soluble tracers that drive the
    # trace-element growth-limitation factor in rhos_madm1. measured_as the element so the
    # mass->mol conversion uses the atomic weight; elemental chemical basis keeps thermo
    # resolution robust (the species identity is immaterial for a conservative, undegraded tracer).
    S_Co = Component.from_chemical('S_Co', chemical='Co', description='Cobalt (trace micronutrient)',
                                   measured_as='Co', **ion_properties)
    S_Ni = Component.from_chemical('S_Ni', chemical='Ni', description='Nickel (trace micronutrient)',
                                   measured_as='Ni', **ion_properties)
    S_Se = Component.from_chemical('S_Se', chemical='Se', description='Selenium (trace; Se/W/Mo formate-dehydrogenase cofactor proxy)',
                                   measured_as='Se', **ion_properties)

    cmps_madm1 = Components([_cmps.S_su, S_aa, S_fa, _cmps.S_va, S_bu,
                             S_pro, S_ac, _cmps.S_h2, _cmps.S_ch4,
                             _cmps.S_IC, _cmps.S_IN, S_IP, S_I,
                             X_ch, X_pr, X_li, *adm1_biomass, X_I,
                             X_PHA, asm_cmps.X_PP, X_PAO, S_K, S_Mg,
                             S_SO4, S_IS, X_hSRB, X_aSRB, X_pSRB, X_c4SRB,
                             S_S0, S_Fe3, S_Fe2, X_HFO_H, X_HFO_L, X_HFO_old,
                             X_HFO_HP, X_HFO_LP, X_HFO_HP_old, X_HFO_LP_old,
                             S_Ca, S_Al, X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD,
                             X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS,
                             X_Mackinawite, X_Fe3PO42, X_Siderite, X_AlPO4,
                             S_Na, S_Cl, S_Co, S_Ni, S_Se,
                             *(_organic_sulfur_components(S_IS.i_COD) if organic_sulfur else ()),
                             *(_microaeration.microaeration_components() if microaeration else ()),
                             _cmps.H2O])

    # ============================================================================
    # CRITICAL FIX: Disable NOD for COD-basis pseudo-components
    # ============================================================================
    # Per upstream ADM1-P pattern (QSDsan/qsdsan/processes/_adm1_p_extension.py)
    # COD surrogates must have i_NOD = None to prevent nitrogen oxygen demand
    # from inflating .COD composite calculations.
    #
    # Without this fix, components with i_N > 0 (amino acids, proteins, biomass)
    # cause .COD to include NOD, over-stating COD_removed by ~13% (472 kg COD/d
    # in our 1000 m³/d, 4890 mg/L COD sludge scenario).
    #
    # Affected components (all have i_N > 0):
    # - S_aa, X_pr: i_N = 0.11065 (11% nitrogen)
    # - Biomass (X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2): i_N = 0.08615
    # - S_I, X_I: i_N = 0.06003
    # - Extension biomass (X_PAO, X_hSRB, X_aSRB, X_pSRB, X_c4SRB): inherit i_N
    #
    # Reference: QSD-Group/QSDsan _adm1_p_extension.py lines ~180-220
    # ============================================================================

    # Disable NOD for COD surrogates with nitrogen content
    for cmp in (S_aa, X_pr, S_fa, S_I, X_I):
        cmp.i_NOD = None

    # Disable NOD for all ADM1 biomass groups
    for bio in adm1_biomass:  # (X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2)
        bio.i_NOD = None

    # Disable NOD for extension biomass (inherit from X_PAO copy)
    for bio_ext in (X_PAO, X_hSRB, X_aSRB, X_pSRB, X_c4SRB):
        bio_ext.i_NOD = None

    # VFAs and carbohydrates don't have nitrogen, but disable for consistency
    for vfa in (S_bu, S_pro, S_ac):
        vfa.i_NOD = None
    for carb in (X_ch, X_li):
        carb.i_NOD = None

    # Storage polymer X_PHA (no nitrogen)
    X_PHA.i_NOD = None

    # Following upstream QSDsan pattern: use flags to synthesize surrogate thermodynamic properties
    # for COD-based components instead of blocking validation
    cmps_madm1.default_compile(ignore_inaccurate_molar_weight=True, adjust_MW_to_measured_as=True)

    if set_thermo: qs.set_thermo(cmps_madm1)
    return cmps_madm1

#%% rate functions

# https://wiki.dynamita.com/en/biokinetic_process_models#chemical-phosphorus-removal-with-metal-salts-addition-iron-or-aluminium

# =============================================================================
# state_variable_indices = {
#     'S_su': 0, 'S_aa': 1, 'S_fa': 2, 'S_va': 3, 'S_bu': 4, 'S_pro': 5, 'S_ac': 6, 'S_h2': 7,
#     'S_ch4': 8, 'S_IC': 9, 'S_IN': 10, 'S_IP': 11, 'S_I': 12,
#     'X_ch': 13, 'X_pr': 14, 'X_li': 15,
#     'X_su': 16, 'X_aa': 17, 'X_fa': 18, 'X_c4': 19, 'X_pro': 20, 'X_ac': 21, 'X_h2': 22, 'X_I': 23,
#     'X_PHA': 24, 'X_PP': 25, 'X_PAO': 26, 'S_K': 27, 'S_Mg': 28,
#     'S_SO4': 29, 'S_IS': 30, 'X_hSRB': 31, 'X_aSRB': 32, 'X_pSRB': 33, 'X_c4SRB': 34,
#     'S_S0': 35, 'S_Fe3': 36, 'S_Fe2': 37,
#     'X_HFO_H': 38, 'X_HFO_L': 39, 'X_HFO_old': 40, 'X_HFO_HP': 41, 'X_HFO_LP': 42, 'X_HFO_HP_old': 43, 'X_HFO_LP_old': 44,
#     'S_Ca': 45, 'S_Al': 46,
#     'X_CCM': 47, 'X_ACC': 48, 'X_ACP': 49, 'X_HAP': 50, 'X_DCPD': 51, 'X_OCP': 52,
#     'X_struv': 53, 'X_newb': 54, 'X_magn': 55, 'X_kstruv': 56,
#     'X_FeS': 57, 'X_Fe3PO42': 58,
#     'X_AlPO4': 59,
#     'S_Na': 60, 'S_Cl': 61,
#     'S_Co': 62, 'S_Ni': 63, 'S_Se': 64,   # trace micronutrient metals (conservative)
#     'H2O': 65
#     }
# =============================================================================

def calc_biogas(state_arr, params, pH, log_gamma1=0.0):
    """
    Calculate dissolved molecular H2S concentration.

    Uses correct Henderson-Hasselbalch neutral fraction to compute H2S from total sulfide.

    Parameters
    ----------
    state_arr : ndarray
        State variable array
    params : dict
        Kinetic parameters (must include Ka_h2s for temperature-corrected equilibrium
        and 'components' for unit conversion)
    pH : float
        Computed pH from PCM

    Returns
    -------
    float
        Dissolved molecular H2S concentration [kmol-S/m³]

    Notes
    -----
    H2S speciation: H2S <-> HS- + H+, pKa1 ~ 7.0 at 25°C
    Neutral fraction: α0 = 1 / (1 + 10^(pH - pKa))

    CRITICAL FIXES (per Codex reviews):
    - Previous formula `S_IS * 10^(pKa - pH)` overpredicts by factor of 2 at pH ≈ pKa
    - Codex fix #7: Uses temperature-corrected Ka_h2s from params for consistency
    - Codex fix #8: Convert S_IS to molar units before applying α0 (was bloating H2S)
    - Codex fix #9: Use dynamic component indexing instead of hard-coded index 30
    """
    # Codex fix #9: Get S_IS index dynamically from component set
    cmps = params['components']
    is_idx = cmps.index('S_IS')

    # Extract total sulfide (S_IS) in kg/m³
    S_IS_kg = state_arr[is_idx] if len(state_arr) > is_idx else 0.0

    # FIX #2a: Use SAME unit conversion as rest of rate function (per Codex analysis 2025-10-24)
    # CRITICAL: Must match unit_conversion at line 819 in rhos_madm1
    # The inconsistent i_mass/chem_MW was causing H2S to be on wrong scale
    # FIX: Remove extra 1e3 factor - mass2mol_conversion already converts kg/m³ to mol/L
    unit_conversion = madm1_mass2mol(cmps)  # kg/m³ -> mol/L (NOT kmol/m³!)
    S_IS_M = S_IS_kg * unit_conversion[is_idx]

    # Codex fix #7: Get temperature-corrected Ka_h2s from params
    # If not available, fallback to 25°C value
    Ka_h2s = params.get('Ka_h2s', 1e-7)
    pKa_h2s = -np.log10(Ka_h2s)

    # Neutral (molecular) fraction with Davies activities (pH = -log10 a_H; HS- carries gamma_1, H2S none):
    # [HS-]/[H2S] = Ka / (a_H gamma_1) = 10^(pH - pKa - log10 gamma_1). log_gamma1 = 0 is the ideal limit.
    alpha_0 = 1.0 / (1.0 + 10**(pH - pKa_h2s - log_gamma1))

    # Dissolved molecular H2S in kmol/m³ (correct molar units)
    h2s = S_IS_M * alpha_0

    return h2s

def _compute_lumped_ions(state_liquid, cmps, unit_conversion):
    """
    Compute lumped S_cat/S_an from measured ion concentrations in mADM1 state.

    CRITICAL FIX (per Codex review):
    mADM1 explicitly models Na⁺ (S_Na) and Cl⁻ (S_Cl) as state variables.
    Use these actual values instead of hard-coded constants to ensure charge
    balance responds correctly to influent salinity changes.

    Also aggregate ALL multivalent cations (Mg²⁺, Ca²⁺, Fe²⁺, Fe³⁺, Al³⁺) for
    accurate positive charge accounting in iron/alum dosing scenarios.

    Parameters
    ----------
    state_liquid : ndarray
        Liquid-phase state vector (first n_cmps entries) [kg/m³]
    cmps : Components
        mADM1 component set
    unit_conversion : ndarray
        Conversion factors from kg/m³ to M

    Returns
    -------
    tuple
        (S_cat, S_divalent, S_trivalent, S_an) in M
        where S_cat = Na⁺, S_divalent = Mg²⁺ + Ca²⁺ + Fe²⁺,
        S_trivalent = Fe³⁺ + Al³⁺, S_an = Cl⁻
    """
    # Extract measured monovalent ions from state (Codex fix #3)
    S_Na = state_liquid[cmps.index('S_Na')] if 'S_Na' in cmps.IDs else 0.0
    S_Cl = state_liquid[cmps.index('S_Cl')] if 'S_Cl' in cmps.IDs else 0.0

    # Extract divalent cations (Codex fix #4)
    S_Mg = state_liquid[cmps.index('S_Mg')] if 'S_Mg' in cmps.IDs else 0.0
    S_Ca = state_liquid[cmps.index('S_Ca')] if 'S_Ca' in cmps.IDs else 0.0
    S_Fe2 = state_liquid[cmps.index('S_Fe2')] if 'S_Fe2' in cmps.IDs else 0.0

    # Extract trivalent cations (Codex fix #6: for iron/alum dosing)
    S_Fe3 = state_liquid[cmps.index('S_Fe3')] if 'S_Fe3' in cmps.IDs else 0.0
    S_Al = state_liquid[cmps.index('S_Al')] if 'S_Al' in cmps.IDs else 0.0

    # Convert to molar concentrations
    S_cat_M = S_Na * unit_conversion[cmps.index('S_Na')] if 'S_Na' in cmps.IDs else 0.0
    S_an_M = S_Cl * unit_conversion[cmps.index('S_Cl')] if 'S_Cl' in cmps.IDs else 0.0

    # Aggregate divalents: Mg²⁺ + Ca²⁺ + Fe²⁺ (all contribute 2× charge)
    S_divalent_M = 0.0
    if 'S_Mg' in cmps.IDs:
        S_divalent_M += S_Mg * unit_conversion[cmps.index('S_Mg')]
    if 'S_Ca' in cmps.IDs:
        S_divalent_M += S_Ca * unit_conversion[cmps.index('S_Ca')]
    if 'S_Fe2' in cmps.IDs:
        S_divalent_M += S_Fe2 * unit_conversion[cmps.index('S_Fe2')]

    # Aggregate trivalents: Fe³⁺ + Al³⁺ (all contribute 3× charge)
    # Codex fix #6: Prevents pH bias during iron/alum dosing campaigns
    S_trivalent_M = 0.0
    if 'S_Fe3' in cmps.IDs:
        S_trivalent_M += S_Fe3 * unit_conversion[cmps.index('S_Fe3')]
    if 'S_Al' in cmps.IDs:
        S_trivalent_M += S_Al * unit_conversion[cmps.index('S_Al')]

    return S_cat_M, S_divalent_M, S_trivalent_M, S_an_M


def _temperature_corrected_acid_constants(params):
    """Base-system (Kw, NH4, CO2, ac, pro, bu, va), H2S and H2PO4 acid constants at T_op, from the shared speciation
    constants (van't Hoff with the kernel's R_gas; process_chemistry_reference.madm1_speciation.acid_constants)."""
    from puran_mcp_support.chemistry.madm1_speciation import acid_constants
    pk = acid_constants(params.get('T_op', params['T_base']), _speciation_values(params))
    Ka = np.array([10.0**-pk[acid] for acid in _BASE_ACIDS])
    return Ka, 10.0**-pk['h2s'], 10.0**-pk['h2po4']


#: The water inventory the molal basis reads (kg water per m3): the WaterTAP property package's constant
#: (``ModifiedADM1ParameterBlock.dens_mass``), so both engines speciate on ONE water basis. The twin's H2O state is not

#: carries no H2O (tests/test_madm1_inhibition_ext.py's commissioning feed), which put the molal basis 5e7x off and

DEFAULT_WATER_KG_M3 = 997.0


def _davies_liquor(state_liquid, cmps, unit_conversion, mr):
    """The liquor's molar totals on this model's own basis (madm1_mass2mol): explicit strong ions, weak-acid totals."""
    def molar(cid):
        if cid not in cmps.IDs:
            return 0.0
        idx = cmps.index(cid)
        return float(state_liquid[idx]) * float(unit_conversion[idx])

    na, k, cl, so4 = molar('S_Na'), molar('S_K'), molar('S_Cl'), molar('S_SO4')
    divalent = molar('S_Mg') + molar('S_Ca') + molar('S_Fe2')
    trivalent = molar('S_Fe3') + molar('S_Al')
    return MadmLiquor(
        strong_charge_M=na + k + 2.0 * divalent + 3.0 * trivalent - cl - 2.0 * so4,
        strong_ionic_M=na + k + 4.0 * divalent + 9.0 * trivalent + cl + 4.0 * so4,
        IN=molar('S_IN'),
        # the reduced observer's exclude_sip_from_charge: an unassayed placeholder takes no part in the charge
        IP=0.0 if mr.get('exclude_sip_from_charge') else molar('S_IP'),
        IC=molar('S_IC'), IS=molar('S_IS'),
        ac=molar('S_ac'), pro=molar('S_pro'), bu=molar('S_bu'), va=molar('S_va'),
        # the pairing metals and sulfate as totals (they stay in the strong sums above; the shared solve's ION_PAIRS
        # take their complexes from them -- HOUSE-MADM1-SPECIATION-ION-PAIRS-FIRST-20261008)
        Ca=molar('S_Ca'), Mg=molar('S_Mg'), Fe2=molar('S_Fe2'), SO4=so4)


def _water_kg_m3(state_liquid, cmps):
    """The water inventory the molal basis reads: DEFAULT_WATER_KG_M3, WaterTAP's own (see its note)."""
    return DEFAULT_WATER_KG_M3


def pcm_speciation(state_arr, params):
    """The full Davies speciation of the liquid state (process_chemistry_reference.madm1_speciation): pH on the
    activity/molal basis, every species (mol/L), ionic strength, log10 gamma, the acid pKa at T_op."""
    cmps = params['components']
    T_op = params.get('T_op', params['T_base'])
    state_liquid = np.asarray(state_arr[:len(cmps)], dtype=float)
    unit_conversion = madm1_mass2mol(cmps)
    mr = params.get('model_reduction') or {}
    spec = solve_madm1_speciation(_davies_liquor(state_liquid, cmps, unit_conversion, mr), temperature_k=T_op,
                            water_kg_m3=_water_kg_m3(state_liquid, cmps), values=_speciation_values(params))
    params['Ka_h2s'] = 10.0**-spec['pKa']['h2s']
    params['Ka_h2po4'] = 10.0**-spec['pKa']['h2po4']
    return spec


def pcm(state_arr, params):
    """pH / free NH3 / dissolved CO2 of the liquid state, solved with Davies activities.

    Owner ruling HOUSE-MADM1-DAVIES-BOTH-ENGINES-20261007 (Hersh, 2026-10-07), verbatim answer: "Davies for both
    (Recommended)". The equations are the WaterTAP house Davies block's, evaluated by the shared
    ``process_chemistry_reference.madm1_speciation`` (tests/test_madm1_davies_speciation.py holds both engines to
    it): pH = -log10(a_H) on the molal basis of WaterTAP's 997 kg/m3 water inventory; Davies gamma for every ion; NH4/NH3,
    CO2/HCO3/CO3, H2PO4/HPO4, H2S/HS and the VFAs as activity equilibria; Na, K, Mg, Ca, Fe2, Fe3, Al, Cl and SO4
    fully dissociated. S_IP converts on elemental P (madm1_mass2mol). The former ideal solve omitted CO3-2 and
    took pH as -log10[H+].

    Returns ``(pH, nh3, co2, activities)``: free NH3 and CO2(aq) in mol/L; ``activities`` is the historical inert
    slot every caller unpacks and none reads (live SI work happens in thermodynamics.calc_saturation_indices).
    """
    spec = pcm_speciation(state_arr, params)
    species = spec['species_M']
    return spec['pH'], species['NH3'], species['CO2'], np.ones(13)


def total_alkalinity_meq_L(state_arr, params, pH=None):
    """Thermodynamic total alkalinity on the model's molar-equivalent basis (codex 2026-07-11 H5), from the same
    Davies speciation as pcm(): HCO3- + 2 CO3-2 + NH3 + HPO4-2 + VFA- + HS- + OH- - H+ (proton reference CO2,
    NH4+, H2PO4-, the acids, H2S, water). Phosphate counts only when it is part of the charge system (the reduced
    observer's exclude_sip_from_charge keeps it out of both). ``pH`` (activity) evaluates the species at that pH
    with the state's own activity coefficients; omitted, the state's solved pH."""
    spec = pcm_speciation(state_arr, params)
    species = spec['species_M']
    if pH is not None and pH != spec['pH']:
        cmps = params['components']
        state_liquid = np.asarray(state_arr[:len(cmps)], dtype=float)
        liquor = _davies_liquor(state_liquid, cmps, madm1_mass2mol(cmps), params.get('model_reduction') or {})
        species = species_at(float(pH), liquor, pk=spec['pKa'], log_gamma=spec['log_gamma'],
                             molar_to_molal=spec['molar_to_molal'])
    vfa = species['ac_ion'] + species['pro_ion'] + species['bu_ion'] + species['va_ion']
    # M equivalents/L x 1000 = meq/L; the ion pairs count by the ligand forms they hold, on the same proton reference
    return 1000.0 * (species['HCO3'] + 2.0 * species['CO3'] + species['NH3'] + species['HPO4'] + vfa
                     + species['HS'] + species['OH'] - species['H'] + pair_alkalinity_M(species))


def titration_alkalinity_mg_caco3_L(state_arr, params, endpoint_pH=4.30, start_pH=None):
    'Like-basis titration alkalinity to a strong-acid endpoint, as mg CaCO3/L — directly comparable\n    to a lab titration (unlike ``total_alkalinity_meq_L``, which is the full ANC on the model basis).\n\n    Equals the acid required to move a filtered sample from its pH to ``endpoint_pH``, computed as the\n    difference in modelled acid-neutralising capacity between the two pH values. Reuses the same\n    speciation as the pH solve, so it honours the ``model_reduction`` flags (e.g. excludes unassayed P).'
    if start_pH is None:
        start_pH = pcm(state_arr, params)[0]
    # ANC(pH) in meq/L at each pH; the titre from start->endpoint is their difference. x50 -> mg CaCO3/L.
    anc_start = total_alkalinity_meq_L(state_arr, params, pH=start_pH)
    anc_end = total_alkalinity_meq_L(state_arr, params, pH=endpoint_pH)
    return 50.0 * (anc_start - anc_end)


def fostac_like_basis(state_arr, params, start_pH=None):
    "alkalinity = titre to pH 4.30; VA = titre pH 5.75 -> 4.30; FOS/TAC = VA / alkalinity. Comparable\n    directly to the plant's reported alk (~1055), VA (~245) and FOS/TAC (~0.23)."
    if start_pH is None:
        start_pH = pcm(state_arr, params)[0]
    alk = titration_alkalinity_mg_caco3_L(state_arr, params, endpoint_pH=4.30, start_pH=start_pH)
    # VA = acid consumed between pH 5.75 and 4.30 = ANC(5.75) - ANC(4.30), x50 -> mg CaCO3/L.
    va = 50.0 * (total_alkalinity_meq_L(state_arr, params, pH=5.75)
                 - total_alkalinity_meq_L(state_arr, params, pH=4.30))
    return alk, va, (va / alk if alk else None)


def _lab_prep_precipitate_to_equilibrium(state, params, cmps, T_lab_K, unit_conversion,
                                         phase_name, removals_kg_per_mol, si_tol=1e-3):
    """Remove x mol/L of ``phase_name``'s lattice from the given state components until its
    registry SI (at ``T_lab_K``) reaches 0, re-solving pH each trial. Returns the new state.
    ``removals_kg_per_mol`` maps component ID -> kg removed per mol phase (elemental MW basis)."""
    idx = {cid: cmps.index(cid) for cid in removals_kg_per_mol}
    x_max = min(state[i] / mw for cid, mw in removals_kg_per_mol.items()
                for i in (idx[cid],))

    def si_at(x):
        probe = state.copy()
        for cid, mw in removals_kg_per_mol.items():
            probe[idx[cid]] = max(probe[idx[cid]] - x * mw, 0.0)
        spec = pcm_speciation(probe, params)
        return calc_saturation_indices(probe, cmps, spec['pH'], T_lab_K, unit_conversion, speciation=spec,
                                       speciation_temperature_k=params.get('T_op', params['T_base'])).get(
            phase_name, 0.0), probe

    si0, _ = si_at(0.0)
    if si0 <= si_tol:
        return state
    si_hi, probe_hi = si_at(x_max)
    if si_hi > 0.0:          # even full removal stays supersaturated (degenerate) — take it
        return probe_hi
    lo, hi = 0.0, x_max
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        si_mid, probe_mid = si_at(mid)
        if si_mid > 0.0:
            lo = mid
        else:
            hi = mid
    return si_at(hi)[1]


def _lab_prep_state(state_arr, params, *, T_lab_K=303.15, prep_start_pH=8.75,
                    si_tol=1e-3, max_passes=3):
    """The PREPPED sample state (a copy) after simulating the plant lab prep:
    (1) strip S_IC at CONSTANT ANC (removing neutral CO2 cannot change ANC) until the
    pcm pH reaches the observed titration-start pH (~8.75); (2) equilibrate calcite
    (and struvite when its SI is positive AND S_IP participates in the charge system)
    against the REGISTRY Ksp at 30 C. Returns None when the state cannot reach the
    prep pH even CO2-free (fail-open to absent rather than fabricating a prep)."""
    cmps = params['components']
    state = np.array(state_arr[:len(cmps)], dtype=float)
    ic = cmps.index('S_IC')
    pH0 = pcm(state, params)[0]
    if not np.isfinite(pH0):
        return None
    if pH0 < prep_start_pH:
        probe = state.copy()
        probe[ic] = 1e-9
        if pcm(probe, params)[0] < prep_start_pH:
            return None          # even CO2-free the sample cannot reach the prep pH
        lo, hi = 1e-9, state[ic]  # pH is decreasing in S_IC
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            probe = state.copy()
            probe[ic] = mid
            if pcm(probe, params)[0] > prep_start_pH:
                lo = mid
            else:
                hi = mid
        state[ic] = 0.5 * (lo + hi)

    unit_conversion = madm1_mass2mol(cmps)
    mr = params.get('model_reduction') or {}
    _MW_CA, _MW_C, _MW_MG, _MW_N, _MW_P = 40.078, 12.011, 24.305, 14.007, 30.974
    t_spec = params.get('T_op', params['T_base'])
    for _ in range(max_passes):
        spec = pcm_speciation(state, params)
        sis = calc_saturation_indices(state, cmps, spec['pH'], T_lab_K, unit_conversion, speciation=spec,
                                      speciation_temperature_k=t_spec)
        moved = False
        if sis.get('CCM', 0.0) > si_tol:
            state = _lab_prep_precipitate_to_equilibrium(
                state, params, cmps, T_lab_K, unit_conversion, 'CCM',
                {'S_Ca': _MW_CA, 'S_IC': _MW_C}, si_tol=si_tol)
            moved = True
        if not mr.get('exclude_sip_from_charge'):
            spec = pcm_speciation(state, params)
            sis = calc_saturation_indices(state, cmps, spec['pH'], T_lab_K, unit_conversion, speciation=spec,
                                          speciation_temperature_k=t_spec)
            if sis.get('struv', 0.0) > si_tol:
                state = _lab_prep_precipitate_to_equilibrium(
                    state, params, cmps, T_lab_K, unit_conversion, 'struv',
                    {'S_Mg': _MW_MG, 'S_IN': _MW_N, 'S_IP': _MW_P}, si_tol=si_tol)
                moved = True
        if not moved:
            break
    return state


def lab_prep_alkalinity_mg_caco3_L(state_arr, params, *, T_lab_K=303.15,
                                   prep_start_pH=8.75, endpoint_pH=4.30,
                                   si_tol=1e-3, max_passes=3):
    'LAB-EQUIVALENT alkalinity: simulate the plant lab prep from the effluent state,'
    state = _lab_prep_state(state_arr, params, T_lab_K=T_lab_K,
                            prep_start_pH=prep_start_pH, si_tol=si_tol,
                            max_passes=max_passes)
    if state is None:
        return None
    return titration_alkalinity_mg_caco3_L(state, params, endpoint_pH=endpoint_pH)


def lab_prep_fostac_mg_caco3_L(state_arr, params, *, T_lab_K=303.15,
                               prep_start_pH=8.75, endpoint_pH=4.30,
                               si_tol=1e-3, max_passes=3):
    'The plant\'s reported "VA" (~245) is THIS titre, not a molecular VFA: in the\n    5.75->4.30 window residual bicarbonate titrates alongside the acids, so at low\n    true VFA the titre is carbonate-dominated. Compare measured VA against the VA\n    returned here, never against ``vfa_mg_HAc_L`` (virtual-assay v2, 2026-08-30 —\n    the same category discipline as alk_labprep vs alk_titre). OUTPUT-ONLY, same\n    contract as ``lab_prep_alkalinity_mg_caco3_L``.\n\n    Returns (alk, va, fostac) or (None, None, None) when the prep cannot run.'
    state = _lab_prep_state(state_arr, params, T_lab_K=T_lab_K,
                            prep_start_pH=prep_start_pH, si_tol=si_tol,
                            max_passes=max_passes)
    if state is None:
        return None, None, None
    alk = titration_alkalinity_mg_caco3_L(state, params, endpoint_pH=endpoint_pH)
    va = 50.0 * (total_alkalinity_meq_L(state, params, pH=5.75)
                 - total_alkalinity_meq_L(state, params, pH=endpoint_pH))
    return alk, va, (va / alk if alk else None)


# H2 tracking for Newton solver - required by AnaerobicCSTR
_H2_EPS = 1e-8

def dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in):
    """
    H2 mass balance residual for Newton solver.

    Computes the algebraic hydrogen balance:
    Q/V_liq*(S_h2_in - S_h2) + Σ(rhos * ν_H2)

    Parameters
    ----------
    S_h2 : float
        Current H2 concentration guess [kmol/m³]
    state_arr : ndarray
        State variable array
    h : float or None
        Proton concentration (if None, will be computed)
    params : dict
        Kinetic parameters
    f_stoichio : callable
        Stoichiometry function
    V_liq : float
        Liquid volume [m³]
    S_h2_in : float
        Influent H2 concentration [kmol/m³]

    Returns
    -------
    float
        Residual value for Newton iteration
    """
    cmps = params['components']
    h2_idx = cmps.index('S_h2')
    S_saved = state_arr[h2_idx]

    # Temporarily set H2 to current guess
    state_arr[h2_idx] = S_h2

    # Get T_op from params or use default
    T_op = params.get('T_op', params.get('T_base', 298.15))

    # Compute reaction rates with current H2 value
    rhos = rhos_madm1(state_arr, params, T_op, h=h)

    # Get H2 stoichiometry
    stoichio = f_stoichio(state_arr)

    # Get flow rate (last element of state array)
    Q = state_arr[-1]

    # Compute residual: accumulation + reaction
    residual = Q/V_liq * (S_h2_in - S_h2) + np.dot(rhos, stoichio)

    # Restore original H2 value
    state_arr[h2_idx] = S_saved

    return residual


def grad_dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps=_H2_EPS):
    """
    Gradient of H2 mass balance residual for Newton solver.

    Computes the derivative of the residual with respect to S_h2
    using numerical differentiation (central difference).

    Parameters
    ----------
    S_h2 : float
        Current H2 concentration guess [kmol/m³]
    state_arr : ndarray
        State variable array
    h : float or None
        Proton concentration (if None, will be computed)
    params : dict
        Kinetic parameters
    f_stoichio : callable
        Stoichiometry function
    V_liq : float
        Liquid volume [m³]
    S_h2_in : float
        Influent H2 concentration [kmol/m³]
    eps : float
        Perturbation for numerical differentiation

    Returns
    -------
    float
        Gradient (d_residual/d_S_h2) for Newton iteration
    """
    cmps = params['components']
    h2_idx = cmps.index('S_h2')
    S_saved = state_arr[h2_idx]

    # Adaptive epsilon based on magnitude. H2 is a TINY positive concentration; a backward perturbation
    # that drives S_h2 <= 0 makes rhos_madm1 non-physical (negative substrate) and poisons the gradient
    # (M2). Use a central difference only while the backward point stays strictly positive; otherwise a
    # one-sided FORWARD difference. Always restore the saved H2 (finally), even if rhos_madm1 raises.
    # Scale the perturbation to the H2 concentration itself.  The former
    # ``max(1, |S_h2|)`` floor forced eps=1e-6 for an O(1e-7) state, so the
    # numerical derivative sampled far outside the local H2 regime and let
    # Newton stop with a material balance residual.
    eps = max(1e-12, 1e-5 * max(abs(S_h2), 1e-8))

    # Get T_op from params
    T_op = params.get('T_op', params.get('T_base', 298.15))

    try:
        state_arr[h2_idx] = S_h2 + eps
        rhos_plus = rhos_madm1(state_arr, params, T_op, h=h).copy()
        if S_h2 - eps > 0.0:
            state_arr[h2_idx] = S_h2 - eps
            denom = 2.0 * eps
        else:
            state_arr[h2_idx] = S_h2                    # one-sided: keep the base point strictly positive
            denom = eps
        rhos_minus = rhos_madm1(state_arr, params, T_op, h=h).copy()
    finally:
        state_arr[h2_idx] = S_saved

    # Difference derivative (central where positive, one-sided forward near zero)
    dr_dS = (rhos_plus - rhos_minus) / denom

    # Get H2 stoichiometry
    stoichio = f_stoichio(state_arr)

    # Get flow rate
    Q = state_arr[-1]

    # Compute gradient: d(Q/V*(S_in - S))/dS + d(Σ rhos*ν)/dS
    return -Q/V_liq + np.dot(dr_dS, stoichio)


# H9: rhos / Cs are now allocated LOCAL to rhos_madm1 (reentrant) — no shared mutable rate workspace.
# nu per phase now comes from the mineral registry (single source): the old hardcoded copy carried
# nu=2 for vivianite (5 ions) and proton-convention counts for DCPD/newberyite that did not match
# the live IAP conventions (fix/madm1-mineral-subsystem).
sum_stoichios = mineral_nu_array()

_MADM1_BASE_PROCESS_NAMES = (
    'hydrolysis_carbs',
    'hydrolysis_proteins',
    'hydrolysis_lipids',
    'uptake_sugars',
    'uptake_amino_acids',
    'uptake_LCFA',
    'uptake_valerate',
    'uptake_butyrate',
    'uptake_propionate',
    'uptake_acetate',
    'uptake_h2',
    'decay_Xsu',
    'decay_Xaa',
    'decay_Xfa',
    'decay_Xc4',
    'decay_Xpro',
    'decay_Xac',
    'decay_Xh2',
    'storage_PHA_valerate',
    'storage_PHA_butyrate',
    'storage_PHA_propionate',
    'storage_PHA_acetate',
    'lysis_XPAO',
    'lysis_XPP',
    'lysis_XPHA',
    'growth_SRB_h2',
    'decay_XhSRB',
    'growth_SRB_acetate',
    'decay_XaSRB',
    'growth_SRB_propionate',
    'decay_XpSRB',
    'growth_SRB_butyrate',
    'growth_SRB_valerate',
    'decay_Xc4SRB',
    'reduction_HFO_H_h2',
    'reduction_HFO_L_h2',
    'reduction_HFO_H_IS',
    'reduction_HFO_L_IS',
    'aging_HFO_H',
    'aging_HFO_L',
    'fast_P_binding',
    'slow_P_sorption',
    'aging_HFO_HP',
    'aging_HFO_LP',
    'dissolution_HFO_HP',
    'dissolution_HFO_LP',
)

_MADM1_MINERAL_AND_GAS_PROCESS_NAMES = (
    'precipitation_CCM',
    'precipitation_ACC',
    'precipitation_ACP',
    'precipitation_HAP',
    'precipitation_DCPD',
    'precipitation_OCP',
    'precipitation_struv',
    'precipitation_newb',
    'precipitation_magn',
    'precipitation_kstruv',
    'precipitation_FeS',
    'precipitation_Mackinawite',
    'precipitation_Fe3PO42',
    'precipitation_Siderite',
    'precipitation_AlPO4',
    'transfer_h2',
    'transfer_ch4',
    'transfer_co2',
    'transfer_h2s',
)

_MADM1_PROCESS_NAMES = (
    _MADM1_BASE_PROCESS_NAMES
    + tuple(spec[0] for spec in _HFO_HOST_REDUCTION_SPECS)
    + _MADM1_MINERAL_AND_GAS_PROCESS_NAMES
)

_MADM1_BASE_PROCESS_COUNT = len(_MADM1_BASE_PROCESS_NAMES)
_MADM1_HFO_HOST_REDUCTION_COUNT = len(_HFO_HOST_REDUCTION_SPECS)
_MADM1_MINERAL_RATE_START = (
    _MADM1_BASE_PROCESS_COUNT + _MADM1_HFO_HOST_REDUCTION_COUNT
)

#: The mineral-precipitation rows: 15 phases (``_MADM1_MMP_MINERAL_NAMES``, checked below).
_MADM1_MINERAL_COUNT = 15
#: The four gas-liquid transfers (H2, CH4, CO2, H2S) follow the minerals. Their rate positions are NAMED here, not
#: taken as "the last four": a process appended after them (a kernel extension numbered after 74) must not shift
#: the gas slice the reactor's headspace balance reads (plan step 0, 2026-10-09).
_MADM1_GAS_RATE_COUNT = 4
_MADM1_GAS_RATE_START = _MADM1_MINERAL_RATE_START + _MADM1_MINERAL_COUNT
MADM1_GAS_RATE_SLICE = slice(_MADM1_GAS_RATE_START, _MADM1_GAS_RATE_START + _MADM1_GAS_RATE_COUNT)
#: The core rate vector's length (base + host reductions + minerals + gas transfers).
MADM1_CORE_RATE_COUNT = _MADM1_GAS_RATE_START + _MADM1_GAS_RATE_COUNT


def madm1_non_gas_rate_mask(n_rates: int) -> np.ndarray:
    """True for every rate position that is NOT a gas-liquid transfer (the liquid-side processes, minerals and any
    extension appended after the transfers)."""
    mask = np.ones(int(n_rates), dtype=bool)
    mask[MADM1_GAS_RATE_SLICE] = False
    return mask


_MADM1_PROCESS_INDICES = {
    'primary_uptake': list(range(3, 11)),
    'biomass_decay': list(range(11, 18)),
    # The PAO storage kinetics are the VFA-specific PHA storage reactions at 18:22.
    'pao_storage': list(range(18, 22)),
    'pao_vfa_uptake': list(range(18, 22)),
    'pao_decay': list(range(22, 25)),
    'srb_uptake': [25, 27, 29, 31, 32],
    'hfo_chemical_p': list(range(38, 46)),
    'hfo_host_transformation': list(
        range(_MADM1_BASE_PROCESS_COUNT, _MADM1_MINERAL_RATE_START)
    ),
    'mmp': list(range(_MADM1_MINERAL_RATE_START, _MADM1_MINERAL_RATE_START + _MADM1_MINERAL_COUNT)),
    'gas_transfer': list(range(MADM1_GAS_RATE_SLICE.start, MADM1_GAS_RATE_SLICE.stop)),
}

_MADM1_MMP_MINERAL_NAMES = (
    'CCM', 'ACC', 'ACP', 'HAP', 'DCPD', 'OCP',
    'struv', 'newb', 'magn', 'kstruv', 'FeS', 'Mackinawite',
    'Fe3PO42', 'Siderite', 'AlPO4',
)
assert len(_MADM1_MMP_MINERAL_NAMES) == _MADM1_MINERAL_COUNT

_MADM1_GAS_TRANSFER_SPECIES = ('CH4', 'CO2', 'H2', 'H2S')
_MADM1_GAS_TRANSFER_DIAG_ORDER = np.array([1, 2, 0, 3], dtype=int)

#: Canonical guild map for trace-element micronutrient limitation (rhos process indices):
#: 9 = acetoclastic, 10 = hydrogenotrophic methanogens; 6/7/8 = valerate/butyrate/propionate
#: syntrophic VFA oxidisers. Co/Ni are methanogen cofactors (MCR/F430, corrinoid/B12); Se is the
#: formate-dehydrogenase (Se/W/Mo) proxy for hydrogenotrophs + syntrophic VFA oxidisers.
_TRACE_GUILD_MAP_DEFAULT = {'Co': (9, 10), 'Ni': (9, 10), 'Se': (6, 7, 8, 10)}

#: Keys a complete trace-element calibration contract MUST carry before it may alter reaction rates.
_TRACE_KINETICS_REQUIRED = (
    'response_model', 'concentration_basis', 'parameter_units', 'parameter_kind',
    'parameters', 'calibration_id', 'calibration_source_kind', 'matrix_scope', 'validity_envelope',
)

_HFO_LIGAND_REQUIRED = (
    'response_model', 'concentration_basis', 'parameter_units',
    'parameter_kind', 'parameters', 'observed_smp_or_doc_mg_C_L',
    'observation_id', 'calibration_id', 'calibration_source_kind',
    'matrix_scope', 'validity_envelope',
)


def _validate_trace_kinetics(tk):
    'Validate a trace-element (Co/Ni/Se) micronutrient calibration contract — fail-closed.'
    if tk is None:
        return None
    if not isinstance(tk, Mapping):
        raise ValueError(f"trace_kinetics must be a mapping or None, got {type(tk).__name__}.")
    missing = [k for k in _TRACE_KINETICS_REQUIRED if k not in tk]
    if missing:
        raise ValueError(
            f"trace_kinetics is a partial contract; missing required keys {missing}. A partial "
            "contract must not fall back to defaults — supply a complete validated calibration or "
            "leave trace_kinetics=None (inventory only, no rate effect).")
    if tk['parameter_kind'] != 'half_saturation':
        raise ValueError(
            f"trace_kinetics.parameter_kind={tk['parameter_kind']!r} is not a rate constant. A Monod "
            "half-saturation is required; a supplementation_dose or media_optimum is a different kind "
            "of quantity and cannot be used as a K — that mis-classification was the original defect.")
    if tk['calibration_source_kind'] not in ('bench_tested', 'plant_data'):
        raise ValueError(
            f"trace_kinetics.calibration_source_kind={tk['calibration_source_kind']!r} is not an "
            "empirical calibration. Only 'bench_tested' or 'plant_data' may activate kinetics; "
            "literature may seed priors for fitting but never activate production rates.")
    if tk['parameter_units'] != 'mol_L':
        raise ValueError(
            f"trace_kinetics.parameter_units={tk['parameter_units']!r} unsupported; the engine "
            "consumes mol/L. Convert (e.g. mg/L -> mol/L via atomic weight) before activation.")
    if tk['concentration_basis'] != 'operationally_dissolved_total':
        raise ValueError(
            f"trace_kinetics.concentration_basis={tk['concentration_basis']!r} unsupported. The engine "
            "applies the half-saturation to the state (dissolved) concentration directly, so the "
            "calibration must be fitted on the same 'operationally_dissolved_total' basis.")
    if tk['response_model'] != 'apparent_dissolved_total_monod':
        raise ValueError(
            f"trace_kinetics.response_model={tk['response_model']!r} unsupported by this engine.")
    # Provenance must be MEANINGFUL, not merely present.
    for _prov in ('calibration_id', 'matrix_scope'):
        if not (isinstance(tk[_prov], str) and tk[_prov].strip()):
            raise ValueError(f"trace_kinetics.{_prov} must be a non-empty string.")
    if not (isinstance(tk['validity_envelope'], Mapping) and tk['validity_envelope']):
        raise ValueError("trace_kinetics.validity_envelope must be a non-empty mapping.")
    params = tk['parameters']
    if not isinstance(params, Mapping) or not params:
        raise ValueError("trace_kinetics.parameters must be a non-empty {metal: KS_mol_L} dict.")
    norm_params = {}
    for metal, ks in params.items():
        if metal not in _TRACE_GUILD_MAP_DEFAULT:
            raise ValueError(f"trace_kinetics.parameters: unknown metal {metal!r}; expected one of "
                             f"{tuple(_TRACE_GUILD_MAP_DEFAULT)}.")
        if isinstance(ks, bool):   # bool is an int subclass — True would read as 1.0
            raise ValueError(f"trace_kinetics.parameters[{metal!r}]={ks!r} must be a number, not bool.")
        ks = float(ks)
        if not (np.isfinite(ks) and ks > 0):
            raise ValueError(
                f"trace_kinetics.parameters[{metal!r}]={ks} must be a finite positive half-saturation.")
        norm_params[metal] = ks
    # Guilds are fixed biology, NOT caller-configurable: a remap could throttle the wrong process or
    # duplicate an index. A `guilds` key may only restate canonical exactly (mapping, covering exactly
    # the calibrated metals); anything else is rejected rather than silently replaced.
    if 'guilds' in tk:
        _supplied = tk['guilds']
        if not isinstance(_supplied, Mapping) or not _supplied:
            raise ValueError("trace_kinetics.guilds, when present, must be a non-empty mapping "
                             "restating the canonical guild map; omit the key to use canonical.")
        if set(_supplied) != set(norm_params):
            raise ValueError(
                f"trace_kinetics.guilds covers {sorted(_supplied)} but the calibrated metals are "
                f"{sorted(norm_params)}; it must cover exactly the calibrated metals or be omitted.")
        for _m in norm_params:
            _raw = _supplied[_m]
            if isinstance(_raw, (str, bytes)) or not isinstance(_raw, (list, tuple)):
                raise ValueError(f"trace_kinetics.guilds[{_m!r}]={_raw!r} must be a list/tuple of "
                                 "integer process indices.")
            # Strict ints: int() would coerce 9.9 -> 9 and silently accept a wrong index as canonical.
            if any(isinstance(i, bool) or not isinstance(i, int) for i in _raw):
                raise ValueError(f"trace_kinetics.guilds[{_m!r}]={_raw!r} must contain integer process "
                                 "indices (no floats/bools).")
            _got = tuple(_raw)
            if _got != _TRACE_GUILD_MAP_DEFAULT[_m]:
                raise ValueError(
                    f"trace_kinetics.guilds[{_m!r}]={_got} deviates from the canonical guild map "
                    f"{_TRACE_GUILD_MAP_DEFAULT[_m]}; guild assignment is fixed biology and is not "
                    "caller-configurable.")
    norm_guilds = {m: _TRACE_GUILD_MAP_DEFAULT[m] for m in norm_params}
    return {
        'parameters': norm_params, 'guilds': norm_guilds,
        # provenance retained so diagnostics can report WHICH calibration was applied (not just that
        # one was) — a plant-data calibration must never render as "predictive / not assayed".
        'calibration_id': tk['calibration_id'],
        'calibration_source_kind': tk['calibration_source_kind'],
        'response_model': tk['response_model'],
    }


def _validate_hfo_ligand_competition(contract):
    """Validate a site-specific conditional SMP/DOC site-blocking contract.

    No default is inferred. Published observations may motivate the model form,
    but only bench-tested or plant-data calibration may activate a rate effect.
    This first live implementation uses one synchronized SMP/DOC observation as
    a fixed forcing over the simulated horizon.
    """

    if contract is None:
        return None
    if not isinstance(contract, Mapping):
        raise ValueError(
            "hfo_ligand_competition must be a mapping or None, got "
            f"{type(contract).__name__}."
        )
    missing = [key for key in _HFO_LIGAND_REQUIRED if key not in contract]
    if missing:
        raise ValueError(
            "hfo_ligand_competition is a partial contract; missing required "
            f"keys {missing}. Omit the contract to keep ligand competition a "
            "rate no-op."
        )
    if contract['response_model'] != 'conditional_noncompetitive_site_blocking':
        raise ValueError(
            "hfo_ligand_competition.response_model must be "
            "'conditional_noncompetitive_site_blocking'."
        )
    if contract['concentration_basis'] != 'measured_SMP_or_DOC_mg_C_L':
        raise ValueError(
            "hfo_ligand_competition.concentration_basis must be "
            "'measured_SMP_or_DOC_mg_C_L'."
        )
    if contract['parameter_units'] != 'mg_C_L':
        raise ValueError("hfo_ligand_competition.parameter_units must be 'mg_C_L'.")
    if contract['parameter_kind'] != 'half_inhibition_concentration':
        raise ValueError(
            "hfo_ligand_competition.parameter_kind must be "
            "'half_inhibition_concentration'."
        )
    if contract['calibration_source_kind'] not in {'bench_tested', 'plant_data'}:
        raise ValueError(
            "hfo_ligand_competition.calibration_source_kind must be "
            "'bench_tested' or 'plant_data'; literature-only values may not "
            "activate plant rates."
        )
    for key in ('observation_id', 'calibration_id', 'matrix_scope'):
        if not isinstance(contract[key], str) or not contract[key].strip():
            raise ValueError(f"hfo_ligand_competition.{key} must be a non-empty string.")

    parameters = contract['parameters']
    if not isinstance(parameters, Mapping) or set(parameters) != {
        'K_half_mg_C_L', 'exponent'
    }:
        raise ValueError(
            "hfo_ligand_competition.parameters must contain exactly "
            "K_half_mg_C_L and exponent."
        )
    normalized_parameters = {}
    for key in ('K_half_mg_C_L', 'exponent'):
        raw = parameters[key]
        if isinstance(raw, bool):
            raise ValueError(
                f"hfo_ligand_competition.parameters[{key!r}] must not be bool."
            )
        value = float(raw)
        if not np.isfinite(value) or value <= 0:
            raise ValueError(
                f"hfo_ligand_competition.parameters[{key!r}] must be finite "
                "and positive."
            )
        normalized_parameters[key] = value

    raw_observed = contract['observed_smp_or_doc_mg_C_L']
    if isinstance(raw_observed, bool):
        raise ValueError(
            "hfo_ligand_competition.observed_smp_or_doc_mg_C_L must not be bool."
        )
    observed = float(raw_observed)
    if not np.isfinite(observed) or observed < 0:
        raise ValueError(
            "hfo_ligand_competition.observed_smp_or_doc_mg_C_L must be finite "
            "and nonnegative."
        )

    envelope = contract['validity_envelope']
    required_envelope = {
        'smp_or_doc_min_mg_C_L', 'smp_or_doc_max_mg_C_L', 'pH_min', 'pH_max'
    }
    if not isinstance(envelope, Mapping) or not required_envelope <= set(envelope):
        raise ValueError(
            "hfo_ligand_competition.validity_envelope must include "
            f"{sorted(required_envelope)}."
        )
    normalized_envelope = {
        key: float(envelope[key]) for key in sorted(required_envelope)
    }
    if any(not np.isfinite(value) for value in normalized_envelope.values()):
        raise ValueError("hfo_ligand_competition validity bounds must be finite.")
    if not (
        0 <= normalized_envelope['smp_or_doc_min_mg_C_L']
        <= observed
        <= normalized_envelope['smp_or_doc_max_mg_C_L']
    ):
        raise ValueError(
            "the observed SMP/DOC value is outside the calibration validity envelope."
        )
    if not (
        0 <= normalized_envelope['pH_min']
        < normalized_envelope['pH_max']
        <= 14
    ):
        raise ValueError("the HFO ligand calibration pH envelope is invalid.")

    return {
        'response_model': contract['response_model'],
        'concentration_basis': contract['concentration_basis'],
        'parameter_units': contract['parameter_units'],
        'parameter_kind': contract['parameter_kind'],
        'parameters': normalized_parameters,
        'observed_smp_or_doc_mg_C_L': observed,
        'observation_id': contract['observation_id'],
        'calibration_id': contract['calibration_id'],
        'calibration_source_kind': contract['calibration_source_kind'],
        'matrix_scope': contract['matrix_scope'],
        'validity_envelope': normalized_envelope,
    }


def rhos_madm1(state_arr, params, T_op, h=None):
    """
    Compute mADM1 process rates.

    Parameters
    ----------
    state_arr : array
        State vector
    params : dict
        Model parameters
    T_op : float
        Operating temperature [K]
    h : tuple, optional
        Pre-computed (pH, nh3, co2, acts) from pcm(). If None, computed internally.

    Returns
    -------
    array
        Process rates
    """
    # H9 thread-safety: allocate the rate + intermediate buffers LOCAL to this call. They were module
    # globals reused across every solve, so overlapping mADM1 solves (e.g. a nightly advance + a weekly
    # calibrate, or an unrelated ASM/mADM request) clobbered each other's rate workspace. Local buffers
    # make rhos_madm1 reentrant. rhos is ZEROED (several slices are intentionally left 0); Cs is fully
    # overwritten below. Per-call allocation of two small arrays is negligible vs the BDF solve.
    n_minerals = len(_MADM1_MMP_MINERAL_NAMES)
    hfo_host_reduction_start = _MADM1_BASE_PROCESS_COUNT
    hfo_host_reduction_slice = slice(
        hfo_host_reduction_start,
        hfo_host_reduction_start + _MADM1_HFO_HOST_REDUCTION_COUNT,
    )
    mineral_rate_start = _MADM1_MINERAL_RATE_START
    mineral_rate_slice = slice(mineral_rate_start, mineral_rate_start + n_minerals)
    rhos = np.zeros(MADM1_CORE_RATE_COUNT)
    Cs = np.empty(38 + 8)
    ks = params['rate_constants']
    Ks = params['half_sat_coeffs']
    K_PP = params['K_PP']
    K_so4 = params['K_so4']
    cmps = params['components']
    # n = len(cmps)
    pH_LLs, pH_ULs = params['pH_limits']
    KS_IN = params['KS_IN']
    KS_IP = params['KS_IP']
    KI_nh3 = params['KI_nh3']
    KIs_h2 = params['KIs_h2']
    KIs_h2s = params['KIs_h2s']
    inhibition_ext = params.get('inhibition_ext') or {}
    # Reduced mechanistic observer flags (default: full model). For a plant whose chemistry sub-model
    # is unidentifiable from routine signals (no ion/TDS/metals/orthophosphate/TAN assay), gate off the
    # subsystems that cannot be identified and were shown to drive a false-convergence washout: the
    # mineral precipitation subsystem, phosphate (Monod) limitation, and unassayed sulfide/ammonia
    # inhibition. The identifiable C/N/VFA/CH4 core is untouched. All-False -> bit-identical prior model.
    model_reduction = params.get('model_reduction') or {}
    KHb = params['K_H_base']
    Kab = params['Ka_base']
    KH_dH = params['K_H_dH']
    Ka_dH = params['Ka_dH']
    kLa = params['kLa']
    k_cryst = params['k_cryst']
    n_cryst = params['n_cryst']
    T_base = params['T_base']

    # ZERO-CLAMP on inventory RATE INPUTS (2026-08-30, X_PP sign-flip fix): every Cs
    # entry drives a rate FIRST-ORDER in that inventory, and X_PP/X_PAO feeds a Monod
    # ratio below. A BDF micro-undershoot below zero made first-order lysis NEGATIVE
    # (production) and flipped pao_pp_monod's SIGN, turning PAO VFA uptake (rhos[18:22])
    # into VFA production — the P5-battery ic_hi physicality flag. Clamping the rate
    # INPUT is mass-conservative (rates vanish at zero inventory); the STATE VECTOR is
    # deliberately NOT clamped. Same pattern as the mineral block's np.maximum below.
    # On any trajectory that stays nonnegative this is bit-identical to the old code.
    Cs[:7] = np.maximum(state_arr[13:20], 0.0)  # original ADM1 processes
    Cs[7:11] = np.maximum(state_arr[19:23], 0.0)
    Cs[11:18] = np.maximum(state_arr[16:23], 0.0)
    Cs[18:23] = X_PAO = max(float(state_arr[26]), 0.0)   # P extension processes
    X_PP, X_PHA = np.maximum(state_arr[[25, 24]], 0.0)
    Cs[23:25] = X_PP, X_PHA
    Cs[25:27] = max(float(state_arr[31]), 0.0)  # S extension processes
    Cs[27:29] = max(float(state_arr[32]), 0.0)
    Cs[29:31] = max(float(state_arr[33]), 0.0)
    Cs[31:34] = max(float(state_arr[34]), 0.0)
    Cs[34:36] = Cs[36:38] = Cs[38:40] = Cs[40:42] = np.maximum(state_arr[38:40], 0.0)   # Fe extension processes + HFO module
    Cs[42:44] = Cs[44:46] = np.maximum(state_arr[41:43], 0.0)

    rhos[:46] = ks * Cs
    primary_substrates = state_arr[:8]

    rhos[3:11] *= substr_inhibit(primary_substrates, Ks[:8])
    c4 = primary_substrates[[3,4]]
    if sum(c4) > 0: rhos[[6,7]] *= c4/sum(c4)

    vfas = primary_substrates[3:7]
    pao_vfa_monod = substr_inhibit(vfas, Ks[8])
    rhos[18:22] *= pao_vfa_monod
    pao_vfa_distribution = np.zeros_like(vfas)
    if sum(vfas) > 0:
        pao_vfa_distribution = vfas/sum(vfas)
        rhos[18:22] *= pao_vfa_distribution
    pao_pp_monod = 1.0
    if X_PAO > 0:
        pao_pp_monod = float(substr_inhibit(X_PP/X_PAO, K_PP))
        rhos[18:22] *= pao_pp_monod
    # PAO rates are not sulfate-limited in the current kinetic formulation.
    pao_sulfate_monod = 1.0

    srb_subs = np.flip(primary_substrates[3:])
    S_SO4, S_IS = state_arr[29:31]
    srb_substrate_monod = substr_inhibit(srb_subs, Ks[9:14])
    srb_sulfate_monod = substr_inhibit(S_SO4, K_so4)
    rhos[[25,27,29,31,32]] *= srb_substrate_monod * srb_sulfate_monod  # Updated to Ks[9:14] for 5 SRB processes
    srb_c4_distribution = np.zeros(2)
    if sum(srb_subs[-2:]) > 0:
        srb_c4_distribution = srb_subs[-2:]/sum(srb_subs[-2:])
        rhos[[31,32]] *= srb_c4_distribution

    # Convert native donor states to kmol/m3. The second-order HFO-reduction
    # constants multiply kg Fe inventory by donor molarity, yielding kg Fe/m3/d.
    S_h2 = primary_substrates[-1]
    rhos[34:36] *= S_h2 / H2_COD_KG_PER_KMOL
    rhos[36:38] *= S_IS / S_KG_PER_KMOL

    # Directly destroy every remaining P-bound/aged HFO host on the same finite
    # donor basis.  The paired stoichiometric rows release the exact bound-P
    # inventory to S_IP and convert the host Fe(III) to S_Fe2, so surface
    # capacity cannot survive reduction or sulfidation as a ghost state.
    host_reduction_constants = params['hfo_host_reduction_rate_constants']
    host_reduction_rates = np.empty(_MADM1_HFO_HOST_REDUCTION_COUNT, dtype=float)
    for offset, (_process_id, host_id, donor_id, _history_class) in enumerate(
        _HFO_HOST_REDUCTION_SPECS
    ):
        host_inventory = max(float(state_arr[cmps.index(host_id)]), 0.0)
        donor_inventory = (
            S_h2 / H2_COD_KG_PER_KMOL
            if donor_id == 'h2'
            else S_IS / S_KG_PER_KMOL
        )
        host_reduction_rates[offset] = (
            host_reduction_constants[offset]
            * host_inventory
            * max(float(donor_inventory), 0.0)
        )
    rhos[hfo_host_reduction_slice] = host_reduction_rates

    KPbind, KPdiss = Ks[-2:]
    S_IP = state_arr[11]
    hfo_adsorption_factor = float(substr_inhibit(S_IP, KPbind))
    hfo_desorption_factor = float(non_compet_inhibit(S_IP, KPdiss))
    ligand_contract = inhibition_ext.get('hfo_ligand_competition')
    hfo_ligand_competition_configured = ligand_contract is not None
    hfo_ligand_competition_active = False
    hfo_ligand_competition_factor = 1.0

    # inhibition factors
    # ******************
    # Convert kg/m³ (model states) to mol/L
    # FIX: Remove extra 1e3 factor - mass2mol_conversion already converts kg/m³ to mol/L
    unit_conversion = madm1_mass2mol(cmps)  # kg/m³ -> mol/L (NOT kmol/m³!); S_IP on elemental P
    if T_op == T_base:
        Ka = Kab
        KH = KHb / unit_conversion[[7,8,9,30]]
    else:
        # Cache the temperature-corrected equilibrium constants against the
        # temperature they were computed for (``_Ka_T_op``), NOT against the
        # live ``T_op`` key. ``rhos_wrapper`` sources the operating ``T_op``
        # FROM ``params['T_op']``, so the previous guard (`T_op == popped
        # 'T_op'`) was always True and the recompute branch never ran once a
        # runtime ``T_op`` was injected -- a runtime temperature change (e.g.
        # the B4 dynamic heating actuator setting ``params['T_op']``) silently
        # reused stale constants or KeyError'd. Keying the cache on the
        # temperature the constants belong to makes a runtime ``T_op`` step
        # recompute Ka/KH/Ksp correctly (acid-base + Henry respond to T; the
        # biological Arrhenius rates remain constant-parameter in mADM1).
        if params.get('_Ka_T_op') == T_op and 'Ka' in params:
            Ka = params['Ka']
            KH = params['KH']
        else:
            params['_Ka_T_op'] = T_op
            Ka = params['Ka'] = Kab * T_correction_factor(T_base, T_op, Ka_dH)
            KH = params['KH'] = KHb * T_correction_factor(T_base, T_op, KH_dH) / unit_conversion[[7,8,9,30]]

    S_IN, S_IP = state_arr[[10,11]]
    
    # no orthophosphate assay) and the mineral subsystem depletes soluble P to a growth-throttling floor.
    # When disabled, P does not limit any guild; N limitation (identifiable via TKN) still applies.
    I_P = 1.0 if model_reduction.get('disable_p_limitation') else substr_inhibit(S_IP, KS_IP)
    I_nutrients = substr_inhibit(S_IN, KS_IN) * I_P
    rhos[3:11] *= I_nutrients
    rhos[[25,27,29,31,32]] *= I_nutrients

# =============================================================================
#     !!! place holder for PCM (speciation)
# =============================================================================
    # Use pre-computed h if provided, otherwise compute it
    if h is None:
        _spec = pcm_speciation(state_arr, params)
        pH, nh3, co2, acts = _spec['pH'], _spec['species_M']['NH3'], _spec['species_M']['CO2'], np.ones(13)
        log_gamma1 = _spec['log_gamma'][1]
        # dissolved CO2 as a fraction of the TOTAL inorganic carbon, from the same Davies speciation (gas transfer).
        # The total, not the free ligand sum: the ion pairs hold HCO3-/CO3-2 (CaHCO3+, MgHCO3+, FeCO3, FeHCO3+;
        # HOUSE-MADM1-SPECIATION-ION-PAIRS-FIRST-20261008), so CO2/(CO2 + HCO3 + CO3) would overstate CO2(aq)
        _ic_idx = params['components'].index('S_IC')
        _ic_total = float(state_arr[_ic_idx]) * float(unit_conversion[_ic_idx])
        co2_fraction = (_spec['species_M']['CO2'] / _ic_total) if _ic_total > 0 else None
        # the solve's FREE H2S (after Fe(HS)2 / Fe(HS)3-), the species the inhibition and the stripping read
        free_h2s_M = float(_spec['species_M']['H2S'])
    else:
        # a caller-fixed pH tuple (the pH-control diagnostic): ideal limit, as that diagnostic always was
        pH, nh3, co2, acts = h
        log_gamma1, co2_fraction, free_h2s_M = 0.0, None, None

    # The conditional ligand model is calibrated against a pH domain, so it
    # must be evaluated only after the same live pH/speciation solve used by
    # the process rates. Selecting it from influent/initial pH would recreate
    # the inconsistent formation-pH defect identified in the HFO review.
    _hfo_flag = model_reduction.get('disable_hfo_p')
    hfo_p_disabled = bool(
        _hfo_flag if _hfo_flag is not None
        else model_reduction.get('disable_minerals')
    )
    if ligand_contract is not None and not hfo_p_disabled:
        envelope = ligand_contract['validity_envelope']
        if not envelope['pH_min'] <= pH <= envelope['pH_max']:
            raise ValueError(
                "live pH is outside the calibrated HFO SMP/DOC competition "
                f"domain [{envelope['pH_min']}, {envelope['pH_max']}]: {pH}"
            )
        observed = ligand_contract['observed_smp_or_doc_mg_C_L']
        k_half = ligand_contract['parameters']['K_half_mg_C_L']
        exponent = ligand_contract['parameters']['exponent']
        hfo_ligand_competition_factor = float(
            1.0 / (1.0 + (observed / k_half) ** exponent)
        )

    # HFO chemical-P gate: `disable_hfo_p` is tri-state. Absent (None) it INHERITS
    
    # observer relies on both subsystems being off under the one flag. Set explicitly,
    # it controls the HFO rows independently (a FeCl3-dosed plant needs HFO-P live).
    if hfo_p_disabled:
        rhos[40:42] = 0.0
        rhos[44:46] = 0.0
    else:
        rhos[40:42] *= (
            hfo_adsorption_factor * hfo_ligand_competition_factor
        )
        rhos[44:46] *= hfo_desorption_factor
        hfo_ligand_competition_active = hfo_ligand_competition_configured

    Is_pH = Hill_inhibit(10**(-pH), pH_ULs, pH_LLs)
    rhos[3:9] *= Is_pH[0]
    rhos[9:11] *= Is_pH[1:3]
    rhos[[25,27]] *= Is_pH[3:5]
    rhos[[29,31,32]] *= Is_pH[-1]

    Is_h2 = non_compet_inhibit(S_h2, KIs_h2)
    rhos[5:9] *= Is_h2
    n_nh3 = params.get('n_nh3', 1.0)
    # n_nh3 = 1 is the shipped non-competitive hyperbola, KI/(KI+S), and is the default,
    # so nothing changes unless a caller asks for it. n_nh3 > 1 gives the steeper,
    # threshold-like shape of Astals et al. (2018, Water Res. 143:127,
    # doi 10.1016/j.watres.2018.06.021) without swapping the functional family.
    # Both the exponent and KI_nh3 must be FITTED; see the KI_nh3 default note below.
    Inh3 = 1.0 / (1.0 + (nh3 / KI_nh3) ** n_nh3) if n_nh3 != 1.0         else non_compet_inhibit(nh3, KI_nh3)
    # Free-ammonia inhibition on acetoclastic methanogens: gated off in the reduced observer because
    
    if not model_reduction.get('disable_nh3_inhibition'):
        rhos[9] *= Inh3

    # the shared solve's free H2S; a caller-fixed pH keeps the ideal HS-/H2S split of the total
    Z_h2s = free_h2s_M if free_h2s_M is not None else calc_biogas(state_arr, params, pH, log_gamma1=log_gamma1)
    # UNITS: calc_biogas returns dissolved molecular H2S in kmol-S/m3, but KIs_h2s is
    # documented and defaulted in kg S/m3 (core/kinetic_params.py KI_h2s_*, 0.400-0.520).
    # Convert with the SAME unit_conversion the gas-transfer term uses below, so the
    # inhibition and the transfer consume one number. Before 2026-08 the kmol/m3 value
    # was compared against the kg S/m3 constant directly, flattering inhibition ~32x
    # (fingerprint token h2s-units/v2-kgS-basis-2026-08 reseeds h2s-active checkpoints).
    Z_h2s_kgS = Z_h2s / unit_conversion[30]
    Is_h2s = non_compet_inhibit(Z_h2s_kgS, KIs_h2s)
    
    # S_SO4/S_IS are unidentified placeholders). Z_h2s/Is_h2s stay computed for advisory diagnostics.
    if not model_reduction.get('disable_h2s_inhibition'):
        rhos[6:11] *= Is_h2s[:5]
        rhos[[25,27,29,31,32]] *= Is_h2s[5:]

    # ---- Extended inhibition/limitation: Na osmotic + trace-element micronutrient (QSDsan-only) ----
    # Constants in mol/L; conservative-tracer states clamped >=0; factors clipped to [0,1].
    I_Na = 1.0
    ionic_strength_molL = 0.0
    I_trace = {}                    # per-metal Monod limitation factor (only for evaluable metals)
    trace_contract_configured = False   # a validated calibration contract is present
    trace_kinetics_active = False       # a factor was actually EVALUATED and applied to a rate
    trace_provenance = {}               # calibration id/source/model, only when a contract is present
    if inhibition_ext:
        def _ion_molL(_name):
            if _name in cmps.IDs:
                _i = cmps.index(_name)
                return max(0.0, float(state_arr[_i]) * float(unit_conversion[_i]))
            return 0.0
        # Sodium / osmotic inhibition (non-competitive) on the salinity-sensitive methanogens.
        KI_Na = inhibition_ext.get('KI_Na', 0.0)
        if KI_Na and KI_Na > 0:
            I_Na = min(1.0, max(0.0, float(non_compet_inhibit(_ion_molL('S_Na'), KI_Na))))
            for _gi in inhibition_ext.get('na_guilds', ()):
                rhos[_gi] *= I_Na
        # Ionic-strength / osmolality index (watch-only; Na alone is a partial salinity surrogate).
        ionic_strength_molL = 0.5 * (
            _ion_molL('S_Na') + _ion_molL('S_K') + _ion_molL('S_Cl')
            + 4.0 * _ion_molL('S_Mg') + 4.0 * _ion_molL('S_Ca'))

        # Trace-element (Co/Ni/Se) micronutrient limitation — QUARANTINED. Alters reaction rates ONLY
        # when a complete, validated calibration contract is present (trace_kinetics != None). The
        # half-saturation acts on the state (dissolved) concentration DIRECTLY, on the contract's stated
        # 'operationally_dissolved_total' basis. There is deliberately NO separate free/bioavailable
        # discount here: the former scalar sulfide gate double-counted sequestration (an apparent-total
        # K applied again to a free fraction) and was metal-agnostic (one scalar for Co, Ni AND Se —
        # indefensible for Se, an oxyanion that does not form a metal sulfide). Absent a contract this
        # loop never runs, so trace inventory is a strict rate no-op.
        _tk = inhibition_ext.get('trace_kinetics')
        if _tk:
            trace_contract_configured = True
            trace_provenance = {'calibration_id': _tk.get('calibration_id'),
                                'calibration_source_kind': _tk.get('calibration_source_kind'),
                                'response_model': _tk.get('response_model')}
            _guilds = _tk['guilds']
            for _metal, _ks in _tk['parameters'].items():
                conc_Me = _ion_molL('S_' + _metal)
                # A zero state means the metal was NOT ASSAYED (the tracer is only ever seeded from an
                # assay), NOT "assayed at zero". Applying the Monod at 0 would make an unassayed metal
                # instantly lethal — fabricating starvation from absence, the very failure this fix
                # closes. So an unevaluable metal is SKIPPED, and kinetics_active reports whether a
                # factor was actually evaluated rather than merely configured.
                if conc_Me <= 0 or not _ks or _ks <= 0:
                    continue
                I_Me = min(1.0, max(0.0, float(substr_inhibit(conc_Me, _ks))))
                I_trace[_metal] = I_Me
                trace_kinetics_active = True
                for _gi in _guilds.get(_metal, ()):
                    rhos[_gi] *= I_Me

    # Opt-in micro-aeration extension (absent from params unless ModifiedADM1(microaeration=True)): L2, the kernel's
    # O2-inhibition factor KI_O2 / (KI_O2 + S_O2), on exactly the uptakes the extension names (3-10 and the SRB
    # uptakes). Exactly 1.0 at S_O2 = 0, so a variant with no oxygen leaves every base rate bit-identical.
    microaeration = params.get('microaeration')
    if microaeration is not None:
        I_o2 = _microaeration.microaeration_o2_inhibition_factor(state_arr, microaeration)
        rhos[microaeration['inhibited_indices']] *= I_o2

    # multiple mineral precipitation
    # ******************************
    # Calculate real saturation indices using thermodynamics module
    # on the SAME Davies speciation as the pH (owner ruling 2026-10-08, "Wait for two more steps", step 2); a
    # caller-fixed pH tuple (the ideal-limit diagnostic) has none and keeps the reduced activity model
    SI_dict = calc_saturation_indices(state_arr, cmps, pH, T_op, unit_conversion,
                                      speciation=_spec if h is None else None,
                                      speciation_temperature_k=(params.get('T_op', params['T_base'])
                                                                if h is None else None))

    # Map the thermodynamic registry to the live component vector by ID.  This
    # removes the former 47:60 positional contract, which silently broke when a
    # phase was added or reordered.
    mineral_names = _MADM1_MMP_MINERAL_NAMES
    SIs = require_saturation_indices(SI_dict, mineral_names)

    # Precipitation/dissolution rate: r = k * X_eff * sign(SI^(1/nu)-1) * |SI^(1/nu)-1|^n
    # (SI is NOT clamped at 1 — the sign carries dissolution; struvite's n_cryst=3 is odd,
    # which the abs()/sign() construction handles correctly.)
    #
    # Nucleation surrogate (fix/madm1-mineral-subsystem, codex-vetted): the rate is
    # autocatalytic in the inventory, so from X=0 precipitation could never start. The old
    # workaround — every missing mineral defaulted to 1e-6 kg/m3 in the INFLUENT — was a
    
    # disable_minerals flag. Instead: under supersaturation only, the rate sees at least a
    # declared per-phase seed inventory (a primary-nucleation surrogate, mass-conservative,
    # inert at equilibrium); dissolution is proportional to the ACTUAL inventory clamped at
    # zero, so it decays to exactly zero and a BDF undershoot cannot amplify.
    mineral_state_indices = np.asarray(
        [cmps.index(f'X_{name}') for name in mineral_names], dtype=int)
    X_minerals = np.maximum(state_arr[mineral_state_indices], 0.0)
    # NOTE (2026-08 convention coupling): sum_stoichios (= mineral_nu_array()) feeds the
    # driving-force exponent, so the DCPD/newberyite move to the minteq.v4 proton-inclusive
    # convention (nu 2 -> 3, 15-phase benchmark closure) SLOWED those rates at unchanged
    # k_cryst: DF(nu=3)/DF(nu=2) with n_cryst=2 is ~0.285 at SI=10 (~0.164 at SI=100).
    
    # minerals live) argues the slower Ca-P kinetics move prediction TOWARD observation.
    # See kinetic_params.py k_cryst note + test_dcpd_nu_convention_slows_driving_force.
    SI_driving_force = SIs**(1/sum_stoichios) - 1
    magnitude = np.abs(SI_driving_force)**n_cryst
    X_seed = params['mineral_seed_kg_m3']
    rates = np.where(
        SI_driving_force > 0,
        k_cryst * np.maximum(X_minerals, X_seed) * magnitude,
        -k_cryst * X_minerals * magnitude,
    )

    if model_reduction.get('disable_minerals'):
        # Reduced observer: the Ca/Mg/Fe/Al-phosphate precipitation subsystem is
        # unidentifiable without an ion/TDS/metals/orthophosphate assay. Zero the reaction
        # RATES (no state driven) while still exposing SIs above as an advisory screen.
        rhos[mineral_rate_slice] = 0.0
    else:
        rhos[mineral_rate_slice] = rates

    # gas transfer
    # ************
    # Henry's law: rhos = kLa * (biogas_S - KH * biogas_p)
    # CRITICAL: Keep everything in kg/m³ (mass units) to match KH which is already kg/m³/bar
    # KH was temperature-corrected at line 833: KH = KHb * T_corr / unit_conversion
    # This made KH have units of kg/m³/bar (from original kmol/m³/bar)

    # Dynamic headspace indexing: the liquid phase is the full mADM1 component vector and the
    # gas phase starts immediately after it. Derive the count from cmps (single source of truth)
    # — NEVER hard-code it, so adding components (e.g. the trace metals S_Co/S_Ni/S_Se) cannot
    # silently shift the gas slice. Biogas species in liquid phase: S_h2 (7), S_ch4 (8),
    # S_IC (9), S_IS (30) — all before any appended component, so their indices stay fixed.
    n_cmps = len(cmps)
    n_gas = 4           # mADM1+S biogas species: H2, CH4, CO2, H2S
    gas_slice = slice(n_cmps, n_cmps + n_gas)

    biogas_S = state_arr[[7,8,9,30]].copy()  # Start with kg/m³ from state

    # UPSTREAM APPROACH: Calculate dissolved CO2 directly from state_arr[S_IC]
    # This naturally includes biological supersaturation from ODE dynamics
    # Matches QSDsan upstream: co2 = state_arr[9] * h / (Ka[3] + h)
    if co2_fraction is not None:
        # the Davies speciation's CO2(aq) share of S_IC (CO3-2 included), the same solve as pH
        co2_dissolved = state_arr[9] * co2_fraction  # kg C/m³
    else:
        h_ion = 10**(-pH)  # ideal limit for a caller-fixed pH (or a state with no inorganic carbon)
        Ka_co2 = Ka[2]  # Ka for CO2/HCO3- equilibrium
        co2_dissolved = state_arr[9] * h_ion / (Ka_co2 + h_ion)  # kg/m³ (dissolved CO2 from TOTAL S_IC)
    biogas_S[2] = co2_dissolved  # Use directly - S_IC already contains supersaturation from biology
    biogas_S[3] = Z_h2s / unit_conversion[30]  # H2S from calc_biogas: kmol/m³ -> kg/m³

    # Partial pressures in bar (R is in bar·m³/(kmol·K), state[gas_slice] is kmol/m³)
    biogas_p = R * T_op * state_arr[gas_slice]  # bar - FIXED to use dynamic slice

    # Gas transfer rate in kg/m³/d (all terms in consistent mass units)
    # Now biogas_S includes biological supersaturation, so driving force is non-zero
    biogas_equilibrium = KH * biogas_p
    gas_driving_force = biogas_S - biogas_equilibrium
    rhos[MADM1_GAS_RATE_SLICE] = kLa * gas_driving_force  # kg/m³/d, at the NAMED gas-transfer positions

    # DIAGNOSTIC HOOKS: Populate root.data for CLI diagnostics (per Codex advice)
    root = params.get('root')
    if root is not None:
        # Calculate Monod factors for diagnostics using Ks from params
        # Primary substrates (S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2)
        primary_substrates = state_arr[[0,1,2,3,4,5,6,7]]
        Ks_main = Ks[:8]  # First 8 Ks values are for main uptake processes
        monod_main = substr_inhibit(primary_substrates, Ks_main)
        gas_diag_order = _MADM1_GAS_TRANSFER_DIAG_ORDER

        # Store comprehensive diagnostics
        root.data = {
            'pH': float(pH),
            'nh3_M': float(nh3),
            'co2_M': float(co2),
            'h2s_M': float(Z_h2s),
            'biogas_dissolved': {
                'co2_kmol_m3': float(biogas_S[2]),
                'h2s_kmol_m3': float(biogas_S[3]),
            },
            'gas_equilibrium': {
                'co2_eq_kmol_m3': float(biogas_equilibrium[2]),
                'h2s_eq_kmol_m3': float(biogas_equilibrium[3]),
            },
            'I_pH': {
                'acidogens': float(Is_pH[0]),
                'acetoclastic': float(Is_pH[1]),
                'hydrogenotrophic': float(Is_pH[2]),
                'SRB_h2': float(Is_pH[3]),
                'SRB_ac': float(Is_pH[4]),
                'SRB_aa': float(Is_pH[5]),
            },
            'I_h2': {
                'LCFA': float(Is_h2[0]),
                'C4_valerate': float(Is_h2[1]),
                'C4_butyrate': float(Is_h2[2]),
                'propionate': float(Is_h2[3]),
            },
            'I_h2s': {
                'C4_valerate': float(Is_h2s[0]),
                'C4_butyrate': float(Is_h2s[1]),
                'propionate': float(Is_h2s[2]),
                'acetate': float(Is_h2s[3]),
                'hydrogen': float(Is_h2s[4]),
                'SRB_h2': float(Is_h2s[5]),
                'SRB_ac': float(Is_h2s[6]),
                'SRB_prop': float(Is_h2s[7]),
                'SRB_bu': float(Is_h2s[8]),
                'SRB_va': float(Is_h2s[9]),
            },
            'I_nutrients': {
                'I_IN_lim': float(substr_inhibit(S_IN, KS_IN)),
                'I_IP_lim': float(substr_inhibit(S_IP, KS_IP)),
                'combined': float(I_nutrients),
                'I_nh3': float(Inh3),
            },
            'I_Na': float(I_Na),
            'ionic_strength_molL': float(ionic_strength_molL),
            'I_trace': {k: float(v) for k, v in I_trace.items()},
            # configured = a validated contract is present; active = a factor was actually evaluated
            # and applied. They differ when a contract is configured but no metal is evaluable (no
            # inventory) — reporting only "active" would overstate what the model did.
            'trace_contract_configured': bool(trace_contract_configured),
            'trace_kinetics_active': bool(trace_kinetics_active),
            'trace_provenance': dict(trace_provenance),
            'Monod': monod_main.tolist(),
            'process_names': list(_MADM1_PROCESS_NAMES),
            'process_indices': {k: list(v) for k, v in _MADM1_PROCESS_INDICES.items()},
            'pao': {
                'vfa_Monod': np.asarray(pao_vfa_monod, dtype=float).tolist(),
                'pp_Monod': float(pao_pp_monod),
                'sulfate_Monod': float(pao_sulfate_monod),
                'vfa_distribution': np.asarray(pao_vfa_distribution, dtype=float).tolist(),
            },
            'srb': {
                'sulfate_Monod': np.asarray(srb_sulfate_monod, dtype=float).tolist(),
                'substrate_Monod': np.asarray(srb_substrate_monod, dtype=float).tolist(),
                'c4_distribution': np.asarray(srb_c4_distribution, dtype=float).tolist(),
            },
            'hfo': {
                'adsorption_factor': float(hfo_adsorption_factor),
                'desorption_factor': float(hfo_desorption_factor),
                'ligand_competition_configured': bool(
                    hfo_ligand_competition_configured
                ),
                'ligand_competition_active': bool(hfo_ligand_competition_active),
                'ligand_competition_factor': float(hfo_ligand_competition_factor),
                'ligand_model_id': (
                    ligand_contract['response_model']
                    if ligand_contract is not None
                    else 'none_unvalidated'
                ),
                'ligand_calibration_id': (
                    ligand_contract['calibration_id']
                    if ligand_contract is not None
                    else None
                ),
                'smp_or_doc_observation_id': (
                    ligand_contract['observation_id']
                    if ligand_contract is not None
                    else None
                ),
                'smp_or_doc_basis': (
                    ligand_contract['concentration_basis']
                    if ligand_contract is not None
                    else 'not_supplied'
                ),
                'observed_smp_or_doc_mg_C_L': (
                    ligand_contract['observed_smp_or_doc_mg_C_L']
                    if ligand_contract is not None
                    else None
                ),
                'ligand_calibration_source_kind': (
                    ligand_contract['calibration_source_kind']
                    if ligand_contract is not None
                    else None
                ),
                'ligand_matrix_scope': (
                    ligand_contract['matrix_scope']
                    if ligand_contract is not None
                    else None
                ),
                'ligand_validity_envelope': (
                    dict(ligand_contract['validity_envelope'])
                    if ligand_contract is not None
                    else None
                ),
                'host_reduction_processes': [
                    spec[0] for spec in _HFO_HOST_REDUCTION_SPECS
                ],
                'host_reduction_rate_constants': np.asarray(
                    host_reduction_constants, dtype=float
                ).tolist(),
                'bound_and_aged_host_destruction_represented': True,
                'bound_p_release_on_host_destruction_represented': True,
                'fes_reoxidation_to_hfo_represented': False,
            },
            'mmp': {
                'saturation_indices': np.asarray(SIs, dtype=float).tolist(),
                'mineral_names': list(mineral_names),
                'k_mmp': np.asarray(k_cryst, dtype=float).tolist(),
            },
            'gas_transfer': {
                'species': list(_MADM1_GAS_TRANSFER_SPECIES),
                'dissolved_kg_m3': np.asarray(biogas_S[gas_diag_order], dtype=float).tolist(),
                'KH_times_p_kg_m3': np.asarray(biogas_equilibrium[gas_diag_order], dtype=float).tolist(),
                'driving_force_kg_m3': np.asarray(gas_driving_force[gas_diag_order], dtype=float).tolist(),
                'kLa': float(kLa),
            },
            'biomass_kg_m3': {
                'X_su': float(state_arr[16]),
                'X_aa': float(state_arr[17]),
                'X_fa': float(state_arr[18]),
                'X_c4': float(state_arr[19]),
                'X_pro': float(state_arr[20]),
                'X_ac': float(state_arr[21]),
                'X_h2': float(state_arr[22]),
                'X_PAO': float(state_arr[26]),
                'X_hSRB': float(state_arr[31]),
                'X_aSRB': float(state_arr[32]),
                'X_pSRB': float(state_arr[33]),
                'X_c4SRB': float(state_arr[34]),
            },
            'process_rates': rhos.tolist(),
        }

    # Opt-in organic-sulfur extension (absent from params unless ModifiedADM1(organic_sulfur=True)): its rates are
    # APPENDED after the core 75 (process 75 on), first order in the zero-clamped organic-S inventory at protein's own
    # hydrolysis constant -- the X_pr convention of rhos[1] above (Cs zero-clamp: rates vanish at zero inventory).
    organic_sulfur = params.get('organic_sulfur')
    if organic_sulfur is not None:
        rhos = np.concatenate((rhos, [ks[k_index] * max(float(state_arr[x_index]), 0.0)
                                      for x_index, k_index in organic_sulfur['rates']]))

    # Opt-in micro-aeration extension: its ten rates (76-85) are APPENDED after organic sulfur's 75, each the kernel's
    # own expression at the twin's state, pH and headspace (models/madm1_microaeration.py); the gas rows of 76 and 77
    # reach the headspace through the reactor's NAMED extension positions, never through the gas-transfer slice.
    if microaeration is not None:
        if len(rhos) != microaeration['rate_offset']:
            raise RuntimeError(f"{len(rhos)} rates precede the micro-aeration extension, "
                               f"{microaeration['rate_offset']} expected (core + organic sulfur)")
        ma_rates, ma_symbols = _microaeration.microaeration_rates(state_arr, params, T_op, pH, R)
        rhos = np.concatenate((rhos, ma_rates))
        if root is not None:
            root.data['microaeration'] = {
                'extension_id': microaeration['extension_id'],
                'I_o2': float(I_o2),
                'derived_symbols': dict(ma_symbols),
                'rates': dict(zip(_microaeration.EXT_PROCESS_IDS, ma_rates.tolist())),
            }

    return rhos

#%% modified ADM1 class
_load_components = settings.get_default_chemicals

@chemicals_user
class ModifiedADM1(CompiledProcesses):
    'Modified Anaerobic Digestion Model no.1 [1]_, [2]_, [3]_'

    _cmp_dependent_stoichio = ('K_XPP', 'Mg_XPP',
                               'MW_S0', 'MW_IS',
                               'i_mass_S0', 'i_mass_IS', 'i_mass_Fe2')
    _stoichio_params = (*ADM1._stoichio_params[5:],
                        'f_ch_xb', 'f_pr_xb', 'f_li_xb', 'f_xI_xb', 'f_sI_xb',
                        'f_va_pha',	'f_bu_pha',	'f_pro_pha', 'f_ac_pha',
                        'f_is_pro', 'f_is_bu', 'f_is_va',
                        'Y_PO4', 'Y_hSRB', 'Y_aSRB', 'Y_pSRB', 'Y_c4SRB',
                        *_cmp_dependent_stoichio,
                        # the kernel's inert soluble product of acidogenic uptake: 0 unless soluble_inert_uptake=True
                        SOLUBLE_INERT_UPTAKE.name,
                        )
    _kinetic_params = ('rate_constants', 'half_sat_coeffs', 'K_PP', 'K_so4',
                       'pH_limits', 'KS_IN', 'KS_IP', 'KI_nh3', 'n_nh3', 'KIs_h2', 'KIs_h2s',
                       'inhibition_ext',
                       'Ka_base', 'Ka_dH', 'K_H_base', 'K_H_dH', 'kLa',
                       'hfo_host_reduction_rate_constants',
                       'k_cryst', 'n_cryst', 'mineral_seed_kg_m3',
                       'T_base', 'components',
                       'root'
                       )
    _acid_base_pairs = ADM1._acid_base_pairs
    _biogas_IDs = (*ADM1._biogas_IDs, 'S_IS')
    #: the gas-transfer rate positions the reactor's headspace balance reads (never "the last n_gas")
    gas_transfer_rate_slice = MADM1_GAS_RATE_SLICE
    #: the opt-in micro-aeration extension's headspace description (two more gas states at NAMED positions, the gas
    #: rows of rates 76 and 77): None on every model that does not compile it, so the reactor's balance is unchanged
    microaeration_headspace = None
    _biomass_IDs = (*ADM1._biomass_IDs, 'X_PAO', 'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB')
    _precipitates = ('X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP',
                    'X_struv', 'X_newb', 'X_magn', 'X_kstruv',
                    'X_FeS', 'X_Mackinawite', 'X_Fe3PO42', 'X_Siderite', 'X_AlPO4')
    _T_base = _K['T_base']
    _K_H_base = [_K['KH_h2'], _K['KH_ch4'], _K['KH_co2'], _K['KH_h2s']]    # biogas species Henry's Law constant [M/bar]
    _K_H_dH = [_K['dH_KH_h2'], _K['dH_KH_ch4'], _K['dH_KH_co2'], _K['dH_KH_h2s']]  # heat of liquid-gas transfer [J/mol]
    # The former _pKsp_base/_Ksp_dH tables are gone (fix/madm1-mineral-subsystem):
    # they were computed into params['Ksp'] and never read, and they mixed
    # proton-inclusive reaction conventions with the molar-IAP live path. The
    # single mineral registry lives in models/thermodynamics.MINERAL_PHASES,
    # which preserved the one value from the old table that was on the model
    # lineage's own convention (FeS, proton-inclusive, log K -2.95).

    def __new__(cls, components=None, path=None,
                f_ch_xb=_K['f_ch_xb'], f_pr_xb=_K['f_pr_xb'], f_li_xb=_K['f_li_xb'], f_xI_xb=_K['f_xI_xb'],
                f_fa_li=_K['f_fa_li'], f_bu_su=_K['f_bu_su'], f_pro_su=_K['f_pro_su'], f_ac_su=_K['f_ac_su'],
                f_va_aa=_K['f_va_aa'], f_bu_aa=_K['f_bu_aa'], f_pro_aa=_K['f_pro_aa'], f_ac_aa=_K['f_ac_aa'],
                f_ac_fa=_K['f_ac_fa'], f_pro_va=_K['f_pro_va'], f_ac_va=_K['f_ac_va'], f_ac_bu=_K['f_ac_bu'], f_ac_pro=_K['f_ac_pro'],
                Y_su=_K['Y_su'], Y_aa=_K['Y_aa'], Y_fa=_K['Y_fa'], Y_c4=_K['Y_c4'], Y_pro=_K['Y_pro'], Y_ac=_K['Y_ac'], Y_h2=_K['Y_h2'],
                f_va_pha=_K['f_va_pha'], f_bu_pha=_K['f_bu_pha'], f_pro_pha=_K['f_pro_pha'],
                Y_PO4=_K['Y_PO4'], Y_hSRB=_KS['Y_hSRB'], Y_aSRB=_KS['Y_aSRB'], Y_pSRB=_KS['Y_pSRB'], Y_c4SRB=_KS['Y_c4SRB'],
                q_ch_hyd=_K['q_ch_hyd'], q_pr_hyd=_K['q_pr_hyd'], q_li_hyd=_K['q_li_hyd'],
                k_su=_K['k_su'], k_aa=_K['k_aa'], k_fa=_K['k_fa'], k_c4=_K['k_c4'], k_pro=_K['k_pro'], k_ac=_K['k_ac'], k_h2=_K['k_h2'],
                K_su=_K['K_su'], K_aa=_K['K_aa'], K_fa=_K['K_fa'], K_c4=_K['K_c4'], K_pro=_K['K_pro'], K_ac=_K['K_ac'], K_h2=_K['K_h2'],
                b_su=_K['b_su'], b_aa=_K['b_aa'], b_fa=_K['b_fa'], b_c4=_K['b_c4'], b_pro=_K['b_pro'], b_ac=_K['b_ac'], b_h2=_K['b_h2'],
                q_pha=_K['q_pha'], b_pao=_K['b_pao'], b_pp=_K['b_pp'], b_pha=_K['b_pha'], K_A=_K['K_A'], K_PP=_K['K_PP'],
                k_hSRB=_KS['k_hSRB'], k_aSRB=_KS['k_aSRB'], k_pSRB=_KS['k_pSRB'], k_c4SRB=_KS['k_c4SRB'],
                b_hSRB=_KS['b_hSRB'], b_aSRB=_KS['b_aSRB'], b_pSRB=_KS['b_pSRB'], b_c4SRB=_KS['b_c4SRB'],
                K_hSRB=_KS['K_hSRB'], K_aSRB=_KS['K_aSRB'], K_pSRB=_KS['K_pSRB'], K_c4SRB=_KS['K_c4SRB'],
                K_so4_hSRB=_KS['K_so4_hSRB'], K_so4_aSRB=_KS['K_so4_aSRB'], K_so4_pSRB=_KS['K_so4_pSRB'], K_so4_c4SRB=_KS['K_so4_c4SRB'],
                # HFO reduction rate constants — literature-screening defaults,
                # NOT plant-calibrated. Poulton, Krom & Raiswell 2004 (GCA 68:3703,
                # 10.1016/j.gca.2004.03.012) ferrihydrite sulfidation t1/2 ~ hours at
                # 1 mM S(-II); Kraal et al. 2022 (10.1016/j.gca.2021.12.032)
                # ferrihydrite near-complete sulfidation over ~14 d at 2-10 mM
                # => screening k_Fe3t2 ~ 4.7e5/Fe_mw m3/kmol/d. Site-specific
                # synchronized Fe/S measurements are still required to fit it.
                k_Fe3t2_h2=_KS['k_Fe3t2_h2'],
                k_Fe3t2_is=_KS['k_Fe3t2_is'],
                q_aging_H=_KS['q_aging_H'], q_aging_L=_KS['q_aging_L'], q_Pcoprec=_KS['q_Pcoprec'], q_Pbinding=_KS['q_Pbinding'], q_diss_H=_KS['q_diss_H'], q_diss_L=_KS['q_diss_L'],
                hfo_bound_reduction_factor=_KS['hfo_bound_reduction_factor'],
                hfo_aged_reduction_factor=_KS['hfo_aged_reduction_factor'],
                hfo_aged_bound_reduction_factor=_KS['hfo_aged_bound_reduction_factor'],
                K_Pbind=_KS['K_Pbind'], K_Pdiss=_KS['K_Pdiss'], # 1.20 and 0.03 in MATLAB, assuming in kmol-P/m3 ?
                # KI_nh3 = 1.8e-3 M (~25 mg N/L) is the ADM1 default, kept so existing
                # calibrations are bit-identical. It is NOT a safe default for a
                # high-ammonia plant: it is widely reported to predict process failure
                # that the reactor does not show, and published re-fits move it toward
                # 8e-3. Treat it as not_identifiable until fitted against site data --
                # but not_identifiable does not mean unbounded. Literature priors for
                # the fit: FAN IC50 spans 39-2,565 mg NH3-N/L across studies
                # (Capson-Tojo et al. 2020, "Unraveling the literature chaos around
                # free ammonia inhibition", Renew. Sustain. Energy Rev.,
                # doi 10.1016/j.rser.2019.109487); unacclimated mesophilic sludge
                # sits near ~215 mg FA/L IC50, while acclimated high-ammonia
                # digesters report ~500-2,500 (equivalently KI_nh3 ~ 3.6e-2 to
                # 1.8e-1 M for the non-competitive form). A fitted value falling
                # outside that band is a red flag on the fit, not a discovery.
                # n_nh3 = 1 reproduces the shipped non-competitive form exactly; > 1
                # gives the threshold-like shape of Astals et al. (2018).
                KI_h2_fa=_K['KI_h2_fa'], KI_h2_c4=_K['KI_h2_c4'], KI_h2_pro=_K['KI_h2_pro'], KI_nh3=_K['KI_nh3'], n_nh3=_K['n_nh3'], KS_IN=_K['KS_IN'], KS_IP=_K['KS_IP'],
                KI_h2s_c4=_K['KI_h2s_c4'], KI_h2s_pro=_K['KI_h2s_pro'], KI_h2s_ac=_K['KI_h2s_ac'], KI_h2s_h2=_K['KI_h2s_h2'],
                KI_h2s_c4SRB=_KS['KI_h2s_c4SRB'], KI_h2s_pSRB=_KS['KI_h2s_pSRB'], KI_h2s_aSRB=_KS['KI_h2s_aSRB'], KI_h2s_hSRB=_KS['KI_h2s_hSRB'],
                # Extended inhibition/limitation (QSDsan-only). KI_Na: non-competitive Na/osmotic
                # half-inhibition (~0.3 mol/L ~ 7 g/L, Chen/Cheng/Creamer 2008) — a literature-held term,
                # always applied. Trace-element (Co/Ni/Se) micronutrient limitation is QUARANTINED: it has
                # NO numeric defaults and touches reaction rates ONLY via a complete, validated
                # `trace_kinetics` calibration contract (see _validate_trace_kinetics). Absent it, trace
                # inventory is a strict rate no-op. The former KS_Co/KS_Ni/KS_Se defaults were literature
                # dose/optimum magnitudes mis-shipped as Monod half-saturations (fix/madm1-trace-quarantine).
                KI_Na=_K['KI_Na'], trace_kinetics=None, hfo_ligand_competition=None,
                model_reduction=None,
                
                # hydrolysis_organic_S (X_Sorg -> S_IS at q_pr_hyd) AFTER the gas transfers; needs the component set
                # of create_madm1_cmps(organic_sulfur=True). False (default): the 75-process model, unchanged.
                organic_sulfur=False,
                # Opt-in micro-aeration extension (PUBLIC-RULE-6dce80fc160d; needs
                # organic_sulfur=True and the component set of create_madm1_cmps(organic_sulfur=True,
                # microaeration=True)): True appends processes 76-85 AFTER process 75, applies the O2-inhibition
                # factor to the uptakes the kernel names and adds two headspace states (S_o2, S_n2).
                # ``microaeration_parameters`` overrides the EXTENSION's own parameters (a bracket edge). False
                # (default): unchanged.
                microaeration=False, microaeration_parameters=None,
                # Opt-in inert soluble product of sugar and amino-acid uptake (owner ruling
                
                # uptake rows to the kernel's (S_I += Y_sI_upt, products x (1-Y-Y_sI_upt)). ``Y_sI_upt`` overrides the
                # kernel's value and is refused without the flag. False (default): the TSV's rows, unchanged.
                soluble_inert_uptake=False, Y_sI_upt=None,
                pH_limits_aa=(_K['pH_LL_aa'], _K['pH_UL_aa']), pH_limits_ac=(_K['pH_LL_ac'], _K['pH_UL_ac']), pH_limits_h2=(_K['pH_LL_h2'], _K['pH_UL_h2']),
                pH_limits_aa_SRB=(_KS['pH_LL_aa_SRB'], _KS['pH_UL_aa_SRB']), pH_limits_ac_SRB=(_KS['pH_LL_ac_SRB'], _KS['pH_UL_ac_SRB']), pH_limits_h2_SRB=(_KS['pH_LL_h2_SRB'], _KS['pH_UL_h2_SRB']),
                kLa=_K['kLa'], pKa_base=[_K['pKa_w'], _K['pKa_nh4'], _K['pKa_co2'], _K['pKa_ac'], _K['pKa_pro'], _K['pKa_bu'], _K['pKa_va']],
                Ka_dH=[_K['dH_Ka_w'], _K['dH_Ka_nh4'], _K['dH_Ka_co2'], _K['dH_Ka_ac'], _K['dH_Ka_pro'],
                       _K['dH_Ka_bu'], _K['dH_Ka_va']],
                k_cryst=[0.35, 1e-3, 3.0, 1e-3, 2.0, 0.76, 5.0, 1e-3,
                         1e-3, 1e-3, _KS['k_cryst_FeS'], 1e2, 1e-3, 1e-3, 1e-3],
                n_cryst=[2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2, 2, 2],
                # Per-phase nucleation-surrogate seed inventory [kg/m3]: under
                # supersaturation the crystallization rate sees at least this
                # inventory, so precipitation can start from X=0 without the old
                # unphysical influent mass floor. Scalar broadcasts to every phase.
                mineral_seed_kg_m3=1e-6,
                **kwargs):

        cmps = _load_components(components)

        # Fail closed on the quarantined trace-element parameters. These are no longer signature
        # parameters, so without this guard they would be silently swallowed by **kwargs (and land in
        # dct via dct.update(kwargs)) — a legacy caller would think trace kinetics were active when they
        # are inert. Reject explicitly and point at the calibration contract.
        _legacy_trace_keys = {'trace_active', 'KS_Co', 'KS_Ni', 'KS_Se',
                              'f_bioavail_floor', 'f_bioavail_k_sulf'}
        _leaked = _legacy_trace_keys.intersection(kwargs)
        if _leaked:
            raise ValueError(
                f"ModifiedADM1 received legacy trace-element parameter(s) {sorted(_leaked)}. These were "
                "removed (fix/madm1-trace-quarantine): literature dose/optimum magnitudes had been shipped "
                "as Monod half-saturations. Pass a complete, validated `trace_kinetics` calibration "
                "contract instead, or omit them entirely (trace inventory then has no effect on rates).")

        # The organic-sulfur opt-in and the component set must agree: a set carrying X_Sorg without process 75
        # would hold organic S that never releases, and process 75 without X_Sorg has nothing to act on.
        organic_sulfur = _organic_sulfur_flag(organic_sulfur)
        # The micro-aeration opt-in, the organic-sulfur opt-in and the component set must agree the same way: S_O2
        # without the extension's processes would be an oxygen state nothing consumes, and the processes without it
        # have nothing to read.
        microaeration = _microaeration_flag(microaeration, organic_sulfur)
        # The acidogenic inert soluble product: the kernel's value when asked, 0 (no row rewritten) otherwise.
        soluble_inert_uptake = _soluble_inert_uptake_flag(soluble_inert_uptake)
        if Y_sI_upt is not None and not soluble_inert_uptake:
            raise ValueError("Y_sI_upt was given but the inert soluble product was not asked for: pass "
                             "soluble_inert_uptake=True")
        if soluble_inert_uptake:
            Y_sI_upt = float(_K[SOLUBLE_INERT_UPTAKE.name] if Y_sI_upt is None else Y_sI_upt)
            if not (np.isfinite(Y_sI_upt) and 0.0 <= Y_sI_upt < 1.0 - max(Y_su, Y_aa)):
                raise ValueError(f"Y_sI_upt must be finite, >= 0 and leave the acidogens' products positive, "
                                 f"got {Y_sI_upt!r}")
        else:
            Y_sI_upt = 0.0
        _ma_ids = _microaeration.EXT.component_ids
        _ma_present = [cid for cid in _ma_ids if cid in cmps.IDs]
        if microaeration and len(_ma_present) != len(_ma_ids):
            raise ValueError("ModifiedADM1(microaeration=True) needs the component set of "
                             "create_madm1_cmps(organic_sulfur=True, microaeration=True); missing "
                             f"{sorted(set(_ma_ids) - set(_ma_present))}")
        if not microaeration and _ma_present:
            raise ValueError(f"the component set carries the micro-aeration extension {_ma_present} but "
                             "ModifiedADM1 was not asked for it: pass microaeration=True")
        if microaeration_parameters is not None and not microaeration:
            raise ValueError("microaeration_parameters were given but the micro-aeration extension was not asked "
                             "for: pass microaeration=True")
        # the extension's values with any stated override of ITS OWN parameters (a base-kernel name is refused
        # here, before anything is built: it is this model's own argument)
        _ma_values = _microaeration.microaeration_values(microaeration_parameters) if microaeration else None
        if microaeration and (model_reduction or {}).get('disable_p_limitation'):
            # the kernel's I1 (the acidogens' nutrient x pH factor) carries the P limitation; the reduced observer
            # drops it from the base uptakes, and no reduced form of the extension is defined
            raise ValueError("the micro-aeration extension has no reduced-observer form: its aerobic uptakes read "
                             "the phosphorus limitation that model_reduction['disable_p_limitation'] switches off")
        _ext_ids = MADM1_ORGANIC_SULFUR_EXTENSION.component_ids
        _present = [cid for cid in _ext_ids if cid in cmps.IDs]
        if organic_sulfur and len(_present) != len(_ext_ids):
            raise ValueError("ModifiedADM1(organic_sulfur=True) needs the component set of "
                             "create_madm1_cmps(organic_sulfur=True); missing "
                             f"{sorted(set(_ext_ids) - set(_present))}")
        if not organic_sulfur and _present:
            raise ValueError(f"the component set carries the organic-sulfur extension {_present} but "
                             "ModifiedADM1 was not asked for it: pass organic_sulfur=True")
        if organic_sulfur:
            # the core processes below are built on the GLOBAL thermo's chemicals, so it must be this set
            try:
                _active = _load_components(None).IDs
            except Exception:
                _active = ()
            if tuple(_active) != tuple(cmps.IDs):
                if microaeration:
                    raise ValueError("ModifiedADM1(microaeration=True) must be built under the micro-aeration "
                                     "thermo: call utils.simulate_madm1.ensure_madm1_thermo(organic_sulfur=True, "
                                     "microaeration=True) first")
                raise ValueError("ModifiedADM1(organic_sulfur=True) must be built under the organic-sulfur thermo: "
                                 "call utils.simulate_madm1.ensure_madm1_thermo(organic_sulfur=True) first")

        if not path: path = _path
        self = Processes.load_from_file(path,
                                        components=cmps,
                                        conserved_for=('C', 'N', 'P'),
                                        parameters=cls._stoichio_params,
                                        compile=False)
        _correct_srb_sulfur_mass_basis(self, cmps)   # COD->S basis fix for SRB S_SO4/S_IS (codex)
        _correct_hfo_reduction_mass_basis(self, cmps)
        _add_hfo_host_reduction_processes(self, cmps)
        if soluble_inert_uptake:
            _add_soluble_inert_uptake_product(self)

        for i in ('fast_P_binding', 'slow_P_sorption', 'dissolution_HFO_HP', 'dissolution_HFO_LP'):
            p = getattr(self, i)
            p.ref_component = 'S_IP'

        precipitation = []
        for i in cls._precipitates[:10]:
            new_p = Process('precipitation_%s' % i.lstrip('X_'),
                            reaction='[?]S_IC + [?]S_IN + [?]S_IP + [?]S_K + [?]S_Mg + [?]S_Ca -> %s' % i,
                            ref_component=i,
                            conserved_for=('C', 'N', 'P', 'K', 'Mg', 'Ca'),
                            parameters=())
            precipitation.append(new_p)

        i_mass_IS = cmps.S_IS.i_mass
        i_mass_Fe2 = cmps.S_Fe2.i_mass
        for phase_id in ('X_FeS', 'X_Mackinawite'):
            phase_mw = getattr(cmps, phase_id).chem_MW
            new_p = Process('precipitation_' + phase_id.lstrip('X_'),
                            reaction={'S_Fe2': -Fe_mw/phase_mw/i_mass_Fe2,
                                      # S_IS is measured_as='S': its native
                                      # state is already kg S, not kg H2S.
                                      # Dividing by i_mass_IS under-consumed S
                                      # by ~5.9% and made FeS precipitation
                                      # manufacture sulfur.
                                      'S_IS': -S_mw/phase_mw,
                                      phase_id: 1},
                            ref_component=phase_id,
                            conserved_for=())
            precipitation.append(new_p)

        Fe3PO42_mw = cmps.X_Fe3PO42.chem_MW
        new_p = Process('precipitation_Fe3PO42',
                        reaction={'S_Fe2': -3*Fe_mw/Fe3PO42_mw/i_mass_Fe2,
                                  'S_IP': '?',
                                  'X_Fe3PO42': 1},
                        ref_component='X_Fe3PO42',
                        conserved_for=('P',))
        precipitation.append(new_p)

        siderite_mw = cmps.X_Siderite.chem_MW
        i_mass_IC = cmps.S_IC.i_mass
        C_mw = get_mw({'C': 1})
        new_p = Process('precipitation_Siderite',
                        reaction={'S_Fe2': -Fe_mw/siderite_mw/i_mass_Fe2,
                                  'S_IC': -C_mw/siderite_mw/i_mass_IC,
                                  'X_Siderite': 1},
                        ref_component='X_Siderite',
                        conserved_for=())
        precipitation.append(new_p)

        AlPO4_mw = cmps.X_AlPO4.chem_MW
        Al_mw = cmps.S_Al.chem_MW
        new_p = Process('precipitation_AlPO4',
                        reaction={'S_Al': -Al_mw/AlPO4_mw,
                                  'S_IP': '?',
                                  'X_AlPO4': 1},
                        ref_component='X_AlPO4',
                        conserved_for=('P',))
        precipitation.append(new_p)

        self.extend(precipitation)

        gas_transfer = []
        for i in cls._biogas_IDs:
            new_p = Process('%s_transfer' % i.lstrip('S_'),
                            reaction={i:-1},
                            ref_component=i,
                            conserved_for=(),
                            parameters=())
            gas_transfer.append(new_p)
        self.extend(gas_transfer)
        if organic_sulfur:
            # Appended AFTER the four gas transfers, at the indices the extension names (75 on), so the named gas
            # slice (71:75) does not move. The coefficients are the extension's own (both kg S), never restated.
            self.extend([
                Process(p.process_id,
                        reaction={cid: float(coef.value) for cid, coef in p.stoichiometry.items()},
                        ref_component=p.reference_component,
                        components=cmps,
                        conserved_for=(),
                        parameters=())
                for p in MADM1_ORGANIC_SULFUR_EXTENSION.processes
            ])
        if microaeration:
            # Appended AFTER organic sulfur's process 75, at the indices the extension names (76-85). The liquid
            # coefficients are the extension's own, evaluated at its values (with any stated override); the C/N/P
            # carriers are closed by QSDsan from the component contents, as the core table's are.
            self.extend(_microaeration.microaeration_processes(cmps, _ma_values))
        self.compile(to_class=cls)
        if organic_sulfur:
            for p in MADM1_ORGANIC_SULFUR_EXTENSION.processes:
                if self.IDs.index(p.process_id) != p.index:
                    raise RuntimeError(f"{p.process_id} compiled at {self.IDs.index(p.process_id)}, "
                                       f"the extension names {p.index}")

        stoichio_vals = (f_fa_li, f_bu_su, f_pro_su, f_ac_su, 1-f_bu_su-f_pro_su-f_ac_su,
                         f_va_aa, f_bu_aa, f_pro_aa, f_ac_aa, 1-f_va_aa-f_bu_aa-f_pro_aa-f_ac_aa,
                         f_ac_fa, 1-f_ac_fa, f_pro_va, f_ac_va, 1-f_pro_va-f_ac_va,
                         f_ac_bu, 1-f_ac_bu, f_ac_pro, 1-f_ac_pro,
                         Y_su, Y_aa, Y_fa, Y_c4, Y_pro, Y_ac, Y_h2,
                         f_ch_xb, f_pr_xb, f_li_xb, f_xI_xb, round(1.0-f_ch_xb-f_pr_xb-f_li_xb-f_xI_xb, 4),
                         f_va_pha, f_bu_pha, f_pro_pha, 1-f_va_pha-f_bu_pha-f_pro_pha,
                         1-f_ac_pro, 1-f_ac_bu, 1-f_pro_va-f_ac_va,
                         Y_PO4, Y_hSRB, Y_aSRB, Y_pSRB, Y_c4SRB,
                         cmps.X_PP.i_K, cmps.X_PP.i_Mg,
                         cmps.S_S0.chem_MW, cmps.S_IS.chem_MW,
                         cmps.S_S0.i_mass, i_mass_IS, i_mass_Fe2,
                         Y_sI_upt)

        pH_limits = np.array([pH_limits_aa, pH_limits_ac, pH_limits_h2,
                              pH_limits_h2_SRB, pH_limits_ac_SRB, pH_limits_aa_SRB]).T

        ks = np.array((q_ch_hyd, q_pr_hyd, q_li_hyd,
                       k_su, k_aa, k_fa, k_c4, k_c4, k_pro, k_ac, k_h2,
                       b_su, b_aa, b_fa, b_c4, b_pro, b_ac, b_h2,               # original ADM1
                       q_pha, q_pha, q_pha, q_pha, b_pao, b_pp, b_pha,          # P extension
                       k_hSRB, b_hSRB, k_aSRB, b_aSRB, k_pSRB, b_pSRB, k_c4SRB, k_c4SRB, b_c4SRB, # S extension
                       k_Fe3t2_h2, k_Fe3t2_h2, k_Fe3t2_is, k_Fe3t2_is,          # Fe extension
                       q_aging_H, q_aging_L, q_Pcoprec, q_Pbinding,             # HFO module
                       q_aging_H, q_aging_L, q_diss_H, q_diss_L))

        Ks = np.array((K_su, K_aa, K_fa, K_c4, K_c4, K_pro, K_ac, K_h2,         # original ADM1
                       K_A,                                                     # P extension
                       K_hSRB, K_aSRB, K_pSRB, K_c4SRB, K_c4SRB,                # S extension (duplicate c4 for butyrate & valerate)
                       K_Pbind, K_Pdiss))                                       # HFO module
        K_so4 = np.array((K_so4_hSRB, K_so4_aSRB, K_so4_pSRB, K_so4_c4SRB, K_so4_c4SRB))  # Duplicate c4 for butyrate & valerate

        KIs_h2 = np.array((KI_h2_fa, KI_h2_c4, KI_h2_c4, KI_h2_pro))
        KIs_h2s = np.array((KI_h2s_c4, KI_h2s_c4, KI_h2s_pro, KI_h2s_ac, KI_h2s_h2,
                            KI_h2s_hSRB, KI_h2s_aSRB, KI_h2s_pSRB, KI_h2s_c4SRB, KI_h2s_c4SRB))
        K_H_base = np.array(cls._K_H_base)
        K_H_dH = np.array(cls._K_H_dH)
        Ka_base = np.array([10**(-pKa) for pKa in pKa_base])
        Ka_dH = np.array(Ka_dH)
        k_cryst = np.array(k_cryst) * 24    # converted to d^(-1)
        n_cryst = np.array(n_cryst)
        n_minerals = len(cls._precipitates)
        if k_cryst.shape != (n_minerals,) or n_cryst.shape != (n_minerals,):
            raise ValueError(
                "k_cryst and n_cryst must each contain one value per mineral "
                f"phase ({n_minerals}); received {k_cryst.size} and {n_cryst.size}. "
                "Legacy 13-phase parameter sets must be explicitly migrated for "
                "Mackinawite and Siderite rather than silently broadcast."
            )
        mineral_seed = np.broadcast_to(
            np.asarray(mineral_seed_kg_m3, dtype=float), (n_minerals,)).copy()
        if (mineral_seed < 0).any():
            raise ValueError("mineral_seed_kg_m3 must be non-negative")

        hfo_history_factors = {
            'fresh_bound': float(hfo_bound_reduction_factor),
            'aged_unbound': float(hfo_aged_reduction_factor),
            'aged_bound': float(hfo_aged_bound_reduction_factor),
        }
        invalid_hfo_factors = sorted(
            name
            for name, factor in hfo_history_factors.items()
            if not np.isfinite(factor) or factor < 0.0
        )
        if invalid_hfo_factors:
            raise ValueError(
                "HFO host-reduction factors must be finite and non-negative; "
                f"invalid={invalid_hfo_factors}"
            )
        hfo_host_reduction_rate_constants = np.asarray(
            [
                (k_Fe3t2_h2 if donor_id == 'h2' else k_Fe3t2_is)
                * hfo_history_factors[history_class]
                for _process_id, _host_id, donor_id, history_class
                in _HFO_HOST_REDUCTION_SPECS
            ],
            dtype=float,
        )

        # Create TempState object for storing intermediate calculation results
        # It needs a .data attribute that can be copied and used as a dict
        class TempState:
            def __init__(self):
                self.data = {}  # Temporary storage for solver state (dictionary)

        root = TempState()
        dct = self.__dict__
        dct.update(kwargs)

        dct['_parameters'] = dict(zip(cls._stoichio_params, stoichio_vals))

        # Wrapper to adapt rhos_madm1's 3-argument signature to QSDsan's expected 2-argument signature
        # T_op is retrieved from the reactor temperature during simulation
        # h parameter is optional - used for pH control (passed as kwarg from AnaerobicCSTRmADM1._compile_ODE)
        # **kwargs captures h without violating QSDsan's 2-argument requirement
        def rhos_wrapper(state_arr, params, **kwargs):
            # Get T_op from params dict (set by reactor during simulation)
            T_op = params.get('T_op', cls._T_base)
            # Extract h from kwargs (None if not provided)
            h = kwargs.get('h', None)
            return rhos_madm1(state_arr, params, T_op, h=h)

        # Extended inhibition/limitation config (QSDsan-only): a single documented dict threaded through
        # the kinetic params. Na/osmotic is a literature-held non-competitive term and always applies.
        # Trace-element (Co/Ni/Se) limitation is QUARANTINED: `trace_kinetics` is None (inventory only,
        # exact rate neutrality) unless a complete validated calibration contract is supplied, in which
        # case _validate_trace_kinetics returns {'parameters': {metal: KS_mol_L}, 'guilds': {...}}. Guild
        # indices in rhos: 9 = acetoclastic, 10 = hydrogenotrophic methanogens; 6/7/8 = valerate/butyrate/
        # propionate syntrophic VFA oxidisers.
        inhibition_ext = {
            'KI_Na': float(KI_Na),
            'na_guilds': (9, 10),
            'trace_kinetics': _validate_trace_kinetics(trace_kinetics),
            'hfo_ligand_competition': _validate_hfo_ligand_competition(
                hfo_ligand_competition
            ),
        }

        self.set_rate_function(rhos_wrapper)
        params_dict = dict(zip(cls._kinetic_params,
                               [ks, Ks, K_PP, K_so4,
                                pH_limits, KS_IN*N_mw, KS_IP*P_mw,
                                KI_nh3, n_nh3, KIs_h2, KIs_h2s,
                                inhibition_ext,
                                Ka_base, Ka_dH, K_H_base, K_H_dH, kLa,
                                hfo_host_reduction_rate_constants,
                                k_cryst, n_cryst, mineral_seed,
                                cls._T_base, self._components,
                                root,
                                ]))
        self.rate_function._params = params_dict

        # Reduced-observer feature flags (default: full model, bit-identical to prior behaviour). A plant
        # whose chemistry sub-model is unidentifiable from routine signals (no ion/TDS/metals/orthophosphate/
        # TAN assay) opts into a reduced mechanistic observer that disables the mineral-precipitation
        # subsystem + phosphate limitation/charge and the unassayed sulfide/ammonia inhibition, leaving the
        # identifiable hydrolysis/acidogenesis/acetogenesis/methanogenesis + C/N/VFA/CH4/carbonate core.
        _mr = model_reduction or {}
        _hfo = _mr.get('disable_hfo_p')
        self.rate_function._params['model_reduction'] = {
            'disable_minerals': bool(_mr.get('disable_minerals', False)),
            'disable_p_limitation': bool(_mr.get('disable_p_limitation', False)),
            'exclude_sip_from_charge': bool(_mr.get('exclude_sip_from_charge', False)),
            'disable_h2s_inhibition': bool(_mr.get('disable_h2s_inhibition', False)),
            'disable_nh3_inhibition': bool(_mr.get('disable_nh3_inhibition', False)),
            
            # an explicit bool controls the HFO chemical-P rows independently.
            'disable_hfo_p': None if _hfo is None else bool(_hfo),
        }

        if organic_sulfur:
            # The extension's rate laws, read by rhos_madm1 AFTER the core 75: (state index of the hydrolysed
            # component, index of its rate constant in ks). q_pr_hyd is ks[1] (the tuple above), so organic S
            # follows protein through any override or temperature adjustment of q_pr_hyd.
            _rate_constant_index = {'q_pr_hyd': 1}[ORGANIC_SULFUR_RATE_CONSTANT]
            if ks[_rate_constant_index] != q_pr_hyd:
                raise RuntimeError("ks[1] is not q_pr_hyd: the organic-sulfur rate would read the wrong constant")
            ext = MADM1_ORGANIC_SULFUR_EXTENSION
            rates = []
            for offset, p in enumerate(ext.processes):
                if p.index != MADM1_CORE_RATE_COUNT + offset:
                    raise RuntimeError(f"{p.process_id}: index {p.index} does not follow the core rate vector")
                rates.append((cmps.index(_first_order_rate_component(p)), _rate_constant_index))
            self.rate_function._params['organic_sulfur'] = {
                'extension_id': ext.extension_id,
                'sha256': ext.sha256,
                'rates': tuple(rates),
            }

        if microaeration:
            # The extension's rate laws are evaluated by rhos_madm1 AFTER organic sulfur's rate, from the kernel's
            # own expressions; the base-kernel parameters they read (k_su, k_aa, k_fa, KS_IN, KS_IP, the acidogens'
            # pH window, KI_h2_fa) are bound to this model's LIVE values and checked here against what it was built
            # with. The wetted headspace area is geometry: the reactor binds it (never defaulted).
            from .reactors import madm1_gas_mass_to_molar_conversion
            self.rate_function._params['microaeration'] = _microaeration.microaeration_rate_parameters(
                cmps, _ma_values, live_params=self.rate_function._params,
                expected_base={'k_su': k_su, 'k_aa': k_aa, 'k_fa': k_fa, 'KS_IN': KS_IN, 'KS_IP': KS_IP,
                               'MW_N': N_mw, 'MW_P': P_mw, 'pH_LL_aa': pH_limits_aa[0],
                               'pH_UL_aa': pH_limits_aa[1], 'KI_h2_fa': KI_h2_fa},
                preceding_rate_count=MADM1_CORE_RATE_COUNT + len(MADM1_ORGANIC_SULFUR_EXTENSION.processes))
            _base_gas_idx = cmps.indices(cls._biogas_IDs)
            dct['microaeration_headspace'] = _microaeration.microaeration_headspace(
                _ma_values, biogas_ids=cls._biogas_IDs,
                base_molar_conversion=dict(zip(cls._biogas_IDs, madm1_gas_mass_to_molar_conversion(
                    cmps, _base_gas_idx, cls._biogas_IDs))))

        # Pre-compute stoichiometric rows for gas-phase supersaturation correction
        # Only the non-transfer processes (every column but the named gas transfers) contribute biologically
        # Must be done AFTER params are set so stoichio_eval() can lambdify properly
        stoichio_mass = self.stoichio_eval().T  # shape: (n_components, n_processes)
        n_gas = len(cls._biogas_IDs)
        non_gas = madm1_non_gas_rate_mask(stoichio_mass.shape[1])
        ic_idx = cmps.index('S_IC')
        supersat_data = {
            'n_gas': n_gas,
            'nu_IC': stoichio_mass[ic_idx, non_gas].copy(),
        }
        if 'S_IS' in cmps.IDs:
            supersat_data['nu_IS'] = stoichio_mass[cmps.index('S_IS'), non_gas].copy()
        else:
            supersat_data['nu_IS'] = None

        # Add supersaturation data to params
        self.rate_function._params['gas_supersat'] = supersat_data
        return self

    def solve_pH(self, state_arr, params=None):
        """
        Solve for pH using the PCM model.

        This method is expected by QSDsan's AnaerobicCSTR reactor.
        It wraps our pcm() function to provide the interface the reactor needs.

        Parameters
        ----------
        state_arr : ndarray
            State variable array
        params : dict, optional
            Kinetic parameters (if None, uses self._rhos_func.params)

        Returns
        -------
        float
            Computed pH value
        """
        if params is None:
            params = self._rhos_func.params if hasattr(self, '_rhos_func') else {}

        pH, nh3, co2, acts = pcm(state_arr, params)
        return pH

    def dydt_Sh2_AD(self, S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in):
        """
        H2 mass balance residual for Newton solver.

        This method is expected by QSDsan's AnaerobicCSTR reactor for H2 tracking.

        Parameters
        ----------
        S_h2 : float
            Trial H2 concentration [kg/m³]
        state_arr : ndarray
            State variable array (will be temporarily modified)
        h : tuple
            Pre-computed (pH, nh3, co2, acts) from pcm()
        params : dict
            Kinetic parameters
        f_stoichio : callable
            Stoichiometry function
        V_liq : float
            Liquid volume [m³]
        S_h2_in : float
            Influent H2 concentration [kg/m³]

        Returns
        -------
        float
            Residual of H2 mass balance
        """
        return dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in)

    def grad_dydt_Sh2_AD(self, S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps=_H2_EPS):
        """
        Gradient of H2 mass balance residual using numerical differentiation.

        This method is expected by QSDsan's AnaerobicCSTR reactor for H2 tracking.

        Parameters
        ----------
        S_h2 : float
            Trial H2 concentration [kg/m³]
        state_arr : ndarray
            State variable array
        h : tuple
            Pre-computed (pH, nh3, co2, acts) from pcm()
        params : dict
            Kinetic parameters
        f_stoichio : callable
            Stoichiometry function
        V_liq : float
            Liquid volume [m³]
        S_h2_in : float
            Influent H2 concentration [kg/m³]
        eps : float, optional
            Finite difference step size

        Returns
        -------
        float
            Gradient dR/dS_h2
        """
        return grad_dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps)
