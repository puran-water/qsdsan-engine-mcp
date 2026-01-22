"""
TEA (Techno-Economic Analysis) Wrapper for QSDsan Systems.

Provides a unified interface to QSDsan's TEA capabilities for cost estimation
and economic analysis of wastewater treatment systems.

IMPORTANT: Many QSDsan SanUnits (CSTR, Splitter, Mixer) do NOT have complete
_cost() methods. This wrapper handles units with and without costing gracefully.

Usage:
    from utils.tea_wrapper import create_tea, get_capex_breakdown, get_opex_summary

    # After simulation completes
    tea = create_tea(system, discount_rate=0.05, lifetime_years=20)
    capex = get_capex_breakdown(tea)
    opex = get_opex_summary(tea)

Reference:
    - QSDsan TEA documentation
    - BioSTEAM TEA base class
    - DeepWiki: qsdsan-group/QSDsan TEA capabilities
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def create_tea(
    system: "qs.System",
    discount_rate: float = 0.05,
    lifetime_years: int = 20,
    uptime_ratio: float = 0.95,
    annual_labor: float = 0.0,
    annual_maintenance_factor: float = 0.03,
    electricity_price: float = 0.07,  # $/kWh
    **kwargs,
) -> Optional["qs.SimpleTEA"]:
    """
    Create TEA object for a QSDsan System.

    Args:
        system: Completed QSDsan System (must have run simulate())
        discount_rate: Annual discount rate (default 0.05 = 5%)
        lifetime_years: Project lifetime in years (default 20)
        uptime_ratio: Operating time fraction (default 0.95 = 95% uptime)
        annual_labor: Annual labor cost in $ (default 0)
        annual_maintenance_factor: Maintenance as fraction of TCI (default 0.03)
        electricity_price: Electricity price in $/kWh (default $0.07)
        **kwargs: Additional TEA parameters

    Returns:
        SimpleTEA object or None if TEA creation fails

    Example:
        >>> tea = create_tea(system, discount_rate=0.05, lifetime_years=20)
        >>> print(f"TCI: ${tea.TCI:,.0f}")
    """
    try:
        import qsdsan as qs

        # Create SimpleTEA for the system
        tea = qs.SimpleTEA(
            system=system,
            discount_rate=discount_rate,
            start_year=2024,
            lifetime=lifetime_years,
            uptime_ratio=uptime_ratio,
            lang_factor=None,  # Will use default from equipment costs
            annual_maintenance=annual_maintenance_factor,
            annual_labor=annual_labor,
            construction_schedule=None,  # Default schedule
            WC_over_FCI=0.05,  # Working capital as fraction of FCI
            finance_interest=0.0,  # No financing
            finance_years=0,
            finance_fraction=0.0,
            **kwargs,
        )

        # Store electricity price for OPEX calculations
        tea._electricity_price = electricity_price

        logger.info(f"Created TEA for system '{system.ID}': lifetime={lifetime_years}y, discount={discount_rate*100:.1f}%")
        return tea

    except Exception as e:
        logger.error(f"Failed to create TEA: {e}")
        return None


def get_capex_breakdown(
    tea: "qs.SimpleTEA",
    include_units: bool = True,
) -> Dict[str, Any]:
    """
    Get CAPEX breakdown from TEA object.

    QSDsan/BioSTEAM CAPEX hierarchy:
    - installed_equipment_cost: Sum of all unit purchase costs × installation factors
    - DPI (Direct Permanent Investment): installed_equipment_cost × (1 + site_factor)
    - TDC (Total Depreciable Capital): DPI + warehouse + other direct costs
    - FCI (Fixed Capital Investment): TDC + contingency + contractor fees
    - TCI (Total Capital Investment): FCI + startup costs + working capital

    Args:
        tea: SimpleTEA object from create_tea()
        include_units: Include per-unit cost breakdown

    Returns:
        Dict with CAPEX hierarchy and per-unit breakdown

    Example:
        >>> capex = get_capex_breakdown(tea)
        >>> print(f"TCI: ${capex['TCI']:,.0f}")
    """
    result = {
        "success": False,
        "error": None,
    }

    try:
        # Get system
        system = tea.system

        # Calculate CAPEX hierarchy
        # Note: Some values may be 0 if units lack _cost() methods
        try:
            installed_equipment = tea.installed_equipment_cost
        except Exception:
            installed_equipment = 0.0

        try:
            dpi = tea.DPI  # Direct Permanent Investment
        except Exception:
            dpi = 0.0

        try:
            tdc = tea.TDC  # Total Depreciable Capital
        except Exception:
            tdc = 0.0

        try:
            fci = tea.FCI  # Fixed Capital Investment
        except Exception:
            fci = 0.0

        try:
            tci = tea.TCI  # Total Capital Investment
        except Exception:
            tci = 0.0

        result.update({
            "success": True,
            "installed_equipment_cost": installed_equipment,
            "DPI": dpi,
            "TDC": tdc,
            "FCI": fci,
            "TCI": tci,
            "currency": "USD",
        })

        # Per-unit breakdown
        if include_units:
            unit_costs = {}
            units_with_cost = 0
            units_without_cost = 0

            for unit in system.units:
                unit_id = unit.ID
                try:
                    # Try to get purchase cost
                    purchase_cost = unit.purchase_cost if hasattr(unit, 'purchase_cost') else 0.0
                    installed_cost = unit.installed_cost if hasattr(unit, 'installed_cost') else 0.0

                    if purchase_cost > 0 or installed_cost > 0:
                        unit_costs[unit_id] = {
                            "unit_type": type(unit).__name__,
                            "purchase_cost": purchase_cost,
                            "installed_cost": installed_cost,
                            "has_costing": True,
                        }
                        units_with_cost += 1
                    else:
                        unit_costs[unit_id] = {
                            "unit_type": type(unit).__name__,
                            "purchase_cost": 0.0,
                            "installed_cost": 0.0,
                            "has_costing": False,
                            "note": "Unit lacks _cost() method or returns 0",
                        }
                        units_without_cost += 1

                except Exception as e:
                    unit_costs[unit_id] = {
                        "unit_type": type(unit).__name__,
                        "purchase_cost": 0.0,
                        "installed_cost": 0.0,
                        "has_costing": False,
                        "error": str(e),
                    }
                    units_without_cost += 1

            result["units"] = unit_costs
            result["units_with_costing"] = units_with_cost
            result["units_without_costing"] = units_without_cost

            if units_without_cost > 0:
                result["warning"] = (
                    f"{units_without_cost} of {units_with_cost + units_without_cost} units "
                    f"lack costing methods. CAPEX may be underestimated."
                )

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Failed to get CAPEX breakdown: {e}")

    return result


def get_opex_summary(
    tea: "qs.SimpleTEA",
    include_utilities: bool = True,
) -> Dict[str, Any]:
    """
    Get OPEX (Operating Expenses) summary from TEA object.

    QSDsan/BioSTEAM OPEX components:
    - FOC (Fixed Operating Cost): Labor, maintenance, insurance
    - VOC (Variable Operating Cost): Utilities, chemicals, waste disposal
    - AOC (Annual Operating Cost): FOC + VOC

    Args:
        tea: SimpleTEA object from create_tea()
        include_utilities: Include utility breakdown

    Returns:
        Dict with OPEX components

    Example:
        >>> opex = get_opex_summary(tea)
        >>> print(f"Annual operating cost: ${opex['AOC']:,.0f}/year")
    """
    result = {
        "success": False,
        "error": None,
    }

    try:
        system = tea.system

        # Get OPEX components
        try:
            foc = tea.FOC  # Fixed Operating Cost
        except Exception:
            foc = 0.0

        try:
            voc = tea.VOC  # Variable Operating Cost
        except Exception:
            voc = 0.0

        try:
            aoc = tea.AOC  # Annual Operating Cost
        except Exception:
            aoc = foc + voc

        # Annualized costs
        try:
            annualized_capex = tea.annualized_CAPEX
        except Exception:
            annualized_capex = 0.0

        try:
            annualized_equipment = tea.annualized_equipment_cost
        except Exception:
            annualized_equipment = 0.0

        result.update({
            "success": True,
            "FOC": foc,
            "VOC": voc,
            "AOC": aoc,
            "annualized_CAPEX": annualized_capex,
            "annualized_equipment_cost": annualized_equipment,
            "total_annualized_cost": aoc + annualized_capex,
            "currency": "USD",
            "period": "per_year",
        })

        # Utility breakdown
        if include_utilities:
            utilities = get_utility_costs(tea)
            if utilities.get("success"):
                result["utilities"] = utilities

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Failed to get OPEX summary: {e}")

    return result


def get_utility_costs(
    tea: "qs.SimpleTEA",
) -> Dict[str, Any]:
    """
    Get utility consumption and costs from TEA object.

    Args:
        tea: SimpleTEA object from create_tea()

    Returns:
        Dict with utility consumption (power, heating, cooling)

    Example:
        >>> utilities = get_utility_costs(tea)
        >>> print(f"Power: {utilities['electricity_kWh_year']:,.0f} kWh/year")
    """
    result = {
        "success": False,
        "error": None,
    }

    try:
        system = tea.system

        # Get electricity consumption
        try:
            # Method 1: Try system method
            power_kW = system.get_electricity_consumption()
        except Exception:
            # Method 2: Sum from units
            power_kW = 0.0
            for unit in system.units:
                if hasattr(unit, 'power_utility'):
                    try:
                        power_kW += unit.power_utility.consumption
                    except Exception:
                        pass

        # Convert to annual (assuming uptime)
        uptime_ratio = getattr(tea, 'uptime_ratio', 0.95)
        hours_per_year = 8760 * uptime_ratio
        electricity_kWh_year = power_kW * hours_per_year

        # Get electricity price
        electricity_price = getattr(tea, '_electricity_price', 0.07)  # $/kWh
        electricity_cost_year = electricity_kWh_year * electricity_price

        # Get heating/cooling if available
        heating_GJ_year = 0.0
        cooling_GJ_year = 0.0
        try:
            for unit in system.units:
                if hasattr(unit, 'heat_utilities'):
                    for hu in unit.heat_utilities:
                        duty = getattr(hu, 'duty', 0.0)  # kJ/hr
                        if duty > 0:
                            heating_GJ_year += duty * hours_per_year / 1e6
                        else:
                            cooling_GJ_year += abs(duty) * hours_per_year / 1e6
        except Exception:
            pass

        result.update({
            "success": True,
            "electricity": {
                "power_kW": power_kW,
                "consumption_kWh_year": electricity_kWh_year,
                "price_per_kWh": electricity_price,
                "cost_per_year": electricity_cost_year,
            },
            "heating_GJ_year": heating_GJ_year,
            "cooling_GJ_year": cooling_GJ_year,
            "uptime_ratio": uptime_ratio,
        })

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Failed to get utility costs: {e}")

    return result


def estimate_aeration_power(
    system: "qs.System",
    sote: float = 0.35,  # Standard Oxygen Transfer Efficiency
    aerator_efficiency: float = 0.70,  # Overall aerator efficiency
) -> Dict[str, Any]:
    """
    Estimate aeration power for aerobic reactors.

    This is a supplementary calculation since many CSTR units don't have
    built-in power estimation.

    Args:
        system: QSDsan System
        sote: Standard Oxygen Transfer Efficiency (default 0.35 = 35%)
        aerator_efficiency: Overall aerator efficiency (default 0.70)

    Returns:
        Dict with estimated aeration power

    Formula:
        Power = OTR / (SOTE × efficiency × 1.2 kg O2/kWh)

    Where:
        OTR = Oxygen Transfer Rate required (kg O2/hr)
        1.2 kg O2/kWh is typical for fine bubble diffusers
    """
    result = {
        "success": False,
        "error": None,
    }

    try:
        total_power_kW = 0.0
        reactor_powers = {}

        for unit in system.units:
            unit_id = unit.ID
            unit_type = type(unit).__name__

            # Check if unit has aeration
            if hasattr(unit, 'aeration') and unit.aeration is not None and unit.aeration > 0:
                # Estimate OTR from aeration setpoint and reactor volume
                V_m3 = getattr(unit, 'V_max', 0)
                DO_setpoint = unit.aeration  # mg/L

                # Simplified OTR estimation:
                # OTR ≈ kLa × (Cs - C) × V
                # Assume kLa = 10-20 hr^-1 for typical aeration
                # Cs (saturation) ≈ 8 mg/L at 20°C
                # C = DO setpoint

                kLa = 15.0  # hr^-1 (typical)
                Cs = 8.0  # mg/L saturation
                C = min(DO_setpoint, Cs - 0.5)  # Operating DO

                OTR_kg_hr = kLa * (Cs - C) * V_m3 / 1000  # kg O2/hr

                # Power = OTR / (SOTE × eff × 1.2)
                power_kW = OTR_kg_hr / (sote * aerator_efficiency * 1.2)

                reactor_powers[unit_id] = {
                    "unit_type": unit_type,
                    "volume_m3": V_m3,
                    "DO_setpoint_mg_L": DO_setpoint,
                    "OTR_kg_O2_hr": OTR_kg_hr,
                    "estimated_power_kW": power_kW,
                }
                total_power_kW += power_kW

        result.update({
            "success": True,
            "total_aeration_power_kW": total_power_kW,
            "reactors": reactor_powers,
            "assumptions": {
                "SOTE": sote,
                "aerator_efficiency": aerator_efficiency,
                "kLa_hr": 15.0,
                "power_factor_kg_O2_per_kWh": 1.2,
            },
            "note": "Estimated values - actual power depends on equipment selection",
        })

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Failed to estimate aeration power: {e}")

    return result


def get_tea_summary(
    tea: "qs.SimpleTEA",
    flow_m3_d: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Get comprehensive TEA summary suitable for reports.

    Args:
        tea: SimpleTEA object
        flow_m3_d: Design flow rate for per-m3 cost calculation

    Returns:
        Dict with complete TEA summary
    """
    result = {
        "success": False,
        "error": None,
    }

    try:
        capex = get_capex_breakdown(tea, include_units=True)
        opex = get_opex_summary(tea, include_utilities=True)

        if not capex.get("success") or not opex.get("success"):
            result["error"] = "Failed to get CAPEX or OPEX"
            return result

        # Calculate per-m3 costs if flow provided
        per_m3_costs = {}
        if flow_m3_d and flow_m3_d > 0:
            m3_per_year = flow_m3_d * 365 * tea.uptime_ratio
            per_m3_costs = {
                "TCI_per_m3_capacity": capex["TCI"] / (flow_m3_d * 365),
                "AOC_per_m3_treated": opex["AOC"] / m3_per_year,
                "total_annualized_per_m3": opex["total_annualized_cost"] / m3_per_year,
            }

        result.update({
            "success": True,
            "capex": {
                "installed_equipment": capex["installed_equipment_cost"],
                "DPI": capex["DPI"],
                "TDC": capex["TDC"],
                "FCI": capex["FCI"],
                "TCI": capex["TCI"],
            },
            "opex": {
                "FOC": opex["FOC"],
                "VOC": opex["VOC"],
                "AOC": opex["AOC"],
            },
            "annualized": {
                "CAPEX": opex["annualized_CAPEX"],
                "OPEX": opex["AOC"],
                "total": opex["total_annualized_cost"],
            },
            "per_m3": per_m3_costs if per_m3_costs else None,
            "units_summary": {
                "with_costing": capex.get("units_with_costing", 0),
                "without_costing": capex.get("units_without_costing", 0),
            },
            "currency": "USD",
            "warning": capex.get("warning"),
        })

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Failed to get TEA summary: {e}")

    return result


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'create_tea',
    'get_capex_breakdown',
    'get_opex_summary',
    'get_utility_costs',
    'estimate_aeration_power',
    'get_tea_summary',
]
