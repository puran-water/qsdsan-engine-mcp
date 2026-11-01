# -*- coding: utf-8 -*-
'''
Custom AnaerobicCSTR subclass for mADM1 with 4 biogas species support.

This module extends QSDsan's AnaerobicCSTR to handle Modified ADM1 (mADM1)
which tracks 4 biogas species (CH4, CO2, H2, H2S) instead of the standard
3 species (CH4, CO2, H2).

Author: Adapted from QSDsan's AnaerobicCSTR
License: University of Illinois/NCSA Open Source License
'''

import logging

import numpy as np
from scipy.optimize import brentq, newton
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()
from qsdsan.process_models._adm1 import T_correction_factor
from qsdsan.unit_operations import AnaerobicCSTR

logger = logging.getLogger(__name__)

__all__ = (
    'AnaerobicCSTRmADM1',
    'madm1_gas_mass_to_molar_conversion',
    'solve_madm1_algebraic_h2',
    'sync_madm1_biogas_outlets',
)


def madm1_gas_mass_to_molar_conversion(components, gas_component_indices, gas_ids):
    """Return exact kmol-gas per native-kg conversion for mADM1 gas rows.

    The COD-to-mass metadata in the inherited ADM1 components is rounded, so
    ``i_mass / chem_MW`` yields 0.1250047 rather than exactly 0.125 kmol e per
    kg COD for H2 and CH4. That tiny discrepancy accumulates in long redox
    ledgers. Use the declared native measurement bases directly and retain the
    inherited metadata formula only for an unknown future gas component.
    """

    exact = {
        'S_h2': 1.0 / 16.0,      # kg COD per kmol H2
        'S_ch4': 1.0 / 64.0,     # kg COD per kmol CH4
        'S_IC': 1.0 / 12.0107,   # kg C per kmol CO2
        'S_IS': 1.0 / 32.065,    # kg S per kmol H2S
    }
    fallback = (
        components.i_mass[gas_component_indices]
        / components.chem_MW[gas_component_indices]
    )
    return np.asarray(
        [exact.get(gas_id, float(fallback[index])) for index, gas_id in enumerate(gas_ids)],
        dtype=float,
    )


def solve_madm1_algebraic_h2(
    residual_function,
    state_arr,
    params,
    h2_stoichio,
    V_liq,
    S_h2_in,
    *,
    initial_guess=2.8309e-7,
    gradient_function=None,
):
    """Solve the nonnegative algebraic H2 balance and verify its residual.

    The former Newton solve used its default absolute *step* tolerance while
    H2 itself is typically O(1e-7) kg COD/m3.  It could therefore stop after a
    small concentration update with a material mass-balance residual; the ODE
    then overwrote ``dS_h2/dt`` with zero and silently created/destroyed redox
    equivalents.  A sign-bracketed Brent solve is insensitive to the gradient
    scaling and is accepted only after an explicit residual check.
    """

    def evaluate(value):
        return float(
            residual_function(
                float(value),
                state_arr,
                None,
                params,
                h2_stoichio,
                V_liq,
                S_h2_in,
            )
        )

    residual_tolerance = 1e-10
    if gradient_function is not None:
        def gradient(value):
            return float(
                gradient_function(
                    float(value),
                    state_arr,
                    None,
                    params,
                    h2_stoichio,
                    V_liq,
                    S_h2_in,
                )
            )

        try:
            candidate = float(
                newton(
                    evaluate,
                    max(float(initial_guess), 1e-16),
                    fprime=gradient,
                    tol=1e-14,
                    rtol=1e-10,
                    maxiter=20,
                )
            )
            candidate_residual = evaluate(candidate)
            if (
                np.isfinite(candidate)
                and candidate >= 0.0
                and abs(candidate_residual) <= residual_tolerance
            ):
                return candidate
        except (RuntimeError, ValueError, ZeroDivisionError, FloatingPointError):
            pass

    lower = 0.0
    f_lower = evaluate(lower)
    if abs(f_lower) <= 1e-12:
        return lower
    upper = max(1e-6, 10.0 * abs(float(initial_guess)), 10.0 * abs(float(S_h2_in)))
    f_upper = evaluate(upper)
    for _ in range(24):
        if f_lower * f_upper <= 0.0:
            break
        upper *= 10.0
        f_upper = evaluate(upper)
    else:
        # A stiff-solver TRIAL state with negative substrates can make the H2
        # balance a net sink at every nonnegative H2 (a Monod term with
        # S < -K flips hugely negative), so no nonnegative root exists for
        # the raw state (observed 2026-08-25: a BDF trial step drove S_su to
        # -0.43 kg/m3 and evaluate(0) to -207). Retry once on the
        # nonnegative PROJECTION of the state — a no-op at every accepted
        # physical step, and the physically meaningful reading of a trial
        # excursion. A physical state that still cannot bracket stays a
        # loud failure: silently zeroing H2 would create/destroy redox
        # equivalents, which is exactly what this solver exists to prevent.
        raw_state = np.asarray(state_arr, dtype=float)
        if np.any(raw_state < 0.0):
            projected = np.maximum(raw_state, 0.0)
            return solve_madm1_algebraic_h2(
                residual_function,
                projected,
                params,
                h2_stoichio,
                V_liq,
                max(float(S_h2_in), 0.0),
                initial_guess=initial_guess,
                gradient_function=gradient_function,
            )
        raise RuntimeError(
            "could not bracket the nonnegative mADM1 algebraic H2 root; "
            f"residuals were {f_lower:.6g} at 0 and {f_upper:.6g} at {upper:.6g}"
        )

    root = float(
        brentq(
            evaluate,
            lower,
            upper,
            xtol=1e-15,
            rtol=1e-12,
            maxiter=100,
        )
    )
    residual = evaluate(root)
    residual_tolerance += 1e-9 * max(abs(f_lower), abs(f_upper), 1.0)
    if not np.isfinite(root) or root < 0.0 or abs(residual) > residual_tolerance:
        raise RuntimeError(
            "mADM1 algebraic H2 solve did not close its mass balance: "
            f"root={root:.6g}, residual={residual:.6g}, "
            f"tolerance={residual_tolerance:.6g}"
        )
    return root

