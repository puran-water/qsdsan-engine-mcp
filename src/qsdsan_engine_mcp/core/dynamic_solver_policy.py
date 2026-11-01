"""Governed numerical-integration tolerances for dynamic biological twins."""

from __future__ import annotations

import math
from typing import Any

MADM1_BDF_RTOL = 1e-6
MADM1_BDF_ATOL = 1e-9


def madm1_bdf_options(*, max_step: float | None = None) -> dict[str, Any]:
    """Return the mass-balance-qualified mADM1 BDF integration policy."""

    options: dict[str, Any] = {
        "method": "BDF",
        "rtol": MADM1_BDF_RTOL,
        "atol": MADM1_BDF_ATOL,
        "dense_output": True,
    }
    if max_step is not None:
        step = float(max_step)
        if not math.isfinite(step) or step <= 0:
            raise ValueError("max_step must be finite and positive")
        options["max_step"] = step
    return options


__all__ = ["MADM1_BDF_ATOL", "MADM1_BDF_RTOL", "madm1_bdf_options"]
