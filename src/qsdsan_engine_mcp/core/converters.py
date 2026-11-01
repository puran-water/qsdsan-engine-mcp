"""
State converters backed by the upstream QSDsan ADM1p <-> mASM2d A1 junctions.

The public conversion surface is intentionally junction-only. Standalone
conversion now uses the same canonical upstream A1 basis that QSDsan exposes
for plant-wide modified-chemistry coupling, with explicit carried residuals for
repo-only species that do not have exact counterparts in the upstream core.
"""

import logging
from itertools import count
from typing import Dict, Any, Tuple, Optional, Iterable

import numpy as np
from qsdsan_engine_mcp.core.contracts.stream_state import StreamState
from qsdsan_engine_mcp.core.model_types import ModelType, model_type_value
from qsdsan_engine_mcp.core.model_registry import MADM1_COMPONENTS, ASM2D_COMPONENTS, MASM2D_COMPONENTS
from qsdsan_engine_mcp.core.components.catalog import COMPONENT_CATALOG, MODEL_COMPONENTS, STREAM_STATE_EXTRA_COMPONENTS
from qsdsan_engine_mcp.core.adm1p_projection import project_madm1_to_adm1p_core, reconstruct_madm1_from_adm1p_core, project_asm_family_to_masm2d_core, reconstruct_asm_family_from_masm2d_core

logger = logging.getLogger(__name__)

__all__ = [
    'convert_state',
    'create_junction_unit',
    'extract_component_coefficients',
    'get_coefficients',
    'validate_mass_balance',
    'validate_charge_balance',
    'validate_state_consistency',
    'InvalidComponentCoefficientError',
    'UnknownModelCoefficientsError',
    'UnresolvedComponentCoefficientsError',
    'ZERO_COEFFICIENT_COMPONENTS',
]

# =============================================================================
# Dynamic Coefficient Extraction (Phase 2B)
# =============================================================================

# Module-level cache for extracted coefficients
_coefficient_cache: Dict[str, Dict[str, Dict[str, float]]] = {}
_junction_counter = count()

# Model identifiers the coefficient surface accepts (mirrors the branch tables in
# extract_component_coefficients / get_coefficients).
_COEFFICIENT_MODEL_TYPES: Tuple[str, ...] = (
    'mADM1', 'ADM1', 'ADM1p', 'ADM1_p_extension',
    'ASM1', 'ASM2d', 'mASM2d', 'mASM2d2N', 'mASM2d2N_N2O',
)

_COEFFICIENT_DIMENSIONS: Tuple[str, ...] = ('i_COD', 'i_N', 'i_P')


class UnknownModelCoefficientsError(ValueError):
    """COD/N/P coefficients were requested for a model this engine does not define."""

    def __init__(self, model_type: Any):
        self.model_type = model_type
        valid = ", ".join(_COEFFICIENT_MODEL_TYPES)
        super().__init__(
            f"Unknown model type for COD/N/P coefficient extraction: {model_type!r}. "
            f"Valid: {valid}. Pass one of the valid identifiers (see "
            "core.components.catalog.normalize_model_type) or register the model's "
            "components in the catalog before requesting conversion coefficients."
        )


class InvalidComponentCoefficientError(ValueError):
    """A component's stoichiometric coefficient is present but not a usable number."""

    def __init__(self, model_type: str, component_id: str, dimension: str, value: Any, reason: str):
        self.model_type = model_type
        self.component_id = component_id
        self.dimension = dimension
        super().__init__(
            f"Component {component_id!r} of model {model_type!r} has {reason} {dimension} "
            f"value ({value!r}). Fix the component definition so {dimension} is numeric, "
            "or — only if the coefficient is genuinely zero — add the component to "
            "ZERO_COEFFICIENT_COMPONENTS in core/converters.py."
        )


class UnresolvedComponentCoefficientsError(ValueError):
    """Components could not be resolved to explicit COD/N/P coefficients."""

    def __init__(self, model_type: str, unresolved: Iterable[str], *, context: str):
        self.model_type = model_type
        self.unresolved = tuple(sorted(set(unresolved)))
        super().__init__(
            f"{context} for model {model_type!r}: {', '.join(self.unresolved)}. "
            "Conversion totals cannot silently under-count; add the component to the "
            "coefficient source or ZERO_COEFFICIENT_COMPONENTS in core/converters.py."
        )


# =============================================================================
# Fail-closed zero-coefficient policy
# =============================================================================
# ZERO_COEFFICIENT_COMPONENTS declares, per model and per dimension (i_COD/i_N/i_P),
# the ONLY components allowed to be absent from a coefficient source. Absence of a
# listed component maps to an explicit 0.0 in the dense maps built by
# get_coefficients(); absence of anything else raises. The sets are DERIVED from the
# component catalog's explicit per-component basis metadata (single-sourced against
# QSDsan measured_as; see core/components/catalog.py) plus the narrow, documented
# engine-basis exceptions below:
#
#   basis "mass"  (mineral precipitates, HFO sorbents carried as Fe, ions, trace
#       metals, dissolved O2, H2O) -> legal 0.0 for i_COD, i_N and i_P: on the
#       engine's fallback basis, mass-basis species carry no COD/TKN/TP. When
#       QSDsan itself states a value (e.g. S_O2 i_COD=-1 electron credit, X_MeP
#       i_P, X_struv i_N/i_P), the dynamic use_component_props=True path extracts
#       and uses it — the zero sets only ever cover ABSENCE, never override a value.
#   basis "C"     (S_IC, S_ALK) -> inorganic carbon: no COD, no TKN, no TP.
#   basis "N"     -> legal 0.0 for i_COD and i_P (pure nitrogen species); for i_N
#       itself only via _NON_TKN_NITROGEN_IDS (the engine's fallback N balance is
#       a TKN balance).
#   basis "P"     -> legal 0.0 for i_COD and i_N.
#   basis "S"     -> legal 0.0 for i_N and i_P; for i_COD only via
#       _SULFUR_COD_EXCLUDED_IDS (the fallback COD basis is carbonaceous COD).
#   basis "COD"   -> NEVER legal to omit, except the documented per-model
#       fallback-basis exclusions below (_FALLBACK_COD_EXCLUDED and the
#       genuinely-N/P-free organics lists).

# TKN excludes oxidized nitrogen and the dissolved N2/N2O/NO/NH2OH gas/intermediate
# species. QSDsan stamps i_N=1 on all of them (they are measured as N), so the
# dynamic path counts them in its (total-N) balance; the fallback TKN basis books
# an explicit 0.0 when the fallback map omits them.
_NON_TKN_NITROGEN_IDS = frozenset({"S_N2", "S_NO3", "S_NO2", "S_NO", "S_N2O", "S_NH2OH"})

# Sulfide and elemental sulfur exert oxygen demand in QSDsan (S_IS i_COD~2.0,
# S_S0~1.5), but the engine's long-standing fallback COD basis is carbonaceous
# COD, so their absence from the fallback map is an explicit 0.0 (the dynamic
# path counts them). Sulfate is fully oxidized: genuinely zero COD.
_SULFUR_COD_EXCLUDED_IDS = frozenset({"S_SO4", "S_IS", "S_S0"})

