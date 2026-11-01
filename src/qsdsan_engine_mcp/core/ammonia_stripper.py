'Rate-limited counter-current ammonia **steam** stripper.'

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from qsdsan_engine_mcp.core.public_units import _StaticStateMixin, _prepare_output, _valid_inputs
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()

from qsdsan import SanUnit

logger = logging.getLogger(__name__)


class StripperPhysicsError(RuntimeError):
    'Public engineering calculation interface.'


class StripperConfigurationError(ValueError):
    """The column cannot be operated as specified -- e.g. not enough steam to reach
    column temperature, so there is nothing left over to strip with."""


# --------------------------------------------------------------------------- constants

#: Latent heat of vaporisation of water, kJ/kg, as a function of saturation temperature.
#: Regnault-form fit good to ~0.3% over 60-140 C, which covers every steam-stripper
#: pressure this will see.
def _h_fg_kJ_kg(T_K: float) -> float:
    """Watson relation anchored on h_fg(0 C) = 2500.9 kJ/kg, exponent fitted to steam
    tables at 100 C. Within 0.2% over 0-200 C, which spans every steam-stripper pressure
    this will see. (The commonly quoted cubic-in-Celsius fit is good to ~50 C and is 1.6%
    low at 100 C -- i.e. wrong in the middle of this unit's operating range.)"""
    T_c = 647.096
    return 2500.9 * ((1.0 - T_K / T_c) / (1.0 - 273.15 / T_c)) ** 0.33059


def henry_cc_co2(T_K: float) -> float:
    """Dimensionless Henry coefficient for CO2, from Carroll, Slupsky & Mather (1991).

    ``ln(H / MPa) = -6.8346 + 1.2817e4/T - 3.7668e6/T^2 + 2.997e8/T^3``, published for
    **273-433 K** -- i.e. it actually covers a steam stripper, which is the point.

    This replaces the fork's van 't Hoff extrapolation of a 25 C constant. That
    extrapolation gives ``H_cc = 4.705`` at 100 C against Carroll's ~2.95-3.08, so it
    made neutral CO2 roughly 60% more volatile than it is -- at the temperature this
    column actually runs. Getting CO2 volatility wrong is not a side issue: CO2 leaves
    first, and how fast it leaves sets the pH trajectory that sets the free-ammonia
    fraction.

    Reference: J. Phys. Chem. Ref. Data 20(6):1201, https://doi.org/10.1063/1.555900
    IAPWS also publishes a high-temperature Henry guideline for CO2 if a cross-check is
    wanted: https://iapws.org/documents/release/HenGuide
    """
    T = float(T_K)
    H_MPa = np.exp(-6.8346 + 1.2817e4 / T - 3.7668e6 / T ** 2 + 2.997e8 / T ** 3)
    rho_molar = water_density_kg_m3(T) / 18.015 * 1e3      # mol/m3
    return H_MPa * 1e6 / (_R_J * T * rho_molar)


def water_density_kg_m3(T_K: float) -> float:
    """Saturated-liquid water density, 0-150 C, within ~0.1%.

    Not a rounding concern here: a column bottom sits near 100 C where water is
    958 kg/m3, so taking 1000 overstates the condensate VOLUME by 4% -- and the
    condensate volume is what sets how much the bottoms are diluted, which is what moves
    every downstream concentration and the recycle ratio R.
    """
    t = T_K - 273.15
    return 1000.0 * (1.0 - (t + 288.9414) / (508929.2 * (t + 68.12963)) * (t - 3.9863) ** 2)


def saturation_temperature_K(P_atm: float) -> float:
    """Water saturation temperature from the Antoine equation (NIST).

    A steam stripper's column temperature is set by pressure, not chosen: at 1 atm the
    bottoms sit at 100 C and there is no operating freedom about it. Quoting a stripping
    temperature inconsistent with the column pressure is one of the easier ways to
    produce an impossible design.
    """
    P_bar = float(P_atm) * 1.01325
    # Two published ranges; the 379-573 K set is 0.45 K out at atmospheric, which is
    # exactly the pressure a direct-steam stripper usually runs at.
    if P_bar <= 1.0132:
        A, B, C = 5.40221, 1838.675, -31.737     # 273-303 K
    elif P_bar <= 3.0:
        A, B, C = 5.0768, 1659.793, -45.854      # 344-373 K
    else:
        A, B, C = 4.6543, 1435.264, -64.848      # 379-573 K
    return B / (A - np.log10(P_bar)) - C


_R_L_ATM = 0.082057  # L.atm/(mol.K)
_R_J = 8.314462      # J/(mol.K)

#: A converged solve must actually satisfy its equations, not merely stop moving.
#: least_squares reports success on a small STEP; this checks the RESIDUAL.
_RESIDUAL_TOL = 1e-8

#: Below this the model's own uncertainty exceeds the number, so it must be reported as
#: an inequality. 1 mg N/L is around routine analytical resolution for TAN in a dirty
#: matrix, and comfortably inside the uncertainty from activity coefficients, neglected
#: carbamate, tray hydraulics and the 0-50 C acid-base extrapolation. A column that
#: computes 0.0000 mg N/L has not achieved 100.000000% removal; it has run off the end of
#: what this model can resolve.
_REPORTING_FLOOR_mgN_L = 1.0

# Recuperator-outlet temperature for the secondary scaling screen: inside every
# Ksp fit's range, and the cooled exchanger surface is where scale is managed.
_RECUPERATOR_SCREEN_T_K = 318.15  # 45 C

#: Concentration floor for the log-space damping below. Well under any reportable

_FLOOR = 1e-12

#: Above this the speciation and Henry correlations are extrapolations, not fits.
_CORRELATION_VALID_MAX_K = 323.15  # 50 C

#: Separate CO2 stage efficiency, OFF by default (None -> same as E_murphree).
#:
#: Setting this below E_murphree stabilises the solve dramatically and barely moves the
#: ammonia answer, and there is a plausible mechanism -- NH4+ <-> NH3 + H+ is a proton
#: transfer relaxing in nanoseconds, while CO2 desorption waits on the slow dehydration

