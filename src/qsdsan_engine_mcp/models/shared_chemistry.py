"""QSDsan numeric adapter for the shared Fe-S-P chemistry definition."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Mapping

from qsdsan_engine_mcp.core.dynamic_solver_policy import MADM1_BDF_ATOL
from puran_mcp_support.chemistry import advance_cstr_inventory, advance_reduced_chemistry_cstr, constrained_reaction_step, evaluate, evaluate_reduced_chemistry_rates, reduced_chemistry_cstr_rhs, simulate_serial_chemistry_train
from puran_mcp_support.schema import MADM1_FECL3_FE_S_P_KERNEL, AssumptionLedger, ChemistryKernelDefinition, SerialChemistryTrainDefinition, mechanism_interpretation_from_ledger, validate_kernel_definition

from .native_chemistry_migration import qsdsan_madm1_native_migration_report
from .thermodynamics import DAVIES_ACTIVITY_DOMAIN


@dataclass(frozen=True)
class QSDsanChemistryAdapter:
    kernel: ChemistryKernelDefinition = MADM1_FECL3_FE_S_P_KERNEL

    @property
    def expression_hash(self) -> str:
        return self.kernel.sha256

    @property
    def recommendation_capable(self) -> bool:
        return self.kernel.recommendation_capable

    def validate(self) -> None:
        validate_kernel_definition(self.kernel)

    def native_to_chemical_totals(self, native_state: Mapping[str, float], *, basis_map_id: str) -> dict[str, float]:
        mapping = next((row for row in self.kernel.basis_maps if row.map_id == basis_map_id), None)
        if mapping is None:
            raise KeyError(f"kernel {self.kernel.kernel_id!r} has no basis map {basis_map_id!r}")
        return mapping.to_chemical_totals(native_state)

    def phreeqc_endpoint_totals(
        self,
        native_state: Mapping[str, float],
        *,
        temperature_c: float | None = None,
        ph: float | None = None,
        gas_boundary_mode: str = "unsupplied",
        gas_boundary: Mapping[str, float] | None = None,
    ) -> dict[str, object]:
        """Translate a full live mADM1 dissolved state for PHREEQC auditing.

        Native elemental mass concentrations (kg element/m3) are converted to
        mol/L while preserving Fe and S oxidation-state totals. The P total is
        specifically the modeled soluble inorganic-P state; it is not plant TP.
        ``analytical_totals_ready`` only certifies the basis translation. A
        runnable endpoint audit additionally requires pH, temperature, and an
        explicit gas-boundary declaration.
        """

        required = ("S_IP", "S_SO4", "S_IS", "S_Fe3", "S_Fe2", "S_IC", "S_Cl")
        missing = [component_id for component_id in required if component_id not in native_state]
        if missing:
            raise KeyError(f"PHREEQC endpoint translation missing native states {missing}")
        selected = {component_id: float(native_state[component_id]) for component_id in required}
        invalid = sorted(
            component_id for component_id, value in selected.items() if not math.isfinite(value) or value < 0
        )
        if invalid:
            raise ValueError(f"PHREEQC endpoint translation requires finite nonnegative states; invalid={invalid}")
        totals = self.native_to_chemical_totals(
            selected,
            basis_map_id="qsdsan_madm1_native_to_fe_s_p_totals_v1",
        )
        mol_l = {key: value * 1e-3 for key, value in totals.items()}
        phreeqc_totals = {
            "P": mol_l["P_total_mol_m3"],
            "S(6)": mol_l["sulfate_total_mol_m3"],
            "S(-2)": mol_l["sulfide_total_mol_m3"],
            "Fe(3)": mol_l["Fe_III_total_mol_m3"],
            "Fe(2)": mol_l["Fe_II_total_mol_m3"],
            "C(4)": mol_l["TIC_total_mol_m3"],
            "Cl": mol_l["chloride_total_mol_m3"],
        }

        # Keep kinetic solid history separate from the analytical aqueous
        # totals. A PHREEQC audit may use these as explicitly declared initial
        # phase/surface inventories, but must not silently equilibrate them as
        # if the dynamic model had no path history.
        optional_solids = (
            "X_FeS",
            "X_Mackinawite",
            "X_Fe3PO42",
            "X_Siderite",
            "X_HFO_H",
            "X_HFO_L",
            "X_HFO_old",
            "X_HFO_HP",
            "X_HFO_LP",
            "X_HFO_HP_old",
            "X_HFO_LP_old",
        )
        solid_values = {component_id: float(native_state.get(component_id, 0.0)) for component_id in optional_solids}
        invalid_solids = sorted(
            component_id for component_id, value in solid_values.items() if not math.isfinite(value) or value < 0
        )
        if invalid_solids:
            raise ValueError(
                f"PHREEQC endpoint solid translation requires finite nonnegative states; invalid={invalid_solids}"
            )

        context_invalid: list[str] = []
        if temperature_c is not None and (not math.isfinite(float(temperature_c)) or float(temperature_c) <= -273.15):
            context_invalid.append("temperature_c")
        if ph is not None and (not math.isfinite(float(ph)) or not 0.0 <= float(ph) <= 14.0):
            context_invalid.append("pH")
        if context_invalid:
            raise ValueError("PHREEQC endpoint context is invalid for " + ", ".join(context_invalid))
        normalized_gas_mode = str(gas_boundary_mode).strip()
        allowed_gas_modes = {
            "unsupplied",
            "closed",
            "fixed_partial_pressure",
            "finite_headspace",
        }
        if normalized_gas_mode not in allowed_gas_modes:
            raise ValueError("PHREEQC endpoint gas_boundary_mode is invalid")
        gas = {str(species): float(value) for species, value in sorted((gas_boundary or {}).items())}
        invalid_gas = sorted(species for species, value in gas.items() if not math.isfinite(value) or value < 0)
        if invalid_gas:
            raise ValueError(f"PHREEQC endpoint gas values must be finite and nonnegative; invalid={invalid_gas}")
        if normalized_gas_mode == "closed" and gas:
            raise ValueError("closed PHREEQC gas boundary must not specify gas values")
        if normalized_gas_mode in {"fixed_partial_pressure", "finite_headspace"} and not gas:
            raise ValueError(f"{normalized_gas_mode} PHREEQC gas boundary requires gas values")

        molar_mass_kg_mol = {
            "X_FeS": 0.055845 + 0.032065,
            "X_Mackinawite": 0.055845 + 0.032065,
            "X_Fe3PO42": (3.0 * 0.055845 + 2.0 * 0.030973761998 + 8.0 * 0.015999),
            "X_Siderite": 0.055845 + 0.0120107 + 3.0 * 0.015999,
        }
        phase_inventory_mol_l = {
            phase_id: solid_values[component_id] / molar_mass_kg_mol[component_id] * 1e-3
            for phase_id, component_id in (
                ("FeS_amorphous", "X_FeS"),
                ("Mackinawite", "X_Mackinawite"),
                ("Vivianite", "X_Fe3PO42"),
                ("Siderite", "X_Siderite"),
            )
        }
        hfo_history_mol_fe_l = {
            history_id: solid_values[component_id] / 0.055845 * 1e-3
            for history_id, component_id in (
                ("fresh_high_sites_unbound", "X_HFO_H"),
                ("fresh_low_sites_unbound", "X_HFO_L"),
                ("aged_unbound", "X_HFO_old"),
                ("fresh_high_sites_p_bound", "X_HFO_HP"),
                ("fresh_low_sites_p_bound", "X_HFO_LP"),
                ("aged_high_sites_p_bound", "X_HFO_HP_old"),
                ("aged_low_sites_p_bound", "X_HFO_LP_old"),
            )
        }
        audit_readiness_blockers: list[str] = []
        if temperature_c is None:
            audit_readiness_blockers.append("temperature_not_supplied")
        if ph is None:
            audit_readiness_blockers.append("pH_not_supplied")
        if normalized_gas_mode == "unsupplied":
            audit_readiness_blockers.append("gas_boundary_not_supplied")

        mechanism_interpretation = mechanism_interpretation_from_ledger(
            {
                "surface_dataset_id": (
                    self.kernel.surfaces[0].surface_dataset_id
                    if self.kernel.surfaces
                    else None
                ),
                "surface_parameter_interpretation": (
                    "legacy_dynamic_kinetic_surrogate"
                ),
                "oxide_history_class": "mixed_dynamic_unresolved",
                "oxide_history_id": (
                    "fresh_aged_reduced_pools_without_reoxidized_history_v1"
                ),
                "oxide_formation_ph": None,
                "oxide_formation_ph_basis": "not_dynamically_resolved",
                "active_metal_fraction_basis": "not_identified",
                "effective_active_fe_fraction": None,
                "parameter_contact": None,
                "parameter_contact_evidence_id": None,
                "mechanism_identified": False,
                "mechanism_evidence_id": None,
                "extrapolation_status": "not_assessed",
                "extrapolation_reasons": (),
            }
        )
        return {
            "kernel_id": self.kernel.kernel_id,
            "kernel_hash": self.kernel.sha256,
            "totals_mol_l": phreeqc_totals,
            "kinetic_phase_inventory_mol_l": phase_inventory_mol_l,
            "hfo_history_inventory_mol_fe_l": hfo_history_mol_fe_l,
            "p_total_basis": "mADM1_S_IP_soluble_inorganic_P_not_plant_TP",
            "temperature_c": (None if temperature_c is None else float(temperature_c)),
            "ph": None if ph is None else float(ph),
            "gas_boundary_mode": normalized_gas_mode,
            "gas_boundary": gas,
            "analytical_totals_ready": True,
            "translation_ready": True,
            "audit_ready": not audit_readiness_blockers,
            "audit_readiness_blockers": audit_readiness_blockers,
            "mechanism_interpretation": mechanism_interpretation,
            **{
                key: mechanism_interpretation[key]
                for key in (
                    "mechanism_identified",
                    "surface_dataset_id",
                    "oxide_history_id",
                    "formation_ph_basis",
                    "effective_active_fe_fraction",
                    "parameter_contact",
                    "extrapolation_status",
                )
            },
            "mapping_limitations": [
                "organic ligands and SMP are not represented",
                "solid inventories are translated separately and retain their kinetic-history labels",
                "the live model has no explicit FeS-reoxidized-HFO inventory",
                "gas boundary, pH, temperature, and water mass must be supplied separately",
            ],
        }

    def audit_native_state(
        self,
        native_state: Mapping[str, float],
        *,
        components: Any | None = None,
        ionic_strength_molal: float | None = None,
        temperature_c: float | None = None,
        species_coverage_fraction: float | None = None,
        hfo_ligand_diagnostics: Mapping[str, Any] | None = None,
        mbr_retention_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Return a fail-closed inventory/provenance audit for a live mADM1 state.

        QSDsan keeps its native COD/element/mineral mass bases.  This method does
        not relabel those states as molar; it applies the declared basis map for
        dissolved totals and uses QSDsan component metadata for the whole-state
        P and S inventories.  Iron-bearing solid pools are reported explicitly
        because their native bases are mixed (HFO pools are kg Fe/m3 while
        mineral pools are kg mineral/m3).
        """

        values = {key: float(value) for key, value in native_state.items()}
        exact_negatives = sorted(key for key, value in values.items() if value < 0.0)
        negatives = sorted(
            key for key, value in values.items() if value < -MADM1_BDF_ATOL
        )
        numerical_undershoots = sorted(
            key
            for key, value in values.items()
            if -MADM1_BDF_ATOL <= value < 0.0
        )
        nonfinite = sorted(key for key, value in values.items() if not math.isfinite(value))
        if not nonfinite:
            canonical_state = json.dumps(
                values,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            input_hash = hashlib.sha256(canonical_state.encode("utf-8")).hexdigest()
        else:
            input_hash = "unavailable_nonfinite_state"
        dissolved_totals = self.native_to_chemical_totals(
            values,
            basis_map_id="qsdsan_madm1_native_to_fe_s_p_totals_v1",
        )

        def value(component_id: str) -> float:
            return values.get(component_id, 0.0)

        phase_inventory = {
            "FeS_amorphous": value("X_FeS"),
            "Mackinawite": value("X_Mackinawite"),
            "Vivianite": value("X_Fe3PO42"),
            "Siderite": value("X_Siderite"),
        }
        hfo_history = {
            "fresh_high_sites_unbound": value("X_HFO_H"),
            "fresh_low_sites_unbound": value("X_HFO_L"),
            "aged_unbound": value("X_HFO_old"),
            "fresh_high_sites_p_bound": value("X_HFO_HP"),
            "fresh_low_sites_p_bound": value("X_HFO_LP"),
            "aged_high_sites_p_bound": value("X_HFO_HP_old"),
            "aged_low_sites_p_bound": value("X_HFO_LP_old"),
        }
        hfo_host_transformation = {
            "fresh_unbound_hfo_reduction_represented": True,
            "fresh_p_bound_hfo_direct_reduction_or_sulfidation_represented": True,
            "aged_hfo_reduction_or_sulfidation_represented": True,
            "fresh_p_bound_desorption_to_unbound_hfo_represented": True,
            "aged_p_bound_phosphorus_release_represented": True,
            "fes_reoxidation_to_hfo_represented": False,
            "dynamic_anaerobic_host_site_destruction_complete": True,
            "dynamic_host_site_destruction_complete": False,
            "classification": "anaerobic_host_destruction_complete_reoxidation_missing",
        }

        element_inventory: dict[str, float | None] = {"P": None, "S": None}
        if components is not None:
            p_total = 0.0
            s_total = 0.0
            for component_id, concentration in values.items():
                if component_id not in getattr(components, "IDs", ()):
                    continue
                component = components[component_id]
                p_total += concentration * float(getattr(component, "i_P", 0.0) or 0.0)
                if component_id in {"S_SO4", "S_IS", "S_S0"}:
                    s_fraction = 1.0
                elif component_id in {"X_FeS", "X_Mackinawite"}:
                    s_fraction = 32.065 / (55.845 + 32.065)
                else:
                    # QSDsan currently has no general i_S component metadata;
                    # retain the fallback for future extensions.
                    s_fraction = float(getattr(component, "i_S", 0.0) or 0.0)
                s_total += concentration * s_fraction
            element_inventory["P"] = p_total
            element_inventory["S"] = s_total

        # Native Fe pools use two bases. Dissolved and HFO states are kg Fe/m3;
        # FeS/vivianite/siderite states are kg dry mineral/m3.
        fe_mw = 55.845
        s_mw = 32.065
        p_mw = 30.973761998
        c_mw = 12.0107
        mineral_fe = (
            (value("X_FeS") + value("X_Mackinawite")) * fe_mw / (fe_mw + s_mw)
            + value("X_Fe3PO42") * (3.0 * fe_mw) / (3.0 * fe_mw + 2.0 * p_mw + 8.0 * 15.999)
            + value("X_Siderite") * fe_mw / (fe_mw + c_mw + 3.0 * 15.999)
        )
        element_inventory["Fe"] = value("S_Fe2") + value("S_Fe3") + sum(hfo_history.values()) + mineral_fe

        activity_report = None
        if ionic_strength_molal is not None and temperature_c is not None and species_coverage_fraction is not None:
            activity_report = self.activity_domain_report(
                ionic_strength_molal=ionic_strength_molal,
                temperature_c=temperature_c,
                species_coverage_fraction=species_coverage_fraction,
            )

        ligand = dict(hfo_ligand_diagnostics or {})
        retention_profile = dict(mbr_retention_profile or {})
        if retention_profile.get("mode") == "explicit_srt_wasting":
            retention_profile_id = "qsdsan_madm1_explicit_srt_wasting_v1"
        elif retention_profile.get("mode") == "legacy_fraction_retain":
            retention_profile_id = "qsdsan_madm1_legacy_fraction_retain_v1"
        else:
            retention_profile_id = "not_configured_or_not_attached"
        ligand_configured = bool(ligand.get("ligand_competition_configured"))
        ligand_active = bool(ligand.get("ligand_competition_active"))
        ligand_model_id = str(ligand.get("ligand_model_id") or self.kernel.ligand_model_id)
        smp_or_doc_basis = str(ligand.get("smp_or_doc_basis") or "not_supplied")
        ligand_calibration_id = ligand.get("ligand_calibration_id")
        ligand_limitations = []
        if not ligand_configured:
            ligand_limitations.append("no calibrated SMP or organic-ligand competition model")
        elif not ligand_active:
            ligand_limitations.append(
                "calibrated SMP/DOC competition contract was configured but not active in the evaluated HFO rates"
            )
        else:
            ligand_limitations.append(
                "SMP/DOC competition is a fixed conditional calibration surrogate; "
                "dynamic ligand and Fe-organic complexation states are not represented"
            )

        assumption_ledger_record = AssumptionLedger(
            target_variable="dynamic_reactor_P_inventory_diagnostic",
            thermodynamic_dataset_id=self.kernel.thermodynamic_dataset_id,
            database_hash="unresolved_runtime_database_hash",
            phase_definition_set_id=self.kernel.phase_definition_set_id,
            activity_model_id=self.kernel.activity_model_id,
            redox_method=self.kernel.redox_reaction_set_id,
            sulfide_source="dynamic_madm1_sulfur_inventory",
            iron_sulfide_phase_case="amorphous_and_mackinawite_dynamic",
            chemistry_treatment="dynamic_kinetic_inventory_model",
            solids_inventory_id="live_qsdsan_state",
            mbr_retention_profile_id=retention_profile_id,
            calibration_domain_id=(str(ligand_calibration_id) if ligand_calibration_id else None),
            ligand_model_id=ligand_model_id,
            model_commit=(os.environ.get("PURANOS_MODEL_COMMIT") or os.environ.get("GIT_COMMIT_SHA") or "unknown"),
            input_hash=input_hash,
            # The live mADM1 HFO pools still use the legacy high/low dynamic
            # capacity model; do not mislabel them as the shared D-M surface.
            surface_dataset_id="qsdsan_madm1_legacy_two_capacity_hfo_uncommissioned",
            surface_parameter_interpretation="legacy_dynamic_kinetic_surrogate",
            oxide_history_class="mixed_dynamic_unresolved",
            oxide_history_id="fresh_aged_reduced_pools_without_reoxidized_history_v1",
            oxide_formation_ph=None,
            oxide_formation_ph_basis="not_dynamically_resolved",
            active_metal_fraction_basis="not_identified",
            effective_active_fe_fraction=None,
            parameter_contact=None,
            mechanism_identified=False,
            smp_or_doc_basis=smp_or_doc_basis,
            extrapolation_status="not_assessed",
            extrapolation_reasons=(
                "HFO surface capacity, aging, and reoxidation are uncalibrated",
                *(() if ligand_active else ("SMP/DOC ligand competition is absent or inactive",)),
            ),
        )
        assumption_ledger = assumption_ledger_record.public_dict()
        mechanism_interpretation = mechanism_interpretation_from_ledger(
            assumption_ledger_record
        )

        return {
            "kernel_id": self.kernel.kernel_id,
            "kernel_hash": self.expression_hash,
            "commissioning_status": self.kernel.commissioning_status,
            "recommendation_capable": self.recommendation_capable,
            "recommendation_eligible": False,
            "native_basis": "QSDsan mixed component basis; not relabeled as molar",
            "dissolved_chemical_totals_mol_m3": dissolved_totals,
            "element_inventory_kg_m3": element_inventory,
            "phase_inventory_kg_m3_dry_basis": phase_inventory,
            "hfo_history_inventory_kg_fe_m3": hfo_history,
            "hfo_host_transformation": hfo_host_transformation,
            "mechanism_interpretation": mechanism_interpretation,
            "mbr_retention_profile": retention_profile,
            "hfo_ligand_competition": {
                "configured": ligand_configured,
                "active": ligand_active,
                "factor": ligand.get("ligand_competition_factor"),
                "model_id": ligand_model_id,
                "calibration_id": ligand_calibration_id,
                "calibration_source_kind": ligand.get("ligand_calibration_source_kind"),
                "matrix_scope": ligand.get("ligand_matrix_scope"),
                "observation_id": ligand.get("smp_or_doc_observation_id"),
                "observation_basis": smp_or_doc_basis,
                "observed_smp_or_doc_mg_C_L": ligand.get("observed_smp_or_doc_mg_C_L"),
                "validity_envelope": ligand.get("ligand_validity_envelope"),
                "treatment": ("fixed_conditional_calibration_surrogate" if ligand_active else "no_active_rate_effect"),
            },
            "negative_state_components": negatives,
            "numerical_undershoot_components": numerical_undershoots,
            "state_negativity_tolerance_kg_m3": MADM1_BDF_ATOL,
            "nonfinite_state_components": nonfinite,
            "state_nonnegative": not exact_negatives,
            "state_nonnegative_within_solver_tolerance": not negatives,
            "state_finite": not nonfinite,
            "activity_model_domain": activity_report,
            "assumption_ledger": assumption_ledger,
            "recommendation_blockers": [
                "precipitation kinetics are not commissioned against independent data",
                "FeS reoxidation and serial aerobic HFO-formation history are not represented",
                "active delivered-iron fraction is not identified",
                "PHREEQC endpoint audit and plant holdout validation have not passed",
                *(
                    []
                    if ligand_active
                    else ["SMP/DOC ligand competition is absent or inactive"]
                ),
            ],
            "model_limitations": [
                "uncommissioned precipitation kinetics",
                "HFO reduction kinetics require site calibration",
                "P-bound and aged HFO destruction/release kinetics are screening priors, not calibrated",
                "FeS reoxidation to a history-specific HFO surface is not represented",
                *ligand_limitations,
                "active delivered-iron fraction is not identified",
                "PHREEQC endpoint audit and plant holdout validation are required",
            ],
        }

    def kinetic_rates(self, symbols: Mapping[str, float]) -> dict[str, float]:
        return {kinetic.kinetic_id: evaluate(kinetic.expression, symbols) for kinetic in self.kernel.kinetics}

    def canonical_dynamic_rate_report(
        self,
        state_mol_m3: Mapping[str, float],
        **chemistry_options: Any,
    ) -> dict[str, Any]:
        """Evaluate the shared reduced state with the dependency-light compiler.

        This is the numeric/QSDsan execution surface paired with WaterTAP's
        Pyomo compiler. It does not translate a native mADM1 state implicitly;
        callers must use an explicit basis map and supply the complete reduced
        state so COD-basis biological pseudo-components are never relabeled as
        molar species.
        """

        return evaluate_reduced_chemistry_rates(
            state_mol_m3,
            kernel=self.kernel,
            **chemistry_options,
        )

    def advance_canonical_dynamic_cstr(
        self,
        state_mol_m3: Mapping[str, float],
        *,
        inlet_mol_m3: Mapping[str, float],
        dose_source_mol_m3_d: Mapping[str, float] | None = None,
        dt_days: float,
        hydraulic_residence_time_days: float,
        solids_retention_time_days: float,
        **chemistry_options: Any,
    ):
        """Advance one positivity-preserving shared-chemistry dynamic step."""

        return advance_reduced_chemistry_cstr(
            state_mol_m3,
            inlet_mol_m3=inlet_mol_m3,
            dose_source_mol_m3_d=dose_source_mol_m3_d,
            dt_days=dt_days,
            hydraulic_residence_time_days=hydraulic_residence_time_days,
            solids_retention_time_days=solids_retention_time_days,
            kernel=self.kernel,
            **chemistry_options,
        )

    def canonical_dynamic_cstr_rhs(
        self,
        state_mol_m3: Mapping[str, float],
        *,
        inlet_mol_m3: Mapping[str, float],
        dose_source_mol_m3_d: Mapping[str, float] | None = None,
        hydraulic_residence_time_days: float,
        solids_retention_time_days: float,
        **chemistry_options: Any,
    ):
        """Render the continuous-time shared-chemistry RHS for QSDsan."""

        return reduced_chemistry_cstr_rhs(
            state_mol_m3,
            inlet_mol_m3=inlet_mol_m3,
            dose_source_mol_m3_d=dose_source_mol_m3_d,
            hydraulic_residence_time_days=hydraulic_residence_time_days,
            solids_retention_time_days=solids_retention_time_days,
            kernel=self.kernel,
            **chemistry_options,
        )

    def native_madm1_to_serial_feed(
        self,
        native_state: Mapping[str, float],
        *,
        ligand_sites_mol_m3: float = 0.0,
        dissolved_oxygen_mol_m3: float = 0.0,
    ) -> dict[str, float]:
        """Map a native mADM1 liquid endpoint onto the reduced serial feed.

        Native biological pseudo-components remain in their COD/element bases.
        Only the explicitly declared analytical chemistry states are mapped.
        ``S_h2`` is kg COD/m3, so the H2 conversion uses 16 kg COD/kmol H2
        (0.016 kg COD/mol) rather than treating it as a molar concentration.
        """

        required = (
            "S_IP",
            "S_SO4",
            "S_IS",
            "S_Fe3",
            "S_Fe2",
            "S_IC",
            "S_Cl",
            "S_h2",
        )
        missing = [component_id for component_id in required if component_id not in native_state]
        if missing:
            raise KeyError(f"serial Fe-S-P feed mapping missing native states {missing}")
        selected = {component_id: float(native_state[component_id]) for component_id in required}
        invalid = sorted(
            component_id
            for component_id, concentration in selected.items()
            if not math.isfinite(concentration) or concentration < 0.0
        )
        if invalid:
            raise ValueError(
                "serial Fe-S-P feed mapping requires finite nonnegative native states; "
                f"invalid={invalid}"
            )
        ligand = float(ligand_sites_mol_m3)
        oxygen = float(dissolved_oxygen_mol_m3)
        if any(not math.isfinite(value) or value < 0.0 for value in (ligand, oxygen)):
            raise ValueError("ligand-site and dissolved-oxygen boundaries must be finite and nonnegative")
        return {
            "Fe_III_aq": selected["S_Fe3"] / 0.055845,
            "Fe_II_aq": selected["S_Fe2"] / 0.055845,
            "H2_aq": selected["S_h2"] / 0.016,
            "O2_aq": oxygen,
            "HS_minus": selected["S_IS"] / 0.032065,
            "SO4": selected["S_SO4"] / 0.032065,
            "PO4": selected["S_IP"] / 0.030973761998,
            "CO3": selected["S_IC"] / 0.0120107,
            "Cl_minus": selected["S_Cl"] / 0.035453,
            "Ligand_sites": ligand,
        }

    def simulate_serial_madm1_history(
        self,
        native_liquid_state: Mapping[str, float],
        *,
        train: SerialChemistryTrainDefinition,
        inlet_ph: float,
        horizon_days: float,
        time_step_days: float,
        native_gas_transfer_completed: bool,
        ligand_sites_mol_m3: float = 0.0,
        dissolved_oxygen_mol_m3: float = 0.0,
        return_native_dissolved_feedback: bool = False,
        native_chemistry_delegation: Mapping[str, Any] | None = None,
        **serial_options: Any,
    ) -> dict[str, Any]:
        'Run the history-resolving mineral sidecar from a live mADM1 state.'

        if not native_gas_transfer_completed:
            raise ValueError(
                "native mADM1 gas transfer must be advanced before the serial mineral history sidecar"
            )
        feed = self.native_madm1_to_serial_feed(
            native_liquid_state,
            ligand_sites_mol_m3=ligand_sites_mol_m3,
            dissolved_oxygen_mol_m3=dissolved_oxygen_mol_m3,
        )
        result = simulate_serial_chemistry_train(
            train,
            feed_mol_m3=feed,
            inlet_ph=inlet_ph,
            horizon_days=horizon_days,
            time_step_days=time_step_days,
            kernel=self.kernel,
            **serial_options,
        )
        feedback: dict[str, Any] = {}
        feedback_requested_but_unavailable = False
        if return_native_dissolved_feedback:
            required_delegation = {
                "disable_minerals": True,
                "disable_hfo_p": True,
                "shared_kernel_owns_retained_minerals": True,
            }
            delegation = dict(native_chemistry_delegation or {})
            invalid_delegation = sorted(
                field
                for field, required_value in required_delegation.items()
                if delegation.get(field) is not required_value
            )
            if invalid_delegation:
                raise ValueError(
                    "native dissolved feedback requires explicit chemistry "
                    "delegation with native mineral/HFO reactions disabled; "
                    f"invalid={invalid_delegation}"
                )
            native_retained_ids = (
                "X_FeS",
                "X_Mackinawite",
                "X_Fe3PO42",
                "X_Siderite",
                "X_HFO_H",
                "X_HFO_L",
                "X_HFO_old",
                "X_HFO_HP",
                "X_HFO_LP",
                "X_HFO_HP_old",
                "X_HFO_LP_old",
            )
            missing_retained = sorted(
                component_id
                for component_id in native_retained_ids
                if component_id not in native_liquid_state
            )
            nonzero_retained = sorted(
                component_id
                for component_id in native_retained_ids
                if abs(float(native_liquid_state.get(component_id, 0.0))) > 1e-12
            )
            if missing_retained or nonzero_retained:
                raise ValueError(
                    "native dissolved feedback cannot prove exclusive retained-"
                    "chemistry ownership; "
                    f"missing_native_states={missing_retained}, "
                    f"nonzero_native_states={nonzero_retained}"
                )
            feedback_valid = bool(
                result.get("solver_converged") is True
                and result.get("mass_balance_passed") is True
                and (result.get("serial_checkpoint") or {}).get("persistable") is True
            )
            if not feedback_valid:
                feedback_requested_but_unavailable = True
                feedback = {
                    "available": False,
                    "applied_by_adapter": False,
                    "unavailable_reason": (
                        "serial chemistry must converge, close mass balance, and "
                        "produce a persistable checkpoint before dissolved feedback"
                    ),
                    "solver_converged": result.get("solver_converged"),
                    "mass_balance_passed": result.get("mass_balance_passed"),
                    "serial_checkpoint_persistable": bool(
                        (result.get("serial_checkpoint") or {}).get("persistable")
                    ),
                    "delegation": required_delegation,
                }
            else:
                anaerobic_cells = [
                    f"{zone.zone_id}:{zone.tanks_in_series}"
                    for zone in train.zones
                    if zone.zone_kind == "anaerobic_contact"
                ]
                if not anaerobic_cells:
                    raise ValueError("serial train has no anaerobic feedback cell")
                feedback_cell_id = anaerobic_cells[-1]
                chemistry_state = result["zone_states_mol_m3"][feedback_cell_id]
                conversion = {
                    "S_Fe3": ("Fe_III_aq", 0.055845),
                    "S_Fe2": ("Fe_II_aq", 0.055845),
                    "S_h2": ("H2_aq", 0.016),
                    "S_IS": ("HS_minus", 0.032065),
                    "S_SO4": ("SO4", 0.032065),
                    "S_IP": ("PO4", 0.030973761998),
                    "S_IC": ("CO3", 0.0120107),
                    "S_Cl": ("Cl_minus", 0.035453),
                }
                projected = dict(native_liquid_state)
                deltas: dict[str, float] = {}
                for native_id, (chemistry_id, kg_per_mol) in conversion.items():
                    projected_value = float(chemistry_state[chemistry_id]) * kg_per_mol
                    deltas[native_id] = projected_value - float(
                        native_liquid_state[native_id]
                    )
                    projected[native_id] = projected_value
                feedback = {
                    "available": True,
                    "applied_by_adapter": False,
                    "feedback_cell_id": feedback_cell_id,
                    "native_state_kg_m3": projected,
                    "native_component_delta_kg_m3": deltas,
                    "retained_history_location": "serial_checkpoint_only",
                    "application_semantics": (
                        "lagged_operator_split_boundary_for_next_native_step"
                    ),
                    "delegation": required_delegation,
                }
        result["qsdsan_coupling"] = {
            "coupling_class": "operator_split_native_madm1_to_shared_serial_mineral_history",
            "native_biology_advanced": True,
            "native_finite_headspace_advanced": True,
            "mineral_extent_feedback_to_native_state": False,
            "native_dissolved_feedback_projection_available": bool(
                feedback.get("available") is True
            ),
            "feed_basis": "explicit_native_basis_map_not_universal_molar_state",
            "h2_conversion_basis": "16_kg_COD_per_kmol_H2",
            "native_state_migration": qsdsan_madm1_native_migration_report(),
        }
        result["native_dissolved_feedback_candidate"] = feedback
        result["recommendation_eligible"] = False
        coupling_blocker = (
            "dissolved operator-split feedback was requested but withheld because "
            "the serial solve, balance, or checkpoint gate failed"
            if feedback_requested_but_unavailable
            else
            "dissolved operator-split feedback is only a lagged candidate and retained "
            "history is not part of the native mADM1 ODE"
            if feedback.get("available") is True
            else "operator-split mineral extents are not yet fed back into native mADM1 biology"
        )
        result["recommendation_blockers"] = list(result["recommendation_blockers"]) + [
            coupling_blocker
        ]
        return result

    def activity_domain_report(
        self,
        *,
        ionic_strength_molal: float,
        temperature_c: float,
        species_coverage_fraction: float,
    ) -> dict[str, object]:
        """Report the reduced activity model's recommendation eligibility."""

        return DAVIES_ACTIVITY_DOMAIN.check(
            ionic_strength_molal=ionic_strength_molal,
            temperature_c=temperature_c,
            species_coverage_fraction=species_coverage_fraction,
        )

    def advance_inventory(
        self,
        state_mol: Mapping[str, float],
        rates_mol_per_time: Mapping[str, float],
        dt: float,
    ):
        """Apply all reaction extents simultaneously with finite availability."""

        requested = {reaction_id: float(rate) * float(dt) for reaction_id, rate in rates_mol_per_time.items()}
        return constrained_reaction_step(state_mol, self.kernel.reactions, requested)

    def advance_cstr_inventory(
        self,
        state_mol: Mapping[str, float],
        *,
        rates_mol_per_volume_time: Mapping[str, float],
        inlet_mol: Mapping[str, float],
        dose_source_mol_per_volume_time: Mapping[str, float],
        aqueous_component_ids: tuple[str, ...],
        retained_solid_ids: tuple[str, ...],
        dt: float,
        hydraulic_residence_time: float,
        solids_retention_time: float,
    ):
        """Advance the shared chemistry inventory with explicit HRT and SRT."""

        return advance_cstr_inventory(
            state_mol,
            reactions=self.kernel.reactions,
            rates_mol_per_volume_time=rates_mol_per_volume_time,
            inlet=inlet_mol,
            dose_source_mol_per_volume_time=dose_source_mol_per_volume_time,
            aqueous_component_ids=aqueous_component_ids,
            retained_solid_ids=retained_solid_ids,
            component_definitions=self.kernel.components,
            dt=dt,
            hydraulic_residence_time=hydraulic_residence_time,
            solids_retention_time=solids_retention_time,
        )


DEFAULT_MADM1_FE_S_P_ADAPTER = QSDsanChemistryAdapter()


__all__ = ["DEFAULT_MADM1_FE_S_P_ADAPTER", "QSDsanChemistryAdapter"]
