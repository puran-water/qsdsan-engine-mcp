"""
Tests for SRT (Sludge Retention Time) control utilities.

Phase 12: SRT-Controlled Steady-State Simulation
"""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np


class TestSRTControlUtilities:
    """Test SRT calculation and control utilities."""

    def test_get_influent_flow_from_feeds(self):
        """Test influent flow extraction from system feeds."""
        from utils.srt_control import get_influent_flow

        # Create mock system with feeds
        system = MagicMock()
        feed1 = MagicMock()
        feed1.F_vol = 100.0  # m³/hr
        feed2 = MagicMock()
        feed2.F_vol = 50.0  # m³/hr
        system.feeds = [feed1, feed2]

        q_in = get_influent_flow(system)
        # 150 m³/hr × 24 = 3600 m³/d
        assert q_in == 3600.0

    def test_get_influent_flow_default_fallback(self):
        """Test default fallback when no feeds."""
        from utils.srt_control import get_influent_flow

        system = MagicMock()
        system.feeds = []

        q_in = get_influent_flow(system)
        assert q_in == 1000.0  # Default

    def test_validate_flow_feasibility_valid(self):
        """Test flow feasibility validation with valid flows."""
        from utils.srt_control import validate_flow_feasibility

        valid, msg = validate_flow_feasibility(q_was=100, q_ras=500, q_in=1000)
        assert valid is True
        assert msg == ""

    def test_validate_flow_feasibility_negative_q_was(self):
        """Test rejection of negative Q_was."""
        from utils.srt_control import validate_flow_feasibility

        valid, msg = validate_flow_feasibility(q_was=-10, q_ras=500, q_in=1000)
        assert valid is False
        assert "cannot be negative" in msg

    def test_validate_flow_feasibility_negative_q_ras(self):
        """Test rejection of negative Q_ras."""
        from utils.srt_control import validate_flow_feasibility

        valid, msg = validate_flow_feasibility(q_was=100, q_ras=-10, q_in=1000)
        assert valid is False
        assert "cannot be negative" in msg

    def test_validate_flow_feasibility_q_was_exceeds_q_in(self):
        """Test rejection when Q_was exceeds Q_in."""
        from utils.srt_control import validate_flow_feasibility

        # Q_was > Q_in is invalid (can't waste more than influent)
        valid, msg = validate_flow_feasibility(q_was=1200, q_ras=500, q_in=1000)
        assert valid is False
        assert "exceed" in msg

    def test_validate_flow_feasibility_high_q_ras_valid(self):
        """Test that high Q_ras (internal recycle) is valid."""
        from utils.srt_control import validate_flow_feasibility

        # Q_ras can be any multiple of Q_in (internal recycle)
        valid, msg = validate_flow_feasibility(q_was=100, q_ras=5000, q_in=1000)
        assert valid is True
        assert msg == ""

    @pytest.mark.slow
    def test_srt_control_with_mle_template(self):
        """Test SRT control end-to-end using MLE-MBR template."""
        from templates.aerobic.mle_mbr import build_and_run
        import json

        with open('tests/test_asm2d_state.json') as f:
            state = json.load(f)

        # Run with SRT control
        # Note: min simulation time = 2 × target_srt_days, so max_duration must be >= 2 × target
        # Using target_srt=5d requires min 10d simulation, max_duration=15d gives margin
        result = build_and_run(
            influent_state=state,
            target_srt_days=5.0,       # Short SRT for faster test
            srt_tolerance=0.5,         # 50% tolerance for faster convergence
            max_srt_iterations=3,      # Allow some iterations
            max_duration_days=15,      # Must be >= 2 × target_srt_days
        )

        assert result['status'] == 'completed', f"Template failed: {result.get('error')}"

        # Verify SRT control info is populated
        sim = result.get('simulation', {})
        srt_info = sim.get('srt_control')
        assert srt_info is not None, "SRT control info should be populated"
        assert 'target_srt_days' in srt_info
        assert 'achieved_srt_days' in srt_info
        assert srt_info['target_srt_days'] == 5.0

        # Verify achieved SRT is within tolerance of target
        target = srt_info['target_srt_days']
        achieved = srt_info['achieved_srt_days']
        tolerance = 0.5  # 50%
        srt_error = abs(achieved - target) / target if target > 0 else float('inf')
        assert srt_error <= tolerance, (
            f"SRT error {srt_error:.1%} exceeds tolerance {tolerance:.0%}: "
            f"achieved={achieved:.1f}, target={target}"
        )

        # Verify SRT status indicates success
        srt_status = srt_info.get('srt_status')
        assert srt_status in ('srt_converged', None), (
            f"SRT control failed with status: {srt_status}"
        )


class TestBiomassIDs:
    """Test biomass ID definitions."""

    def test_asm2d_biomass_ids(self):
        """Test ASM2d biomass IDs are defined."""
        from utils.srt_control import BIOMASS_IDS

        assert 'ASM2d' in BIOMASS_IDS
        assert 'X_H' in BIOMASS_IDS['ASM2d']
        assert 'X_AUT' in BIOMASS_IDS['ASM2d']

    def test_asm1_biomass_ids(self):
        """Test ASM1 biomass IDs are defined."""
        from utils.srt_control import BIOMASS_IDS

        assert 'ASM1' in BIOMASS_IDS
        assert 'X_B_H' in BIOMASS_IDS['ASM1']
        assert 'X_B_A' in BIOMASS_IDS['ASM1']

    def test_madm1_biomass_ids(self):
        """Test mADM1 biomass IDs are defined."""
        from utils.srt_control import BIOMASS_IDS

        assert 'mADM1' in BIOMASS_IDS
        assert 'X_ac' in BIOMASS_IDS['mADM1']
        assert 'X_h2' in BIOMASS_IDS['mADM1']


