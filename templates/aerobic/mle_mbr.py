"""
MLE-MBR (Modified Ludzack-Ettinger with Membrane Bioreactor) Template.

Flowsheet: Influent -> A1 -> A2 -> O1 -> O2 -> [SP_IR] -> MBR -> Effluent + WAS
                      ^______IR______v         v
                      ^___________RAS___________v

Reference: Pune_Nanded_WWTP_updated.py (Gates Foundation MBR project)

Usage:
    from templates.aerobic.mle_mbr import build_and_run

    result = build_and_run(
        influent_state={"flow_m3_d": 4000, "temperature_K": 293.15, "concentrations": {...}},
        reactor_config={"V_anoxic_m3": 156, "V_aerobic_m3": 252, "V_mbr_m3": 382},
        duration_days=15,
    )
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import numpy as np
import qsdsan as qs
from qsdsan import processes as pc, sanunits as su

from utils.analysis.aerobic import (
    analyze_aerobic_stream,
    analyze_aerobic_performance,
    DEFAULT_ASM2D_KWARGS,
    DEFAULT_DOMESTIC_WW,
)

logger = logging.getLogger(__name__)

__all__ = ['build_and_run', 'get_default_reactor_config', 'get_default_influent']


def create_asm2d_components(set_thermo: bool = True):
    """
    Create ASM2d components with calibrated BOD5/COD fractions.

    Uses QSDsan's upstream pc.create_asm2d_cmps() directly.
    """
    cmps = pc.create_asm2d_cmps(set_thermo=False)
    cmps.X_S.f_BOD5_COD = 0.25
    cmps.S_F.f_BOD5_COD = 0.6
    cmps.S_A.f_BOD5_COD = 0.4
    cmps.compile()
    if set_thermo:
        qs.set_thermo(cmps)
    return cmps


def get_default_reactor_config(flow_m3_d: float = 4000) -> Dict[str, Any]:
    """
    Get default reactor configuration for MLE-MBR.

    Based on Pune Nanded WWTP design manual.
    """
    return {
        "V_anoxic_m3": 156,       # Per stage (2 anoxic stages)
        "n_anoxic_stages": 2,
        "V_aerobic_m3": 252,      # Per stage (2 aerobic stages)
        "n_aerobic_stages": 2,
        "V_mbr_m3": 382,          # MBR tank volume
        "DO_aerobic_mg_L": 2.3,   # DO setpoint in aerobic zones
        "DO_mbr_mg_L": 2.2,       # DO setpoint in MBR
        "SCR": 0.999,             # Solids capture rate
        "Q_ras_multiplier": 4.0,  # RAS = Q * multiplier
        "Q_ir_multiplier": 2.0,   # Internal recycle (IR) = Q * multiplier (for denitrification)
        "Q_was_m3_d": 768,        # WAS flow rate
    }


def get_default_influent() -> Dict[str, float]:
    """Get default domestic wastewater composition (mg/L)."""
    return DEFAULT_DOMESTIC_WW.copy()


def build_and_run(
    influent_state: Dict[str, Any],
    reactor_config: Optional[Dict[str, Any]] = None,
    kinetic_params: Optional[Dict[str, Any]] = None,
    duration_days: float = 15.0,
    timestep_hours: Optional[float] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build and run MLE-MBR simulation with ASM2d.

    Parameters
    ----------
    influent_state : dict
        Influent state with:
        - flow_m3_d: Flow rate in m³/d
        - temperature_K: Temperature in K (default 293.15)
        - concentrations: Dict of component concentrations in mg/L
    reactor_config : dict, optional
        Reactor configuration overrides
    kinetic_params : dict, optional
        ASM2d kinetic parameter overrides (e.g., {"mu_H": 6.0, "K_F": 10.0})
    duration_days : float
        Simulation duration in days
    timestep_hours : float, optional
        Output timestep in hours. If provided, generates t_eval array for simulation.
    output_dir : Path, optional
        Directory to save results

    Returns
    -------
    dict
        Simulation results including effluent, performance, and time series
    """
    try:
        # Extract influent parameters
        Q = influent_state.get('flow_m3_d', 4000)
        T = influent_state.get('temperature_K', 293.15)
        concentrations = influent_state.get('concentrations', DEFAULT_DOMESTIC_WW)

        # Merge with defaults
        config = get_default_reactor_config(Q)
        if reactor_config:
            config.update(reactor_config)

        asm_kwargs = DEFAULT_ASM2D_KWARGS.copy()
        if kinetic_params:
            asm_kwargs.update(kinetic_params)

        logger.info(f"Building MLE-MBR system: Q={Q} m³/d, T={T-273.15}°C")

        # Create components and set thermo
        cmps = create_asm2d_components(set_thermo=True)

        # Create influent stream
        influent = qs.WasteStream('influent', T=T)
        influent.set_flow_by_concentration(Q, concentrations=concentrations, units=('m3/d', 'mg/L'))

        # Create recycle streams
        RAS = qs.WasteStream('RAS', T=T)
        IR = qs.WasteStream('IR', T=T)   # Internal recycle for denitrification
        WAS = qs.WasteStream('WAS', T=T)

        # Create ASM2d process model
        asm2d = pc.ASM2d(**asm_kwargs)

        # Apply kinetic parameter overrides after creation
        applied_params = {}
        if kinetic_params:
            asm2d.set_parameters(**kinetic_params)
            # Extract applied params from both stoichiometry and kinetics
            stoichio_params = getattr(asm2d, '_parameters', {})
            kinetic_rate_params = getattr(asm2d.rate_function, 'params', {}) if hasattr(asm2d, 'rate_function') else {}
            for k in kinetic_params:
                if k in stoichio_params:
                    applied_params[k] = stoichio_params[k]
                elif k in kinetic_rate_params:
                    applied_params[k] = kinetic_rate_params[k]
                else:
                    applied_params[k] = None  # Parameter not found/applied
            logger.info(f"Applied kinetic params: {applied_params}")

        # Extract configuration
        V_an = config['V_anoxic_m3']
        V_ae = config['V_aerobic_m3']
        V_mbr = config['V_mbr_m3']
        DO_ae = config['DO_aerobic_mg_L']
        DO_mbr = config['DO_mbr_mg_L']
        SCR = config['SCR']
        Q_ras = Q * config['Q_ras_multiplier']
        Q_ir = Q * config['Q_ir_multiplier']
        Q_was = config['Q_was_m3_d']

        # Calculate split ratio for RAS/WAS
        split_ras = Q_ras / (Q_ras + Q_was)
        # Calculate split ratio for IR (flow to MBR vs IR)
        Q_to_mbr = Q + Q_ras - Q_ir  # Approximate flow to MBR
        split_ir = Q_ir / (Q_ir + Q_to_mbr) if (Q_ir + Q_to_mbr) > 0 else 0

        # Build flowsheet: A1 -> A2 -> O1 -> O2 -> SP_IR -> MBR
        #                  ^____IR____v          v
        #                  ^_______RAS____________v

        # Anoxic zones (no aeration) - receive influent, RAS, and IR
        A1 = su.CSTR(
            'A1',
            ins=[influent, RAS, IR],
            V_max=V_an,
            aeration=None,
            DO_ID=None,
            suspended_growth_model=asm2d,
        )

        A2 = su.CSTR(
            'A2',
            ins=[A1-0],
            V_max=V_an,
            aeration=None,
            DO_ID=None,
            suspended_growth_model=asm2d,
        )

        # Aerobic zones (with aeration)
        O1 = su.CSTR(
            'O1',
            ins=[A2-0],
            V_max=V_ae,
            aeration=DO_ae,
            DO_ID='S_O2',
            suspended_growth_model=asm2d,
        )

        O2 = su.CSTR(
            'O2',
            ins=[O1-0],
            V_max=V_ae,
            aeration=DO_ae,
            DO_ID='S_O2',
            suspended_growth_model=asm2d,
        )

        # Internal recycle splitter (splits flow from O2 to IR and MBR)
        SP_IR = su.Splitter('SP_IR', ins=O2-0, outs=[IR, 'to_mbr'], split=split_ir)

        # MBR (membrane separation with aeration)
        MBR = su.CompletelyMixedMBR(
            'MBR',
            ins=SP_IR-1,
            outs=('effluent', 'retain'),
            V_max=V_mbr,
            solids_capture_rate=SCR,
            pumped_flow=Q_was + Q_ras,
            aeration=DO_mbr,
            DO_ID='S_O2',
            gas_stripping=False,
            suspended_growth_model=asm2d,
        )

        # RAS/WAS splitter
        SP = su.Splitter('SP', ins=MBR-1, outs=[RAS, WAS], split=split_ras)

        # Create system with both recycles
        sys = qs.System(
            'MLE_MBR',
            path=(A1, A2, O1, O2, SP_IR, MBR, SP),
            recycle=[RAS, IR],
        )

        # Set dynamic tracker
        sys.set_dynamic_tracker(*sys.products)

        logger.info(f"Simulating for {duration_days} days...")

        # Build simulation kwargs
        sim_kwargs = {
            'state_reset_hook': 'reset_cache',
            't_span': (0, duration_days),
            'method': 'RK23',
        }

        # Add t_eval if timestep_hours is specified
        if timestep_hours is not None and timestep_hours > 0:
            dt = timestep_hours / 24  # Convert hours to days
            # Use epsilon to avoid floating-point overshoot
            t_eval = np.arange(0, duration_days + 1e-9, dt)
            # Clamp to duration_days
            t_eval = t_eval[t_eval <= duration_days + 1e-9]
            # Ensure final point is included
            if len(t_eval) == 0 or t_eval[-1] < duration_days - 1e-9:
                t_eval = np.append(t_eval, duration_days)
            sim_kwargs['t_eval'] = t_eval
            logger.info(f"Using timestep {timestep_hours}h -> {len(t_eval)} evaluation points")

        # Run simulation
        sys.simulate(**sim_kwargs)

        logger.info("Simulation completed, analyzing results...")

        # Get streams
        eff_stream = sys.flowsheet.stream.effluent
        was_stream = sys.flowsheet.stream.WAS

        # Analyze performance
        inf_analysis = analyze_aerobic_stream(influent)
        eff_analysis = analyze_aerobic_stream(eff_stream)
        performance = analyze_aerobic_performance(
            influent, eff_stream,
            system=sys,
            was_stream=was_stream,
        )

        # Calculate HRT
        total_V = 2 * V_an + 2 * V_ae + V_mbr
        hrt_hours = total_V / Q * 24

        # Build result
        result = {
            "status": "completed",
            "template": "mle_mbr_asm2d",
            "influent": {
                "flow_m3_d": Q,
                "temperature_K": T,
                "COD_mg_L": inf_analysis.get('COD_mg_L', 0),
                "TKN_mg_L": inf_analysis.get('nitrogen', {}).get('NH4_mg_N_L', 0),
            },
            "reactor": {
                "type": "MLE-MBR",
                "V_anoxic_m3": V_an * 2,
                "V_aerobic_m3": V_ae * 2,
                "V_mbr_m3": V_mbr,
                "V_total_m3": total_V,
                "HRT_hours": hrt_hours,
                "DO_aerobic_mg_L": DO_ae,
                "DO_mbr_mg_L": DO_mbr,
                "Q_ras_m3_d": Q_ras,
                "Q_ir_m3_d": Q_ir,
                "Q_was_m3_d": Q_was,
            },
            "effluent": {
                "COD_mg_L": eff_analysis.get('COD_mg_L', 0),
                "TSS_mg_L": eff_analysis.get('TSS_mg_L', 0),
                "NH4_mg_N_L": eff_analysis.get('nitrogen', {}).get('NH4_mg_N_L', 0),
                "NO3_mg_N_L": eff_analysis.get('nitrogen', {}).get('NO3_mg_N_L', 0),
                "PO4_mg_P_L": eff_analysis.get('phosphorus', {}).get('PO4_mg_P_L', 0),
            },
            "performance": performance if performance.get('success') else {"error": performance.get('error')},
            "simulation": {
                "duration_days": duration_days,
                "method": "RK23",
                "status": "completed",
            },
        }

        # Add deterministic metadata (Phase 3C)
        import datetime
        try:
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
            "engine_version": "3.0.0",
            "template": "mle_mbr_asm2d",
            "solver": {
                "method": "RK23",
                "duration_days": duration_days,
                "timestep_hours": timestep_hours,
                "rtol": 1e-3,
                "atol": 1e-6,
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "model_type": "ASM2d",
            "applied_kinetic_params": applied_params if applied_params else None,
        }

        # Generate diagram and mass balance data
        try:
            from utils.diagram import (
                save_system_diagram,
                generate_mass_balance_table,
                generate_unit_summary,
            )

            # Generate mass balance data (always, for report)
            streams_data = generate_mass_balance_table(sys, model_type="ASM2d")
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
            if result.get("flowsheet") is not None:
                try:
                    diagram_path = save_system_diagram(
                        sys,
                        output_path=output_dir / "flowsheet",
                        kind="thorough",
                        format="svg",
                        title=f"MLE-MBR - {Q:.0f} m3/d",
                    )
                    if diagram_path:
                        result["flowsheet"]["diagram_path"] = str(diagram_path)
                        logger.info(f"Diagram saved to: {diagram_path}")
                except Exception as e:
                    logger.warning(f"Could not generate diagram: {e}")

            with open(output_dir / "simulation_results.json", "w") as f:
                json.dump(result, f, indent=2, default=str)

            logger.info(f"Results saved to {output_dir}")

        return result

    except Exception as e:
        logger.error(f"MLE-MBR simulation failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "template": "mle_mbr_asm2d",
            "error": str(e),
        }
