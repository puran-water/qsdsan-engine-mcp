"""Declarative feed-disturbance scheduling for QSDsan dynamic simulations.

This is the universal, MCP-exposed mechanism for transient scenarios — load
shocks, pH / alkalinity excursions, and CIP slugs — applied to a compiled
dynamic system's feed stream(s) at controller-sample boundaries.

Design parallels :mod:`core.control`:

* The schedule objects (``Disturbance`` / ``DisturbanceScheduler``) are pure
  Python and unit-testable without importing QSDsan.  Only the feed-state write
  in :meth:`DisturbanceScheduler.apply` touches a live QSDsan system.
* The sample-and-hold driver (``utils.run_to_convergence.run_sampled_data_system``)
  evaluates the schedule at each segment boundary and writes the disturbed feed
  state for the *next* ODE segment.  A step is genuinely discrete; ramps/pulses
  are realized piecewise at the controller sample resolution.

Mechanics (validated against ASM1 CSTR feeds):

* The authoritative baseline is the feed stream's ``conc`` (mg/L per component,
  in ``components.IDs`` order) and ``F_vol * 24`` (m3/d).  ``WasteStream.state``
  is zero until the first segment runs, so baseline is captured from ``conc``.
* A concentration disturbance writes ``feed.state[component_index]`` directly;
  this propagates into the reactor ODE on the next segment because the driver
  continues integration with ``state_reset_hook=None`` after the first segment.
* A flow ("Q") disturbance updates the feed thermo flow and re-runs
  ``stabilize_dynamic_hydraulics`` so the frozen-hydraulics guard picks up the
  new flow (the guard reads ``F_vol``, not ``state[-1]``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence


FLOW_VARIABLE = "Q"
_VALID_TYPES = {"step", "ramp", "pulse"}
_VALID_MODES = {"relative", "absolute"}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _require_finite(value: Any, label: str) -> float:
    if not _finite(value):
        raise ValueError(f"{label} must be a finite number (got {value!r})")
    return float(value)


@dataclass
class Disturbance:
    """A single declarative feed disturbance.

    Parameters
    ----------
    variable:
        Feed component id (concentration, mg/L) or ``"Q"`` (volumetric flow,
        m3/d).
    type:
        ``"step"`` — hold ``value`` from ``start_h`` onward.
        ``"ramp"`` — linear from baseline at ``start_h`` to ``value`` at
        ``end_h``, held thereafter.
        ``"pulse"`` — hold ``value`` over ``[start_h, end_h]``, baseline
        elsewhere (slug / CIP return).
    value:
        ``mode="relative"`` multiplies the baseline by ``value``; ``"absolute"``
        sets the variable to ``value`` directly.
    """

    variable: str
    type: str = "step"
    start_h: float = 0.0
    value: float = 1.0
    end_h: float | None = None
    mode: str = "relative"
    target: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.variable, str) or not self.variable:
            raise ValueError("disturbance.variable must be a non-empty string")
        self.type = str(self.type).lower()
        if self.type not in _VALID_TYPES:
            raise ValueError(f"disturbance.type must be one of {sorted(_VALID_TYPES)}")
        self.mode = str(self.mode).lower()
        if self.mode not in _VALID_MODES:
            raise ValueError(f"disturbance.mode must be one of {sorted(_VALID_MODES)}")
        self.start_h = _require_finite(self.start_h, "disturbance.start_h")
        if self.start_h < 0:
            raise ValueError("disturbance.start_h cannot be negative")
        self.value = _require_finite(self.value, "disturbance.value")
        if self.type in {"ramp", "pulse"}:
            if self.end_h is None:
                raise ValueError(f"disturbance.type={self.type!r} requires end_h")
            self.end_h = _require_finite(self.end_h, "disturbance.end_h")
            if self.end_h <= self.start_h:
                raise ValueError("disturbance.end_h must be greater than start_h")
        elif self.end_h is not None:
            self.end_h = _require_finite(self.end_h, "disturbance.end_h")

    @property
    def is_flow(self) -> bool:
        return self.variable == FLOW_VARIABLE

    def level_at(self, t_h: float, baseline: float) -> float | None:
        """Return the value to set at ``t_h`` or ``None`` to hold baseline."""
        t_h = float(t_h)
        target_value = self.value * baseline if self.mode == "relative" else self.value

        if self.type == "step":
            return target_value if t_h >= self.start_h - 1e-9 else None

        if self.type == "pulse":
            if self.start_h - 1e-9 <= t_h <= float(self.end_h) + 1e-9:
                return target_value
            return None

        # ramp
        if t_h < self.start_h - 1e-9:
            return None
        if t_h >= float(self.end_h) - 1e-9:
            return target_value
        frac = (t_h - self.start_h) / (float(self.end_h) - self.start_h)
        return baseline + frac * (target_value - baseline)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "variable": self.variable,
            "type": self.type,
            "mode": self.mode,
            "start_h": self.start_h,
            "end_h": self.end_h,
            "value": self.value,
        }


def _components_ids(obj: Any) -> list[str]:
    components = getattr(obj, "components", None)
    ids = getattr(components, "IDs", None)
    if ids is None:
        raise LookupError("feed stream has no component metadata")
    return list(ids)


def _feed_flow_m3_d(feed: Any) -> float:
    try:
        return float(getattr(feed, "F_vol", 0.0) or 0.0) * 24.0
    except Exception:
        return 0.0


def _feed_concentrations(feed: Any) -> dict[str, float]:
    """Authoritative baseline concentrations (mg/L) keyed by component id."""
    ids = _components_ids(feed)
    conc = getattr(feed, "conc", None)
    out: dict[str, float] = {}
    if conc is not None:
        for index, comp_id in enumerate(ids):
            try:
                out[comp_id] = float(conc[index])
            except Exception:
                continue
    return out


class DisturbanceScheduler:
    """Apply declarative feed disturbances at sample-and-hold boundaries."""

    def __init__(self, disturbances: Sequence[Disturbance]) -> None:
        self.disturbances = list(disturbances)
        if not self.disturbances:
            raise ValueError("DisturbanceScheduler requires at least one disturbance")
        for index, dist in enumerate(self.disturbances):
            if dist.id is None:
                dist.id = f"disturbance_{index + 1}"
        self._feeds: dict[str, Any] = {}
        self._baseline_conc: dict[str, dict[str, float]] = {}
        self._baseline_flow: dict[str, float] = {}
        self._comp_index: dict[str, dict[str, int]] = {}
        self.history: list[dict[str, Any]] = []
        self.initialized = False

    # ------------------------------------------------------------------ setup
    def _resolve_feed(self, system: Any, target: str | None) -> Any:
        feeds = [f for f in (getattr(system, "feeds", None) or []) if f is not None]
        if target:
            for feed in feeds:
                if getattr(feed, "ID", None) == target:
                    return feed
            # fall back to any stream in the system with that ID
            for unit in getattr(system, "units", []) or []:
                for stream in getattr(unit, "ins", []) or []:
                    if getattr(stream, "ID", None) == target:
                        return stream
            raise LookupError(
                f"disturbance target feed {target!r} not found among system feeds "
                f"{[getattr(f, 'ID', None) for f in feeds]}"
            )
        if not feeds:
            raise LookupError("system has no feed streams for disturbance application")
        # primary feed = largest volumetric flow
        return max(feeds, key=_feed_flow_m3_d)

    def initialize(self, system: Any) -> None:
        for dist in self.disturbances:
            feed = self._resolve_feed(system, dist.target)
            feed_id = getattr(feed, "ID", None) or f"feed_{id(feed)}"
            dist.target = feed_id
            if feed_id not in self._feeds:
                self._feeds[feed_id] = feed
                self._baseline_conc[feed_id] = _feed_concentrations(feed)
                self._baseline_flow[feed_id] = _feed_flow_m3_d(feed)
                self._comp_index[feed_id] = {
                    comp_id: idx for idx, comp_id in enumerate(_components_ids(feed))
                }
            if not dist.is_flow and dist.variable not in self._comp_index[feed_id]:
                raise LookupError(
                    f"disturbance variable {dist.variable!r} is not a component of "
                    f"feed {feed_id!r}; available: {sorted(self._comp_index[feed_id])}"
                )
        self.initialized = True

    # ------------------------------------------------------------------ apply
    def _baseline(self, feed_id: str, variable: str) -> float:
        if variable == FLOW_VARIABLE:
            return self._baseline_flow[feed_id]
        return self._baseline_conc[feed_id].get(variable, 0.0)

    def apply(self, t_h: float, system: Any) -> list[dict[str, Any]]:
        """Evaluate all disturbances at ``t_h`` and write disturbed feed state."""
        if not self.initialized:
            self.initialize(system)
        t_h = float(t_h)
        applied: list[dict[str, Any]] = []
        flow_changed_feeds: set[str] = set()

        for dist in self.disturbances:
            feed_id = dist.target
            feed = self._feeds[feed_id]
            baseline = self._baseline(feed_id, dist.variable)
            level = dist.level_at(t_h, baseline)
            realized = baseline if level is None else level

            if dist.is_flow:
                self._write_flow(feed, realized)
                if level is not None:
                    flow_changed_feeds.add(feed_id)
            else:
                self._write_concentration(feed, feed_id, dist.variable, realized)

            applied.append(
                {
                    "time_h": t_h,
                    "time_days": t_h / 24.0,
                    "disturbance_id": dist.id,
                    "target": feed_id,
                    "variable": dist.variable,
                    "baseline": baseline,
                    "value": realized,
                    "active": level is not None,
                    "relative": (realized / baseline) if baseline else None,
                }
            )

        if flow_changed_feeds:
            self._restabilize(system)

        self.history.extend(applied)
        return applied

    def _write_concentration(
        self, feed: Any, feed_id: str, variable: str, value: float
    ) -> None:
        index = self._comp_index[feed_id][variable]
        state = getattr(feed, "state", None)
        if state is None:
            raise RuntimeError(
                f"feed {feed_id!r} has no dynamic state vector; cannot apply "
                "concentration disturbance (run at least one segment first)"
            )
        state[index] = float(value)

    def _write_flow(self, feed: Any, q_m3_d: float) -> None:
        q_m3_d = max(0.0, float(q_m3_d))
        # Update the thermo flow so stabilize_dynamic_hydraulics (which reads
        # F_vol) picks up the new flow. Scale total mass flow to hit Q.
        current = _feed_flow_m3_d(feed)
        if current <= 0:
            try:
                feed.set_total_flow(q_m3_d, "m3/d")
                return
            except Exception as exc:  # pragma: no cover - depends on feed type
                raise RuntimeError(
                    f"cannot set flow on feed {getattr(feed, 'ID', None)!r}: {exc}"
                ) from exc
        scale = q_m3_d / current
        try:
            feed.F_mass = float(feed.F_mass) * scale
        except Exception as exc:  # pragma: no cover - depends on feed type
            raise RuntimeError(
                f"cannot scale flow on feed {getattr(feed, 'ID', None)!r}: {exc}"
            ) from exc

    def _restabilize(self, system: Any) -> None:
        try:
            from qsdsan_engine_mcp.utils.dynamic_hydraulics import stabilize_dynamic_hydraulics
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("flow disturbances require dynamic_hydraulics") from exc
        stabilize_dynamic_hydraulics(system)

    # ----------------------------------------------------------------- report
    def as_results(self) -> dict[str, Any]:
        feeds_summary = {
            feed_id: {
                "baseline_flow_m3_d": self._baseline_flow.get(feed_id),
                "baseline_concentrations_mg_l": self._baseline_conc.get(feed_id, {}),
            }
            for feed_id in self._feeds
        }
        return {
            "enabled": True,
            "disturbances": [dist.as_dict() for dist in self.disturbances],
            "feeds": feeds_summary,
            "history": self.history,
            "note": (
                "Feed disturbances are applied at controller-sample boundaries. "
                "Concentration disturbances write feed.state directly; flow "
                "disturbances re-stabilize the dynamic hydraulics. The realized "
                "feed trajectory is in 'history'; pair it with tracked effluent "
                "timeseries to read the transient KPI response."
            ),
        }


def build_disturbance_scheduler(spec: Any) -> DisturbanceScheduler | None:
    """Build a :class:`DisturbanceScheduler` from a JSON-like spec.

    Accepts a mapping ``{"disturbances": [ ... ]}``, a bare list of disturbance
    specs, or a single disturbance mapping. Returns ``None`` for an empty spec.
    """
    if spec is None or spec is False:
        return None
    if isinstance(spec, DisturbanceScheduler):
        return spec
    if isinstance(spec, Mapping):
        items = spec.get("disturbances", spec.get("schedule"))
        if items is None and "variable" in spec:
            items = [spec]
    elif isinstance(spec, Sequence) and not isinstance(spec, (str, bytes, bytearray)):
        items = spec
    else:
        raise TypeError(
            "disturbance_schedule must be a mapping, sequence, or DisturbanceScheduler"
        )
    if not items:
        return None

    disturbances: list[Disturbance] = []
    allowed = {"id", "target", "variable", "type", "start_h", "end_h", "value", "mode"}
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TypeError("each disturbance must be a mapping")
        kwargs = {key: value for key, value in item.items() if key in allowed}
        if "variable" not in kwargs:
            raise ValueError(f"disturbance #{index + 1} is missing 'variable'")
        disturbances.append(Disturbance(**kwargs))
    return DisturbanceScheduler(disturbances)


__all__ = [
    "Disturbance",
    "DisturbanceScheduler",
    "build_disturbance_scheduler",
    "FLOW_VARIABLE",
]
