'Structured engineering calculation interface.'

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

RECONCILIATION_SCHEMA_VERSION = "qsdsan_state_reconciliation/v1"

# Component-name aliases across ASM1 / ASM2d / mASM2d so the map is model
# agnostic. The first alias present on a unit's components wins.
_DO_ALIASES = ("S_O2", "S_O")
_NH4_ALIASES = ("S_NH4", "S_NH")
_NO3_ALIASES = ("S_NO3", "S_NO")
_PO4_ALIASES = ("S_PO4",)

# QSDsan particle-size labels. ``get_TSS`` counts particulate by default and
# adds colloidal only when ``include_colloidal=True``.
_PARTICULATE_SIZE = "Particulate"
_COLLOIDAL_SIZE = "Colloidal"

# A particulate is treated as charged (and therefore held out of the MLSS
# COD-organic scale) when its ionic charge exceeds this magnitude.
_CHARGE_EPS = 1e-12

# Ideal-gas constant in bar*m3/(kmol*K); mADM1 computes headspace partial
# pressure as p_i = R * T * S_gas_i with S_gas in kmol/m3 (models/madm1.py:1053).
_R_BAR_M3_PER_KMOL_K = 0.0831446261815324

# Default bounded-projection options. These are deliberately conservative: a
# single reconciliation moves the state at most halfway to the measurement and
# never by more than 25 % of the current value.
DEFAULT_OPTIONS: Dict[str, Any] = {
    "nudge_fraction": 0.5,       # fraction of the residual to close per call
    "max_rel_change": 0.25,      # hard clip on |delta / pre| per state var/scale
    "max_abs_change": None,      # absolute step cap for near-zero baselines
    "positivity_floor": 1e-9,    # mg/L floor; states never go <= 0
    "min_rel_residual": 1e-4,    # below this relative residual, skip (no-op)
}

# Biomass component IDs that are NOT independently observable from a single bulk
# SCADA/lab measurement. They are surfaced in the map as ``estimated`` so a
# caller cannot accidentally pin an individual biomass fraction; MLSS (the
# aggregate) is the reconcilable quantity, and it is projected ratio-preserving.
_BIOMASS_IDS = {
    "ASM1": ("X_B_H", "X_B_A", "X_BH", "X_BA"),
    "ASM2d": ("X_H", "X_AUT", "X_PAO", "X_PHA", "X_PP"),
    "mASM2d": ("X_H", "X_AUT", "X_PAO", "X_PHA", "X_PP"),
    # Two-step nitrification splits X_AUT into X_AOB + X_NOB. Without these keys the
    # lookup below returns an empty set, and data assimilation would silently treat
    # both nitrifier guilds as non-biomass -- omitting exactly the populations a
    # nitrification twin exists to track.
    "mASM2d2N": ("X_H", "X_AOB", "X_NOB", "X_PAO", "X_PHA", "X_PP"),
    "mASM2d2N_N2O": ("X_H", "X_AOB", "X_NOB", "X_PAO", "X_PHA", "X_PP"),
}


class StateReconciliationError(RuntimeError):
    """Raised on malformed measurements / unmapped quantities / topology drift."""


# --------------------------------------------------------------------- helpers
def _component_ids(obj: Any) -> List[str]:
    cmps = getattr(obj, "components", None)
    if cmps is None:
        return []
    return list(getattr(cmps, "IDs", []) or [])


def _resolve_alias(component_ids: Sequence[str], aliases: Sequence[str]) -> Optional[str]:
    idset = set(component_ids)
    for alias in aliases:
        if alias in idset:
            return alias
    return None


def _state_index(unit: Any, component: str) -> Optional[int]:
    ids = _component_ids(unit)
    try:
        return ids.index(component)
    except ValueError:
        return None


def _dynamic_units(system: Any) -> List[Any]:
    return [u for u in getattr(system, "units", []) or [] if getattr(u, "hasode", False)]


def _component_attr(unit: Any, component: str, attr: str, default: float = 0.0) -> float:
    cmps = getattr(unit, "components", None)
    if cmps is None:
        return default
    cmp = getattr(cmps, component, None)
    if cmp is None:
        return default
    value = getattr(cmp, attr, default)
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _tss_sizes(include_colloidal: bool) -> Tuple[str, ...]:
    return (_PARTICULATE_SIZE, _COLLOIDAL_SIZE) if include_colloidal else (_PARTICULATE_SIZE,)


def _particulate_components(
    unit: Any, *, include_colloidal: bool = False
) -> List[Dict[str, Any]]:
    """Return the TSS-bearing particulate (and optionally colloidal) components.

    Mirrors QSDsan ``WasteStream.get_TSS`` (``sum conc * i_mass`` over particulate
    components by default; colloidal added only when requested). Each entry
    carries the unit-state index, the TSS factor (``i_mass``), the COD factor
    (``i_COD``), the phosphorus factor (``i_P``), the ionic charge (``i_charge``),
    whether the component is organic, and a ``held`` flag.

    ``held`` marks a particulate that must NOT be touched by the MLSS bulk
    COD-organic scale -- the inorganic / charged / phosphorus-storage /
    mineral-precipitate slots (``X_PP``, ``X_MeP``, ``X_MeOH``, ``X_CaCO3`` ...).
    Scaling those would silently move the stored-P, charge, or mineral inventory
    without a TP / PO4 / charge measurement. Only the COD-organic particulate
    fraction (``held == False``) is scalable.
    """
    cmps = getattr(unit, "components", None)
    if cmps is None:
        return []
    sizes = _tss_sizes(include_colloidal)
    out: List[Dict[str, Any]] = []
    for idx, cid in enumerate(_component_ids(unit)):
        cmp = getattr(cmps, cid, None)
        if cmp is None:
            continue
        psize = getattr(cmp, "particle_size", None)
        if psize not in sizes:
            continue
        organic = bool(getattr(cmp, "organic", False))
        i_charge = _component_attr(unit, cid, "i_charge", 0.0)
        # Hold any inorganic OR charged particulate out of the COD-organic scale.
        # That captures X_PP (polyphosphate storage) and every mineral precipitate
        # (CaCO3, struvite, AlPO4, FePO4, ...) -- all inorganic -- plus any charged
        # particulate, without depending on a hardcoded id blocklist.
        held = (not organic) or (abs(i_charge) > _CHARGE_EPS)
        out.append(
            {
                "component": cid,
                "index": idx,
                "i_mass": _component_attr(unit, cid, "i_mass", 0.0),
                "i_cod": _component_attr(unit, cid, "i_COD", 0.0),
                "i_p": _component_attr(unit, cid, "i_P", 0.0),
                "i_charge": i_charge,
                "organic": organic,
                "held": held,
            }
        )
    return out


