"""
Anaerobic CSTR Template - Single CSTR with mADM1 model.

This template builds and runs an anaerobic digester simulation using:
- mADM1 (Modified ADM1): 63 components with P/S/Fe extensions
- AnaerobicCSTRmADM1: Custom reactor with 4 biogas species (CH4, CO2, H2, H2S)

Usage:
    from templates.anaerobic.cstr import build_and_run

    result = build_and_run(
        influent_state=plant_state,
        reactor_config={"V_liq": 1000, "V_gas": 100, "T": 308.15},
        duration_days=30,
        output_dir="./output"
    )
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from core.version import __version__ as ENGINE_VERSION

logger = logging.getLogger(__name__)


def build_and_run(
    influent_state: Dict[str, Any],
    reactor_config: Optional[Dict[str, Any]] = None,
    kinetic_params: Optional[Dict[str, Any]] = None,
    duration_days: float = 30.0,
    timestep_hours: Optional[float] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build and run anaerobic CSTR simulation with mADM1.

    Args:
        influent_state: PlantState dict with mADM1 concentrations
            - flow_m3_d: Flow rate in m3/d
            - temperature_K: Temperature in K
            - concentrations: Dict of component concentrations (kg/m3)
        reactor_config: Reactor configuration (V_liq, V_gas, T, etc.)
        kinetic_params: Optional kinetic parameter overrides (not yet implemented)
        duration_days: Stored in metadata but simulation runs to steady state
        timestep_hours: Not supported - raises ValueError if provided
        output_dir: Directory to save results

    Returns:
        Dict with simulation results including:
        - status: "completed" or "failed"
        - effluent: Effluent stream analysis
        - biogas: Biogas production analysis
        - performance: Performance metrics
        - inhibition: Inhibition analysis
        - time_series: Time series data

    Raises:
        ValueError: If timestep_hours is provided (not supported for steady-state)
    """
    # Validate unsupported parameters
    if timestep_hours is not None:
        raise ValueError(
            "timestep_hours not supported for steady-state anaerobic simulation. "
            "The mADM1 CSTR template runs to convergence, not time-domain simulation."
        )

    if kinetic_params:
        logger.warning(
            "kinetic_params not yet implemented for mADM1 - using default parameters. "
            f"Ignored parameters: {list(kinetic_params.keys())}"
        )

    # Import simulation module (triggers QSDsan load)
    from utils.simulate_madm1 import run_simulation_sulfur
    from utils.stream_analysis import (
        analyze_liquid_stream,
        analyze_gas_stream,
        analyze_inhibition,
        analyze_biomass_yields,
        calculate_sulfur_metrics,
    )

    # Default reactor config
    if reactor_config is None:
        reactor_config = {}

    # Extract influent parameters
    flow_m3_d = influent_state.get("flow_m3_d", 1000)
    temperature_K = influent_state.get("temperature_K", 308.15)
    concentrations = influent_state.get("concentrations", {})

    # Calculate HRT from reactor config or use default
    V_liq = reactor_config.get("V_liq", flow_m3_d * 20)  # 20-day HRT default
    HRT_days = V_liq / flow_m3_d

    try:
        logger.info(f"Starting anaerobic CSTR simulation with mADM1")
        logger.info(f"Q={flow_m3_d} m3/d, T={temperature_K} K, HRT={HRT_days:.1f} days")

        # Prepare basis dict for run_simulation_sulfur
        basis = {
            "Q": flow_m3_d,
            "Temp": temperature_K,
        }

        # Run simulation to steady state
        # Returns: (sys, inf, eff, gas, converged_at, status, time_series)
        sys, inf, eff, gas, converged_at, status, time_series = run_simulation_sulfur(
            basis=basis,
            adm1_state_62=concentrations,
            HRT=HRT_days,
            check_interval=2,  # Check convergence every 2 days
            tolerance=1e-3,
        )

        logger.info(f"Simulation completed: {status} at t={converged_at:.1f} days")
        logger.info("Analyzing results...")

        # Analyze streams using actual API
        effluent_analysis = analyze_liquid_stream(eff, include_components=False)
        biogas_analysis = analyze_gas_stream(gas, inf_stream=inf, eff_stream=eff)

        # Calculate performance metrics
        performance_metrics = _calculate_performance_metrics(inf, eff, gas, V_liq)

        # Analyze inhibition
        # Note: analyze_inhibition expects sim_results tuple, not reactor
        sim_results = (sys, inf, eff, gas, converged_at, status, time_series)
        inhibition_analysis = analyze_inhibition(sim_results)

        # Calculate biomass yields
        biomass_yields = analyze_biomass_yields(inf, eff, system=sys)

        # Calculate sulfur metrics
        sulfur_metrics = calculate_sulfur_metrics(inf, eff, gas)

        # Build result
        result = {
            "status": "completed",
            "template": "anaerobic_cstr_madm1",
            "influent": {
                "flow_m3_d": flow_m3_d,
                "temperature_K": temperature_K,
                "n_components": len(concentrations),
                "COD_mg_L": float(inf.COD) if hasattr(inf, 'COD') else None,
            },
            "reactor": {
                "V_liq_m3": V_liq,
                "V_gas_m3": V_liq * 0.1,
                "HRT_days": HRT_days,
            },
            "effluent": effluent_analysis,
            "biogas": biogas_analysis,
            "performance": performance_metrics,
            "inhibition": inhibition_analysis,
            "biomass_yields": biomass_yields,
            "sulfur": sulfur_metrics,
            "simulation": {
                "converged_at_days": converged_at,
                "status": status,
                "converged": status == "converged",
            },
        }

        # Add time series if available
        if time_series:
            result["time_series_available"] = True
            result["time_series"] = time_series

        # Add deterministic metadata (Phase 3C)
        import datetime
        try:
            import qsdsan as qs
            qsdsan_version = getattr(qs, "__version__", "unknown")
        except Exception:
            qsdsan_version = "unknown"

        try:
            import biosteam as bst
            biosteam_version = getattr(bst, "__version__", "unknown")
        except Exception:
            biosteam_version = "unknown"

        result["metadata"] = {
            "qsdsan_version": qsdsan_version,
            "biosteam_version": biosteam_version,
            "engine_version": ENGINE_VERSION,
            "template": "anaerobic_cstr_madm1",
            "solver": {
                "method": "BDF",
                "duration_days": duration_days,
                "rtol": 1e-3,
                "atol": 1e-6,
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "model_type": "mADM1",
        }

        # Generate diagram and mass balance data
        try:
            from utils.diagram import (
                save_system_diagram,
                generate_mass_balance_table,
                generate_unit_summary,
            )

            # Generate mass balance data (always, for report)
            streams_data = generate_mass_balance_table(sys, model_type="mADM1")
            units_data = generate_unit_summary(sys)

            result["flowsheet"] = {
                "streams": streams_data,
                "units": units_data,
            }
        except Exception as e:
            logger.warning(f"Could not generate flowsheet data: {e}")
            result["flowsheet"] = None

        # Save results if output_dir provided
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Generate and save diagram
            try:
                from utils.diagram import save_system_diagram
                diagram_path = save_system_diagram(
                    sys,
                    output_path=output_dir / "flowsheet",
                    kind="thorough",
                    format="svg",
                    title=f"Anaerobic CSTR - {flow_m3_d:.0f} m³/d",
                )
                if diagram_path:
                    result["flowsheet"]["diagram_path"] = str(diagram_path)
                    logger.info(f"Diagram saved to: {diagram_path}")
            except Exception as e:
                logger.warning(f"Could not generate diagram: {e}")

            with open(output_dir / "simulation_results.json", "w") as f:
                json.dump(result, f, indent=2, default=str)

            # Save compact summaries
            with open(output_dir / "simulation_performance.json", "w") as f:
                json.dump(result["performance"], f, indent=2, default=str)

            with open(output_dir / "simulation_inhibition.json", "w") as f:
                json.dump(result["inhibition"], f, indent=2, default=str)

            logger.info(f"Results saved to {output_dir}")

        return result

    except Exception as e:
        logger.error(f"Simulation failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "template": "anaerobic_cstr_madm1",
            "error": str(e),
        }


def _calculate_performance_metrics(inf, eff, gas, V_liq: float) -> Dict[str, Any]:
    """
    Calculate key performance metrics from simulation results.

    Args:
        inf: Influent WasteStream
        eff: Effluent WasteStream
        gas: Biogas WasteStream
        V_liq: Reactor liquid volume in m3

    Returns:
        Dict with performance metrics
    """
    try:
        # COD removal
        COD_in = float(inf.COD) if hasattr(inf, 'COD') else 0
        COD_out = float(eff.COD) if hasattr(eff, 'COD') else 0
        COD_removed = COD_in - COD_out
        COD_removal_pct = (COD_removed / COD_in * 100) if COD_in > 0 else 0

        # Biogas production
        biogas_m3_d = float(gas.F_vol * 24) if hasattr(gas, 'F_vol') else 0

        # Get methane flow using molar basis (correct for ADM1 components)
        # S_ch4 is measured_as='COD', so imass gives COD-equivalent mass, not actual CH4
        # Use imol (molar flow) to avoid COD-mass confusion
        STP_MOLAR_VOLUME = 22.414  # L/mol at STP
        CH4_DENSITY_STP = 0.717    # kg/m³ at STP
        try:
            # Primary: molar basis (correct for ADM1 with adjust_MW_to_measured_as=True)
            ch4_mol = gas.imol['S_ch4']  # kmol/hr
            ch4_flow = ch4_mol * STP_MOLAR_VOLUME * 24  # Nm³/d
        except Exception as e1:
            try:
                # Fallback: mass basis with i_mass correction
                i_mass_ch4 = gas.components.S_ch4.i_mass  # ~0.25067 g CH4/g COD
                ch4_flow = gas.imass['S_ch4'] * i_mass_ch4 * 24 / CH4_DENSITY_STP
            except Exception as e2:
                # FAIL LOUDLY - do not silently produce wrong results
                raise RuntimeError(
                    f"Cannot calculate CH4 flow: molar method failed ({e1}), "
                    f"mass method failed ({e2}). Check gas stream configuration."
                )

        # Specific methane yield
        COD_removed_kg_d = COD_removed * inf.F_vol * 24 / 1000 if hasattr(inf, 'F_vol') else 0  # mg/L * m3/h * 24 / 1000 = kg/d
        specific_ch4_yield = ch4_flow / COD_removed_kg_d if COD_removed_kg_d > 0 else 0

        # Volumetric loading
        OLR = (COD_in * inf.F_vol * 24 / 1000) / V_liq if V_liq > 0 and hasattr(inf, 'F_vol') else 0  # kg COD/m3/d

        return {
            "COD_in_mg_L": round(COD_in, 1),
            "COD_out_mg_L": round(COD_out, 1),
            "COD_removal_pct": round(COD_removal_pct, 1),
            "biogas_m3_d": round(biogas_m3_d, 2),
            "methane_m3_d": round(ch4_flow, 2),
            "specific_CH4_yield_m3_kg_COD": round(specific_ch4_yield, 3),
            "OLR_kg_COD_m3_d": round(OLR, 2),
        }
    except Exception as e:
        logger.warning(f"Error calculating performance metrics: {e}")
        return {"error": str(e)}


def get_default_reactor_config(flow_m3_d: float, srt_days: float = 20) -> Dict[str, Any]:
    """
    Get default reactor configuration for given flow and SRT.

    Args:
        flow_m3_d: Design flow rate in m3/d
        srt_days: Target solids retention time in days

    Returns:
        Dict with V_liq, V_gas, and other reactor parameters
    """
    V_liq = flow_m3_d * srt_days
    V_gas = V_liq * 0.1  # 10% headspace

    return {
        "V_liq": V_liq,
        "V_gas": V_gas,
        "T": 308.15,  # 35°C mesophilic
        "headspace_P": 101325,  # 1 atm
    }
