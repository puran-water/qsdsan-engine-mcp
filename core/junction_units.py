"""
Junction Units - Custom junction classes for ASM2d ↔ mADM1 state conversion.

This module provides custom junction units that work with our 63-component mADM1
(ModifiedADM1) model. The standard QSDsan junction units expect ADM1_p_extension,
but our model inherits from CompiledProcesses directly.

Key design:
1. Inherit from ASM2dtomADM1/mADM1toASM2d to get _compile_reactions() method
2. Override __init__ to allow deferred stream connection
3. Override model setters to accept CompiledProcesses (not just ADM1_p_extension)
4. Preserve ALL QSDsan conversion logic via inheritance

Usage:
    from core.junction_units import ASM2dtomADM1_custom, mADM1toASM2d_custom
    import qsdsan as qs
    import qsdsan.processes as pc
    from models.madm1 import create_madm1_cmps, ModifiedADM1

    # Set up thermo context
    cmps = create_madm1_cmps()
    qs.set_thermo(cmps)

    # Create junction (models can be set later)
    j1 = ASM2dtomADM1_custom('J1')
    j1.adm1_model = ModifiedADM1(components=cmps)
    j1.asm2d_model = pc.ASM2d()
"""

import logging
from qsdsan import CompiledProcesses, SanUnit
from qsdsan.sanunits._junction import ASM2dtomADM1, mADM1toASM2d
import qsdsan.processes as pc

logger = logging.getLogger(__name__)

__all__ = [
    'ASM2dtomADM1_custom',
    'mADM1toASM2d_custom',
]


class ASM2dtomADM1_custom(ASM2dtomADM1):
    """
    Custom ASM2d → mADM1 junction that accepts ModifiedADM1 process model.

    This class inherits from QSDsan's ASM2dtomADM1 to get:
    - _compile_reactions() - the actual conversion stoichiometry
    - _run() - stream conversion using compiled reactions
    - Mass balance preservation (COD, TKN, TP)

    The changes from parent class:
    1. Relaxed type check on adm1_model (accepts any CompiledProcesses)
    2. Allows deferred model/stream assignment
    """

    def __init__(
        self,
        ID: str = '',
        upstream=None,
        downstream=(),
        thermo=None,
        init_with: str = 'WasteStream',
        F_BM_default=None,
        isdynamic: bool = False,
        adm1_model=None,
        asm2d_model=None,
        T: float = 298.15,
        pH: float = 7.0,
        **kwargs,
    ):
        # Store conversion parameters
        self._T = T
        self._pH = pH

        # Initialize model storage (before SanUnit init which may access them)
        self._adm1_model = None
        self._asm2d_model = None

        # Initialize as basic SanUnit to avoid reaction compilation
        # QSDsan's Junction hierarchy tries to compile reactions during init
        # which requires connected streams. We defer this.
        SanUnit.__init__(
            self,
            ID=ID,
            ins=upstream,
            outs=downstream,
            thermo=thermo,
            init_with=init_with,
            F_BM_default=F_BM_default,
            isdynamic=isdynamic,
        )

        # Set default junction parameters (from mADMjunction)
        self.xs_to_li = kwargs.pop('xs_to_li', 0.7)
        self.bio_to_li = kwargs.pop('bio_to_li', 0.4)
        self.frac_deg = kwargs.pop('frac_deg', 0.68)
        self.rtol = kwargs.pop('rtol', 1e-2)
        self.atol = kwargs.pop('atol', 1e-6)

        # Set models using our relaxed setters
        if adm1_model is not None:
            self.adm1_model = adm1_model
        if asm2d_model is not None:
            self.asm2d_model = asm2d_model

        logger.debug(f"Created ASM2dtomADM1_custom junction: {ID}")

    @property
    def T(self):
        """Temperature in K."""
        return self._T

    @T.setter
    def T(self, value):
        self._T = value

    @property
    def pH(self):
        """pH value."""
        return self._pH

    @pH.setter
    def pH(self, value):
        self._pH = value

    @property
    def adm1_model(self):
        """The ADM1 or ModifiedADM1 process model."""
        return self._adm1_model

    @adm1_model.setter
    def adm1_model(self, model):
        """
        Set the ADM1 model, accepting ModifiedADM1.

        Relaxed type check: accepts any CompiledProcesses, not just ADM1_p_extension.
        """
        if model is not None and not isinstance(model, CompiledProcesses):
            raise ValueError(
                f"`adm1_model` must be a CompiledProcesses object (ADM1, ModifiedADM1, etc.), "
                f"the given object is {type(model).__name__}."
            )
        self._adm1_model = model

    @property
    def asm2d_model(self):
        """The ASM2d process model."""
        return self._asm2d_model

    @asm2d_model.setter
    def asm2d_model(self, model):
        """Set the ASM2d model, accepting any CompiledProcesses."""
        if model is not None and not isinstance(model, (pc.ASM2d, pc.mASM2d, CompiledProcesses)):
            raise ValueError(
                f"`asm2d_model` must be an ASM2d or CompiledProcesses object, "
                f"the given object is {type(model).__name__}."
            )
        self._asm2d_model = model


