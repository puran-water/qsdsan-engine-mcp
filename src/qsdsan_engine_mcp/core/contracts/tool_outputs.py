"""Pydantic output contracts for MCP tool registration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel
from pydantic import BaseModel, Field, create_model, model_validator

from qsdsan_engine_mcp.core.errors import PublicError


class ToolOutputBase(StrictFiniteBaseModel):
    """Base result envelope shared by all public tool outputs."""

    message: str | None = None
    code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


_PUBLIC_ERROR_KEYS = {"code", "message", "details", "correlation_id"}


class ToolResultBase(ToolOutputBase):
    """Result model that accepts either success fields or a public error."""

    _success_required_fields: ClassVar[frozenset[str]] = frozenset()
    _json_schema_override: ClassVar[dict[str, Any] | None] = None

    @model_validator(mode="before")
    @classmethod
    def _require_success_or_public_error(cls, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload

        keys = set(payload)
        if {"code", "message"} <= keys <= _PUBLIC_ERROR_KEYS:
            if keys != _PUBLIC_ERROR_KEYS:
                missing = ", ".join(sorted(_PUBLIC_ERROR_KEYS - keys))
                raise ValueError(f"public error payload missing fields: {missing}")
            PublicError.model_validate(payload)
            return payload

        missing = cls._success_required_fields - keys
        if missing:
            raise ValueError(
                "success payload missing required fields: "
                + ", ".join(sorted(missing))
            )
        return payload

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if cls._json_schema_override is not None:
            return deepcopy(cls._json_schema_override)
        return super().model_json_schema(*args, **kwargs)


JsonDict = dict[str, Any]
JsonList = list[Any]


def _optional(field_type: Any, default: Any = None) -> tuple[Any, Any]:
    return field_type | None, Field(default=default)


def _required(field_type: Any) -> tuple[Any, Any]:
    return field_type, Field(...)


def _success_model(tool_name: str, name: str, fields: dict[str, Any]) -> type[ToolOutputBase]:
    return create_model(
        name,
        __base__=ToolOutputBase,
        **{
            field_name: (
                _optional(field_type)
                if field_name in OPTIONAL_TOOL_OUTPUT_FIELDS.get(tool_name, set())
                else _required(field_type)
            )
            for field_name, field_type in fields.items()
        },
    )


def _result_schema(name: str, success_model: type[ToolOutputBase]) -> dict[str, Any]:
    return {
        "title": name,
        "type": "object",
        "anyOf": [
            success_model.model_json_schema(),
            PublicError.model_json_schema(),
        ],
    }


def _result_model(tool_name: str, name: str, fields: dict[str, Any]) -> type[BaseModel]:
    success_model = _success_model(tool_name, f"{name}Success", fields)
    required_fields = frozenset(
        field_name
        for field_name in fields
        if field_name not in OPTIONAL_TOOL_OUTPUT_FIELDS.get(tool_name, set())
    )
    base_model = type(
        f"{name}Base",
        (ToolResultBase,),
        {
            "__module__": __name__,
            "_success_required_fields": required_fields,
            "_json_schema_override": _result_schema(name, success_model),
        },
    )
    return create_model(
        name,
        __base__=base_model,
        **{field_name: _optional(field_type) for field_name, field_type in fields.items()},
    )


TOOL_OUTPUT_FIELD_TYPES: dict[str, dict[str, Any]] = {
    "get_version": {
        "engine_version": str,
        "qsdsan_version": str,
        "biosteam_version": str,
        "python_version": str,
    },
    "get_job_status": {
        "job_id": str,
        "status": str,
        "elapsed_time_seconds": float,
        "started_at": float,
        "progress": JsonDict,
        "completed_at": float,
        "total_time_seconds": float,
        "exit_code": int,
        "timeout_seconds": float,
        "time_remaining_seconds": float,
    },
    "get_job_results": {
        "job_id": str,
        "status": str,
        "total_time_seconds": float,
        "stdout_file": str,
        "stderr_file": str,
        "results": JsonDict,
        "result_file": str,
        "warning": str,
        "time_series_available": bool,
        "time_series_note": str,
        "artifact_persistence": JsonDict,
        "result_status": str,
        "result_valid": bool,
        "result_validity_blockers": JsonList,
    },
    "build_simulation_diagnostics_payload": {
        "schema_version": str,
        "project_ref": str | None,
        "plant_slug": str | None,
        "engine": str,
        "engine_name": str,
        "engine_run_id": str,
        "job_id": str,
        "session_id": str | None,
        "producer_tool": str,
        "generated_at": str,
        "status": str,
        "result_valid": bool,
        "result_validity_blockers": JsonList,
        "model_type": str | None,
        "unit_ops": JsonList,
        "session": JsonDict,
        "simulation_diagnostics": JsonDict,
        "process_diagnostics": JsonDict,
        "stream_states": JsonDict,
        "raw_results": JsonDict,
        "accessibility_contract": JsonDict,
    },
    "get_process_diagnostics": {
        "schema_version": str,
        "project_ref": str | None,
        "plant_slug": str | None,
        "engine": str,
        "engine_name": str,
        "engine_run_id": str,
        "job_id": str,
        "session_id": str | None,
        "producer_tool": str,
        "generated_at": str,
        "status": str,
        "result_valid": bool,
        "result_validity_blockers": JsonList,
        "model_type": str | None,
        "unit_ops": JsonList,
        "session": JsonDict,
        "simulation_diagnostics": JsonDict,
        "process_diagnostics": JsonDict,
        "stream_states": JsonDict,
        "raw_results": JsonDict,
        "accessibility_contract": JsonDict,
    },
    "persist_simulation_artifacts": {
        "job_id": str,
        "artifact_persistence": JsonDict,
    },
    "validate_state": {
        "model_type": str,
        "is_valid": bool,
        "errors": list[str],
        "warnings": list[str],
        "charge_balance": JsonDict | None,
        "mass_balance": JsonDict | None,
        "missing_components": list[str],
        "extra_components": list[str],
    },
    "convert_state": {
        "job_id": str,
        "status": str,
        "conversion": str,
        "conversion_method": str,
        "message": str,
        "state": JsonDict,
        "supported": list[str],
    },
    "list_jobs": {
        "jobs": JsonList,
        "total": int,
        "filter": str,
        "running_jobs": int,
        "max_concurrent": int,
    },
    "terminate_job": {
        "job_id": str,
        "status": str,
        "message": str,
    },
    "get_timeseries_data": {
        "job_id": str,
        "status": str,
        "time_series": JsonDict,
        "result_file": str,
        "note": str,
    },
    "create_flowsheet_session": {
        "session_id": str,
        "model_type": str,
        "status": str,
        "message": str,
        "compatible_units": JsonList,
    },
    "create_session": {
        "session_id": str,
        "model_type": str,
        "status": str,
        "message": str,
        "compatible_units": JsonList,
    },
    "create_stream": {
        "session_id": str,
        "stream_id": str,
        "status": str,
        "message": str,
        "model_type": str,
        "session_status": str,
        "stream": JsonDict,
        "warnings": list[str],
        "was_compiled": bool,
        "was_failed": bool,
    },
    "update_stream": {
        "session_id": str,
        "stream_id": str,
        "status": str,
        "message": str,
        "stream": JsonDict,
        "warnings": list[str],
        "updated_fields": list[str],
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "delete_stream": {
        "session_id": str,
        "stream_id": str,
        "status": str,
        "success": bool,
        "message": str,
        "removed_from_units": JsonList,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "create_unit": {
        "session_id": str,
        "unit_id": str,
        "unit_type": str,
        "status": str,
        "message": str,
        "model_type": str,
        "warnings": list[str],
        "n_ins": int,
        "n_outs": int,
        "effective_model": str,
        "kinetic_model": str,
        "engineering_profile": str,
        "engineering_inputs": JsonDict,
        "engineering_requirements": JsonDict,
        "applied_engineering_defaults": JsonDict,
        "stream_topology": JsonDict,
        "has_aux_config": bool,
        "has_source_spec": bool,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "update_unit": {
        "session_id": str,
        "unit_id": str,
        "status": str,
        "message": str,
        "unit": JsonDict,
        "warnings": list[str],
        "updated_fields": list[str],
        "engineering_profile": str,
        "engineering_inputs": JsonDict,
        "applied_engineering_defaults": JsonDict,
        "has_aux_config": bool,
        "has_source_spec": bool,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "set_unit_engineering_inputs": {
        "session_id": str,
        "unit_id": str,
        "status": str,
        "message": str,
        "params": JsonDict,
        "aux_config": JsonDict,
        "source_spec": JsonDict,
        "warnings": list[str],
        "updated_fields": list[str],
        "engineering_profile": str,
        "engineering_inputs": JsonDict,
        "applied_defaults": JsonDict,
        "applied_engineering_defaults": JsonDict,
        "has_aux_config": bool,
        "has_source_spec": bool,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "delete_unit": {
        "session_id": str,
        "unit_id": str,
        "status": str,
        "success": bool,
        "message": str,
        "removed_connections": JsonList,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "connect_units": {
        "session_id": str,
        "connections_added": int,
        "results": JsonList,
        "status": str,
        "message": str,
    },
    "delete_connection": {
        "session_id": str,
        "connection_id": str,
        "from": str,
        "to": str,
        "status": str,
        "success": bool,
        "message": str,
        "session_status": str,
        "was_compiled": bool,
        "was_failed": bool,
    },
    "generate_session_inoculum": {
        "session_id": str,
        "model_type": str,
        "engine_family": str,
        "status": str,
        "message": str,
        "inoculum": JsonDict,
        "influent_basis": JsonDict,
        "created_streams": JsonList,
        "units_seeded": list[str],
        "components_seeded": list[str],
        "startup_metadata": JsonDict,
        "warnings": list[str],
    },
    "build_from_simulation_seeds": {
        "session_id": str,
        "status": str,
        "message": str,
        "model_type": str,
        "created_units": JsonList,
        "connections": JsonList,
        "external_streams": JsonList,
        "recycle_stream_ids": list[str],
        "role_map": JsonDict,
        "roles_by_name": JsonDict,
        "warnings": list[str],
    },
    "build_system": {
        "session_id": str,
        "system_id": str,
        "status": str,
        "message": str,
        "units_created": int,
        "streams_created": int,
        "unit_order": list[str],
        "warnings": list[str],
        "errors": list[str],
        "auto_detected_recycles": bool,
        "recycle_edges": JsonList,
        "recycle_streams": list[str],
    },
    "simulate_built_system": {
        "job_id": str,
        "session_id": str,
        "status": str,
        "message": str,
        "duration_days": float,
        "credibility": JsonDict,
    },
    "get_flowsheet_session": {
        "session_id": str,
        "model_type": str,
        "primary_model_type": str,
        "model_types": list[str],
        "status": str,
        "session": JsonDict,
        "costing": JsonDict,
        "stream_roles": JsonDict,
        "startup_metadata": JsonDict,
        "streams": JsonDict,
        "units": JsonDict,
        "connections": JsonList,
        "created_at": str,
        "updated_at": str,
    },
    "list_flowsheet_sessions": {
        "sessions": JsonList,
        "total": int,
        "filter": str,
    },
    "clone_session": {
        "session_id": str,
        "source_session_id": str,
        "cloned_session_id": str,
        "new_session_id": str,
        "status": str,
        "message": str,
        "n_streams": int,
        "n_units": int,
        "n_connections": int,
    },
    "get_flowsheet_timeseries": {
        "job_id": str,
        "n_streams": int,
        "n_timepoints": int,
        "streams": JsonDict,
        "time": JsonList,
        "time_units": str,
        "component_evidence_available": bool,
        "omitted_zero_components": dict[str, list[str]],
        "unavailable_streams": list[str],
        "unavailable_components": dict[str, list[str]],
        "component_findings": JsonList,
        "findings_basis": str,
    },
    "delete_session": {
        "session_id": str,
        "status": str,
        "success": bool,
        "message": str,
    },
    "list_units": {
        "units": JsonList,
        "total": int,
        "filter": JsonDict,
        "categories": JsonDict,
    },
    "get_unit_spec": {
        "unit_type": str,
        "category": str,
        "canonical_unit_op_type": str,
        "description": str,
        "compatible_models": list[str],
        "is_dynamic": bool,
        "n_ins": int,
        "n_outs": int,
        "solve_supported": bool,
        "publicly_supported": bool,
        "support_note": str,
        "zone_family": str,
        "component_profile": str,
        "output_roles": list[str],
        "aux_schema": JsonDict,
        "bootstrap_policy": str,
        "engine_fit": str,
        "recommended_use_cases": list[str],
        "handoff_ready_profiles": list[str],
        "engineering_profiles": JsonList,
        "engineering_inputs": JsonDict,
        "applied_profile_defaults": JsonDict,
        "qsdsan_class": str,
        "costing_supported": bool,
        "costing_package": str,
        "costing_basis": str,
        "cost_certified": bool,
        "required_economic_inputs": list[str],
        "public_support_error": str,
    },
    "list_downstream_handoff_profiles": {
        "profiles": JsonList,
        "total": int,
    },
    "prepare_downstream_handoff": {
        "target_engine": str,
        "target_profile": str,
        "session_preset": str,
        "property_package": str,
        "property_package_config": JsonDict,
        "state_payload": JsonDict,
        "completeness": JsonDict,
        "missing_or_estimated_species": list[str],
        "assumptions": list[str],
        "conversion_chain": JsonList,
        "source_model_type": str,
        "strict_contract": bool,
        "estimated_by_design": bool,
        "source": JsonDict,
        "missing_analytes": list[str],
        "type_mismatches": list[str],
        "unit_mismatches": list[str],
    },
    "seed_from_watertap_solution": {
        "session_id": str,
        "status": str,
        "model_type": str,
        "applied_streams": JsonList,
        "applied_units": JsonList,
        # Roadmap C1: mADM1 headspace (gas-phase) seed targets applied onto the
        # digester gas slice. Always present on success (empty list for mASM2d
        # payloads, which carry no gas phase).
        "applied_gas_units": list[str],
        "skipped_streams": JsonList,
        "skipped_units": JsonList,
        # Roadmap C1: non-fatal gas-seed resolution warnings (e.g. no mADM1
        # digester to receive the headspace seed). Always present on success.
        "gas_seed_warnings": list[str],
        "available_streams": list[str],
        "available_units": list[str],
        "missing_streams": list[str],
        "missing_units": list[str],
        "seed_report": JsonDict,
        "hint": str,
        "next_step": str,
    },
    "reconcile_state": {
        # Roadmap C2 / B3: ``qsdsan_state_reconciliation/v1`` payload produced by
        # utils.state_reconciliation.reconcile_restored_state (schema_version,
        # status, dry_run, options, quantities, state_update_summary, quality),
        # augmented by the cli ``flowsheet reconcile`` command (session_id,
        # checkpoint_time_days, checkpoint_restore, observability_map,
        # pre_observations, checkpoint_persisted) and the credibility stamp the
        # reconcile_state tool sets via setdefault.
        "schema_version": str,
        "status": str,
        "method": str,
        "conservation_report": JsonDict,
        "dry_run": bool,
        "options": JsonDict,
        "quantities": JsonList,
        "state_update_summary": JsonDict,
        "quality": JsonDict,
        "decision_readiness": JsonDict,
        "session_id": str,
        "checkpoint_time_days": float | None,
        "checkpoint_restore": JsonDict,
        "observability_map": JsonDict,
        "pre_observations": JsonDict,
        "checkpoint_persisted": bool,
        "credibility": JsonDict,
    },
    "get_model_components": {
        "model_type": str,
        "status": str,
        "description": str,
        "n_components": int,
        "concentration_units": str,
        "components": JsonList,
        "handoff_only": JsonList,
        "categories": JsonDict,
        "note": str,
    },
    "validate_flowsheet": {
        "session_id": str,
        "is_valid": bool,
        "n_streams": int,
        "n_units": int,
        "n_connections": int,
        "errors": list[str],
        "warnings": list[str],
        "detected_recycles": JsonList,
        "engineering_readiness": bool,
        "missing_engineering_inputs": JsonDict,
        "applied_engineering_defaults": JsonDict,
        "internal_hygiene_summary": JsonDict,
    },
    "suggest_recycles": {
        "session_id": str,
        "detected_cycles": JsonList,
        "n_cycles_detected": int,
        "suggestions": JsonList,
        "topology": JsonDict,
    },
    "get_artifact": {
        "job_id": str,
        "artifact_type": str,
        "format": str,
        "encoding": str,
        "content": Any,
        "path": str,
        "job_dir": str,
        "searched": JsonList,
        "size_bytes": int,
    },
    "enable_costing": {
        "session_id": str,
        "status": str,
        "message": str,
        "costing": JsonDict,
        "enabled": bool,
        "tea_profile": str,
        "economic_assumptions": JsonDict,
        "costed_units": JsonList,
    },
    "add_unit_costing": {
        "session_id": str,
        "unit_id": str,
        "status": str,
        "message": str,
        "costing": JsonDict,
        "unit_type": str,
        "costing_supported": bool,
        "costing_package": str,
        "costing_basis": str,
        "costed_units": JsonList,
    },
    "list_costed_units": {
        "session_id": str,
        "units": JsonList,
        "total": int,
        "enabled": bool,
        "tea_profile": str,
        "economic_assumptions": JsonDict,
        "costed_units": JsonList,
    },
    "get_costing": {
        "session_id": str,
        "job_id": str,
        "status": str,
        "costing": JsonDict,
        "summary": JsonDict,
    },
    "simulate_madm1_serial_mineral_history": {
        "status": str,
        "solver_converged": bool,
        "chemistry_output_available": bool,
        "kernel_id": str,
        "kernel_hash": str,
        "train_id": str,
        "hydraulic_flow_m3_d": float,
        "elapsed_days": float,
        "checkpoint_time_days": float,
        "resumed_from_checkpoint": bool,
        "starting_checkpoint_time_days": float,
        "requested_horizon_days": float,
        "time_step_days": float,
        "cell_count": int,
        "zone_states_mol_m3": JsonDict,
        "terminal_state_mol_m3": JsonDict,
        "mbr_permeate_state_mol_m3": JsonDict,
        "p_metrics": JsonDict,
        "oxide_history_inventory_mol_fe_m3": JsonDict,
        "surface_p_inventory_mol_p_m3": JsonDict,
        "iron_sulfide_inventory_mol_m3": JsonDict,
        "fixed_background_charge_eq_m3": float,
        "charge_diagnostics": JsonDict,
        "ph_charge_solve_passed": bool,
        "local_reaction_balance_passed": bool,
        "whole_train_balance": JsonDict,
        "mass_balance_passed": bool,
        "implicit_reservoir_exchange_mol": JsonDict,
        "cumulative_wasting_mol": JsonDict,
        "extent_limiter_activation_count": int,
        "forced_host_capacity_release_count": int,
        "terminal_change_rate_mol_m3_d": float,
        "trajectory": JsonList,
        "last_rate_reports": JsonDict,
        "initial_inventory_id": str,
        "final_inventory_id": str,
        "serial_checkpoint": JsonDict,
        "outlet_activities": JsonDict,
        "phase_saturation_index": JsonDict,
        "phreeqc_endpoint_translation": JsonDict,
        "phreeqc_audit_request": JsonDict,
        "phreeqc_audit_dispatch": JsonDict,
        "mechanism_interpretation": JsonDict,
        "gas_coupling_class": str,
        "mechanism_identified": bool,
        "recommendation_eligible": bool,
        "recommendation_blockers": JsonList,
        "qsdsan_coupling": JsonDict,
        "native_dissolved_feedback_candidate": JsonDict,
    },
    # --- liquid-train digester ops twin -------------------------------------
    # These tools shipped without output contracts, so the registry-completeness
    # test has been red since d3580d4d. An ops twin is exactly where a loose
    # envelope hurts most: `physicality_ok`, `steady_state_ok`, `reseed_required`
    # and `change_class` are safety interlocks, and a caller that reads a
    # misspelled or absent key gets a falsy value and proceeds.
    "commission_liquid_digester": {
        "checkpoint": JsonDict,
        "status": str,
        "time_days": float,
        "labbases": JsonDict,
        "diagnostics": JsonDict,
        "physicality_ok": bool,
        "physicality_failures": JsonList,
        "steady_state_ok": bool,
        "fixed_point_assessment": JsonDict,
    },
    "advance_liquid_digester": {
        "checkpoint": JsonDict,
        "time_days": float,
        "labbases": JsonDict,
        "diagnostics": JsonDict,
        # top-level convenience copy of labbases.methanogen_biomass_mg_L — the nightly
        # workflow reads it for the washout trend + checkpoint biomass persist. Omitting
        # it here made every FULL success payload fail the additionalProperties=false
        # gate (latent while the checkpoints were fingerprint-stale, since the guard
        # path's short payload validated fine — surfaced by the 2026-08-26 C1 check).
        "methanogen_biomass_mg_L": float,
        "reseed_required": bool,
        "physicality_ok": bool,
        "physicality_failures": JsonList,
        "errors": JsonList,
        "shared_serial_chemistry_active": bool,
        "serial_chemistry": JsonDict,
        "shared_serial_dissolved_feedback_applied": bool,
        "shared_serial_chemistry_reseed_required": bool,
        "shared_serial_coupling": JsonDict,
        "recommendation_eligible": bool,
        "recommendation_blockers": JsonList,
    },
    "evaluate_liquid_digester": {
        "labbases": JsonDict,
        "diagnostics": JsonDict,
        "methanogen_biomass_mg_L": float,   # top-level copy — see advance_liquid_digester
        "model_time_days": float,
        "reseed_required": bool,
        "physicality_ok": bool,
        "physicality_failures": JsonList,
        "evaluated_as_of": bool,
        "errors": JsonList,
    },
    "whatif_liquid_digester": {
        "scenarios": JsonList,
        "baseline_label": str,
        "elapsed_days": float,
        "basis": str,
        "recommendation_eligible": bool,
        "decision_readiness": JsonDict,
    },
    "screen_liquid_digester_fecl3_dose_envelope": {
        "schema_version": str,
        "calculation_class": str,
        "dose_semantics": str,
        "dose_grid": JsonList,
        "realization_count": int,
        "evaluation_rows": JsonList,
        "branch_count": int,
        "branches": JsonList,
        "selected_branch_id": Any,
        "selected_screening_branch_id": Any,
        "recommendation_eligible": bool,
        "recommendation_blockers": JsonList,
        "random_seed": int,
        "input_hash": str,
        "model_hash": str,
        "starting_checkpoint_hash": str,
        "target": JsonDict,
        "operating_constraints": JsonDict,
        "uncertainty_parameter_contract": JsonList,
        "basis": str,
    },
    "migrate_liquid_digester": {
        "change_class": str,
        "migrated": bool,
        "needs_reseed": bool,
        "checkpoint": JsonDict,
        "time_days": float,
        "labbases": JsonDict,
        "diagnostics": JsonDict,
        "methanogen_biomass_mg_L": float,   # top-level copy — see advance_liquid_digester
        "physicality_ok": bool,
        "physicality_failures": JsonList,
        "errors": JsonList,
    },
    "calibrate_liquid_digester": {
        "parameter_set": JsonDict,
        "objective": JsonDict,
        "residuals": JsonDict,
        "stages": JsonList,
        "identifiability": JsonDict,
        "n_days": int,
        "method": str,
    },
}


# Fields listed here are conditional success payload members, not required on
# every successful tool execution.  All other declared success fields are
# required in the generated ResultModel so ``model_validate({})`` cannot pass.
OPTIONAL_TOOL_OUTPUT_FIELDS: dict[str, set[str]] = {
    "get_job_status": {
        "progress",  # Present only after stdout progress can be parsed.
        "completed_at",  # Present only for completed/terminated jobs.
        "total_time_seconds",  # Present only after completion.
        "exit_code",  # Present only for failed/completed subprocesses.
        "timeout_seconds",  # Present only for timeout-aware jobs.
        "time_remaining_seconds",  # Present only for running timeout-aware jobs.
    },
    "get_job_results": {
        "results",  # Omitted when no result JSON file was produced.
        "result_file",  # Omitted when no result JSON file was found.
        "warning",  # Present only for completed jobs with missing result JSON.
        "time_series_available",  # Present only when time_series was filtered.
        "time_series_note",  # Present only when time_series was filtered.
        "artifact_persistence",  # Present only when result persistence runs.
        "result_status",  # Present only when the process completed but its result is invalid.
        "result_valid",  # Present only with an explicit invalid-result classification.
        "result_validity_blockers",  # Present only with an explicit invalid-result classification.
    },
    "convert_state": {
        "job_id",  # Omitted for same-model no-op conversions.
        "conversion",  # Omitted for same-model no-op conversions.
        "conversion_method",  # Omitted for same-model no-op conversions.
        "state",  # Present only for same-model no-op conversions.
        "supported",  # Present only on unsupported-conversion errors.
    },
    "list_jobs": {
        "filter",  # None when no status filter was requested.
    },
    "get_timeseries_data": {
        "status",  # Omitted for not-found and parse-error responses.
        "time_series",  # Omitted when time-series data is unavailable.
        "result_file",  # Omitted when time-series data is unavailable.
        "note",  # Present only for no-timeseries guidance responses.
    },
    "create_stream": {
        "message",  # FlowsheetSessionManager returns compact mutation payloads.
        "session_id",
        "stream",
        "warnings",
    },
    "update_stream": {
        "message",
        "session_id",
        "stream",
        "warnings",
    },
    "delete_stream": {
        "message",
        "session_id",
        "success",
    },
    "create_unit": {
        "message",
        "session_id",
        "stream_topology",  # Present only when caller provided topology metadata.
    },
    "update_unit": {
        "message",
        "session_id",
        "unit",
    },
    "set_unit_engineering_inputs": {
        "message",
        "session_id",
        "params",
        "aux_config",
        "source_spec",
        "applied_defaults",
    },
    "delete_unit": {
        "message",
        "session_id",
        "success",
    },
    "delete_connection": {
        "message",
        "session_id",
        "connection_id",
        "success",
    },
    "generate_session_inoculum": {
        "message",
        "status",
        "inoculum",
        "created_streams",
        "warnings",
    },
    "get_flowsheet_session": {
        "costing",  # Present only after costing is configured.
        "startup_metadata",  # Present only after startup/inoculum metadata exists.
    },
    "list_flowsheet_sessions": {
        "filter",  # None when no status filter was requested.
    },
    "clone_session": {
        "session_id",  # Manager returns source_session_id/new_session_id instead.
        "message",
        "cloned_session_id",  # Legacy alias may be omitted by newer handlers.
        "new_session_id",  # New alias may be omitted by older handlers.
    },
    "get_unit_spec": {
        "public_support_error",  # None for publicly supported units.
    },
    "prepare_downstream_handoff": {
        "missing_analytes",  # Populated only on strict handoff validation errors.
        "type_mismatches",  # Populated only on strict handoff validation errors.
        "unit_mismatches",  # Populated only on strict handoff validation errors.
    },
    "seed_from_watertap_solution": {
        "available_streams",  # Present only when strict stream matching fails.
        "available_units",  # Present only when strict unit matching fails.
        "missing_streams",  # Present only when strict stream matching fails.
        "missing_units",  # Present only when strict unit matching fails.
        "hint",  # Present only when strict matching fails.
    },
    "get_model_components": {
        "status",  # Omitted for upstream-QSDsan component-list fallback.
        "description",  # Omitted for upstream-QSDsan component-list fallback.
        "concentration_units",  # Omitted for upstream-QSDsan component-list fallback.
        "components",  # Omitted for upstream-QSDsan component-list fallback.
        "categories",  # Omitted for upstream-QSDsan component-list fallback.
        "note",  # Present only for upstream fallback or handoff-only explanation.
    },
    "get_artifact": {
        "encoding",  # Present only for binary/base64 artifacts.
        "job_dir",  # Present only when reporting missing artifact search context.
        "searched",  # Present only when reporting missing artifact search context.
        "size_bytes",  # Omitted for parsed JSON artifacts.
    },
    "enable_costing": {
        "status",
        "message",
        "costing",
    },
    "add_unit_costing": {
        "status",
        "message",
        "costing",
    },
    "get_costing": {
        "session_id",  # Omitted for job_id-driven costing lookups.
        "job_id",  # Omitted for pre-validation errors.
    },
    # The twin's guard paths return a short payload instead of raising, so most
    # fields are conditional. What stays REQUIRED is the interlock the caller must
    # branch on -- `reseed_required` for advance/evaluate, `change_class` and
    # `migrated` for migrate -- because those are exactly the keys whose absence
    # would otherwise read as "no, carry on".
    "advance_liquid_digester": {
        "checkpoint", "time_days", "labbases", "diagnostics",
        "methanogen_biomass_mg_L",  # labbases.get(...) — null when unavailable; absent on guard paths.
        "physicality_ok", "physicality_failures",
        "shared_serial_chemistry_active", "serial_chemistry",
        "shared_serial_dissolved_feedback_applied",
        "shared_serial_chemistry_reseed_required",
        "shared_serial_coupling", "recommendation_eligible",
        "recommendation_blockers",
        "errors",  # Present only on the reseed-required guard path.
    },
    "evaluate_liquid_digester": {
        "labbases", "diagnostics", "model_time_days",
        "methanogen_biomass_mg_L",  # labbases.get(...) — null when unavailable; absent on guard paths.
        "physicality_ok", "physicality_failures", "evaluated_as_of",
        "errors",  # Present only on the reseed-required guard path.
    },
    "migrate_liquid_digester": {
        "checkpoint", "time_days", "labbases", "diagnostics",
        "methanogen_biomass_mg_L",  # labbases.get(...) — null when unavailable; absent on guard paths.
        "physicality_ok", "physicality_failures",
        "errors",  # Present on the hard/identical refusal paths.
    },
}


# The pure reader returns the same job evidence, without a write receipt.
TOOL_OUTPUT_FIELD_TYPES["read_job_results"] = {
    name: field_type
    for name, field_type in TOOL_OUTPUT_FIELD_TYPES["get_job_results"].items()
    if name != "artifact_persistence"
}
TOOL_OUTPUT_FIELD_TYPES["read_job_results"].update(
    results_normalized=bool,
    results_wrapper_key=str,
)
OPTIONAL_TOOL_OUTPUT_FIELDS["read_job_results"] = (
    OPTIONAL_TOOL_OUTPUT_FIELDS["get_job_results"] - {"artifact_persistence"}
) | {"results_normalized", "results_wrapper_key"}


TOOL_SUCCESS_MODELS: dict[str, type[ToolOutputBase]] = {
    tool_name: _success_model(
        tool_name,
        "".join(part.capitalize() for part in tool_name.split("_")) + "ResultSuccess",
        fields,
    )
    for tool_name, fields in TOOL_OUTPUT_FIELD_TYPES.items()
}

TOOL_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    tool_name: _result_model(
        tool_name,
        "".join(part.capitalize() for part in tool_name.split("_")) + "Result",
        fields,
    )
    for tool_name, fields in TOOL_OUTPUT_FIELD_TYPES.items()
}

TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    tool_name: model.model_json_schema()
    for tool_name, model in TOOL_OUTPUT_MODELS.items()
}


def output_schema_for_tool(tool_name: str) -> dict[str, Any]:
    """Return the registered Pydantic JSON schema for a public MCP tool."""

    try:
        return TOOL_OUTPUT_SCHEMAS[tool_name]
    except KeyError as exc:
        valid = ", ".join(sorted(TOOL_OUTPUT_SCHEMAS))
        raise KeyError(f"No output schema registered for tool {tool_name!r}. Valid: {valid}") from exc


__all__ = [
    "OPTIONAL_TOOL_OUTPUT_FIELDS",
    "TOOL_OUTPUT_FIELD_TYPES",
    "TOOL_OUTPUT_MODELS",
    "TOOL_SUCCESS_MODELS",
    "TOOL_OUTPUT_SCHEMAS",
    "ToolOutputBase",
    "output_schema_for_tool",
]