#: independently: `degasser-design-mcp/tools/simulation_sizing.py` drops CO2 from 0.7 to
#: 0.35 above pH 7 because "the carbonate system is sensitive and needs gentler
#: stripping".
#:
#: It is NOT defaulted on, and it must not be described as mechanistic rate limitation.

#: an uncalibrated stabiliser for a different system (packed AIR stripper, PHREEQC), and
#: a number that changes the answer while resting on neither measurement nor theory at
#: the operating point is exactly the kind of parameter this module exists to avoid.
#: Treat any value set here as a fitted parameter and say so wherever it is quoted.



# ----------------------------------------------------------------------------- results


@dataclass
class StageState:
    """One theoretical stage, top (1) to bottom (N)."""

    index: int
    T_K: float
    pH: float
    f_nh3: float                 # free NH3 fraction of TAN
    f_co2: float                 # dissolved CO2 fraction of total inorganic carbon
    x_tan_g_m3: float            # liquid TAN leaving this stage, g N/m3
    x_tic_g_m3: float            # liquid inorganic carbon leaving this stage, g C/m3
    y_nh3_g_m3: float            # vapour NH3 leaving this stage (upward), g N/m3
    y_co2_g_m3: float
    K_nh3: float                 # H_cc * f_nh3, the stage's stripping factor numerator
    K_co2: float

    def as_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items()}


@dataclass
class StripperResult:
    """Everything a reviewer needs to argue with the number, not just the number."""

    converged: bool
    iterations: int
    grade: str
    nh3_removal_fraction: float
    tic_removal_fraction: float
    feed_tan_g_m3: float
    bottoms_tan_g_m3: float
    feed_flow_m3_d: float
    bottoms_flow_m3_d: float
    steam_kg_d: float
    steam_condensed_kg_d: float
    steam_to_feed_kg_m3: float
    vapour_flow_m3_d: float
    column_T_K: float
    column_P_atm: float
    n_stages: int
    E_murphree: float
    E_murphree_co2: float
    nh3_product_kg_N_d: float
    naoh_dose_mol_m3: float
    stages: List[StageState] = field(default_factory=list)
    saturation_indices: Dict[str, float] = field(default_factory=dict)
    saturation_indices_recuperator: Dict[str, float] = field(default_factory=dict)
    #: "ok" when both scaling screens ran; "degraded" when an expected
    #: thermodynamic-validity/availability limitation kept one from running (the
    #: structured reasons live in ``caveats`` under ``scaling_screen_degraded``).
    #: The SI dicts stay pure name->log10(IAP/Ksp) maps either way.
    scaling_screen_status: str = "ok"
    caveats: List[str] = field(default_factory=list)
    closure: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "stages"}
        out["stages"] = [s.as_dict() for s in self.stages]
        return out


# ------------------------------------------------------------------------------- unit


