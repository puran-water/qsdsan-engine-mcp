"""Per-process inhibition & activity diagnostics for QSDsan bio-reactor runs.

This module produces proposal-grade diagnostic reports for two engine families:

* **mADM1** (modified Anaerobic Digestion Model 1) — reads the instrumented
  ``root.data`` the ``rhos_madm1`` rate function writes on every call.
* **mASM2d** (modified Activated Sludge Model 2d) — computes Monod factors
  post-hoc from the final state array and the reactor's kinetic params
  (QSDsan's built-in ``_rhos_asm2d`` does not instrument itself, so we
  replay the Monod formulae from ``qsdsan.processes._asm2d``).

Output shape is modelled on ``puran-water/adm1-mcp/inhibition.py``:

.. code-block:: python

    {
        "engine": "madm1"|"asm2d",
        "current_pH": <float>,
        "process_inhibition": [
            {"process": <name>, "pH_inhibition": %, "substrate_limitation": %,
             "overall_inhibition": %, "without_substrate": %, "process_rate": ...},
            ...
        ],
        "health_assessment": {"status": Excellent|Good|Fair|Poor|Critical,
                              "max_inhibition": %, "limiting_process": str},
        "safety_assessment": {"status": High|Good|Moderate|Low,
                              "min_safety_margin": %, "limiting_process": str,
                              "message": str},
        "inhibition_factors": [{"type": ..., "value": %}, ...],
        "recommendations": [str, ...],
        "safety_recommendations": [str, ...],
    }

mADM1 output **extends** the adm1-mcp reference with:

* PAO, SRB, HFO, and MMP per-process blocks driven directly from
  ``root.data['process_names']`` + ``root.data['process_indices']``.
* Four-species gas-transfer diagnostics (CH4, CO2, H2, H2S).
* Per-population pH/H2 inhibition instead of positional arrays.

mASM2d output covers all 19 biological processes (hydrolysis aerobic/anoxic/
anaerobic, heterotroph growth aerobic/anoxic on F/A, fermentation, lysis,
PAO storage/P-storage/growth/lysis, autotroph growth/lysis) with per-process
Monod activity factors.

Consumed by the ``_build_engine_diagnostics`` helper in ``server.py`` which
emits the payload into the stage='c' ``simulation_report.diagnostics.
process_inhibition`` field the compiler renders in the proposal.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


__all__ = [
    "analyze_madm1_process_health",
    "analyze_asm2d_process_health",
    "capture_system_process_health",
    "summarize_health_status",
    "summarize_safety_status",
    "inhibition_degree",
]


def capture_system_process_health(system: Any) -> dict[str, Any] | None:
    """Unified entry point — auto-dispatch by engine family.

    The design rule (from 2026-04-19): diagnostic capture must not depend
    on which QSDsan *template* built the system. Templates cover only a
    fraction of the flowsheet option space; agents will increasingly
    construct flowsheets dynamically. This helper introspects the system
    to identify the engine (mADM1 via ``rate_function._params['root']``
    or ASM2d via Ks_o2 in params) and dispatches to the right analyzer.

    Returns
    -------
    dict | None
        The analyzer payload (with ``success: True``) for the first
        matching engine found, or ``None`` if no instrumentable engine
        is present. Callers attach to ``results['process_health']``.
    """
    if system is None:
        return None
    # mADM1: check for root-captured diagnostic data first (strongest signal).
    root = _read_madm1_root_data(system)
    if root is not None and ("I_pH" in root or "Iph" in root):
        report = analyze_madm1_process_health(system)
        if report.get("success"):
            return report

    # mASM2d: reachable via reactor params containing Ks_o2 + K_X.
    asm2d = _read_asm2d_state_and_params(system)
    if asm2d is not None:
        report = analyze_asm2d_process_health(system)
        if report.get("success"):
            return report

    return None


# --------------------------------------------------------------------------
# Generic health/safety tier helpers
# --------------------------------------------------------------------------


def inhibition_degree(factor: float) -> float:
    """Convert a [0, 1] activity factor to a [0, 100]% inhibition degree.

    Clamps negative or >1 values defensively so a numerical blip
    (``Monod`` on a tiny sub-zero state) does not produce a negative
    percentage in the proposal.
    """
    try:
        f = float(factor)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        f = 1.0
    if f < 0.0:
        f = 0.0
    return (1.0 - f) * 100.0


def summarize_health_status(max_inhibition_pct: float) -> str:
    """Tier the worst-case inhibition percentage into a 5-level health label.

    Thresholds match puran-water/adm1-mcp so proposal language stays
    consistent across engines.
    """
    if max_inhibition_pct < 10:
        return "Excellent"
    if max_inhibition_pct < 20:
        return "Good"
    if max_inhibition_pct < 40:
        return "Fair"
    if max_inhibition_pct < 60:
        return "Poor"
    return "Critical"


def summarize_safety_status(min_safety_margin_pct: float) -> tuple[str, str]:
    """Tier the minimum substrate-limitation percentage into a safety label.

    A high ``substrate_limitation`` percentage is good — the reactor has
    significant unused kinetic headroom — so the thresholds read opposite
    to ``summarize_health_status``.
    """
    if min_safety_margin_pct > 60:
        return (
            "High",
            "The system has a high safety margin against shock loads and inhibitory conditions.",
        )
    if min_safety_margin_pct > 30:
        return (
            "Good",
            "The system has a good safety margin, providing adequate buffer against upsets.",
        )
    if min_safety_margin_pct > 10:
        return (
            "Moderate",
            "The system has a moderate safety margin. Consider monitoring closely.",
        )
    return (
        "Low",
        "The system has a low safety margin and may be operating near maximum substrate utilization rate.",
    )


def _safety_recommendations(min_margin: float) -> list[str]:
    if min_margin > 60:
        return [
            f"High Safety Margin ({min_margin:.1f}% substrate limitation)",
            "The reactor has significant reserve capacity and is well-protected against shock organic loads",
            "Consider increasing organic loading rate to improve production or reducing HRT to improve throughput",
        ]
    if min_margin > 30:
        return [
            f"Good Safety Margin ({min_margin:.1f}% substrate limitation)",
            "The reactor has adequate reserve capacity for normal operation",
            "Maintain current loading rate; gradually test higher loads if increased production is desired",
        ]
    if min_margin > 10:
        return [
            f"Moderate Safety Margin ({min_margin:.1f}% substrate limitation)",
            "The reactor has limited buffer against upsets — monitor process closely",
            "Maintain consistent feeding patterns and avoid rapid changes in feedstock composition",
        ]
    return [
        f"Low Safety Margin ({min_margin:.1f}% substrate limitation)",
        "The reactor is operating close to maximum substrate utilization with minimal safety margin",
        "Consider reducing HRT to introduce substrate limitation or adding biomass to increase margin",
        "Monitor for inhibitory compounds that could slow the process further",
    ]


# --------------------------------------------------------------------------
# mADM1 analysis (reads pre-captured root.data)
# --------------------------------------------------------------------------


# Canonical 8-process names matching adm1-mcp for proposal-narrative
# alignment across plants. SRB + PAO processes are emitted as extension
# rows beyond this core list.
_MADM1_PRIMARY_PROCESSES = [
    "Sugar Uptake",
    "Amino Acid Uptake",
    "LCFA Uptake",
    "Valerate Uptake",
    "Butyrate Uptake",
    "Propionate Uptake",
    "Acetate Uptake",
    "H2 Uptake",
]

# Which populations in our rich root.data drive pH inhibition for each
# primary process. Indices correspond to ``I_pH`` dict keys.
_MADM1_PH_POPULATION = [
    "acidogens",    # sugar — acidogenic
    "acidogens",    # aa — acidogenic
    "acidogens",    # LCFA — acidogenic (uses same pH tier)
    "acidogens",    # valerate
    "acidogens",    # butyrate
    "acidogens",    # propionate
    "acetoclastic",  # acetate
    "hydrogenotrophic",  # H2
]

# H2 inhibition applies only to LCFA (idx 2), valerate (3), butyrate (4),
# propionate (5). Our ``I_h2`` dict keys match the process order.
_MADM1_H2_KEYS = {
    2: "LCFA",
    3: "C4_valerate",
    4: "C4_butyrate",
    5: "propionate",
}

_MADM1_SRB_PH_KEYS = (
    "SRB_h2",
    "SRB_ac",
    "SRB_aa",
    "SRB_aa",
    "SRB_aa",
)

_MADM1_SRB_H2S_KEYS = (
    "SRB_h2",
    "SRB_ac",
    "SRB_prop",
    "SRB_bu",
    "SRB_va",
)

_MADM1_REQUIRED_ROOT_KEYS = (
    "process_names",
    "process_indices",
    "pao",
    "srb",
    "hfo",
    "mmp",
    "gas_transfer",
)


def analyze_madm1_process_health(system: Any) -> dict[str, Any]:
    """Build full process health report for a mADM1 simulation.

    Reads the instrumented ``root.data`` written by ``rhos_madm1`` on
    every rate-function call — pH, per-population pH/H2/H2S inhibition,
    N-limitation, NH3 inhibition, primary-substrate Monod factors, and
    the full process-rate vector. Produces an adm1-mcp-shape payload
    extended with:

    * SRB (sulfate-reducing bacteria) process rows.
    * PAO (phosphate-accumulating organism) process rows.
    * H2S inhibition per-population breakdown surfaced as
      ``inhibition_factors`` (the old H2S-only ``analyze_inhibition``
      helper retired in PR-DD — its work is subsumed here).

    Returns
    -------
    dict
        Payload conforming to the module-level schema, or
        ``{"success": False, "reason": ...}`` if diagnostics are not
        available.
    """
    root_data = _read_madm1_root_data(system)
    if root_data is None:
        return {
            "success": False,
            "reason": (
                "mADM1 root.data unavailable (rate_function not instrumented; "
                "use the custom ModifiedADM1 engine, not stock QSDsan ADM1p)"
            ),
        }

    try:
        required_blocks = {
            key: _madm1_required_block(root_data, key)
            for key in _MADM1_REQUIRED_ROOT_KEYS
        }
    except KeyError as err:
        return _madm1_missing_key(err.args[0])
    process_names = required_blocks["process_names"]
    process_indices = required_blocks["process_indices"]
    pao_data = required_blocks["pao"]
    srb_data = required_blocks["srb"]
    hfo_data = required_blocks["hfo"]
    mmp_data = required_blocks["mmp"]
    gas_transfer_data = required_blocks["gas_transfer"]

    pH_value = float(root_data.get("pH", 7.0))
    I_pH = root_data.get("I_pH", {}) or {}
    I_h2 = root_data.get("I_h2", {}) or {}
    I_h2s = root_data.get("I_h2s", {}) or {}
    I_nutrients = root_data.get("I_nutrients", {}) or {}
    Monod = root_data.get("Monod") or []
    process_rates = root_data.get("process_rates") or []
    n_processes = len(process_names)

    try:
        primary_indices = _madm1_index_list(process_indices, "primary_uptake", n_processes)
        pao_rows = _madm1_pao_rows(process_names, process_indices, process_rates, pao_data)
        srb_rows = _madm1_srb_rows(
            process_names,
            process_indices,
            process_rates,
            srb_data,
            I_pH,
            I_h2s,
            I_nutrients,
            n_processes,
        )
        hfo_rows = _madm1_hfo_rows(process_names, process_indices, process_rates, hfo_data, n_processes)
        hfo_rows.extend(
            _madm1_hfo_host_transformation_rows(
                process_names,
                process_indices,
                process_rates,
                hfo_data,
                n_processes,
            )
        )
        mmp_rows = _madm1_mmp_rows(process_names, process_indices, process_rates, mmp_data, n_processes)
        gas_transfer = _madm1_gas_transfer_block(
            process_names,
            process_indices,
            process_rates,
            gas_transfer_data,
            n_processes,
        )
    except KeyError as err:
        return _madm1_missing_key(err.args[0])

    # Our richer root.data uses per-population dict keys (`acidogens`,
    # `hydrogenotrophic`, etc.) — adm1-mcp's 8-element positional array
    # is a subset. We fan the dict back into an 8-vector aligned with
    # _MADM1_PRIMARY_PROCESSES for the adm1-mcp-compatible output block.
    I_pH_vec = np.array([float(I_pH.get(pop, 1.0)) for pop in _MADM1_PH_POPULATION])
    I_h2_vec = np.ones(8)
    for idx, key in _MADM1_H2_KEYS.items():
        I_h2_vec[idx] = float(I_h2.get(key, 1.0))
    Inh3 = float(I_nutrients.get("I_nh3", 1.0))
    I_nh3_vec = np.ones(8)
    I_nh3_vec[6] = Inh3  # acetoclastic methanogenesis
    Iin = float(I_nutrients.get("combined", 1.0))
    substrate = np.ones(8)
    if len(Monod) >= 8:
        substrate = np.array([float(x) for x in Monod[:8]])

    rates_vec = np.array([
        _madm1_process_rate(process_rates, idx) for idx in primary_indices[:8]
    ])

    # Build per-process rows matching adm1-mcp's shape.
    process_inhibition = []
    overall_activity_no_sub = []
    for i, name in enumerate(_MADM1_PRIMARY_PROCESSES):
        with_sub = I_pH_vec[i] * Iin * I_h2_vec[i] * I_nh3_vec[i] * substrate[i]
        no_sub = I_pH_vec[i] * Iin * I_h2_vec[i] * I_nh3_vec[i]
        overall_activity_no_sub.append(no_sub)
        process_inhibition.append({
            "process": name,
            "pH_inhibition": inhibition_degree(I_pH_vec[i]),
            "h2_inhibition": inhibition_degree(I_h2_vec[i]),
            "n_limitation": inhibition_degree(Iin),
            "nh3_inhibition": inhibition_degree(I_nh3_vec[i]),
            "substrate_limitation": inhibition_degree(substrate[i]),
            "overall_inhibition": inhibition_degree(with_sub),
            "without_substrate": inhibition_degree(no_sub),
            "process_rate": float(rates_vec[i]),
        })

    # Health + safety summary uses the 8 primary rows (process-in-series
    # argument) so tier language stays aligned with adm1-mcp.
    without_sub_pct = [inhibition_degree(x) for x in overall_activity_no_sub]
    max_inhibition = max(without_sub_pct) if without_sub_pct else 0.0
    limiting_idx = without_sub_pct.index(max_inhibition) if without_sub_pct else 0
    limiting_process = _MADM1_PRIMARY_PROCESSES[limiting_idx] if without_sub_pct else ""
    health_status = summarize_health_status(max_inhibition)

    margin_pct = [inhibition_degree(x) for x in substrate]
    min_margin = min(margin_pct) if margin_pct else 0.0
    min_margin_idx = margin_pct.index(min_margin) if margin_pct else 0
    min_margin_process = _MADM1_PRIMARY_PROCESSES[min_margin_idx] if margin_pct else ""
    safety_status, safety_message = summarize_safety_status(min_margin)

    factors = [
        {"type": "pH Inhibition", "value": max([inhibition_degree(v) for v in I_pH.values()] or [0.0])},
        {"type": "H2 Inhibition", "value": max([inhibition_degree(v) for v in I_h2.values()] or [0.0])},
        {"type": "N Limitation", "value": inhibition_degree(Iin)},
        {"type": "NH3 Inhibition", "value": inhibition_degree(Inh3)},
    ]
    # H2S inhibition per population — taken from our rich ``I_h2s`` dict
    # (mADM1 stores per-population factors during rate calls). Replaces
    # the old external-speciation ``analyze_inhibition`` helper.
    if I_h2s:
        worst_h2s = max([inhibition_degree(v) for v in I_h2s.values()] or [0.0])
        factors.append({"type": "H2S Inhibition (max population)", "value": worst_h2s})
    factors.sort(key=lambda x: x["value"], reverse=True)

    recommendations = _madm1_recommendations(factors, pH_value)
    safety_recommendations = _safety_recommendations(min_margin)

    h2s_detail = None
    if I_h2s:
        h2s_detail = {
            pop: {"activity": float(v), "inhibition_pct": inhibition_degree(v)}
            for pop, v in I_h2s.items()
        }

    return {
        "success": True,
        "engine": "madm1",
        "current_pH": round(pH_value, 2),
        "process_inhibition": process_inhibition,
        "pao_processes": pao_rows,
        "srb_processes": srb_rows,
        "hfo_processes": hfo_rows,
        "mmp_processes": mmp_rows,
        "gas_transfer": gas_transfer,
        "h2s_per_population": h2s_detail,
        "health_assessment": {
            "status": health_status,
            "max_inhibition": round(max_inhibition, 1),
            "limiting_process": limiting_process,
        },
        "safety_assessment": {
            "status": safety_status,
            "min_safety_margin": round(min_margin, 1),
            "limiting_process": min_margin_process,
            "message": safety_message,
        },
        "inhibition_factors": factors,
        "recommendations": recommendations,
        "safety_recommendations": safety_recommendations,
    }


def _read_madm1_root_data(system: Any) -> dict | None:
    """Traverse system → reactor → model → rate_function → params['root'].data.

    Returns ``None`` if any hop is missing; callers degrade gracefully.
    """
    if system is None:
        return None
    try:
        # Try the adm1-mcp access pattern first
        root = system._path[0].model.rate_function._params["root"]
        return getattr(root, "data", None)
    except (AttributeError, KeyError, IndexError, TypeError):
        pass
    # Fallback: scan units for something with a model.rate_function._params['root']
    try:
        for unit in getattr(system, "units", []) or []:
            model = getattr(unit, "model", None)
            if model is None:
                continue
            rate_function = getattr(model, "rate_function", None)
            if rate_function is None:
                continue
            params = getattr(rate_function, "_params", None) or getattr(rate_function, "params", None)
            if params is None:
                continue
            root = params.get("root") if hasattr(params, "get") else None
            if root is not None and hasattr(root, "data"):
                return root.data
    except Exception:
        logger.debug("Could not locate root.data on any system unit", exc_info=True)
    return None


def _madm1_required_block(root_data: dict[str, Any], key: str) -> Any:
    value = root_data.get(key)
    if value is None:
        raise KeyError(key)
    return value


def _madm1_required_subkey(block: dict[str, Any], parent: str, key: str) -> Any:
    value = block.get(key)
    if value is None:
        raise KeyError(f"{parent}.{key}")
    return value


def _madm1_missing_key(key: str) -> dict[str, Any]:
    return {
        "success": False,
        "reason": f"mADM1 root.data missing key: '{key}' — rate_function instrumentation is out of date",
    }


def _madm1_index_list(process_indices: dict[str, Any], category: str, process_count: int) -> list[int]:
    raw = process_indices.get(category)
    if raw is None:
        raise KeyError(f"process_indices.{category}")
    values = [int(raw)] if isinstance(raw, (int, np.integer)) else [int(v) for v in raw]
    return [idx if idx >= 0 else process_count + idx for idx in values]


def _madm1_process_name(process_names: list[str], idx: int) -> str:
    if idx < 0 or idx >= len(process_names):
        return f"process_{idx}"
    return str(process_names[idx])


def _madm1_process_rate(process_rates: list[Any], idx: int) -> float:
    if idx < 0 or idx >= len(process_rates):
        return 0.0
    return float(process_rates[idx])


def _madm1_factor_vector(value: Any, size: int, default: float = 1.0) -> np.ndarray:
    if isinstance(value, (list, tuple, np.ndarray)):
        arr = np.asarray(value, dtype=float).reshape(-1)
    elif value is None:
        arr = np.full(size, default, dtype=float)
    else:
        arr = np.full(size, float(value), dtype=float)
    if arr.size == 1 and size != 1:
        arr = np.full(size, float(arr[0]), dtype=float)
    if arr.size < size:
        arr = np.pad(arr, (0, size - arr.size), constant_values=default)
    return arr[:size]


def _madm1_required_exact_vector(
    value: Any,
    size: int,
    *,
    key: str,
) -> np.ndarray:
    """Return one exact, finite instrumentation vector or fail closed.

    Mineral SI and kinetic arrays are positional against the phase registry.
    Padding a short vector with SI=1 or k=0 fabricates equilibrium/inactivity
    for whichever phases were omitted and can conceal registry drift.
    """

    if not isinstance(value, (list, tuple, np.ndarray)):
        raise KeyError(f"{key} must be an explicit vector of length {size}")
    array = np.asarray(value, dtype=float).reshape(-1)
    if array.size != size:
        raise KeyError(f"{key} length {array.size} != required {size}")
    if not np.all(np.isfinite(array)):
        raise KeyError(f"{key} contains nonfinite values")
    return array


def _madm1_pao_rows(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    pao_data: dict[str, Any],
) -> list[dict[str, Any]]:
    process_count = len(process_names)
    uptake_indices = _madm1_index_list(process_indices, "pao_vfa_uptake", process_count)
    decay_indices = _madm1_index_list(process_indices, "pao_decay", process_count)
    vfa_monod = _madm1_factor_vector(_madm1_required_subkey(pao_data, "pao", "vfa_Monod"), len(uptake_indices))
    pp_monod = float(_madm1_required_subkey(pao_data, "pao", "pp_Monod"))
    sulfate_monod = _madm1_factor_vector(_madm1_required_subkey(pao_data, "pao", "sulfate_Monod"), len(uptake_indices))
    vfa_distribution = _madm1_factor_vector(pao_data.get("vfa_distribution", 1.0), len(uptake_indices))

    rows: list[dict[str, Any]] = []
    for i, idx in enumerate(uptake_indices):
        activity = float(vfa_monod[i] * pp_monod * sulfate_monod[i] * vfa_distribution[i])
        rows.append({
            "process": _madm1_process_name(process_names, idx),
            "category": "pao_vfa_uptake",
            "activity": activity,
            "inhibition_pct": inhibition_degree(activity),
            "attributes": {
                "vfa_Monod": inhibition_degree(vfa_monod[i]),
                "pp_Monod": inhibition_degree(pp_monod),
                "sulfate_Monod": inhibition_degree(sulfate_monod[i]),
                "vfa_share_pct": float(vfa_distribution[i] * 100.0),
            },
            "process_rate": _madm1_process_rate(process_rates, idx),
        })

    for idx in decay_indices:
        rows.append({
            "process": _madm1_process_name(process_names, idx),
            "category": "pao_decay",
            "activity": 1.0,
            "inhibition_pct": 0.0,
            "attributes": {"first_order_decay": 0.0},
            "process_rate": _madm1_process_rate(process_rates, idx),
        })
    return rows


def _madm1_srb_rows(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    srb_data: dict[str, Any],
    I_pH: dict[str, Any],
    I_h2s: dict[str, Any],
    I_nutrients: dict[str, Any],
    process_count: int,
) -> list[dict[str, Any]]:
    srb_indices = _madm1_index_list(process_indices, "srb_uptake", process_count)
    substrate_monod = _madm1_factor_vector(_madm1_required_subkey(srb_data, "srb", "substrate_Monod"), len(srb_indices))
    sulfate_monod = _madm1_factor_vector(_madm1_required_subkey(srb_data, "srb", "sulfate_Monod"), len(srb_indices))
    c4_distribution = _madm1_factor_vector(srb_data.get("c4_distribution"), 2, default=1.0)
    nutrient_factor = float(I_nutrients.get("combined", 1.0))

    rows: list[dict[str, Any]] = []
    for i, idx in enumerate(srb_indices):
        pH_factor = float(I_pH.get(_MADM1_SRB_PH_KEYS[i], 1.0))
        h2s_factor = float(I_h2s.get(_MADM1_SRB_H2S_KEYS[i], 1.0))
        activity = float(pH_factor * nutrient_factor * substrate_monod[i] * sulfate_monod[i] * h2s_factor)
        attrs: dict[str, float] = {
            "pH_inhibition": inhibition_degree(pH_factor),
            "nutrient_limitation": inhibition_degree(nutrient_factor),
            "substrate_Monod": inhibition_degree(substrate_monod[i]),
            "sulfate_Monod": inhibition_degree(sulfate_monod[i]),
            "h2s_inhibition": inhibition_degree(h2s_factor),
        }
        if i >= 3 and c4_distribution.size >= 2:
            share = float(c4_distribution[i - 3])
            activity *= share
            attrs["c4_share_pct"] = share * 100.0
        rows.append({
            "process": _madm1_process_name(process_names, idx),
            "category": "srb_uptake",
            "activity": activity,
            "inhibition_pct": inhibition_degree(activity),
            "attributes": attrs,
            "process_rate": _madm1_process_rate(process_rates, idx),
        })
    return rows


def _madm1_hfo_rows(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    hfo_data: dict[str, Any],
    process_count: int,
) -> list[dict[str, Any]]:
    indices = _madm1_index_list(process_indices, "hfo_chemical_p", process_count)
    adsorption = float(_madm1_required_subkey(hfo_data, "hfo", "adsorption_factor"))
    desorption = float(_madm1_required_subkey(hfo_data, "hfo", "desorption_factor"))
    rows: list[dict[str, Any]] = []

    for idx in indices:
        if idx in (40, 41):
            category = "hfo_adsorption"
            activity = adsorption
            attrs = {"adsorption_factor": inhibition_degree(adsorption)}
        elif idx in (44, 45):
            category = "hfo_desorption"
            activity = desorption
            attrs = {"desorption_factor": inhibition_degree(desorption)}
        else:
            category = "hfo_aging"
            activity = 1.0
            attrs = {"first_order_decay": 0.0}
        rows.append({
            "process": _madm1_process_name(process_names, idx),
            "category": category,
            "activity": float(activity),
            "inhibition_pct": inhibition_degree(activity),
            "attributes": attrs,
            "process_rate": _madm1_process_rate(process_rates, idx),
        })
    return rows


def _madm1_hfo_host_transformation_rows(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    hfo_data: dict[str, Any],
    process_count: int,
) -> list[dict[str, Any]]:
    """Expose finite-donor bound/aged HFO destruction as first-class rows."""

    indices = _madm1_index_list(
        process_indices, "hfo_host_transformation", process_count
    )
    constants = _madm1_required_exact_vector(
        _madm1_required_subkey(
            hfo_data, "hfo", "host_reduction_rate_constants"
        ),
        len(indices),
        key="hfo.host_reduction_rate_constants",
    )
    return [
        {
            "process": _madm1_process_name(process_names, idx),
            "category": "hfo_host_reduction_or_sulfidation",
            # These transformations are governed by donor concentration and
            # host availability rather than a normalized inhibition factor.
            "activity": 1.0,
            "inhibition_pct": 0.0,
            "attributes": {
                "screening_rate_constant": float(constants[offset]),
                "kinetics_calibrated": False,
            },
            "process_rate": _madm1_process_rate(process_rates, idx),
        }
        for offset, idx in enumerate(indices)
    ]


def _madm1_mmp_rows(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    mmp_data: dict[str, Any],
    process_count: int,
) -> list[dict[str, Any]]:
    indices = _madm1_index_list(process_indices, "mmp", process_count)
    mineral_names = list(_madm1_required_subkey(mmp_data, "mmp", "mineral_names") or [])
    if len(mineral_names) != len(indices):
        raise KeyError(
            f"mmp.mineral_names length {len(mineral_names)} != required {len(indices)}"
        )
    saturation_indices = _madm1_required_exact_vector(
        _madm1_required_subkey(mmp_data, "mmp", "saturation_indices"),
        len(indices),
        key="mmp.saturation_indices",
    )
    k_mmp = _madm1_required_exact_vector(
        _madm1_required_subkey(mmp_data, "mmp", "k_mmp"),
        len(indices),
        key="mmp.k_mmp",
    )
    rows: list[dict[str, Any]] = []

    for i, idx in enumerate(indices):
        si = float(saturation_indices[i])
        activity = float(abs(si - 1.0))
        rows.append({
            "process": _madm1_process_name(process_names, idx),
            "category": "mmp",
            "activity": activity,
            "inhibition_pct": 0.0,
            "attributes": {
                "mineral": mineral_names[i] if i < len(mineral_names) else _madm1_process_name(process_names, idx),
                "saturation_index": si,
                "k_mmp": float(k_mmp[i]),
            },
            "process_rate": _madm1_process_rate(process_rates, idx),
        })
    return rows


def _madm1_gas_transfer_block(
    process_names: list[str],
    process_indices: dict[str, Any],
    process_rates: list[Any],
    gas_transfer_data: dict[str, Any],
    process_count: int,
) -> dict[str, Any]:
    gas_indices = _madm1_index_list(process_indices, "gas_transfer", process_count)
    for key in ("species", "dissolved_kg_m3", "KH_times_p_kg_m3", "driving_force_kg_m3", "kLa"):
        _madm1_required_subkey(gas_transfer_data, "gas_transfer", key)
    block = dict(gas_transfer_data)
    block["process_names"] = [_madm1_process_name(process_names, idx) for idx in gas_indices]
    block["process_rates"] = [_madm1_process_rate(process_rates, idx) for idx in gas_indices]
    return block


def _madm1_recommendations(factors: list[dict], pH: float) -> list[str]:
    if not factors or factors[0]["value"] < 10.0:
        return ["The reactor is operating optimally. No adjustments needed."]
    top_type = factors[0]["type"]
    top_value = factors[0]["value"]
    if top_type == "pH Inhibition":
        return [
            f"pH Inhibition Detected ({top_value:.1f}%)",
            f"Current pH: {pH:.2f}",
            "If pH is too low, add alkalinity (e.g., sodium bicarbonate)",
            "If pH is too high, consider adding CO2 or reducing alkalinity",
            "Monitor VFA accumulation which can cause pH drops",
        ]
    if top_type == "H2 Inhibition":
        return [
            f"H2 Inhibition Detected ({top_value:.1f}%)",
            "Optimize mixing to enhance H2 transfer to methanogens",
            "Ensure healthy hydrogenotrophic methanogen population",
            "Consider reducing organic loading rate temporarily",
        ]
    if top_type == "N Limitation":
        return [
            f"Nitrogen Limitation Detected ({top_value:.1f}%)",
            "Supplement feedstock with nitrogen-rich substrates",
            "Consider adding ammonium or urea as nitrogen source",
            "Maintain C:N ratio between 20:1 and 30:1",
        ]
    if top_type == "NH3 Inhibition":
        return [
            f"Ammonia Inhibition Detected ({top_value:.1f}%)",
            "Reduce nitrogen-rich feedstocks",
            "Consider lowering pH slightly (within safe range) to reduce free ammonia",
            "Dilute reactor contents or increase HRT",
            "Investigate ammonia-tolerant methanogen species",
        ]
    # H2S or other factor from h2s_inhibition overlay
    return [
        f"{top_type} Detected ({top_value:.1f}%)",
        "See H2S analysis block for speciation and mitigation options",
    ]


# --------------------------------------------------------------------------
# mASM2d analysis (computed post-hoc from final state + params)
# --------------------------------------------------------------------------


# Ordered list of 19 ASM2d/mASM2d biological processes matching
# ``qsdsan.processes._asm2d.rhos_asm2d`` slice conventions. Indices here
# must stay in lock-step with the QSDsan source — if QSDsan reorders
# processes in a future release, update this list and the
# ``_asm2d_process_inhibitions`` mapping below.
_ASM2D_PROCESSES = [
    "Hydrolysis (aerobic)",            # 0
    "Hydrolysis (anoxic)",             # 1
    "Hydrolysis (anaerobic)",          # 2
    "Heterotroph growth on SF (aerobic)",   # 3
    "Heterotroph growth on SA (aerobic)",   # 4
    "Heterotroph growth on SF (anoxic)",    # 5
    "Heterotroph growth on SA (anoxic)",    # 6
    "Fermentation",                    # 7
    "Heterotroph lysis",               # 8
    "PAO PHA storage",                 # 9
    "PAO P-storage (aerobic)",         # 10
    "PAO P-storage (anoxic)",          # 11
    "PAO growth (aerobic)",            # 12
    "PAO growth (anoxic)",             # 13
    "PAO biomass lysis",               # 14
    "PAO PP lysis",                    # 15
    "PAO PHA lysis",                   # 16
    "Autotroph growth (nitrification)",  # 17
    "Autotroph lysis",                 # 18
]


def _monod(S: float, K: float) -> float:
    """Standard Monod saturation M = S / (K + S). Defensive against negatives."""
    if S <= 0 or K <= 0:
        return 0.0
    return float(S / (K + S))


def analyze_asm2d_process_health(
    system: Any,
) -> dict[str, Any]:
    """Build per-process health report for an ASM2d/mASM2d simulation.

    Since QSDsan's built-in ``_rhos_asm2d`` does not instrument itself,
    we reconstruct Monod activity factors post-hoc from the final state
    vector + kinetic params. This matches the physics of the stock rate
    function bit-for-bit (see ``qsdsan/processes/_asm2d.py``) so the
    reported inhibitions stay consistent with the simulator output.

    Parameters
    ----------
    system : qsdsan.System
        System with an ASM2d/mASM2d CSTR-style reactor. The first unit's
        ``_state`` (final state_arr) + ``model.rate_function._params``
        must be accessible.

    Returns
    -------
    dict
        Payload conforming to the module-level schema; emits 19 process
        rows covering the full ASM2d biological process set.
    """
    payload = _read_asm2d_state_and_params(system)
    if payload is None:
        return {"success": False, "reason": "ASM2d state/params unavailable"}
    state_arr, params = payload

    Ks_o2 = np.asarray(params.get("Ks_o2", [0.2] * 6), dtype=float)
    Ks_no3 = np.asarray(params.get("Ks_no3", [0.5] * 6), dtype=float)
    Ks_nh4 = np.asarray(params.get("Ks_nh4", [0.05] * 3), dtype=float)
    Ks_po4 = np.asarray(params.get("Ks_po4", [0.01] * 3), dtype=float)
    Kx = float(params.get("K_X", 0.1))
    Kf = float(params.get("K_F", 4.0))
    Kfe = float(params.get("K_fe", 4.0))
    Ka_H = float(params.get("K_A_H", 4.0))
    Ka_PAO = float(params.get("K_A_PAO", 4.0))
    Kp_stor = float(params.get("K_P_S", 0.2))
    Kpp = float(params.get("K_PP", 0.01))
    Kmax = float(params.get("K_MAX", 0.34))
    Kipp = float(params.get("K_IPP", 0.02))
    Kpha = float(params.get("K_PHA", 0.01))

    # mASM2d state slice — see _asm2d.py line 609. Shape:
    # S_O2, S_N2, S_NH4, S_NO3, S_PO4, S_F, S_A, S_I, S_IC, S_K, S_Mg,
    # X_I, X_S, X_H, X_PAO, X_PP, X_PHA, X_AUT, ...
    # ASM2d stock: S_O2, S_F, S_A, S_I, S_NH4, S_N2, S_NO3, S_PO4, S_ALK,
    # X_I, X_S, X_H, X_PAO, X_PP, X_PHA, X_AUT, X_MeOH, X_MeP
    # We probe species names from params['cmps'] when available.
    state_map = _resolve_asm2d_state_map(state_arr, params)
    S_O2 = state_map.get("S_O2", 0.0)
    S_NH4 = state_map.get("S_NH4", 0.0)
    S_NO3 = state_map.get("S_NO3", 0.0)
    S_PO4 = state_map.get("S_PO4", 0.0)
    S_F = state_map.get("S_F", 0.0)
    S_A = state_map.get("S_A", 0.0)
    X_S = state_map.get("X_S", 0.0)
    X_H = state_map.get("X_H", 0.0)
    X_PAO = state_map.get("X_PAO", 0.0)
    X_PP = state_map.get("X_PP", 0.0)
    X_PHA = state_map.get("X_PHA", 0.0)

    # Monod factor vectors indexed by process position
    aero = np.array([_monod(S_O2, k) for k in Ks_o2])  # 6-vector (H-hydro, H-growth, H-lysis?, PAO, PAO-lysis?, AUT)
    anox = np.array([_monod(S_NO3, k) for k in Ks_no3])
    nutr_nh4 = np.array([_monod(S_NH4, k) for k in Ks_nh4])  # [H, PAO, AUT]
    nutr_po4 = np.array([_monod(S_PO4, k) for k in Ks_po4])
    nutrients = nutr_nh4 * nutr_po4

    # Substrate Monod factors
    M_Xratio = _monod((X_S / X_H) if X_H > 0 else 0.0, Kx)
    M_F = _monod(S_F, Kf)
    M_Fe = _monod(S_F, Kfe)
    M_A_H = _monod(S_A, Ka_H)
    M_A_PAO = _monod(S_A, Ka_PAO)
    frac_F = (S_F / (S_F + S_A)) if (S_F + S_A) > 0 else 0.0
    frac_A = (S_A / (S_F + S_A)) if (S_F + S_A) > 0 else 0.0
    M_PHA = _monod((X_PHA / X_PAO) if X_PAO > 0 else 0.0, Kpha)
    M_PP = _monod((X_PP / X_PAO) if X_PAO > 0 else 0.0, Kpp)
    M_PP_max = _monod((Kmax - (X_PP / X_PAO)) if X_PAO > 0 else Kmax, Kipp)
    M_P_stor = _monod(S_PO4, Kp_stor)

    # Build per-process activity factor and attribute-level inhibition
    rows: list[dict[str, Any]] = []

    def _append(idx: int, activity: float, attrs: dict[str, float]) -> None:
        rows.append({
            "process": _ASM2D_PROCESSES[idx],
            "activity": float(activity),
            "inhibition_pct": inhibition_degree(activity),
            "attributes": {k: inhibition_degree(v) for k, v in attrs.items()},
        })

    # Hydrolysis (3): rate * Monod(X_S/X_H, Kx) * environment modifier
    for idx, env_label, env in (
        (0, "aerobic_O2", aero[0]),
        (1, "anoxic_NO3", (1 - aero[0]) * anox[0]),
        (2, "anaerobic", (1 - aero[0]) * (1 - anox[0])),
    ):
        activity = env * M_Xratio
        _append(idx, activity, {"env": env, "X_ratio": M_Xratio})

    # Heterotroph growth on SF/SA, aerobic/anoxic (idx 3-6):
    # multiplier = nutrients[0] * frac * M_substrate * env
    for idx, env_label, env, is_F, is_anox in (
        (3, "aerobic_SF", aero[1], True, False),
        (4, "aerobic_SA", aero[1], False, False),
        (5, "anoxic_SF", (1 - aero[1]) * anox[1], True, True),
        (6, "anoxic_SA", (1 - aero[1]) * anox[1], False, True),
    ):
        sub_factor = (M_F * frac_F) if is_F else (M_A_H * frac_A)
        activity = env * nutrients[0] * sub_factor
        _append(idx, activity, {
            "env": env,
            "nutrients": nutrients[0],
            "substrate": sub_factor,
        })

    # Fermentation (idx 7): (1-aero[1]) * (1-anox[1]) * M_F_fermentation
    env7 = (1 - aero[1]) * (1 - anox[1])
    activity7 = env7 * M_Fe
    _append(7, activity7, {"env": env7, "substrate_SF_fermentation": M_Fe})

    # Heterotroph lysis (idx 8): aero[1] + eta*(1-aero[1])*anox[1]
    eta_decay = np.asarray(params.get("eta_decay", [0.5, 0.33, 0.33, 0.33, 0.5]), dtype=float)
    lysis_H = aero[1] + eta_decay[0] * (1 - aero[1]) * anox[1]
    _append(8, lysis_H, {"effective_decay": lysis_H})

    # PAO storage of PHA (idx 9): M_A_PAO * M_PP
    activity9 = M_A_PAO * M_PP
    _append(9, activity9, {"M_A_PAO": M_A_PAO, "M_PP": M_PP})

    # PAO P-storage aerobic (idx 10): aero[3] * M_PHA * M_PP_max * M_P_stor
    activity10 = aero[3] * M_PHA * M_PP_max * M_P_stor
    _append(10, activity10, {
        "env": aero[3], "M_PHA": M_PHA, "M_PP_max": M_PP_max, "M_P_stor": M_P_stor,
    })

    # PAO P-storage anoxic (idx 11)
    activity11 = (1 - aero[3]) * anox[3] * M_PHA * M_PP_max * M_P_stor
    _append(11, activity11, {
        "env": (1 - aero[3]) * anox[3],
        "M_PHA": M_PHA, "M_PP_max": M_PP_max, "M_P_stor": M_P_stor,
    })

    # PAO growth aerobic (idx 12): aero[3] * nutrients[1] * M_PHA
    activity12 = aero[3] * nutrients[1] * M_PHA
    _append(12, activity12, {
        "env": aero[3], "nutrients": nutrients[1], "M_PHA": M_PHA,
    })

    # PAO growth anoxic (idx 13)
    activity13 = (1 - aero[3]) * anox[3] * nutrients[1] * M_PHA
    _append(13, activity13, {
        "env": (1 - aero[3]) * anox[3], "nutrients": nutrients[1], "M_PHA": M_PHA,
    })

    # PAO biomass lysis / PP lysis / PHA lysis (idx 14/15/16)
    lysis_PAO = aero[3] + eta_decay[1] * (1 - aero[3]) * anox[3]
    lysis_PP = aero[3] + eta_decay[2] * (1 - aero[3]) * anox[3]
    lysis_PHA = aero[3] + eta_decay[3] * (1 - aero[3]) * anox[3]
    _append(14, lysis_PAO, {"effective_decay": lysis_PAO})
    _append(15, lysis_PP, {"effective_decay": lysis_PP})
    _append(16, lysis_PHA, {"effective_decay": lysis_PHA})

    # Autotroph growth (idx 17): aero[5] * nutrients[2]
    activity17 = aero[5] * nutrients[2]
    _append(17, activity17, {"env": aero[5], "nutrients": nutrients[2]})

    # Autotroph lysis (idx 18)
    lysis_AUT = aero[5] + eta_decay[4] * (1 - aero[5]) * anox[5]
    _append(18, lysis_AUT, {"effective_decay": lysis_AUT})

    # Summaries: use the lysis-excluded subset for health since lysis
    # activity is not "inhibition" in the usual proposal sense.
    active_processes = [row for row in rows if "lysis" not in row["process"].lower()]
    inhibition_pcts = [r["inhibition_pct"] for r in active_processes]
    max_inhibition = max(inhibition_pcts) if inhibition_pcts else 0.0
    limiting_idx = inhibition_pcts.index(max_inhibition) if inhibition_pcts else 0
    limiting_process = active_processes[limiting_idx]["process"] if active_processes else ""

    # Safety margin: use the worst substrate Monod across growth processes
    substrate_monods = [M_F, M_A_H, M_A_PAO, nutr_nh4[2], nutr_po4[2]]
    min_monod = min([m for m in substrate_monods if m > 0], default=1.0)
    min_margin = inhibition_degree(min_monod)
    safety_status, safety_message = summarize_safety_status(min_margin)

    factors = [
        {"type": "O2 limitation (heterotroph)", "value": inhibition_degree(aero[1])},
        {"type": "NO3 limitation (anoxic)", "value": inhibition_degree(anox[1])},
        {"type": "SF substrate limitation", "value": inhibition_degree(M_F)},
        {"type": "SA substrate limitation", "value": inhibition_degree(M_A_H)},
        {"type": "NH4 limitation (autotroph)", "value": inhibition_degree(nutr_nh4[2])},
        {"type": "NH4 limitation (heterotroph)", "value": inhibition_degree(nutr_nh4[0])},
        {"type": "PO4 limitation (autotroph)", "value": inhibition_degree(nutr_po4[2])},
        {"type": "O2 limitation (autotroph nitrifier)", "value": inhibition_degree(aero[5])},
    ]
    factors.sort(key=lambda x: x["value"], reverse=True)

    recommendations = _asm2d_recommendations(factors, S_O2, S_NH4, S_PO4)
    safety_recommendations = _safety_recommendations(min_margin)

    return {
        "success": True,
        "engine": "asm2d",
        "dissolved_oxygen_mg_l": float(S_O2),
        "nh4_mg_l_n": float(S_NH4),
        "no3_mg_l_n": float(S_NO3),
        "po4_mg_l_p": float(S_PO4),
        "process_inhibition": rows,
        "health_assessment": {
            "status": summarize_health_status(max_inhibition),
            "max_inhibition": round(max_inhibition, 1),
            "limiting_process": limiting_process,
        },
        "safety_assessment": {
            "status": safety_status,
            "min_safety_margin": round(min_margin, 1),
            "limiting_process": "substrate_Monod",
            "message": safety_message,
        },
        "inhibition_factors": factors,
        "recommendations": recommendations,
        "safety_recommendations": safety_recommendations,
    }


def _read_asm2d_state_and_params(system: Any) -> tuple[np.ndarray, dict] | None:
    """Locate the first ASM2d-like reactor and return (state_arr, params)."""
    if system is None:
        return None
    try:
        for unit in getattr(system, "units", []) or []:
            state_arr = _unit_final_state(unit)
            if state_arr is None:
                continue
            for attr_name in ("model", "_model", "suspended_growth_model"):
                model = getattr(unit, attr_name, None)
                if model is None:
                    continue
                rate_function = getattr(model, "rate_function", None)
                if rate_function is None:
                    continue
                params = getattr(rate_function, "_params", None) or getattr(rate_function, "params", None)
                if not params:
                    continue
                # Accept both stubbed diagnostic payloads and live QSDsan ASM-family models.
                if hasattr(params, "__contains__") and (
                    "Ks_o2" in params or "K_O2_H" in params or "K_X" in params
                ):
                    return np.asarray(state_arr, dtype=float), dict(params)
    except Exception:
        logger.debug("Failed to locate ASM2d state/params", exc_info=True)
    return None


def _unit_final_state(unit: Any) -> np.ndarray | None:
    """Pull the final state array from a QSDsan reactor unit."""
    for attr in ("_state", "state", "_concs"):
        arr = getattr(unit, attr, None)
        if arr is not None:
            try:
                return np.asarray(arr, dtype=float)
            except (TypeError, ValueError):
                continue
    # Fallback: try effluent outs
    try:
        outs = getattr(unit, "outs", None) or []
        if outs:
            eff = outs[0]
            conc = getattr(eff, "conc", None)
            if conc is not None:
                return np.asarray(conc, dtype=float)
    except Exception:
        pass
    return None


def _resolve_asm2d_state_map(state_arr: np.ndarray, params: dict) -> dict[str, float]:
    """Build a species-name → concentration map.

    Prefers ``params['cmps']`` (CompiledComponents) for canonical
    ordering; falls back to mASM2d's positional layout when not found.
    """
    # Try to resolve via components
    cmps = params.get("cmps") if hasattr(params, "get") else None
    if cmps is not None:
        try:
            ids = list(getattr(cmps, "IDs", []))
            if ids and len(ids) >= len(state_arr):
                return {sid: float(state_arr[i]) for i, sid in enumerate(ids)}
        except Exception:
            logger.debug("cmps.IDs lookup failed; falling back to positional", exc_info=True)

    # mASM2d positional layout (from _asm2d.py line 609)
    masm2d_positions = [
        "S_O2", "S_N2", "S_NH4", "S_NO3", "S_PO4", "S_F", "S_A", "S_I",
        "S_IC", "S_K", "S_Mg",
        "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AUT",
        "S_Ca", "X_CaCO3", "X_struv", "X_newb", "X_ACP", "X_MgCO3",
        "X_AlOH", "X_AlPO4", "X_FeOH", "X_FePO4",
        "S_Na", "S_Cl",
    ]
    # ASM2d stock layout (19 components)
    asm2d_positions = [
        "S_O2", "S_F", "S_A", "S_I", "S_NH4", "S_N2", "S_NO3", "S_PO4", "S_ALK",
        "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AUT", "X_MeOH", "X_MeP", "H2O",
    ]
    layout = masm2d_positions if len(state_arr) >= len(masm2d_positions) else asm2d_positions
    return {
        layout[i]: float(state_arr[i])
        for i in range(min(len(layout), len(state_arr)))
    }


def _asm2d_recommendations(
    factors: list[dict[str, Any]],
    S_O2: float,
    S_NH4: float,
    S_PO4: float,
) -> list[str]:
    if not factors or factors[0]["value"] < 10.0:
        return ["The reactor is operating within acceptable bounds for the dominant biological processes."]
    top_type = factors[0]["type"]
    top_value = factors[0]["value"]
    if "O2 limitation (heterotroph)" in top_type:
        return [
            f"Aerobic heterotroph O2 Limitation Detected ({top_value:.1f}%)",
            f"Current bulk DO: {S_O2:.2f} mg/L",
            "If DO is < 2 mg/L, increase blower output or reduce organic loading",
            "Check diffuser condition and SOTE if aeration demand has grown",
        ]
    if "O2 limitation (autotroph nitrifier)" in top_type:
        return [
            f"Nitrifier O2 Limitation Detected ({top_value:.1f}%)",
            "Autotrophs need DO >= 2 mg/L (K_O2_AUT ~ 0.5). Raise aerobic-zone DO setpoint",
            "Check SRT against minimum for nitrification at current temperature",
        ]
    if "NH4 limitation (autotroph)" in top_type:
        return [
            f"Nitrifier NH4 Limitation Detected ({top_value:.1f}%)",
            f"Current NH4-N: {S_NH4:.2f} mg/L (effluent will be polished below requirement)",
            "This is typically a sign of over-designed aerobic volume; consider SRT reduction",
        ]
    if "PO4 limitation" in top_type:
        return [
            f"P Limitation Detected ({top_value:.1f}%)",
            f"Current PO4-P: {S_PO4:.2f} mg/L",
            "Check influent P concentration; dose supplemental P if below 1 mg/L",
        ]
    if "SF substrate limitation" in top_type or "SA substrate limitation" in top_type:
        return [
            f"{top_type} ({top_value:.1f}%)",
            "High substrate depletion indicates mature biomass; verify SRT within design range",
        ]
    return [
        f"{top_type} Detected ({top_value:.1f}%)",
        "Review process setpoints against the inhibition factor producing the most activity loss",
    ]