def _mlss_breakdown(unit: Any, *, include_colloidal: bool = False) -> Dict[str, Any]:
    """Decompose the live unit dynamic state into MLSS / scalable / held parts.

    Returns the total MLSS (mg/L, matching ``get_TSS``), the scalable
    COD-organic TSS and its particulate COD, the held (charged / P / mineral)
    TSS floor and its stored-P inventory, and the per-component entries.
    """
    state = np.asarray(getattr(unit, "_state", []), dtype=float)
    parts = _particulate_components(unit, include_colloidal=include_colloidal)
    scalable_tss = 0.0
    held_tss = 0.0
    scalable_cod = 0.0
    held_p = 0.0
    for entry in parts:
        conc = float(state[entry["index"]])
        tss = conc * entry["i_mass"]
        if entry["held"]:
            held_tss += tss
            held_p += conc * entry["i_p"]
        else:
            scalable_tss += tss
            if entry["organic"]:
                scalable_cod += conc * entry["i_cod"]
    return {
        "mlss": scalable_tss + held_tss,
        "scalable_tss": scalable_tss,
        "held_tss": held_tss,
        "scalable_cod": scalable_cod,
        "held_p": held_p,
        "parts": parts,
    }


def _mlss_and_cod(unit: Any, *, include_colloidal: bool = False) -> Tuple[float, float]:
    """(MLSS mg/L, scalable particulate organic COD mg/L) from the unit state."""
    bd = _mlss_breakdown(unit, include_colloidal=include_colloidal)
    return bd["mlss"], bd["scalable_cod"]


def _is_anaerobic_madm1(unit: Any) -> bool:
    return type(unit).__name__ == "AnaerobicCSTRmADM1"


def _gas_slice(unit: Any):
    # Derive the headspace slice from the unit (n_cmps .. n_cmps+n_gas); never a
    # literal index -- the gas-species count varies (ADM1=3, mADM1=4 with S_IS).
    from qsdsan_engine_mcp.utils.state_checkpoint import _madm1_gas_slice

    return _madm1_gas_slice(unit)


def _gas_ids(unit: Any) -> List[str]:
    from qsdsan_engine_mcp.utils.state_checkpoint import _madm1_gas_ids

    return _madm1_gas_ids(unit)


def _gas_temperature(unit: Any) -> float:
    t = getattr(unit, "T", None)
    try:
        t = float(t) if t is not None else None
    except (TypeError, ValueError):
        t = None
    return t if (t and t > 0) else 298.15


def _headspace_total_pressure_bar(unit: Any) -> float:
    """Total headspace pressure (bar) = sum of partial pressures over the gas
    slice, p_i = R * T * S_gas_i (mADM1 convention, S_gas in kmol/m3)."""
    gslice = _gas_slice(unit)
    state = np.asarray(getattr(unit, "_state", []), dtype=float)
    gas = state[gslice]
    return float(_R_BAR_M3_PER_KMOL_K * _gas_temperature(unit) * np.sum(gas))


# ----------------------------------------------------------- observability map
def build_observability_map(
    system: Any,
    *,
    model_type: Optional[str] = None,
    effluent_stream_ids: Optional[Sequence[str]] = None,
    effluent_unit_ids: Optional[Sequence[str]] = None,
    include_colloidal: bool = False,
) -> Dict[str, Any]:
    """Map each measurable quantity to the twin state var(s) that realize it.

    Quantity ids
    ------------
    * ``DO@<unit>`` -- dissolved oxygen (``S_O2``/``S_O``); **direct**.
    * ``MLSS@<unit>`` -- mixed-liquor TSS (particulate-only by default, matching
      ``get_TSS(include_colloidal=False)``); **derived**, projected by scaling
      only the COD-organic particulate fraction (charged / P / mineral slots are
      held).
    * ``eff_NH4`` / ``eff_NO3`` / ``eff_PO4`` -- effluent solubles at the
      producing unit; **direct** (solubles pass an MBR membrane unchanged).
    * ``biogas:<species>`` -- mADM1 headspace gas-slice species (``S_ch4`` ...);
      **derived** (DERIVED gas slice, ``kmol/m3``).
    * ``biogas_flow`` -- **estimated** (emergent output, reported not nudged).
    * ``biomass:<comp>@<unit>`` / ``T@<unit>`` -- **estimated** only.

    ``include_colloidal`` controls the MLSS definition: ``False`` (default)
    mirrors ``get_TSS`` particulate-only; ``True`` adds colloidal.

    The map is JSON-able. Each entry's ``state_vars`` are
    ``{unit_id, component, index}`` triples giving the exact unit-state slots.
    """
    fingerprint = None
    try:
        from qsdsan_engine_mcp.utils.state_checkpoint import build_fingerprint

        fingerprint = build_fingerprint(system)
    except Exception as exc:  # pragma: no cover - fingerprint is advisory
        logger.warning("could not build observability-map fingerprint: %s", exc)
        fingerprint = None

    quantities: List[Dict[str, Any]] = []
    dyn = _dynamic_units(system)

    # ---- DO (direct) + MLSS (derived) + estimated biomass, per dynamic reactor
    biomass_set = set(_BIOMASS_IDS.get(model_type or "", ()))
    for unit in dyn:
        uid = getattr(unit, "ID", None)
        ids = _component_ids(unit)

        do_comp = _resolve_alias(ids, _DO_ALIASES)
        if do_comp is not None:
            idx = _state_index(unit, do_comp)
            quantities.append(
                {
                    "quantity_id": f"DO@{uid}",
                    "kind": "DO",
                    "observability": "direct",
                    "unit_id": uid,
                    "component": do_comp,
                    "measurement_units": "mg/L",
                    "state_vars": [{"unit_id": uid, "component": do_comp, "index": idx}],
                    "notes": "dissolved oxygen, direct state component",
                }
            )

        particulate = _particulate_components(unit, include_colloidal=include_colloidal)
        if particulate:
            scalable = [e for e in particulate if not e["held"]]
            held = [e for e in particulate if e["held"]]
            quantities.append(
                {
                    "quantity_id": f"MLSS@{uid}",
                    "kind": "MLSS",
                    "observability": "derived",
                    "unit_id": uid,
                    "measurement_units": "mg/L",
                    "include_colloidal": bool(include_colloidal),
                    "scalable_components": [e["component"] for e in scalable],
                    "held_components": [e["component"] for e in held],
                    "state_vars": [
                        {"unit_id": uid, "component": e["component"], "index": e["index"]}
                        for e in particulate
                    ],
                    "notes": (
                        "mixed-liquor TSS = sum(particulate conc * i_mass), matching "
                        "get_TSS(include_colloidal=%s); projected by scaling only the "
                        "COD-organic particulate fraction so COD/TSS closure is "
                        "conserved and the charged/P/mineral inventory is unchanged"
                        % bool(include_colloidal)
                    ),
                }
            )
            # Individual biomass fractions are NOT independently observable.
            for e in particulate:
                if e["component"] in biomass_set or (
                    e["component"].startswith(("X_B", "X_H", "X_AUT", "X_PAO"))
                ):
                    quantities.append(
                        {
                            "quantity_id": f"biomass:{e['component']}@{uid}",
                            "kind": "biomass",
                            "observability": "estimated",
                            "unit_id": uid,
                            "component": e["component"],
                            "measurement_units": "mg/L",
                            "state_vars": [
                                {"unit_id": uid, "component": e["component"], "index": e["index"]}
                            ],
                            "notes": (
                                "individual biomass fraction is estimated-only; reconcile via "
                                "the aggregate MLSS, not directly"
                            ),
                        }
                    )

        # Temperature is not an ASM/ASM2d state var (isothermal twin) -> estimated.
        quantities.append(
            {
                "quantity_id": f"T@{uid}",
                "kind": "temperature",
                "observability": "estimated",
                "unit_id": uid,
                "measurement_units": "K",
                "state_vars": [],
                "notes": "reactor temperature is a unit attribute, not a dynamic state var",
            }
        )

    # ---- effluent solubles (direct, at the producing unit)
    eff_sources = _resolve_effluent_sources(
        system, effluent_stream_ids=effluent_stream_ids, effluent_unit_ids=effluent_unit_ids
    )
    for kind, aliases, qid in (
        ("eff_NH4", _NH4_ALIASES, "eff_NH4"),
        ("eff_NO3", _NO3_ALIASES, "eff_NO3"),
        ("eff_PO4", _PO4_ALIASES, "eff_PO4"),
    ):
        for unit, stream_id in eff_sources:
            ids = _component_ids(unit)
            comp = _resolve_alias(ids, aliases)
            if comp is None:
                continue
            idx = _state_index(unit, comp)
            quantities.append(
                {
                    "quantity_id": qid,
                    "kind": kind,
                    "observability": "direct",
                    "unit_id": getattr(unit, "ID", None),
                    "stream_id": stream_id,
                    "component": comp,
                    "measurement_units": "mg/L",
                    "state_vars": [
                        {"unit_id": getattr(unit, "ID", None), "component": comp, "index": idx}
                    ],
                    "notes": "effluent soluble nutrient; solubles pass the membrane unchanged",
                }
            )
            break  # one producing unit per effluent nutrient

    # ---- mADM1 biogas (derived gas slice) + biogas flow (estimated)
    for unit in dyn:
        if not _is_anaerobic_madm1(unit):
            continue
        uid = getattr(unit, "ID", None)
        gslice = _gas_slice(unit)
        gas_ids = _gas_ids(unit)
        for offset, gid in enumerate(gas_ids):
            quantities.append(
                {
                    "quantity_id": f"biogas:{gid}@{uid}",
                    "kind": "biogas_species",
                    "observability": "derived",
                    "unit_id": uid,
                    "component": gid,
                    # mADM1 headspace concentrations are kmol/m3 (partial pressure
                    # p_i = R*T*S_gas_i); callers may also pass mole-fraction/vol%
                    # via a per-measurement {"units": "vol%"} and it is converted.
                    "measurement_units": "kmol/m3",
                    "gas_slice_bounds": [int(gslice.start), int(gslice.stop)],
                    "gas_state_index": int(gslice.start) + offset,
                    "state_vars": [
                        {"unit_id": uid, "component": gid, "index": int(gslice.start) + offset}
                    ],
                    "notes": "headspace gas-slice species (DERIVED slice, kmol/m3)",
                }
            )
        quantities.append(
            {
                "quantity_id": f"biogas_flow@{uid}",
                "kind": "biogas_flow",
                "observability": "estimated",
                "unit_id": uid,
                "measurement_units": "m3/d",
                "state_vars": [],
                "notes": (
                    "biogas volumetric flow is an emergent output of the gas-transfer + "
                    "headspace balance; reported, not nudged (over-nudging a derived flow "
                    "breaks gas-transfer consistency)"
                ),
            }
        )

    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "model_type": model_type,
        "tss_include_colloidal": bool(include_colloidal),
        "build_fingerprint": fingerprint,
        "quantities": quantities,
        "quantity_index": {q["quantity_id"]: i for i, q in enumerate(quantities)},
    }


