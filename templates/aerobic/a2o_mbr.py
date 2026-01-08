"""
A2O-MBR (Anaerobic-Anoxic-Oxic with Membrane Bioreactor) Template.

Enhanced biological phosphorus removal (EBPR) configuration.

Flowsheet: Influent -> AN -> A1 -> O1 -> O2 -> MBR -> Effluent + WAS
                            ^_______RAS_______v

The anaerobic zone (AN) promotes PAO enrichment for EBPR.

Usage:
    from templates.aerobic.a2o_mbr import build_and_run

    result = build_and_run(
        influent_state={"flow_m3_d": 4000, "temperature_K": 293.15, "concentrations": {...}},
        reactor_config={"V_anaerobic_m3": 100, "V_anoxic_m3": 156, "V_aerobic_m3": 252, "V_mbr_m3": 382},
        duration_days=15,
    )
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

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
    """Get default reactor configuration for A2O-MBR with EBPR."""
    return {
        "V_anaerobic_m3": 100,    # Anaerobic zone for PAO
        "V_anoxic_m3": 156,       # Anoxic zone
        "V_aerobic_m3": 252,      # Per stage (2 aerobic stages)
        "n_aerobic_stages": 2,
        "V_mbr_m3": 382,
        "DO_aerobic_mg_L": 2.3,
        "DO_mbr_mg_L": 2.2,
        "SCR": 0.999,
        "Q_ras_multiplier": 4.0,
        "Q_was_m3_d": 768,
    }


def get_default_influent() -> Dict[str, float]:
    """Get default domestic wastewater composition (mg/L)."""
    return DEFAULT_DOMESTIC_WW.copy()


def build_and_run(
    influent_state: Dict[str, Any],
    reactor_config: Optional[Dict[str, Any]] = None,
    kinetic_params: Optional[Dict[str, Any]] = None,
    duration_days: float = 15.0,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build and run A2O-MBR simulation with ASM2d.

    Enhanced biological phosphorus removal (EBPR) via anaerobic zone.
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

        logger.info(f"Building A2O-MBR system with EBPR: Q={Q} m³/d, T={T-273.15}°C")

        cmps = create_asm2d_components(set_thermo=True)

        influent = qs.WasteStream('influent', T=T)
        influent.set_flow_by_concentration(Q, concentrations=concentrations, units=('m3/d', 'mg/L'))

        RAS = qs.WasteStream('RAS', T=T)
        WAS = qs.WasteStream('WAS', T=T)

        asm2d = pc.ASM2d(**asm_kwargs)

        V_ana = config['V_anaerobic_m3']
        V_an = config['V_anoxic_m3']
        V_ae = config['V_aerobic_m3']
        n_ae = config.get('n_aerobic_stages', 2)
        V_mbr = config['V_mbr_m3']
        DO_ae = config['DO_aerobic_mg_L']
        DO_mbr = config['DO_mbr_mg_L']
        SCR = config['SCR']
        Q_ras = Q * config['Q_ras_multiplier']
        Q_was = config['Q_was_m3_d']
        split_ras = Q_ras / (Q_ras + Q_was)

        # Anaerobic zone (for PAO enrichment - no aeration, no nitrate recycle)
        AN = su.CSTR(
            'AN',
            ins=[influent],
            V_max=V_ana,
            aeration=None,
            DO_ID=None,
            suspended_growth_model=asm2d,
        )

        # Anoxic zone (receives RAS)
        A1 = su.CSTR(
            'A1',
            ins=[AN-0, RAS],
            V_max=V_an,
            aeration=None,
            DO_ID=None,
            suspended_growth_model=asm2d,
        )

        # Aerobic zones
        O1 = su.CSTR(
            'O1',
            ins=[A1-0],
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

        # MBR
        MBR = su.CompletelyMixedMBR(
            'MBR',
            ins=O2-0,
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

        sys = qs.System(
            'A2O_MBR',
            path=(AN, A1, O1, O2, MBR, SP),
            recycle=[RAS],
        )

        sys.set_dynamic_tracker(*sys.products)

        logger.info(f"Simulating for {duration_days} days...")

        sys.simulate(
            state_reset_hook='reset_cache',
            t_span=(0, duration_days),
            method='RK23',
        )

        logger.info("Simulation completed, analyzing results...")

        eff_stream = sys.flowsheet.stream.effluent
        was_stream = sys.flowsheet.stream.WAS

        inf_analysis = analyze_aerobic_stream(influent)
        eff_analysis = analyze_aerobic_stream(eff_stream)
        performance = analyze_aerobic_performance(
            influent, eff_stream,
            system=sys,
            was_stream=was_stream,
        )

        total_V = V_ana + V_an + n_ae * V_ae + V_mbr
        hrt_hours = total_V / Q * 24

        # Check EBPR activity
        pao_conc = eff_analysis.get('biomass', {}).get('X_PAO_mg_COD_L', 0)
        pp_conc = eff_analysis.get('phosphorus', {}).get('PP_mg_P_L', 0)
        ebpr_active = pao_conc > 50 and pp_conc > 10

        result = {
            "status": "completed",
            "template": "a2o_mbr_asm2d",
            "influent": {
                "flow_m3_d": Q,
                "temperature_K": T,
                "COD_mg_L": inf_analysis.get('COD_mg_L', 0),
                "PO4_mg_P_L": inf_analysis.get('phosphorus', {}).get('PO4_mg_P_L', 0),
            },
            "reactor": {
                "type": "A2O-MBR",
                "V_anaerobic_m3": V_ana,
                "V_anoxic_m3": V_an,
                "V_aerobic_m3": n_ae * V_ae,
                "V_mbr_m3": V_mbr,
                "V_total_m3": total_V,
                "HRT_hours": hrt_hours,
                "DO_aerobic_mg_L": DO_ae,
                "DO_mbr_mg_L": DO_mbr,
                "Q_ras_m3_d": Q_ras,
                "Q_was_m3_d": Q_was,
            },
            "effluent": {
                "COD_mg_L": eff_analysis.get('COD_mg_L', 0),
                "TSS_mg_L": eff_analysis.get('TSS_mg_L', 0),
                "NH4_mg_N_L": eff_analysis.get('nitrogen', {}).get('NH4_mg_N_L', 0),
                "NO3_mg_N_L": eff_analysis.get('nitrogen', {}).get('NO3_mg_N_L', 0),
                "PO4_mg_P_L": eff_analysis.get('phosphorus', {}).get('PO4_mg_P_L', 0),
            },
            "ebpr": {
                "active": ebpr_active,
                "PAO_biomass_mg_COD_L": pao_conc,
                "PP_storage_mg_P_L": pp_conc,
            },
            "performance": performance if performance.get('success') else {"error": performance.get('error')},
            "simulation": {
                "duration_days": duration_days,
                "method": "RK23",
                "status": "completed",
            },
        }

        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_dir / "simulation_results.json", "w") as f:
                json.dump(result, f, indent=2, default=str)
            logger.info(f"Results saved to {output_dir}")

        return result

    except Exception as e:
        logger.error(f"A2O-MBR simulation failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "template": "a2o_mbr_asm2d",
            "error": str(e),
        }
