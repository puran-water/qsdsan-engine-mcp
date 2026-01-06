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

logger = logging.getLogger(__name__)


def build_and_run(
    influent_state: Dict[str, Any],
    reactor_config: Optional[Dict[str, Any]] = None,
    kinetic_params: Optional[Dict[str, Any]] = None,
    duration_days: float = 30.0,
    timestep_hours: float = 1.0,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build and run anaerobic CSTR simulation with mADM1.

    Args:
        influent_state: PlantState dict with mADM1 concentrations
        reactor_config: Reactor configuration (V_liq, V_gas, T, etc.)
        kinetic_params: Optional kinetic parameter overrides
        duration_days: Simulation duration in days
        timestep_hours: Output timestep in hours
        output_dir: Directory to save results

    Returns:
        Dict with simulation results including:
        - status: "completed" or "failed"
        - effluent: Effluent stream analysis
        - biogas: Biogas production analysis
        - performance: Performance metrics
        - inhibition: Inhibition analysis
        - time_series: Time series data (if requested)
    """
    # Import simulation module (triggers QSDsan load)
    from utils.simulate_madm1 import (
        create_influent_stream_sulfur,
        create_anaerobic_digester_system_sulfur,
        run_simulation_sulfur,
    )
    from utils.stream_analysis import (
        analyze_liquid_stream,
        analyze_biogas_stream,
        calculate_biogas_metrics,
        calculate_inhibition_metrics,
    )

    # Default reactor config
    if reactor_config is None:
        reactor_config = {}

    # Extract influent parameters
    flow_m3_d = influent_state.get("flow_m3_d", 1000)
    temperature_K = influent_state.get("temperature_K", 308.15)
    concentrations = influent_state.get("concentrations", {})

    # Default reactor volume based on HRT
    V_liq = reactor_config.get("V_liq", flow_m3_d * 20)  # 20-day HRT default
    V_gas = reactor_config.get("V_gas", V_liq * 0.1)  # 10% headspace

    try:
        logger.info(f"Creating influent stream: Q={flow_m3_d} m3/d, T={temperature_K} K")

        # Create influent stream
        influent = create_influent_stream_sulfur(
            Q=flow_m3_d,
            Temp=temperature_K,
            adm1_state_62=concentrations,
        )

        logger.info(f"Building system: V_liq={V_liq} m3, V_gas={V_gas} m3")

        # Create system
        system, ad = create_anaerobic_digester_system_sulfur(
            influent_stream=influent,
            V_liq=V_liq,
            V_gas=V_gas,
            T=temperature_K,
        )

        logger.info(f"Running simulation for {duration_days} days")

        # Run simulation
        t_end = duration_days * 24  # Convert to hours
        t_eval_step = timestep_hours

        # Run with convergence checking
        system, effluent, biogas_stream, time_series = run_simulation_sulfur(
            system=system,
            t_end=t_end,
            t_eval_step=t_eval_step,
            check_convergence=True,
            convergence_window_hours=24 * 5,  # 5-day window
            convergence_threshold=0.01,
        )

        logger.info("Analyzing results")

        # Analyze streams
        effluent_analysis = analyze_liquid_stream(effluent, include_sulfur=True)
        biogas_analysis = analyze_biogas_stream(biogas_stream)

        # Calculate metrics
        biogas_metrics = calculate_biogas_metrics(
            influent=influent,
            effluent=effluent,
            biogas=biogas_stream,
            V_liq=V_liq,
        )
        inhibition_metrics = calculate_inhibition_metrics(ad)

        # Build result
        result = {
            "status": "completed",
            "template": "anaerobic_cstr_madm1",
            "influent": {
                "flow_m3_d": flow_m3_d,
                "temperature_K": temperature_K,
                "n_components": len(concentrations),
            },
            "reactor": {
                "V_liq_m3": V_liq,
                "V_gas_m3": V_gas,
                "HRT_days": V_liq / flow_m3_d,
            },
            "effluent": effluent_analysis,
            "biogas": biogas_analysis,
            "performance": biogas_metrics,
            "inhibition": inhibition_metrics,
            "simulation": {
                "duration_days": duration_days,
                "timestep_hours": timestep_hours,
                "converged": time_series.get("converged", False),
            },
        }

        # Add time series if available
        if time_series:
            result["time_series_available"] = True
            result["time_series"] = time_series

        # Save results if output_dir provided
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

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
