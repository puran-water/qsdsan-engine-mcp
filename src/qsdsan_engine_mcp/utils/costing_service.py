"""Strict public costing service for solved QSDsan flowsheets."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from qsdsan_engine_mcp.core.unit_registry import get_unit_spec
from qsdsan_engine_mcp.utils.tea_wrapper import create_tea, get_capex_breakdown, get_opex_summary, get_utility_costs


def _resolve_product_stream(system) -> Optional[Any]:
    for stream in getattr(system, "products", []):
        if stream is None:
            continue
        if getattr(stream, "phase", "l") == "g":
            continue
        return stream
    for stream in getattr(system, "streams", []):
        if stream is None:
            continue
        if getattr(stream, "phase", "l") == "g":
            continue
        return stream
    return None


def _normalize_unit_ids(unit_ids: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for unit_id in unit_ids:
        if unit_id in seen:
            continue
        ordered.append(unit_id)
        seen.add(unit_id)
    return ordered


def build_costing_report(
    system,
    session,
    *,
    costed_unit_ids: Iterable[str],
    economic_assumptions: Optional[Dict[str, Any]] = None,
    tea_profile: str = "qsdsan_simpletea",
) -> Dict[str, Any]:
    """
    Build a strict public costing report for an already solved system.

    The report is certified only when:
    - costing is explicitly enabled for the session
    - every requested unit is declared cost-supported on the public surface
    - every requested unit returns non-zero upstream cost data at runtime
    """
    selected_unit_ids = _normalize_unit_ids(costed_unit_ids)
    assumptions = {
        "discount_rate": 0.05,
        "lifetime_years": 20,
        "uptime_ratio": 0.95,
        "annual_labor": 0.0,
        "annual_maintenance_factor": 0.03,
        "electricity_price": 0.07,
    }
    assumptions.update(economic_assumptions or {})

    unsupported_units = []
    for unit_id in selected_unit_ids:
        if unit_id not in session.units:
            unsupported_units.append(
                {
                    "unit_id": unit_id,
                    "reason": "unit_not_found_in_session",
                }
            )
            continue
        spec = get_unit_spec(session.units[unit_id].unit_type)
        if not spec.costing.costing_supported:
            unsupported_units.append(
                {
                    "unit_id": unit_id,
                    "unit_type": spec.unit_type,
                    "reason": "unit_not_cost_supported",
                    "support_note": spec.costing.support_note,
                }
            )

    if not selected_unit_ids:
        return {
            "status": "not_configured",
            "tea_profile": tea_profile,
            "certification": {
                "cost_configured": False,
                "cost_supported": False,
                "cost_certified": False,
            },
            "error": "No units selected for costing. Call add_unit_costing() first.",
        }

    tea = create_tea(system=system, **assumptions)
    if tea is None:
        return {
            "status": "error",
            "tea_profile": tea_profile,
            "certification": {
                "cost_configured": True,
                "cost_supported": False,
                "cost_certified": False,
            },
            "error": "Failed to create qs.SimpleTEA for the solved system.",
            "unsupported_units": unsupported_units,
        }

    capex = get_capex_breakdown(tea, include_units=True)
    opex = get_opex_summary(tea, include_utilities=True)
    utilities = get_utility_costs(tea)

    runtime_uncosted = []
    unit_costs = capex.get("units", {})
    for unit_id in selected_unit_ids:
        unit_cost = unit_costs.get(unit_id, {})
        if not unit_cost.get("has_costing"):
            runtime_uncosted.append(
                {
                    "unit_id": unit_id,
                    "unit_type": session.units[unit_id].unit_type if unit_id in session.units else None,
                    "reason": "runtime_costing_missing_or_zero",
                }
            )

    product_stream = _resolve_product_stream(system)
    product_metrics = None
    if product_stream is not None and getattr(product_stream, "F_vol", 0.0):
        try:
            flow_m3_d = float(product_stream.get_total_flow("m3/d"))
            if flow_m3_d > 0:
                hours = assumptions["uptime_ratio"] * 24 * 365
                m3_per_year = flow_m3_d * 365 * assumptions["uptime_ratio"]
                product_metrics = {
                    "product_stream": product_stream.ID,
                    "flow_m3_d": flow_m3_d,
                    "annualized_per_m3": (opex.get("total_annualized_cost", 0.0)) / m3_per_year,
                }
        except Exception:
            product_metrics = None

    cost_supported = not unsupported_units and not runtime_uncosted
    cost_certified = bool(
        cost_supported
        and capex.get("success")
        and opex.get("success")
        and capex.get("TCI", 0.0) > 0
        and (capex.get("units_with_costing", 0) >= len(selected_unit_ids))
    )

    return {
        "status": "complete" if cost_certified else "incomplete",
        "tea_profile": tea_profile,
        "certification": {
            "cost_configured": True,
            "cost_supported": cost_supported,
            "cost_certified": cost_certified,
        },
        "economic_assumptions": assumptions,
        "capital": {
            "installed_equipment_cost": capex.get("installed_equipment_cost", 0.0),
            "DPI": capex.get("DPI", 0.0),
            "TDC": capex.get("TDC", 0.0),
            "FCI": capex.get("FCI", 0.0),
            "TCI": capex.get("TCI", 0.0),
        },
        "operating": {
            "FOC": opex.get("FOC", 0.0),
            "VOC": opex.get("VOC", 0.0),
            "AOC": opex.get("AOC", 0.0),
            "annualized_equipment_cost": opex.get("annualized_equipment_cost", 0.0),
            "annualized_CAPEX": opex.get("annualized_CAPEX", 0.0),
            "total_annualized_cost": opex.get("total_annualized_cost", 0.0),
        },
        "utilities": utilities if utilities.get("success") else None,
        "product_metrics": product_metrics,
        "units": {
            unit_id: unit_costs.get(unit_id, {"has_costing": False})
            for unit_id in selected_unit_ids
        },
        "unsupported_units": unsupported_units,
        "runtime_uncosted_units": runtime_uncosted,
    }

