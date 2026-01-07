"""
State Converters - Junction-based state conversion between biological models.

This module provides state conversion between ASM2d and mADM1 models using
custom Junction units that work with our 63-component ModifiedADM1. Key use cases:
- WAS → digester feed (ASM2d → mADM1)
- Digester effluent → sidestream (mADM1 → ASM2d)

The conversions preserve mass balance for COD, TKN, and TP.

Reference: QSDsan sanunits/_junction.py (mADMjunction classes)
"""

import logging
from typing import Dict, Any, Tuple, Optional

import numpy as np
from core.plant_state import PlantState, ModelType
from core.model_registry import MADM1_COMPONENTS, ASM2D_COMPONENTS

logger = logging.getLogger(__name__)

__all__ = [
    'convert_asm2d_to_madm1',
    'convert_madm1_to_asm2d',
    'convert_state',
    'create_junction_unit',
]


def convert_asm2d_to_madm1(
    input_state: PlantState,
    xs_to_li: float = 0.6,
    bio_to_li: float = 0.4,
    frac_deg: float = 0.68,
    rtol: float = 1e-2,
    atol: float = 1e-3,
) -> Tuple[PlantState, Dict[str, Any]]:
    """
    Convert ASM2d state to mADM1 state.

    Use case: WAS from aerobic MBR → anaerobic digester feed

    This performs a stoichiometric mapping from ASM2d components to mADM1
    components while preserving mass balance (COD, TKN, TP).

    Parameters
    ----------
    input_state : PlantState
        Input state with ASM2d concentrations
    xs_to_li : float
        Split of slowly biodegradable substrate COD to lipid (default 0.6)
    bio_to_li : float
        Split of biomass COD to lipid (default 0.4)
    frac_deg : float
        Biodegradable fraction of biomass COD (default 0.68)
    rtol, atol : float
        Tolerances for COD/TKN/TP balance checking

    Returns
    -------
    output_state : PlantState
        Converted state with mADM1 concentrations
    metadata : dict
        Conversion metadata including mass balance checks

    Examples
    --------
    >>> from core.plant_state import PlantState, ModelType
    >>> from core.converters import convert_asm2d_to_madm1
    >>> asm_state = PlantState(
    ...     model_type=ModelType.ASM2D,
    ...     concentrations={'X_H': 5000, 'X_S': 2000, 'S_NH4': 30},
    ...     flow_m3_d=100,
    ... )
    >>> adm_state, meta = convert_asm2d_to_madm1(asm_state)
    >>> print(adm_state.model_type)
    ModelType.MADM1
    """
    from core.junction_components import get_asm2d_to_madm1_mapping

    # Validate input
    if input_state.model_type not in (ModelType.ASM2D, ModelType.MASM2D):
        raise ValueError(
            f"Input state must be ASM2d or mASM2d, got {input_state.model_type}"
        )

    asm_concs = input_state.concentrations
    mapping = get_asm2d_to_madm1_mapping()

    # Initialize output concentrations
    adm_concs: Dict[str, float] = {cid: 0.0 for cid in MADM1_COMPONENTS}

    # Track mass balance
    cod_in = 0.0
    cod_out = 0.0
    tkn_in = 0.0
    tkn_out = 0.0
    tp_in = 0.0
    tp_out = 0.0

    # Component property coefficients (typical values)
    # ASM2d
    asm_i_cod = {'X_H': 1.42, 'X_PAO': 1.42, 'X_AUT': 1.42, 'X_S': 1.0, 'X_I': 1.5,
                 'S_F': 1.0, 'S_A': 1.0, 'S_I': 1.0, 'X_PHA': 1.67, 'X_PP': 0.0}
    asm_i_n = {'X_H': 0.07, 'X_PAO': 0.07, 'X_AUT': 0.07, 'X_S': 0.04, 'X_I': 0.03,
               'S_F': 0.03, 'S_A': 0.0, 'S_I': 0.01, 'S_NH4': 1.0}
    asm_i_p = {'X_H': 0.02, 'X_PAO': 0.02, 'X_AUT': 0.02, 'X_S': 0.01, 'X_I': 0.01,
               'X_PP': 0.31, 'S_PO4': 1.0}

    # mADM1
    adm_i_cod = {'X_su': 1.39, 'X_aa': 1.39, 'X_fa': 1.39, 'X_c4': 1.39,
                 'X_pro': 1.39, 'X_ac': 1.39, 'X_h2': 1.39, 'X_PAO': 1.39,
                 'X_pr': 1.35, 'X_li': 2.81, 'X_ch': 1.0,
                 'S_su': 1.0, 'S_aa': 1.35, 'S_fa': 2.5, 'S_ac': 1.07,
                 'S_I': 1.54, 'X_I': 1.54, 'X_PHA': 1.67}
    adm_i_n = {'X_su': 0.09, 'X_aa': 0.09, 'X_fa': 0.09, 'X_c4': 0.09,
               'X_pro': 0.09, 'X_ac': 0.09, 'X_h2': 0.09, 'X_PAO': 0.09,
               'X_pr': 0.11, 'S_aa': 0.11, 'S_I': 0.06, 'X_I': 0.06, 'S_IN': 1.0}
    adm_i_p = {'X_su': 0.02, 'X_aa': 0.02, 'X_fa': 0.02, 'X_c4': 0.02,
               'X_pro': 0.02, 'X_ac': 0.02, 'X_h2': 0.02, 'X_PAO': 0.02,
               'X_li': 0.01, 'S_I': 0.01, 'X_I': 0.01, 'S_IP': 1.0, 'X_PP': 0.31}

    # Map components
    for asm_id, asm_val in asm_concs.items():
        if asm_val <= 0:
            continue

        # Track input mass
        cod_in += asm_val * asm_i_cod.get(asm_id, 0.0)
        tkn_in += asm_val * asm_i_n.get(asm_id, 0.0)
        tp_in += asm_val * asm_i_p.get(asm_id, 0.0)

        adm_target = mapping.get(asm_id)
        if adm_target is None:
            continue

        if isinstance(adm_target, str):
            # Direct mapping
            if adm_target in adm_concs:
                adm_concs[adm_target] += asm_val
        elif isinstance(adm_target, list):
            # Split mapping with weighting
            n_targets = len(adm_target)

            if asm_id == 'X_S':
                # Slowly biodegradable: split to proteins/lipids/carbs
                # Using xs_to_li parameter
                adm_concs['X_li'] += asm_val * xs_to_li
                adm_concs['X_pr'] += asm_val * (1 - xs_to_li) * 0.5
                adm_concs['X_ch'] += asm_val * (1 - xs_to_li) * 0.5
            elif asm_id == 'X_H':
                # Biomass: split based on degradability
                degradable = asm_val * frac_deg
                inert = asm_val * (1 - frac_deg)

                # Degradable to substrates
                adm_concs['X_li'] += degradable * bio_to_li
                adm_concs['X_pr'] += degradable * (1 - bio_to_li) * 0.5
                adm_concs['X_ch'] += degradable * (1 - bio_to_li) * 0.5

                # Inert fraction
                adm_concs['X_I'] += inert
            else:
                # Equal split
                for t in adm_target:
                    if t in adm_concs:
                        adm_concs[t] += asm_val / n_targets

    # Calculate output mass balance
    for adm_id, adm_val in adm_concs.items():
        if adm_val > 0:
            cod_out += adm_val * adm_i_cod.get(adm_id, 0.0)
            tkn_out += adm_val * adm_i_n.get(adm_id, 0.0)
            tp_out += adm_val * adm_i_p.get(adm_id, 0.0)

    # Check mass balance
    cod_error = abs(cod_out - cod_in) / max(cod_in, 1e-6)
    tkn_error = abs(tkn_out - tkn_in) / max(tkn_in, 1e-6)
    tp_error = abs(tp_out - tp_in) / max(tp_in, 1e-6)

    balance_ok = (cod_error <= rtol and tkn_error <= rtol and tp_error <= rtol)

    if not balance_ok:
        logger.warning(
            f"Mass balance deviation: COD {cod_error:.2%}, "
            f"TKN {tkn_error:.2%}, TP {tp_error:.2%}"
        )

    # Create output state
    output_state = PlantState(
        model_type=ModelType.MADM1,
        concentrations=adm_concs,
        flow_m3_d=input_state.flow_m3_d,
        temperature_K=input_state.temperature_K or 308.15,
    )

    metadata = {
        "success": True,
        "conversion": "ASM2d → mADM1",
        "balance": {
            "cod_in_kg_m3": cod_in / 1000,
            "cod_out_kg_m3": cod_out / 1000,
            "cod_error": cod_error,
            "tkn_in_mg_l": tkn_in,
            "tkn_out_mg_l": tkn_out,
            "tkn_error": tkn_error,
            "tp_in_mg_l": tp_in,
            "tp_out_mg_l": tp_out,
            "tp_error": tp_error,
            "balance_ok": balance_ok,
        },
        "parameters": {
            "xs_to_li": xs_to_li,
            "bio_to_li": bio_to_li,
            "frac_deg": frac_deg,
        },
    }

    logger.info(
        f"Converted ASM2d → mADM1: {len(asm_concs)} → {sum(1 for v in adm_concs.values() if v > 0)} components"
    )

    return output_state, metadata


