"""
Pipe Parser - Parse BioSTEAM/QSDsan pipe notation for flowsheet construction.

This module parses string-based pipe notation into port references that can
be resolved to actual QSDsan unit ports. Supports full BioSTEAM syntax.

Supported notations:
    - "A1-0"      → Output port 0 of unit A1
    - "1-M1"      → Input port 1 of unit M1
    - "U1-U2"     → Direct connection: U1.outs[0] → U2.ins[0]
    - "U1-0-1-U2" → Explicit: U1.outs[0] → U2.ins[1]
    - "influent"  → Named stream
    - "(A1-0, B1-0)" → Tuple fan-in notation

Usage:
    from utils.pipe_parser import (
        parse_port_notation,
        resolve_port,
        parse_tuple_notation,
    )

    # Parse notation to reference
    ref = parse_port_notation("A1-0")
    # ref.unit_id = "A1", ref.port_type = "output", ref.index = 0

    # Resolve to actual QSDsan object
    port = resolve_port("A1-0", unit_registry, stream_registry)
"""

from typing import Union, List, Optional, Any
from dataclasses import dataclass


@dataclass
class PortReference:
    """
    Parsed port reference.

    Attributes:
        unit_id: Unit identifier or stream name
        port_type: One of "input", "output", "stream", "direct"
        index: Port index (-1 for streams)
        target_unit_id: For direct connections (U1-U2), the target unit
        target_index: For explicit port mapping (U1-0-1-U2), the input index
    """
    unit_id: str
    port_type: str  # "input", "output", "stream", "direct"
    index: int = -1  # -1 for streams, port index otherwise
    target_unit_id: Optional[str] = None  # For direct U1-U2 connections
    target_index: Optional[int] = None  # For explicit U1-0-1-U2 connections


def parse_port_notation(port_str: str) -> PortReference:
    """
    Parse pipe notation string with full BioSTEAM syntax support.

    Supported notations:
        "A1-0"      → Output port 0 of unit A1
        "1-M1"      → Input port 1 of unit M1
        "U1-U2"     → Direct connection: U1.outs[0] → U2.ins[0]
        "U1-0-1-U2" → Explicit: U1.outs[0] → U2.ins[1]
        "influent"  → Named stream

    Args:
        port_str: Port notation string

    Returns:
        PortReference with parsed details

    Raises:
        ValueError: If notation cannot be parsed

    Examples:
        >>> parse_port_notation("A1-0")
        PortReference(unit_id='A1', port_type='output', index=0)

        >>> parse_port_notation("1-M1")
        PortReference(unit_id='M1', port_type='input', index=1)

        >>> parse_port_notation("U1-U2")
        PortReference(unit_id='U1', port_type='direct', index=0, target_unit_id='U2')

        >>> parse_port_notation("U1-0-1-U2")
        PortReference(unit_id='U1', port_type='direct', index=0, target_unit_id='U2', target_index=1)

        >>> parse_port_notation("influent")
        PortReference(unit_id='influent', port_type='stream', index=-1)
    """
    port_str = port_str.strip()

    if not port_str:
        raise ValueError("Empty port notation")

    # Check for tuple notation (handled separately)
    if port_str.startswith("("):
        raise ValueError(
            f"Tuple notation '{port_str}' should be parsed with parse_tuple_notation()"
        )

    # No dash = plain stream name
    if "-" not in port_str:
        return PortReference(port_str, "stream", -1)

    parts = port_str.split("-")

    if len(parts) == 2:
        # Could be: "A1-0", "1-M1", or "U1-U2"
        left, right = parts

        # Check "1-M1" (input notation: number-unit)
        if left.isdigit() and not right.isdigit():
            return PortReference(
                unit_id=right,
                port_type="input",
                index=int(left),
            )

        # Check "A1-0" (output notation: unit-number)
        elif right.isdigit() and not left.isdigit():
            return PortReference(
                unit_id=left,
                port_type="output",
                index=int(right),
            )

        # Check "U1-U2" (direct unit-to-unit: both non-numeric)
        elif not left.isdigit() and not right.isdigit():
            return PortReference(
                unit_id=left,
                port_type="direct",
                index=0,  # Default output port 0
                target_unit_id=right,
                target_index=0,  # Default input port 0
            )

        else:
            # Both numeric - ambiguous
            raise ValueError(
                f"Ambiguous notation '{port_str}': "
                "both parts are numeric. Use explicit U1-0-0-U2 format."
            )

    elif len(parts) == 4:
        # Explicit port mapping: "U1-0-1-U2"
        # U1.outs[0] → U2.ins[1]
        src_unit, out_idx_str, in_idx_str, dst_unit = parts

        # Validate numeric parts
        if not out_idx_str.isdigit():
            raise ValueError(
                f"Invalid explicit port notation '{port_str}': "
                f"output index '{out_idx_str}' must be numeric"
            )
        if not in_idx_str.isdigit():
            raise ValueError(
                f"Invalid explicit port notation '{port_str}': "
                f"input index '{in_idx_str}' must be numeric"
            )

        return PortReference(
            unit_id=src_unit,
            port_type="direct",
            index=int(out_idx_str),
            target_unit_id=dst_unit,
            target_index=int(in_idx_str),
        )

    elif len(parts) == 3:
        # Could be a unit ID with dash (e.g., "A-1-0")
        # Try to interpret as "unit_with_dash-port"
        # Check if last part is numeric
        if parts[-1].isdigit():
            # Assume format: unit-with-dash-port
            unit_id = "-".join(parts[:-1])
            port_idx = int(parts[-1])
            return PortReference(
                unit_id=unit_id,
                port_type="output",
                index=port_idx,
            )
        elif parts[0].isdigit():
            # Assume format: port-unit-with-dash
            port_idx = int(parts[0])
            unit_id = "-".join(parts[1:])
            return PortReference(
                unit_id=unit_id,
                port_type="input",
                index=port_idx,
            )
        else:
            raise ValueError(
                f"Unsupported 3-part notation '{port_str}'. "
                "Expected format like 'unit-id-0' or '0-unit-id'"
            )

    else:
        raise ValueError(
            f"Unsupported pipe notation '{port_str}' with {len(parts)} parts. "
            "Supported formats: 'A1-0', '1-M1', 'U1-U2', 'U1-0-1-U2', or stream name"
        )


