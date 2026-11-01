"""Input normalization helpers shared by aerobic template builders."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Tuple

_IC_TO_ALK = 50.0 / 12.0
_ALK_TO_IC = 12.0 / 50.0


def normalize_template_influent_concentrations(
    concentrations: Dict[str, float],
    component_ids: Iterable[str],
    *,
    logger: logging.Logger | None = None,
) -> Tuple[Dict[str, float], List[str]]:
    """Align influent concentrations to the target component package.

    This handles the common ASM2d <-> mASM2d alkalinity/inorganic-carbon field
    mismatch and removes components that are not present in the selected model.
    """
    normalized = dict(concentrations or {})
    component_id_set = set(component_ids)
    warnings: List[str] = []

    expects_alk = 'S_ALK' in component_id_set and 'S_IC' not in component_id_set
    expects_ic = 'S_IC' in component_id_set and 'S_ALK' not in component_id_set

    if expects_alk and 'S_IC' in normalized:
        s_ic = float(normalized.pop('S_IC'))
        if 'S_ALK' in normalized:
            warnings.append(
                "Dropped influent S_IC because the selected template uses S_ALK and S_ALK was already provided."
            )
        else:
            normalized['S_ALK'] = s_ic * _IC_TO_ALK
            warnings.append(
                "Converted influent S_IC to S_ALK using 50/12 (mg C/L -> mg CaCO3/L)."
            )

    if expects_ic and 'S_ALK' in normalized:
        s_alk = float(normalized.pop('S_ALK'))
        if 'S_IC' in normalized:
            warnings.append(
                "Dropped influent S_ALK because the selected template uses S_IC and S_IC was already provided."
            )
        else:
            normalized['S_IC'] = s_alk * _ALK_TO_IC
            warnings.append(
                "Converted influent S_ALK to S_IC using 12/50 (mg CaCO3/L -> mg C/L)."
            )

    extra_components = sorted(comp_id for comp_id in normalized if comp_id not in component_id_set)
    if extra_components:
        for comp_id in extra_components:
            normalized.pop(comp_id, None)
        warnings.append(
            "Stripped influent components not present in the selected model: "
            + ", ".join(extra_components)
        )

    if logger is not None:
        for warning in warnings:
            logger.warning(warning)

    return normalized, warnings