# Dissolved VFAs beyond acetate and the dissolved gases are excluded from the
# mADM1/ADM1 fallback COD map. This preserves the engine's long-standing fallback
# basis (used by reactor seeding and SRT control; see get_coefficients); the
# dynamic path counts them at their QSDsan values (i_COD=1 on their COD basis).
_FALLBACK_COD_EXCLUDED: Dict[str, frozenset] = {
    "mADM1": frozenset({"S_va", "S_bu", "S_pro", "S_h2", "S_ch4"}),
}

# COD-basis organics that genuinely carry no nitrogen (QSDsan i_N == 0): sugars,
# LCFA, VFAs, dissolved gases, carbohydrates, lipids, PHA, and (for ASM1) the
# S_S/S_I substrates which ASM1 defines N-free.
_COD_BASIS_N_FREE: Dict[str, frozenset] = {
    "mADM1": frozenset({
        "S_su", "S_fa", "S_va", "S_bu", "S_pro", "S_ac", "S_h2", "S_ch4",
        "X_ch", "X_li", "X_PHA",
    }),
    "ASM1": frozenset({"S_S", "S_I"}),
}

# COD-basis organics that genuinely carry no phosphorus on the fallback basis
# (QSDsan i_P == 0 for all of these): sugars, amino acids, LCFA, VFAs,
# dissolved gases, carbohydrates, proteins, PHA.
_COD_BASIS_P_FREE: Dict[str, frozenset] = {
    "mADM1": frozenset({
        "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac", "S_h2", "S_ch4",
        "X_ch", "X_pr", "X_PHA",
    }),
}


def _build_zero_coefficient_components() -> Dict[str, Dict[str, frozenset]]:
    policy: Dict[str, Dict[str, frozenset]] = {}
    for model_value in MODEL_COMPONENTS:
        specs = dict(COMPONENT_CATALOG[model_value])
        # Handoff-only extras (e.g. mASM2d S_SO4) are valid StreamState components,
        # so they must resolve in the dense maps too.
        specs.update(STREAM_STATE_EXTRA_COMPONENTS.get(model_value, {}))
        zero_cod: set = set()
        zero_n: set = set()
        zero_p: set = set()
        for component_id, spec in specs.items():
            basis = spec.basis
            if basis in ("mass", "C"):
                zero_cod.add(component_id)
                zero_n.add(component_id)
                zero_p.add(component_id)
            elif basis == "N":
                zero_cod.add(component_id)
                zero_p.add(component_id)
                if component_id in _NON_TKN_NITROGEN_IDS:
                    zero_n.add(component_id)
            elif basis == "P":
                zero_cod.add(component_id)
                zero_n.add(component_id)
            elif basis == "S":
                zero_n.add(component_id)
                zero_p.add(component_id)
                if component_id in _SULFUR_COD_EXCLUDED_IDS:
                    zero_cod.add(component_id)
            elif basis == "COD":
                if component_id in _FALLBACK_COD_EXCLUDED.get(model_value, frozenset()):
                    zero_cod.add(component_id)
                if component_id in _COD_BASIS_N_FREE.get(model_value, frozenset()):
                    zero_n.add(component_id)
                if component_id in _COD_BASIS_P_FREE.get(model_value, frozenset()):
                    zero_p.add(component_id)
        if model_value == "ASM1":
            # The ASM1 fallback deliberately carries no phosphorus vector
            # ("ASM1 does not model phosphorus"); every component is legally 0.0
            # for i_P on the fallback basis. The dynamic path extracts QSDsan's
            # nonzero ASM1 i_P values instead.
            zero_p.update(specs)
        policy[model_value] = {
            "i_COD": frozenset(zero_cod),
            "i_N": frozenset(zero_n),
            "i_P": frozenset(zero_p),
        }
    # The engine treats plain 'ADM1' on the mADM1 component space (see
    # extract_component_coefficients / get_coefficients).
    policy["ADM1"] = policy["mADM1"]
    return policy


ZERO_COEFFICIENT_COMPONENTS: Dict[str, Dict[str, frozenset]] = _build_zero_coefficient_components()


def _dense_domain(model_type: str) -> Optional[Tuple[str, ...]]:
    """
    Catalog component domain the model's coefficient maps must cover.

    Returns None for upstream-only component sets (ADM1p) that have no catalog
    entry: there the extracted QSDsan component list is itself the authority and
    every extracted component must carry numeric coefficients.
    """
    if model_type in ('ADM1p', 'ADM1_p_extension'):
        return None
    canonical = 'mADM1' if model_type == 'ADM1' else model_type
    component_ids = MODEL_COMPONENTS.get(canonical)
    if component_ids is None:
        raise UnknownModelCoefficientsError(model_type)
    extras = tuple(STREAM_STATE_EXTRA_COMPONENTS.get(canonical, {}))
    return component_ids + extras


def _densify_coefficient_maps(
    model_type: str,
    maps: Dict[str, Dict[str, float]],
) -> Dict[str, Dict[str, float]]:
    """
    Make coefficient maps dense over the catalog component domain.

    Every catalog component of the model ends up present in every dimension:
    either with its source coefficient or with an explicit 0.0 sanctioned by
    ZERO_COEFFICIENT_COMPONENTS. Any remaining gap raises (fail-closed
    completeness post-condition) listing the missing component IDs.
    """
    domain = _dense_domain(model_type)
    if domain is None:
        return maps
    zero_sets = ZERO_COEFFICIENT_COMPONENTS['mADM1' if model_type == 'ADM1' else model_type]
    dense: Dict[str, Dict[str, float]] = {}
    for dimension in _COEFFICIENT_DIMENSIONS:
        dimension_map = dict(maps[dimension])
        missing = []
        for component_id in domain:
            if component_id in dimension_map:
                continue
            if component_id in zero_sets[dimension]:
                dimension_map[component_id] = 0.0
            else:
                missing.append(component_id)
        if missing:
            raise UnresolvedComponentCoefficientsError(
                model_type,
                missing,
                context=(
                    f"Coefficient map {dimension} is missing catalog component(s)"
                ),
            )
        dense[dimension] = dimension_map
    return dense


