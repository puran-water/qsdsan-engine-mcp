"""
Phase 1 Comprehensive Test Suite

Per Codex review recommendations, this test suite covers:
1. Model Registry - Component counts and IDs
2. Template Registry - All 4 templates
3. State Validation - Edge cases
4. Aerobic Flowsheets - MLE, A/O, A2O MBR
5. Anaerobic Flowsheet - CSTR mADM1
6. Edge Cases - Zero flow, missing components

Run with: python -m pytest tests/test_phase1.py -v
"""

import json
import sys
import pytest
from pathlib import Path

# =============================================================================
# Model Registry Tests
# =============================================================================

class TestModelRegistry:
    """Test model component definitions."""

    def test_madm1_component_count(self):
        """mADM1 should have exactly 63 components."""
        from core.model_registry import MADM1_COMPONENTS
        assert len(MADM1_COMPONENTS) == 63, f"Expected 63, got {len(MADM1_COMPONENTS)}"

    def test_asm2d_component_count(self):
        """ASM2d should have exactly 19 components (including X_MeP)."""
        from core.model_registry import ASM2D_COMPONENTS
        assert len(ASM2D_COMPONENTS) == 19, f"Expected 19, got {len(ASM2D_COMPONENTS)}"

    def test_asm2d_has_x_mep(self):
        """ASM2d must include X_MeP for chemical P removal."""
        from core.model_registry import ASM2D_COMPONENTS
        assert 'X_MeP' in ASM2D_COMPONENTS, "X_MeP missing from ASM2D_COMPONENTS"

    def test_asm2d_has_x_meoh(self):
        """ASM2d must include X_MeOH (Metal-hydroxides)."""
        from core.model_registry import ASM2D_COMPONENTS
        assert 'X_MeOH' in ASM2D_COMPONENTS, "X_MeOH missing from ASM2D_COMPONENTS"

    def test_madm1_srb_components_disaggregated(self):
        """mADM1 SRB should be disaggregated (not lumped X_SRB)."""
        from core.model_registry import MADM1_COMPONENTS
        # Should have all 4 disaggregated SRB
        srb_ids = ['X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB']
        for srb in srb_ids:
            assert srb in MADM1_COMPONENTS, f"{srb} missing from MADM1_COMPONENTS"
        # Should NOT have lumped X_SRB
        assert 'X_SRB' not in MADM1_COMPONENTS, "Lumped X_SRB should not be in MADM1_COMPONENTS"

    def test_madm1_has_s_fe3(self):
        """mADM1 should have S_Fe3 (not generic S_Fe)."""
        from core.model_registry import MADM1_COMPONENTS
        assert 'S_Fe3' in MADM1_COMPONENTS, "S_Fe3 missing from MADM1_COMPONENTS"
        assert 'S_Fe' not in MADM1_COMPONENTS, "Generic S_Fe should not be in MADM1_COMPONENTS"


# =============================================================================
# Template Registry Tests
# =============================================================================

class TestTemplateRegistry:
    """Test template definitions."""

    def test_template_count(self):
        """Should have 4 templates: 3 aerobic + 1 anaerobic."""
        from core.template_registry import TEMPLATES
        assert len(TEMPLATES) == 4, f"Expected 4 templates, got {len(TEMPLATES)}"

    def test_aerobic_templates_exist(self):
        """All 3 aerobic templates should exist."""
        from core.template_registry import TEMPLATES
        expected = ['mle_mbr_asm2d', 'ao_mbr_asm2d', 'a2o_mbr_asm2d']
        for template_id in expected:
            assert template_id in TEMPLATES, f"{template_id} not in TEMPLATES"

    def test_anaerobic_template_exists(self):
        """Anaerobic CSTR template should exist."""
        from core.template_registry import TEMPLATES
        assert 'anaerobic_cstr_madm1' in TEMPLATES

    def test_templates_are_available(self):
        """All templates should have AVAILABLE status."""
        from core.template_registry import TEMPLATES, TemplateStatus
        for template_id, template in TEMPLATES.items():
            assert template.status == TemplateStatus.AVAILABLE, \
                f"{template_id} status is {template.status}, expected AVAILABLE"


# =============================================================================
# State Validation Tests
# =============================================================================