def resolve_port(
    port_str: str,
    unit_registry: dict,
    stream_registry: dict,
) -> Any:
    """
    Resolve port notation to actual QSDsan object.

    Args:
        port_str: Port notation or stream ID
        unit_registry: Dict of unit_id → SanUnit object
        stream_registry: Dict of stream_id → WasteStream object

    Returns:
        WasteStream for streams, or unit.ins[i]/unit.outs[i] for ports

    Raises:
        ValueError: If unit/stream not found or port index out of bounds
    """
    ref = parse_port_notation(port_str)

    if ref.port_type == "stream":
        if ref.unit_id not in stream_registry:
            available = ", ".join(sorted(stream_registry.keys())[:10])
            raise ValueError(
                f"Stream '{ref.unit_id}' not found. "
                f"Available streams: {available}{'...' if len(stream_registry) > 10 else ''}"
            )
        return stream_registry[ref.unit_id]

    # All other types need unit lookup
    if ref.unit_id not in unit_registry:
        available = ", ".join(sorted(unit_registry.keys())[:10])
        raise ValueError(
            f"Unit '{ref.unit_id}' not found. "
            f"Available units: {available}{'...' if len(unit_registry) > 10 else ''}"
        )

    unit = unit_registry[ref.unit_id]

    if ref.port_type == "output":
        if ref.index >= len(unit.outs):
            raise ValueError(
                f"Unit '{ref.unit_id}' has no output port {ref.index}. "
                f"Available: 0-{len(unit.outs)-1}"
            )
        return unit.outs[ref.index]

    elif ref.port_type == "input":
        if ref.index >= len(unit.ins):
            raise ValueError(
                f"Unit '{ref.unit_id}' has no input port {ref.index}. "
                f"Available: 0-{len(unit.ins)-1}"
            )
        return unit.ins[ref.index]

    elif ref.port_type == "direct":
        # For direct connections, return the output port
        # The caller handles wiring to target unit
        if ref.index >= len(unit.outs):
            raise ValueError(
                f"Unit '{ref.unit_id}' has no output port {ref.index}. "
                f"Available: 0-{len(unit.outs)-1}"
            )
        return unit.outs[ref.index]

    raise ValueError(f"Unknown port type: {ref.port_type}")


def parse_tuple_notation(tuple_str: str) -> List[str]:
    """
    Parse tuple fan-in/fan-out notation.

    Tuple notation is used for connecting multiple streams to a single input
    (fan-in) or splitting output to multiple streams (fan-out).

    Args:
        tuple_str: Tuple notation string like "(A1-0, B1-0)"

    Returns:
        List of individual port notation strings

    Examples:
        >>> parse_tuple_notation("(A1-0, B1-0)")
        ['A1-0', 'B1-0']

        >>> parse_tuple_notation("(effluent, WAS)")
        ['effluent', 'WAS']

        >>> parse_tuple_notation("single")
        ['single']
    """
    tuple_str = tuple_str.strip()

    if tuple_str.startswith("(") and tuple_str.endswith(")"):
        inner = tuple_str[1:-1]
        parts = [p.strip() for p in inner.split(",")]
        return [p for p in parts if p]  # Filter empty strings

    # Not tuple notation, return as single-element list
    return [tuple_str]


def is_tuple_notation(notation: str) -> bool:
    """
    Check if a string is tuple notation.

    Args:
        notation: Notation string to check

    Returns:
        True if this is tuple notation (starts with '(' and ends with ')')
    """
    notation = notation.strip()
    return notation.startswith("(") and notation.endswith(")")


def validate_port_notation(port_str: str) -> tuple[bool, Optional[str]]:
    """
    Validate port notation without resolving.

    Useful for early validation before units are created.

    Args:
        port_str: Port notation to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    try:
        parse_port_notation(port_str)
        return True, None
    except ValueError as e:
        return False, str(e)


def extract_unit_ids(port_str: str) -> List[str]:
    """
    Extract all unit IDs referenced in a port notation.

    Useful for dependency analysis.

    Args:
        port_str: Port notation string

    Returns:
        List of unit IDs (empty for stream references)

    Examples:
        >>> extract_unit_ids("A1-0")
        ['A1']

        >>> extract_unit_ids("U1-U2")
        ['U1', 'U2']

        >>> extract_unit_ids("influent")
        []
    """
    ref = parse_port_notation(port_str)

    if ref.port_type == "stream":
        return []

    ids = [ref.unit_id]
    if ref.target_unit_id:
        ids.append(ref.target_unit_id)

    return ids


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'PortReference',
    'parse_port_notation',
    'resolve_port',
    'parse_tuple_notation',
    'is_tuple_notation',
    'validate_port_notation',
    'extract_unit_ids',
]