class mADM1toASM2d_custom(mADM1toASM2d):
    """
    Custom mADM1 → ASM2d junction that accepts ModifiedADM1 process model.

    This class inherits from QSDsan's mADM1toASM2d to get:
    - _compile_reactions() - the actual conversion stoichiometry
    - _run() - stream conversion using compiled reactions
    - Mass balance preservation (COD, TKN, TP)

    The changes from parent class:
    1. Relaxed type check on adm1_model (accepts any CompiledProcesses)
    2. Allows deferred model/stream assignment
    """

    def __init__(
        self,
        ID: str = '',
        upstream=None,
        downstream=(),
        thermo=None,
        init_with: str = 'WasteStream',
        F_BM_default=None,
        isdynamic: bool = False,
        adm1_model=None,
        asm2d_model=None,
        T: float = 298.15,
        pH: float = 7.0,
        **kwargs,
    ):
        # Store conversion parameters
        self._T = T
        self._pH = pH

        # Initialize model storage
        self._adm1_model = None
        self._asm2d_model = None

        # Initialize as basic SanUnit to avoid reaction compilation
        SanUnit.__init__(
            self,
            ID=ID,
            ins=upstream,
            outs=downstream,
            thermo=thermo,
            init_with=init_with,
            F_BM_default=F_BM_default,
            isdynamic=isdynamic,
        )

        # Set default junction parameters
        self.bio_to_xs = kwargs.pop('bio_to_xs', 0.7)
        self.rtol = kwargs.pop('rtol', 1e-2)
        self.atol = kwargs.pop('atol', 1e-6)

        # Set models using our relaxed setters
        if adm1_model is not None:
            self.adm1_model = adm1_model
        if asm2d_model is not None:
            self.asm2d_model = asm2d_model

        logger.debug(f"Created mADM1toASM2d_custom junction: {ID}")

    @property
    def T(self):
        """Temperature in K."""
        return self._T

    @T.setter
    def T(self, value):
        self._T = value

    @property
    def pH(self):
        """pH value."""
        return self._pH

    @pH.setter
    def pH(self, value):
        self._pH = value

    @property
    def adm1_model(self):
        """The ADM1 or ModifiedADM1 process model."""
        return self._adm1_model

    @adm1_model.setter
    def adm1_model(self, model):
        """
        Set the ADM1 model, accepting ModifiedADM1.

        Relaxed type check: accepts any CompiledProcesses, not just ADM1_p_extension.
        """
        if model is not None and not isinstance(model, CompiledProcesses):
            raise ValueError(
                f"`adm1_model` must be a CompiledProcesses object (ADM1, ModifiedADM1, etc.), "
                f"the given object is {type(model).__name__}."
            )
        self._adm1_model = model

    @property
    def asm2d_model(self):
        """The ASM2d process model."""
        return self._asm2d_model

    @asm2d_model.setter
    def asm2d_model(self, model):
        """Set the ASM2d model, accepting any CompiledProcesses."""
        if model is not None and not isinstance(model, (pc.ASM2d, pc.mASM2d, CompiledProcesses)):
            raise ValueError(
                f"`asm2d_model` must be an ASM2d or CompiledProcesses object, "
                f"the given object is {type(model).__name__}."
            )
        self._asm2d_model = model