def _resolve_effluent_sources(
    system: Any,
    *,
    effluent_stream_ids: Optional[Sequence[str]] = None,
    effluent_unit_ids: Optional[Sequence[str]] = None,
) -> List[Tuple[Any, Optional[str]]]:
    """Return [(producing_unit, stream_id)] for the effluent boundary.

    Resolution order: explicit unit ids -> explicit stream ids -> auto-detect
    (terminal liquid streams whose id looks like an effluent/permeate and whose
    source is a dynamic unit).
    """
    units_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", []) or []}
    out: List[Tuple[Any, Optional[str]]] = []
    seen = set()

    if effluent_unit_ids:
        for uid in effluent_unit_ids:
            unit = units_by_id.get(uid)
            if unit is not None and id(unit) not in seen:
                seen.add(id(unit))
                out.append((unit, None))
        return out

    streams = list(getattr(system, "streams", []) or [])
    if effluent_stream_ids:
        wanted = set(effluent_stream_ids)
        for stream in streams:
            if stream is None or getattr(stream, "ID", None) not in wanted:
                continue
            src = getattr(stream, "_source", None) or getattr(stream, "source", None)
            if src is not None and getattr(src, "hasode", False) and id(src) not in seen:
                seen.add(id(src))
                out.append((src, getattr(stream, "ID", None)))
        return out

    # Auto-detect: terminal liquid streams that read like an effluent.
    for stream in streams:
        if stream is None:
            continue
        sid = (getattr(stream, "ID", "") or "")
        low = sid.lower()
        if not any(tok in low for tok in ("eff", "perm", "effluent", "permeate", "outflow")):
            continue
        if getattr(stream, "phase", "l") == "g":
            continue
        src = getattr(stream, "_source", None) or getattr(stream, "source", None)
        if src is not None and getattr(src, "hasode", False) and id(src) not in seen:
            seen.add(id(src))
            out.append((src, sid))
    return out


