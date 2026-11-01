"""Full-state checkpoint / restart for QSDsan dynamic twins.

QSDsan's live dynamic trajectory is *not* just stream states. It is the
assembled ``System._state`` vector, each dynamic unit's private ``_state`` /
``_dstate`` arrays (the unit ``_state`` is a *view* into the system vector,
``biosteam/_system.py``), the stream input buffers (``WasteStream.state`` for
non-product streams resolves to ``sink._ins_QC[i]``), the controller /
disturbance object state, and the dynamic-hydraulics monkey-patch fields
installed by :func:`utils.dynamic_hydraulics.stabilize_dynamic_hydraulics`.

If ``System._state`` is absent on reload, ``System._load_state`` calls every
unit's ``_init_state()`` and the twin re-equilibrates from scratch (~150
simulated days for a converged 7-unit train). This module captures and restores
the *entire* private dynamic state so a converged twin resumes a transient with
no re-equilibration.

This module is the single place that touches the QSDsan/BioSTEAM private
attributes. Capture produces a JSON-able payload (numpy arrays -> lists);
restore rebuilds float64 arrays and re-links the views directly.

Public API
----------
``capture_dynamic_checkpoint(system, *, time_days, ...) -> dict``
    Snapshot the full dynamic state into a JSON-able payload.
``restore_dynamic_checkpoint(system, checkpoint, *, ...) -> dict``
    Rebuild the dynamic state onto a freshly-compiled system (no re-equilibration).
``build_fingerprint(system) -> dict``
    ``unit_order`` + ``recycle_stream_ids`` + ``connections_hash`` topology guard.
``validate_checkpoint_header(system, checkpoint, *, strict=True) -> list[str]``
    Validate schema version / topology / per-unit components; raise on mismatch.
``capture_control_state`` / ``restore_control_state``
``capture_disturbance_state`` / ``restore_disturbance_state``
    Controller and feed-disturbance object state (used by the resume driver).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "qsdsan_full_state_checkpoint/v1"

# Runtime attributes installed on dynamic units by the hydraulic guard and the
# sampled-data aeration actuator. These are the JSON-able scalar/list fields the
# guarded ODE and the frozen-hydraulics update read at integration time; they
# must round-trip so the first resumed segment uses the exact held values. The
# saved original-method handles (``_codex_original_*``) and live QSDsan objects
# (``_codex_diffused_aeration``) are intentionally NOT captured -- they are
# re-derived by ``stabilize_dynamic_hydraulics`` on the fresh system.
_CODEX_RUNTIME_ATTRS = (
    "_codex_fixed_hydraulic_inflow_m3_d",
    "_codex_fixed_hydraulic_outflows_m3_d",
    "_codex_dynamic_kLa_d",
    "_codex_dynamic_Q_air_m3_d",
    "_codex_do_control_DO_ID",
    "_codex_do_control_DO_sat_mg_L",
    "_codex_do_control_mode",
    "_codex_do_control_actuator_id",
    "_codex_dynamic_control_attached",
    "_DO_ID",
)

class StateCheckpointError(RuntimeError):
    """Raised on schema/topology/component mismatch or malformed checkpoints."""


# mADM1 gas-phase round-trip: the AnaerobicCSTRmADM1 unit ``_state`` is
# ``[liquid (len components) | gas (_n_gas) | Q]`` (``models/reactors.py:89,98``;
# QSDsan upstream uses ``y[n_cmps:(n_cmps + n_gas)]`` for the headspace). The
# local reactor supports a variable gas-species count (3 for ADM1, 4 for mADM1
# which adds S_IS), so the gas slice must be DERIVED from the unit attrs, never
# hardcoded -- a literal 63:67 silently reads liquid components if the model's
# component count changes.
def _madm1_gas_slice(unit: Any) -> slice:
    n_cmps = len(getattr(unit, "components", []) or [])
    n_gas = int(getattr(unit, "_n_gas", 3) or 3)
    return slice(n_cmps, n_cmps + n_gas)


def _madm1_gas_ids(unit: Any) -> List[str]:
    model = getattr(unit, "_model", None) or getattr(unit, "model", None)
    return list(getattr(model, "_biogas_IDs", ()) or ())


def apply_madm1_gas_seed(
    unit: Any,
    gas_seed_kmol_m3: Dict[str, float],
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    """Seed an ``AnaerobicCSTRmADM1`` unit's headspace (gas) slice (kmol/m3).

    The WaterTAP -> QSDsan seed bridge (roadmap C1) converts a WaterTAP mADM1
    steady solve's converged headspace into the QSDsan gas-slice basis
    (kmol/m3; ``p_i = R*T*S_gas_i``, ``models/madm1.py:1053``) and applies it here
    so the dynamic run STARTS at the steady headspace instead of re-equilibrating.

    The gas slice is located via the DERIVED slice + biogas-species ordering
    (:func:`_madm1_gas_slice` / :func:`_madm1_gas_ids`), never a literal index, so
    the seed lands correctly regardless of the ADM1(3) vs mADM1(4, +S_IS)
    biogas-species count.

    Two write points cover the unit lifecycle:

    * ``_seed_gas_kmol_m3`` is stored on the unit so ``_init_state`` (run by
      ``System._load_state`` at the first simulate) builds the headspace from the
      seed rather than the cold defaults.
    * If the unit is already assembled (``_state`` populated, a live view into
      ``System._state``), the gas slice is ALSO overwritten in place so an
      already-initialised unit reflects the seed immediately.

    Returns a JSON-able report of the applied / skipped species. Species in the
    seed that are not in this unit's headspace are reported under ``skipped``,
    never silently dropped.
    """
    gas_ids = _madm1_gas_ids(unit)
    if not gas_ids:
        msg = (
            f"unit {getattr(unit, 'ID', '?')!r} is not an mADM1 reactor "
            "(no _biogas_IDs); cannot seed gas phase"
        )
        if strict:
            raise StateCheckpointError(msg)
        return {
            "applied": {},
            "skipped": list((gas_seed_kmol_m3 or {}).keys()),
            "warning": msg,
        }

    applied: Dict[str, float] = {}
    skipped: List[str] = []
    stored = dict(getattr(unit, "_seed_gas_kmol_m3", {}) or {})
    for gid, value in (gas_seed_kmol_m3 or {}).items():
        if gid not in gas_ids:
            skipped.append(gid)
            continue
        if value is None:
            continue
        fval = float(value)
        stored[gid] = fval
        applied[gid] = fval
    unit._seed_gas_kmol_m3 = stored

    # Direct in-place write for an already-assembled unit (state is a live view
    # into System._state, so this keeps the system vector consistent).
    state = getattr(unit, "_state", None)
    if state is not None and applied:
        gslice = _madm1_gas_slice(unit)
        arr = np.asarray(state, dtype=float)
        if arr.size >= gslice.stop:
            for i, gid in enumerate(gas_ids):
                if gid in applied:
                    unit._state[gslice.start + i] = applied[gid]

    return {
        "applied": applied,
        "skipped": skipped,
        "gas_component_ids": list(gas_ids),
    }


# --------------------------------------------------------------------------- json
def _to_jsonable(value: Any) -> Any:
    """Recursively convert numpy/tuples to JSON-able Python containers."""
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return value.astype(float).tolist()
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _to_list(arr: Any) -> Optional[List[float]]:
    if arr is None:
        return None
    return np.asarray(arr, dtype=float).tolist()


def _to_array(values: Any) -> np.ndarray:
    return np.asarray(values, dtype="float64")


# ----------------------------------------------------------------- fingerprint
def build_fingerprint(system: Any) -> Dict[str, Any]:
    """Topology fingerprint: unit order, recycle stream ids, connections hash."""
    unit_order = [getattr(u, "ID", None) for u in getattr(system, "units", [])]

    recycle = getattr(system, "recycle", None)
    if recycle is None:
        recycle_ids: List[str] = []
    elif isinstance(recycle, (list, tuple, set)):
        recycle_ids = sorted(getattr(r, "ID", str(r)) for r in recycle if r is not None)
    else:
        recycle_ids = [getattr(recycle, "ID", str(recycle))]

    connections: List[Any] = []
    for unit in getattr(system, "units", []):
        uid = getattr(unit, "ID", None)
        for i, stream in enumerate(getattr(unit, "ins", []) or []):
            if stream is None:
                continue
            src = getattr(getattr(stream, "_source", None), "ID", None)
            connections.append(["in", getattr(stream, "ID", None), src, uid, i])
        for j, stream in enumerate(getattr(unit, "outs", []) or []):
            if stream is None:
                continue
            snk = getattr(getattr(stream, "_sink", None), "ID", None)
            connections.append(["out", getattr(stream, "ID", None), uid, snk, j])
    blob = json.dumps(connections, sort_keys=True, default=str)
    connections_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return {
        "unit_order": unit_order,
        "recycle_stream_ids": recycle_ids,
        "connections_hash": connections_hash,
    }


def validate_checkpoint_header(
    system: Any, checkpoint: Dict[str, Any], *, strict: bool = True
) -> List[str]:
    """Validate schema version, topology fingerprint, and per-unit components."""
    errors: List[str] = []

    version = checkpoint.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: {version!r} != {SCHEMA_VERSION!r}")

    fp_now = build_fingerprint(system)
    fp_ckpt = checkpoint.get("build_fingerprint", {}) or {}
    if fp_now.get("unit_order") != fp_ckpt.get("unit_order"):
        errors.append(
            f"unit_order mismatch (topology changed): "
            f"{fp_now.get('unit_order')} != {fp_ckpt.get('unit_order')}"
        )
    if fp_now.get("connections_hash") != fp_ckpt.get("connections_hash"):
        errors.append("connections_hash mismatch (flowsheet wiring changed)")
    # The recycle tear set defines the fixed point the dynamic state converged
    # to; a different tear set restarts on a different fixed point even when the
    # unit order/wiring match. Compare order-insensitively.
    recycle_now = set(fp_now.get("recycle_stream_ids") or [])
    recycle_ckpt = set(fp_ckpt.get("recycle_stream_ids") or [])
    if recycle_now != recycle_ckpt:
        errors.append(
            f"recycle_stream_ids mismatch (tear set changed): "
            f"{sorted(recycle_now)} != {sorted(recycle_ckpt)}"
        )

    unit_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", [])}
    for uid, udata in (checkpoint.get("units") or {}).items():
        unit = unit_by_id.get(uid)
        if unit is None:
            errors.append(f"checkpoint unit {uid!r} not present in system")
            continue
        current = list(getattr(unit.components, "IDs", []))
        captured = list(udata.get("component_ids", []))
        if current != captured:
            errors.append(f"unit {uid!r} component_ids mismatch (model changed)")

    if errors and strict:
        raise StateCheckpointError(
            "checkpoint header validation failed: " + "; ".join(errors)
        )
    return errors


# ----------------------------------------------------------------- stream state
def _safe_stream_state(stream: Any) -> Optional[np.ndarray]:
    try:
        state = stream.state
    except Exception:
        return None
    if state is None:
        return None
    try:
        return np.asarray(state, dtype=float)
    except Exception:
        return None


def _safe_stream_dstate(stream: Any) -> Optional[np.ndarray]:
    try:
        dstate = stream.dstate
    except Exception:
        return None
    if dstate is None:
        return None
    try:
        return np.asarray(dstate, dtype=float)
    except Exception:
        return None


def _find_stream(system: Any, stream_id: str) -> Any:
    for stream in getattr(system, "streams", []) or []:
        if stream is not None and getattr(stream, "ID", None) == stream_id:
            return stream
    return None


def _restore_stream(stream: Any, payload: Dict[str, Any]) -> None:
    state = payload.get("state")
    if state is not None:
        stream.state = _to_array(state)
    dstate = payload.get("dstate")
    if dstate is not None:
        stream.dstate = _to_array(dstate)


def _capture_stream_thermo(stream: Any) -> Dict[str, Any]:
    """Snapshot the thermo (molar) flows + T/P/phase used as the recycle guess."""
    thermo: Dict[str, Any] = {}
    try:
        thermo["mol"] = np.asarray(stream.mol, dtype=float).tolist()
    except Exception:
        thermo["mol"] = None
    for attr in ("T", "P"):
        try:
            thermo[attr] = float(getattr(stream, attr))
        except Exception:
            thermo[attr] = None
    try:
        thermo["phase"] = str(stream.phase)
    except Exception:
        thermo["phase"] = None
    return thermo


def _restore_stream_thermo(stream: Any, thermo: Dict[str, Any]) -> None:
    """Restore the thermo (molar) flows + T/P so re-convergence is deterministic.

    The recycle solver inside ``System.converge`` (invoked by the resume driver's
    ``stabilize_dynamic_hydraulics``) uses the current stream molar flows as its
    fixed-point guess. Restoring the exact thermo makes the resumed convergence --
    and therefore the frozen hydraulic flows -- bit-identical to the in-memory
    continuation.
    """
    if not thermo:
        return
    phase = thermo.get("phase")
    if phase is not None:
        try:
            stream.phase = phase
        except Exception:
            pass
    for attr in ("T", "P"):
        value = thermo.get(attr)
        if value is not None:
            try:
                setattr(stream, attr, float(value))
            except Exception:
                pass
    mol = thermo.get("mol")
    if mol is not None:
        try:
            stream.mol[:] = _to_array(mol)
        except Exception:
            try:
                stream.mol = _to_array(mol)
            except Exception:
                pass


# ----------------------------------------------------------------- runtime attrs
def _capture_runtime_attrs(unit: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for attr in _CODEX_RUNTIME_ATTRS:
        if hasattr(unit, attr):
            out[attr] = _to_jsonable(getattr(unit, attr))
    return out


def _restore_runtime_attrs(unit: Any, attrs: Dict[str, Any]) -> None:
    for attr, value in attrs.items():
        if attr == "_codex_fixed_hydraulic_outflows_m3_d" and value is not None:
            setattr(unit, attr, [float(x) for x in value])
        else:
            setattr(unit, attr, value)


# ----------------------------------------------------------------- control state
def capture_control_state(manager: Any) -> Optional[Dict[str, Any]]:
    """Snapshot every PID/Sensor/Actuator/ControlLoop internal for a manager."""
    if manager is None:
        return None
    loops: List[Dict[str, Any]] = []
    for loop in getattr(manager, "loops", []) or []:
        c = loop.controller
        s = loop.sensor
        a = loop.actuator
        rng_state = None
        if getattr(s, "noise_std", 0.0):
            try:
                rng_state = list(s._rng.getstate())
            except Exception:
                rng_state = None
        loops.append(
            {
                "id": loop.id,
                "controller": {
                    "integral": c.integral,
                    "last_output": c.last_output,
                    "last_update_time_h": c.last_update_time_h,
                    "last_measurement": c.last_measurement,
                    "last_error": c.last_error,
                    "manual_output": c.manual_output,
                    "manual_mode": c.manual_mode,
                    "output_bias": c.output_bias,
                    "tracking_time_h": c.tracking_time_h,
                    "saturated": c.saturated,
                    "lower_saturated": c.lower_saturated,
                    "upper_saturated": c.upper_saturated,
                },
                "sensor": {
                    "last_sample_time_h": s.last_sample_time_h,
                    "last_raw_value": s.last_raw_value,
                    "last_measurement": s.last_measurement,
                    "last_good_value": s.last_good_value,
                    "last_valid": s.last_valid,
                    "rng_state": _to_jsonable(rng_state),
                },
                "actuator": {
                    "held_value": a.held_value,
                    "last_apply_time_h": a.last_apply_time_h,
                    "total_time_h": a.total_time_h,
                    "saturation_time_h": a.saturation_time_h,
                    "lower_saturation_time_h": a.lower_saturation_time_h,
                    "upper_saturation_time_h": a.upper_saturation_time_h,
                    "lower_saturated": a.lower_saturated,
                    "upper_saturated": a.upper_saturated,
                    "saturated": a.saturated,
                },
                "history": [dict(r) for r in getattr(loop, "history", [])],
            }
        )
    return {"initialized": bool(getattr(manager, "initialized", False)), "loops": loops}


def restore_control_state(manager: Any, payload: Optional[Dict[str, Any]]) -> None:
    """Overlay captured controller state onto a freshly-built control manager."""
    if manager is None or not payload:
        return
    by_id = {lp["id"]: lp for lp in payload.get("loops", [])}
    for loop in getattr(manager, "loops", []) or []:
        data = by_id.get(loop.id)
        if data is None:
            continue
        cs = data["controller"]
        c = loop.controller
        c.integral = cs["integral"]
        c.last_output = cs["last_output"]
        c.last_update_time_h = cs["last_update_time_h"]
        c.last_measurement = cs["last_measurement"]
        c.last_error = cs["last_error"]
        c.manual_output = cs["manual_output"]
        c.manual_mode = cs["manual_mode"]
        c.output_bias = cs["output_bias"]
        c.tracking_time_h = cs["tracking_time_h"]
        c.saturated = cs["saturated"]
        c.lower_saturated = cs["lower_saturated"]
        c.upper_saturated = cs["upper_saturated"]

        ss = data["sensor"]
        s = loop.sensor
        s.last_sample_time_h = ss["last_sample_time_h"]
        s.last_raw_value = ss["last_raw_value"]
        s.last_measurement = ss["last_measurement"]
        s.last_good_value = ss["last_good_value"]
        s.last_valid = ss["last_valid"]
        rng_state = ss.get("rng_state")
        if rng_state is not None:
            try:
                version, internal, gauss_next = rng_state
                s._rng.setstate((int(version), tuple(int(x) for x in internal), gauss_next))
            except Exception:
                logger.debug("Could not restore sensor RNG state for loop %s", loop.id)

        a_state = data["actuator"]
        a = loop.actuator
        a.held_value = a_state["held_value"]
        a.last_apply_time_h = a_state["last_apply_time_h"]
        a.total_time_h = a_state["total_time_h"]
        a.saturation_time_h = a_state["saturation_time_h"]
        a.lower_saturation_time_h = a_state["lower_saturation_time_h"]
        a.upper_saturation_time_h = a_state["upper_saturation_time_h"]
        a.lower_saturated = a_state["lower_saturated"]
        a.upper_saturated = a_state["upper_saturated"]
        a.saturated = a_state["saturated"]

        loop.history = [dict(r) for r in data.get("history", [])]
    manager.initialized = bool(payload.get("initialized", True))


# Actuator bookkeeping fields that ``Actuator.apply`` mutates (held command +
# saturation statistics). ``apply_held_value_on_restore`` snapshots and rolls
# these back so re-pushing the side effect onto the hardware does not perturb the
# stats that ``restore_control_state`` already overlaid.
_ACTUATOR_STATS_FIELDS = (
    "held_value",
    "last_apply_time_h",
    "total_time_h",
    "saturation_time_h",
    "lower_saturation_time_h",
    "upper_saturation_time_h",
    "lower_saturated",
    "upper_saturated",
    "saturated",
)


def apply_held_value_on_restore(
    manager: Any, system: Any, *, strict: bool = True
) -> List[str]:
    """Re-apply each restored actuator's held command to its process hardware.

    ``restore_control_state`` overlays only the controller/actuator *bookkeeping*
    (``held_value``, saturation timers). It does NOT re-create the actuator's
    process-side SIDE EFFECTS, which a freshly-built system carries at
    construction defaults:

    * ``AerationActuator`` -> ``_codex_dynamic_kLa_d`` (already covered by the
      runtime-attr replay, but re-applied here too for uniformity/idempotence).
    * ``WASActuator`` -> ``utils.srt_control.update_wastage_actuator`` mutates
      ``unit.pumped_flow`` / splitter ``split`` / clarifier ``wastage``.
    * ``DoseFlowActuator`` -> writes a stream/unit attribute (dose flow / conc).

    Without this, a restored SRT/dosing twin resumes with the held command living
    in the controller object but FRESH-BUILD hardware -- so the next-segment
    hydraulics (which ``stabilize_dynamic_hydraulics`` derives from
    ``pumped_flow``/``split``/``wastage`` and ``F_vol``) are wrong. This pushes
    the held command back onto the hardware, then restores the actuator stats so
    the replay is bookkeeping-neutral.

    Must run AFTER ``restore_control_state`` (needs ``held_value``) and BEFORE the
    resume driver's ``stabilize_dynamic_hydraulics`` so the frozen hydraulics see
    the corrected hardware.
    """
    if manager is None:
        return []
    reapplied: List[str] = []
    for loop in getattr(manager, "loops", []) or []:
        actuator = getattr(loop, "actuator", None)
        if actuator is None:
            continue
        held = getattr(actuator, "held_value", None)
        if held is None:
            continue
        snapshot = {
            field: getattr(actuator, field)
            for field in _ACTUATOR_STATS_FIELDS
            if hasattr(actuator, field)
        }
        # Apply at the actuator's own last_apply_time so any stats delta is zero;
        # the snapshot is restored unconditionally regardless.
        t_h = getattr(actuator, "last_apply_time_h", None)
        t_h = 0.0 if t_h is None else float(t_h)
        try:
            actuator.apply(t_h, float(held), system)
            reapplied.append(getattr(actuator, "id", None) or getattr(loop, "id", ""))
        except Exception as exc:
            if strict:
                raise StateCheckpointError(
                    f"could not re-apply held command for loop "
                    f"{getattr(loop, 'id', '?')!r}: {exc}"
                ) from exc
            logger.warning(
                "skipped held-command re-apply for loop %s on restore: %s",
                getattr(loop, "id", "?"),
                exc,
            )
        finally:
            for field, value in snapshot.items():
                setattr(actuator, field, value)
    return reapplied


# ------------------------------------------------------------- disturbance state
def capture_disturbance_state(scheduler: Any) -> Optional[Dict[str, Any]]:
    """Snapshot feed-disturbance baselines, component index, and history."""
    if scheduler is None:
        return None
    return {
        "initialized": bool(getattr(scheduler, "initialized", False)),
        "baseline_conc": _to_jsonable(getattr(scheduler, "_baseline_conc", {})),
        "baseline_flow": _to_jsonable(getattr(scheduler, "_baseline_flow", {})),
        "comp_index": _to_jsonable(getattr(scheduler, "_comp_index", {})),
        "history": [dict(r) for r in getattr(scheduler, "history", [])],
        "disturbance_targets": {
            d.id: d.target for d in getattr(scheduler, "disturbances", [])
        },
    }


def restore_disturbance_state(
    scheduler: Any, payload: Optional[Dict[str, Any]], system: Any
) -> None:
    """Re-bind a freshly-built scheduler to the new system and overlay state.

    ``DisturbanceScheduler.initialize`` re-resolves each disturbance's feed on the
    fresh system. For an IMPLICIT target (``dist.target is None``) the resolver
    picks the LARGEST feed (``core.disturbance``), so on a multi-feed system a
    fresh build could silently bind the disturbance to a different feed than the
    one the checkpoint converged against. Re-pin each disturbance to its CAPTURED
    resolved feed id before ``initialize`` so the rebinding is exact, and fail
    loudly if that feed is absent in the fresh system.
    """
    if scheduler is None or not payload:
        return

    captured_targets = payload.get("disturbance_targets") or {}
    if captured_targets:
        feed_ids = {
            getattr(f, "ID", None)
            for f in (getattr(system, "feeds", None) or [])
            if f is not None
        }
        inlet_ids = {
            getattr(s, "ID", None)
            for unit in (getattr(system, "units", []) or [])
            for s in (getattr(unit, "ins", []) or [])
            if s is not None
        }
        resolvable = {x for x in (feed_ids | inlet_ids) if x is not None}
        for dist in getattr(scheduler, "disturbances", []) or []:
            captured = captured_targets.get(getattr(dist, "id", None))
            if captured is None:
                continue
            if captured not in resolvable:
                raise StateCheckpointError(
                    f"disturbance {getattr(dist, 'id', '?')!r} captured feed target "
                    f"{captured!r} is absent in the fresh system "
                    f"(available feeds: {sorted(x for x in feed_ids if x)})"
                )
            # Pin the explicit target so initialize() binds to the exact feed
            # instead of re-resolving an implicit (largest-feed) default.
            dist.target = captured

    # Re-resolve feed references on the new system (the old feed objects are gone).
    scheduler.initialize(system)
    # Overlay the captured baselines/index (deterministic, but make it exact).
    baseline_conc = payload.get("baseline_conc")
    if isinstance(baseline_conc, dict):
        scheduler._baseline_conc = {
            feed_id: {k: float(v) for k, v in (conc or {}).items()}
            for feed_id, conc in baseline_conc.items()
        }
    baseline_flow = payload.get("baseline_flow")
    if isinstance(baseline_flow, dict):
        scheduler._baseline_flow = {k: float(v) for k, v in baseline_flow.items()}
    comp_index = payload.get("comp_index")
    if isinstance(comp_index, dict):
        scheduler._comp_index = {
            feed_id: {k: int(v) for k, v in (idx or {}).items()}
            for feed_id, idx in comp_index.items()
        }
    scheduler.history = [dict(r) for r in payload.get("history", [])]
    scheduler.initialized = True


# --------------------------------------------------------------------- capture
def capture_dynamic_checkpoint(
    system: Any,
    *,
    time_days: float,
    system_id: Optional[str] = None,
    model_type: Optional[str] = None,
    control_manager: Any = None,
    disturbance_scheduler: Any = None,
) -> Dict[str, Any]:
    """Capture the full dynamic state of a live, integrated QSDsan system.

    Parameters
    ----------
    system :
        A compiled, dynamic, already-integrated BioSTEAM/QSDsan ``System``
        (``system._state`` must be populated -- run at least one segment first).
    time_days :
        The absolute simulated time (days) at the checkpoint. Stored as both
        ``time_days`` and ``time_h`` so the resume driver can run an absolute
        ``t_span``.
    control_manager / disturbance_scheduler :
        Optional live ``DynamicControlManager`` / ``DisturbanceScheduler``; their
        internal object state is snapshotted so resume does not re-initialize
        (which would re-apply a t=0 boundary action -- the double-application
        trap).
    """
    if not getattr(system, "isdynamic", False):
        raise StateCheckpointError("cannot checkpoint a non-dynamic system")
    sys_state = getattr(system, "_state", None)
    if sys_state is None:
        raise StateCheckpointError(
            "system._state is None; run at least one dynamic segment before checkpointing "
            "(an uninitialized system has nothing to snapshot)"
        )
    state_idx = getattr(system, "_state_idx", None)
    if not state_idx:
        raise StateCheckpointError("system._state_idx is empty; system not assembled")

    units: Dict[str, Any] = {}
    for unit in getattr(system, "units", []):
        if not getattr(unit, "hasode", False):
            continue
        u_state = getattr(unit, "_state", None)
        if u_state is None:
            continue
        cls = type(unit).__name__
        entry = {
            "class": cls,
            "state": _to_list(unit._state),
            "dstate": _to_list(getattr(unit, "_dstate", None)),
            "state_header": list(getattr(unit, "_state_header", []) or []),
            "component_ids": list(getattr(unit.components, "IDs", [])),
            "runtime_attrs": _capture_runtime_attrs(unit),
        }
        if cls == "AnaerobicCSTRmADM1":
            gslice = _madm1_gas_slice(unit)
            gas = np.asarray(unit._state, dtype=float)[gslice]
            entry["gas_slice"] = gas.tolist()
            entry["gas_slice_bounds"] = [gslice.start, gslice.stop]
            entry["gas_component_ids"] = _madm1_gas_ids(unit)
        units[unit.ID] = entry

    streams: Dict[str, Any] = {}
    for stream in getattr(system, "streams", []) or []:
        if stream is None:
            continue
        state = _safe_stream_state(stream)
        if state is None:
            continue
        streams[stream.ID] = {
            "state": state.tolist(),
            "dstate": _to_list(_safe_stream_dstate(stream)),
            "components": list(getattr(stream.components, "IDs", [])),
            "thermo_flow_m3_d": float(getattr(stream, "F_vol", 0.0) or 0.0) * 24.0,
            "thermo": _capture_stream_thermo(stream),
        }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "time_h": float(time_days) * 24.0,
        "time_days": float(time_days),
        "system_id": system_id or getattr(system, "ID", None),
        "model_type": model_type,
        "build_fingerprint": build_fingerprint(system),
        "system": {
            "state": np.asarray(sys_state, dtype=float).tolist(),
            "state_idx": {uid: [int(a), int(b)] for uid, (a, b) in state_idx.items()},
        },
        "units": units,
        "streams": streams,
        "dynamic_controls": capture_control_state(control_manager),
        "disturbance_scheduler": capture_disturbance_state(disturbance_scheduler),
    }
    return payload


# --------------------------------------------------------------------- restore
def restore_dynamic_checkpoint(
    system: Any,
    checkpoint: Dict[str, Any],
    *,
    control_manager: Any = None,
    disturbance_scheduler: Any = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """Restore a full dynamic checkpoint onto a freshly-compiled system.

    The system must already be compiled (units created, wired, ``System``
    assembled) but need not have been simulated. After this call the system is
    ready to resume integration with NO re-equilibration. The first resumed
    ``system.simulate(...)`` MUST pass ``state_reset_hook=None`` (never
    ``"reset_cache"``) -- the resume driver handles that.

    Restore ordering (matches the design blueprint):

    1. Validate the checkpoint header (schema / topology / components).
    2. ``system.reset_cache()`` -- re-init dynamic buffers (``_ins_QC``) to the
       correct shapes for the final wiring; nulls states/DAE.
    3. ``stabilize_dynamic_hydraulics(system)`` -- install the frozen-hydraulics
       guards so the monkey-patch fields exist.
    4. Overlay captured ``_codex_*`` runtime attrs (frozen flows + held kLa/DO).
    5. Restore controller object state (no re-init -> no double boundary action).
    6. Restore disturbance scheduler (re-bind feeds + overlay baselines/history).
    7. ``system._load_state()`` cold pass -- structurally builds ``System._state``
       / ``_state_idx``, links each dynamic ``unit._state`` as a VIEW into the
       system vector, and sets up algebraic-unit buffers (e.g. a Splitter's
       ``_split_out0_state``). Cold guesses only -- no integration.
    8. Overwrite ``System._state`` (in place) and each ``unit._dstate`` with the
       restored trajectory; assert the slice/gas-slice round-trip.
    9. Restore stream states + thermo (molar flows) so the resume-start
       convergence lands on the identical recycle fixed point.
    9b. Re-apply each restored actuator's held command to its process hardware
        (WAS pumped_flow/split/wastage, dose flow/conc, aeration kLa) so the
        resume-start hydraulics derive from the correct hardware. Stats-neutral.
    10. Null ``system._DAE`` and each ``unit._ODE`` (stale closures capture old
        arrays) so they recompile against the restored state.
    """
    from qsdsan_engine_mcp.utils.dynamic_hydraulics import stabilize_dynamic_hydraulics

    if not getattr(system, "isdynamic", False):
        raise StateCheckpointError("cannot restore onto a non-dynamic system")

    # 1. header validation
    validate_checkpoint_header(system, checkpoint, strict=strict)

    units_payload: Dict[str, Any] = checkpoint.get("units") or {}
    unit_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", [])}

    # 2. re-init dynamic buffers to correct shapes; nulls states + DAE
    if hasattr(system, "reset_cache"):
        system.reset_cache()

    # 3. install frozen-hydraulics guards (converges + monkey-patches CSTR/MBR)
    stabilize_dynamic_hydraulics(system)

    # 4. overlay captured runtime attrs (exact frozen flows + held aeration)
    for uid, udata in units_payload.items():
        unit = unit_by_id.get(uid)
        if unit is None:
            continue
        _restore_runtime_attrs(unit, udata.get("runtime_attrs", {}))

    # 5. controller object state (skip re-init / boundary re-apply)
    if control_manager is not None:
        restore_control_state(control_manager, checkpoint.get("dynamic_controls"))

    # 6. disturbance scheduler (re-bind feeds to the new system, overlay state)
    if disturbance_scheduler is not None:
        restore_disturbance_state(
            disturbance_scheduler, checkpoint.get("disturbance_scheduler"), system
        )

    # 7. structural set-up via the cold load path. ``System._load_state`` (cold,
    #    because reset_cache nulled ``_state``) runs every unit's ``_init_state``
    #    -- this is what allocates each algebraic unit's private buffers (e.g. a
    #    Splitter's ``_split_out0_state``, referenced by its AE/_update_dstate),
    #    builds ``System._state`` + ``_state_idx``, and links each dynamic
    #    ``unit._state`` as a VIEW into the system vector. It only writes cold
    #    *guesses* (no integration -> no re-equilibration); we overwrite them
    #    with the restored trajectory next.
    system._load_state()
    sys_idx = {uid: (int(a), int(b)) for uid, (a, b) in system._state_idx.items()}

    sys_payload = checkpoint.get("system") or {}
    restored_vec = _to_array(sys_payload.get("state"))
    if system._state.size != restored_vec.size:
        raise StateCheckpointError(
            f"system state length {restored_vec.size} != freshly assembled "
            f"{system._state.size}; topology/dynamic-set drift"
        )
    captured_idx = {
        uid: (int(a), int(b)) for uid, (a, b) in sys_payload.get("state_idx", {}).items()
    }
    if sys_idx != captured_idx:
        raise StateCheckpointError(
            f"state_idx mismatch on restore: {sys_idx} != {captured_idx} "
            "(unit ordering / dynamic-unit set changed)"
        )
    # 8. overwrite the assembled system vector + per-unit dstates IN PLACE so the
    #    views built by _load_state stay valid (reassigning would orphan them).
    system._state[:] = restored_vec

    restored_units: List[str] = []
    for uid, udata in units_payload.items():
        unit = unit_by_id.get(uid)
        if unit is None or uid not in sys_idx:
            if strict:
                raise StateCheckpointError(f"checkpoint unit {uid!r} missing on restore")
            continue
        start, stop = sys_idx[uid]
        captured_state = _to_array(udata["state"])
        if (stop - start) != captured_state.size:
            raise StateCheckpointError(
                f"unit {uid!r} state length {captured_state.size} != slice "
                f"{stop - start}; topology/component drift"
            )
        # unit._state is the view into system._state[start:stop]; it now reflects
        # the restored vector. Verify the slice equals the captured unit state.
        if not np.array_equal(unit._state, captured_state):
            raise StateCheckpointError(
                f"unit {uid!r} state inconsistent with system._state slice"
            )
        # In-place dstate overwrite keeps the array object the guarded ODE/AE
        # closures captured (belt-and-suspenders; _ODE is nulled below anyway).
        restored_dstate = _to_array(udata["dstate"])
        if unit._dstate.size != restored_dstate.size:
            raise StateCheckpointError(f"unit {uid!r} dstate length mismatch")
        unit._dstate[:] = restored_dstate
        header = udata.get("state_header")
        if header:
            unit._state_header = list(header)

        # mADM1 gas-slice round-trip assertion (liquid|gas|Q). Derive the slice
        # from the fresh unit's component count + _n_gas so it survives a
        # component-count change, and validate the captured biogas-species set.
        if type(unit).__name__ == "AnaerobicCSTRmADM1" and "gas_slice" in udata:
            gslice = _madm1_gas_slice(unit)
            captured_gas_ids = list(udata.get("gas_component_ids", []) or [])
            current_gas_ids = _madm1_gas_ids(unit)
            if captured_gas_ids and current_gas_ids and captured_gas_ids != current_gas_ids:
                raise StateCheckpointError(
                    f"unit {uid!r} mADM1 biogas species changed "
                    f"({current_gas_ids} != {captured_gas_ids})"
                )
            gas_now = np.asarray(unit._state, dtype=float)[gslice]
            gas_ckpt = _to_array(udata["gas_slice"])
            if not np.array_equal(gas_now, gas_ckpt):
                raise StateCheckpointError(
                    f"unit {uid!r} mADM1 gas slice failed to round-trip"
                )
            gas_dnow = np.asarray(unit._dstate, dtype=float)[gslice]
            gas_dckpt = _to_array(udata["dstate"])[gslice]
            if not np.array_equal(gas_dnow, gas_dckpt):
                raise StateCheckpointError(
                    f"unit {uid!r} mADM1 gas-slice derivative failed to round-trip"
                )
        restored_units.append(uid)

    # 9. stream input/product buffers. Done AFTER _load_state so the cold-guess
    #    propagation does not clobber the restored values. Feed-stream buffers
    #    (no upstream source) are not re-derived during integration, so restoring
    #    them is essential; internal/product buffers are restored for exact t=t0
    #    continuity (they would also be re-propagated on the first ODE step).
    restored_streams: List[str] = []
    for sid, sdata in (checkpoint.get("streams") or {}).items():
        stream = _find_stream(system, sid)
        if stream is None:
            if strict:
                raise StateCheckpointError(f"checkpoint stream {sid!r} missing on restore")
            continue
        try:
            _restore_stream(stream, sdata)
            _restore_stream_thermo(stream, sdata.get("thermo") or {})
            restored_streams.append(sid)
        except Exception as exc:
            if strict:
                raise StateCheckpointError(
                    f"failed to restore stream {sid!r}: {exc}"
                ) from exc
            logger.warning("skipped stream %s on restore: %s", sid, exc)

    # 9b. re-push each restored actuator's held command onto the process hardware
    #     (WAS pumped_flow/split/wastage, dose flow/conc, aeration kLa). Done AFTER
    #     the stream thermo restore so flow inference reads the restored flows, and
    #     BEFORE the resume driver's stabilize_dynamic_hydraulics so the frozen
    #     hydraulics see the corrected hardware. Stats-neutral (see the helper).
    reapplied_actuators: List[str] = []
    if control_manager is not None:
        reapplied_actuators = apply_held_value_on_restore(
            control_manager, system, strict=strict
        )

    # 10. drop stale compiled closures so they rebuild against restored arrays
    system._DAE = None
    for unit in getattr(system, "units", []):
        if getattr(unit, "hasode", False) and hasattr(unit, "_ODE"):
            unit._ODE = None

    return {
        "schema_version": checkpoint.get("schema_version"),
        "time_days": checkpoint.get("time_days"),
        "time_h": checkpoint.get("time_h"),
        "restored_units": restored_units,
        "restored_streams": restored_streams,
        "reapplied_actuators": reapplied_actuators,
        "controls_restored": control_manager is not None
        and bool(checkpoint.get("dynamic_controls")),
        "disturbances_restored": disturbance_scheduler is not None
        and bool(checkpoint.get("disturbance_scheduler")),
    }


__all__ = [
    "SCHEMA_VERSION",
    "StateCheckpointError",
    "apply_madm1_gas_seed",
    "build_fingerprint",
    "validate_checkpoint_header",
    "capture_dynamic_checkpoint",
    "restore_dynamic_checkpoint",
    "capture_control_state",
    "restore_control_state",
    "apply_held_value_on_restore",
    "capture_disturbance_state",
    "restore_disturbance_state",
]
