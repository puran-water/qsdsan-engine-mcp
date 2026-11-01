"""Declared QSDsan mADM1 native-state migration boundary for shared Fe-S-P."""

from __future__ import annotations

from collections.abc import Iterable

from puran_mcp_support.schema import MADM1_FECL3_FE_S_P_KERNEL, NativeStateMigrationContract, NativeStateRequirement

QSDSAN_MADM1_FE_S_P_NATIVE_MIGRATION = NativeStateMigrationContract(
    migration_id="qsdsan-madm1-shared-fe-s-p-native-v2",
    engine_id="qsdsan-engine-mcp",
    model_variant="ModifiedADM1",
    kernel_id=MADM1_FECL3_FE_S_P_KERNEL.kernel_id,
    kernel_hash=MADM1_FECL3_FE_S_P_KERNEL.sha256,
    source_layout_version=(
        "madm1/current-extended-minerals-and-hfo-combined-host-surface"
    ),
    target_layout_version="madm1/shared-fe-s-p-governing-native/v2",
    checkpoint_policy="cold_reseed",
    requirements=(
        NativeStateRequirement(
            "dissolved_analytical_totals",
            (
                "Fe_III_aq",
                "Fe_II_aq",
                "H2_aq",
                "HS_minus",
                "SO4",
                "PO4",
                "CO3",
                "Cl_minus",
            ),
            "differential_state",
            "governing",
            ("S_Fe3", "S_Fe2", "S_h2", "S_IS", "S_SO4", "S_IP", "S_IC", "S_Cl"),
            "native elemental/COD bases mapped explicitly; acid-base free ions are algebraic",
        ),
        NativeStateRequirement(
            "elemental_sulfur",
            ("S_zero",),
            "differential_state",
            "governing",
            ("S_S0",),
            "kg_S_m3",
        ),
        NativeStateRequirement(
            "selected_mineral_inventories",
            ("FeS_amorphous", "Mackinawite", "Vivianite", "Siderite"),
            "differential_state",
            "governing",
            ("X_FeS", "X_Mackinawite", "X_Fe3PO42", "X_Siderite"),
            "native mineral mass bases with explicit formula adapters",
        ),
        NativeStateRequirement(
            "fresh_and_aged_hfo_hosts",
            ("HFO", "HFO_aged"),
            "differential_state",
            "partial_native",
            ("X_HFO_H", "X_HFO_L", "X_HFO_old"),
            "native high/low/old Fe-basis hosts do not match the canonical history/site split",
            "native_HFO_host_semantics_do_not_match_shared_history_states",
        ),
        NativeStateRequirement(
            "fes_reoxidized_hfo_host",
            ("HFO_reoxidized",),
            "differential_state",
            "sidecar_only",
            (),
            "retained only in the serial chemistry checkpoint",
            "FeS_reoxidized_HFO_is_not_a_governing_native_state",
        ),
        NativeStateRequirement(
            "history_specific_surface_p",
            (
                "HFO_fresh_surface_P",
                "HFO_reoxidized_surface_P",
                "HFO_aged_surface_P",
            ),
            "differential_state",
            "partial_native",
            ("X_HFO_HP", "X_HFO_LP", "X_HFO_HP_old", "X_HFO_LP_old"),
            "native pseudo-components combine host Fe and bound P; reoxidized surface P is sidecar-only",
            "surface_P_is_not_separable_from_native_hosts_and_reoxidized_surface_P_is_missing",
        ),
        NativeStateRequirement(
            "oxic_history_driver",
            ("O2_aq",),
            "differential_state",
            "sidecar_only",
            (),
            "supplied only to serial oxidation/aerobic zones",
            "native_single_anaerobic_reactor_has_no_oxic_history_state",
        ),
        NativeStateRequirement(
            "conditional_ligand_inventory",
            ("Ligand_sites",),
            "differential_state",
            "sidecar_only",
            (),
            "conditional ligand sites are transported only by the serial sidecar",
            "conditional_ligand_inventory_is_not_a_governing_native_state",
        ),
        NativeStateRequirement(
            "proton_charge_closure",
            ("H_plus",),
            "algebraic_state",
            "partial_native",
            ("pcm_charge_balance_pH",),
            "native pH closure does not yet consume all shared chemistry source terms",
            "shared_reaction_extents_are_not_coupled_to_native_charge_closure",
        ),
        NativeStateRequirement(
            "solvent_reservoir",
            ("H2O",),
            "implicit_reservoir",
            "implicit_reservoir",
            (),
            "declared implicit solvent reservoir",
        ),
    ),
    shared_material_source_terms_coupled=False,
    shared_charge_acid_base_coupled=False,
    serial_zone_topology_coupled=False,
    initialization_and_scaling_complete=False,
    restart_migration_implemented=False,
)


def qsdsan_madm1_native_migration_report(
    native_component_ids: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return the migration contract and optionally verify catalog coverage."""

    report = QSDSAN_MADM1_FE_S_P_NATIVE_MIGRATION.public_dict()
    if native_component_ids is None:
        report["native_component_catalog_checked"] = False
        report["catalog_missing_declared_states"] = []
        return report

    available = {str(component_id) for component_id in native_component_ids}
    declared = {
        component_id
        for requirement in QSDSAN_MADM1_FE_S_P_NATIVE_MIGRATION.requirements
        if requirement.implementation_status in {"governing", "partial_native"}
        for component_id in requirement.native_state_ids
        if component_id != "pcm_charge_balance_pH"
    }
    missing = sorted(declared - available)
    report["native_component_catalog_checked"] = True
    report["catalog_missing_declared_states"] = missing
    if missing:
        report["native_coupling_complete"] = False
        report["blockers"] = list(
            dict.fromkeys(
                [*report["blockers"], "declared_native_component_missing_from_runtime_catalog"]
            )
        )
    return report


__all__ = [
    "QSDSAN_MADM1_FE_S_P_NATIVE_MIGRATION",
    "qsdsan_madm1_native_migration_report",
]