def extract_component_coefficients(
    model_type: str,
    use_cache: bool = True,
) -> Dict[str, Dict[str, float]]:
    """
    Extract i_COD, i_N, i_P coefficients from component objects.

    Dynamically reads the stoichiometric coefficients from QSDsan component
    definitions, with caching for performance.

    Parameters
    ----------
    model_type : str
        Model type ('ASM2d', 'mADM1', 'ADM1', 'ASM1')
    use_cache : bool
        Whether to use cached coefficients (default True)

    Returns
    -------
    dict
        Nested dict with keys 'i_COD', 'i_N', 'i_P', each containing
        {component_id: coefficient_value}. Maps are dense over the catalog
        component list of the model (plus handoff-only extras): every
        component appears, either with its extracted coefficient or with an
        explicit 0.0 sanctioned by ZERO_COEFFICIENT_COMPONENTS.

    Raises
    ------
    UnknownModelCoefficientsError
        If model_type is not a coefficient-bearing model of this engine.
    InvalidComponentCoefficientError
        If a component carries a NaN/non-numeric coefficient, or is missing a
        coefficient without a ZERO_COEFFICIENT_COMPONENTS sanction.
    UnresolvedComponentCoefficientsError
        If the dense maps would not cover the catalog component list.

    Examples
    --------
    >>> coeffs = extract_component_coefficients('ASM2d')
    >>> print(coeffs['i_COD']['X_H'])  # 1.42 for heterotrophs
    """
    cache_key = f"{model_type}"

    if use_cache and cache_key in _coefficient_cache:
        return _coefficient_cache[cache_key]

    # Get component definitions - use model-specific component sets
    if model_type in ('mADM1', 'ADM1'):
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps
        cmps = create_madm1_cmps(set_thermo=False)
    elif model_type in ('ADM1p', 'ADM1_p_extension'):
        from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
        from qsdsan_engine_mcp.core.qsdsan_compat import ensure_qsdsan_processes_load_from_file

        apply_runtime_compat()
        ensure_qsdsan_processes_load_from_file()
        import qsdsan.process_models as pc

        cmps = pc.create_adm1p_cmps(set_thermo=False)
    elif model_type == 'ASM1':
        # ASM1 has 13 components, different from ASM2d's 19
        from qsdsan import process_models as pc
        cmps = pc.create_asm1_cmps(set_thermo=False)
    elif model_type == 'ASM2d':
        from qsdsan import process_models as pc
        cmps = pc.create_asm2d_cmps(set_thermo=False)
    elif model_type == 'mASM2d':
        from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
        from qsdsan_engine_mcp.core.qsdsan_compat import ensure_qsdsan_processes_load_from_file

        apply_runtime_compat()
        ensure_qsdsan_processes_load_from_file()
        import qsdsan.process_models as pc

        cmps = pc.create_masm2d_cmps(set_thermo=False)
    elif model_type == 'mASM2d2N':
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_2n_components
        cmps = create_masm2d_2n_components(set_thermo=False)
    elif model_type == 'mASM2d2N_N2O':
        from qsdsan_engine_mcp.models.masm2d_n2o import create_masm2d_2n_n2o_components
        cmps = create_masm2d_2n_n2o_components(set_thermo=False)
    else:
        raise UnknownModelCoefficientsError(model_type)

    zero_sets = ZERO_COEFFICIENT_COMPONENTS.get(
        'mADM1' if model_type == 'ADM1' else model_type,
        {dimension: frozenset() for dimension in _COEFFICIENT_DIMENSIONS},
    )

    # Extract coefficients explicitly from component objects. A present but
    # NaN/non-numeric coefficient names the offending component; a genuinely
    # absent coefficient is only legal for ZERO_COEFFICIENT_COMPONENTS members.
    maps: Dict[str, Dict[str, float]] = {
        dimension: {} for dimension in _COEFFICIENT_DIMENSIONS
    }
    for cmp in cmps:
        cmp_id = cmp.ID
        for dimension in _COEFFICIENT_DIMENSIONS:
            raw = getattr(cmp, dimension, None)
            if raw is None:
                if cmp_id in zero_sets[dimension]:
                    maps[dimension][cmp_id] = 0.0
                    continue
                raise InvalidComponentCoefficientError(
                    model_type, cmp_id, dimension, raw, "no"
                )
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise InvalidComponentCoefficientError(
                    model_type, cmp_id, dimension, raw, "a non-numeric"
                ) from exc
            if np.isnan(value):
                raise InvalidComponentCoefficientError(
                    model_type, cmp_id, dimension, raw, "a NaN"
                )
            maps[dimension][cmp_id] = value

    result = _densify_coefficient_maps(model_type, maps)

    if use_cache:
        _coefficient_cache[cache_key] = result

    logger.debug(
        f"Extracted coefficients for {model_type}: "
        f"{len(result['i_COD'])} i_COD, {len(result['i_N'])} i_N, "
        f"{len(result['i_P'])} i_P"
    )

    return result