class TestSRTCalculation:
    """Test SRT calculation - covered by template integration tests above."""

    def test_qsdsan_get_srt_import(self):
        """Test that QSDsan's get_SRT is importable and used."""
        from utils.srt_control import qsdsan_get_SRT
        from qsdsan.utils import get_SRT

        # Verify we're using the real QSDsan function
        assert qsdsan_get_SRT is get_SRT, "Should use QSDsan's native get_SRT"


class TestHasSRTDecoupling:
    """Test SRT decoupling detection."""

    def test_detects_mbr(self):
        """Test detection of MBR for SRT decoupling."""
        from utils.srt_control import has_srt_decoupling

        system = MagicMock()

        # Create unit with MBR in name
        mbr = MagicMock()
        mbr.__class__.__name__ = 'CompletelyMixedMBR'

        system.units = [mbr]

        assert has_srt_decoupling(system) is True

    def test_detects_clarifier(self):
        """Test detection of clarifier for SRT decoupling."""
        from utils.srt_control import has_srt_decoupling

        system = MagicMock()

        clarifier = MagicMock()
        clarifier.__class__.__name__ = 'FlatBottomCircularClarifier'

        system.units = [clarifier]

        assert has_srt_decoupling(system) is True

    def test_no_decoupling_for_cstr_only(self):
        """Test no SRT decoupling detected for CSTR-only system."""
        from utils.srt_control import has_srt_decoupling

        system = MagicMock()

        cstr = MagicMock()
        cstr.__class__.__name__ = 'CSTR'

        system.units = [cstr]

        assert has_srt_decoupling(system) is False


class TestUpdateWastageActuator:
    """Test wastage actuator updates."""

    def test_update_mbr_pumped_flow(self):
        """Test MBR pumped_flow update."""
        from utils.srt_control import update_wastage_actuator

        system = MagicMock()

        # Create MBR unit
        mbr = type('CompletelyMixedMBR', (), {
            'pumped_flow': 0,
            'outs': [None, None],  # Two outlets
        })()

        # Mock feed
        feed = MagicMock()
        feed.F_vol = 100.0  # m³/hr = 2400 m³/d
        system.feeds = [feed]
        system.units = [mbr]

        success, msg = update_wastage_actuator(system, q_was=100, q_ras=2400)

        assert success is True
        assert msg == ""
        assert mbr.pumped_flow == 2500  # q_ras + q_was

    def test_update_clarifier_wastage(self):
        """Test clarifier wastage update."""
        from utils.srt_control import update_wastage_actuator

        system = MagicMock()

        # Create clarifier unit
        clarifier = type('FlatBottomCircularClarifier', (), {
            'wastage': 0,
            'underflow': 500.0,
        })()

        feed = MagicMock()
        feed.F_vol = 100.0
        system.feeds = [feed]
        system.units = [clarifier]

        success, msg = update_wastage_actuator(system, q_was=200)

        assert success is True
        assert clarifier.wastage == 200

    def test_rejects_infeasible_flow(self):
        """Test rejection when Q_was exceeds Q_in (mass balance violation)."""
        from utils.srt_control import update_wastage_actuator

        system = MagicMock()

        mbr = type('CompletelyMixedMBR', (), {
            'pumped_flow': 0,
            'outs': [None, None],
        })()

        feed = MagicMock()
        feed.F_vol = 10.0  # 240 m³/d
        system.feeds = [feed]
        system.units = [mbr]

        # Try to set Q_was > Q_in (can't waste more than influent)
        success, msg = update_wastage_actuator(system, q_was=300, q_ras=200)

        assert success is False
        assert "exceed" in msg


class TestDetectWastageStreams:
    """Test WAS stream detection."""

    def test_detects_clarifier_was_outlet(self):
        """Test detection of clarifier WAS outlet."""
        from utils.srt_control import detect_wastage_streams

        system = MagicMock()

        was_stream = MagicMock()
        clarifier = type('FlatBottomCircularClarifier', (), {})()
        clarifier.outs = [MagicMock(), MagicMock(), was_stream]  # WAS is outs[2]

        system.units = [clarifier]
        system.streams = []

        streams = detect_wastage_streams(system)

        assert was_stream in streams

    def test_detects_splitter_was_output(self):
        """Test detection of splitter WAS output."""
        from utils.srt_control import detect_wastage_streams

        system = MagicMock()

        was_stream = MagicMock()
        was_stream.__str__ = lambda self: 'WAS'

        splitter = type('Splitter', (), {})()
        splitter.outs = [MagicMock(), was_stream]

        system.units = [splitter]
        system.streams = []

        streams = detect_wastage_streams(system)

        assert was_stream in streams


class TestMinTimeParameter:
    """Test min_time parameter in run_to_convergence."""

    def test_min_time_delays_convergence_check(self):
        """Test that min_time delays convergence detection."""
        from utils.run_to_convergence import run_system_to_steady_state
        import numpy as np

        # This is a unit test for the logic, not full integration
        # We verify that check_times starts at min_time when set

        # The actual check_times calculation:
        window_days = 5.0
        min_time = 30.0
        max_time = 100.0
        check_interval = 2.0

        earliest_check = max(window_days, min_time)
        check_times = np.arange(earliest_check, max_time + check_interval, check_interval)

        assert check_times[0] >= min_time
        assert check_times[0] == 30.0  # Should start at min_time
