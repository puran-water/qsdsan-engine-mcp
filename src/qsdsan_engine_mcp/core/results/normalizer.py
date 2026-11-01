"""Normalize raw CLI/job result payloads into the public result shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedResults:
    """One-level normalized results plus optional wrapper context."""

    data: dict[str, Any]
    was_wrapped: bool = False
    wrapper_key: str | None = None
    wrapper_context: dict[str, Any] = field(default_factory=dict)


def normalize_results_payload(payload: Mapping[str, Any] | None) -> NormalizedResults:
    """
    Normalize QSDsan CLI output by unwrapping one ``simulation_results`` layer.

    The CLI historically emitted both raw result dicts and wrapper dicts shaped
    as ``{"simulation_results": {...}}``. This function unwraps exactly one
    layer so nested/accidental recursive wrappers remain visible to callers.
    """

    if payload is None:
        return NormalizedResults(data={})
    if not isinstance(payload, Mapping):
        raise TypeError(f"results payload must be a mapping, got {type(payload).__name__}")

    raw = dict(payload)
    inner = raw.get("simulation_results")
    if isinstance(inner, Mapping):
        context = {key: value for key, value in raw.items() if key != "simulation_results"}
        return NormalizedResults(
            data=dict(inner),
            was_wrapped=True,
            wrapper_key="simulation_results",
            wrapper_context=context,
        )
    return NormalizedResults(data=raw)


def normalized_results_dict(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the normalized result data dict."""

    return normalize_results_payload(payload).data


def result_validity_blockers(payload: Mapping[str, Any] | None) -> list[str]:
    """Return explicit physical/numerical blockers carried by a result payload.

    Older valid result payloads predate these fields, so absence alone is not a
    blocker. Any explicit invalid signal is authoritative, including one found
    after unwrapping the historical ``simulation_results`` envelope.
    """

    results = normalized_results_dict(payload)
    blockers: list[str] = []
    if results.get("result_valid") is False:
        blockers.append("result_valid_false")
    if results.get("physicality_ok") is False:
        blockers.append("physicality_failed")
    physicality = results.get("physicality")
    if isinstance(physicality, Mapping) and physicality.get("physicality_ok") is False:
        blockers.append("nested_physicality_failed")
    if results.get("simulation_completed") is False:
        blockers.append("simulation_not_validly_completed")
    simulation = results.get("simulation")
    if isinstance(simulation, Mapping):
        status = str(simulation.get("status", ""))
        if status.startswith("invalid_"):
            blockers.append(status)
    return list(dict.fromkeys(blockers))


def normalize_job_response(response: Mapping[str, Any], *, drop_time_series: bool = True) -> dict[str, Any]:
    """Normalize the ``results`` member of a JobManager response."""

    normalized_response = dict(response)
    results = normalized_response.get("results")
    if not isinstance(results, Mapping):
        return normalized_response

    normalized = normalize_results_payload(results)
    data = dict(normalized.data)
    if drop_time_series and "time_series" in data:
        normalized_response["time_series_available"] = True
        normalized_response["time_series_note"] = (
            "Time series data excluded from response. Use get_timeseries_data(job_id) to retrieve."
        )
        data.pop("time_series", None)

    normalized_response["results"] = data
    validity_blockers = result_validity_blockers(data)
    if validity_blockers:
        # Preserve ``status=completed`` as the background process lifecycle,
        # while surfacing that its scientific result is invalid.
        normalized_response["result_status"] = "invalid_result"
        normalized_response["result_valid"] = False
        normalized_response["result_validity_blockers"] = validity_blockers
    if normalized.was_wrapped:
        normalized_response["results_normalized"] = True
        normalized_response["results_wrapper_key"] = normalized.wrapper_key
    return normalized_response
