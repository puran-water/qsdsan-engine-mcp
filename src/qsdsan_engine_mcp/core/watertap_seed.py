"""WaterTAP-to-QSDsan dynamic seeding helpers.

The converter is intentionally narrow: WaterTAP solved states are treated as
initial conditions for QSDsan dynamic runs, never as authoritative dynamic
results. It accepts the persistence/mirror shapes produced by
watertap-engine-mcp and returns QSDsan-native stream states plus per-unit
startup concentrations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from qsdsan_engine_mcp.core.model_registry import normalize_model_name


_AEROBIC_MODELS = {"ASM1", "ASM2d", "mASM2d"}
_ANAEROBIC_MODELS = {"ADM1", "mADM1"}

_COMPONENT_ALIASES = {
    "S_ALK": "S_IC",
    "S_NH": "S_NH4",
    "S_NO": "S_NO3",
    "S_O": "S_O2",
}

# --- mADM1 gas-phase (headspace) seeding (roadmap C1) -----------------------
# QSDsan stores the mADM1 headspace as a gas-slice concentration S_gas (kmol/m3)
# whose partial pressure is p_i = R * T * S_gas_i (models/madm1.py:1053). R here
# is the ideal-gas constant in bar*m3/(kmol*K); the WaterTAP vapor phase reports
# partial pressures, so the conversion to the QSDsan basis is
# S_gas_i[kmol/m3] = p_i[bar] / (R * T).
_R_BAR_M3_PER_KMOL_K = 0.0831446261815324

# Default headspace temperature (mesophilic AD) when a payload omits it.
_DEFAULT_ANAEROBIC_T_K = 308.15

# WaterTAP vapor-phase species names -> QSDsan mADM1 biogas-slice IDs. The QSDsan
# gas slice is ordered by ``_biogas_IDs`` = (S_h2, S_ch4, S_IC, S_IS): headspace
# CO2 is carried as S_IC (inorganic carbon) and H2S as S_IS, so a WaterTAP
# ``S_co2`` / ``S_h2s`` must be remapped onto those QSDsan ids. Keys are matched
# case-insensitively.
_GAS_COMPONENT_ALIASES = {
    "s_co2": "S_IC", "co2": "S_IC", "s_ic": "S_IC", "ic": "S_IC",
    "s_h2s": "S_IS", "h2s": "S_IS", "s_is": "S_IS", "is": "S_IS",
    "s_ch4": "S_ch4", "ch4": "S_ch4", "methane": "S_ch4",
    "s_h2": "S_h2", "h2": "S_h2", "hydrogen": "S_h2",
}

# Phase tokens that denote the vapor/gas phase in a WaterTAP phase-indexed key
# (e.g. ``conc_mol_phase_comp[0,Vap,S_ch4]``). Flat phase-indexed keys are
# filtered to these so a liquid ``S_IC`` is never mistaken for headspace CO2.
_VAPOR_PHASE_TOKENS = {"vap", "g", "gas", "vapor", "vapour"}


@dataclass(frozen=True)
class ConvertedSeed:
    stream_seeds: dict[str, dict[str, Any]]
    per_unit_init_conc: dict[str, dict[str, float]]
    warnings: list[str]
    provenance: dict[str, Any]
    gas_phase: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_seeds": self.stream_seeds,
            "per_unit_init_conc": self.per_unit_init_conc,
            "warnings": self.warnings,
            "provenance": self.provenance,
            "gas_phase": self.gas_phase,
        }


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _payload_value(payload: Any) -> tuple[float | None, str | None]:
    if isinstance(payload, dict):
        return _coerce_float(payload.get("value")), payload.get("units")
    return _coerce_float(payload), None


def _index_tokens(key: str) -> list[str]:
    match = re.search(r"\[(.*)\]", key)
    if not match:
        return []
    return [token.strip().strip("'\"") for token in match.group(1).split(",")]


def _canonical_component(component_id: str) -> str:
    return _COMPONENT_ALIASES.get(component_id, component_id)


def _is_aerobic_model(model_type: str) -> bool:
    return normalize_model_name(model_type) in _AEROBIC_MODELS


def _is_anaerobic_model(model_type: str) -> bool:
    return normalize_model_name(model_type) in _ANAEROBIC_MODELS


def _concentration_for_qsdsan(value: float, units: str | None, model_type: str) -> float:
    unit_text = (units or "").lower().replace(" ", "")
    if _is_aerobic_model(model_type):
        if "kg" in unit_text and ("m**3" in unit_text or "m^3" in unit_text or "m3" in unit_text):
            return value * 1000.0
        return value
    if _is_anaerobic_model(model_type):
        if "mg" in unit_text and ("l" in unit_text or "liter" in unit_text):
            return value / 1000.0
        if not unit_text:
            return value
        return value
    return value


def _stream_scalar(stream: dict[str, Any], name: str) -> tuple[float | None, str | None]:
    direct = stream.get(name)
    if direct is not None:
        value, units = _payload_value(direct)
        if value is not None:
            return value, units
    for key, payload in stream.items():
        if key == name or key.startswith(f"{name}["):
            value, units = _payload_value(payload)
            if value is not None:
                return value, units
    return None, None


def _flow_m3_d(stream: dict[str, Any]) -> float | None:
    direct = _coerce_float(stream.get("flow_m3_d"))
    if direct is not None:
        return direct
    value, units = _stream_scalar(stream, "flow_vol")
    if value is None:
        return None
    unit_text = (units or "").lower().replace(" ", "")
    if "/s" in unit_text or "s**-1" in unit_text:
        return value * 86400.0
    if "/hr" in unit_text or "/h" in unit_text or "h**-1" in unit_text:
        return value * 24.0
    return value


def _temperature_k(stream: dict[str, Any]) -> float | None:
    direct = _coerce_float(stream.get("temperature_K") or stream.get("temperature_k"))
    if direct is not None:
        return direct
    value, units = _stream_scalar(stream, "temperature")
    if value is None:
        return None
    unit_text = (units or "").lower()
    if unit_text in {"c", "degc", "degree_celsius"}:
        return value + 273.15
    return value


def _components_from_raw_stream(stream: dict[str, Any], model_type: str) -> dict[str, float]:
    components: dict[str, float] = {}
    for key, payload in stream.items():
        value, units = _payload_value(payload)
        if value is None:
            continue
        tokens = _index_tokens(key)
        component = _canonical_component(tokens[-1]) if tokens else None
        if key.startswith("conc_mass_comp[") and component:
            components[component] = _concentration_for_qsdsan(value, units, model_type)
        elif key.startswith("conc_mol_comp[") and component:
            components[f"{component}_mol_conc"] = value
    return components


def _components_from_mirror_stream(stream: dict[str, Any], model_type: str) -> dict[str, float]:
    raw = stream.get("raw")
    if isinstance(raw, dict):
        raw_components = _components_from_raw_stream(raw, model_type)
        if raw_components:
            return raw_components

    components: dict[str, float] = {}
    raw_components = stream.get("concentrations") or stream.get("components") or {}
    units = stream.get("concentration_units") or stream.get("component_units")
    if isinstance(raw_components, dict):
        for key, payload in raw_components.items():
            value, item_units = _payload_value(payload)
            if value is None:
                continue
            components[_canonical_component(str(key))] = _concentration_for_qsdsan(
                value,
                item_units or units,
                model_type,
            )
    return components


def convert_watertap_stream_state(
    stream_id: str,
    stream: dict[str, Any],
    *,
    model_type: str,
    source_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Convert one WaterTAP stream snapshot into a QSDsan StreamState dict."""

    source = stream.get("raw") if isinstance(stream.get("raw"), dict) else stream
    flow_m3_d = _flow_m3_d(source) or _flow_m3_d(stream)
    temperature_k = _temperature_k(source) or _temperature_k(stream) or 298.15
    components = _components_from_mirror_stream(stream, model_type)
    if not components:
        components = _components_from_raw_stream(source, model_type)
    if flow_m3_d is None or flow_m3_d <= 0.0 or not components:
        return None
    return {
        "model_type": normalize_model_name(model_type),
        "flow_m3_d": flow_m3_d,
        "temperature_K": temperature_k,
        "concentrations": components,
        "metadata": {
            "source_engine": "watertap-engine-mcp",
            "source_run_id": source_run_id,
            "source_stream_id": stream_id,
            "seed_role": "dynamic_initial_condition",
        },
    }


