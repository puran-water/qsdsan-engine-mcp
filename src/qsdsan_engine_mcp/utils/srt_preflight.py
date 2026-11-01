"""Preflight validation helpers for SRT-controlled flowsheet runs."""

from __future__ import annotations

from typing import Iterable, Tuple

from qsdsan_engine_mcp.core.model_registry import normalize_model_name


BIOMASS_SEED_IDS = {
    "ASM2d": ("X_H", "X_AUT", "X_PAO", "X_PHA", "X_PP"),
    "mASM2d": ("X_H", "X_AUT", "X_PAO", "X_PHA", "X_PP"),
    "ASM1": ("X_B_H", "X_B_A"),
    "mADM1": (
        "X_su",
        "X_aa",
        "X_fa",
        "X_c4",
        "X_pro",
        "X_ac",
        "X_h2",
        "X_PAO",
        "X_hSRB",
        "X_aSRB",
        "X_pSRB",
        "X_c4SRB",
    ),
    # Two-step nitrification splits X_AUT into X_AOB + X_NOB; X_AUT does not
    # exist in these component sets.
    "mASM2d2N": ("X_H", "X_AOB", "X_NOB", "X_PAO", "X_PHA", "X_PP"),
    "mASM2d2N_N2O": ("X_H", "X_AOB", "X_NOB", "X_PAO", "X_PHA", "X_PP"),
    "ADM1": ("X_su", "X_aa", "X_fa", "X_c4", "X_pro", "X_ac", "X_h2"),
}


# Components that must EACH be independently non-zero, not merely contribute to
# a non-zero total. Without this, a two-step session seeded with X_AOB but no
# X_NOB passes preflight and then silently cannot oxidise nitrite -- which is
# indistinguishable from the nitrite-lock condition the model exists to detect.
REQUIRED_GUILD_SEEDS = {
    "mASM2d2N": ("X_AOB", "X_NOB"),
    "mASM2d2N_N2O": ("X_AOB", "X_NOB"),
}


def get_biomass_seed_ids(model_type: str) -> Tuple[str, ...]:
    """Return biomass component IDs relevant for startup/SRT preflight."""
    canonical = normalize_model_name(model_type)
    return BIOMASS_SEED_IDS.get(canonical, BIOMASS_SEED_IDS["ASM2d"])


def _sum_biomass_seed(concentrations: dict, biomass_ids: Iterable[str]) -> float:
    """Sum non-zero biomass concentrations for a single stream config."""
    total = 0.0
    for comp_id in biomass_ids:
        try:
            total += max(float(concentrations.get(comp_id, 0.0)), 0.0)
        except (TypeError, ValueError):
            continue
    return total


def session_has_biomass_seed(session, model_type: str | None = None) -> tuple[bool, Tuple[str, ...], float]:
    """
    Determine whether a flowsheet session contains any explicit biomass seed.

    SRT control on a fresh compiled system requires at least one incoming stream
    with non-zero biomass concentration; otherwise a blank activated-sludge
    system cannot accumulate solids and SRT is undefined.
    """
    canonical = normalize_model_name(model_type or session.primary_model_type)
    biomass_ids = get_biomass_seed_ids(canonical)

    sources = []
    for stream_cfg in getattr(session, "streams", {}).values():
        concentrations = getattr(stream_cfg, "concentrations", {}) or {}
        sources.append(concentrations)

    startup_metadata = getattr(session, "startup_metadata", {}) or {}
    per_unit_init_conc = startup_metadata.get("per_unit_init_conc") or {}
    if isinstance(per_unit_init_conc, dict):
        for init_conc in per_unit_init_conc.values():
            if isinstance(init_conc, dict):
                sources.append(init_conc)

    total_seed = sum(_sum_biomass_seed(src, biomass_ids) for src in sources)

    # Per-guild requirement: the aggregate total is not sufficient when distinct
    # guilds must each be present (see REQUIRED_GUILD_SEEDS).
    for guild_id in REQUIRED_GUILD_SEEDS.get(canonical, ()):
        if sum(_sum_biomass_seed(src, (guild_id,)) for src in sources) <= 0.0:
            return False, biomass_ids, total_seed

    return total_seed > 0.0, biomass_ids, total_seed


def format_missing_biomass_seed_error(model_type: str, biomass_ids: Iterable[str]) -> str:
    """Return a consistent user-facing error for impossible unseeded SRT runs."""
    canonical = normalize_model_name(model_type)
    biomass_list = ", ".join(biomass_ids)
    message = (
        f"SRT control for model '{canonical}' requires seeded biomass "
        f"in at least one session stream. Add non-zero concentrations for one or more biomass "
        f"components before requesting --target-srt / target_srt_days. Expected seed components: "
        f"{biomass_list}."
    )
    required = REQUIRED_GUILD_SEEDS.get(canonical)
    if required:
        guild_list = " and ".join(required)
        message += (
            f" Note that {canonical} additionally requires {guild_list} to each be "
            f"independently non-zero: a session seeded with only one of the two guilds "
            f"cannot complete nitrification, and the resulting nitrite accumulation would be "
            f"a seeding artefact rather than a process finding."
        )
    return message