class AmmoniaSteamStripper(_StaticStateMixin, SanUnit):
    'Counter-current direct-steam ammonia stripper.'

    _N_ins = 2
    _N_outs = 2

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        *,
        n_stages: int = 30,
        E_murphree: float = 1.0,
        E_murphree_co2: Optional[float] = None,
        P_atm: float = 1.0,
        T_column_K: Optional[float] = None,
        steam_to_feed_kg_m3: float = 110.0,
        naoh_dose_mol_m3: float = 0.0,
        heat_loss_fraction: float = 0.02,
        max_iter: int = 500,
        tol: float = 1e-6,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        if n_stages < 1:
            raise StripperConfigurationError("n_stages must be >= 1")
        if not 0.0 < E_murphree <= 1.0:
            raise StripperConfigurationError(
                f"E_murphree={E_murphree} must be in (0, 1]. Above 1 a stage would leave "
                "vapour richer than equilibrium with the liquid it contacted, which is "
                "the exact class of error this unit asserts against. Apparent Murphree "
                "efficiencies outside [0, 1] are reported in sour-water service "
                "(Weiland & Hatcher see -100% to +200%) but they are an artefact of "
                "re-absorption between stages, which this cascade already represents."
            )
        self.n_stages = int(n_stages)
        self.E_murphree = float(E_murphree)
        if E_murphree_co2 is not None and not 0.0 < E_murphree_co2 <= 1.0:
            raise StripperConfigurationError("E_murphree_co2 must be in (0, 1]")
        # Defaults to the NH3 value: no silent cap. A CO2 efficiency that differs from
        # the NH3 one is a modelling CHOICE the caller makes explicitly.
        self.E_murphree_co2 = (
            float(E_murphree_co2) if E_murphree_co2 is not None else float(E_murphree)
        )
        self.P_atm = float(P_atm)
        self._T_column_K = T_column_K
        self.steam_to_feed_kg_m3 = float(steam_to_feed_kg_m3)
        self.naoh_dose_mol_m3 = float(naoh_dose_mol_m3)
        self.heat_loss_fraction = float(heat_loss_fraction)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self._speciation_model = None
        self._speciation_T_K = None
        self._warm_start = None
        self.results: Optional[StripperResult] = None
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    # ------------------------------------------------------------------ configuration

    @property
    def T_column_K(self) -> float:
        """Column temperature: the saturation temperature at column pressure unless
        explicitly overridden. A direct-steam column boils; it does not get to choose."""
        if self._T_column_K is not None:
            return float(self._T_column_K)
        return saturation_temperature_K(self.P_atm)

    @T_column_K.setter
    def T_column_K(self, value):
        self._T_column_K = None if value is None else float(value)

    def _speciation(self, T_K: float):
        """A private ``ModifiedASM2d`` held at column temperature.

        Reused rather than reimplemented so the stripper and the biology speciate through
        the *same* constants. ``set_temperature`` moves water, ammonium, both carbonate
        constants, phosphate, acetate, nitrous acid AND the Henry coefficients together --
        freezing some at 25 C while moving others yields a pH from constants that do not
        all describe the same water.

        Note ``ModifiedASM2d(T_op_K=...)`` alone is NOT sufficient here: it corrects only
        NH4+ and HNO2 at construction and leaves ``H_cc`` pinned at 298.15 K, and H_cc is
        precisely what sets the stripping driving force.
        """
        if self._speciation_model is not None and self._speciation_T_K == T_K:
            return self._speciation_model
        from qsdsan_engine_mcp.models.masm2d import ModifiedASM2d, create_masm2d_2n_components

        try:
            model = ModifiedASM2d()
        except Exception:  # components not set on the active thermo yet
            create_masm2d_2n_components(set_thermo=True)
            model = ModifiedASM2d()
        model.set_temperature(T_K)
        self._speciation_model = model
        self._speciation_T_K = T_K
        return model

    # ------------------------------------------------------------------------- solving

    def _run(self):
        feed = self.ins[0]
        steam_in = self.ins[1] if len(self.ins) > 1 else None
        bottoms, vapour = self.outs

        bottoms.copy_like(feed)
        _prepare_output(vapour, feed, phase="g")

        Q_feed = float(feed.get_total_flow("m3/d")) if getattr(feed, "F_vol", 0.0) else 0.0
        if Q_feed <= 0:
            self.results = None
            return

        cmps = feed.components
        i_nh4 = cmps.index("S_NH4")
        i_ic = cmps.index("S_IC")
        i_na = cmps.index("S_Na")

        state = np.asarray(feed.conc, dtype=float)  # g/m3 == mg/L
        result = self.solve_profile(
            state=state,
            Q_feed_m3_d=Q_feed,
            T_feed_K=float(getattr(feed, "T", 308.15)),
            steam_kg_d=self._steam_rate_kg_d(steam_in, Q_feed),
            cmps=cmps,
        )
        self.results = result

        # --- bottoms: stripped, diluted by condensed steam, at column temperature.
        # The dilution is not a detail. Direct steam ADDS water, so every downstream
        # concentration moves and the recycle ratio R moves with it -- which is exactly
        # the term the naive 1/(1-R(1-eta)) amplification formula does not carry.
        dilution = Q_feed / result.bottoms_flow_m3_d
        bottoms_conc = state * dilution
        bottoms_conc[i_nh4] = result.bottoms_tan_g_m3
        bottoms_conc[i_ic] = result.stages[-1].x_tic_g_m3
        if self.naoh_dose_mol_m3 > 0:
            bottoms_conc[i_na] = (state[i_na] + self.naoh_dose_mol_m3 * 22.99) * dilution
        # Temperature BEFORE flow: set_flow_by_concentration solves the bulk-water flow
        # against the stream's density, so setting T afterwards would silently re-scale
        # the volumetric rate away from the value just reported.
        bottoms.T = result.column_T_K
        _set_stream_by_conc(bottoms, cmps, bottoms_conc, result.bottoms_flow_m3_d)

        # --- overhead: the stripped load plus whatever steam did not condense.
        vapour.empty()
        vapour.imass["S_NH4"] = result.nh3_product_kg_N_d / 24.0
        vapour.imass["S_IC"] = (
            result.stages[0].y_co2_g_m3 * result.vapour_flow_m3_d / 1e3 / 24.0
        )
        vapour.imass["H2O"] = (result.steam_kg_d - result.steam_condensed_kg_d) / 24.0
        vapour.T = result.column_T_K
        vapour.P = self.P_atm * 101325.0

    def _steam_rate_kg_d(self, steam_in, Q_feed_m3_d: float) -> float:
        if steam_in is not None and getattr(steam_in, "F_mass", 0.0):
            return float(steam_in.F_mass) * 24.0
        return self.steam_to_feed_kg_m3 * Q_feed_m3_d

    def solve_profile(
        self,
        *,
        state: np.ndarray,
        Q_feed_m3_d: float,
        T_feed_K: float,
        steam_kg_d: float,
        cmps,
    ) -> StripperResult:
        """Solve the axial profile. Separated from ``_run`` so it is callable directly
        for screening, sensitivity and calibration without building a flowsheet."""
        T_col = self.T_column_K
        model = self._speciation(T_col)
        params = model.rate_function._params
        Ka = params["Ka"]
        unit_conv = params["cmps"].i_mass / params["cmps"].chem_MW
        H_cc = np.asarray(model.H_cc, dtype=float)
        i_nh3_gas = model.gas_IDs.index("S_NH4")
        h_cc_nh3 = float(H_cc[i_nh3_gas])
        # CO2 comes from Carroll (273-433 K), NOT from the fork's van 't Hoff
        # extrapolation of a 25 C constant -- which is 1.53x too volatile at 100 C and
        # 1.89x at 120 C. The two agree to 1% at 25 C, which is what says the
        # implementation is right and the extrapolation is what was wrong.
        h_cc_co2 = henry_cc_co2(T_col)

        i_nh4 = cmps.index("S_NH4")
        i_ic = cmps.index("S_IC")
        i_na = cmps.index("S_Na")

        caveats: List[str] = []
        if T_col > _CORRELATION_VALID_MAX_K:
            caveats.append(
                f"extrapolated_above_50C: column at {T_col - 273.15:.1f} C. BOUNDED, not "
                "unknown -- see the module docstring. CO2 Henry is Carroll (273-433 K), "
                "so interpolated. The acid-base constants are stated 0-50 C but the "
                "family extrapolates well: Harned & Owen reproduces pKw at 100 C to "
                "0.004 units, and Emerson's NH4+ slope implies dH = 52.26 kJ/mol, the "
                "accepted standard value, so its residual error is neglected dCp rather "
                "than a bad fit. NH3 H_cc cross-checks to ~1.6% against "
                "Clegg-Brimblecombe. STILL UNQUANTIFIED: NH3-CO2-H2O ternary "
                "non-ideality and carbamate. Olofsson (1975) for NH4+ over a wide range, "
                "and GPA RR-48/RR-52/RR-65 for the ternary data, would close what remains."
            )

        # --- water and energy balance, first, because it sets the vapour flow ---------
        # Density AT COLUMN TEMPERATURE, not 1000 kg/m3. The condensate volume is what
        # dilutes the bottoms, and at 100 C water is 958 kg/m3 -- taking 1000 overstates
        # that volume by 4% and, worse, leaves the reported bottoms flow disagreeing with
        # the volumetric flow QSDsan computes for the same mass at the same temperature.
        rho = water_density_kg_m3(T_col)
        cp = 4.18
        h_fg = _h_fg_kJ_kg(T_col)
        duty_kJ_d = Q_feed_m3_d * rho * cp * max(T_col - T_feed_K, 0.0)
        duty_kJ_d /= max(1.0 - self.heat_loss_fraction, 1e-6)
        condensed = duty_kJ_d / h_fg
        if condensed >= steam_kg_d:
            raise StripperConfigurationError(
                f"{self.ID or 'stripper'}: {steam_kg_d:,.0f} kg/d of steam is entirely "
                f"consumed heating the feed from {T_feed_K - 273.15:.1f} C to "
                f"{T_col - 273.15:.1f} C ({condensed:,.0f} kg/d condensed), leaving "
                "nothing to strip with. Preheat the feed against the bottoms, or raise "
                "the steam rate. Bryan Research's industrial range is 60-300 kg steam "
                "per m3 of feed."
            )
        steam_vapour_kg_d = steam_kg_d - condensed
        Q_bottoms = Q_feed_m3_d + condensed / rho

        # Vapour volumetric flow at column conditions, ideal gas on water.
        # kg/d / (g/mol) = kmol/d; x R[L.atm/mol.K] x T / P = 1000 L/d = m3/d.
        V_m3_d = steam_vapour_kg_d / 18.015 * _R_L_ATM * T_col / self.P_atm

        # Condensate is added to the feed at the TOP, so the liquid rate is constant down
        # the column at Q_bottoms. That is both the physical picture for direct steam
        # (the feed is heated by contact, and the water that heats it stays with it) and
        # the choice that makes the envelope balance conserve mass EXACTLY rather than to
        # solver tolerance -- a mean-flow approximation leaves a residual of order
        # condensate/feed, which here is 10-20%.
        L_m3_d = Q_bottoms
        dilution = Q_feed_m3_d / Q_bottoms
        feed_conc = np.asarray(state, dtype=float) * dilution

        # --- the cascade ---------------------------------------------------------------
        n = self.n_stages
        E = self.E_murphree
        x0_tan = float(feed_conc[i_nh4])
        x0_tic = float(feed_conc[i_ic])

        stage_state = feed_conc.copy()
        if self.naoh_dose_mol_m3 > 0:
            # Caustic frees NH4+ as NH3 by raising pH, at the cost of Na+ that has no
            # removal path and accumulates through the recycle at dose/(1-R).
            stage_state[i_na] += self.naoh_dose_mol_m3 * 22.99 * dilution

        ratio = V_m3_d / L_m3_d
        solve = _solve_simultaneous(
            n=n, ratio=ratio, E_nh3=E, E_co2=self.E_murphree_co2,
            h_cc_nh3=h_cc_nh3, h_cc_co2=h_cc_co2,
            x0_tan=x0_tan, x0_tic=x0_tic,
            base_state=stage_state, Ka=Ka, unit_conv=unit_conv,
            warm=self._warm_start, tol=self.tol, max_iter=self.max_iter,
        )
        if not solve["converged"]:
            self._warm_start = None
            raise StripperPhysicsError(
                f"{self.ID or 'stripper'}: the {3 * n}-unknown column solve did not "
                f"converge (max residual {solve['residual']:.3e}): {solve['message']}. "
                "Reporting an unconverged profile would hand over a plausible number "
                "with nothing behind it."
            )

        x_tan, x_tic = solve["x_tan"], solve["x_tic"]
        y_nh3, y_co2 = solve["y_nh3"], solve["y_co2"]
        pH, f_nh3, f_co2 = solve["pH"], solve["f_nh3"], solve["f_co2"]
        K_nh3, K_co2 = solve["K_nh3"], solve["K_co2"]
        iterations = solve["nfev"]
        converged = True
        self._warm_start = solve["z"]

        self._assert_equilibrium_ceiling(y_nh3, K_nh3, x_tan, "NH3")
        self._assert_equilibrium_ceiling(y_co2, K_co2, x_tic, "CO2")

        bottoms_tan = float(x_tan[-1])
        nh3_stripped_kg_N_d = V_m3_d * float(y_nh3[0]) / 1e3
        closure = _closure_ledger(
            feed_load=Q_feed_m3_d * float(state[i_nh4]) / 1e3,
            bottoms_load=Q_bottoms * bottoms_tan / 1e3,
            vapour_load=nh3_stripped_kg_N_d,
        )
        if abs(closure["relative_error"]) > 1e-6:
            raise StripperPhysicsError(
                f'{self.ID or 'stripper'}: nitrogen does not close ({closure['relative_error']:.2e} Physical consistency failed; check the model inputs and diagnostics.'
            )

        stages = [
            StageState(
                index=j + 1, T_K=T_col, pH=float(pH[j]),
                f_nh3=float(f_nh3[j]), f_co2=float(f_co2[j]),
                x_tan_g_m3=float(x_tan[j]), x_tic_g_m3=float(x_tic[j]),
                y_nh3_g_m3=float(y_nh3[j]), y_co2_g_m3=float(y_co2[j]),
                K_nh3=float(K_nh3[j]), K_co2=float(K_co2[j]),
            )
            for j in range(n)
        ]

        bottom_state = stage_state.copy()
        bottom_state[i_nh4] = bottoms_tan
        bottom_state[i_ic] = float(x_tic[-1])
        si, si_degraded = self._saturation_indices(
            bottom_state, cmps, float(pH[-1]), T_col, unit_conv
        )
        # Second screen at a recuperator-outlet temperature: the Ksp fits are all
        # in-range there, and cooled exchanger surfaces are where scale is actually
        # managed. Same composition and pH as the column bottom -- a stated
        # approximation (pH re-speciation with temperature is not re-solved).
        si_recup, si_recup_degraded = self._saturation_indices(
            bottom_state, cmps, float(pH[-1]), _RECUPERATOR_SCREEN_T_K, unit_conv
        )
        screen_degradations = [
            f"{label}: {reason}"
            for label, reason in (
                ("column-bottom screen", si_degraded),
                ("recuperator-outlet screen", si_recup_degraded),
            )
            if reason
        ]
        for note in screen_degradations:
            caveats.append(
                "scaling_screen_degraded: " + note + " -- no saturation indices for "
                "this screen; the absence of a listed phase is NOT evidence of no "
                "scaling risk."
            )
        scaling = sorted(
            (m for m, v in si.items() if v > 0.0), key=lambda m: -si[m]
        )
        if scaling:
            # Struvite-family solids are thermally unstable in hot service:
            # stepwise NH3/water loss from ~40-60 C (Bhuiyan et al. 2008, Chemosphere,
            # doi 10.1016/j.chemosphere.2007.09.056; steps near 40/58/83 C), so a
            # hot-column SI against them describes a phase that does not persist.
            unstable_hot = {"struv", "kstruv", "newb"}
            def _tag(m):
                label = f"{m} ({si[m]:+.0f})"
                if m in unstable_hot and T_col > 333.15:
                    label += " [solid unstable >~60 C]"
                return label
            caveats.append(
                "supersaturated at the column bottom (log10 SI): "
                + ", ".join(_tag(m) for m in scaling[:4])
                + ". Validity by phase: calcite and aragonite carry the Plummer & "
                "Busenberg (1982) analytic fit, published 0-90 C, so at the boil they "
                "are a ~10 K extrapolation of a fitted curve. Every other phase is a "
                "25 C literature anchor extended by van't Hoff, and phases without a "
                "defensible dH are held at 25 C (see "
                "models.thermodynamics.MINERAL_PHASES). At column temperature "
                "direction is robust, magnitude indicative. Digestate at "
                "elevated pH and near-boiling is a precipitation problem before it "
                "is a mass-transfer problem, and the pilot record already flags "
                "hardness, alkalinity, foaming and scaling."
            )
        recup_scaling = sorted(
            (m for m, v in si_recup.items() if v > 0.0), key=lambda m: -si_recup[m]
        )
        if recup_scaling:
            caveats.append(
                f"recuperator-outlet screen ({_RECUPERATOR_SCREEN_T_K - 273.15:.0f} C, "
                "all Ksp fits in-range; composition and pH held at column-bottom "
                "values as a stated approximation): "
                + ", ".join(f"{m} ({si_recup[m]:+.0f})" for m in recup_scaling[:4])
                + ". Cooled exchanger surfaces are where the scale is managed."
            )
        if self.E_murphree >= 1.0:
            caveats.append(
                "E_murphree=1.0 -- equilibrium stages. This is a THERMODYNAMIC CEILING, "
                "not a performance prediction: it answers whether a claim is possible, "
                "not whether it will be met. Note N is then THEORETICAL stages; with "
                "E < 1, N is actual trays. The two are not interchangeable."
            )
        if bottoms_tan < _REPORTING_FLOOR_mgN_L:
            caveats.append(
                f"below_reporting_floor: computed bottoms {bottoms_tan:.3g} mg N/L is "
                f"under {_REPORTING_FLOOR_mgN_L} mg N/L. Report it as "
                f"'< {_REPORTING_FLOOR_mgN_L} mg N/L', never as a figure. It is below "
                "routine analytical resolution AND below what this model's uncertainty "
                "can distinguish -- activity coefficients, carbamate, tray hydraulics "
                "and the 0-50 C acid-base extrapolation all bite harder than this. "
                "Industrial context (sour-water stripping practice): modern designs "
                "deliver < 10 ppmw free NH3 in the stripped bottoms, typical operation "
                "runs 10-20 mg/L, and > 100 mg/L signals fouling or inadequate "
                "stripping (Weiland & Hatcher, Sour Water Strippers Exposed; Digital "
                "Refining, Sour Water Stripping Part 3). Quote the demonstrated floor "
                "band, not the model's equilibrium zero."
            )

        return StripperResult(
            converged=converged,
            iterations=iterations,
            grade="bracketing",
            nh3_removal_fraction=1.0 - (Q_bottoms * bottoms_tan)
            / max(Q_feed_m3_d * float(state[i_nh4]), 1e-12),
            tic_removal_fraction=1.0 - (Q_bottoms * float(x_tic[-1]))
            / max(Q_feed_m3_d * float(state[i_ic]), 1e-12),
            feed_tan_g_m3=float(state[i_nh4]),
            bottoms_tan_g_m3=bottoms_tan,
            feed_flow_m3_d=Q_feed_m3_d,
            bottoms_flow_m3_d=Q_bottoms,
            steam_kg_d=steam_kg_d,
            steam_condensed_kg_d=condensed,
            steam_to_feed_kg_m3=steam_kg_d / Q_feed_m3_d,
            vapour_flow_m3_d=V_m3_d,
            column_T_K=T_col,
            column_P_atm=self.P_atm,
            n_stages=n,
            E_murphree=E,
            E_murphree_co2=self.E_murphree_co2,
            nh3_product_kg_N_d=nh3_stripped_kg_N_d,
            naoh_dose_mol_m3=self.naoh_dose_mol_m3,
            stages=stages,
            saturation_indices=si,
            saturation_indices_recuperator=si_recup,
            scaling_screen_status="degraded" if screen_degradations else "ok",
            caveats=caveats,
            closure=closure,
        )

    # ------------------------------------------------------------------------ guards

    def _assert_equilibrium_ceiling(self, y, K, x, species: str) -> None:
        'The overhead cannot be richer than equilibrium with the richest liquid it met.\n\n        The earlier version of this check compared each stage against\n        ``max(K_j x_j, y_{j+1})``, and that is very nearly a **tautology**: the stage\n        relation ``y_j = (1-E) y_{j+1} + E K_j x_j`` with ``0 <= E <= 1`` is a convex\n        combination of exactly those two quantities, so it cannot violate it by\n        construction. A guard that cannot fail is not a guard. (Caught on review.)\n\n        The bound with teeth is the GLOBAL one::\n\n            y_1 <= max_j (K_j x_j)'
        eq = K * x
        ceiling = float(np.max(eq)) if eq.size else 0.0
        overhead = float(y[0])
        if overhead > ceiling * (1.0 + 1e-9) + 1e-12:
            j = int(np.argmax(eq))
            raise StripperPhysicsError(
                f'{self.ID or 'stripper'}: {species} overhead is {overhead:.6g} g/m3 against a global equilibrium ceiling of {ceiling:.6g} g/m3, set by stage {j + 1} (K={K[j]:.4g}, x={x[j]:.6g} Physical consistency failed; check the model inputs and diagnostics.'
            )

    @staticmethod
    def _saturation_indices(state, cmps, pH, T_K, unit_conv) -> Tuple[Dict[str, float], Optional[str]]:
        """Scaling screen at the hot, high-pH bottom, reusing the fork's own Ksp/IAP
        suite so the stripper and the digester agree about what precipitates.

        The fork's ``calc_saturation_indices`` returns the RAW ``IAP/Ksp`` ratio (its
        documented convention), and its contract is a **kg/m3 state** (ADM1-native,
        as mADM1 passes it). The stripper works in g/m3 (mASM2d-native), so the state
        is converted here -- passing g/m3 straight in feeds the Davies activity model
        concentrations 1000x too high, ionic strength lands at ~59 M, and the
        activity coefficients for multivalent ions explode to 1e70+, which is exactly
        the astronomic pseudo-SI this method used to emit. Reported as
        ``log10(IAP/Ksp)``; non-finite or non-positive ratios are dropped.

        Returns ``(si, degraded_reason)``. ``degraded_reason`` is ``None`` when the
        screen ran, and a structured reason when an EXPECTED limitation kept it from
        running: the ``ValueError`` family that the thermodynamics module raises by
        contract (constant/domain validity, including its subclasses
        ``InvalidThermodynamicConstantError`` and ``ActivityModelOutOfDomainError``),
        or the module being unimportable. The caller records that reason on the
        result; it is never reduced to a bare ``{}``. Any OTHER exception is a defect
        and propagates -- the previous blanket ``except Exception`` was fail-open: the
        old 0-50 C Debye-Huckel A(T) raise fired at every steam-column bottom
        temperature and every steam-stripper case silently lost its scaling screen.
        """
        try:
            from qsdsan_engine_mcp.models.thermodynamics import calc_saturation_indices
        except ImportError as exc:  # pragma: no cover - env without the thermo module
            logger.warning("saturation-index screen unavailable: %s", exc)
            return {}, f"thermodynamics module unavailable ({exc})"

        state_kg_m3 = np.asarray(state, dtype=float) * 1e-3
        try:
            ratios = calc_saturation_indices(state_kg_m3, cmps, pH, T_K, unit_conv)
        except ValueError as exc:
            logger.warning(
                "saturation-index screen degraded at T=%.2f K: %s", float(T_K), exc
            )
            return {}, f"thermodynamic validity at T={float(T_K):.2f} K: {exc}"
        out: Dict[str, float] = {}
        for k, v in ratios.items():
            v = float(v)
            if v > 0.0 and math.isfinite(v):
                out[k] = math.log10(v)
        return out, None


