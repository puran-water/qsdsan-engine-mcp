# -*- coding: utf-8 -*-
'''
Custom AnaerobicCSTR subclass for mADM1 with 4 biogas species support.

This module extends QSDsan's AnaerobicCSTR to handle Modified ADM1 (mADM1)
which tracks 4 biogas species (CH4, CO2, H2, H2S) instead of the standard
3 species (CH4, CO2, H2).

Author: Adapted from QSDsan's AnaerobicCSTR
License: University of Illinois/NCSA Open Source License
'''

import numpy as np
from scipy.optimize import newton
from qsdsan.processes._adm1 import T_correction_factor
from qsdsan.sanunits import AnaerobicCSTR

__all__ = ('AnaerobicCSTRmADM1',)


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
    - Replaces hardcoded `rhos[-3:]` with dynamic `rhos[-n_gas:]`
    - Generalizes gas transfer calculations to support variable biogas species
    - Maintains compatibility with standard ADM1 (3 species) and mADM1 (4 species)
    - Follows Codex recommendation to keep `algebraic_h2 = True` for numerical stability

    Examples
    --------
    >>> from qsdsan import processes as pc, WasteStream
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
    qsdsan.sanunits.AnaerobicCSTR
    utils.qsdsan_madm1.ModifiedADM1
    """

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
        model_gas_ids = getattr(self._model, '_biogas_IDs', ())
        for i, ID in enumerate(model_gas_ids):
            Gs[i] = defaults.get(ID, 0.0)

        self._state = np.concatenate((Cs, Gs, [Q])).astype('float64')
        self._dstate = np.zeros_like(self._state)

    def _compile_ODE(self, algebraic_h2=True, pH_ctrl=None):
        """
        Compile ODE system with support for variable biogas species.

        Overrides parent to replace hardcoded `rhos[-3:]` with dynamic
        `rhos[-n_gas:]` for mADM1's 4 biogas species.

        This is the core fix identified by Codex - generalizing gas handling
        to work with any number of biogas species defined by the process model.
        """
        if self._model is None:
            super()._compile_ODE(algebraic_h2=algebraic_h2, pH_ctrl=pH_ctrl)
            return

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
        V_liq = self.V_liq
        V_gas = self.V_gas
        # FIX #4: Gas mass-to-molar conversion (kg/m³ → kmol/m³)
        # CRITICAL: Rate function returns rhos in kg COD/m³/d (COD basis, NOT pure mass basis)
        # Must use COD-weighted conversion: i_mass/MW to account for COD content per mole
        # i_mass = kg COD/kg compound, MW = kg/kmol → i_mass/MW = kmol/kg COD
        # Using 1/MW (pure molecular weight) would OVERCOUNT CH4 moles by ~4×
        # Reference: QSDsan upstream AnaerobicCSTR uses i_mass/MW (line 518, 557, 622)
        gas_mass2mol_conversion = cmps.i_mass[self._gas_cmp_idx] / cmps.chem_MW[self._gas_cmp_idx]  # kmol/kg COD
        hasexo = bool(len(self._exovars))
        f_exovars = self.eval_exo_dynamic_vars
        f_qgas = self.f_q_gas_fixed_P_headspace if self._fixed_P_gas else self.f_q_gas_var_P_headspace

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
            unit_conversion = cmps.i_mass / cmps.chem_MW
            solve_pH = self.model.solve_pH
            dydt_Sh2_AD = self.model.dydt_Sh2_AD
            grad_dydt_Sh2_AD = self.model.grad_dydt_Sh2_AD
            def solve_h2(QC, S_in, T, h=pH_ctrl):
                # h parameter should be None for our mADM1 (it will compute pH internally)
                # The dydt_Sh2_AD and grad_dydt_Sh2_AD expect h=None or h=(pH, nh3, co2, acts)
                S_h2_0 = 2.8309e-07
                S_h2_in = S_in[h2_idx]
                return newton(
                    dydt_Sh2_AD, S_h2_0, grad_dydt_Sh2_AD,
                    args=(QC, None, params, h2_stoichio, V_liq, S_h2_in),
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
            _dstate[:n_cmps] = (Q_ins @ S_ins - Q * S_liq * (1 - f_rtn)) / V_liq \
                + np.dot(M_stoichio(QC), rhos)
            gas_rhos = rhos[-n_gas:]
            q_gas = f_qgas(gas_rhos, S_gas, T)
            _dstate[n_cmps:(n_cmps + n_gas)] = - q_gas * S_gas / V_gas \
                + gas_rhos * V_liq / V_gas * gas_mass2mol_conversion

            # Q derivative (always at n_cmps + n_gas index)
            _dstate[n_cmps + n_gas] = 0.

            # H2 handling (algebraic constraint)
            update_h2_dstate(_dstate)

            _update_dstate()

        self._ODE = dy_dt
