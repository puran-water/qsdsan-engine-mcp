"""
A/O-MBR (Anoxic-Oxic with Membrane Bioreactor) Template.

Simplified configuration for basic nitrogen removal.

Flowsheet: Influent -> A1 -> O1 -> MBR -> Effluent + WAS
                      ^_____RAS_____v

Usage:
    from templates.aerobic.ao_mbr import build_and_run

    result = build_and_run(
        influent_state={"flow_m3_d": 4000, "temperature_K": 293.15, "concentrations": {...}},
        reactor_config={"V_anoxic_m3": 300, "V_aerobic_m3": 500, "V_mbr_m3": 400},
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
from utils.aerobic_inoculum_generator import (
    generate_aerobic_inoculum,
    estimate_equilibration_time,
)

logger = logging.getLogger(__name__)

__all__ = ['build_and_run', 'get_default_reactor_config', 'get_default_influent']


def create_asm2d_components(set_thermo: bool = True):
    """Create ASM2d components with calibrated BOD5/COD fractions."""
    cmps = pc.create_asm2d_cmps(set_thermo=False)
    cmps.X_S.f_BOD5_COD = 0.25
    cmps.S_F.f_BOD5_COD = 0.6
    cmps.S_A.f_BOD5_COD = 0.4
    cmps.compile()
    if set_thermo:
        qs.set_thermo(cmps)
    return cmps


def get_default_reactor_config(flow_m3_d: float = 4000) -> Dict[str, Any]:
    """Get default reactor configuration for A/O-MBR."""
    return {
        "V_anoxic_m3": 300,       # Single anoxic zone
        "V_aerobic_m3": 500,      # Single aerobic zone
        "V_mbr_m3": 400,          # MBR tank volume
        "DO_aerobic_mg_L": 2.5,   # DO setpoint
        "DO_mbr_mg_L": 2.2,
        "SCR": 0.999,
        "Q_ras_multiplier": 3.0,
        "Q_was_m3_d": 500,
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
    run_to_convergence: bool = False,
    convergence_atol: float = 0.1,
    convergence_rtol: float = 1e-3,
    check_interval_days: float = 2.0,
    max_duration_days: Optional[float] = None,
    # SRT Control Parameters (Phase 12)
    target_srt_days: Optional[float] = None,
    srt_tolerance: float = 0.1,
    max_srt_iterations: int = 10,
) -> Dict[str, Any]:
    """
    Build and run A/O-MBR simulation with ASM2d.

    Simplified 2-stage process for basic nitrification/denitrification.

    Parameters
    ----------
    kinetic_params : dict, optional
        ASM2d kinetic parameter overrides (e.g., {"mu_H": 6.0, "K_F": 10.0})
    timestep_hours : float, optional
        Output timestep in hours. If provided, generates t_eval array for simulation.
    run_to_convergence : bool, optional
        If True, run simulation until steady state is reached (default False).
    convergence_atol : float, optional
        Absolute tolerance for convergence (default 0.1 mg/L/d).
    convergence_rtol : float, optional
        Relative tolerance for convergence (default 1e-3).
    check_interval_days : float, optional
        Days between convergence checks (default 2.0).
    max_duration_days : float, optional
        Maximum simulation time when run_to_convergence=True.
    target_srt_days : float, optional
        Target SRT in days. If set, Q_was is iteratively adjusted to achieve
        the target SRT at steady state. Implies run_to_convergence=True. (Phase 12)
    srt_tolerance : float, optional
        Relative tolerance on achieved SRT (default 0.1 = 10%).
    max_srt_iterations : int, optional
        Maximum Q_was adjustment iterations for SRT control (default 10).
    """
    try:
        Q = influent_state.get('flow_m3_d', 4000)
        T = influent_state.get('temperature_K', 293.15)
        concentrations = influent_state.get('concentrations', DEFAULT_DOMESTIC_WW)

        config = get_default_reactor_config(Q)
        if reactor_config:
            config.update(reactor_config)

        asm_kwargs = DEFAULT_ASM2D_KWARGS.copy()
        if kinetic_params:
            asm_kwargs.update(kinetic_params)

        logger.info(f"Building A/O-MBR system: Q={Q} m³/d, T={T-273.15}°C")

        cmps = create_asm2d_components(set_thermo=True)

        influent = qs.WasteStream('influent', T=T)
        influent.set_flow_by_concentration(Q, concentrations=concentrations, units=('m3/d', 'mg/L'))

        RAS = qs.WasteStream('RAS', T=T)
        WAS = qs.WasteStream('WAS', T=T)

        asm2d = pc.ASM2d(**asm_kwargs)

        # Apply kinetic parameter overrides after creation
        applied_params = {}
        if kinetic_params:
            asm2d.set_parameters(**kinetic_params)
            stoichio_params = getattr(asm2d, '_parameters', {})
            kinetic_rate_params = getattr(asm2d.rate_function, 'params', {}) if hasattr(asm2d, 'rate_function') else {}
            for k in kinetic_params:
                if k in stoichio_params:
                    applied_params[k] = stoichio_params[k]
                elif k in kinetic_rate_params:
                    applied_params[k] = kinetic_rate_params[k]
                else:
                    applied_params[k] = None
            logger.info(f"Applied kinetic params: {applied_params}")

        V_an = config['V_anoxic_m3']
        V_ae = config['V_aerobic_m3']
        V_mbr = config['V_mbr_m3']
        DO_ae = config['DO_aerobic_mg_L']
        DO_mbr = config['DO_mbr_mg_L']
        SCR = config['SCR']
        Q_ras = Q * config['Q_ras_multiplier']
        Q_was = config['Q_was_m3_d']
        split_ras = Q_ras / (Q_ras + Q_was)

        # Anoxic zone
        A1 = su.CSTR(
            'A1',
            ins=[influent, RAS],
            V_max=V_an,
            aeration=None,
            DO_ID=None,
            suspended_growth_model=asm2d,
        )

        # Aerobic zone
        O1 = su.CSTR(
            'O1',
            ins=[A1-0],
            V_max=V_ae,
            aeration=DO_ae,
            DO_ID='S_O2',
            suspended_growth_model=asm2d,
        )

        # MBR
        MBR = su.CompletelyMixedMBR(
            'MBR',
            ins=O1-0,
            outs=('effluent', 'retain'),
            V_max=V_mbr,
            solids_capture_rate=SCR,
            pumped_flow=Q_was + Q_ras,
            aeration=DO_mbr,
            DO_ID='S_O2',
            gas_stripping=False,
            suspended_growth_model=asm2d,
        )

        SP = su.Splitter('SP', ins=MBR-1, outs=[RAS, WAS], split=split_ras)

        # =====================================================================
        # PHASE 12B: Initialize reactors with acclimated sludge inoculum
        # =====================================================================
        # This solves the nitrification failure problem where reactors
        # initialized with influent composition (~5 mg/L X_AUT) fail to
        # achieve >80% NH4 removal due to insufficient nitrifier biomass.
        #
        # CRITICAL: CSTR does NOT accept initial_state parameter.
        # Must use set_init_conc(**kwargs) method after unit creation.
        # =====================================================================

        # Generate inoculum with established nitrifier population
        # Target: 3500 mg VSS/L MLSS, ~5% as nitrifiers (~249 mg COD/L X_AUT)
        reactor_inoculum = generate_aerobic_inoculum(
            target_mlvss_mg_L=config.get('target_mlvss_mg_L', 3500),
            x_aut_fraction=0.05,  # 5% nitrifiers (IWA typical for nitrifying AS)
            x_pao_fraction=0.02,  # 2% PAOs (minimal for A/O without EBPR)
            x_h_fraction=0.85,    # 85% heterotrophs
        )

        # Apply inoculum to all reactors
        all_reactors = [A1, O1, MBR]
        for reactor in all_reactors:
            try:
                reactor.set_init_conc(**reactor_inoculum)
                logger.debug(f"Initialized {reactor.ID} with inoculum")
            except Exception as e:
                logger.warning(f"Could not initialize {reactor.ID}: {e}")

        logger.info(
            f"Reactor inoculation complete: X_AUT={reactor_inoculum.get('X_AUT', 0):.0f} mg COD/L"
        )

        sys = qs.System(
            'AO_MBR',
            path=(A1, O1, MBR, SP),
            recycle=[RAS],
        )

        # Track both effluent and WAS for convergence detection
        eff_stream = sys.flowsheet.stream.effluent
        was_stream = sys.flowsheet.stream.WAS
        sys.set_dynamic_tracker(eff_stream, was_stream)

        # Initialize convergence tracking variables
        srt_days = None
        converged_at = None
        conv_status = None
        conv_metrics = None

        # =====================================================================
        # PHASE 12B: Simulation duration warning / equilibration time
        # =====================================================================
        # Nitrifiers grow slowly (μ_AUT ~1.0 d⁻¹ at 20°C).
        # Short simulations may not reach steady-state nitrification.
        # =====================================================================
        equil_estimate = estimate_equilibration_time(
            target_mlvss_mg_L=config.get('target_mlvss_mg_L', 3500),
            x_aut_fraction=0.05,
            srt_days=15.0,  # Typical A/O SRT
        )

        # Determine simulation mode
        # Phase 12: SRT control takes precedence if target_srt_days is set
        if target_srt_days is not None:
            # SRT-controlled simulation
            from utils.run_to_srt import run_to_target_srt

            logger.info(
                f"Running SRT-controlled simulation: target_srt={target_srt_days}d, "
                f"tolerance={srt_tolerance:.0%}"
            )

            # Set default max_duration from target SRT (4x for margin)
            if max_duration_days is None:
                max_duration_days = max(100.0, target_srt_days * 4)

            # Configure convergence components
            convergence_components = {
                eff_stream.ID: ['S_NH4', 'S_NO3', 'S_O2'],
                was_stream.ID: ['X_AUT', 'X_H', 'X_PAO'],
            }

            achieved_srt, srt_status, srt_metrics = run_to_target_srt(
                system=sys,
                target_srt_days=target_srt_days,
                wastage_streams=[was_stream],
                effluent_streams=None,  # MBR permeate has no solids
                convergence_streams=[eff_stream, was_stream],
                convergence_components=convergence_components,
                model_type='ASM2d',
                srt_tolerance=srt_tolerance,
                max_srt_iterations=max_srt_iterations,
                min_time_multiplier=2.0,
                check_interval=check_interval_days,
                atol=convergence_atol,
                rtol=convergence_rtol,
                max_time=max_duration_days,
            )

            # Store results
            actual_duration = srt_metrics.get('converged_at', max_duration_days) if srt_metrics else max_duration_days
            simulation_method = 'BDF'
            converged_at = actual_duration
            conv_status = srt_status
            conv_metrics = srt_metrics
            srt_days = achieved_srt

            logger.info(
                f"SRT control complete: achieved_srt={achieved_srt:.1f}d "
                f"(target={target_srt_days}d), status={srt_status}"
            )

        elif run_to_convergence:
            from utils.run_to_convergence import run_system_to_steady_state

            # Set default max_duration from equilibration estimate
            if max_duration_days is None:
                max_duration_days = equil_estimate['recommended_days']

            convergence_components = {
                eff_stream.ID: ['S_NH4', 'S_NO3', 'S_O2'],
                was_stream.ID: ['X_AUT', 'X_H', 'X_PAO'],
            }

            logger.info(
                f"Running to convergence: max_time={max_duration_days:.0f}d, "
                f"atol={convergence_atol}, rtol={convergence_rtol}"
            )

            converged_at, conv_status, conv_metrics = run_system_to_steady_state(
                system=sys,
                convergence_streams=[eff_stream, was_stream],
                convergence_components=convergence_components,
                check_interval=check_interval_days,
                t_step=0.5,
                atol=convergence_atol,
                rtol=convergence_rtol,
                method='BDF',
                max_time=max_duration_days,
            )

            actual_duration = converged_at
            simulation_method = 'BDF'

        else:
            # Fixed-duration simulation (original behavior)
            if duration_days < equil_estimate['minimum_days']:
                logger.warning(
                    f"Simulation duration {duration_days}d may be insufficient for nitrifier "
                    f"equilibration. Minimum recommended: {equil_estimate['minimum_days']:.0f}d, "
                    f"optimal: {equil_estimate['recommended_days']:.0f}d. "
                    f"Consider longer simulation or use run_to_convergence=True."
                )

            logger.info(f"Simulating for {duration_days} days (fixed duration)...")

            # Build simulation kwargs
            sim_kwargs = {
                'state_reset_hook': 'reset_cache',
                't_span': (0, duration_days),
                'method': 'RK23',
            }

            # Add t_eval if timestep_hours is specified
            if timestep_hours is not None and timestep_hours > 0:
                dt = timestep_hours / 24
                t_eval = np.arange(0, duration_days + 1e-9, dt)
                t_eval = t_eval[t_eval <= duration_days + 1e-9]
                if len(t_eval) == 0 or t_eval[-1] < duration_days - 1e-9:
                    t_eval = np.append(t_eval, duration_days)
                sim_kwargs['t_eval'] = t_eval
                logger.info(f"Using timestep {timestep_hours}h -> {len(t_eval)} evaluation points")

            sys.simulate(**sim_kwargs)

            actual_duration = duration_days
            simulation_method = 'RK23'

        logger.info("Simulation completed, analyzing results...")

        inf_analysis = analyze_aerobic_stream(influent)
        eff_analysis = analyze_aerobic_stream(eff_stream)
        performance = analyze_aerobic_performance(
            influent, eff_stream,
            system=sys,
            was_stream=was_stream,
        )

        total_V = V_an + V_ae + V_mbr
        hrt_hours = total_V / Q * 24

        # =====================================================================
        # PHASE 12B: Calculate SRT using QSDsan utilities
        # =====================================================================
        # When SRT control was used, srt_days is already set from controller result
        # Otherwise, calculate SRT post-hoc for reporting purposes
        # =====================================================================
        if target_srt_days is None:
            try:
                from utils.srt_control import calculate_srt, detect_wastage_streams
                wastage_streams = detect_wastage_streams(sys)
                srt_days = calculate_srt(
                    system=sys,
                    wastage_streams=wastage_streams,
                    model_type='ASM2d',
                )
                logger.info(f"Calculated SRT: {srt_days:.1f} days")
            except Exception as e:
                logger.warning(f"Could not calculate SRT: {e}")
                srt_days = None

        result = {
            "status": "completed",
            "template": "ao_mbr_asm2d",
            "influent": {
                "flow_m3_d": Q,
                "temperature_K": T,
                "COD_mg_L": inf_analysis.get('COD_mg_L', 0),
            },
            "reactor": {
                "type": "A/O-MBR",
                "V_anoxic_m3": V_an,
                "V_aerobic_m3": V_ae,
                "V_mbr_m3": V_mbr,
                "V_total_m3": total_V,
                "HRT_hours": hrt_hours,
                "SRT_days": srt_days,
                "DO_aerobic_mg_L": DO_ae,
                "DO_mbr_mg_L": DO_mbr,
                "Q_ras_m3_d": Q_ras,
                "Q_was_m3_d": Q_was,
                "inoculum_X_AUT_mg_COD_L": reactor_inoculum.get('X_AUT', 0),
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
                "duration_days": actual_duration,
                "method": simulation_method,
                "status": "completed",
                "run_to_convergence": run_to_convergence,
            },
        }

        # Add convergence info if applicable
        if run_to_convergence or target_srt_days is not None:
            result["simulation"]["converged_at_days"] = converged_at
            result["simulation"]["convergence_status"] = conv_status
            result["simulation"]["convergence_metrics"] = conv_metrics

        # Add SRT control info if applicable (Phase 12B)
        if target_srt_days is not None:
            result["simulation"]["srt_control"] = {
                "target_srt_days": target_srt_days,
                "achieved_srt_days": srt_days,
                "srt_tolerance": srt_tolerance,
                "srt_status": conv_status,
                "q_was_optimal": conv_metrics.get('q_was_optimal') if conv_metrics else None,
                "srt_iterations": conv_metrics.get('srt_iterations') if conv_metrics else None,
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
            "template": "ao_mbr_asm2d",
            "solver": {
                "method": simulation_method,
                "duration_days": actual_duration,
                "timestep_hours": timestep_hours,
                "rtol": 1e-3,
                "atol": 1e-6,
                "run_to_convergence": run_to_convergence or target_srt_days is not None,
                "convergence_atol": convergence_atol if (run_to_convergence or target_srt_days is not None) else None,
                "convergence_rtol": convergence_rtol if (run_to_convergence or target_srt_days is not None) else None,
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
                        title=f"A/O-MBR - {Q:.0f} m3/d",
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
        logger.error(f"A/O-MBR simulation failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "template": "ao_mbr_asm2d",
            "error": str(e),
        }