class TestStateValidation:
    """Test PlantState validation."""

    def test_validate_asm2d_state(self):
        """Valid ASM2d state should pass validation."""
        from core.model_registry import validate_components
        from core.plant_state import ModelType

        state_file = Path(__file__).parent / "test_asm2d_state.json"
        with open(state_file) as f:
            state = json.load(f)

        provided = set(state['concentrations'].keys())
        missing, extra = validate_components(ModelType.ASM2D, provided)

        assert len(missing) == 0, f"Missing components: {missing}"
        assert len(extra) == 0, f"Extra components: {extra}"

    def test_validate_madm1_state(self):
        """Valid mADM1 state should pass validation."""
        from core.model_registry import validate_components
        from core.plant_state import ModelType

        state_file = Path(__file__).parent / "test_madm1_state.json"
        with open(state_file) as f:
            state = json.load(f)

        provided = set(state['concentrations'].keys())
        missing, extra = validate_components(ModelType.MADM1, provided)

        assert len(missing) == 0, f"Missing components: {missing}"
        assert len(extra) == 0, f"Extra components: {extra}"

    def test_missing_component_detected(self):
        """Validation should detect missing components."""
        from core.model_registry import validate_components
        from core.plant_state import ModelType

        # Provide incomplete ASM2d state (missing X_MeP)
        incomplete = {'S_O2', 'S_F', 'S_A', 'S_I', 'S_NH4', 'S_N2', 'S_NO3', 'S_PO4',
                      'S_ALK', 'X_I', 'X_S', 'X_H', 'X_PAO', 'X_PP', 'X_PHA', 'X_AUT',
                      'X_MeOH', 'H2O'}  # Missing X_MeP

        missing, extra = validate_components(ModelType.ASM2D, incomplete)
        assert 'X_MeP' in missing, "Should detect X_MeP as missing"

    def test_extra_component_detected(self):
        """Validation should detect extra components."""
        from core.model_registry import validate_components
        from core.plant_state import ModelType

        # Add an invalid component
        state_file = Path(__file__).parent / "test_asm2d_state.json"
        with open(state_file) as f:
            state = json.load(f)

        provided = set(state['concentrations'].keys())
        provided.add('X_INVALID')

        missing, extra = validate_components(ModelType.ASM2D, provided)
        assert 'X_INVALID' in extra, "Should detect X_INVALID as extra"


# =============================================================================
# Aerobic Analysis Tests
# =============================================================================

class TestAerobicAnalysis:
    """Test aerobic-specific analysis functions."""

    def test_default_asm2d_kwargs_exists(self):
        """DEFAULT_ASM2D_KWARGS should be importable."""
        from models.asm2d import DEFAULT_ASM2D_KWARGS
        assert isinstance(DEFAULT_ASM2D_KWARGS, dict)
        assert 'Y_H' in DEFAULT_ASM2D_KWARGS, "Should include Y_H (heterotroph yield)"

    def test_default_domestic_ww_exists(self):
        """DEFAULT_DOMESTIC_WW should be importable."""
        from models.asm2d import DEFAULT_DOMESTIC_WW
        assert isinstance(DEFAULT_DOMESTIC_WW, dict)
        assert 'S_F' in DEFAULT_DOMESTIC_WW, "Should include S_F"
        assert 'X_MeOH' in DEFAULT_DOMESTIC_WW, "Should include X_MeOH"

    def test_create_asm2d_components(self):
        """create_asm2d_components should return 19 components."""
        from models.asm2d import create_asm2d_components
        cmps = create_asm2d_components(set_thermo=False)
        assert len(cmps.IDs) == 19, f"Expected 19, got {len(cmps.IDs)}"


# =============================================================================
# Anaerobic Analysis Tests
# =============================================================================

class TestAnaerobicAnalysis:
    """Test anaerobic-specific analysis functions."""

    def test_empty_sulfur_metrics_on_zero_flow(self):
        """Sulfur metrics should return empty dict on zero flow, not raise."""
        from utils.analysis.anaerobic import _empty_sulfur_metrics

        result = _empty_sulfur_metrics("test reason")
        assert result['success'] is False
        assert result['reason'] == "test reason"
        assert result['sulfate_in_mg_L'] == 0.0


