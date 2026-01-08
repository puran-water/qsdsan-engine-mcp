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
5. Optional auto_align_components flag for automatic component property alignment

Usage:
    from core.junction_units import ASM2dtomADM1_custom, mADM1toASM2d_custom
    import qsdsan as qs
    import qsdsan.processes as pc
    from models.madm1 import create_madm1_cmps, ModifiedADM1

    # Set up thermo context
    cmps = create_madm1_cmps()
    qs.set_thermo(cmps)

    # Create junction with auto-alignment (recommended for mADM1)
    j1 = ASM2dtomADM1_custom('J1', auto_align_components=True)
    j1.adm1_model = ModifiedADM1(components=cmps)
    j1.asm2d_model = pc.ASM2d()
"""

import logging
from typing import Optional, Tuple, Any
from qsdsan import CompiledProcesses, SanUnit
from qsdsan.sanunits._junction import ASM2dtomADM1, mADM1toASM2d
import qsdsan.processes as pc

logger = logging.getLogger(__name__)

__all__ = [
    'ASM2dtomADM1_custom',
    'mADM1toASM2d_custom',
]

# Module-level cache for aligned component sets
_aligned_components_cache: dict = {}


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
    3. Optional auto_align_components for property alignment (Phase 2B)

    Parameters
    ----------
    auto_align_components : bool, optional
        If True, automatically align component properties (i_COD, i_N, i_P,
        measured_as) between ASM2d and mADM1 component sets before reaction
        compilation. This resolves property mismatch errors with ModifiedADM1.
        Default is False for backward compatibility.
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
        auto_align_components: bool = False,
        **kwargs,
    ):
        # Store conversion parameters
        self._T = T
        self._pH = pH
        self._auto_align_components = auto_align_components
        self._aligned_cmps: Optional[Tuple[Any, Any]] = None

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

        logger.debug(f"Created ASM2dtomADM1_custom junction: {ID} (auto_align={auto_align_components})")

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

    @property
    def auto_align_components(self) -> bool:
        """Whether to auto-align component properties before compilation."""
        return self._auto_align_components

    @auto_align_components.setter
    def auto_align_components(self, value: bool):
        self._auto_align_components = value
        # Clear cached components if flag changes
        self._aligned_cmps = None

    def ensure_aligned_components(self) -> Tuple[Any, Any]:
        """
        Ensure component sets are aligned for junction operations.

        Returns aligned copies of ASM2d and mADM1 components with:
        - Formulas removed (unlocks i_COD, i_N, i_P modification)
        - Key properties aligned between equivalent components
        - Cached for reuse across multiple calls

        Returns
        -------
        asm_cmps : CompiledComponents
            ASM2d components aligned for junction
        adm_cmps : CompiledComponents
            mADM1 components aligned for junction
        """
        if self._aligned_cmps is not None:
            return self._aligned_cmps

        # Check module-level cache
        cache_key = 'asm2d_to_madm1'
        if cache_key in _aligned_components_cache:
            self._aligned_cmps = _aligned_components_cache[cache_key]
            logger.debug(f"Using cached aligned components for {self.ID}")
            return self._aligned_cmps

        # Build aligned components
        try:
            from core.junction_components import build_junction_components
            asm_cmps, adm_cmps = build_junction_components(
                direction='asm2d_to_madm1',
                set_thermo=False,
            )
            self._aligned_cmps = (asm_cmps, adm_cmps)
            _aligned_components_cache[cache_key] = self._aligned_cmps
            logger.info(f"Built aligned components for junction {self.ID}")
            return self._aligned_cmps
        except Exception as e:
            logger.warning(f"Failed to build aligned components: {e}")
            return None, None

    @property
    def aligned_asm_cmps(self):
        """Get aligned ASM2d components (or None if not aligned)."""
        if self._aligned_cmps:
            return self._aligned_cmps[0]
        return None

    @property
    def aligned_adm_cmps(self):
        """Get aligned mADM1 components (or None if not aligned)."""
        if self._aligned_cmps:
            return self._aligned_cmps[1]
        return None

    def prepare_for_simulation(self) -> dict:
        """
        Prepare junction for simulation by ensuring component alignment.

        Call this method before running System.simulate() when using
        auto_align_components=True.

        Returns
        -------
        dict
            Status with keys: 'aligned', 'asm_count', 'adm_count', 'direction'
        """
        status = {
            'aligned': False,
            'asm_count': 0,
            'adm_count': 0,
            'direction': 'asm2d_to_madm1',
            'junction_id': self.ID,
        }

        if self._auto_align_components:
            asm_cmps, adm_cmps = self.ensure_aligned_components()
            if asm_cmps is not None and adm_cmps is not None:
                status['aligned'] = True
                status['asm_count'] = len(asm_cmps)
                status['adm_count'] = len(adm_cmps)
                logger.info(f"Junction {self.ID} prepared with aligned components")
            else:
                logger.warning(f"Junction {self.ID} alignment failed")
        else:
            logger.debug(f"Junction {self.ID} using default components (auto_align=False)")

        return status

    def _compile_reactions(self):
        """
        Override parent _compile_reactions to use aligned components when enabled.

        This fixes the issue where junctions created under ASM2d thermo have both
        input and output streams with ASM2d components. When auto_align_components=True,
        we use our aligned component sets instead of stream components.
        """
        if self._auto_align_components and self._aligned_cmps is not None:
            # Use aligned components instead of stream components
            cmps_asm, cmps_adm = self._aligned_cmps
            logger.debug(f"Junction {self.ID}: Using aligned components for reaction compilation")
        else:
            # Fall back to parent behavior (get from streams)
            cmps_asm = self.ins[0].components if self.ins[0] else None
            cmps_adm = self.outs[0].components if self.outs[0] else None

            if cmps_asm is None or cmps_adm is None:
                logger.warning(f"Junction {self.ID}: Missing stream components, cannot compile reactions")
                return

        # Call parent's _compile_reactions with our components
        # We need to temporarily override what the parent sees as stream components
        try:
            # Store original stream references
            _orig_ins_cmps = None
            _orig_outs_cmps = None

            if self.ins[0]:
                _orig_ins_cmps = self.ins[0]._components
                self.ins[0]._components = cmps_asm

            if self.outs[0]:
                _orig_outs_cmps = self.outs[0]._components
                self.outs[0]._components = cmps_adm

            # Call parent implementation
            super()._compile_reactions()

        finally:
            # Restore original components
            if _orig_ins_cmps is not None and self.ins[0]:
                self.ins[0]._components = _orig_ins_cmps
            if _orig_outs_cmps is not None and self.outs[0]:
                self.outs[0]._components = _orig_outs_cmps


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
    3. Optional auto_align_components for property alignment (Phase 2B)

    Parameters
    ----------
    auto_align_components : bool, optional
        If True, automatically align component properties (i_COD, i_N, i_P,
        measured_as) between mADM1 and ASM2d component sets before reaction
        compilation. This resolves property mismatch errors with ModifiedADM1.
        Default is False for backward compatibility.
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
        auto_align_components: bool = False,
        **kwargs,
    ):
        # Store conversion parameters
        self._T = T
        self._pH = pH
        self._auto_align_components = auto_align_components
        self._aligned_cmps: Optional[Tuple[Any, Any]] = None

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

        logger.debug(f"Created mADM1toASM2d_custom junction: {ID} (auto_align={auto_align_components})")

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

    @property
    def auto_align_components(self) -> bool:
        """Whether to auto-align component properties before compilation."""
        return self._auto_align_components

    @auto_align_components.setter
    def auto_align_components(self, value: bool):
        self._auto_align_components = value
        # Clear cached components if flag changes
        self._aligned_cmps = None

    def ensure_aligned_components(self) -> Tuple[Any, Any]:
        """
        Ensure component sets are aligned for junction operations.

        Returns aligned copies of mADM1 and ASM2d components with:
        - Formulas removed (unlocks i_COD, i_N, i_P modification)
        - Key properties aligned between equivalent components
        - Cached for reuse across multiple calls

        Returns
        -------
        asm_cmps : CompiledComponents
            ASM2d components aligned for junction
        adm_cmps : CompiledComponents
            mADM1 components aligned for junction
        """
        if self._aligned_cmps is not None:
            return self._aligned_cmps

        # Check module-level cache
        cache_key = 'madm1_to_asm2d'
        if cache_key in _aligned_components_cache:
            self._aligned_cmps = _aligned_components_cache[cache_key]
            logger.debug(f"Using cached aligned components for {self.ID}")
            return self._aligned_cmps

        # Build aligned components
        try:
            from core.junction_components import build_junction_components
            asm_cmps, adm_cmps = build_junction_components(
                direction='madm1_to_asm2d',
                set_thermo=False,
            )
            self._aligned_cmps = (asm_cmps, adm_cmps)
            _aligned_components_cache[cache_key] = self._aligned_cmps
            logger.info(f"Built aligned components for junction {self.ID}")
            return self._aligned_cmps
        except Exception as e:
            logger.warning(f"Failed to build aligned components: {e}")
            return None, None

    @property
    def aligned_asm_cmps(self):
        """Get aligned ASM2d components (or None if not aligned)."""
        if self._aligned_cmps:
            return self._aligned_cmps[0]
        return None

    @property
    def aligned_adm_cmps(self):
        """Get aligned mADM1 components (or None if not aligned)."""
        if self._aligned_cmps:
            return self._aligned_cmps[1]
        return None

    def prepare_for_simulation(self) -> dict:
        """
        Prepare junction for simulation by ensuring component alignment.

        Call this method before running System.simulate() when using
        auto_align_components=True.

        Returns
        -------
        dict
            Status with keys: 'aligned', 'asm_count', 'adm_count', 'direction'
        """
        status = {
            'aligned': False,
            'asm_count': 0,
            'adm_count': 0,
            'direction': 'madm1_to_asm2d',
            'junction_id': self.ID,
        }

        if self._auto_align_components:
            asm_cmps, adm_cmps = self.ensure_aligned_components()
            if asm_cmps is not None and adm_cmps is not None:
                status['aligned'] = True
                status['asm_count'] = len(asm_cmps)
                status['adm_count'] = len(adm_cmps)
                logger.info(f"Junction {self.ID} prepared with aligned components")
            else:
                logger.warning(f"Junction {self.ID} alignment failed")
        else:
            logger.debug(f"Junction {self.ID} using default components (auto_align=False)")

        return status

    def _compile_reactions(self):
        """
        Override parent _compile_reactions to use aligned components when enabled.

        This fixes the issue where junctions created under mADM1 thermo have both
        input and output streams with mADM1 components. When auto_align_components=True,
        we use our aligned component sets instead of stream components.
        """
        if self._auto_align_components and self._aligned_cmps is not None:
            # Use aligned components instead of stream components
            # Note: For mADM1→ASM2d, input is mADM1 (adm), output is ASM2d (asm)
            cmps_asm, cmps_adm = self._aligned_cmps
            logger.debug(f"Junction {self.ID}: Using aligned components for reaction compilation")
        else:
            # Fall back to parent behavior (get from streams)
            cmps_adm = self.ins[0].components if self.ins[0] else None
            cmps_asm = self.outs[0].components if self.outs[0] else None

            if cmps_asm is None or cmps_adm is None:
                logger.warning(f"Junction {self.ID}: Missing stream components, cannot compile reactions")
                return

        # Call parent's _compile_reactions with our components
        # We need to temporarily override what the parent sees as stream components
        try:
            # Store original stream references
            _orig_ins_cmps = None
            _orig_outs_cmps = None

            if self.ins[0]:
                _orig_ins_cmps = self.ins[0]._components
                self.ins[0]._components = cmps_adm  # Input is mADM1

            if self.outs[0]:
                _orig_outs_cmps = self.outs[0]._components
                self.outs[0]._components = cmps_asm  # Output is ASM2d

            # Call parent implementation
            super()._compile_reactions()

        finally:
            # Restore original components
            if _orig_ins_cmps is not None and self.ins[0]:
                self.ins[0]._components = _orig_ins_cmps
            if _orig_outs_cmps is not None and self.outs[0]:
                self.outs[0]._components = _orig_outs_cmps