def _solved_state(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("solved_state"), dict):
        return payload["solved_state"]
    if isinstance(payload.get("simulation_diagnostics"), dict) and isinstance(
        payload["simulation_diagnostics"].get("solved_state"),
        dict,
    ):
        return payload["simulation_diagnostics"]["solved_state"]
    if isinstance(payload.get("raw"), dict) and isinstance(payload["raw"].get("solved_state"), dict):
        return payload["raw"]["solved_state"]
    return payload


def _stream_payloads(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state = _solved_state(payload)
    streams = state.get("streams") if isinstance(state, dict) else None
    if isinstance(streams, dict):
        return {str(k): v for k, v in streams.items() if isinstance(v, dict)}
    stream_id = payload.get("stream_id")
    if isinstance(stream_id, str):
        return {stream_id: payload}
    return {}


def _unit_payloads(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state = _solved_state(payload)
    units = state.get("units") if isinstance(state, dict) else None
    if isinstance(units, dict):
        return {str(k): v for k, v in units.items() if isinstance(v, dict)}
    return {}


def _unit_initial_concentrations(unit_state: dict[str, Any], model_type: str) -> dict[str, float]:
    concentrations: dict[str, float] = {}
    for key, payload in unit_state.items():
        value, units = _payload_value(payload)
        if value is None:
            continue
        tokens = _index_tokens(key)
        component = _canonical_component(tokens[-1]) if tokens else None
        if not component:
            continue
        if key.startswith("conc_mass_comp[") or key.startswith("state[") or "conc_mass_comp" in key:
            concentrations[component] = _concentration_for_qsdsan(value, units, model_type)
    return concentrations


def _canonical_gas_component(name: Any) -> str | None:
    """Map a WaterTAP vapor-species name to a QSDsan biogas-slice id, or None."""
    if name is None:
        return None
    return _GAS_COMPONENT_ALIASES.get(str(name).strip().lower())


def _pressure_to_bar(value: float | None, units: str | None) -> float | None:
    """Convert a pressure value to bar. Pa is the WaterTAP SI default when units
    are absent."""
    if value is None:
        return None
    unit_text = (units or "").lower().replace(" ", "")
    if "bar" in unit_text:
        return value
    if "kpa" in unit_text:
        return value / 100.0
    if "mpa" in unit_text:
        return value * 10.0
    if "atm" in unit_text:
        return value * 1.01325
    # Pa or unspecified (WaterTAP vapor pressures are SI Pa).
    return value / 1.0e5


def _molar_conc_to_kmol_m3(
    value: float | None, units: str | None, *, default_kmol: bool
) -> float | None:
    """Convert a molar concentration to kmol/m3 (QSDsan gas-slice basis)."""
    if value is None:
        return None
    unit_text = (units or "").lower().replace(" ", "")
    if "kmol" in unit_text:
        return value
    if "mol" in unit_text:  # mol/m3 -> kmol/m3
        return value / 1000.0
    # No units: hand-authored gas blocks are kmol/m3; flat WaterTAP
    # conc_mol_phase_comp keys are mol/m3.
    return value if default_kmol else value / 1000.0


def _partial_pressure_to_kmol_m3(p_bar: float, temperature_k: float) -> float:
    """Headspace partial pressure (bar) -> gas-slice concentration (kmol/m3)."""
    return p_bar / (_R_BAR_M3_PER_KMOL_K * temperature_k)


def _gas_block_temperature_k(block: dict[str, Any]) -> float:
    direct = _coerce_float(block.get("temperature_K") or block.get("temperature_k"))
    if direct is not None and direct > 0:
        return direct
    flat, _ = _stream_scalar(block, "temperature")
    if flat is not None and flat > 0:
        # WaterTAP serialises temperature in K.
        return flat
    return _DEFAULT_ANAEROBIC_T_K


def _gas_total_pressure_bar(block: dict[str, Any]) -> float | None:
    direct_bar = _coerce_float(block.get("headspace_pressure_bar"))
    if direct_bar is not None:
        return direct_bar
    direct_pa = _coerce_float(
        block.get("headspace_pressure_pa") or block.get("headspace_pressure_Pa")
    )
    if direct_pa is not None:
        return direct_pa / 1.0e5
    for name in ("total_pressure", "headspace_pressure", "pressure"):
        if name in block:
            value, units = _payload_value(block[name])
            converted = _pressure_to_bar(value, units)
            if converted is not None:
                return converted
        flat, units = _stream_scalar(block, name)
        if flat is not None:
            converted = _pressure_to_bar(flat, units)
            if converted is not None:
                return converted
    return None


def _vapor_species_from_index(key: str) -> str | None:
    """Return the canonical gas id from a flat phase-indexed key, vapor-only."""
    tokens = _index_tokens(key)
    if not tokens:
        return None
    if len(tokens) >= 3 and tokens[-2].strip().lower() not in _VAPOR_PHASE_TOKENS:
        # 3-token phase_comp key whose phase is not vapor (e.g. a liquid S_IC).
        return None
    return _canonical_gas_component(tokens[-1])


def _collect_partials_bar(block: dict[str, Any]) -> dict[str, float]:
    """Gas-species partial pressures (bar), keyed by QSDsan biogas id."""
    partials: dict[str, float] = {}
    # The synchronous ADM1 ``solve_flowsheet`` producer
    # (watertap actions._adm1_effluent_payload) emits the digester headspace
    # partial pressures under ``partial_pressures_pa`` (raw Pa floats), mirroring
    # its ``headspace_pressure_pa`` total-pressure key (already handled in
    # _gas_total_pressure_bar). Accept that suffixed key so the REAL adm1_uasb
    # solve's ``biogas`` block seeds the gas slice (roadmap follow-up #18 -- the
    # hand-authored fixtures used ``partial_pressures`` and masked this drift).
    nested = (
        block.get("partial_pressures")
        or block.get("partial_pressures_pa")
        or block.get("partial_pressures_Pa")
        or block.get("pressure_sat")
    )
    if isinstance(nested, dict):
        for raw_name, payload in nested.items():
            gid = _canonical_gas_component(raw_name)
            if gid is None:
                continue
            value, units = _payload_value(payload)
            p_bar = _pressure_to_bar(value, units)
            if p_bar is not None:
                partials[gid] = partials.get(gid, 0.0) + p_bar
    for key, payload in block.items():
        if not isinstance(key, str) or not key.startswith("pressure_sat["):
            continue
        gid = _vapor_species_from_index(key)
        if gid is None:
            continue
        value, units = _payload_value(payload)
        p_bar = _pressure_to_bar(value, units)
        if p_bar is not None:
            partials[gid] = partials.get(gid, 0.0) + p_bar
    return partials


def _collect_molar_kmol(block: dict[str, Any]) -> dict[str, float]:
    """Gas-species molar concentrations (kmol/m3), keyed by QSDsan biogas id."""
    molar: dict[str, float] = {}
    for nested_key, default_kmol in (
        ("concentrations", True),
        ("concentrations_kmol_m3", True),
        ("conc", True),
        ("conc_mol_phase_comp", False),
    ):
        nested = block.get(nested_key)
        if isinstance(nested, dict):
            for raw_name, payload in nested.items():
                gid = _canonical_gas_component(raw_name)
                if gid is None:
                    continue
                value, units = _payload_value(payload)
                conc = _molar_conc_to_kmol_m3(value, units, default_kmol=default_kmol)
                if conc is not None:
                    molar[gid] = molar.get(gid, 0.0) + conc
    for key, payload in block.items():
        if not isinstance(key, str):
            continue
        if key.startswith("conc_mol_phase_comp[") or key.startswith("conc_mol_comp["):
            gid = _vapor_species_from_index(key)
            if gid is None:
                continue
            value, units = _payload_value(payload)
            conc = _molar_conc_to_kmol_m3(value, units, default_kmol=False)
            if conc is not None:
                molar[gid] = molar.get(gid, 0.0) + conc
    return molar


def _collect_mole_fractions(block: dict[str, Any]) -> dict[str, float]:
    """Gas-species mole fractions, keyed by QSDsan biogas id."""
    fractions: dict[str, float] = {}
    nested = block.get("mole_fractions") or block.get("mole_frac")
    if isinstance(nested, dict):
        for raw_name, payload in nested.items():
            gid = _canonical_gas_component(raw_name)
            if gid is None:
                continue
            value, _ = _payload_value(payload)
            if value is not None:
                fractions[gid] = fractions.get(gid, 0.0) + value
    for key, payload in block.items():
        if not isinstance(key, str):
            continue
        if key.startswith("mole_frac_phase_comp[") or key.startswith("mole_frac_comp["):
            gid = _vapor_species_from_index(key)
            if gid is None:
                continue
            value, _ = _payload_value(payload)
            if value is not None:
                fractions[gid] = fractions.get(gid, 0.0) + value
    return fractions


def _parse_gas_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one candidate gas block into a QSDsan gas-slice seed (kmol/m3).

    Conversion priority: partial pressures (the WaterTAP/digester-oracle native
    quantity) -> molar concentrations -> mole fractions + total pressure.
    """
    if not isinstance(block, dict):
        return None

    temperature_k = _gas_block_temperature_k(block)
    total_pressure_bar = _gas_total_pressure_bar(block)

    concentrations: dict[str, float] = {}
    source_basis: dict[str, str] = {}

    partials = _collect_partials_bar(block)
    if partials:
        for gid, p_bar in partials.items():
            concentrations[gid] = _partial_pressure_to_kmol_m3(p_bar, temperature_k)
            source_basis[gid] = "partial_pressure"
        if total_pressure_bar is None:
            total_pressure_bar = sum(partials.values())
    else:
        molar = _collect_molar_kmol(block)
        if molar:
            for gid, conc in molar.items():
                concentrations[gid] = conc
                source_basis[gid] = "molar_concentration"
            if total_pressure_bar is None:
                total_pressure_bar = sum(
                    _R_BAR_M3_PER_KMOL_K * temperature_k * conc
                    for conc in molar.values()
                )
        else:
            fractions = _collect_mole_fractions(block)
            if fractions and total_pressure_bar:
                for gid, frac in fractions.items():
                    p_bar = frac * total_pressure_bar
                    concentrations[gid] = _partial_pressure_to_kmol_m3(
                        p_bar, temperature_k
                    )
                    source_basis[gid] = "mole_fraction"

    if not concentrations:
        return None

    return {
        "concentrations_kmol_m3": concentrations,
        "temperature_K": temperature_k,
        "headspace_pressure_bar": total_pressure_bar,
        "source_basis": source_basis,
    }


def _gather_gas_candidates(
    payload: dict[str, Any],
    state: dict[str, Any],
) -> list[tuple[dict[str, Any], str | None]]:
    """Collect candidate gas blocks (block, watertap_unit_hint) from a payload.

    Search order: explicit ``gas_phase`` / ``headspace`` / ``vapor`` / ``biogas``
    dicts, then vapor-looking streams in the solved state, then digester unit
    blocks carrying vapor-phase keys.
    """
    candidates: list[tuple[dict[str, Any], str | None]] = []

    for container in (payload, state):
        if not isinstance(container, dict):
            continue
        for key in ("gas_phase", "headspace", "vapor", "biogas"):
            block = container.get(key)
            if isinstance(block, dict):
                hint = block.get("unit") or block.get("unit_id") or block.get("digester_id")
                candidates.append((block, hint))
        raw = container.get("raw")
        if isinstance(raw, dict):
            for key in ("gas_phase", "headspace", "vapor", "biogas"):
                block = raw.get(key)
                if isinstance(block, dict):
                    hint = block.get("unit") or block.get("unit_id")
                    candidates.append((block, hint))

    roles = state.get("stream_roles") if isinstance(state, dict) else None
    roles = roles if isinstance(roles, dict) else {}
    for stream_id, stream in _stream_payloads(payload).items():
        sid_lower = stream_id.lower()
        role = str(roles.get(stream_id, "")).lower()
        looks_vapor = (
            any(token in sid_lower for token in ("vapor", "vapour", "biogas", "gas"))
            or "gas_phase" in role
            or any(
                isinstance(k, str)
                and (k.startswith("pressure_sat[") or ",vap," in k.lower())
                for k in stream
            )
        )
        if looks_vapor:
            hint = stream_id.split(".")[0] if "." in stream_id else stream_id
            candidates.append((stream, hint))

    for unit_id, unit_state in _unit_payloads(payload).items():
        has_vapor = any(
            isinstance(k, str)
            and (k.startswith("pressure_sat[") or ",vap," in k.lower())
            for k in unit_state
        )
        nested = unit_state.get("vapor_phase") or unit_state.get("gas_phase")
        if has_vapor:
            candidates.append((unit_state, unit_id))
        if isinstance(nested, dict):
            candidates.append((nested, unit_id))

    return candidates


def extract_gas_phase_seed(
    payload: dict[str, Any],
    *,
    model_type: str,
    unit_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Extract + convert a WaterTAP mADM1 headspace into a QSDsan gas-slice seed.

    Only runs for the anaerobic (``adm1``) family; aerobic (mASM2d) payloads have
    no gas phase and return ``None``. The returned block carries the gas-slice
    concentrations (kmol/m3, keyed by QSDsan biogas ids), the headspace
    temperature/pressure, and a unit hint (raw WaterTAP id + the unit-mapped
    QSDsan id) so the caller can target the correct digester.
    """
    if not _is_anaerobic_model(model_type):
        return None

    state = _solved_state(payload)
    unit_seed_map = unit_map or {}

    for block, raw_hint in _gather_gas_candidates(payload, state):
        parsed = _parse_gas_block(block)
        if parsed and parsed.get("concentrations_kmol_m3"):
            parsed["watertap_unit_hint"] = raw_hint
            parsed["qsdsan_unit_hint"] = (
                unit_seed_map.get(raw_hint, raw_hint) if raw_hint else None
            )
            return parsed
    return None


def _solution_quality_values(payload: dict[str, Any]) -> list[str]:
    """Collect every ``solution_quality`` value carried anywhere in the payload.

    The WaterTAP SolveResult contract carries ``solution_quality`` at the top
    level, but a payload may nest a solve result (e.g. under ``raw`` or
    ``simulation_diagnostics``), so walk the tree and gather them all.
    """

    found: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            value = obj.get("solution_quality")
            if isinstance(value, str):
                found.append(value.strip().lower())
            for child in obj.values():
                _walk(child)
        elif isinstance(obj, (list, tuple)):
            for child in obj:
                _walk(child)

    _walk(payload)
    return found


def seed_has_near_feasible_flag(payload: dict[str, Any]) -> bool:
    """True when a WaterTAP payload represents a near-feasible (non-strict) solve.

    The authoritative signal is the A2 ``solution_quality`` field on the
    SolveResult contract: ``"near_feasible"`` => refuse unless acknowledged,
    while an explicit ``"strict_optimal"`` (or ``"infeasible_structured"``)
    means the boolean fallback should NOT independently flip the verdict. Only
    when no ``solution_quality`` is present anywhere do we fall back to the
    legacy ``near_feasible_accepted`` / ``accepted_near_feasible`` boolean flags.
    """

    qualities = _solution_quality_values(payload)
    if qualities:
        return "near_feasible" in qualities

    text = json.dumps(payload, default=str).lower()
    return '"near_feasible_accepted": true' in text or '"accepted_near_feasible": true' in text


def build_watertap_seed(
    payload: dict[str, Any],
    *,
    model_type: str,
    stream_map: dict[str, str] | None = None,
    unit_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build QSDsan stream and per-unit initial-condition seeds from WaterTAP output."""

    canonical_model = normalize_model_name(model_type)
    source_run_id = (
        payload.get("engine_run_id")
        or payload.get("session_id")
        or payload.get("job_id")
        or payload.get("model_run_id")
    )
    warnings: list[str] = []
    stream_seed_map = stream_map or {}
    unit_seed_map = unit_map or {}

    stream_seeds: dict[str, dict[str, Any]] = {}
    for watertap_stream_id, stream in _stream_payloads(payload).items():
        qsdsan_stream_id = stream_seed_map.get(watertap_stream_id, watertap_stream_id)
        converted = convert_watertap_stream_state(
            watertap_stream_id,
            stream,
            model_type=canonical_model,
            source_run_id=str(source_run_id) if source_run_id else None,
        )
        if converted is None:
            warnings.append(f"Skipped WaterTAP stream '{watertap_stream_id}': no flow/components usable for QSDsan.")
            continue
        stream_seeds[qsdsan_stream_id] = converted

    per_unit_init_conc: dict[str, dict[str, float]] = {}
    for watertap_unit_id, unit_state in _unit_payloads(payload).items():
        qsdsan_unit_id = unit_seed_map.get(watertap_unit_id, watertap_unit_id)
        concentrations = _unit_initial_concentrations(unit_state, canonical_model)
        if concentrations:
            per_unit_init_conc[qsdsan_unit_id] = concentrations
        else:
            warnings.append(f"Skipped WaterTAP unit '{watertap_unit_id}': no concentration state variables found.")

    # mADM1 headspace seeding (roadmap C1): a WaterTAP mADM1 steady solve carries a
    # converged gas phase; aerobic (mASM2d) payloads have none. Extraction is
    # guarded to the anaerobic family inside extract_gas_phase_seed.
    gas_phase = extract_gas_phase_seed(
        payload,
        model_type=canonical_model,
        unit_map=unit_seed_map,
    )

    return ConvertedSeed(
        stream_seeds=stream_seeds,
        per_unit_init_conc=per_unit_init_conc,
        warnings=warnings,
        provenance={
            "source_engine": "watertap-engine-mcp",
            "source_run_id": source_run_id,
            "model_type": canonical_model,
            "stream_map_applied": stream_seed_map,
            "unit_map_applied": unit_seed_map,
        },
        gas_phase=gas_phase,
    ).to_dict()


__all__ = [
    "build_watertap_seed",
    "convert_watertap_stream_state",
    "extract_gas_phase_seed",
    "seed_has_near_feasible_flag",
]