def gas_rate_slice(unit) -> slice:
    """The gas-liquid transfer positions in ``unit.model``'s rate vector.

    The mADM1 model names them (``ModifiedADM1.gas_transfer_rate_slice``), so a process
    appended after the transfers cannot shift them. A model that names none keeps the
    upstream convention: the trailing ``unit._n_gas`` rates.
    """
    named = getattr(getattr(unit, 'model', None), 'gas_transfer_rate_slice', None)
    if named is not None:
        return named
    return slice(-int(unit._n_gas), None)


class AnaerobicCSTRmADM1(AnaerobicCSTR):
    """
    Anaerobic CSTR with support for mADM1's 4 biogas species.

    Extends QSDsan's AnaerobicCSTR to handle Modified ADM1 models that track
    H2S in addition to the standard biogas species (CH4, CO2, H2).

    The key modification is generalizing gas handling from hardcoded 3 species
    to dynamic `n_gas` species based on the process model's `_biogas_IDs`.

    Parameters
    ----------
    Same as AnaerobicCSTR, plus:

    algebraic_h2 : bool, optional
        Whether to use algebraic H2 tracking. Default is True (recommended for
        mADM1 stability, following QSDsan BSM2 pattern).

    Notes
    -----
    - Replaces hardcoded `rhos[-3:]` with the model's NAMED gas-transfer positions
      (`ModifiedADM1.gas_transfer_rate_slice`; the trailing `n_gas` rates for a model that names none)
    - Generalizes gas transfer calculations to support variable biogas species
    - Maintains compatibility with standard ADM1 (3 species) and mADM1 (4 species)
    - Follows Codex recommendation to keep `algebraic_h2 = True` for numerical stability

    Examples
    --------
    >>> from qsdsan import process_models as pc, WasteStream
    >>> from models.madm1 import create_madm1_cmps, ModifiedADM1
    >>> from models.reactors import AnaerobicCSTRmADM1
    >>>
    >>> # Create mADM1 components and model
    >>> cmps = create_madm1_cmps()
    >>> madm1 = ModifiedADM1(components=cmps)
    >>>
    >>> # Create influent stream
    >>> inf = WasteStream('influent', T=308.15)
    >>> inf.set_flow_by_concentration(1000, concentrations={...}, units=('m3/d', 'kg/m3'))
    >>>
    >>> # Create reactor with 4 biogas species support
    >>> AD = AnaerobicCSTRmADM1('AD', ins=inf, outs=('biogas', 'effluent'),
    ...                         V_liq=10000, V_gas=1000, T=308.15,
    ...                         model=madm1, isdynamic=True)
    >>> AD.algebraic_h2 = True  # Recommended for stability

    See Also
    --------
    qsdsan.unit_operations.AnaerobicCSTR
    utils.qsdsan_madm1.ModifiedADM1
    """

    # ------------------------------------------------------------------ #
    # Opt-in micro-aeration headspace (models/madm1_microaeration.py)
    # ------------------------------------------------------------------ #
    #: the model's micro-aeration headspace description; None on every model that does not compile the extension
    _headspace_extension = None
    #: the dosed oxidant, kmol/d per headspace state (zeros until ``set_headspace_oxidant`` states a dose)
    _headspace_gas_inflow_kmol_d = None
    #: the wetted headspace surface the caller stated (m2); None until stated, never defaulted
    headspace_wetted_area_m2 = None

    model = property(AnaerobicCSTR.model.fget, doc=AnaerobicCSTR.model.__doc__)

    @model.setter
    def model(self, model):
        AnaerobicCSTR.model.fset(self, model)
        headspace = getattr(model, 'microaeration_headspace', None)
        self._headspace_extension = headspace
        if headspace is None:
            return
        # The micro-aeration variant carries two more headspace states AFTER the four base gases, at the positions
        # the extension names (S_o2, S_n2). ``_biogas_IDs`` / ``_gas_cmp_idx`` stay the four gases with a liquid
        # partner and a core transfer row; N2 has none (dissolved N2 is omitted), so it is a gas-only state.
        extra = headspace.gas_state_ids[headspace.base_gas_state_count:]
        self._n_gas = len(headspace.gas_state_ids)
        keys = self._state_keys[:-1] + [f'{gas_id}_gas' for gas_id in extra] + self._state_keys[-1:]
        units = (['kg/m3'] * len(self.components) + ['M'] * self._n_gas + ['m3/d'])
        self._state_keys = keys
        self._state_header = [f'{name} [{unit}]' for name, unit in zip(keys, units)]
        self._headspace_gas_inflow_kmol_d = np.zeros(self._n_gas, dtype=float)

    def set_headspace_oxidant(self, *, headspace_wetted_area_m2, gas_inflow_kmol_d):
        """State the micro-aeration geometry and dose (the opt-in variant only).

        ``headspace_wetted_area_m2`` is the digester's own wetted headspace surface (liquid surface + wetted wall
        band): the kernel's G1 and G4 read it per m3 of liquor, and it is never defaulted -- a variant reactor
        with none stated refuses its first rate call. ``gas_inflow_kmol_d`` is the dosed gas per headspace state
        (``{gas id: kmol/d}`` over the extension's gas ids; ``models.madm1_microaeration.resolve_oxidant_inlet``
        builds it from the oxidant statement the WaterTAP digester takes, argon lumped into N2 by moles)."""
        from .madm1_microaeration import bind_headspace_wetted_area

        headspace = self._headspace_extension
        if headspace is None:
            raise ValueError("set_headspace_oxidant: the reactor's model does not compile the micro-aeration "
                             "extension (ModifiedADM1(organic_sulfur=True, microaeration=True))")
        unknown = sorted(set(gas_inflow_kmol_d) - set(headspace.gas_state_ids))
        if unknown:
            raise ValueError(f"set_headspace_oxidant: {unknown} are not headspace states "
                             f"{headspace.gas_state_ids}")
        inflow = np.zeros(self._n_gas, dtype=float)
        for gas_id, value in gas_inflow_kmol_d.items():
            value = float(value)
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"set_headspace_oxidant: the inflow of {gas_id} must be finite and >= 0, "
                                 f"got {value!r}")
            inflow[headspace.index(gas_id)] = value
        bind_headspace_wetted_area(self.model.rate_function._params,
                                   headspace_wetted_area_m2=headspace_wetted_area_m2,
                                   liquid_volume_m3=self.V_liq)
        self.headspace_wetted_area_m2 = float(headspace_wetted_area_m2)
        self._headspace_gas_inflow_kmol_d = inflow

    def configure_explicit_solids_retention(
        self,
        *,
        solids_srt_days,
        permeate_rejection_fraction,
    ):
        """Enable a distinct permeate and mixed-liquor wasting outlet.

        The inherited QSDsan ``fraction_retain`` formulation lowers the
        particulate concentration in one liquid outlet but does not expose a
        sludge-waste stream or an independently specified SRT.  In this mode,
        retained components leave through membrane leakage plus a mixed-liquor
        waste stream, with the combined first-order loss fixed at ``1/SRT``.
        Soluble components leave through both liquid outlets at the hydraulic
        rate, so liquid volume and component mass remain closed.
        """

        srt = float(solids_srt_days)
        rejection = float(permeate_rejection_fraction)
        if not np.isfinite(srt) or srt <= 0.0:
            raise ValueError("solids_srt_days must be finite and positive")
        if not np.isfinite(rejection) or not 0.0 < rejection <= 1.0:
            raise ValueError(
                "permeate_rejection_fraction must be finite and in (0, 1]"
            )
        if len(self.outs) != 3:
            raise ValueError(
                "explicit solids retention requires outlets (biogas, permeate, sludge_waste)"
            )
        if not np.any(np.asarray(self._f_retain, dtype=float) > 0.0):
            raise ValueError("explicit solids retention requires retained components")
        retained = np.asarray(self._f_retain, dtype=float) > 0.0
        self._f_retain[retained] = rejection
        self._solids_srt_days = srt
        self._permeate_rejection_fraction = rejection
        self._explicit_solids_wasting = True

        # Validate the configured SRT against the current hydraulic flow now,
        # before the ODE starts. The same check is repeated if flow changes.
        flow_m3_d = sum(stream.get_total_flow('m3/d') for stream in self.ins)
        self.explicit_liquid_outlet_flows(flow_m3_d)

    @property
    def explicit_solids_wasting_enabled(self):
        return bool(getattr(self, '_explicit_solids_wasting', False))

    def explicit_liquid_outlet_flows(self, inlet_flow_m3_d):
        """Return ``(permeate, waste)`` flows that realize the specified SRT."""

        if not self.explicit_solids_wasting_enabled:
            raise RuntimeError("explicit solids retention is not configured")
        flow = float(inlet_flow_m3_d)
        if not np.isfinite(flow) or flow <= 0.0:
            raise ValueError("explicit SRT operation requires positive finite inlet flow")
        rejection = float(self._permeate_rejection_fraction)
        target_solids_export_m3_d = float(self.V_liq) / float(self._solids_srt_days)
        waste = (
            target_solids_export_m3_d - flow * (1.0 - rejection)
        ) / rejection
        tolerance = 1e-10 * max(flow, target_solids_export_m3_d, 1.0)
        if waste < -tolerance:
            max_srt = float(self.V_liq) / (flow * (1.0 - rejection))
            raise ValueError(
                "requested solids_srt_days exceeds the maximum attainable SRT "
                f"({max_srt:.6g} d) at the configured membrane rejection"
            )
        if waste > flow + tolerance:
            hrt = float(self.V_liq) / flow
            raise ValueError(
                "requested solids_srt_days is below the hydraulic retention time "
                f"({hrt:.6g} d); the implied sludge-waste flow exceeds influent flow"
            )
        waste = min(max(waste, 0.0), flow)
        return flow - waste, waste

    def _run(self):
        if not self.explicit_solids_wasting_enabled:
            return super()._run()
        mixed = self._mixed
        mixed.mix_from(self.ins)
        gas, permeate, sludge_waste = self.outs
        inlet_flow_m3_h = mixed.F_vol
        permeate_flow_m3_d, waste_flow_m3_d = self.explicit_liquid_outlet_flows(
            inlet_flow_m3_h * 24.0
        )
        permeate.copy_like(mixed)
        permeate.set_total_flow(permeate_flow_m3_d, 'm3/d')
        permeate.T = self.T
        sludge_waste.copy_like(mixed)
        sludge_waste.set_total_flow(waste_flow_m3_d, 'm3/d')
        sludge_waste.T = self.T
        gas.copy_like(self._biogas)
        gas.T = self.T
        if self._fixed_P_gas:
            gas.P = self.headspace_P * 1e5

    def _update_state(self):
        """Project the reactor state to outlets without mutating/clipping it."""

        y = self._state
        y[-1] = sum(stream.state[-1] for stream in self.ins)
        f_rtn = self._f_retain
        n_cmps = len(self.components)
        pH = self.pH_ctrl or self._tempstate.pop('pH', 7)
        permeate_conc = y[:n_cmps] * (1.0 - f_rtn) * 1e3

        if self.explicit_solids_wasting_enabled:
            gas, permeate, sludge_waste = self._outs
            permeate_flow, waste_flow = self.explicit_liquid_outlet_flows(y[-1])
            if permeate.state is None:
                permeate.state = np.append(permeate_conc, permeate_flow)
            else:
                permeate.state[:n_cmps] = permeate_conc
                permeate.state[-1] = permeate_flow
            if sludge_waste.state is None:
                sludge_waste.state = np.append(y[:n_cmps] * 1e3, waste_flow)
            else:
                sludge_waste.state[:n_cmps] = y[:n_cmps] * 1e3
                sludge_waste.state[-1] = waste_flow
            permeate._pH = sludge_waste._pH = pH
        else:
            gas, liquid = self._outs
            if liquid.state is None:
                liquid.state = np.append(permeate_conc, y[-1])
            else:
                liquid.state[:n_cmps] = permeate_conc
                liquid.state[-1] = y[-1]
            liquid._pH = pH

        if gas.state is None:
            gas.state = np.zeros(n_cmps + 1)
        # The outlet stream carries the gases that have a liquid-partner component: every gas state of a model
        # without a headspace extension (``len(_gas_cmp_idx) == _n_gas``, the slice it always was). The opt-in
        # micro-aeration variant's O2 and N2 states follow them and are NOT on the stream (N2 has no component);
        # ``models.madm1_microaeration.microaeration_headspace_report`` and the state are their record.
        gas.state[self._gas_cmp_idx] = y[n_cmps:(n_cmps + len(self._gas_cmp_idx))]
        gas.state[self.components.index('H2O')] = self._S_vapor
        gas.state[-1] = self._q_gas
        gas.state[:n_cmps] = (
            gas.state[:n_cmps]
            * self.components.chem_MW
            / self.components.i_mass
            * 1e3
        )

    def _update_dstate(self):
        if not self.explicit_solids_wasting_enabled:
            return super()._update_dstate()
        self._tempstate = self.model.rate_function._params['root'].data.copy()
        dy = self._dstate
        n_cmps = len(self.components)
        gas, permeate, sludge_waste = self._outs
        permeate_dconc = dy[:n_cmps] * (1.0 - self._f_retain) * 1e3
        waste_dconc = dy[:n_cmps] * 1e3
        if permeate.dstate is None:
            permeate.dstate = np.append(permeate_dconc, 0.0)
        else:
            permeate.dstate[:n_cmps] = permeate_dconc
            permeate.dstate[-1] = 0.0
        if sludge_waste.dstate is None:
            sludge_waste.dstate = np.append(waste_dconc, 0.0)
        else:
            sludge_waste.dstate[:n_cmps] = waste_dconc
            sludge_waste.dstate[-1] = 0.0
        if gas.dstate is None:
            gas.dstate = np.zeros(n_cmps + 1)

    @property
    def pH(self):
        """Live liquid-phase pH from the mADM1 PCM charge balance.

        Solves the model's ``solve_pH`` against the reactor's current dynamic
        state (``_state``) using the rate-function param dict (the model's
        ``solve_pH`` default of ``self._rhos_func.params`` is not populated, so
        the params are passed explicitly). Exposed as a readable so dynamic
        control sensors (e.g. the B4 base-dose pH loop via
        ``core.control.read_process_variable``) can sample digester pH directly.
        Returns ``None`` if the state/params are not yet available.
        """
        model = getattr(self, '_model', None) or getattr(self, 'model', None)
        state = getattr(self, '_state', None)
        if model is None or state is None:
            return None
        params = None
        rate_function = getattr(model, 'rate_function', None)
        if rate_function is not None:
            params = getattr(rate_function, '_params', None) or getattr(rate_function, 'params', None)
        try:
            return float(model.solve_pH(np.asarray(state, dtype=float), params=params))
        except Exception:
            return None

    def _seed_operating_T_op(self):
        """Seed the rate-function operating temperature from the reactor's ``self.T``.

        Follow-up #21: an UNCONTROLLED mADM1 digester never propagated its operating
        temperature (``self.T``) into the rate-function param dict, so ``rhos_madm1``
        and ``pcm`` fell back to ``T_base`` (298.15 K) for the liquid-phase chemistry
        (acid-base Ka, Henry KH, mineral Ksp, pH speciation) WHILE the gas phase ran
        at ``self.T`` (``reactors.py`` ``gas.T = self.T`` / ``biogas_p = R*T_op*...``
        with ``T_op == self.T``). That left the baseline liquid chemistry solved
        ~10 C too cold for a mesophilic (308.15 K) digester -- an internal temperature
        inconsistency. Seeding ``params['T_op'] = self.T`` here makes the uncontrolled
        baseline solve its liquid chemistry at the digester's actual operating T.

        The B4 dynamic heating controller (``core.control.DigesterHeatingActuator``)
        still OVERRIDES ``params['T_op']`` per step on a controlled run and keeps
        ``self.T`` in sync (it writes ``unit.T`` then ``params['T_op']`` to the same
        value, then forces an ODE recompile). Re-seeding from ``self.T`` on that
        recompile therefore re-establishes the SAME value the controller set -- no
        conflict. When ``self.T == T_base`` (298.15 K) the ``rhos_madm1`` ``T_op ==
        T_base`` fast path still holds, so a genuinely isothermal-at-25C digester
        keeps using the base constants (no spurious Van't Hoff recompute).

        ``MultiKinetics.params`` is a read-only property returning the SAME dict object
        as ``._params``, so a single mutation updates both surfaces; both references
        are written explicitly (and only once if they alias) for robustness.
        """
        model = getattr(self, '_model', None) or getattr(self, 'model', None)
        if model is None:
            return
        rate_function = getattr(model, 'rate_function', None)
        if rate_function is None:
            return
        T_op = float(self.T)
        p_underscore = getattr(rate_function, '_params', None)
        if isinstance(p_underscore, dict):
            p_underscore['T_op'] = T_op
        p_public = getattr(rate_function, 'params', None)
        if isinstance(p_public, dict) and p_public is not p_underscore:
            p_public['T_op'] = T_op

    def _init_state(self):
        """
        Initialize dynamic state with support for variable biogas species count.

        Overrides parent to build the headspace state vector based on the
        process model's `_biogas_IDs` instead of assuming three gases.
        """
        mixed = self._mixed
        Q = mixed.get_total_flow('m3/d')
        if self._concs is not None:
            Cs = self._concs * 1e-3  # mg/L to kg/m3
        else:
            Cs = mixed.conc * 1e-3

        n_gas = getattr(self, '_n_gas', 3)
        Gs = np.zeros(n_gas, dtype=float)
        defaults = {'S_h2': 0.041 * 0.01,
                    'S_ch4': 0.041 * 0.57,
                    'S_IC': 0.041 * 0.40}
        # A WaterTAP mADM1 steady solve can seed the headspace through the
        # WaterTAP -> QSDsan bridge (roadmap C1): the converged gas composition
        # (kmol/m3, keyed by `_biogas_IDs`) is stored on `_seed_gas_kmol_m3` by
        # `utils.state_checkpoint.apply_madm1_gas_seed`. Honour it here so the
        # dynamic run STARTS at the steady headspace instead of re-equilibrating
        # from the cold defaults. The seed is indexed by `_biogas_IDs` -- the same
        # ordering `_madm1_gas_slice` uses -- so it survives the ADM1(3) vs
        # mADM1(4, +S_IS) species-count difference.
        seed_gas = getattr(self, '_seed_gas_kmol_m3', None)
        model_gas_ids = getattr(self._model, '_biogas_IDs', ())
        for i, ID in enumerate(model_gas_ids):
            if seed_gas and seed_gas.get(ID) is not None:
                Gs[i] = float(seed_gas[ID])
            else:
                Gs[i] = defaults.get(ID, 0.0)

        self._state = np.concatenate((Cs, Gs, [Q])).astype('float64')
        self._dstate = np.zeros_like(self._state)

        # Follow-up #21: seed the operating T_op so the uncontrolled baseline
        # chemistry (and any pre-run ``AD.pH`` sample) starts at the digester's
        # actual operating temperature, not T_base.
        self._seed_operating_T_op()

    def _compile_ODE(self, algebraic_h2=True, pH_ctrl=None):
        """
        Compile ODE system with support for variable biogas species.

        Overrides parent to replace hardcoded `rhos[-3:]` with the model's named
        gas-transfer positions (`_gas_rate_slice`) for mADM1's 4 biogas species.

        This is the core fix identified by Codex - generalizing gas handling
        to work with any number of biogas species defined by the process model.
        """
        if self._model is None:
            super()._compile_ODE(algebraic_h2=algebraic_h2, pH_ctrl=pH_ctrl)
            return

        # Follow-up #21: finalize the rate-function operating T_op from self.T
        # before the integration closures are built. This is the chokepoint where
        # the liquid-phase chemistry (Ka/KH/Ksp/pH via pcm + rhos_madm1) is wired
        # for the run, and it is re-run on every ODE recompile -- including the B4
        # heating controller's forced recompile, where self.T already tracks the
        # controller's commanded temperature, so this stays consistent with it.
        self._seed_operating_T_op()

        cmps = self.components
        f_rtn = self._f_retain
        _state = self._state
        _dstate = self._dstate
        _update_dstate = self._update_dstate

        if pH_ctrl:
            _params = self.model.rate_function.params
            # CRITICAL FIX (per Codex): Must call rate_function.function() to bypass MultiKinetics wrapper
            # MultiKinetics.__call__ only accepts (state_arr), so we need the underlying callable
            # Also, h must be a tuple (pH, nh3, co2, acts) from PCM solver, not a scalar
            # For pH control diagnostic mode, we create a simplified tuple with fixed pH
            hydrogen_tuple = (pH_ctrl, 0.001, 0.001, None)  # (pH, nh3_approx, co2_approx, acts=None)
            _f_rhos = lambda state_arr: self.model.rate_function.function(state_arr, _params, h=hydrogen_tuple)
        else:
            _f_rhos = self.model.rate_function
        _f_param = self.model.params_eval
        n_cmps = len(cmps)
        n_gas = self._n_gas
        gas_rate_slice = self._gas_rate_slice()
        V_liq = self.V_liq
        V_gas = self.V_gas
        # Gas transfer rates retain each liquid component's native basis: kg
        # COD for H2/CH4, kg C for CO2, and kg S for H2S. Use the explicit
        # basis map below; the inherited i_mass/MW metadata is directionally
        # correct but rounded enough to accumulate a redox-balance residual.
        gas_mass2mol_conversion = madm1_gas_mass_to_molar_conversion(
            cmps,
            self._gas_cmp_idx,
            self.model._biogas_IDs,
        )
        hasexo = bool(len(self._exovars))
        f_exovars = self.eval_exo_dynamic_vars
        f_qgas = self.f_q_gas_fixed_P_headspace if self._fixed_P_gas else self.f_q_gas_var_P_headspace

        # Opt-in micro-aeration headspace (None on every model that does not compile the extension): two more gas
        # states after the base four, the gas rows of the extension's rates (G1 consumes H2S and O2, G4 moves O2),
        # and the dosed oxidant. Read at NAMED positions; the base four keep the arithmetic below unchanged.
        headspace = self._headspace_extension
        if headspace is not None:
            if self._fixed_P_gas:
                # the fixed-pressure outflow is a molar balance over the transfers alone; the oxidant inflow and
                # G1's gas consumption are not in it, so it is refused rather than run short
                raise ValueError("the micro-aeration headspace is built for the variable-pressure headspace only "
                                 "(fixed_headspace_P=False)")
            n_base_gas = headspace.base_gas_state_count
            headspace_rate_idx = np.asarray(headspace.rate_indices, dtype=int)
            headspace_molar = np.asarray(headspace.molar_matrix, dtype=float)

        if self.model._dyn_params:
            def M_stoichio(state_arr):
                _f_param(state_arr)
                return self.model.stoichio_eval().T
            def _M_stoichio(state_arr):
                return M_stoichio(state_arr)
        else:
            _M_stoichio = self.model.stoichio_eval().T
            M_stoichio = lambda state_arr: _M_stoichio

        h2_idx = cmps.index('S_h2')
        if algebraic_h2:
            params = self.model.rate_function.params
            if self.model._dyn_params:
                def h2_stoichio(state_arr):
                    return M_stoichio(state_arr)[h2_idx]
            else:
                _h2_stoichio = _M_stoichio[h2_idx]
                h2_stoichio = lambda state_arr: _h2_stoichio
            dydt_Sh2_AD = self.model.dydt_Sh2_AD
            grad_dydt_Sh2_AD = self.model.grad_dydt_Sh2_AD
            def solve_h2(QC, S_in, T, h=pH_ctrl):
                # The governed root helper passes h=None so the live mADM1 pH
                # and activities are recomputed at each H2 trial.
                S_h2_0 = 2.8309e-07
                S_h2_in = S_in[h2_idx]
                return solve_madm1_algebraic_h2(
                    dydt_Sh2_AD,
                    QC,
                    params,
                    h2_stoichio,
                    V_liq,
                    S_h2_in,
                    initial_guess=S_h2_0,
                    gradient_function=grad_dydt_Sh2_AD,
                )
            def update_h2_dstate(dstate):
                dstate[h2_idx] = 0.
        else:
            def solve_h2(QC, S_in, T):
                return QC[h2_idx]
            def update_h2_dstate(dstate):
                pass

        def dy_dt(t, QC_ins, QC, dQC_ins):
            Q_ins = QC_ins[:, -1]
            S_ins = QC_ins[:, :-1] * 1e-3  # mg/L to kg/m3
            Q = sum(Q_ins)
            S_in = Q_ins @ S_ins / Q

            if hasexo:
                exo_vars = f_exovars(t)
                QC = np.append(QC, exo_vars)
                T = exo_vars[0]
            else:
                T = self.T
            QC[h2_idx] = _state[h2_idx] = solve_h2(QC, S_in, T)
            rhos = _f_rhos(QC)
            S_liq = QC[:n_cmps]
            S_gas = QC[n_cmps:(n_cmps + n_gas)]
            if self.explicit_solids_wasting_enabled:
                permeate_flow, waste_flow = self.explicit_liquid_outlet_flows(Q)
                liquid_export_coeff = np.full(n_cmps, Q, dtype=float)
                retained = f_rtn > 0.0
                liquid_export_coeff[retained] = (
                    waste_flow
                    + permeate_flow * (1.0 - f_rtn[retained])
                )
            else:
                liquid_export_coeff = Q * (1.0 - f_rtn)
            _dstate[:n_cmps] = (
                Q_ins @ S_ins - liquid_export_coeff * S_liq
            ) / V_liq + np.dot(M_stoichio(QC), rhos)
            gas_rhos = rhos[gas_rate_slice]
            q_gas = f_qgas(gas_rhos, S_gas, T)
            if headspace is None:
                _dstate[n_cmps:(n_cmps + n_gas)] = - q_gas * S_gas / V_gas \
                    + gas_rhos * V_liq / V_gas * gas_mass2mol_conversion
            else:
                # out = in + transfer + gas-phase reaction for every headspace state. The base four take the SAME
                # expression as above (so a variant with no oxygen moves them identically); the extension's gas
                # rows and the dosed inflow are added on top, and N2 has neither transfer nor reaction: out = in.
                gas_dstate = - q_gas * S_gas / V_gas
                gas_dstate[:n_base_gas] = - q_gas * S_gas[:n_base_gas] / V_gas \
                    + gas_rhos * V_liq / V_gas * gas_mass2mol_conversion
                gas_dstate += (headspace_molar @ rhos[headspace_rate_idx]) * V_liq / V_gas \
                    + self._headspace_gas_inflow_kmol_d / V_gas
                _dstate[n_cmps:(n_cmps + n_gas)] = gas_dstate

            # Q derivative (always at n_cmps + n_gas index)
            _dstate[n_cmps + n_gas] = 0.

            # H2 handling (algebraic constraint)
            update_h2_dstate(_dstate)

            _update_dstate()

        self._ODE = dy_dt

    def f_q_gas_fixed_P_headspace(self, rhoTs, S_gas, T):
        """Fixed-pressure gas flow using the same exact native-basis map as the ODE."""

        gas_mass2mol_conversion = madm1_gas_mass_to_molar_conversion(
            self.components,
            self._gas_cmp_idx,
            self.model._biogas_IDs,
        )
        self._q_gas = (
            self._R
            * T
            / (self._P_gas - self.p_vapor(convert_to_bar=True))
            * self.V_liq
            * sum(rhoTs * gas_mass2mol_conversion)
        )
        return self._q_gas

    # ------------------------------------------------------------------ #
    # Biogas-rate readouts (guarded dynamic path, roadmap follow-up #20)
    # ------------------------------------------------------------------ #
    def _gas_slice(self):
        """``slice`` locating the headspace gas species in the dynamic state.

        Derived from the live component count + ``_n_gas`` so it stays correct
        for ADM1(3) vs mADM1(4, +S_IS) -- the same convention as
        ``utils.state_checkpoint._madm1_gas_slice``.
        """
        n_cmps = len(self.components)
        n_gas = int(self._n_gas)
        return slice(n_cmps, n_cmps + n_gas)

    def _gas_rate_slice(self):
        """The gas-liquid transfer positions in the model's rate vector (``gas_rate_slice``)."""
        return gas_rate_slice(self)

    def _live_q_gas_m3_d(self):
        """Post-hoc biogas outflow (m3/d) from the CURRENT headspace state.

        Recomputes the gas-transfer rate at the present dynamic ``_state`` and
        evaluates the same headspace q_gas function the ODE uses (fixed- or
        variable-pressure), which also refreshes ``self._q_gas``. This is the
        canonical dynamic biogas signal (the headspace gas-slice in kmol/m3 times
        the gas-transfer rate), recomputed at the reported state rather than
        whatever partial solver step last touched ``_q_gas`` during integration.
        Returns ``0.0`` if the rate cannot be evaluated.
        """
        try:
            state = np.asarray(self._state, dtype=float)
            rhos = np.asarray(self.model.rate_function(state))
            s_gas = state[self._gas_slice()]
            fq = (
                self.f_q_gas_fixed_P_headspace
                if self._fixed_P_gas
                else self.f_q_gas_var_P_headspace
            )
            return float(fq(rhos[self._gas_rate_slice()], s_gas, self.T))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "could not compute live q_gas for %s: %s",
                getattr(self, "ID", "?"),
                exc,
            )
            return 0.0

    def get_natural_gas_flow(self, units='m3/d'):
        """Live biogas (natural-gas) volumetric outflow readout.

        The steady-state ``_run`` leaves the vapor outlet empty, so a plain
        ``self.outs[0].F_vol`` reads 0 after a guarded dynamic run. This returns
        the live biogas rate recomputed from the current headspace gas-slice and
        gas-transfer rate (and refreshes ``self._q_gas``), so the biogas RATE is
        always reported live -- never the stale steady-state 0.
        """
        q_gas = self._live_q_gas_m3_d()
        if units in ('m3/d', 'm3/day', 'm3 d'):
            return q_gas
        if units in ('m3/hr', 'm3/h', 'm3 hr'):
            return q_gas / 24.0
        raise ValueError(f"unsupported units {units!r} for get_natural_gas_flow")

    def sync_biogas_outlet(self):
        """Sync the vapor-outlet thermo flow (F_vol) from the live headspace.

        During a guarded dynamic run the integrator only writes the headspace
        gas-slice (kmol/m3) onto the dynamic ``_state`` and ``self._q_gas``; the
        vapor outlet stream's THERMO (``mol`` / ``F_vol``, the surface read by
        ``core.results.gas.analyze_gas_stream`` and ``AD.outs[0].F_vol``) is left
        at the ``_run`` steady-state value (empty -> 0). Recompute the live q_gas,
        rebuild the outlet's molar composition from the headspace gas-slice, and
        scale the total to the live volumetric biogas rate so ``F_vol`` is a
        faithful readout of the gas-slice composition times q_gas -- NOT a
        separate state (the gas-slice remains the source of truth). Returns the
        synced biogas rate (m3/d); ``0.0`` when there is no net production.
        """
        gas = self.outs[0] if self.outs else None
        gas_ids = getattr(self._model, "_biogas_IDs", ()) or ()
        if gas is None or not gas_ids:
            return 0.0
        state = np.asarray(self._state, dtype=float)
        s_gas = state[self._gas_slice()]
        q_gas = self._live_q_gas_m3_d()  # refreshes self._q_gas

        try:
            gas.empty()
            gas.phase = 'g'
            gas.T = self.T
            available = set(getattr(gas.components, "IDs", ()) or ())
            total_conc = 0.0
            for gid, conc in zip(gas_ids, s_gas):
                conc = float(conc)
                if conc > 0.0 and gid in available:
                    gas.imol[gid] = conc  # relative headspace molar composition
                    total_conc += conc
            if q_gas > 0.0 and total_conc > 0.0 and float(gas.F_mol) > 0.0:
                # Scale the gas-slice composition to the live volumetric rate so
                # gas.F_vol * 24 == q_gas (faithful readout at the stream's T/P).
                gas.set_total_flow(q_gas, 'm3/d')
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "biogas outlet thermo sync failed for %s: %s",
                getattr(self, "ID", "?"),
                exc,
            )
        return q_gas


def sync_madm1_biogas_outlets(system):
    """Sync every mADM1 digester's vapor-outlet thermo flow from its headspace.

    The guarded dynamic path never refreshes the biogas outlet's thermo flow
    (``F_vol``) from the live headspace gas-slice, so post-run readouts
    (``analyze_gas_stream`` / ``AD.outs[0].F_vol`` / ``get_natural_gas_flow``)
    report 0. Call this after a guarded dynamic run (and before result/biogas
    extraction) so the biogas RATE is reported live. Defensive: never raises into
    a completed run. Returns a list of ``(unit_id, q_gas_m3_d)`` for the units
    that were synced.
    """
    synced = []
    for unit in getattr(system, "units", ()) or ():
        if type(unit).__name__ != "AnaerobicCSTRmADM1":
            continue
        if getattr(unit, "_state", None) is None:
            continue
        try:
            q_gas = unit.sync_biogas_outlet()
            synced.append((getattr(unit, "ID", "?"), q_gas))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "biogas outlet sync skipped for %s: %s",
                getattr(unit, "ID", "?"),
                exc,
            )
    return synced
