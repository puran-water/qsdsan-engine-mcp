"""
Productized public unit wrappers for the MCP/CLI flowsheet surface.

These wrappers trade exact upstream constructor semantics for a stable public
surface that can be dynamically constructed, compiled, and solved by agents.
They stay within the active stream thermo instead of requiring side shims,
hidden side streams, or brittle source-chain assumptions.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()

from qsdsan import SanUnit
from qsdsan.unit_operations import DynamicInfluent as _UpstreamDynamicInfluent


_GAS_COMPONENT_IDS = {
    "s_ch4",
    "s_co2",
    "s_h2",
    "s_h2s",
    "s_o2",
    "s_n2",
    "co2",
    "ch4",
    "h2",
    "h2s",
    "o2",
    "n2",
    "nh3",
}
_SOLID_COMPONENT_IDS = {
    "otherss",
    "wwtsludge",
    "struvite",
    "ash",
    "membrane",
}
_SOLUBLE_ORGANIC_IDS = {
    "s_f",
    "s_a",
    "s_su",
    "s_aa",
    "s_fa",
    "s_va",
    "s_bu",
    "s_pro",
    "s_ac",
}
_PARTICULATE_ORGANIC_IDS = {
    "x_s",
    "x_ch",
    "x_pr",
    "x_li",
}
_BIOMASS_RECEIVER_IDS = (
    "X_H",
    "X_PAO",
    "X_AUT",
    "X_I",
    "X_ac",
    "X_h2",
)


def _lower(component_id: str) -> str:
    return component_id.lower()


def _is_water(component_id: str) -> bool:
    return component_id == "H2O"


def _is_solid(component_id: str) -> bool:
    comp_lower = _lower(component_id)
    return component_id.startswith("X_") or comp_lower in _SOLID_COMPONENT_IDS


def _is_gas(component_id: str) -> bool:
    return _lower(component_id) in _GAS_COMPONENT_IDS


def _is_soluble_organic(component_id: str) -> bool:
    return _lower(component_id) in _SOLUBLE_ORGANIC_IDS


def _is_particulate_organic(component_id: str) -> bool:
    return _lower(component_id) in _PARTICULATE_ORGANIC_IDS


def _valid_inputs(unit: SanUnit) -> List[Any]:
    return [stream for stream in unit.ins if stream is not None]


def _prepare_output(out, template, phase: Optional[str] = None) -> None:
    out.copy_like(template)
    out.empty()
    out.T = template.T
    out.P = template.P
    if phase:
        out.phase = phase


def _get_receiver_id(component_ids: Sequence[str], preferred: Sequence[str] = _BIOMASS_RECEIVER_IDS) -> Optional[str]:
    component_set = set(component_ids)
    for comp_id in preferred:
        if comp_id in component_set:
            return comp_id
    return None


def _available_gas_ids(component_ids: Sequence[str], requested: Optional[Iterable[str]] = None) -> List[str]:
    candidates = list(requested or ("S_ch4", "S_co2", "S_h2", "S_h2s", "S_N2", "S_O2"))
    available = [comp_id for comp_id in candidates if comp_id in component_ids]
    if available:
        return available
    return [comp_id for comp_id in component_ids if _is_gas(comp_id)]


def _allocate_biogas(gas_out, total_mass: float, gas_ids: Sequence[str]) -> None:
    if total_mass <= 0 or not gas_ids:
        return

    canonical = {
        "s_ch4": 0.65,
        "s_co2": 0.30,
        "s_h2": 0.03,
        "s_h2s": 0.02,
    }
    raw_weights = {}
    for gas_id in gas_ids:
        raw_weights[gas_id] = canonical.get(_lower(gas_id), 0.0)
    if not any(raw_weights.values()):
        for gas_id in gas_ids:
            raw_weights[gas_id] = 1.0
    total_weight = sum(raw_weights.values()) or 1.0
    for gas_id, weight in raw_weights.items():
        gas_out.imass[gas_id] += total_mass * weight / total_weight


def _copy_passthrough(inf, out) -> None:
    out.copy_like(inf)


class _StaticStateMixin:
    """Minimal dynamic hooks so static wrappers can sit in dynamic systems."""

    @staticmethod
    def _stream_state_vector(stream) -> np.ndarray:
        n = len(stream.components.IDs) + 1
        state = np.zeros(n, dtype=float)
        Q_m3_d = float(stream.get_total_flow("m3/d")) if getattr(stream, "F_vol", 0.0) else 0.0
        state[-1] = Q_m3_d
        if Q_m3_d <= 0:
            return state

        if getattr(stream, "phase", "l") == "g":
            Q_m3_hr = float(stream.F_vol)
            if Q_m3_hr > 0:
                state[:-1] = np.asarray(stream.mass, dtype=float) / Q_m3_hr * 1e3
        else:
            state[:-1] = np.asarray(stream.conc, dtype=float)
        return state

    def _sync_input_states(self, QC_ins, dQC_ins) -> None:
        for idx, stream in enumerate(self.ins):
            if stream is None:
                continue
            if QC_ins is not None and idx < len(QC_ins):
                stream.state = np.asarray(QC_ins[idx], dtype=float)
            if dQC_ins is not None and idx < len(dQC_ins):
                stream.dstate = np.asarray(dQC_ins[idx], dtype=float)

    def _refresh_output_states(self) -> None:
        for out in self.outs:
            if out is None:
                continue
            state = self._stream_state_vector(out)
            dstate = np.zeros_like(state)
            if getattr(out, "_sink", None) is not None and hasattr(out._sink, "_ins_QC"):
                idx = out._sink._ins.index(out)
                out._sink._ins_QC[idx, :] = state
                out._sink._ins_dQC[idx, :] = dstate
            else:
                out._state = state
                out._dstate = dstate

    def _init_state(self):
        self._state = np.zeros(0, dtype=float)
        self._dstate = np.zeros(0, dtype=float)
        self._AE = None
        self._run()
        self._refresh_output_states()

    def _update_state(self):
        self._run()
        self._refresh_output_states()

    def _update_dstate(self):
        self._refresh_output_states()
        return None

    @property
    def AE(self):
        if getattr(self, "_AE", None) is None:
            self._compile_AE()
        return self._AE

    def _compile_AE(self):
        def yt(t, QC_ins, dQC_ins):
            self._sync_input_states(QC_ins, dQC_ins)
            self._run()
            self._refresh_output_states()

        self._AE = yt


class _SingleOutletTreatmentUnit(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 1

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        soluble_removal: float = 0.15,
        particulate_removal: float = 0.05,
        biomass_yield: float = 0.35,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.soluble_removal = soluble_removal
        self.particulate_removal = particulate_removal
        self.biomass_yield = biomass_yield
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        eff, = self.outs
        _copy_passthrough(inf, eff)
        receiver_id = _get_receiver_id(eff.components.IDs)
        if receiver_id is None:
            return

        retained_mass = 0.0
        for comp_id in eff.components.IDs:
            mass = float(eff.imass[comp_id])
            if mass <= 0:
                continue
            removal = 0.0
            if _is_soluble_organic(comp_id):
                removal = mass * self.soluble_removal
            elif _is_particulate_organic(comp_id):
                removal = mass * self.particulate_removal
            if removal <= 0:
                continue
            eff.imass[comp_id] = mass - removal
            retained_mass += removal * self.biomass_yield

        if retained_mass > 0:
            eff.imass[receiver_id] += retained_mass


class _EffluentBiogasUnit(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 2

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        soluble_removal: float = 0.35,
        particulate_removal: float = 0.10,
        gas_yield: float = 0.60,
        biomass_yield: float = 0.20,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.soluble_removal = soluble_removal
        self.particulate_removal = particulate_removal
        self.gas_yield = gas_yield
        self.biomass_yield = biomass_yield
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        eff, gas = self.outs
        eff.copy_like(inf)
        _prepare_output(gas, inf, phase="g")

        component_ids = eff.components.IDs
        receiver_id = _get_receiver_id(component_ids)
        removed_mass = 0.0
        retained_mass = 0.0

        for comp_id in component_ids:
            mass = float(eff.imass[comp_id])
            if mass <= 0:
                continue
            removal = 0.0
            if _is_soluble_organic(comp_id):
                removal = mass * self.soluble_removal
            elif _is_particulate_organic(comp_id):
                removal = mass * self.particulate_removal
            if removal <= 0:
                continue
            eff.imass[comp_id] = mass - removal
            removed_mass += removal
            retained_mass += removal * self.biomass_yield

        if receiver_id and retained_mass > 0:
            eff.imass[receiver_id] += retained_mass
        gas_mass = removed_mass * self.gas_yield
        _allocate_biogas(gas, gas_mass, _available_gas_ids(component_ids))


class _LiquidSolidsSplitter(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 2

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        solids_capture: float = 0.90,
        water_to_secondary: float = 0.02,
        soluble_to_secondary: float = 0.05,
        primary_first: str = "liquid",
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.solids_capture = solids_capture
        self.water_to_secondary = water_to_secondary
        self.soluble_to_secondary = soluble_to_secondary
        self.primary_first = primary_first
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        out0, out1 = self.outs
        if self.primary_first == "liquid":
            liquid, secondary = out0, out1
        else:
            secondary, liquid = out0, out1

        _prepare_output(liquid, inf)
        _prepare_output(secondary, inf)

        for comp_id in inf.components.IDs:
            mass = float(inf.imass[comp_id])
            if mass <= 0:
                continue
            if _is_water(comp_id):
                to_secondary = mass * self.water_to_secondary
            elif _is_solid(comp_id):
                to_secondary = mass * self.solids_capture
            else:
                to_secondary = mass * self.soluble_to_secondary
            liquid.imass[comp_id] = mass - to_secondary
            secondary.imass[comp_id] = to_secondary


class _ThermalSplitUnit(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 2

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        volatile_fraction: float = 0.75,
        water_to_gas: float = 0.98,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.volatile_fraction = volatile_fraction
        self.water_to_gas = water_to_gas
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        gas, ash = self.outs
        _prepare_output(gas, inf, phase="g")
        _prepare_output(ash, inf)

        component_ids = inf.components.IDs
        exhaust_gas_ids = _available_gas_ids(component_ids, requested=("S_co2", "S_N2", "S_O2", "S_ch4"))

        for comp_id in component_ids:
            mass = float(inf.imass[comp_id])
            if mass <= 0:
                continue
            if _is_water(comp_id):
                gas.imass[comp_id] = mass * self.water_to_gas
                ash.imass[comp_id] = mass - gas.imass[comp_id]
            elif _is_solid(comp_id):
                ash.imass[comp_id] = mass * (1.0 - min(self.volatile_fraction, 0.25))
                if exhaust_gas_ids:
                    _allocate_biogas(gas, mass - ash.imass[comp_id], exhaust_gas_ids)
                else:
                    ash.imass[comp_id] += mass - ash.imass[comp_id]
            else:
                if exhaust_gas_ids:
                    _allocate_biogas(gas, mass * self.volatile_fraction, exhaust_gas_ids)
                    ash.imass[comp_id] = mass * (1.0 - self.volatile_fraction)
                else:
                    ash.imass[comp_id] = mass


class _GasCombustionUnit(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 1

    def __init__(
        self,
        ID: str = "",
        ins=None,
        outs=(),
        thermo=None,
        init_with: str = "WasteStream",
        combustion_efficiency: float = 0.95,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.combustion_efficiency = combustion_efficiency
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        exhaust, = self.outs
        exhaust.copy_like(inf)
        exhaust.phase = "g"

        gas_ids = exhaust.components.IDs
        co2_id = "S_co2" if "S_co2" in gas_ids else ("S_CO2" if "S_CO2" in gas_ids else None)
        h2o_id = "H2O" if "H2O" in gas_ids else None
        combustible_ids = [
            gas_id for gas_id in gas_ids if _lower(gas_id) in {"s_ch4", "s_h2", "s_h2s", "ch4", "h2", "h2s"}
        ]

        released_mass = 0.0
        for gas_id in combustible_ids:
            mass = float(exhaust.imass[gas_id])
            if mass <= 0:
                continue
            burned = mass * self.combustion_efficiency
            exhaust.imass[gas_id] = mass - burned
            released_mass += burned

        if released_mass <= 0:
            return
        if co2_id:
            exhaust.imass[co2_id] += released_mass * 0.65
        if h2o_id:
            exhaust.imass[h2o_id] += released_mass * 0.35


class ProcessWaterCenter(_StaticStateMixin, SanUnit):
    _N_ins = 2
    _N_outs = 1

    def __init__(self, ID="", ins=None, outs=(), thermo=None, init_with="WasteStream", **kwargs):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inputs = _valid_inputs(self)
        outlet, = self.outs
        if not inputs:
            outlet.empty()
            return
        outlet.mix_from(inputs)


class Tank(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 1

    def __init__(self, ID="", ins=None, outs=(), thermo=None, init_with="WasteStream", **kwargs):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    @property
    def state(self):
        if self._state is None:
            return None
        return dict(zip(list(self.components.IDs) + ["Q"], self._state))

    def _init_state(self):
        self._state = self._ins_QC[0].copy()
        self._dstate = np.zeros_like(self._state)

    def _update_state(self):
        self._outs[0].state = self._state

    def _update_dstate(self):
        self._outs[0].dstate = self._dstate

    @property
    def AE(self):
        if getattr(self, "_AE", None) is None:
            self._compile_AE()
        return self._AE

    def _compile_AE(self):
        _state = self._state
        _dstate = self._dstate
        _update_state = self._update_state
        _update_dstate = self._update_dstate

        def yt(t, QC_ins, dQC_ins):
            _state[:] = QC_ins[0]
            _dstate[:] = dQC_ins[0]
            _update_state()
            _update_dstate()

        self._AE = yt

    def _run(self):
        out, = self.outs
        inputs = _valid_inputs(self)
        if not inputs:
            out.empty()
            return
        out.mix_from(inputs)


class Junction(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 1

    def __init__(self, ID="", ins=None, outs=(), thermo=None, init_with="WasteStream", **kwargs):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    @property
    def state(self):
        if self._state is None:
            return None
        return dict(zip(list(self.components.IDs) + ["Q"], self._state))

    def _init_state(self):
        self._state = self._ins_QC[0].copy()
        self._dstate = np.zeros_like(self._state)

    def _update_state(self):
        self._outs[0].state = self._state

    def _update_dstate(self):
        self._outs[0].dstate = self._dstate

    @property
    def AE(self):
        if getattr(self, "_AE", None) is None:
            self._compile_AE()
        return self._AE

    def _compile_AE(self):
        _state = self._state
        _dstate = self._dstate
        _update_state = self._update_state
        _update_dstate = self._update_dstate

        def yt(t, QC_ins, dQC_ins):
            _state[:] = QC_ins[0]
            _dstate[:] = dQC_ins[0]
            _update_state()
            _update_dstate()

        self._AE = yt

    def _run(self):
        out, = self.outs
        inputs = _valid_inputs(self)
        if not inputs:
            out.empty()
            return
        _copy_passthrough(inputs[0], out)


class DynamicInfluent(_UpstreamDynamicInfluent):
    """Productized wrapper around upstream DynamicInfluent using source_spec."""

    def __init__(
        self,
        ID="",
        ins=None,
        outs=(),
        source_spec: Optional[Dict[str, Any]] = None,
        thermo=None,
        init_with="WasteStream",
        **kwargs,
    ):
        if source_spec is None:
            raise ValueError("DynamicInfluent requires source_spec")
        self._source_spec = source_spec
        data_file, by_mass = self._write_source_data(source_spec, thermo)
        self._source_data_file = data_file
        super().__init__(
            ID=ID,
            ins=None,
            outs=outs,
            data_file=str(data_file),
            by_mass=by_mass,
            thermo=thermo,
            init_with=init_with,
            **kwargs,
        )

    @staticmethod
    def _write_source_data(source_spec: Dict[str, Any], thermo) -> tuple[Path, bool]:
        mode = source_spec.get("mode", "constant")
        by_mass = bool(source_spec.get("by_mass", False))
        if thermo is None:
            import qsdsan as qs

            thermo = qs.get_thermo()
        component_ids = list(getattr(thermo, "IDs", ()) or ())
        if not component_ids and hasattr(thermo, "chemicals"):
            component_ids = list(getattr(thermo.chemicals, "IDs", ()) or ())

        def _row_from_point(point: Dict[str, Any], fallback_flow: float = 0.0) -> Dict[str, Any]:
            row = {"t": float(point["t"])}
            flow = point.get("flow_m3_d", fallback_flow)
            row["Q"] = float(flow)
            concentrations = point.get("concentrations", {})
            for comp_id in component_ids:
                row[comp_id] = float(concentrations.get(comp_id, 0.0))
            return row

        rows: List[Dict[str, Any]] = []
        if mode == "constant":
            concentrations = source_spec.get("concentrations", {})
            flow_m3_d = float(source_spec.get("flow_m3_d", source_spec.get("Q", 0.0)))
            period_days = float(source_spec.get("period_days", 1.0))
            for t in (0.0, period_days):
                row = {"t": t, "Q": flow_m3_d}
                for comp_id in component_ids:
                    row[comp_id] = float(concentrations.get(comp_id, 0.0))
                rows.append(row)
        elif mode == "timeseries":
            points = source_spec.get("points") or []
            if len(points) < 2:
                raise ValueError("DynamicInfluent timeseries mode requires at least 2 points")
            for point in points:
                rows.append(_row_from_point(point))
        else:
            raise ValueError(f"Unsupported DynamicInfluent source_spec mode: {mode}")

        handle = NamedTemporaryFile("w", suffix=".tsv", delete=False)
        with handle as f:
            header = ["t", *component_ids, "Q"]
            f.write("\t".join(header) + "\n")
            for row in rows:
                values = [row.get("t", 0.0), *[row.get(comp_id, 0.0) for comp_id in component_ids], row.get("Q", 0.0)]
                f.write("\t".join(str(value) for value in values) + "\n")
        return Path(handle.name), by_mass


class AnaerobicBaffledReactor(_EffluentBiogasUnit):
    pass


class InternalCirculationRx(_EffluentBiogasUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.45)
        kwargs.setdefault("particulate_removal", 0.15)
        kwargs.setdefault("gas_yield", 0.65)
        super().__init__(*args, **kwargs)


class PFR(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        n_tanks = max(int(kwargs.get("N_tanks_in_series", 5)), 1)
        kwargs.setdefault("soluble_removal", min(0.10 + 0.04 * n_tanks, 0.45))
        kwargs.setdefault("particulate_removal", min(0.04 + 0.01 * n_tanks, 0.18))
        super().__init__(*args, **kwargs)


class Lagoon(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        design_type = str(kwargs.get("design_type", "anaerobic")).lower()
        if design_type == "aerobic":
            kwargs.setdefault("soluble_removal", 0.30)
            kwargs.setdefault("particulate_removal", 0.12)
        elif design_type == "facultative":
            kwargs.setdefault("soluble_removal", 0.22)
            kwargs.setdefault("particulate_removal", 0.10)
        else:
            kwargs.setdefault("soluble_removal", 0.18)
            kwargs.setdefault("particulate_removal", 0.08)
        super().__init__(*args, **kwargs)


class AnMBR(_LiquidSolidsSplitter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("solids_capture", 0.985)
        kwargs.setdefault("water_to_secondary", 0.03)
        kwargs.setdefault("soluble_to_secondary", 0.04)
        super().__init__(*args, **kwargs)


class Sedimentation(_LiquidSolidsSplitter):
    pass


class SludgeDigester(_EffluentBiogasUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.28)
        kwargs.setdefault("particulate_removal", 0.22)
        kwargs.setdefault("gas_yield", 0.50)
        super().__init__(*args, **kwargs)


class Incinerator(_ThermalSplitUnit):
    pass


class SepticTank(_LiquidSolidsSplitter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("solids_capture", 0.70)
        kwargs.setdefault("water_to_secondary", 0.04)
        kwargs.setdefault("soluble_to_secondary", 0.08)
        super().__init__(*args, **kwargs)


class PolishingFilter(_LiquidSolidsSplitter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("solids_capture", 0.60)
        kwargs.setdefault("water_to_secondary", 0.01)
        kwargs.setdefault("soluble_to_secondary", 0.03)
        super().__init__(*args, **kwargs)


class ActivatedSludgeProcess(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.42)
        kwargs.setdefault("particulate_removal", 0.18)
        kwargs.setdefault("biomass_yield", 0.45)
        super().__init__(*args, **kwargs)


class AnaerobicDigestion(_EffluentBiogasUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.40)
        kwargs.setdefault("particulate_removal", 0.18)
        kwargs.setdefault("gas_yield", 0.62)
        super().__init__(*args, **kwargs)


class SludgePasteurization(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.02)
        kwargs.setdefault("particulate_removal", 0.01)
        kwargs.setdefault("biomass_yield", 0.0)
        super().__init__(*args, **kwargs)


class DryingBed(_LiquidSolidsSplitter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("solids_capture", 0.96)
        kwargs.setdefault("water_to_secondary", 0.75)
        kwargs.setdefault("soluble_to_secondary", 0.12)
        kwargs.setdefault("primary_first", "secondary")
        super().__init__(*args, **kwargs)


class LiquidTreatmentBed(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.20)
        kwargs.setdefault("particulate_removal", 0.06)
        super().__init__(*args, **kwargs)


class AerobicDigester(_SingleOutletTreatmentUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soluble_removal", 0.28)
        kwargs.setdefault("particulate_removal", 0.16)
        kwargs.setdefault("biomass_yield", 0.18)
        super().__init__(*args, **kwargs)


class BiogasCombustion(_GasCombustionUnit):
    pass


class CombinedHeatPower(_GasCombustionUnit):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("combustion_efficiency", 0.98)
        super().__init__(*args, **kwargs)


class MembraneDistillation(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 2

    def __init__(
        self,
        ID="",
        ins=None,
        outs=(),
        thermo=None,
        init_with="WasteStream",
        aux_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.aux_config = aux_config or {}
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        permeate, concentrate = self.outs
        _prepare_output(permeate, inf)
        _prepare_output(concentrate, inf)

        water_recovery = float(self.aux_config.get("water_recovery", 0.75))
        ammonia_recovery = float(self.aux_config.get("ammonia_recovery", 0.55))
        volatile_ids = {
            "S_NH4",
            "S_N",
            "S_IN",
            "NH3",
        }

        for comp_id in inf.components.IDs:
            mass = float(inf.imass[comp_id])
            if mass <= 0:
                continue
            if _is_water(comp_id):
                to_permeate = mass * water_recovery
            elif comp_id in volatile_ids or _lower(comp_id) == "s_nh4":
                to_permeate = mass * ammonia_recovery
            else:
                to_permeate = mass * 0.01
            permeate.imass[comp_id] = to_permeate
            concentrate.imass[comp_id] = mass - to_permeate


class MembraneGasExtraction(_StaticStateMixin, SanUnit):
    _N_ins = 1
    _N_outs = 2

    def __init__(
        self,
        ID="",
        ins=None,
        outs=(),
        thermo=None,
        init_with="WasteStream",
        aux_config: Optional[Dict[str, Any]] = None,
        GasID: Optional[Sequence[str]] = None,
        **kwargs,
    ):
        SanUnit.__init__(self, ID, ins, outs, thermo, init_with=init_with, isdynamic=True)
        self.aux_config = aux_config or {}
        self.GasID = list(GasID or self.aux_config.get("gas_ids") or [])
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def _run(self):
        inf, = _valid_inputs(self)
        liquid, gas = self.outs
        liquid.copy_like(inf)
        _prepare_output(gas, inf, phase="g")

        extraction_fraction = float(self.aux_config.get("extraction_fraction", 0.65))
        gas_ids = _available_gas_ids(inf.components.IDs, self.GasID)
        for gas_id in gas_ids:
            mass = float(liquid.imass[gas_id])
            if mass <= 0:
                continue
            transferred = mass * extraction_fraction
            liquid.imass[gas_id] = mass - transferred
            gas.imass[gas_id] = transferred


__all__ = [
    "ActivatedSludgeProcess",
    "AerobicDigester",
    "AnaerobicBaffledReactor",
    "AnaerobicDigestion",
    "AnMBR",
    "BiogasCombustion",
    "CombinedHeatPower",
    "DryingBed",
    "DynamicInfluent",
    "Incinerator",
    "InternalCirculationRx",
    "Junction",
    "Lagoon",
    "LiquidTreatmentBed",
    "MembraneDistillation",
    "MembraneGasExtraction",
    "PFR",
    "PolishingFilter",
    "ProcessWaterCenter",
    "Sedimentation",
    "SepticTank",
    "SludgeDigester",
    "SludgePasteurization",
    "Tank",
]
