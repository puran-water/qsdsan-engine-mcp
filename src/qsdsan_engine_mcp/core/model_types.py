"""Canonical model identifiers used by QSDsan public contracts."""

from __future__ import annotations

from enum import Enum
from typing import Literal


class ModelType(str, Enum):
    """Supported biological process models."""

    MADM1 = "mADM1"
    ADM1 = "ADM1"
    ASM2D = "ASM2d"
    MASM2D = "mASM2d"
    MASM2D_2N = "mASM2d2N"
    MASM2D_2N_N2O = "mASM2d2N_N2O"
    ASM1 = "ASM1"


ModelTypeLiteral = Literal[
    "mADM1", "ADM1", "ASM2d", "mASM2d", "mASM2d2N", "mASM2d2N_N2O", "ASM1"
]


def model_type_value(model_type: ModelType | str) -> str:
    """Return the canonical string value for model enum or string inputs."""

    return getattr(model_type, "value", str(model_type))


__all__ = ["ModelType", "ModelTypeLiteral", "model_type_value"]