# --------------------------------------------------------------- read forward
def read_observations(
    system: Any,
    omap: Dict[str, Any],
    *,
    quantity_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Forward-read the current twin values for the mapped quantities.

    Uses the canonical read math: direct quantities go through
    ``core.control.read_process_variable`` (the sensor read path); MLSS is the
    ``get_TSS`` particulate sum (matching the map's ``tss_include_colloidal``);
    biogas species read the DERIVED gas slice (kmol/m3).
    """
    from qsdsan_engine_mcp.core.control import read_process_variable

    include_colloidal = bool(omap.get("tss_include_colloidal", False))
    units_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", []) or []}
    wanted = set(quantity_ids) if quantity_ids is not None else None
    out: Dict[str, Dict[str, Any]] = {}

    for q in omap.get("quantities", []):
        qid = q["quantity_id"]
        if wanted is not None and qid not in wanted:
            continue
        kind = q.get("kind")
        unit = units_by_id.get(q.get("unit_id"))
        value: Optional[float] = None
        extra: Dict[str, Any] = {}
        try:
            if kind in ("DO", "eff_NH4", "eff_NO3", "eff_PO4", "biomass"):
                value = float(read_process_variable(system, f"{q['unit_id']}.{q['component']}"))
            elif kind == "MLSS":
                if unit is not None:
                    bd = _mlss_breakdown(unit, include_colloidal=include_colloidal)
                    value = float(bd["mlss"])
                    extra["include_colloidal"] = include_colloidal
                    extra["particulate_organic_cod_mg_l"] = float(bd["scalable_cod"])
                    extra["scalable_tss_mg_l"] = float(bd["scalable_tss"])
                    extra["held_tss_mg_l"] = float(bd["held_tss"])
                    extra["cod_tss_ratio"] = (
                        float(bd["scalable_cod"] / bd["scalable_tss"])
                        if bd["scalable_tss"] > 0 else None
                    )
            elif kind == "biogas_species":
                if unit is not None:
                    state = np.asarray(unit._state, dtype=float)
                    value = float(state[q["gas_state_index"]])
                    extra["measurement_units"] = "kmol/m3"
            elif kind == "biogas_flow":
                if unit is not None:
                    gas = _gas_outlet(unit)
                    if gas is not None:
                        value = float(getattr(gas, "F_vol", 0.0) or 0.0) * 24.0
            elif kind == "temperature":
                if unit is not None:
                    value = float(getattr(unit, "T", None)) if getattr(unit, "T", None) else None
        except Exception as exc:  # read failures are reported, not fatal
            extra["read_error"] = str(exc)

        out[qid] = {
            "quantity_id": qid,
            "kind": kind,
            "observability": q.get("observability"),
            "value": value,
            **extra,
        }
    return out


def _gas_outlet(unit: Any) -> Any:
    for stream in getattr(unit, "outs", []) or []:
        if stream is not None and getattr(stream, "phase", "l") == "g":
            return stream
    return None


# ----------------------------------------------------------- bounded projection
def _coerce_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    opts = dict(DEFAULT_OPTIONS)
    opts["dry_run"] = False
    if options:
        for k, v in options.items():
            opts[k] = v

    for key in ("nudge_fraction", "max_rel_change", "positivity_floor", "min_rel_residual"):
        v = opts[key]
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise StateReconciliationError(f"option {key!r} must be numeric, got {v!r}")
        if not math.isfinite(fv):
            raise StateReconciliationError(f"option {key!r} must be finite, got {v!r}")
        opts[key] = fv

    mac = opts.get("max_abs_change")
    if mac is not None:
        try:
            mac = float(mac)
        except (TypeError, ValueError):
            raise StateReconciliationError("option 'max_abs_change' must be numeric or null")
        if not math.isfinite(mac) or mac < 0:
            raise StateReconciliationError(
                "option 'max_abs_change' must be a finite non-negative number or null"
            )
        opts["max_abs_change"] = mac

    opts["nudge_fraction"] = float(np.clip(opts["nudge_fraction"], 0.0, 1.0))
    opts["max_rel_change"] = max(0.0, opts["max_rel_change"])
    opts["positivity_floor"] = max(0.0, opts["positivity_floor"])
    opts["min_rel_residual"] = max(0.0, opts["min_rel_residual"])
    opts["dry_run"] = bool(opts.get("dry_run", False))
    return opts


def _measurement_value(raw: Any) -> Tuple[Optional[float], Dict[str, Any], Optional[str]]:
    """A measurement may be a scalar or {value, confidence, sigma, ...}.

    Returns ``(value, meta, reject_reason)``. ``reject_reason`` is one of
    ``missing_measurement_value`` / ``non_numeric_measurement`` /
    ``non_finite_measurement`` (or ``None`` when the value is usable). Non-finite
    (nan/inf) values are rejected so they never reach ``_state``.
    """
    meta: Dict[str, Any] = {}
    if isinstance(raw, dict):
        meta = {k: v for k, v in raw.items() if k != "value"}
        val = raw.get("value")
    else:
        val = raw
    if val is None:
        return None, meta, "missing_measurement_value"
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return None, meta, "non_numeric_measurement"
    if not math.isfinite(fval):
        return None, meta, "non_finite_measurement"
    # Declared measurement quality is HONORED (confidence scales the nudge, sigma is a
    # dead-band), so an invalid declaration is rejected loudly — silently carrying it as
    # inert metadata is how a caller comes to believe their variance mattered when it
    # did not.
    if "confidence" in meta:
        try:
            conf = float(meta["confidence"])
        except (TypeError, ValueError):
            return None, meta, "invalid_confidence"
        if not (math.isfinite(conf) and 0.0 <= conf <= 1.0):
            return None, meta, "invalid_confidence"
        meta["confidence"] = conf
    if "sigma" in meta:
        try:
            sig = float(meta["sigma"])
        except (TypeError, ValueError):
            return None, meta, "invalid_sigma"
        if not (math.isfinite(sig) and sig > 0.0):
            return None, meta, "invalid_sigma"
        meta["sigma"] = sig
    return fval, meta, None


def _bounded_target(pre: float, meas: float, opts: Dict[str, Any]) -> Tuple[float, bool, bool]:
    """Move ``pre`` toward ``meas`` by ``nudge_fraction``, clipped by a
    floor-aware absolute+relative cap and a positivity floor.

    For a real positive baseline (``pre > positivity_floor``) the step is bounded
    by ``max_rel_change * pre`` (relative). For a near-zero / zero baseline the
    relative bound collapses, so an ABSOLUTE cap governs instead --
    ``max_abs_change`` if set, else ``max_rel_change * |meas|`` -- which keeps the
    step bounded (no unbounded jump) yet nonzero (no silent no-op).

    Returns ``(target, clipped_by_cap, floored)``.
    """
    floor = opts["positivity_floor"]
    delta = opts["nudge_fraction"] * (meas - pre)
    if pre > floor:
        allowed = opts["max_rel_change"] * pre
    else:
        mac = opts.get("max_abs_change")
        allowed = mac if mac is not None else opts["max_rel_change"] * abs(meas)
    clipped = False
    if abs(delta) > allowed:
        delta = math.copysign(allowed, delta)
        clipped = True
    target = pre + delta
    floored = False
    if target < floor:
        target = floor
        floored = True
    return target, clipped, floored


def reconcile_restored_state(
    system: Any,
    measurements: Dict[str, Any],
    omap: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bounded, positivity-clipped, ratio-preserving projection of the restored
    twin state toward ``measurements``.

    Must run on a B1-restored, dynamically-assembled system (``system._state``
    populated and each ``unit._state`` linked as a view). The observability map's
    topology fingerprint and every indexed component id are validated against the
    live system BEFORE any mutation, so a stale map can never write into the wrong
    slot. For each measured quantity it computes a bounded target and, for DERIVED
    MLSS, scales only the COD-organic particulate fraction (so COD/TSS closure is
    conserved and the charged/P/mineral inventory is untouched). Side effects are
    reported.

    ``options['dry_run']`` reports pre/projected residuals WITHOUT mutating state.

    Mutation discipline (mirrors B1): writes are IN PLACE on ``unit._state``
    (a view into ``system._state``); after all writes, each touched unit's
    ``_update_state()`` refreshes its outlet buffers, ``_dstate`` is zeroed, and
    ``system._DAE`` / each touched ``unit._ODE`` are nulled so the RHS recompiles.
    """
    opts = _coerce_options(options)
    dry_run = opts["dry_run"]
    include_colloidal = bool(omap.get("tss_include_colloidal", False))

    # Validate the map against the live system before trusting any index.
    _validate_omap_against_system(system, omap)

    units_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", []) or []}
    qindex = omap.get("quantity_index") or {
        q["quantity_id"]: i for i, q in enumerate(omap.get("quantities", []))
    }
    quantities = omap.get("quantities", [])

    results: List[Dict[str, Any]] = []
    touched_units: Dict[int, Any] = {}
    pre_states: Dict[int, Any] = {}
    touched_streams: set = set()
    max_rel_component_change = 0.0
    positivity_clips = 0
    rejected = 0
    applied = 0

    for key, raw in (measurements or {}).items():
        entry = _match_quantity(quantities, qindex, key)
        meas, meas_meta, reject_reason = _measurement_value(raw)
        if entry is None:
            results.append(
                {
                    "quantity_id": key,
                    "observability": None,
                    "measurement": meas,
                    "status": "rejected",
                    "flags": ["unmapped_quantity"],
                }
            )
            rejected += 1
            continue
        qid = entry["quantity_id"]
        observ = entry.get("observability")
        kind = entry.get("kind")
        unit = units_by_id.get(entry.get("unit_id"))

        if reject_reason is not None:
            results.append(
                {
                    "quantity_id": qid, "observability": observ, "measurement": meas,
                    "status": "rejected", "flags": [reject_reason],
                    **({"extra": meas_meta} if meas_meta else {}),
                }
            )
            rejected += 1
            continue

        # Estimated-only quantities are never nudged; report residual only.
        if observ == "estimated" or kind in ("biomass", "temperature", "biogas_flow"):
            pre = _read_single(system, entry, unit, include_colloidal=include_colloidal)
            results.append(
                _quantity_record(
                    qid, observ, pre, meas, post=pre, nudge_applied=False,
                    confidence=_confidence(observ, clipped=False, floored=False),
                    flags=["estimated_not_actionable"], extra=meas_meta,
                )
            )
            continue

        # Honor the declared measurement quality: confidence scales the nudge fraction
        # (a half-trusted measurement moves the state half as far), sigma is a dead-band
        # (a residual inside the stated measurement noise is not evidence to move at
        # all). Both were previously parsed and carried as inert metadata.
        eff_opts = opts
        conf = meas_meta.get("confidence")
        if conf is not None and conf < 1.0:
            eff_opts = {**eff_opts, "nudge_fraction": opts["nudge_fraction"] * conf}
        if meas_meta.get("sigma") is not None:
            eff_opts = ({**eff_opts} if eff_opts is opts else eff_opts)
            eff_opts["measurement_sigma"] = meas_meta["sigma"]

        # Snapshot the unit's full state on first touch so the post-update conservation
        # report can compute what the nudges moved, element by element.
        if unit is not None and id(unit) not in pre_states:
            try:
                pre_states[id(unit)] = np.array(unit._state, dtype=float, copy=True)
            except Exception:
                pass

        if kind == "MLSS":
            rec = _reconcile_mlss(unit, meas, eff_opts, dry_run, include_colloidal=include_colloidal)
        elif kind == "biogas_species":
            rec = _reconcile_gas_species(unit, entry, meas, meas_meta, eff_opts, dry_run)
        else:  # direct scalar state component (DO, effluent nutrients)
            rec = _reconcile_direct(unit, entry, meas, eff_opts, dry_run)

        rec.setdefault("flags", [])
        if conf is not None and conf < 1.0 and rec.get("nudge_applied"):
            rec["flags"].append("confidence_scaled_nudge")
        rec["extra"] = {**meas_meta, **rec.get("extra", {})}
        results.append(_finalize_record(qid, observ, meas, rec))

        if rec.get("nudge_applied"):
            applied += 1
            touched_units[id(unit)] = unit
            max_rel_component_change = max(
                max_rel_component_change, float(rec.get("max_rel_change", 0.0))
            )
            positivity_clips += int(rec.get("positivity_clips", 0))

    # ---- commit the mutations (B1 discipline) unless dry_run
    if not dry_run and touched_units:
        for unit in touched_units.values():
            _refresh_touched_unit(unit, touched_streams)
        system._DAE = None

    mutated = bool(touched_units) and not dry_run
    status = _overall_status(
        results, dry_run=dry_run, applied=applied, rejected=rejected, mutated=mutated
    )
    confidences = [r["confidence"] for r in results if r.get("confidence") is not None]
    overall_conf = float(np.mean(confidences)) if confidences else 0.0
    conservation_report = _conservation_report(
        touched_units, pre_states, dry_run=dry_run, mutated=mutated
    )

    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "status": status,
        # This is a bounded projection, not state estimation: no covariance, no
        # innovation statistics, no smoother. Named so a consumer cannot mistake it.
        "method": "bounded_nudge",
        "conservation_report": conservation_report,
        "dry_run": dry_run,
        "options": {k: opts[k] for k in (
            "nudge_fraction", "max_rel_change", "max_abs_change",
            "positivity_floor", "min_rel_residual", "dry_run")},
        "quantities": results,
        "state_update_summary": {
            "touched_units": sorted(getattr(u, "ID", None) for u in touched_units.values()),
            "touched_streams": sorted(touched_streams),
            "max_relative_component_change": float(max_rel_component_change),
            "positivity_clips": int(positivity_clips),
            "mutated": mutated,
        },
        "quality": {
            "overall_confidence": overall_conf,
            "grade": _grade(overall_conf),
            "applied": applied,
            "rejected": rejected,
        },
        # COMPUTED decision readiness — a bounded nudge is never a decision-grade state
        # estimate; the reasons say so machine-readably, and carry the conservation
        # report's summary so a consumer sees what inventory the nudges moved.
        "decision_readiness": {
            "decision_grade": False,
            "reasons": [
                "bounded_nudge_projection_not_state_estimation",
                "no_covariance_or_innovation_statistics",
                _conservation_reason(conservation_report),
            ],
            "forcing_mode": None,
            "degradations": [
                r["quantity_id"] for r in results if r.get("status") == "rejected"
            ],
        },
    }


def _conservation_reason(report: Dict[str, Any]) -> str:
    """One machine-readable reasons-entry summarizing the conservation report."""
    if report.get("status") != "computed":
        return f"conservation_report_{report.get('status', 'unavailable')}"
    units = report.get("units", [])
    moved = sum(
        1 for u in units
        if any(u.get(k) not in (None, 0.0) for k in ("delta_total_N", "delta_total_P", "delta_charge"))
    )
    return f"conservation_deltas_reported_for_{moved}_of_{len(units)}_touched_units"


def _conservation_report(
    touched_units: Dict[int, Any],
    pre_states: Dict[int, Any],
    *,
    dry_run: bool,
    mutated: bool,
) -> Dict[str, Any]:
    """What the nudges moved, element by element — compute-and-report, never blocking.

    Direct scalar nudges write single state slots with no conjugate adjustment, so a
    reconciliation CAN shift total N, P or charge. That is a disclosed property of the
    bounded-nudge method; this report quantifies it per touched unit using the
    component stoichiometry (i_N / i_P / i_charge over the concentration slots), so
    the caller sees exactly what inventory the correction introduced.
    """
    if dry_run or not mutated:
        return {"status": "not_applicable", "reason": "no state mutation (dry_run or nothing applied)"}
    try:
        rows: List[Dict[str, Any]] = []
        for unit in touched_units.values():
            pre = pre_states.get(id(unit))
            post = np.asarray(unit._state, dtype=float)
            if pre is None or pre.shape != post.shape:
                rows.append({"unit_id": getattr(unit, "ID", None), "status": "unavailable",
                             "reason": "no pre-mutation snapshot"})
                continue
            cmps = unit.components
            n = len(cmps.IDs)
            delta = post[:n] - np.asarray(pre, dtype=float)[:n]
            row: Dict[str, Any] = {"unit_id": getattr(unit, "ID", None), "status": "computed"}
            for label, attr in (("delta_total_N", "i_N"), ("delta_total_P", "i_P"),
                                ("delta_charge", "i_charge")):
                coeff = getattr(cmps, attr, None)
                if coeff is None:
                    row[label] = None
                else:
                    row[label] = float(np.dot(delta, np.asarray(coeff, dtype=float)[:n]))
            row["max_abs_component_delta"] = float(np.max(np.abs(delta))) if n else 0.0
            rows.append(row)
        return {
            "status": "computed",
            "basis": "component-stoichiometry deltas (state concentration units) introduced by the nudges",
            "units": rows,
            "note": "reported, not enforced — the bounded nudge does not co-adjust conjugate species",
        }
    except Exception as exc:  # never let the report kill a completed reconciliation
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}


