"""Sampled-data controls for QSDsan dynamic simulations.

This module intentionally stays independent of QSDsan imports.  The objects
below can be unit-tested as pure Python and then attached to QSDsan units or
streams at runtime by the engine integration layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Iterable, Mapping, Sequence


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _process_float(value: Any) -> float | None:
    if not _finite(value):
        return None
    value = float(value)
    # QSDsan/solve_ivp can leave concentrations infinitesimally below zero.
    # Treat these as zero so strict sensor ranges do not latch stale values.
    if -1e-6 <= value < 0.0:
        return 0.0
    return value


def _clamp(value: float, lower: float | None, upper: float | None) -> float:
    if lower is not None:
        value = max(float(lower), value)
    if upper is not None:
        value = min(float(upper), value)
    return value


def _coerce_limits(
    lower: float | None = None,
    upper: float | None = None,
    limits: Sequence[float | None] | None = None,
) -> tuple[float | None, float | None]:
    if limits is not None:
        if len(limits) != 2:
            raise ValueError("limits must contain exactly two values")
        lower = None if limits[0] is None else float(limits[0])
        upper = None if limits[1] is None else float(limits[1])
    if lower is not None:
        lower = float(lower)
    if upper is not None:
        upper = float(upper)
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("lower limit cannot exceed upper limit")
    return lower, upper


def _iter_system_objects(system: Any) -> Iterable[Any]:
    yield system
    for attr in ("units", "streams", "products", "feeds"):
        for obj in getattr(system, attr, ()) or ():
            if obj is not None:
                yield obj
    for unit in getattr(system, "units", ()) or ():
        for attr in ("ins", "outs"):
            for stream in getattr(unit, attr, ()) or ():
                if stream is not None:
                    yield stream


def find_object(system: Any, object_id: str) -> Any:
    """Return a unit or stream with matching ``ID`` from a system-like object."""
    for obj in _iter_system_objects(system):
        if getattr(obj, "ID", None) == object_id:
            return obj
    raise LookupError(f"Could not find object {object_id!r} in system")


def _component_index(obj: Any, component: str) -> int | None:
    components = getattr(obj, "components", None) or getattr(obj, "chemicals", None)
    if components is None:
        return None
    try:
        return int(components.index(component))
    except Exception:
        pass
    ids = getattr(components, "IDs", None)
    if ids is not None:
        try:
            return list(ids).index(component)
        except ValueError:
            return None
    return None


def _read_component_value(obj: Any, component: str) -> float:
    if component == "pH":
        for attr in ("pH", "_pH"):
            if hasattr(obj, attr):
                value = getattr(obj, attr)
                if callable(value):
                    value = value()
                value = _process_float(value)
                if value is not None:
                    return value

    if hasattr(obj, component):
        value = getattr(obj, component)
        if callable(value):
            value = value()
        value = _process_float(value)
        if value is not None:
            return value

    for attr in ("state", "_state"):
        try:
            state = getattr(obj, attr)
        except Exception:
            continue
        if isinstance(state, Mapping) and component in state and _finite(state[component]):
            value = _process_float(state[component])
            if value is not None:
                return value
        index = _component_index(obj, component)
        if index is not None:
            try:
                value = state[index]
                value = _process_float(value)
                if value is not None:
                    return value
            except Exception:
                pass

    for attr in ("iconc", "imass", "imol"):
        if not hasattr(obj, attr):
            continue
        try:
            value = getattr(obj, attr)[component]
            value = _process_float(value)
            if value is not None:
                return value
        except Exception:
            pass

    for attr in ("conc", "_concs"):
        if not hasattr(obj, attr):
            continue
        index = _component_index(obj, component)
        if index is None:
            continue
        try:
            value = getattr(obj, attr)[index]
            value = _process_float(value)
            if value is not None:
                return value
        except Exception:
            pass

    raise LookupError(
        f"Could not read variable {component!r} from {getattr(obj, 'ID', type(obj).__name__)}"
    )


def read_process_variable(system: Any, variable: str) -> float:
    """Read ``unit.component``/``stream.component``/attribute variables."""
    if not isinstance(variable, str) or not variable:
        raise ValueError("variable must be a non-empty string")

    target: Any
    name: str
    if "." in variable:
        object_id, name = variable.split(".", 1)
        target = find_object(system, object_id)
    elif ":" in variable:
        object_id, name = variable.split(":", 1)
        target = find_object(system, object_id)
    else:
        target = system
        name = variable

    return _read_component_value(target, name)


@dataclass
class Sensor:
    """Sampled process measurement with optional first-order lag and noise."""

    variable: str
    sample_time_h: float = 1.0
    lag_time_h: float = 0.0
    noise_std: float = 0.0
    bias: float = 0.0
    deadband: float = 0.0
    valid_range: tuple[float | None, float | None] | None = None
    seed: int | None = None
    id: str | None = None
    last_sample_time_h: float | None = field(default=None, init=False)
    last_raw_value: float | None = field(default=None, init=False)
    last_measurement: float | None = field(default=None, init=False)
    last_good_value: float | None = field(default=None, init=False)
    last_valid: bool = field(default=False, init=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.sample_time_h = float(self.sample_time_h)
        self.lag_time_h = float(self.lag_time_h)
        self.noise_std = float(self.noise_std)
        self.bias = float(self.bias)
        self.deadband = float(self.deadband)
        if self.sample_time_h <= 0:
            raise ValueError("sensor sample_time_h must be positive")
        if self.lag_time_h < 0:
            raise ValueError("sensor lag_time_h cannot be negative")
        if self.noise_std < 0:
            raise ValueError("sensor noise_std cannot be negative")
        if self.valid_range is not None:
            self.valid_range = _coerce_limits(limits=self.valid_range)
        self._rng = random.Random(self.seed)

    def due(self, t_h: float) -> bool:
        return (
            self.last_sample_time_h is None
            or float(t_h) - self.last_sample_time_h >= self.sample_time_h - 1e-12
        )

    def read(self, t_h: float, system: Any, *, force: bool = False) -> float:
        if not force and not self.due(t_h) and self.last_measurement is not None:
            return self.last_measurement

        valid = True
        try:
            raw = read_process_variable(system, self.variable)
        except Exception:
            valid = False
            raw = self.last_good_value

        if raw is None or not _finite(raw):
            valid = False
        else:
            raw = float(raw) + self.bias
            if self.noise_std:
                raw += self._rng.gauss(0.0, self.noise_std)

        if valid and self.valid_range is not None:
            lower, upper = self.valid_range
            if (lower is not None and raw < lower) or (upper is not None and raw > upper):
                valid = False

        if not valid:
            if self.last_good_value is None:
                raise ValueError(f"Sensor {self.id or self.variable!r} has no valid measurement")
            raw = self.last_good_value
        else:
            self.last_good_value = float(raw)

        assert raw is not None
        if self.last_sample_time_h is None or self.last_measurement is None or self.lag_time_h == 0:
            measured = float(raw)
        else:
            dt = max(0.0, float(t_h) - self.last_sample_time_h)
            alpha = 1.0 - math.exp(-dt / self.lag_time_h) if self.lag_time_h > 0 else 1.0
            measured = self.last_measurement + alpha * (float(raw) - self.last_measurement)

        if (
            self.last_measurement is not None
            and self.deadband > 0
            and abs(measured - self.last_measurement) < self.deadband
        ):
            measured = self.last_measurement

        self.last_sample_time_h = float(t_h)
        self.last_raw_value = float(raw)
        self.last_measurement = float(measured)
        self.last_valid = valid
        return self.last_measurement

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "variable": self.variable,
            "sample_time_h": self.sample_time_h,
            "lag_time_h": self.lag_time_h,
            "noise_std": self.noise_std,
            "bias": self.bias,
            "deadband": self.deadband,
            "valid_range": self.valid_range,
            "last_raw_value": self.last_raw_value,
            "last_measurement": self.last_measurement,
            "last_valid": self.last_valid,
        }


@dataclass
class PIDController:
    """Discrete PID with sample-and-hold output, limits, and bumpless mode changes."""

    setpoint: float
    Kp: float
    Ki: float = 0.0
    Kd: float = 0.0
    sample_time_h: float = 1.0
    action: str = "reverse"
    output_min: float | None = None
    output_max: float | None = None
    output_limits: tuple[float | None, float | None] | None = None
    rate_limit_per_h: float | None = None
    anti_windup: str = "clamp"
    tracking_time_h: float | None = None
    derivative_on_measurement: bool = True
    output_bias: float = 0.0
    manual_mode: bool = False
    manual_output: float | None = None
    initial_output: float | None = None
    id: str | None = None
    last_update_time_h: float | None = field(default=None, init=False)
    last_measurement: float | None = field(default=None, init=False)
    last_error: float | None = field(default=None, init=False)
    last_output: float = field(default=0.0, init=False)
    integral: float = field(default=0.0, init=False)
    saturated: bool = field(default=False, init=False)
    lower_saturated: bool = field(default=False, init=False)
    upper_saturated: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.setpoint = float(self.setpoint)
        self.Kp = float(self.Kp)
        self.Ki = float(self.Ki)
        self.Kd = float(self.Kd)
        self.sample_time_h = float(self.sample_time_h)
        self.output_min, self.output_max = _coerce_limits(
            self.output_min,
            self.output_max,
            self.output_limits,
        )
        self.output_bias = float(self.output_bias)
        self.action = self.action.lower()
        self.anti_windup = self.anti_windup.lower()
        if self.sample_time_h <= 0:
            raise ValueError("controller sample_time_h must be positive")
        if self.action not in {"reverse", "direct"}:
            raise ValueError("action must be 'reverse' or 'direct'")
        if self.anti_windup not in {"clamp", "back_calculation", "none"}:
            raise ValueError("anti_windup must be 'clamp', 'back_calculation', or 'none'")
        if self.rate_limit_per_h is not None and self.rate_limit_per_h <= 0:
            raise ValueError("rate_limit_per_h must be positive when provided")
        if self.tracking_time_h is not None and self.tracking_time_h <= 0:
            raise ValueError("tracking_time_h must be positive when provided")

        initial = self.output_bias if self.initial_output is None else float(self.initial_output)
        self.last_output = _clamp(initial, self.output_min, self.output_max)
        self.integral = self.last_output - self.output_bias
        if self.manual_output is None:
            self.manual_output = self.last_output
        else:
            self.manual_output = _clamp(float(self.manual_output), self.output_min, self.output_max)

    def due(self, t_h: float) -> bool:
        return (
            self.last_update_time_h is None
            or float(t_h) - self.last_update_time_h >= self.sample_time_h - 1e-12
        )

    def _error(self, measurement: float) -> float:
        error = self.setpoint - measurement
        return error if self.action == "reverse" else -error

    def _derivative_error(self, error: float, measurement: float, dt: float) -> float:
        if self.last_measurement is None or self.last_error is None or dt <= 0:
            return 0.0
        if self.derivative_on_measurement:
            sign = 1.0 if self.action == "reverse" else -1.0
            return -sign * (measurement - self.last_measurement) / dt
        return (error - self.last_error) / dt

    def _apply_rate_limit(self, value: float, dt: float) -> float:
        if self.rate_limit_per_h is None or self.last_update_time_h is None:
            return value
        delta = float(self.rate_limit_per_h) * max(dt, 0.0)
        return _clamp(value, self.last_output - delta, self.last_output + delta)

    def set_manual(self, output: float | None = None) -> float:
        self.manual_mode = True
        if output is not None:
            self.manual_output = _clamp(float(output), self.output_min, self.output_max)
        assert self.manual_output is not None
        self.last_output = self.manual_output
        return self.last_output

    def set_auto(self, *, measurement: float | None = None) -> None:
        """Switch to auto while preserving the current output."""
        if measurement is not None and _finite(measurement):
            measurement = float(measurement)
            error = self._error(measurement)
            proportional = self.Kp * error
            derivative = 0.0
            self.integral = self.last_output - self.output_bias - proportional - derivative
            self.last_measurement = measurement
            self.last_error = error
        else:
            self.integral = self.last_output - self.output_bias
        self.manual_mode = False

    def update(self, t_h: float, measurement: float, *, force: bool = False) -> float:
        if not _finite(measurement):
            raise ValueError("PID measurement must be finite")
        measurement = float(measurement)

        if self.manual_mode:
            assert self.manual_output is not None
            self.last_output = _clamp(float(self.manual_output), self.output_min, self.output_max)
            if force or self.due(t_h):
                self.last_update_time_h = float(t_h)
                self.last_measurement = measurement
                self.last_error = self._error(measurement)
            return self.last_output

        if not force and not self.due(t_h):
            return self.last_output

        dt = (
            self.sample_time_h
            if self.last_update_time_h is None
            else max(1e-12, float(t_h) - self.last_update_time_h)
        )
        error = self._error(measurement)
        proportional = self.Kp * error
        derivative = self.Kd * self._derivative_error(error, measurement, dt)
        candidate_integral = self.integral + self.Ki * error * dt
        unconstrained = self.output_bias + proportional + candidate_integral + derivative
        limited = _clamp(unconstrained, self.output_min, self.output_max)
        limited = self._apply_rate_limit(limited, dt)

        lower_hit = self.output_min is not None and limited <= self.output_min + 1e-12
        upper_hit = self.output_max is not None and limited >= self.output_max - 1e-12
        limited_by_bounds = (
            (self.output_min is not None and unconstrained < self.output_min)
            or (self.output_max is not None and unconstrained > self.output_max)
        )

        if self.anti_windup == "back_calculation" and limited != unconstrained:
            tracking_time = self.tracking_time_h or self.sample_time_h
            self.integral = candidate_integral + (limited - unconstrained) * dt / tracking_time
        elif self.anti_windup == "clamp" and limited_by_bounds:
            drives_high = upper_hit and error > 0
            drives_low = lower_hit and error < 0
            if not (drives_high or drives_low):
                self.integral = candidate_integral
        else:
            self.integral = candidate_integral

        output = self.output_bias + proportional + self.integral + derivative
        output = _clamp(output, self.output_min, self.output_max)
        output = self._apply_rate_limit(output, dt)

        self.lower_saturated = self.output_min is not None and output <= self.output_min + 1e-12
        self.upper_saturated = self.output_max is not None and output >= self.output_max - 1e-12
        self.saturated = self.lower_saturated or self.upper_saturated
        self.last_update_time_h = float(t_h)
        self.last_measurement = measurement
        self.last_error = error
        self.last_output = output
        return output

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "setpoint": self.setpoint,
            "Kp": self.Kp,
            "Ki": self.Ki,
            "Kd": self.Kd,
            "sample_time_h": self.sample_time_h,
            "action": self.action,
            "output_min": self.output_min,
            "output_max": self.output_max,
            "rate_limit_per_h": self.rate_limit_per_h,
            "anti_windup": self.anti_windup,
            "derivative_on_measurement": self.derivative_on_measurement,
            "manual_mode": self.manual_mode,
            "last_output": self.last_output,
            "integral": self.integral,
            "saturated": self.saturated,
            "lower_saturated": self.lower_saturated,
            "upper_saturated": self.upper_saturated,
        }


class Actuator:
    """Base class for held controller outputs applied to process objects."""

    def __init__(
        self,
        *,
        target: str,
        id: str | None = None,
        output_min: float | None = None,
        output_max: float | None = None,
        output_limits: Sequence[float | None] | None = None,
    ) -> None:
        self.target = target
        self.id = id or target
        self.output_min, self.output_max = _coerce_limits(output_min, output_max, output_limits)
        self.held_value: float | None = None
        self.last_apply_time_h: float | None = None
        self.total_time_h = 0.0
        self.saturation_time_h = 0.0
        self.lower_saturation_time_h = 0.0
        self.upper_saturation_time_h = 0.0
        self.lower_saturated = False
        self.upper_saturated = False
        self.saturated = False

    def _bounded(self, value: float) -> tuple[float, bool, bool]:
        bounded = _clamp(float(value), self.output_min, self.output_max)
        lower = self.output_min is not None and bounded <= self.output_min + 1e-12
        upper = self.output_max is not None and bounded >= self.output_max - 1e-12
        return bounded, lower, upper

    def _update_stats(self, t_h: float, lower: bool, upper: bool) -> None:
        t_h = float(t_h)
        if self.last_apply_time_h is not None:
            dt = max(0.0, t_h - self.last_apply_time_h)
            self.total_time_h += dt
            if self.saturated:
                self.saturation_time_h += dt
            if self.lower_saturated:
                self.lower_saturation_time_h += dt
            if self.upper_saturated:
                self.upper_saturation_time_h += dt
        self.last_apply_time_h = t_h
        self.lower_saturated = lower
        self.upper_saturated = upper
        self.saturated = lower or upper

    def apply(self, t_h: float, command: float, system: Any) -> float:
        raise NotImplementedError

    def finalize(self, t_h: float) -> None:
        self._update_stats(t_h, self.lower_saturated, self.upper_saturated)

    def report(self) -> dict[str, Any]:
        total = self.total_time_h
        sat_frac = self.saturation_time_h / total if total > 0 else 0.0
        return {
            "id": self.id,
            "type": type(self).__name__,
            "target": self.target,
            "held_value": self.held_value,
            "output_min": self.output_min,
            "output_max": self.output_max,
            "saturated": self.saturated,
            "lower_saturated": self.lower_saturated,
            "upper_saturated": self.upper_saturated,
            "saturation_time_h": self.saturation_time_h,
            "lower_saturation_time_h": self.lower_saturation_time_h,
            "upper_saturation_time_h": self.upper_saturation_time_h,
            "total_time_h": total,
            "saturation_fraction": sat_frac,
        }


class AerationActuator(Actuator):
    """Apply a held KLa or Q_air command to an aerobic QSDsan reactor."""

    def __init__(
        self,
        *,
        target: str,
        variable: str = "KLa",
        DO_ID: str = "S_O2",
        DOsat: float | None = None,
        V: float | None = None,
        id: str | None = None,
        output_min: float | None = None,
        output_max: float | None = None,
        output_limits: Sequence[float | None] | None = None,
        alpha: float | None = None,
        beta: float | None = None,
        F: float | None = None,
        theta: float | None = None,
        SOTE: float | None = None,
        d_submergence: float | None = None,
        fine_pore: bool | None = None,
        elevation: float | None = None,
        T_air: float | None = None,
        T_water: float | None = None,
        DOsat_s20: float | None = None,
    ) -> None:
        super().__init__(
            target=target,
            id=id,
            output_min=output_min,
            output_max=output_max,
            output_limits=output_limits,
        )
        self.variable = variable
        self.DO_ID = DO_ID
        # DOsat=None means "inherit the physically-derived saturation from the
        # DiffusedAeration Process" (tau*beta*Omega*delta*DOsat_s20). It used to
        # default to a bare 8.0 and was stamped onto the unit, where the guarded
        # ODE reads it in preference to the Process value -- discarding the
        # temperature, salinity, pressure and submergence corrections. At 20 C and
        # 5 m submergence the correct figure is ~10.3 mg/L, so the driving force
        # was understated by ~22%. Pass a float only to override deliberately.
        if DOsat is not None and DOsat_s20 is not None:
            # QSDsan's DOsat setter back-solves and silently overwrites DOsat_s20, so
            # supplying both is contradictory. The declarative spec path already
            # refuses this; the actuator path must too, or the same invariant holds
            # in one route and not the other.
            raise ValueError(
                "give DOsat or DOsat_s20, not both: QSDsan's DOsat setter back-solves "
                "and overwrites DOsat_s20."
            )
        self.DOsat = None if DOsat is None else float(DOsat)
        self.V = None if V is None else float(V)
        # The ASCE correction chain. Anything left None inherits the QSDsan
        # default; passing them through is what makes alpha, submergence and
        # temperature actually reach a DO-controlled run rather than only a
        # spec-built one.
        self.physical: dict[str, Any] = {
            key: value
            for key, value in (
                ("alpha", alpha),
                ("beta", beta),
                ("F", F),
                ("theta", theta),
                ("SOTE", SOTE),
                ("d_submergence", d_submergence),
                ("fine_pore", fine_pore),
                ("elevation", elevation),
                ("T_air", T_air),
                ("T_water", T_water),
                ("DOsat_s20", DOsat_s20),
            )
            if value is not None
        }

    def _effective_dosat(self, aer: Any) -> float:
        """Resolve the DO saturation to hand the ODE, preferring real physics."""
        if self.DOsat is not None:
            return self.DOsat
        derived = getattr(aer, "DOsat", None)
        try:
            derived = float(derived)
        except (TypeError, ValueError):
            derived = None
        if derived is None or derived <= 0:
            return 8.0
        return derived

    def _maybe_update_diffused_aeration(self, unit: Any, kla: float, q_air: float | None) -> None:
        try:
            from qsdsan.process_models import DiffusedAeration  # type: ignore
        except Exception:
            return

        volume = self.V or float(getattr(unit, "V_max", getattr(unit, "_V_max", 0.0)) or 0.0)
        if volume <= 0:
            return
        aer = getattr(unit, "_codex_diffused_aeration", None)
        if aer is None:
            aer = DiffusedAeration(
                f"{getattr(unit, 'ID', self.target)}_codex_aeration",
                self.DO_ID,
                V=volume,
                KLa=kla if q_air is None else None,
                Q_air=q_air,
                DOsat=self.DOsat,
                **self.physical,
            )
            unit._codex_diffused_aeration = aer
        else:
            aer.V = volume
            for key, value in self.physical.items():
                setattr(aer, key, value)
            # None re-derives from tau*beta*Omega*delta*DOsat_s20 rather than
            # pinning a placeholder.
            aer.DOsat = self.DOsat
            if q_air is None:
                aer.KLa = kla
            else:
                aer.Q_air = q_air
                kla = float(aer.KLa)
        try:
            unit.aeration = aer
        except Exception:
            unit._aeration = aer

    def apply(self, t_h: float, command: float, system: Any) -> float:
        value, lower, upper = self._bounded(command)
        unit = find_object(system, self.target)

        variable = self.variable.lower()
        if variable in {"kla", "kla_d", "kla_per_d"}:
            kla = value
            q_air = None
        elif variable in {"q_air", "air", "airflow", "air_flow"}:
            q_air = value
            self._maybe_update_diffused_aeration(unit, kla=0.0, q_air=q_air)
            aer = getattr(unit, "_codex_diffused_aeration", None)
            if aer is None:
                raise RuntimeError(
                    "Q_air aeration control requires QSDsan DiffusedAeration to be importable"
                )
            kla = float(aer.KLa)
        else:
            raise ValueError("AerationActuator variable must be 'KLa' or 'Q_air'")

        if q_air is None:
            self._maybe_update_diffused_aeration(unit, kla=kla, q_air=None)

        unit._codex_do_control_mode = "kla_pid"
        unit._codex_do_control_actuator_id = self.id
        unit._codex_do_control_DO_ID = self.DO_ID
        # Prefer the Process's physically-derived saturation over a bare default:
        # the guarded ODE reads this stamp in preference to aer.DOsat, so writing
        # a placeholder here silently discards tau/beta/Omega/delta.
        unit._codex_do_control_DO_sat_mg_L = self._effective_dosat(
            getattr(unit, "_codex_diffused_aeration", None)
        )
        unit._codex_dynamic_kLa_d = kla
        unit._codex_dynamic_Q_air_m3_d = q_air
        unit._codex_do_control_semantics = (
            "WaterTAP steady-state actuator values are design-equivalent; "
            "QSDsan PID actuator values are sampled dynamic trajectories."
        )
        if not getattr(unit, "_codex_dynamic_control_attached", False):
            unit._codex_dynamic_control_attached = True
            if hasattr(unit, "_ODE"):
                unit._ODE = None
            if hasattr(system, "_DAE"):
                system._DAE = None

        self.held_value = kla
        self._update_stats(t_h, lower, upper)
        return self.held_value


class DoseFlowActuator(Actuator):
    """Apply a held dose flow or concentration to a stream/unit attribute."""

    def __init__(
        self,
        *,
        target: str,
        attribute: str = "F_vol",
        flow_units: str = "m3/d",
        component_id: str | None = None,
        id: str | None = None,
        output_min: float | None = None,
        output_max: float | None = None,
        output_limits: Sequence[float | None] | None = None,
    ) -> None:
        super().__init__(
            target=target,
            id=id,
            output_min=output_min,
            output_max=output_max,
            output_limits=output_limits,
        )
        self.attribute = attribute
        self.flow_units = flow_units
        self.component_id = component_id

    def apply(self, t_h: float, command: float, system: Any) -> float:
        value, lower, upper = self._bounded(command)
        target = find_object(system, self.target)
        if self.component_id:
            for attr in ("imass", "imol", "iconc"):
                if hasattr(target, attr):
                    try:
                        getattr(target, attr)[self.component_id] = value
                        break
                    except Exception:
                        pass
            else:
                raise AttributeError(f"{self.target!r} does not expose component dosing arrays")
        elif self.attribute in {"F_vol", "flow", "Q"} and hasattr(target, "set_total_flow"):
            target.set_total_flow(value, self.flow_units)
        else:
            setattr(target, self.attribute, value)
        self.held_value = value
        self._update_stats(t_h, lower, upper)
        return value


def _coerce_floats(value: Any) -> tuple[float, ...]:
    if value is None:
        return ()
    if isinstance(value, (int, float)):
        return (float(value),)
    try:
        return tuple(float(x) for x in value)
    except (TypeError, ValueError):
        return ()


def _wastage_hardware_fingerprint(system: Any) -> tuple[float, ...]:
    """Snapshot the wastage-relevant hardware (pumped_flow / wastage / split).

    Used to detect whether ``update_wastage_actuator`` actually moved the
    hydraulics. The SRT actuator infers ``Q_ras`` from the live recycle flow, so
    an UNCHANGED wastage command can still re-derive a different ``pumped_flow``;
    keying the closure invalidation on the realized hardware (not the command)
    catches that and avoids re-converging when nothing moved.
    """
    values: list[float] = []
    for unit in getattr(system, "units", ()) or ():
        for attr in ("pumped_flow", "wastage", "underflow"):
            values.extend(_coerce_floats(getattr(unit, attr, None)))
        values.extend(_coerce_floats(getattr(unit, "split", None)))
    return tuple(values)


def _refresh_hydraulics_and_invalidate_closures(system: Any) -> None:
    """Re-freeze converged hydraulics and drop stale compiled dynamic closures.

    A mid-run wastage change (``MBR.pumped_flow`` / ``Splitter.split`` /
    ``Clarifier.wastage``) alters the recycle hydraulics, but the guarded
    CSTR/MBR ODE bakes ``pumped_flow`` and the frozen outflows into its compiled
    closure, and the downstream Splitter caches its ``split`` in ``_AE`` -- so a
    live command would not take effect until a recompile. (B1 restore handles
    this via a full recompile; the LIVE sample-and-hold run did not.)

    Re-run the frozen-hydraulics stabilizer (re-converges the recycle and
    refreshes ``_codex_fixed_hydraulic_*`` from the new hardware; ``converge``
    leaves the dynamic concentration state untouched), then null the system DAE,
    each dynamic unit's ODE, and EVERY cached algebraic-unit ``_AE`` so the RHS
    recompiles against the new flows on the next integration step. The Splitter
    is NOT ``isdynamic`` but still caches ``_AE`` once compiled, so its split is
    dropped here too -- mirroring B1 restore + ``run_to_srt`` invalidation.
    """
    if not getattr(system, "isdynamic", False):
        return
    try:
        from qsdsan_engine_mcp.utils.dynamic_hydraulics import stabilize_dynamic_hydraulics
    except Exception:  # pragma: no cover - QSDsan-less environments
        stabilize_dynamic_hydraulics = None
    if stabilize_dynamic_hydraulics is not None:
        stabilize_dynamic_hydraulics(system)
    if hasattr(system, "_DAE"):
        system._DAE = None
    for unit in getattr(system, "units", ()) or ():
        if getattr(unit, "isdynamic", False) and hasattr(unit, "_ODE"):
            unit._ODE = None
        # Algebraic units (e.g. Splitter) cache their AE closure even though they
        # are not ``isdynamic``; a split change must drop the cached AE too.
        if hasattr(unit, "_AE"):
            unit._AE = None


class WASActuator(Actuator):
    """Apply a slow wastage-flow command using the existing SRT actuator helper."""

    def apply(self, t_h: float, command: float, system: Any) -> float:
        value, lower, upper = self._bounded(command)
        try:
            from qsdsan_engine_mcp.utils.srt_control import update_wastage_actuator
        except Exception as exc:
            raise RuntimeError("WASActuator requires utils.srt_control") from exc
        before = _wastage_hardware_fingerprint(system)
        success, message = update_wastage_actuator(system, value, validate=True)
        if not success:
            raise RuntimeError(message)
        # The wastage HARDWARE (pumped_flow / split / wastage) just (re)set. When
        # it actually moved, drop the stale frozen hydraulics + compiled closures
        # so the new flow takes effect on the NEXT integration step within this
        # run -- not only after a restart/recompile. When it didn't move, the
        # converged closures stay valid (re-converging a steady sample is wasted
        # work and perturbs nothing).
        if self.held_value is None or _wastage_hardware_fingerprint(system) != before:
            _refresh_hydraulics_and_invalidate_closures(system)
        self.held_value = value
        self._update_stats(t_h, lower, upper)
        return value


class BaseDoseActuator(Actuator):
    """Hold an alkali/cation dose into an anaerobic digester to trim pH.

    The mADM1 PCM solves pH from a full charge balance, so adding a strong
    cation (caustic soda -> ``S_Na``; or any conservative cation component)
    raises pH, while VFA accumulation / lost alkalinity lowers it. This
    actuator writes the held command as the bulk cation concentration
    (kg/m3) directly onto the reactor's *dynamic* state vector
    (``unit._state[component_index]``) -- the live ODE initial condition the
    integrator carries into the next sample-and-hold segment (the same write
    surface :mod:`core.disturbance` uses for feed states). Pairing it with a
    pH sensor + reverse-action PID gives a dynamic base-dose loop: as digester
    pH falls below setpoint the commanded cation level rises and recovers pH.

    The command is an *absolute* bulk cation level (an alkalinity-trim
    set-point the controller re-asserts each sample), not an incremental dose
    rate; ``output_min`` should be the baseline cation concentration and
    ``output_max`` a tight ceiling (mADM1 pH is steeply sensitive to cation
    addition once the weak-acid buffer is exhausted).
    """

    def __init__(
        self,
        *,
        target: str,
        component_id: str = "S_Na",
        id: str | None = None,
        output_min: float | None = None,
        output_max: float | None = None,
        output_limits: Sequence[float | None] | None = None,
    ) -> None:
        super().__init__(
            target=target,
            id=id,
            output_min=output_min,
            output_max=output_max,
            output_limits=output_limits,
        )
        self.component_id = component_id

    def apply(self, t_h: float, command: float, system: Any) -> float:
        value, lower, upper = self._bounded(command)
        unit = find_object(system, self.target)
        index = _component_index(unit, self.component_id)
        if index is None:
            raise LookupError(
                f"BaseDoseActuator: component {self.component_id!r} not found on {self.target!r}"
            )
        state = getattr(unit, "_state", None)
        if state is None:
            raise RuntimeError(
                f"BaseDoseActuator target {self.target!r} has no dynamic _state vector "
                "(run at least one segment / seed the unit first)"
            )
        state[index] = float(value)
        unit._codex_base_dose_component = self.component_id
        unit._codex_base_dose_actuator_id = self.id
        unit._codex_base_dose_value_kg_m3 = float(value)
        self.held_value = float(value)
        self._update_stats(t_h, lower, upper)
        return self.held_value


class DigesterHeatingActuator(Actuator):
    """Hold a heating-duty command on a digester via a lumped thermal model.

    mADM1 in this engine has *constant-parameter* biological kinetics (no
    Arrhenius temperature dependence on the growth/uptake rates), so digester
    temperature acts on the acid-base equilibria (``Ka``), Henry's-law gas
    solubility (``KH``) and the PCM (pH / free-ammonia speciation) -- NOT on
    the biological rate constants. This actuator models the thermal loop as a
    first-order lumped capacitance::

        T_eq      = T_ambient_K + gain_K_per_kW * P_heat
        T_current += (1 - exp(-dt / tau_h)) * (T_eq - T_current)

    and applies the resulting temperature to the reactor (``unit.T``) and to
    the rate-function operating temperature (``params['T_op']``), forcing an
    ODE recompile so the gas-transfer closure also picks up the new T. Paired
    with a temperature sensor (``<unit>.T``, Kelvin) + reverse-action PID, the
    commanded heat duty rises when measured T falls below set-point and drives
    T back up -- a dynamic heating controller that recovers a temperature trip.

    ``held_value`` is the held heating duty (kW); the resulting temperature is
    exposed as ``temperature_K`` and in :meth:`report`.
    """

    def __init__(
        self,
        *,
        target: str,
        tau_h: float = 3.0,
        T_ambient_K: float = 288.15,
        gain_K_per_kW: float = 0.04,
        initial_T_K: float | None = None,
        id: str | None = None,
        output_min: float | None = 0.0,
        output_max: float | None = None,
        output_limits: Sequence[float | None] | None = None,
    ) -> None:
        super().__init__(
            target=target,
            id=id,
            output_min=output_min,
            output_max=output_max,
            output_limits=output_limits,
        )
        self.tau_h = float(tau_h)
        self.T_ambient_K = float(T_ambient_K)
        self.gain_K_per_kW = float(gain_K_per_kW)
        if self.tau_h <= 0:
            raise ValueError("DigesterHeatingActuator tau_h must be positive")
        if self.gain_K_per_kW <= 0:
            raise ValueError("DigesterHeatingActuator gain_K_per_kW must be positive")
        self.temperature_K: float | None = None if initial_T_K is None else float(initial_T_K)

    def _model_params(self, unit: Any) -> Any:
        model = getattr(unit, "_model", None) or getattr(unit, "model", None)
        if model is None:
            return None
        rate_function = getattr(model, "rate_function", None)
        if rate_function is None:
            return None
        return getattr(rate_function, "_params", None) or getattr(rate_function, "params", None)

    def _write_temperature(self, unit: Any, system: Any, T_new: float) -> None:
        """Apply ``T_new`` (K) to the reactor and rate-function operating T."""
        self.temperature_K = float(T_new)
        try:
            unit.T = float(T_new)
        except Exception:
            pass
        params = self._model_params(unit)
        if params is not None:
            params["T_op"] = float(T_new)
        # Force ODE/DAE recompile so the temperature-bound closures (exogenous
        # T -> gas transfer, q_gas = f(R*T)) refresh on the next segment. The
        # dynamic _state is untouched, so this is a warm recompile, not a reset.
        if hasattr(unit, "_ODE"):
            unit._ODE = None
        if hasattr(system, "_DAE"):
            system._DAE = None

    def inject_temperature(self, system: Any, T_new_K: float) -> float:
        """Apply a direct reactor-temperature event (e.g. a heat-source trip).

        Temperature is not a feed-stream variable in mADM1, so a temperature
        trip is applied as a direct reactor event rather than through
        :class:`~core.disturbance.DisturbanceScheduler`. The heating control
        loop then recovers it over subsequent samples.
        """
        unit = find_object(system, self.target)
        self._write_temperature(unit, system, float(T_new_K))
        return self.temperature_K

    def apply(self, t_h: float, command: float, system: Any) -> float:
        duty, lower, upper = self._bounded(command)
        unit = find_object(system, self.target)
        if self.temperature_K is None:
            current = getattr(unit, "T", None)
            self.temperature_K = float(current) if current else (self.T_ambient_K + self.gain_K_per_kW * duty)

        # First-order lumped-capacitance relaxation toward the duty's
        # equilibrium temperature over the elapsed sample interval.
        T_eq = self.T_ambient_K + self.gain_K_per_kW * duty
        if self.last_apply_time_h is not None:
            dt_h = max(0.0, float(t_h) - self.last_apply_time_h)
            alpha = 1.0 - math.exp(-dt_h / self.tau_h) if dt_h > 0 else 0.0
            self.temperature_K = self.temperature_K + alpha * (T_eq - self.temperature_K)
        self._write_temperature(unit, system, self.temperature_K)
        self.held_value = float(duty)
        self._update_stats(t_h, lower, upper)
        return self.held_value

    def report(self) -> dict[str, Any]:
        data = super().report()
        data["temperature_K"] = self.temperature_K
        data["tau_h"] = self.tau_h
        data["T_ambient_K"] = self.T_ambient_K
        data["gain_K_per_kW"] = self.gain_K_per_kW
        return data


@dataclass
class ControlLoop:
    id: str
    sensor: Sensor
    controller: PIDController
    actuator: Actuator
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def sample_time_h(self) -> float:
        return min(self.sensor.sample_time_h, self.controller.sample_time_h)

    def update(self, t_h: float, system: Any, *, force: bool = False) -> dict[str, Any]:
        measurement = self.sensor.read(t_h, system, force=force)
        command = self.controller.update(t_h, measurement, force=force)
        actuator_value = self.actuator.apply(t_h, command, system)
        record = {
            "time_h": float(t_h),
            "time_days": float(t_h) / 24.0,
            "measurement": measurement,
            "setpoint": self.controller.setpoint,
            "error": self.controller.last_error,
            "command": command,
            "actuator_value": actuator_value,
            "controller_saturated": self.controller.saturated,
            "actuator_saturated": self.actuator.saturated,
            "sensor_valid": self.sensor.last_valid,
        }
        self.history.append(record)
        return record

    def finalize(self, t_h: float) -> None:
        self.actuator.finalize(t_h)

    def metrics(self) -> dict[str, Any]:
        iae = 0.0
        previous = None
        for record in self.history:
            error = record.get("error")
            time_h = record.get("time_h")
            if not _finite(error) or not _finite(time_h):
                continue
            error = abs(float(error))
            time_h = float(time_h)
            if previous is not None:
                prev_t, prev_error = previous
                iae += 0.5 * (prev_error + error) * max(0.0, time_h - prev_t)
            previous = (time_h, error)
        final = self.history[-1] if self.history else {}
        return {
            "integral_absolute_error_h": iae,
            "final_error": final.get("error"),
            "final_measurement": final.get("measurement"),
            "final_command": final.get("command"),
            "final_actuator_value": final.get("actuator_value"),
            "samples": len(self.history),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sample_time_h": self.sample_time_h,
            "sensor": self.sensor.as_dict(),
            "controller": self.controller.as_dict(),
            "actuator": self.actuator.report(),
            "metrics": self.metrics(),
            "history": self.history,
        }


class DynamicControlManager:
    """Registry and sample dispatcher for dynamic control loops."""

    def __init__(self, loops: Sequence[ControlLoop]) -> None:
        self.loops = list(loops)
        if not self.loops:
            raise ValueError("DynamicControlManager requires at least one loop")
        self.initialized = False

    @property
    def sample_time_h(self) -> float:
        return min(loop.sample_time_h for loop in self.loops)

    def initialize(self, system: Any, *, t_h: float = 0.0) -> None:
        for loop in self.loops:
            loop.update(t_h, system, force=True)
        self.initialized = True

    def update(self, t_h: float, system: Any) -> list[dict[str, Any]]:
        if not self.initialized:
            self.initialize(system, t_h=t_h)
            return [loop.history[-1] for loop in self.loops]
        return [loop.update(t_h, system) for loop in self.loops]

    def finalize(self, t_h: float) -> None:
        for loop in self.loops:
            loop.finalize(t_h)

    def as_results(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "sample_time_h": self.sample_time_h,
            "loops": [loop.as_dict() for loop in self.loops],
            "reconciliation_note": (
                "WaterTAP steady-state solved actuator values are design-equivalent. "
                "QSDsan PID values are controlled dynamic trajectories/final averages; "
                "report saturation and sensor lag alongside actuator values."
            ),
        }


def _pid_from_spec(spec: Mapping[str, Any]) -> PIDController:
    data = dict(spec)
    if "output_limits" in data:
        # Always pop: a conditional pop leaves the key behind when it is combined with
        # an explicit bound, and the strict unknown-key check then rejects a key this
        # function itself understands.
        limits = data.pop("output_limits")
        if "output_min" in data or "output_max" in data:
            raise ValueError(
                "give output_limits or output_min/output_max, not both -- they are two "
                "spellings of the same bound and the pair is ambiguous."
            )
        data["output_min"], data["output_max"] = limits
    allowed = {
        "id",
        "setpoint",
        "Kp",
        "Ki",
        "Kd",
        "sample_time_h",
        "action",
        "output_min",
        "output_max",
        "rate_limit_per_h",
        "anti_windup",
        "tracking_time_h",
        "derivative_on_measurement",
        "output_bias",
        "manual_mode",
        "manual_output",
        "initial_output",
    }
    return PIDController(**{key: value for key, value in data.items() if key in allowed})


def _sensor_from_spec(spec: Mapping[str, Any]) -> Sensor:
    data = dict(spec)
    allowed = {
        "id",
        "variable",
        "sample_time_h",
        "lag_time_h",
        "noise_std",
        "bias",
        "deadband",
        "valid_range",
        "seed",
    }
    return Sensor(**{key: value for key, value in data.items() if key in allowed})


def _actuator_from_spec(spec: Mapping[str, Any]) -> Actuator:
    data = dict(spec)
    actuator_type = str(data.pop("type", "attribute")).lower()
    if "output_limits" in data:
        # Always pop: a conditional pop leaves the key behind when it is combined with
        # an explicit bound, and the strict unknown-key check then rejects a key this
        # function itself understands.
        limits = data.pop("output_limits")
        if "output_min" in data or "output_max" in data:
            raise ValueError(
                "give output_limits or output_min/output_max, not both -- they are two "
                "spellings of the same bound and the pair is ambiguous."
            )
        data["output_min"], data["output_max"] = limits
    if actuator_type in {"aeration", "do_kla", "kla", "diffused_aeration"}:
        allowed = {
            "id",
            "target",
            "variable",
            "DO_ID",
            "DOsat",
            "V",
            "output_min",
            "output_max",
            # ASCE correction chain -- without these a DO-control run silently
            # falls back to QSDsan's alpha=0.6 / SOTE=0.3 / 5 m defaults.
            "alpha",
            "beta",
            "F",
            "theta",
            "SOTE",
            "d_submergence",
            "fine_pore",
            "elevation",
            "T_air",
            "T_water",
            "DOsat_s20",
        }
        unknown = set(data) - allowed - {"type"}
        if unknown:
            raise ValueError(
                f"unknown aeration actuator key(s): {sorted(unknown)}. "
                f"Accepted: {sorted(allowed)}"
            )
        return AerationActuator(**{key: value for key, value in data.items() if key in allowed})
    if actuator_type in {"dose", "dose_flow", "alkalinity", "base", "caustic"}:
        allowed = {
            "id",
            "target",
            "attribute",
            "flow_units",
            "component_id",
            "output_min",
            "output_max",
        }
        return DoseFlowActuator(**{key: value for key, value in data.items() if key in allowed})
    if actuator_type in {"base_dose", "caustic_dose", "ph_dose", "alkalinity_dose"}:
        allowed = {"id", "target", "component_id", "output_min", "output_max"}
        return BaseDoseActuator(**{key: value for key, value in data.items() if key in allowed})
    if actuator_type in {"heating", "heat_duty", "digester_heating", "temperature"}:
        allowed = {
            "id",
            "target",
            "tau_h",
            "T_ambient_K",
            "gain_K_per_kW",
            "initial_T_K",
            "output_min",
            "output_max",
        }
        return DigesterHeatingActuator(**{key: value for key, value in data.items() if key in allowed})
    if actuator_type in {"was", "wastage"}:
        allowed = {"id", "target", "output_min", "output_max"}
        return WASActuator(**{key: value for key, value in data.items() if key in allowed})
    allowed = {"id", "target", "attribute", "flow_units", "component_id", "output_min", "output_max"}
    return DoseFlowActuator(**{key: value for key, value in data.items() if key in allowed})


def build_control_manager(spec: Any) -> DynamicControlManager | None:
    """Build a :class:`DynamicControlManager` from a JSON-like spec."""
    if spec is None or spec is False:
        return None
    if isinstance(spec, DynamicControlManager):
        return spec
    if isinstance(spec, Mapping):
        loops_spec = spec.get("loops", spec.get("dynamic_controls"))
        if loops_spec is None and {"sensor", "controller", "actuator"} <= set(spec):
            loops_spec = [spec]
    elif isinstance(spec, Sequence) and not isinstance(spec, (str, bytes, bytearray)):
        loops_spec = spec
    else:
        raise TypeError("dynamic_controls must be a mapping, sequence, or DynamicControlManager")
    if not loops_spec:
        return None

    loops: list[ControlLoop] = []
    for index, loop_spec in enumerate(loops_spec):
        if not isinstance(loop_spec, Mapping):
            raise TypeError("each dynamic control loop must be a mapping")
        loop_id = str(loop_spec.get("id") or f"loop_{index + 1}")
        sensor = _sensor_from_spec(loop_spec["sensor"])
        controller = _pid_from_spec(loop_spec["controller"])
        actuator = _actuator_from_spec(loop_spec["actuator"])
        sensor.id = sensor.id or f"{loop_id}.sensor"
        controller.id = controller.id or f"{loop_id}.pid"
        actuator.id = actuator.id or f"{loop_id}.actuator"
        loops.append(ControlLoop(loop_id, sensor, controller, actuator))
    return DynamicControlManager(loops)


__all__ = [
    "Actuator",
    "AerationActuator",
    "BaseDoseActuator",
    "ControlLoop",
    "DigesterHeatingActuator",
    "DoseFlowActuator",
    "DynamicControlManager",
    "PIDController",
    "Sensor",
    "WASActuator",
    "build_control_manager",
    "find_object",
    "read_process_variable",
]