def convert_madm1_to_asm2d(
    input_state: PlantState,
    bio_to_xs: float = 0.7,
    rtol: float = 1e-2,
    atol: float = 1e-3,
) -> Tuple[PlantState, Dict[str, Any]]:
    """
    Convert mADM1 state to ASM2d state.

    Use case: Digester effluent → sidestream treatment (returns to mainstream)

    This performs a stoichiometric mapping from mADM1 components to ASM2d
    components while preserving mass balance (COD, TKN, TP).

    Parameters
    ----------
    input_state : PlantState
        Input state with mADM1 concentrations
    bio_to_xs : float
        Split of total biomass COD to slowly biodegradable substrate (default 0.7)
    rtol, atol : float
        Tolerances for COD/TKN/TP balance checking

    Returns
    -------
    output_state : PlantState
        Converted state with ASM2d concentrations
    metadata : dict
        Conversion metadata including mass balance checks

    Examples
    --------
    >>> from core.plant_state import PlantState, ModelType
    >>> from core.converters import convert_madm1_to_asm2d
    >>> adm_state = PlantState(
    ...     model_type=ModelType.MADM1,
    ...     concentrations={'X_ac': 2000, 'S_IN': 500, 'S_IP': 50},
    ...     flow_m3_d=100,
    ... )
    >>> asm_state, meta = convert_madm1_to_asm2d(adm_state)
    >>> print(asm_state.model_type)
    ModelType.ASM2D
    """
    from core.junction_components import get_madm1_to_asm2d_mapping

    # Validate input
    if input_state.model_type not in (ModelType.MADM1, ModelType.ADM1):
        raise ValueError(
            f"Input state must be mADM1 or ADM1, got {input_state.model_type}"
        )

    adm_concs = input_state.concentrations
    mapping = get_madm1_to_asm2d_mapping()

    # Initialize output concentrations
    asm_concs: Dict[str, float] = {cid: 0.0 for cid in ASM2D_COMPONENTS}

    # Track mass balance
    cod_in = 0.0
    cod_out = 0.0
    tkn_in = 0.0
    tkn_out = 0.0
    tp_in = 0.0
    tp_out = 0.0

    # Component property coefficients (typical values)
    # mADM1 coefficients
    adm_i_cod = {'X_su': 1.39, 'X_aa': 1.39, 'X_fa': 1.39, 'X_c4': 1.39,
                 'X_pro': 1.39, 'X_ac': 1.39, 'X_h2': 1.39, 'X_PAO': 1.39,
                 'X_pr': 1.35, 'X_li': 2.81, 'X_ch': 1.0,
                 'S_su': 1.0, 'S_aa': 1.35, 'S_fa': 2.5, 'S_ac': 1.07,
                 'S_I': 1.54, 'X_I': 1.54, 'X_PHA': 1.67,
                 'X_hSRB': 1.39, 'X_aSRB': 1.39, 'X_pSRB': 1.39, 'X_c4SRB': 1.39}
    adm_i_n = {'X_su': 0.09, 'X_aa': 0.09, 'X_fa': 0.09, 'X_c4': 0.09,
               'X_pro': 0.09, 'X_ac': 0.09, 'X_h2': 0.09, 'X_PAO': 0.09,
               'X_pr': 0.11, 'S_aa': 0.11, 'S_I': 0.06, 'X_I': 0.06, 'S_IN': 1.0,
               'X_hSRB': 0.09, 'X_aSRB': 0.09, 'X_pSRB': 0.09, 'X_c4SRB': 0.09}
    adm_i_p = {'X_su': 0.02, 'X_aa': 0.02, 'X_fa': 0.02, 'X_c4': 0.02,
               'X_pro': 0.02, 'X_ac': 0.02, 'X_h2': 0.02, 'X_PAO': 0.02,
               'X_li': 0.01, 'S_I': 0.01, 'X_I': 0.01, 'S_IP': 1.0, 'X_PP': 0.31,
               'X_hSRB': 0.02, 'X_aSRB': 0.02, 'X_pSRB': 0.02, 'X_c4SRB': 0.02}

    # ASM2d coefficients
    asm_i_cod = {'X_H': 1.42, 'X_PAO': 1.42, 'X_AUT': 1.42, 'X_S': 1.0, 'X_I': 1.5,
                 'S_F': 1.0, 'S_A': 1.0, 'S_I': 1.0, 'X_PHA': 1.67, 'X_PP': 0.0}
    asm_i_n = {'X_H': 0.07, 'X_PAO': 0.07, 'X_AUT': 0.07, 'X_S': 0.04, 'X_I': 0.03,
               'S_F': 0.03, 'S_A': 0.0, 'S_I': 0.01, 'S_NH4': 1.0}
    asm_i_p = {'X_H': 0.02, 'X_PAO': 0.02, 'X_AUT': 0.02, 'X_S': 0.01, 'X_I': 0.01,
               'X_PP': 0.31, 'S_PO4': 1.0}

    # Map components with mass balance tracking
    for adm_id, adm_val in adm_concs.items():
        if adm_val <= 0:
            continue

        # Track input mass
        cod_in += adm_val * adm_i_cod.get(adm_id, 0.0)
        tkn_in += adm_val * adm_i_n.get(adm_id, 0.0)
        tp_in += adm_val * adm_i_p.get(adm_id, 0.0)

        asm_target = mapping.get(adm_id)
        if asm_target is None:
            continue

        if isinstance(asm_target, str) and asm_target in asm_concs:
            asm_concs[asm_target] += adm_val

    # Calculate output mass balance
    for asm_id, asm_val in asm_concs.items():
        if asm_val > 0:
            cod_out += asm_val * asm_i_cod.get(asm_id, 0.0)
            tkn_out += asm_val * asm_i_n.get(asm_id, 0.0)
            tp_out += asm_val * asm_i_p.get(asm_id, 0.0)

    # Check mass balance
    cod_error = abs(cod_out - cod_in) / max(cod_in, 1e-6) if cod_in > 1e-6 else 0.0
    tkn_error = abs(tkn_out - tkn_in) / max(tkn_in, 1e-6) if tkn_in > 1e-6 else 0.0
    tp_error = abs(tp_out - tp_in) / max(tp_in, 1e-6) if tp_in > 1e-6 else 0.0

    balance_ok = (cod_error <= rtol and tkn_error <= rtol and tp_error <= rtol)

    if not balance_ok:
        logger.warning(
            f"Mass balance deviation: COD {cod_error:.2%}, "
            f"TKN {tkn_error:.2%}, TP {tp_error:.2%}"
        )

    # Create output state
    output_state = PlantState(
        model_type=ModelType.ASM2D,
        concentrations=asm_concs,
        flow_m3_d=input_state.flow_m3_d,
        temperature_K=input_state.temperature_K or 293.15,
    )

    metadata = {
        "success": True,
        "conversion": "mADM1 → ASM2d",
        "balance": {
            "cod_in_kg_m3": cod_in / 1000,
            "cod_out_kg_m3": cod_out / 1000,
            "cod_error": cod_error,
            "tkn_in_mg_l": tkn_in,
            "tkn_out_mg_l": tkn_out,
            "tkn_error": tkn_error,
            "tp_in_mg_l": tp_in,
            "tp_out_mg_l": tp_out,
            "tp_error": tp_error,
            "balance_ok": balance_ok,
        },
        "parameters": {
            "bio_to_xs": bio_to_xs,
        },
    }

    logger.info(
        f"Converted mADM1 → ASM2d: {len(adm_concs)} → {sum(1 for v in asm_concs.values() if v > 0)} components"
    )

    return output_state, metadata