# ----------------------------------------------------- observability validation
def _validate_omap_against_system(system: Any, omap: Dict[str, Any]) -> None:
    """Validate the observability map's fingerprint + indexed component ids.

    Raises ``StateReconciliationError`` if the map was built against a different
    topology (fingerprint mismatch) or if any indexed component no longer lives
    at the recorded slot. Run before any mutation so a stale persisted map can
    never write into the wrong state var.
    """
    if not isinstance(omap, dict) or "quantities" not in omap:
        raise StateReconciliationError("observability map is malformed (no 'quantities')")

    fp_map = omap.get("build_fingerprint")
    if fp_map is not None:
        try:
            from qsdsan_engine_mcp.utils.state_checkpoint import build_fingerprint

            fp_now = build_fingerprint(system)
        except Exception as exc:  # pragma: no cover - defensive
            raise StateReconciliationError(
                f"could not fingerprint the live system for map validation: {exc}"
            ) from exc
        if (
            fp_now.get("unit_order") != fp_map.get("unit_order")
            or fp_now.get("connections_hash") != fp_map.get("connections_hash")
            or set(fp_now.get("recycle_stream_ids") or [])
            != set(fp_map.get("recycle_stream_ids") or [])
        ):
            raise StateReconciliationError(
                "observability map fingerprint mismatch: the system topology changed "
                "since the map was built; rebuild build_observability_map(system, ...)"
            )

    units_by_id = {getattr(u, "ID", None): u for u in getattr(system, "units", []) or []}
    for q in omap.get("quantities", []):
        kind = q.get("kind")
        unit = units_by_id.get(q.get("unit_id"))

        if kind == "biogas_species":
            if unit is None or not _is_anaerobic_madm1(unit):
                raise StateReconciliationError(
                    f"map biogas entry {q.get('quantity_id')!r} references a non-mADM1 "
                    f"or missing unit {q.get('unit_id')!r}"
                )
            gslice = _gas_slice(unit)
            gas_ids = _gas_ids(unit)
            comp = q.get("component")
            if comp not in gas_ids:
                raise StateReconciliationError(
                    f"map gas species {comp!r} not in current headspace {gas_ids}"
                )
            expected = int(gslice.start) + gas_ids.index(comp)
            if int(q.get("gas_state_index", -1)) != expected:
                raise StateReconciliationError(
                    f"map gas index for {comp!r} is stale "
                    f"({q.get('gas_state_index')} != {expected})"
                )
            continue

        for sv in q.get("state_vars", []) or []:
            uid = sv.get("unit_id")
            comp = sv.get("component")
            idx = sv.get("index")
            u = units_by_id.get(uid)
            if u is None:
                raise StateReconciliationError(
                    f"map references unit {uid!r} absent from the live system"
                )
            ids = _component_ids(u)
            if idx is None or idx < 0 or idx >= len(ids):
                raise StateReconciliationError(
                    f"map index {idx} out of range for unit {uid!r} ({len(ids)} components)"
                )
            if ids[idx] != comp:
                raise StateReconciliationError(
                    f"map component mismatch at {uid!r}[{idx}]: live {ids[idx]!r} != map {comp!r}"
                )