# =============================================================================
# CLI Integration Tests
# =============================================================================

class TestCLIIntegration:
    """Test CLI commands (non-simulation)."""

    def test_cli_templates_command(self):
        """CLI templates command should list 4 templates grouped by category."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'templates', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        output = json.loads(result.stdout)
        # Output has 'aerobic', 'anaerobic', and 'models' keys
        assert 'aerobic' in output, "Missing 'aerobic' key"
        assert 'anaerobic' in output, "Missing 'anaerobic' key"
        # 3 aerobic + 1 anaerobic = 4 templates total
        total_templates = len(output['aerobic']) + len(output['anaerobic'])
        assert total_templates == 4, f"Expected 4 templates, got {total_templates}"

    def test_cli_validate_asm2d(self):
        """CLI validate should pass for valid ASM2d state."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'validate',
             '--state', 'tests/test_asm2d_state.json', '--model', 'ASM2d', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['is_valid'] is True
        assert output['n_components_provided'] == 19

    def test_cli_validate_madm1(self):
        """CLI validate should pass for valid mADM1 state."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'validate',
             '--state', 'tests/test_madm1_state.json', '--model', 'mADM1', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['is_valid'] is True
        assert output['n_components_provided'] == 63


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_model_type_raises(self):
        """get_model_info should raise for invalid model type."""
        from core.model_registry import get_model_info
        from core.plant_state import ModelType

        # Create a fake model type
        class FakeModelType:
            value = "FAKE"

        with pytest.raises(ValueError, match="Unknown model type"):
            get_model_info(FakeModelType())

    def test_asm2d_loader_in_models_init(self):
        """models.__init__ should lazy-export ASM2d functions."""
        from models import create_asm2d_components, DEFAULT_ASM2D_KWARGS, DEFAULT_DOMESTIC_WW

        assert callable(create_asm2d_components)
        assert isinstance(DEFAULT_ASM2D_KWARGS, dict)
        assert isinstance(DEFAULT_DOMESTIC_WW, dict)


# =============================================================================
# Simulation Tests (Slow - require QSDsan)
# =============================================================================

@pytest.mark.slow
class TestSimulations:
    """Integration tests that run actual simulations."""

    def test_mle_mbr_simulation(self):
        """MLE-MBR template should complete without errors."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'simulate',
             '-t', 'mle_mbr_asm2d', '-i', 'tests/test_asm2d_state.json',
             '-d', '1.0', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
            timeout=120
        )
        assert result.returncode == 0, f"Simulation failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['status'] == 'completed'
        assert output['template'] == 'mle_mbr_asm2d'
        # Performance uses nested structure
        assert output['performance']['cod']['removal_pct'] > 0

    def test_ao_mbr_simulation(self):
        """A/O-MBR template should complete without errors."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'simulate',
             '-t', 'ao_mbr_asm2d', '-i', 'tests/test_asm2d_state.json',
             '-d', '1.0', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
            timeout=120
        )
        assert result.returncode == 0, f"Simulation failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['status'] == 'completed'
        assert output['template'] == 'ao_mbr_asm2d'

    def test_a2o_mbr_simulation(self):
        """A2O-MBR template should complete without errors."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'simulate',
             '-t', 'a2o_mbr_asm2d', '-i', 'tests/test_asm2d_state.json',
             '-d', '1.0', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
            timeout=120
        )
        assert result.returncode == 0, f"Simulation failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['status'] == 'completed'
        assert output['template'] == 'a2o_mbr_asm2d'

    def test_anaerobic_cstr_simulation(self):
        """Anaerobic CSTR template should complete without errors."""
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cli.py', 'simulate',
             '-t', 'anaerobic_cstr_madm1', '-i', 'tests/test_madm1_state.json',
             '-d', '5.0', '--json-out'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
            timeout=180  # Anaerobic needs longer
        )
        assert result.returncode == 0, f"Simulation failed: {result.stderr}"

        output = json.loads(result.stdout)
        assert output['status'] == 'completed'
        assert output['template'] == 'anaerobic_cstr_madm1'
        # Anaerobic should produce biogas at top level
        assert 'biogas' in output, "Biogas not in output"
        assert output['biogas']['flow_total_Nm3_d'] > 0, "No biogas production"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