# ------------------------------------------------------------------------- internals


def _solve_simultaneous(
    *, n, ratio, E_nh3, E_co2, h_cc_nh3, h_cc_co2, x0_tan, x0_tic,
    base_state, Ka, unit_conv, warm, tol, max_iter,
):
    """Solve the column as ONE bounded 3N system, not as a lagged fixed point.

    The first version of this module lagged the speciation: solve the cascade at fixed
    stripping factors, re-speciate, repeat, damped. That converged on the design case and
    **diverged on others** -- 5e19 g/m3 with arithmetic damping, and two operating points
    that never settled at all under any damping tried. It looked like physical
    bistability. It was not. Twelve widely separated starts (normalised concentrations
    0.01-3, pH 7-11) all reach the same root on both of the cases that used to fail, so
    the equations have a unique solution there and the all-or-nothing flip-flopping was
    plain Picard oscillation around a stiff pH front.

    Unknowns per stage, 3N total:

    * ``z_N = x_TAN / x_TAN,feed``  -- normalised, NOT logged. Log variables look
      attractive when the bottoms approach zero but they are the worse choice: they
      become ill-conditioned in exactly that limit.
    * ``z_C = x_TIC / x_TIC,feed``
    * ``pH``  -- not h. The residual is far better scaled in pH.

    Vapour is eliminated analytically. Combining the stage relation
    ``y_j = (1-E) y_{j+1} + E K_j x_j`` with the envelope balance
    ``x_j = x_0 + r (y_{j+1} - y_1)`` gives a two-term recurrence with one global
    coupling to the bottoms::

        x_{j-1} - [(1-E) + r E K_j(pH_j)] x_j - E x_N = 0

    plus one charge-balance residual per stage. That is 2N + N equations in 3N unknowns,
    sparse, with an analytic Jacobian -- the only awkward term being d(charge)/d(pH),
    taken by complex step (exact to machine precision, no nested root solve, no
    finite-difference step to tune).

    Credit where due: this formulation came out of a Codex review of the Picard version.
    """
    from scipy.optimize import least_squares, root
    from scipy.sparse import csr_matrix
    from qsdsan.process_models._asm2d import acid_base_rxn

    from qsdsan_engine_mcp.models.masm2d import _S_A, _S_Ca, _S_Cl, _S_IC, _S_K, _S_Mg, _S_Na, _S_NH4, _S_NO2, _S_NO3, _S_PO4

    m = base_state[:32] * unit_conv[:32] * 1e-3          # mol/L
    ions_base = np.array([
        m[_S_K], m[_S_Mg], m[_S_Ca], m[_S_Na], m[_S_Cl],
        m[_S_NO3] + m[_S_NO2], m[_S_NH4], m[_S_IC], m[_S_PO4], m[_S_A],
    ], dtype=float)
    m0_n = x0_tan * unit_conv[_S_NH4] * 1e-3
    m0_c = x0_tic * unit_conv[_S_IC] * 1e-3

    # Scale the charge residual to the ionic strength of this water, so a pH equation is
    # numerically commensurate with a dimensionless stage balance rather than swamping it.
    charge_scale = max(
        abs(ions_base[0] + 2 * ions_base[1] + 2 * ions_base[2] + ions_base[3]), 1e-3
    )

    Kw, Knh, Kc1, Kc2 = Ka[0], Ka[1], Ka[2], Ka[3]

    def unpack(z):
        zn, zc, ph = z[:n], z[n:2 * n], z[2 * n:]
        h = 10.0 ** (-ph)
        f_n = Knh / (Knh + h)
        den = h * h + Kc1 * h + Kc1 * Kc2
        f_c = h * h / den
        return zn, zc, ph, h, f_n, f_c, den

    def residual(z):
        zn, zc, ph, h, f_n, f_c, _ = unpack(z)
        out = np.empty(3 * n)
        for off, zz, KK, E in ((0, zn, h_cc_nh3 * f_n, E_nh3),
                               (n, zc, h_cc_co2 * f_c, E_co2)):
            prev = np.r_[1.0, zz[:-1]]
            out[off:off + n] = prev - ((1.0 - E) + ratio * E * KK) * zz - E * zz[-1]
        for j in range(n):
            ions = ions_base.copy()
            ions[6] = m0_n * zn[j]
            ions[7] = m0_c * zc[j]
            out[2 * n + j] = acid_base_rxn(h[j], ions, Ka) / charge_scale
        return out

    def jac_dense(z):
        zn, zc, ph, h, f_n, f_c, den = unpack(z)
        J = np.zeros((3 * n, 3 * n))
        ln10 = np.log(10.0)
        df_n = ln10 * h * Knh / (Knh + h) ** 2
        df_c = -ln10 * h * (Kc1 * h * (h + 2.0 * Kc2) / den ** 2)
        for off, zz, HH, ff, dff, E in ((0, zn, h_cc_nh3, f_n, df_n, E_nh3),
                                        (n, zc, h_cc_co2, f_c, df_c, E_co2)):
            KK = HH * ff
            for j in range(n):
                row = off + j
                if j:
                    J[row, off + j - 1] += 1.0
                J[row, off + j] -= (1.0 - E) + ratio * E * KK[j]
                J[row, off + n - 1] -= E
                J[row, 2 * n + j] -= ratio * E * HH * dff[j] * zz[j]
        for j in range(n):
            row = 2 * n + j
            a_nh4 = h[j] / (Knh + h[j])
            a = np.array([h[j] ** 2, Kc1 * h[j], Kc1 * Kc2], dtype=float)
            a /= a.sum()
            J[row, j] = a_nh4 * m0_n / charge_scale
            J[row, n + j] = -(a[1] + 2.0 * a[2]) * m0_c / charge_scale
            # Complex step on the scalar charge polynomial: exact, and no step size to
            # pick. acid_base_rxn is a rational function of h, so it is analytic.
            ions = ions_base.astype(complex)
            ions[6] = m0_n * zn[j]
            ions[7] = m0_c * zc[j]
            eps = 1e-20
            q = acid_base_rxn(10.0 ** (-(ph[j] + 1j * eps)), ions, Ka)
            J[row, 2 * n + j] = q.imag / eps / charge_scale
        return J

    if warm is not None and warm.size == 3 * n:
        z0 = warm.copy()
    else:
        ph_feed = -np.log10(_solve_h(base_state, Ka, unit_conv))
        z0 = np.r_[np.ones(n), np.ones(n), np.full(n, ph_feed)]

    lo = np.r_[np.zeros(2 * n), np.full(n, 1.0)]
    hi = np.r_[np.full(2 * n, 10.0), np.full(n, 14.0)]
    fit = least_squares(
        residual, np.clip(z0, lo + 1e-12, hi - 1e-12),
        jac=lambda z: csr_matrix(jac_dense(z)), bounds=(lo, hi),
        method="trf", xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=max_iter,
    )

    # Bounded trust-region gets into the basin; a square Newton solve finishes the job.
    # least_squares stops on a small STEP or a small GRADIENT, which on this problem
    # leaves a residual around 1e-7 -- fine for the answer, not fine for the conservation
    # guarantees downstream, which are only meaningful if the equations are actually
    # satisfied. The polish takes it to ~1e-15.
    z, nfev = fit.x, int(fit.nfev)
    resid = float(np.max(np.abs(residual(z))))
    if resid > _RESIDUAL_TOL:
        try:
            polished = root(residual, z, jac=jac_dense, method="hybr",
                            tol=_RESIDUAL_TOL * 1e-3)
            cand = np.clip(polished.x, lo, hi)
            cand_resid = float(np.max(np.abs(residual(cand))))
            if np.all(np.isfinite(cand)) and cand_resid < resid:
                z, resid = cand, cand_resid
                nfev += int(getattr(polished, "nfev", 0))
        except Exception as exc:  # pragma: no cover - polish is best-effort
            logger.debug("Newton polish failed, keeping the trust-region result: %s", exc)

    # Convergence is decided by the RESIDUAL, not by the optimiser's stopping flag.
    # `least_squares` reports failure when it exhausts max_nfev even if the Newton polish
    # then lands the residual at 1e-15, and it reports success on a small step even when
    # the residual is 1e-7. Only one of those two numbers says whether the column
    # equations are actually satisfied.
    zn, zc, ph, h, f_n, f_c, _ = unpack(z)
    x_tan = zn * x0_tan
    x_tic = zc * x0_tic
    K_nh3 = h_cc_nh3 * f_n
    K_co2 = h_cc_co2 * f_c

    # Recover vapour from the CONVERGED liquid by the envelope balance,
    # x_{j-1} = r y_j + x_N, so conservation is exact by construction rather than a
    # separate sweep that could disagree with it.
    y_nh3 = np.zeros(n + 1)
    y_co2 = np.zeros(n + 1)
    y_nh3[:n] = (np.r_[x0_tan, x_tan[:-1]] - x_tan[-1]) / ratio
    y_co2[:n] = (np.r_[x0_tic, x_tic[:-1]] - x_tic[-1]) / ratio

    return {
        "converged": resid < _RESIDUAL_TOL,
        "message": fit.message, "residual": resid, "nfev": nfev,
        "z": z, "x_tan": x_tan, "x_tic": x_tic, "y_nh3": y_nh3, "y_co2": y_co2,
        "pH": ph, "f_nh3": f_n, "f_co2": f_c, "K_nh3": K_nh3, "K_co2": K_co2,
    }