# ------------------------------------------------------------- per-kind nudges
def _read_single(
    system: Any, entry: Dict[str, Any], unit: Any, *, include_colloidal: bool = False
) -> Optional[float]:
    from qsdsan_engine_mcp.core.control import read_process_variable

    kind = entry.get("kind")
    try:
        if kind == "MLSS" and unit is not None:
            return float(_mlss_breakdown(unit, include_colloidal=include_colloidal)["mlss"])
        if kind == "biogas_species" and unit is not None:
            return float(np.asarray(unit._state, dtype=float)[entry["gas_state_index"]])
        if kind == "biogas_flow" and unit is not None:
            gas = _gas_outlet(unit)
            return float(getattr(gas, "F_vol", 0.0) or 0.0) * 24.0 if gas is not None else None
        if kind == "temperature" and unit is not None:
            t = getattr(unit, "T", None)
            return float(t) if t else None
        if entry.get("component") and entry.get("unit_id"):
            return float(read_process_variable(system, f"{entry['unit_id']}.{entry['component']}"))
    except Exception:
        return None
    return None


def _reconcile_direct(
    unit: Any, entry: Dict[str, Any], meas: float, opts: Dict[str, Any], dry_run: bool
) -> Dict[str, Any]:
    idx = entry["state_vars"][0]["index"]
    state = unit._state
    pre = float(state[idx])
    flags: List[str] = []
    if pre > opts["positivity_floor"] and abs(meas - pre) / pre < opts["min_rel_residual"]:
        return {
            "pre_twin": pre, "post_twin": pre, "nudge_applied": False,
            "confidence": _confidence(entry.get("observability"), False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["below_residual_threshold"],
        }
    sig = opts.get("measurement_sigma")
    if sig is not None and abs(meas - pre) <= sig:
        return {
            "pre_twin": pre, "post_twin": pre, "nudge_applied": False,
            "confidence": _confidence(entry.get("observability"), False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["within_measurement_sigma"],
        }
    target, clipped, floored = _bounded_target(pre, meas, opts)
    if not dry_run:
        state[idx] = target  # in-place write into the system-vector view
    rel = abs((target - pre) / pre) if pre > 0 else (abs(target - pre))
    if clipped:
        flags.append("max_change_clipped")
    if floored:
        flags.append("positivity_floored")
    return {
        "pre_twin": pre,
        "post_twin": float(target),
        "nudge_applied": not dry_run,
        "confidence": _confidence(entry.get("observability"), clipped, floored),
        "max_rel_change": float(rel),
        "positivity_clips": int(floored),
        "flags": flags,
    }


def _reconcile_mlss(
    unit: Any, meas: float, opts: Dict[str, Any], dry_run: bool, *, include_colloidal: bool = False
) -> Dict[str, Any]:
    """COD-organic-only projection of MLSS.

    MLSS = scalable (COD-organic particulate) TSS + held (charged / P / mineral
    particulate) TSS. We move MLSS toward its (bounded) target by scaling ONLY
    the scalable fraction; the held slots -- ``X_PP`` and the mineral precipitates
    -- are untouched, so the stored-P, charge, and mineral inventory is unchanged
    (moving them would need a TP / PO4 / charge measurement + constraint). Within
    the scaled fraction every inter-component ratio and the COD:TSS ratio are
    invariant (uniform scale), so COD/TSS closure of that fraction is conserved by
    construction. The held TSS floor, the COD shift, and the unchanged P/charge
    inventory are all REPORTED.
    """
    bd = _mlss_breakdown(unit, include_colloidal=include_colloidal)
    pre_mlss = bd["mlss"]
    scalable_tss = bd["scalable_tss"]
    held_tss = bd["held_tss"]
    pre_cod = bd["scalable_cod"]
    parts = bd["parts"]
    held_components = [e["component"] for e in parts if e["held"]]
    scalable_components = [e["component"] for e in parts if not e["held"]]

    base_extra: Dict[str, Any] = {
        "include_colloidal": include_colloidal,
        "held_tss_mg_l": float(held_tss),
        "held_components": held_components,
        "scalable_components": scalable_components,
        "held_p_inventory_mg_p_l": float(bd["held_p"]),
        "held_p_charge_unchanged": True,
        "scalable_tss_mg_l_pre": float(scalable_tss),
        "n_scalable_components": len(scalable_components),
        "n_held_components": len(held_components),
    }

    if pre_mlss <= 0 or not parts:
        return {
            "pre_twin": pre_mlss, "post_twin": pre_mlss, "nudge_applied": False,
            "confidence": 0.0, "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["no_particulate_mass"],
            "extra": {**base_extra, "pre_particulate_cod_mg_l": float(pre_cod)},
        }
    if scalable_tss <= 0:
        # Only charged / P / mineral particulate present; cannot move MLSS without
        # touching the held inventory, which we refuse to do without a measurement.
        return {
            "pre_twin": pre_mlss, "post_twin": pre_mlss, "nudge_applied": False,
            "confidence": _confidence("derived", False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["no_scalable_organic_mass"],
            "extra": {
                **base_extra,
                "note": (
                    "all particulate mass is charged/P/mineral (held); MLSS not "
                    "adjusted without a TP/PO4/charge measurement"
                ),
            },
        }
    sig = opts.get("measurement_sigma")
    if sig is not None and abs(meas - pre_mlss) <= sig:
        return {
            "pre_twin": pre_mlss, "post_twin": pre_mlss, "nudge_applied": False,
            "confidence": _confidence("derived", False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["within_measurement_sigma"],
            "extra": {
                **base_extra,
                "scale_factor": 1.0,
                "scalable_tss_mg_l_post": float(scalable_tss),
                "pre_particulate_cod_mg_l": float(pre_cod),
                "post_particulate_cod_mg_l": float(pre_cod),
                "cod_tss_ratio_pre": float(pre_cod / scalable_tss),
                "cod_tss_ratio_post": float(pre_cod / scalable_tss),
                "ratio_preserved": True,
            },
        }
    if abs(meas - pre_mlss) / pre_mlss < opts["min_rel_residual"]:
        return {
            "pre_twin": pre_mlss, "post_twin": pre_mlss, "nudge_applied": False,
            "confidence": _confidence("derived", False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["below_residual_threshold"],
            "extra": {
                **base_extra,
                "scale_factor": 1.0,
                "scalable_tss_mg_l_post": float(scalable_tss),
                "pre_particulate_cod_mg_l": float(pre_cod),
                "post_particulate_cod_mg_l": float(pre_cod),
                "cod_tss_ratio_pre": float(pre_cod / scalable_tss),
                "cod_tss_ratio_post": float(pre_cod / scalable_tss),
                "ratio_preserved": True,
            },
        }

    # Move the aggregate by nudge_fraction, then translate the achievable move
    # into a scale factor on the scalable fraction (the held floor stays fixed).
    nudged_mlss = pre_mlss + opts["nudge_fraction"] * (meas - pre_mlss)
    required_scalable = nudged_mlss - held_tss
    k = required_scalable / scalable_tss

    # Bound the per-component change: no scalable component moves by more than
    # max_rel_change. Clamp k to [1-max_rel_change, 1+max_rel_change], floor >= 0.
    lo = 1.0 - opts["max_rel_change"]
    hi = 1.0 + opts["max_rel_change"]
    flags: List[str] = []
    clipped = False
    if k < lo:
        k = lo
        clipped = True
    if k > hi:
        k = hi
        clipped = True
    floored = False
    if k < 0.0:
        k = 0.0
        floored = True
    if clipped:
        flags.append("max_change_clipped")
    if floored:
        flags.append("positivity_floored")

    state = unit._state
    if not dry_run:
        for e in parts:
            if not e["held"]:
                state[e["index"]] *= k  # uniform scale preserves scalable ratios
    post_scalable_tss = k * scalable_tss
    post_mlss = post_scalable_tss + held_tss
    post_cod = pre_cod * k
    rel = abs(k - 1.0)
    return {
        "pre_twin": float(pre_mlss),
        "post_twin": float(post_mlss),
        "nudge_applied": not dry_run,
        "confidence": _confidence("derived", clipped, floored),
        "max_rel_change": float(rel),
        "positivity_clips": int(floored),
        "flags": flags,
        "extra": {
            **base_extra,
            "scale_factor": float(k),
            "scalable_tss_mg_l_post": float(post_scalable_tss),
            "residual_held_tss_mg_l": float(held_tss),
            "pre_particulate_cod_mg_l": float(pre_cod),
            "post_particulate_cod_mg_l": float(post_cod),
            "cod_tss_ratio_pre": float(pre_cod / scalable_tss) if scalable_tss else None,
            "cod_tss_ratio_post": float(post_cod / post_scalable_tss) if post_scalable_tss else None,
            "ratio_preserved": True,
        },
    }


def _reconcile_gas_species(
    unit: Any,
    entry: Dict[str, Any],
    meas: float,
    meas_meta: Dict[str, Any],
    opts: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    """Bounded nudge of a single mADM1 headspace gas-slice species (kmol/m3).

    The gas-slice index comes from the DERIVED slice (never a literal index). The
    measurement is interpreted as ``kmol/m3`` (the engine-native unit) unless the
    measurement meta carries ``units`` in {``vol%``, ``mol%``, ``mole_fraction``},
    in which case it is converted to kmol/m3 against the current total headspace
    concentration. The nudge is bounded + positivity-clipped exactly like a direct
    scalar. The per-species nudge is deliberately NOT renormalized (so untouched
    species stay put); the resulting total-pressure shift is REPORTED rather than
    silently absorbed. The biogas *flow* is left untouched (estimated-only).
    """
    gidx = entry["gas_state_index"]
    state = unit._state
    full_state = np.asarray(state, dtype=float)
    gslice = _gas_slice(unit)
    pre = float(state[gidx])
    flags: List[str] = []
    extra: Dict[str, Any] = {"gas_state_index": gidx, "gas_component": entry.get("component")}

    # Optional vol%/mole-fraction -> kmol/m3 conversion against the live headspace.
    units = str(meas_meta.get("units", "")).strip().lower() if meas_meta else ""
    if units in ("vol%", "vol_pct", "volpct", "mol%", "mol_pct", "molpct", "mole_fraction", "molfrac", "%"):
        total_gas = float(np.sum(full_state[gslice]))
        frac = meas / 100.0 if "%" in units or "pct" in units else meas
        meas = frac * total_gas
        extra["converted_from_units"] = units
        extra["converted_measurement_kmol_m3"] = float(meas)
        # Sigma is declared in the measurement's units; convert it on the same basis so
        # the dead-band compares like with like.
        if opts.get("measurement_sigma") is not None:
            sig_raw = float(opts["measurement_sigma"])
            sig_frac = sig_raw / 100.0 if "%" in units or "pct" in units else sig_raw
            opts = {**opts, "measurement_sigma": sig_frac * total_gas}
            extra["converted_sigma_kmol_m3"] = float(sig_frac * total_gas)

    pre_pressure = _headspace_total_pressure_bar(unit)

    sig = opts.get("measurement_sigma")
    if sig is not None and abs(meas - pre) <= sig:
        extra["headspace_pressure_pre_bar"] = float(pre_pressure)
        extra["headspace_pressure_post_bar"] = float(pre_pressure)
        extra["headspace_pressure_delta_bar"] = 0.0
        return {
            "pre_twin": pre, "post_twin": pre, "nudge_applied": False,
            "confidence": _confidence("derived", False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["within_measurement_sigma"],
            "extra": extra,
        }

    if pre > opts["positivity_floor"] and abs(meas - pre) / pre < opts["min_rel_residual"]:
        extra["headspace_pressure_pre_bar"] = float(pre_pressure)
        extra["headspace_pressure_post_bar"] = float(pre_pressure)
        extra["headspace_pressure_delta_bar"] = 0.0
        return {
            "pre_twin": pre, "post_twin": pre, "nudge_applied": False,
            "confidence": _confidence("derived", False, False),
            "max_rel_change": 0.0, "positivity_clips": 0,
            "flags": ["below_residual_threshold"],
            "extra": extra,
        }
    target, clipped, floored = _bounded_target(pre, meas, opts)
    if not dry_run:
        state[gidx] = target
    rel = abs((target - pre) / pre) if pre > 0 else (abs(target - pre))
    if clipped:
        flags.append("max_change_clipped")
    if floored:
        flags.append("positivity_floored")

    post_pressure = _headspace_total_pressure_bar(unit) if not dry_run else (
        pre_pressure + _R_BAR_M3_PER_KMOL_K * _gas_temperature(unit) * (target - pre)
    )
    extra["headspace_pressure_pre_bar"] = float(pre_pressure)
    extra["headspace_pressure_post_bar"] = float(post_pressure)
    extra["headspace_pressure_delta_bar"] = float(post_pressure - pre_pressure)
    extra["headspace_renormalized"] = False
    if abs(post_pressure - pre_pressure) > 0:
        flags.append("headspace_pressure_shifted")
    return {
        "pre_twin": pre,
        "post_twin": float(target),
        "nudge_applied": not dry_run,
        "confidence": _confidence("derived", clipped, floored),
        "max_rel_change": float(rel),
        "positivity_clips": int(floored),
        "flags": flags,
        "extra": extra,
    }


def _refresh_touched_unit(unit: Any, touched_streams: set) -> None:
    """Refresh a unit's outlet buffers + zero its dstate + null its ODE (B1)."""
    # zero the stale derivative buffer (the new state invalidates it; the RHS
    # recomputes it on the first resumed step).
    dstate = getattr(unit, "_dstate", None)
    if dstate is not None:
        try:
            dstate[:] = 0.0
        except Exception:
            pass
    # refresh outlet stream buffers from the projected state.
    try:
        unit._update_state()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not _update_state for %s: %s", getattr(unit, "ID", "?"), exc)
    for stream in getattr(unit, "outs", []) or []:
        if stream is not None and getattr(stream, "ID", None) is not None:
            touched_streams.add(stream.ID)
    # drop the stale compiled ODE closure so it rebuilds against the new state.
    if hasattr(unit, "_ODE"):
        unit._ODE = None


# ------------------------------------------------------------------- scoring
def _confidence(observability: Optional[str], clipped: bool, floored: bool) -> float:
    base = {"direct": 0.9, "derived": 0.6, "estimated": 0.2}.get(observability or "", 0.3)
    if clipped:
        base *= 0.7
    if floored:
        base *= 0.8
    return float(round(base, 4))


def _grade(conf: float) -> str:
    if conf >= 0.75:
        return "good"
    if conf >= 0.5:
        return "fair"
    return "poor"


def _quantity_record(
    qid, observ, pre, meas, post, nudge_applied, confidence, flags, extra=None
) -> Dict[str, Any]:
    pre_resid = (meas - pre) if (meas is not None and pre is not None) else None
    post_resid = (meas - post) if (meas is not None and post is not None) else None
    return {
        "quantity_id": qid,
        "observability": observ,
        "pre_twin": pre,
        "measurement": meas,
        "pre_residual": pre_resid,
        "post_twin": post,
        "post_residual": post_resid,
        "nudge_applied": bool(nudge_applied),
        "confidence": confidence,
        "flags": list(flags or []),
        **({"extra": extra} if extra else {}),
    }


def _finalize_record(qid: str, observ: Optional[str], meas: float, rec: Dict[str, Any]) -> Dict[str, Any]:
    return _quantity_record(
        qid,
        observ,
        rec.get("pre_twin"),
        meas,
        rec.get("post_twin"),
        rec.get("nudge_applied"),
        rec.get("confidence"),
        rec.get("flags"),
        extra=rec.get("extra"),
    )


def _match_quantity(quantities, qindex, key) -> Optional[Dict[str, Any]]:
    """Match a measurement key to a map entry.

    Accepts the exact ``quantity_id`` or a base name (e.g. ``DO`` / ``MLSS``)
    when it resolves to exactly one entry.
    """
    if key in qindex:
        return quantities[qindex[key]]
    # base-name match (case-insensitive on kind or the part before '@')
    candidates = []
    for q in quantities:
        qid = q["quantity_id"]
        base = qid.split("@", 1)[0]
        if key == base or key == q.get("kind"):
            candidates.append(q)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _overall_status(
    results: List[Dict[str, Any]],
    *,
    dry_run: bool,
    applied: int,
    rejected: int,
    mutated: bool,
) -> str:
    """Overall status. ``no_op`` when nothing mutated (and nothing rejected) so a
    caller is never told ``applied`` when the state is unchanged."""
    if dry_run:
        return "dry_run"
    if not mutated:
        # nothing mutated: rejected-only, estimated-only, or all below threshold
        if rejected and rejected == len(results):
            return "rejected"
        if rejected:
            return "partial"
        return "no_op"
    if rejected:
        return "partial"
    return "applied"


__all__ = [
    "RECONCILIATION_SCHEMA_VERSION",
    "StateReconciliationError",
    "DEFAULT_OPTIONS",
    "build_observability_map",
    "read_observations",
    "reconcile_restored_state",
]