def get_coefficients(
    model_type: str,
    use_component_props: bool = False,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """
    Get stoichiometric coefficients for a model type.

    Can use either dynamic extraction from component properties or
    fallback to hard-coded default values.

    Parameters
    ----------
    model_type : str
        Model type ('ASM2d', 'mADM1', etc.)
    use_component_props : bool
        If True, extract coefficients dynamically from component objects.
        If False (default), use hard-coded fallback values for reliability.

    Returns
    -------
    tuple of (i_cod, i_n, i_p) dicts
        Maps are dense over the catalog component list of the model (plus
        handoff-only extras): every component appears, either with its
        source coefficient or with an explicit 0.0 sanctioned by
        ZERO_COEFFICIENT_COMPONENTS.

    Raises
    ------
    UnknownModelCoefficientsError
        If model_type is not a coefficient-bearing model of this engine.

    Notes
    -----
    The default hard-coded values are from QSDsan literature and provide
    reliable fallback when component properties are unavailable or incomplete.
    Use `use_component_props=True` for maximum accuracy with custom components.
    """
    if use_component_props:
        coeffs = extract_component_coefficients(model_type)
        return coeffs['i_COD'], coeffs['i_N'], coeffs['i_P']

    if model_type in ('ASM2d', 'mASM2d', 'mASM2d2N', 'mASM2d2N_N2O'):
        if model_type in ('mASM2d', 'mASM2d2N', 'mASM2d2N_N2O'):
            from qsdsan_engine_mcp.models.masm2d import DEFAULT_MASM2D_KWARGS as asm_kwargs
        else:
            from qsdsan_engine_mcp.models.asm2d import DEFAULT_ASM2D_KWARGS as asm_kwargs

        # COD equivalents stay on the engine's long-standing fallback basis so
        # validate_state matches the reactor-seeding and SRT-control logic.
        # X_AOB/X_NOB (two-step nitrifiers) share the X_AUT autotroph basis.
        i_cod = {
            'X_H': 1.42, 'X_PAO': 1.42, 'X_AUT': 1.42, 'X_AOB': 1.42, 'X_NOB': 1.42,
            'X_S': 1.0, 'X_I': 1.5,
            'S_F': 1.0, 'S_A': 1.0, 'S_I': 1.0,
            'X_PHA': 1.67, 'X_PP': 0.0,
        }
        i_n = {
            'S_I': asm_kwargs['iN_SI'],
            'S_F': asm_kwargs['iN_SF'],
            'S_A': 0.0,
            'X_I': asm_kwargs['iN_XI'],
            'X_S': asm_kwargs['iN_XS'],
            'X_H': asm_kwargs['iN_BM'],
            'X_PAO': asm_kwargs['iN_BM'],
            'X_AUT': asm_kwargs['iN_BM'],
            'X_AOB': asm_kwargs['iN_BM'],
            'X_NOB': asm_kwargs['iN_BM'],
            'X_PHA': 0.0,
            'X_PP': 0.0,
            'S_NH4': 1.0,
        }
        i_p = {
            'S_I': asm_kwargs['iP_SI'],
            'S_F': asm_kwargs['iP_SF'],
            'S_A': 0.0,
            'X_I': asm_kwargs['iP_XI'],
            'X_S': asm_kwargs['iP_XS'],
            'X_H': asm_kwargs['iP_BM'],
            'X_PAO': asm_kwargs['iP_BM'],
            'X_AUT': asm_kwargs['iP_BM'],
            'X_AOB': asm_kwargs['iP_BM'],
            'X_NOB': asm_kwargs['iP_BM'],
            'X_PHA': 0.0,
            'X_PP': 0.31,
            'S_PO4': 1.0,
        }
    # Fallback to hard-coded defaults - model-specific coefficients
    elif model_type == 'ASM1':
        # ASM1 has 13 components, different from ASM2d
        i_cod = {'X_BH': 1.42, 'X_BA': 1.42, 'X_S': 1.0, 'X_I': 1.5, 'X_P': 1.5,
                 'S_S': 1.0, 'S_I': 1.0}
        i_n = {'X_BH': 0.086, 'X_BA': 0.086, 'X_S': 0.04, 'X_I': 0.03, 'X_P': 0.03,
               'S_ND': 1.0, 'X_ND': 1.0, 'S_NH': 1.0}
        i_p = {}  # ASM1 does not model phosphorus
    elif model_type in ('mADM1', 'ADM1'):
        i_cod = {'X_su': 1.39, 'X_aa': 1.39, 'X_fa': 1.39, 'X_c4': 1.39,
                 'X_pro': 1.39, 'X_ac': 1.39, 'X_h2': 1.39, 'X_PAO': 1.39,
                 'X_pr': 1.35, 'X_li': 2.81, 'X_ch': 1.0,
                 'S_su': 1.0, 'S_aa': 1.35, 'S_fa': 2.5, 'S_ac': 1.07,
                 'S_I': 1.54, 'X_I': 1.54, 'X_PHA': 1.67,
                 'X_hSRB': 1.39, 'X_aSRB': 1.39, 'X_pSRB': 1.39, 'X_c4SRB': 1.39}
        i_n = {'X_su': 0.09, 'X_aa': 0.09, 'X_fa': 0.09, 'X_c4': 0.09,
               'X_pro': 0.09, 'X_ac': 0.09, 'X_h2': 0.09, 'X_PAO': 0.09,
               'X_pr': 0.11, 'S_aa': 0.11, 'S_I': 0.06, 'X_I': 0.06, 'S_IN': 1.0,
               'X_hSRB': 0.09, 'X_aSRB': 0.09, 'X_pSRB': 0.09, 'X_c4SRB': 0.09}
        i_p = {'X_su': 0.02, 'X_aa': 0.02, 'X_fa': 0.02, 'X_c4': 0.02,
               'X_pro': 0.02, 'X_ac': 0.02, 'X_h2': 0.02, 'X_PAO': 0.02,
               'X_li': 0.01, 'S_I': 0.01, 'X_I': 0.01, 'S_IP': 1.0, 'X_PP': 0.31,
               'X_hSRB': 0.02, 'X_aSRB': 0.02, 'X_pSRB': 0.02, 'X_c4SRB': 0.02}
    elif model_type in ('ADM1p', 'ADM1_p_extension'):
        coeffs = extract_component_coefficients('ADM1p', use_cache=True)
        return coeffs['i_COD'], coeffs['i_N'], coeffs['i_P']
    else:
        raise UnknownModelCoefficientsError(model_type)

    dense = _densify_coefficient_maps(
        model_type, {'i_COD': i_cod, 'i_N': i_n, 'i_P': i_p}
    )
    return dense['i_COD'], dense['i_N'], dense['i_P']


def _mg_l_scale(model_type: ModelType | str) -> float:
    model_value = model_type_value(model_type)
    if model_value in (ModelType.MADM1.value, ModelType.ADM1.value, 'ADM1p', 'ADM1_p_extension'):
        return 1000.0
    return 1.0


def _units_scale_to_mg_l(units: str) -> float:
    if units == "mg/L":
        return 1.0
    if units == "kg/m3":
        return 1000.0
    raise ValueError(f"Unsupported concentration units: {units}")


def _state_to_mg_l(state: StreamState, concentration: float) -> float:
    return float(concentration) * _units_scale_to_mg_l(state.concentration_units)


def _totals_for_concentrations(
    *,
    model_type: ModelType | str,
    concentrations: Dict[str, float],
    units: str,
    use_component_props: bool = True,
) -> Dict[str, float]:
    model_value = getattr(model_type, 'value', model_type)
    i_cod, i_n, i_p = get_coefficients(model_value, use_component_props)

    if units == 'mg/L':
        scale = 1.0
    elif units == 'kg/m3':
        scale = 1000.0
    else:
        raise ValueError(f"Unsupported concentration units: {units}")

    # Strict lookups: every contributing component must resolve in every
    # dimension. Coefficient maps are dense over the catalog, so an unresolved
    # ID means the component is outside the model's coefficient source.
    unresolved = {
        component_id
        for component_id, value in concentrations.items()
        if value > 0 and (
            component_id not in i_cod
            or component_id not in i_n
            or component_id not in i_p
        )
    }
    if unresolved:
        raise UnresolvedComponentCoefficientsError(
            model_value,
            unresolved,
            context="Concentration component(s) have no COD/N/P coefficient entry",
        )

    cod = 0.0
    tkn = 0.0
    tp = 0.0
    for component_id, value in concentrations.items():
        if value <= 0:
            continue
        conc_mg_l = float(value) * scale
        cod += conc_mg_l * i_cod[component_id]
        tkn += conc_mg_l * i_n[component_id]
        tp += conc_mg_l * i_p[component_id]

    return {"cod_mg_L": cod, "tkn_mg_L": tkn, "tp_mg_L": tp}


def _totals_for_state(
    state: StreamState,
    *,
    use_component_props: bool = True,
) -> Dict[str, float]:
    return _totals_for_concentrations(
        model_type=state.model_type,
        concentrations=state.concentrations,
        units=state.concentration_units,
        use_component_props=use_component_props,
    )


# Zero-input balance rule: below this influent total a relative error is not
# evaluable, so the dimension passes only when the effluent total stays within
# an absolute ceiling (mg/L). A clean stream (0 in -> ~0 out) still passes; an
# artifact that fabricates mass from a zero input cannot.
_ZERO_INPUT_FLOOR_MG_L = 1e-6
_ZERO_INPUT_ABS_TOL_MG_L = 1.0


def _dimension_balance(
    input_mg_l: float,
    output_mg_l: float,
    rtol: float,
) -> Dict[str, Any]:
    if input_mg_l > _ZERO_INPUT_FLOOR_MG_L:
        error = abs(output_mg_l - input_mg_l) / input_mg_l
        return {
            "input_mg_L": input_mg_l,
            "output_mg_L": output_mg_l,
            "error_pct": error * 100,
            "passed": error <= rtol,
            "evaluable": True,
        }
    # Relative error is undefined for a zero input; do not fabricate 0.0.
    return {
        "input_mg_L": input_mg_l,
        "output_mg_L": output_mg_l,
        "error_pct": None,
        "passed": abs(output_mg_l) <= _ZERO_INPUT_ABS_TOL_MG_L,
        "evaluable": False,
    }


def _validate_from_totals(
    input_totals: Dict[str, float],
    output_totals: Dict[str, float],
    rtol: float,
) -> Dict[str, Any]:
    cod_balance = _dimension_balance(
        input_totals["cod_mg_L"], output_totals["cod_mg_L"], rtol
    )
    nitrogen_balance = _dimension_balance(
        input_totals["tkn_mg_L"], output_totals["tkn_mg_L"], rtol
    )
    phosphorus_balance = _dimension_balance(
        input_totals["tp_mg_L"], output_totals["tp_mg_L"], rtol
    )

    return {
        "cod_balance": cod_balance,
        "nitrogen_balance": nitrogen_balance,
        "phosphorus_balance": phosphorus_balance,
        "all_passed": (
            cod_balance["passed"]
            and nitrogen_balance["passed"]
            and phosphorus_balance["passed"]
        ),
        "tolerance_pct": rtol * 100,
    }


def _summarize_balance_with_residuals(
    input_state: StreamState,
    output_state: StreamState,
    residual_packs: Iterable[Dict[str, Any]],
    rtol: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    input_totals = _totals_for_state(input_state, use_component_props=True)
    output_totals = _totals_for_state(output_state, use_component_props=True)

    for pack in residual_packs:
        residual_totals = _totals_for_concentrations(
            model_type=pack["model_type"],
            concentrations=pack["concentrations"],
            units=pack["units"],
            use_component_props=True,
        )
        output_totals["cod_mg_L"] += residual_totals["cod_mg_L"]
        output_totals["tkn_mg_L"] += residual_totals["tkn_mg_L"]
        output_totals["tp_mg_L"] += residual_totals["tp_mg_L"]

    validation = _validate_from_totals(input_totals, output_totals, rtol)

    def _fraction_error(dimension_key: str) -> Optional[float]:
        error_pct = validation[dimension_key]["error_pct"]
        return None if error_pct is None else error_pct / 100.0

    summary = {
        "cod_in_kg_m3": validation["cod_balance"]["input_mg_L"] / 1000.0,
        "cod_out_kg_m3": validation["cod_balance"]["output_mg_L"] / 1000.0,
        "cod_error": _fraction_error("cod_balance"),
        "cod_evaluable": validation["cod_balance"]["evaluable"],
        "tkn_in_mg_l": validation["nitrogen_balance"]["input_mg_L"],
        "tkn_out_mg_l": validation["nitrogen_balance"]["output_mg_L"],
        "tkn_error": _fraction_error("nitrogen_balance"),
        "tkn_evaluable": validation["nitrogen_balance"]["evaluable"],
        "tp_in_mg_l": validation["phosphorus_balance"]["input_mg_L"],
        "tp_out_mg_l": validation["phosphorus_balance"]["output_mg_L"],
        "tp_error": _fraction_error("phosphorus_balance"),
        "tp_evaluable": validation["phosphorus_balance"]["evaluable"],
        "balance_ok": validation["all_passed"],
    }
    return summary, validation


def _decision_grade_from_balance(balance: Dict[str, Any]) -> str:
    """Map balance error to an honest decision-grade label."""
    errors = [
        balance[key]
        for key in ("cod_error", "tkn_error", "tp_error")
        if balance[key] is not None
    ]
    if not errors:
        # No dimension was evaluable (all zero-input); relative closure cannot
        # support a design grade.
        return "screening"
    max_error = max(errors)
    if max_error <= 0.05:
        return "design"
    if max_error <= 0.15:
        return "screening"
    return "experimental"


def _convert_with_junction(
    input_state: StreamState,
    target_model: ModelType | str,
    **kwargs,
) -> Tuple[StreamState, Dict[str, Any]]:
    """Run conversion through upstream ADM1p <-> mASM2d A1 junction units."""
    from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat

    apply_runtime_compat()
    import qsdsan as qs
    from qsdsan import WasteStream
    from qsdsan import process_models as pc

    from qsdsan_engine_mcp.core.qsdsan_compat import ensure_qsdsan_processes_load_from_file
    from qsdsan_engine_mcp.core.a1_junction_units import ADM1ptomASM2d_custom, get_canonical_adm1p_cmps, get_canonical_adm1p_model, get_canonical_masm2d_cmps, get_canonical_masm2d_model, mASM2dtoADM1p_custom

    ensure_qsdsan_processes_load_from_file()

    target_model = ModelType(model_type_value(target_model))
    source_model = model_type_value(input_state.model_type)
    rtol = float(kwargs.get('rtol', 1e-2))
    try:
        original_thermo = qs.get_thermo()
    except RuntimeError:
        original_thermo = None

    masm2d_cmps = get_canonical_masm2d_cmps()
    adm1p_cmps = get_canonical_adm1p_cmps()

    def _make_stream(
        cmps,
        concentrations_mg_l: Dict[str, float],
        *,
        temperature_K: float,
        flow_m3_d: float,
    ) -> WasteStream:
        qs.set_thermo(cmps)
        stream = WasteStream(T=temperature_K)
        stream.set_flow_by_concentration(
            flow_tot=flow_m3_d,
            concentrations={k: v for k, v in concentrations_mg_l.items() if k != 'H2O'},
            units=('m3/d', 'mg/L'),
        )
        return stream

    try:
        if source_model in (ModelType.ASM2D.value, ModelType.MASM2D.value) and target_model == ModelType.MADM1:
            unit_id = f'J_convert_{next(_junction_counter)}'
            projection = project_asm_family_to_masm2d_core(input_state)
            carried_residuals = []
            if projection["carried_residuals"]["component_count"] > 0:
                carried_residuals.append(projection["carried_residuals"])

            upstream = _make_stream(
                masm2d_cmps,
                projection["core_concentrations_mg_L"],
                temperature_K=input_state.temperature_K,
                flow_m3_d=input_state.flow_m3_d,
            )
            downstream = _make_stream(
                adm1p_cmps,
                {component_id: 0.0 for component_id in adm1p_cmps.IDs},
                temperature_K=input_state.temperature_K,
                flow_m3_d=input_state.flow_m3_d,
            )

            masm2d_model = get_canonical_masm2d_model()
            adm1p_model = get_canonical_adm1p_model()
            qs.set_thermo(masm2d_cmps)

            unit = mASM2dtoADM1p_custom(
                ID=unit_id,
                upstream=upstream,
                downstream=downstream,
                thermo=downstream.thermo,
                asm2d_model=masm2d_model,
                adm1_model=adm1p_model,
                xs_to_li=kwargs.get('xs_to_li', 0.6),
                rtol=rtol,
                atol=float(kwargs.get('atol', 1e-3)),
            )
            unit._run()

            output_state, target_sidecars = reconstruct_madm1_from_adm1p_core(
                dict(zip(downstream.components.IDs, downstream.conc.value)),
                flow_m3_d=input_state.flow_m3_d,
                temperature_K=downstream.T,
            )
            carried_residuals.extend(target_sidecars)

        elif source_model == ModelType.MADM1.value and target_model in (ModelType.ASM2D, ModelType.MASM2D):
            unit_id = f'J_convert_{next(_junction_counter)}'
            projection = project_madm1_to_adm1p_core(input_state)
            carried_residuals = []
            if projection["carried_residuals"]["component_count"] > 0:
                carried_residuals.append(projection["carried_residuals"])

            upstream = _make_stream(
                adm1p_cmps,
                projection["core_concentrations_mg_L"],
                temperature_K=input_state.temperature_K,
                flow_m3_d=input_state.flow_m3_d,
            )
            downstream = _make_stream(
                masm2d_cmps,
                {component_id: 0.0 for component_id in masm2d_cmps.IDs},
                temperature_K=input_state.temperature_K,
                flow_m3_d=input_state.flow_m3_d,
            )

            masm2d_model = get_canonical_masm2d_model()
            adm1p_model = get_canonical_adm1p_model()
            qs.set_thermo(adm1p_cmps)

            unit = ADM1ptomASM2d_custom(
                ID=unit_id,
                upstream=upstream,
                downstream=downstream,
                thermo=downstream.thermo,
                adm1_model=adm1p_model,
                asm2d_model=masm2d_model,
                rtol=rtol,
                atol=float(kwargs.get('atol', 1e-3)),
            )
            unit._run()

            output_state, target_sidecars = reconstruct_asm_family_from_masm2d_core(
                dict(zip(downstream.components.IDs, downstream.conc.value)),
                target_model=target_model,
                flow_m3_d=input_state.flow_m3_d,
                temperature_K=downstream.T,
            )
            carried_residuals.extend(target_sidecars)

        else:
            raise ValueError(
                f"Conversion {source_model} -> {target_model.value} is not supported. "
                "Junction mode supports ASM2d/mASM2d -> mADM1 and "
                "mADM1 -> ASM2d/mASM2d."
            )

    finally:
        if original_thermo is not None:
            qs.set_thermo(original_thermo.chemicals)

    balance, carried_balance = _summarize_balance_with_residuals(
        input_state,
        output_state,
        carried_residuals,
        rtol=rtol,
    )
    decision_grade = _decision_grade_from_balance(balance)
    notes: list = []
    non_evaluable_dims = [
        label
        for label, key in (("COD", "cod"), ("TKN", "tkn"), ("TP", "tp"))
        if not balance[f"{key}_evaluable"]
    ]
    if non_evaluable_dims:
        # A non-evaluable balance (zero influent total) cannot support a
        # design grade: cap at screening and say why.
        if decision_grade == "design":
            decision_grade = "screening"
        notes.append(
            "Balance dimension(s) "
            + ", ".join(non_evaluable_dims)
            + " had zero influent totals, so relative closure is not evaluable "
            f"(checked against a {_ZERO_INPUT_ABS_TOL_MG_L:g} mg/L absolute "
            "ceiling instead); decision grade is capped at 'screening'."
        )
    metadata = {
        "success": True,
        "conversion": f"{source_model} -> {target_model.value}",
        "conversion_method": "junction",
        "decision_grade": decision_grade,
        "fallback_used": False,
        "balance": balance,
        "carried_residuals": [pack for pack in carried_residuals if pack["component_count"] > 0],
        "mass_balance": carried_balance,
        "parameters": {
            key: kwargs[key]
            for key in (
                'xs_to_li',
                'rtol',
                'atol',
            )
            if key in kwargs
        },
    }
    if decision_grade != "design":
        notes.append(
            "Junction conversion completed, but COD/TKN/TP closure is outside "
            "design-grade thresholds for this state pair."
        )
    if notes:
        metadata["notes"] = " ".join(notes)
    return output_state, metadata


def convert_asm2d_to_madm1(
    input_state: StreamState,
    xs_to_li: float = 0.6,
    bio_to_li: float = 0.4,
    frac_deg: float = 0.68,
    rtol: float = 1e-2,
    atol: float = 1e-3,
    use_component_props: bool = False,
) -> Tuple[StreamState, Dict[str, Any]]:
    """
    Internal alias for the canonical junction-backed converter.

    Plain ASM2d lacks the full mineral/ion basis required for exact A1
    coupling, so this path projects ASM2d onto upstream mASM2d first and may
    return non-design decision grades.
    """
    return _convert_with_junction(
        input_state,
        ModelType.MADM1,
        xs_to_li=xs_to_li,
        bio_to_li=bio_to_li,
        frac_deg=frac_deg,
        rtol=rtol,
        atol=atol,
        use_component_props=use_component_props,
    )


def convert_madm1_to_asm2d(
    input_state: StreamState,
    bio_to_xs: float = 0.7,
    rtol: float = 1e-2,
    atol: float = 1e-3,
    use_component_props: bool = False,
) -> Tuple[StreamState, Dict[str, Any]]:
    """
    Internal alias for the canonical junction-backed converter.

    mADM1 -> ASM2d reconstruction is an approximate projection from the exact
    mADM1 -> mASM2d A1 path back onto the stock ASM2d component set.
    """
    return _convert_with_junction(
        input_state,
        ModelType.ASM2D,
        bio_to_xs=bio_to_xs,
        rtol=rtol,
        atol=atol,
        use_component_props=use_component_props,
    )


def convert_state(
    input_state: StreamState,
    target_model: ModelType | str,
    validate: bool = False,
    **kwargs,
) -> Tuple[StreamState, Dict[str, Any]]:
    """
    Convert StreamState between model types.

    Dispatcher function that routes to the junction-backed converter.

    Parameters
    ----------
    input_state : StreamState
        Input state to convert
    target_model : ModelType
        Target model type (ASM2D, MASM2D, MADM1, ADM1)
    validate : bool
        If True, run mass and charge balance validation and add results
        to metadata. Logs warnings if balances exceed tolerances.
    **kwargs
        Conversion parameters passed to specific converter

    Returns
    -------
    output_state : StreamState
        Converted state
    metadata : dict
        Conversion metadata. If validate=True, includes 'mass_balance'
        and 'charge_balance' keys.

    Raises
    ------
    ValueError
        If conversion path is not supported
    """
    target_model = ModelType(model_type_value(target_model))
    source_model = model_type_value(input_state.model_type)

    # No conversion needed
    if source_model == target_model.value:
        return input_state, {
            "success": True,
            "conversion": "none",
            "message": "No conversion needed",
            "conversion_method": "identity",
            "decision_grade": "exact",
            "fallback_used": False,
        }

    output_state, metadata = _convert_with_junction(input_state, target_model, **kwargs)

    # Unsupported conversion
    if output_state is None:
        raise ValueError(
            f"Conversion {source_model} -> {target_model.value} is not supported. "
            f"Supported paths: ASM2d/mASM2d <-> mADM1"
        )

    # Run validation if requested
    if validate:
        state_mass_balance = validate_mass_balance(input_state, output_state)
        charge_balance = validate_charge_balance(output_state)

        metadata["state_mass_balance"] = state_mass_balance
        metadata["charge_balance"] = charge_balance

        # Log warnings for failed balances
        if not metadata["mass_balance"]["all_passed"]:
            logger.warning(
                f"Mass balance error: COD {metadata['mass_balance']['cod_balance']['error_pct']:.1f}%, "
                f"N {metadata['mass_balance']['nitrogen_balance']['error_pct']:.1f}%, "
                f"P {metadata['mass_balance']['phosphorus_balance']['error_pct']:.1f}%"
            )
        if not charge_balance["passed"]:
            logger.warning(
                f"Charge imbalance: {charge_balance['imbalance_meq_L']:.2f} meq/L"
            )

    return output_state, metadata


def create_junction_unit(
    direction: str,
    unit_id: str = 'J1',
    **kwargs,
) -> Any:
    """
    Factory function to create a junction unit for state conversion.

    Parameters
    ----------
    direction : str
        'asm2d_to_madm1'/'masm2d_to_madm1' or
        'madm1_to_asm2d'/'madm1_to_masm2d'
    unit_id : str
        Unit identifier (default 'J1')
    **kwargs
        Additional arguments passed to junction constructor

    Returns
    -------
    junction : SanUnit
        The junction unit for state conversion

    Raises
    ------
    ValueError
        If direction is not recognized
    """
    from qsdsan_engine_mcp.core.a1_junction_units import ADM1ptomASM2d_custom, mASM2dtoADM1p_custom
    import qsdsan as qs

    from qsdsan_engine_mcp.core.a1_junction_units import get_canonical_adm1p_cmps, get_canonical_masm2d_cmps

    if direction in {'asm2d_to_madm1', 'masm2d_to_madm1'}:
        qs.set_thermo(get_canonical_adm1p_cmps())
        kwargs.setdefault("thermo", qs.get_thermo())
        return mASM2dtoADM1p_custom(ID=unit_id, **kwargs)
    elif direction in {'madm1_to_asm2d', 'madm1_to_masm2d'}:
        qs.set_thermo(get_canonical_masm2d_cmps())
        kwargs.setdefault("thermo", qs.get_thermo())
        return ADM1ptomASM2d_custom(ID=unit_id, **kwargs)
    else:
        raise ValueError(
            "Unknown direction "
            f"'{direction}'. Use 'asm2d_to_madm1'/'masm2d_to_madm1' or "
            "'madm1_to_asm2d'/'madm1_to_masm2d'."
        )


# =============================================================================
# Mass and Charge Balance Validation (Phase 4)
# =============================================================================

# Component charge valences for electroneutrality calculations (meq/mg)
# Positive = cation, Negative = anion
_COMPONENT_CHARGES: Dict[str, float] = {
    # ASM2d ionic species
    'S_NH4': 1.0 / 14.0,      # NH4+ -> meq/mg-N
    'S_NO3': -1.0 / 14.0,     # NO3- -> meq/mg-N
    'S_PO4': -2.0 / 31.0,     # HPO4^2- at neutral pH -> meq/mg-P
    'S_ALK': 1.0 / 12.0,      # HCO3- approx -> meq/mg-C (alkalinity)
    # mADM1 ionic species
    'S_IN': 1.0 / 14.0,       # NH4+ (inorganic N)
    'S_IP': -2.0 / 31.0,      # HPO4^2- (inorganic P)
    'S_IC': 1.0 / 12.0,       # Bicarbonate
    'S_ac': -1.0 / 60.0,      # Acetate
    'S_pro': -1.0 / 74.0,     # Propionate
    'S_bu': -1.0 / 88.0,      # Butyrate
    'S_va': -1.0 / 102.0,     # Valerate
    'S_SO4': -2.0 / 32.0,     # Sulfate -> meq/mg-S
    'S_IS': -2.0 / 32.0,      # Sulfide (HS- at neutral pH)
    # ASM1 ionic species
    'S_NH': 1.0 / 14.0,       # NH4+ in ASM1
    'S_NO': -1.0 / 14.0,      # NO3- in ASM1
}


def validate_mass_balance(
    input_state: StreamState,
    output_state: StreamState,
    rtol: float = 0.01,
    use_component_props: bool = False,
) -> Dict[str, Any]:
    """
    Validate mass balance between input and output states.

    Checks conservation of COD, total nitrogen, and total phosphorus
    between two states, typically before and after conversion.

    Parameters
    ----------
    input_state : StreamState
        Input state (e.g., before conversion)
    output_state : StreamState
        Output state (e.g., after conversion)
    rtol : float
        Relative tolerance for balance check (default 1%)
    use_component_props : bool
        If True, extract coefficients dynamically from component objects

    Returns
    -------
    dict
        Validation result with:
        - cod_balance: COD balance details and pass/fail
        - nitrogen_balance: TKN balance details and pass/fail
        - phosphorus_balance: TP balance details and pass/fail
        - all_passed: True if all balances within tolerance

    Examples
    --------
    >>> input_state = StreamState.from_concentrations(model_type="ASM2d", concentrations={...})
    >>> output_state, _ = convert_state(input_state, ModelType.MADM1)
    >>> result = validate_mass_balance(input_state, output_state)
    >>> print(f"COD balanced: {result['cod_balance']['passed']}")

    Notes
    -----
    This is a coefficient-based closure check across the source and target
    StreamState objects. It is useful for regression testing and for labeling the
    practical fidelity of a conversion, but it is not itself a thermodynamic or
    stoichiometric solver.
    """
    # Get model types as strings
    input_model = getattr(input_state.model_type, 'value', str(input_state.model_type))
    output_model = getattr(output_state.model_type, 'value', str(output_state.model_type))

    # Get coefficients for both models
    in_i_cod, in_i_n, in_i_p = get_coefficients(input_model, use_component_props)
    out_i_cod, out_i_n, out_i_p = get_coefficients(output_model, use_component_props)

    # Calculate input totals
    cod_in = sum(
        _state_to_mg_l(input_state, conc) * in_i_cod.get(comp_id, 0.0)
        for comp_id, conc in input_state.concentrations.items()
        if conc > 0
    )
    tkn_in = sum(
        _state_to_mg_l(input_state, conc) * in_i_n.get(comp_id, 0.0)
        for comp_id, conc in input_state.concentrations.items()
        if conc > 0
    )
    tp_in = sum(
        _state_to_mg_l(input_state, conc) * in_i_p.get(comp_id, 0.0)
        for comp_id, conc in input_state.concentrations.items()
        if conc > 0
    )

    # Calculate output totals
    cod_out = sum(
        _state_to_mg_l(output_state, conc) * out_i_cod.get(comp_id, 0.0)
        for comp_id, conc in output_state.concentrations.items()
        if conc > 0
    )
    tkn_out = sum(
        _state_to_mg_l(output_state, conc) * out_i_n.get(comp_id, 0.0)
        for comp_id, conc in output_state.concentrations.items()
        if conc > 0
    )
    tp_out = sum(
        _state_to_mg_l(output_state, conc) * out_i_p.get(comp_id, 0.0)
        for comp_id, conc in output_state.concentrations.items()
        if conc > 0
    )

    # Calculate relative errors (avoid division by zero)
    cod_error = abs(cod_out - cod_in) / max(cod_in, 1e-6) if cod_in > 1e-6 else 0.0
    tkn_error = abs(tkn_out - tkn_in) / max(tkn_in, 1e-6) if tkn_in > 1e-6 else 0.0
    tp_error = abs(tp_out - tp_in) / max(tp_in, 1e-6) if tp_in > 1e-6 else 0.0

    cod_passed = cod_error <= rtol
    tkn_passed = tkn_error <= rtol
    tp_passed = tp_error <= rtol

    result = {
        "cod_balance": {
            "input_mg_L": cod_in,
            "output_mg_L": cod_out,
            "error_pct": cod_error * 100,
            "passed": cod_passed,
        },
        "nitrogen_balance": {
            "input_mg_L": tkn_in,
            "output_mg_L": tkn_out,
            "error_pct": tkn_error * 100,
            "passed": tkn_passed,
        },
        "phosphorus_balance": {
            "input_mg_L": tp_in,
            "output_mg_L": tp_out,
            "error_pct": tp_error * 100,
            "passed": tp_passed,
        },
        "all_passed": cod_passed and tkn_passed and tp_passed,
        "tolerance_pct": rtol * 100,
    }

    if not result["all_passed"]:
        logger.warning(
            f"Mass balance validation failed: "
            f"COD {cod_error*100:.1f}%, TKN {tkn_error*100:.1f}%, TP {tp_error*100:.1f}%"
        )

    return result


def validate_charge_balance(
    state: StreamState,
    atol: float = 0.1,
) -> Dict[str, Any]:
    """
    Validate electroneutrality of a state.

    Checks that the sum of cation charges equals the sum of anion charges.
    This is important for ensuring chemical consistency of converted states.

    Parameters
    ----------
    state : StreamState
        State to validate
    atol : float
        Absolute tolerance in meq/L (default 0.1 meq/L)

    Returns
    -------
    dict
        Validation result with:
        - cation_meq_L: Total positive charges
        - anion_meq_L: Total negative charges (absolute value)
        - imbalance_meq_L: Absolute difference
        - passed: True if imbalance <= atol
        - ionic_species: Dict of species contributing to charge

    Examples
    --------
    >>> state = StreamState.from_concentrations(model_type="ASM2d", concentrations={...})
    >>> result = validate_charge_balance(state)
    >>> if not result['passed']:
    ...     print(f"Charge imbalance: {result['imbalance_meq_L']:.2f} meq/L")

    Notes
    -----
    Charge balance calculations assume typical pH (6.5-8.0) speciation.
    Actual speciation depends on pH and may differ from these assumptions.
    """
    cation_charge = 0.0
    anion_charge = 0.0
    ionic_species = {}

    for comp_id, conc in state.concentrations.items():
        if conc <= 0:
            continue

        charge_factor = _COMPONENT_CHARGES.get(comp_id)
        if charge_factor is None:
            continue

        conc_mg_l = _state_to_mg_l(state, conc)
        charge_meq = conc_mg_l * charge_factor
        ionic_species[comp_id] = {
            "concentration_mg_L": conc_mg_l,
            "charge_meq_L": charge_meq,
        }

        if charge_factor > 0:
            cation_charge += charge_meq
        else:
            anion_charge += abs(charge_meq)

    imbalance = abs(cation_charge - anion_charge)
    passed = imbalance <= atol

    result = {
        "cation_meq_L": cation_charge,
        "anion_meq_L": anion_charge,
        "imbalance_meq_L": imbalance,
        "passed": passed,
        "tolerance_meq_L": atol,
        "ionic_species": ionic_species,
    }

    if not passed:
        logger.warning(
            f"Charge balance validation failed: "
            f"cations {cation_charge:.2f} meq/L, anions {anion_charge:.2f} meq/L, "
            f"imbalance {imbalance:.2f} meq/L"
        )

    return result


def validate_state_consistency(
    state: StreamState,
    use_component_props: bool = False,
) -> Dict[str, Any]:
    """
    Validate internal consistency of a single StreamState.

    Computes COD, TKN, and TP totals and checks for implausible values.
    This is a single-state sanity check, unlike validate_mass_balance()
    which compares input vs output states.

    Parameters
    ----------
    state : StreamState
        State to validate
    use_component_props : bool
        If True, extract coefficients dynamically from component objects

    Returns
    -------
    dict
        Validation result with:
        - cod_mg_L: Total COD
        - tkn_mg_L: Total Kjeldahl Nitrogen
        - tp_mg_L: Total Phosphorus
        - passed: True if all values are reasonable
        - warnings: List of warning messages

    Notes
    -----
    Reasonableness thresholds:
    - COD: 0 to 100,000 mg/L
    - TKN: 0 to 10,000 mg/L
    - TP: 0 to 5,000 mg/L

    These are generous bounds that should catch data entry errors
    without rejecting unusual but valid industrial wastewaters.
    """
    # Get model type as string
    model_type_str = getattr(state.model_type, 'value', str(state.model_type))

    # Get coefficients for this model
    i_cod, i_n, i_p = get_coefficients(model_type_str, use_component_props)

    # Calculate totals
    cod_total = sum(
        _state_to_mg_l(state, conc) * i_cod.get(comp_id, 0.0)
        for comp_id, conc in state.concentrations.items()
    )
    tkn_total = sum(
        _state_to_mg_l(state, conc) * i_n.get(comp_id, 0.0)
        for comp_id, conc in state.concentrations.items()
    )
    tp_total = sum(
        _state_to_mg_l(state, conc) * i_p.get(comp_id, 0.0)
        for comp_id, conc in state.concentrations.items()
    )

    warnings = []

    # Check COD bounds
    if cod_total < 0:
        warnings.append(f"Negative COD total: {cod_total:.1f} mg/L")
    elif cod_total > 100000:
        warnings.append(f"Implausibly high COD: {cod_total:.1f} mg/L (> 100,000)")

    # Check TKN bounds
    if tkn_total < 0:
        warnings.append(f"Negative TKN total: {tkn_total:.1f} mg/L")
    elif tkn_total > 10000:
        warnings.append(f"Implausibly high TKN: {tkn_total:.1f} mg/L (> 10,000)")

    # Check TP bounds
    if tp_total < 0:
        warnings.append(f"Negative TP total: {tp_total:.1f} mg/L")
    elif tp_total > 5000:
        warnings.append(f"Implausibly high TP: {tp_total:.1f} mg/L (> 5,000)")

    result = {
        "cod_mg_L": cod_total,
        "tkn_mg_L": tkn_total,
        "tp_mg_L": tp_total,
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }

    if not result["passed"]:
        logger.warning(
            f"State consistency validation failed: {'; '.join(warnings)}"
        )

    return result