def _solve_cascade(x0: float, K: np.ndarray, ratio: float, E: float):
    """Exact counter-current cascade for a FIXED stripping-factor profile.

    Stages are numbered 1 (top, where liquid enters) to N (bottom, where steam enters);
    arrays are 0-based over the same order. Two relations close the column:

    * **Stage transfer**, ``y_j = (1-E) y_{j+1} + E K_j x_j``. A convex combination, so
      0 <= E <= 1 keeps every stage between "no contact" and "equilibrium" -- and a stage
      whose incoming vapour is already richer than equilibrium ABSORBS rather than
      strips. That is the re-absorption Weiland & Hatcher report in sour-water service
      (apparent Murphree efficiencies from -100% to +200%); it emerges here instead of
      being fitted away.
    * **Envelope balance** over the top j stages, ``L x_j = L x_0 + V (y_{j+1} - y_1)``,
      with ``y_{N+1} = 0`` for fresh steam. Written this way rather than stage-local,
      mass conservation is structural: the bottom row IS the overall balance, so
      ``x_N = x_0 - (V/L) y_1`` holds to machine precision.

    Unrolling the transfer relation from the bottom gives
    ``y_j = E sum_{m>=j} (1-E)^(m-j) K_m x_m``, which is linear in x; substituting it into
    the envelope balance leaves one N x N solve. Returns ``(x, y)`` with ``y`` of length
    N+1 (index N is the steam inlet, zero).
    """
    n = K.size
    m_idx = np.arange(n)
    # G[j, m] = E (1-E)^(m-j) K_m for m >= j
    decay = np.where(
        m_idx[None, :] >= m_idx[:, None],
        (1.0 - E) ** np.maximum(m_idx[None, :] - m_idx[:, None], 0),
        0.0,
    )
    G = E * decay * K[None, :]

    G_shift = np.zeros_like(G)
    G_shift[:-1, :] = G[1:, :]          # y_{j+1}; the bottom stage sees fresh steam
    M = np.eye(n) - ratio * G_shift + ratio * np.repeat(G[0:1, :], n, axis=0)

    x = np.linalg.solve(M, np.full(n, float(x0)))
    y = np.zeros(n + 1)
    y[:n] = G @ x
    return x, y


