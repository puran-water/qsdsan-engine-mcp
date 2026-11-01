"""Helpers for resolving flowsheet input references, named outputs, and recycles."""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

from qsdsan_engine_mcp.core.model_registry import normalize_model_name
from qsdsan_engine_mcp.core.unit_registry import get_junction_output_model
from qsdsan_engine_mcp.utils.pipe_parser import is_tuple_notation, parse_port_notation, parse_tuple_notation


def iter_port_strings(input_refs: Iterable[str]) -> Iterable[str]:
    """Yield individual port strings, expanding tuple notation."""
    for input_ref in input_refs:
        if is_tuple_notation(input_ref):
            yield from parse_tuple_notation(input_ref)
        else:
            yield input_ref


def find_named_output_producer(
    units: Dict[str, object],
    output_name: str,
) -> Optional[Tuple[str, int]]:
    """Return the producing unit ID and output index for a declared output name."""
    for unit_id, config in units.items():
        outputs = getattr(config, "outputs", None) or []
        for idx, name in enumerate(outputs):
            if name == output_name:
                return unit_id, idx
    return None


def get_named_output_model(
    units: Dict[str, object],
    output_name: str,
) -> Optional[str]:
    """Infer the effective model of a declared named output stream."""
    producer = find_named_output_producer(units, output_name)
    if not producer:
        return None

    unit_id, _ = producer
    config = units[unit_id]
    junction_transform = get_junction_output_model(getattr(config, "unit_type", ""))
    if junction_transform:
        return normalize_model_name(junction_transform[1])

    model_type = getattr(config, "model_type", None)
    if model_type:
        return normalize_model_name(model_type)
    return None


def infer_connection_stream_id(
    units: Dict[str, object],
    from_port: str,
) -> Optional[str]:
    """Infer a deferred connection's stream name from the source unit outputs."""
    try:
        ref = parse_port_notation(from_port)
    except ValueError:
        return None

    if ref.port_type == "direct":
        unit_id = ref.unit_id
        output_index = ref.index if ref.index >= 0 else 0
    elif ref.port_type == "output":
        unit_id = ref.unit_id
        output_index = ref.index
    else:
        return None

    config = units.get(unit_id)
    if not config:
        return None

    outputs = getattr(config, "outputs", None) or []
    if 0 <= output_index < len(outputs):
        stream_id = outputs[output_index]
        return stream_id or None
    return None


def get_input_model(
    session,
    port_str: str,
    _depth: int = 0,
) -> str:
    """
    Resolve the effective model arriving at a single input reference.

    Supports normal port notation (`U1-0`), direct stream IDs, and declared
    named outputs from upstream units.
    """
    if _depth > 20:
        raise ValueError(
            "Junction chain too deep (>20 levels). Possible cycle detected in flowsheet. "
            "Check for circular unit connections involving junctions."
        )

    try:
        ref = parse_port_notation(port_str)
    except ValueError:
        return normalize_model_name(session.primary_model_type)

    upstream_id = ref.unit_id

    if upstream_id in session.units:
        upstream_config = session.units[upstream_id]
        junction_transform = get_junction_output_model(upstream_config.unit_type)
        if junction_transform:
            return normalize_model_name(junction_transform[1])
        if upstream_config.model_type:
            return normalize_model_name(upstream_config.model_type)
        if upstream_config.inputs:
            input_models = [
                get_input_model(session, child_ref, _depth + 1)
                for child_ref in iter_port_strings(upstream_config.inputs)
            ]
            if input_models:
                return input_models[0]
        return normalize_model_name(session.primary_model_type)

    if upstream_id in session.streams:
        stream_config = session.streams[upstream_id]
        if stream_config.model_type:
            return normalize_model_name(stream_config.model_type)
        return normalize_model_name(session.primary_model_type)

    named_output_model = get_named_output_model(session.units, upstream_id)
    if named_output_model:
        return named_output_model

    return normalize_model_name(session.primary_model_type)


def compute_effective_model(
    session,
    unit_inputs: Iterable[str],
    explicit_model: Optional[str] = None,
) -> tuple[str, list[str]]:
    """
    Compute the effective model at a unit based on explicit override or inputs.
    """
    warnings: list[str] = []

    if explicit_model:
        return normalize_model_name(explicit_model), warnings

    input_models = [get_input_model(session, port_str) for port_str in iter_port_strings(unit_inputs)]
    unique_models = sorted(set(input_models))

    if len(unique_models) > 1:
        warnings.append(
            f"Multiple input models detected: {unique_models}. "
            f"Consider adding junctions to unify component sets before mixing."
        )

    if input_models:
        return input_models[0], warnings
    return normalize_model_name(session.primary_model_type), warnings