def convert_state(
    input_state: PlantState,
    target_model: ModelType,
    **kwargs,
) -> Tuple[PlantState, Dict[str, Any]]:
    """
    Convert PlantState between model types.

    Dispatcher function that routes to appropriate conversion function.

    Parameters
    ----------
    input_state : PlantState
        Input state to convert
    target_model : ModelType
        Target model type (ASM2D, MASM2D, MADM1, ADM1)
    **kwargs
        Conversion parameters passed to specific converter

    Returns
    -------
    output_state : PlantState
        Converted state
    metadata : dict
        Conversion metadata

    Raises
    ------
    ValueError
        If conversion path is not supported
    """
    source_model = input_state.model_type

    # No conversion needed
    if source_model == target_model:
        return input_state, {"success": True, "conversion": "none", "message": "No conversion needed"}

    # ASM2d/mASM2d → mADM1/ADM1
    if source_model in (ModelType.ASM2D, ModelType.MASM2D):
        if target_model in (ModelType.MADM1, ModelType.ADM1):
            return convert_asm2d_to_madm1(input_state, **kwargs)

    # mADM1/ADM1 → ASM2d/mASM2d
    if source_model in (ModelType.MADM1, ModelType.ADM1):
        if target_model in (ModelType.ASM2D, ModelType.MASM2D):
            return convert_madm1_to_asm2d(input_state, **kwargs)

    # Unsupported conversion
    raise ValueError(
        f"Conversion {source_model.value} → {target_model.value} is not supported. "
        f"Supported paths: ASM2d↔mADM1"
    )


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
        'asm2d_to_madm1' or 'madm1_to_asm2d'
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
    from core.junction_units import ASM2dtomADM1_custom, mADM1toASM2d_custom

    if direction == 'asm2d_to_madm1':
        return ASM2dtomADM1_custom(ID=unit_id, **kwargs)
    elif direction == 'madm1_to_asm2d':
        return mADM1toASM2d_custom(ID=unit_id, **kwargs)
    else:
        raise ValueError(
            f"Unknown direction '{direction}'. Use 'asm2d_to_madm1' or 'madm1_to_asm2d'."
        )