def _rel_change(new: np.ndarray, old: np.ndarray, scale: float) -> float:
    """Largest per-stage change, relative but floored at ``scale``.

    Relative, because the bottoms concentration can be four orders of magnitude below
    the feed and an absolute tolerance tight enough for one is absurd for the other.
    Floored, because a purely relative measure against a near-zero bottoms value chases
    numerical noise forever -- at 300 kg steam/m3 the residual reaches 1e-12 g/m3 and a
    bare relative test never terminates on a quantity nobody can measure. ``scale`` is
    the feed concentration times 1e-6: far below anything reportable, far above noise.
    """
    denom = np.maximum(np.abs(new), max(scale, _FLOOR))
    return float(np.max(np.abs(new - old) / denom))


def _solve_h(state_arr, Ka, unit_conversion) -> float:
    from qsdsan_engine_mcp.models.masm2d import solve_pH_2n

    return float(solve_pH_2n(np.asarray(state_arr, dtype=float), Ka, unit_conversion))


def _co2_fraction(h: float, Kc1: float, Kc2: float) -> float:
    """Dissolved-CO2 fraction of total inorganic carbon at hydrogen activity ``h``."""
    from qsdsan.process_models import ion_speciation

    return float(ion_speciation(h, Kc1, Kc2)[0])


def _closure_ledger(*, feed_load: float, bottoms_load: float, vapour_load: float) -> Dict[str, float]:
    out = feed_load - bottoms_load - vapour_load
    return {
        "feed_kg_N_d": float(feed_load),
        "bottoms_kg_N_d": float(bottoms_load),
        "vapour_kg_N_d": float(vapour_load),
        "residual_kg_N_d": float(out),
        "relative_error": float(out / feed_load) if feed_load else 0.0,
    }


def _set_stream_by_conc(stream, cmps, conc_g_m3, Q_m3_d: float) -> None:
    ids = [i for i in cmps.IDs if i != "H2O"]
    idx = [cmps.index(i) for i in ids]
    stream.set_flow_by_concentration(
        Q_m3_d,
        {i: float(max(conc_g_m3[k], 0.0)) for i, k in zip(ids, idx)},
        units=("m3/d", "mg/L"),
    )
