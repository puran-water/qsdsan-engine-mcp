# QSDsan biological process models
"""
QSDsan Engine Models - Biological process model definitions.

Provides:
- mADM1 (Modified ADM1): catalog-driven anaerobic digestion model with P/S/Fe
- ASM2d: 19-component activated sludge model with EBPR (via QSDsan)
- Reactors: Custom reactor classes (AnaerobicCSTRmADM1)
"""

# Lazy imports to avoid QSDsan load time during module discovery
__all__ = [
    'create_madm1_cmps',
    'ModifiedADM1',
    'AnaerobicCSTRmADM1',
    'create_asm2d_components',
    'DEFAULT_ASM2D_KWARGS',
    'DEFAULT_DOMESTIC_WW',
    'ModifiedASM2d',
    'create_masm2d_2n_components',
    'ModifiedASM2d_N2O',
    'create_masm2d_2n_n2o_components',
]


def __getattr__(name):
    """Lazy import to avoid 18s QSDsan load on module import."""
    if name == 'create_madm1_cmps':
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps
        return create_madm1_cmps
    elif name == 'ModifiedADM1':
        from qsdsan_engine_mcp.models.madm1 import ModifiedADM1
        return ModifiedADM1
    elif name == 'AnaerobicCSTRmADM1':
        from qsdsan_engine_mcp.models.reactors import AnaerobicCSTRmADM1
        return AnaerobicCSTRmADM1
    elif name == 'create_asm2d_components':
        from qsdsan_engine_mcp.models.asm2d import create_asm2d_components
        return create_asm2d_components
    elif name == 'DEFAULT_ASM2D_KWARGS':
        from qsdsan_engine_mcp.models.asm2d import DEFAULT_ASM2D_KWARGS
        return DEFAULT_ASM2D_KWARGS
    elif name == 'DEFAULT_DOMESTIC_WW':
        from qsdsan_engine_mcp.models.asm2d import DEFAULT_DOMESTIC_WW
        return DEFAULT_DOMESTIC_WW
    elif name == 'ModifiedASM2d':
        from qsdsan_engine_mcp.models.masm2d import ModifiedASM2d
        return ModifiedASM2d
    elif name == 'create_masm2d_2n_components':
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_2n_components
        return create_masm2d_2n_components
    elif name == 'ModifiedASM2d_N2O':
        from qsdsan_engine_mcp.models.masm2d_n2o import ModifiedASM2d_N2O
        return ModifiedASM2d_N2O
    elif name == 'create_masm2d_2n_n2o_components':
        from qsdsan_engine_mcp.models.masm2d_n2o import create_masm2d_2n_n2o_components
        return create_masm2d_2n_n2o_components
    raise AttributeError(f"module 'models' has no attribute '{name}'")
