"""
Topological Sort - Unit execution order with recycle edge handling.

This module provides topological sorting for flowsheet units while properly
handling recycle streams (back-edges) that would otherwise create cycles.

Usage:
    from utils.topo_sort import (
        topological_sort,
        detect_recycle_streams,
        validate_flowsheet_connectivity,
    )

    # Sort units with explicit recycles
    result = topological_sort(units, connections, recycle_stream_ids={"RAS"})
    print(result.unit_order)  # ['A1', 'O1', 'MBR', 'SP']

    # Auto-detect recycles
    recycles = detect_recycle_streams(units, connections)
"""

from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict, deque
import logging

from utils.pipe_parser import parse_port_notation, extract_unit_ids

logger = logging.getLogger(__name__)


@dataclass
class TopoSortResult:
    """
    Result of topological sort.

    Attributes:
        unit_order: Execution order of units
        recycle_edges: Edges excluded from sort (back-edges)
        has_non_recycle_cycle: True if invalid cycle detected
        warnings: Any warnings generated during sort
    """
    unit_order: List[str]
    recycle_edges: List[Tuple[str, str]] = field(default_factory=list)
    has_non_recycle_cycle: bool = False
    warnings: List[str] = field(default_factory=list)


def _build_dependency_graph(
    units: Dict[str, any],  # unit_id -> UnitConfig
    connections: List[any],  # List of ConnectionConfig
    recycle_stream_ids: Set[str],
) -> Tuple[Dict[str, Set[str]], List[Tuple[str, str]]]:
    """
    Build adjacency list from units and connections.

    Returns:
        Tuple of (adjacency_list, recycle_edges)
        adjacency_list: Dict of unit_id -> set of downstream unit_ids
        recycle_edges: List of (from_unit, to_unit) recycle edges
    """
    # adjacency: unit A -> set of units that depend on A
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    recycle_edges: List[Tuple[str, str]] = []

    # Initialize all units
    for unit_id in units:
        if unit_id not in adjacency:
            adjacency[unit_id] = set()

    # Build from unit inputs
    for unit_id, config in units.items():
        for input_ref in config.inputs:
            try:
                ref = parse_port_notation(input_ref)

                # Skip streams (they're not units)
                if ref.port_type == "stream":
                    # Check if this is a recycle stream
                    if input_ref in recycle_stream_ids:
                        # This is a recycle, we'll handle it via connections
                        pass
                    continue

                # This unit depends on ref.unit_id (the source unit)
                source_unit = ref.unit_id

                # For direct notation (U1-U2), the current unit is the target
                # so we still depend on source_unit (which is U1)
                # The target_unit_id would be the current unit in this context

                # Check if this is a recycle edge - pass the specific input_ref
                if input_ref in recycle_stream_ids or _is_recycle_edge(
                    source_unit, unit_id, recycle_stream_ids, units, input_ref=input_ref
                ):
                    recycle_edges.append((source_unit, unit_id))
                else:
                    # Normal dependency: source -> current
                    adjacency[source_unit].add(unit_id)

            except ValueError:
                # Invalid port notation, skip
                pass

    # Build from explicit connections
    for conn in connections:
        try:
            from_ref = parse_port_notation(conn.from_port)

            # Handle direct notation (U1-U2 or U1-0-1-U2) where target is in from_ref
            if from_ref.port_type == "direct" and from_ref.target_unit_id:
                from_unit = from_ref.unit_id
                to_unit = from_ref.target_unit_id

                # Check if recycle
                is_recycle = conn.stream_id in recycle_stream_ids if conn.stream_id else False

                if is_recycle:
                    recycle_edges.append((from_unit, to_unit))
                else:
                    adjacency[from_unit].add(to_unit)
                continue

            # Standard notation: parse to_port separately
            to_ref = parse_port_notation(conn.to_port)

            from_unit = from_ref.unit_id if from_ref.port_type != "stream" else None
            to_unit = to_ref.unit_id if to_ref.port_type != "stream" else None

            if from_unit and to_unit:
                # Check if recycle - use explicit stream_id if provided
                is_recycle = conn.stream_id in recycle_stream_ids if conn.stream_id else False

                if is_recycle:
                    recycle_edges.append((from_unit, to_unit))
                else:
                    adjacency[from_unit].add(to_unit)

        except ValueError:
            pass

    return dict(adjacency), recycle_edges


def _is_recycle_edge(
    from_unit: str,
    to_unit: str,
    recycle_stream_ids: Set[str],
    units: Dict[str, any],
    input_ref: str = None,
) -> bool:
    """
    Check if a SPECIFIC edge is a recycle edge.

    This is a strict check - only marks an edge as recycle if:
    1. The specific input_ref matches a recycle stream ID, OR
    2. The input notation explicitly references the from_unit and is in recycle_stream_ids

    Does NOT mark edges just because the target unit happens to have ANY recycle input.

    Args:
        from_unit: Source unit ID
        to_unit: Destination unit ID
        recycle_stream_ids: Set of stream IDs known to be recycles
        units: Dict of unit_id -> UnitConfig
        input_ref: The specific input reference being checked

    Returns:
        True only if this specific edge is a recycle
    """
    # If we have a specific input_ref, check if IT is a recycle stream
    if input_ref and input_ref in recycle_stream_ids:
        return True

    # Check if this specific edge (from_unit -> to_unit) involves a recycle stream
    if to_unit in units:
        to_config = units[to_unit]
        for inp in to_config.inputs:
            # Only match if the input explicitly references from_unit
            # AND is in recycle_stream_ids
            if inp in recycle_stream_ids:
                try:
                    ref = parse_port_notation(inp)
                    if ref.port_type != "stream" and ref.unit_id == from_unit:
                        return True
                except ValueError:
                    # If it's a plain stream name in recycle_stream_ids,
                    # it doesn't indicate this specific edge is recycle
                    pass

    return False


def topological_sort(
    units: Dict[str, any],  # unit_id -> UnitConfig
    connections: List[any],  # List of ConnectionConfig
    recycle_stream_ids: Optional[Set[str]] = None,
    manual_order: Optional[List[str]] = None,
) -> TopoSortResult:
    """
    Topological sort of units with recycle handling.

    Algorithm:
    1. Build dependency graph from units and connections
    2. Mark edges involving recycle_stream_ids as "recycle edges"
    3. Exclude recycle edges from cycle detection
    4. Perform Kahn's algorithm on remaining DAG
    5. Return execution order

    Args:
        units: Dict of unit_id -> UnitConfig
        connections: List of ConnectionConfig
        recycle_stream_ids: Stream IDs known to be recycles
        manual_order: If provided, validate and use this order instead

    Returns:
        TopoSortResult with unit order and detected recycle edges

    Example:
        # MLE flowsheet: A1 -> O1 -> MBR -> SP
        #                ^___________RAS__|
        result = topological_sort(units, connections, {"RAS"})
        # result.unit_order = ["A1", "O1", "MBR", "SP"]
        # result.recycle_edges = [("SP", "A1")]
    """
    if recycle_stream_ids is None:
        recycle_stream_ids = set()

    warnings = []

    # If manual order provided, validate and return
    if manual_order is not None:
        unit_ids = set(units.keys())
        manual_set = set(manual_order)

        missing = unit_ids - manual_set
        extra = manual_set - unit_ids

        if missing:
            warnings.append(f"Manual order missing units: {missing}")
        if extra:
            warnings.append(f"Manual order has unknown units: {extra}")

        # Use manual order, adding any missing units at the end
        order = list(manual_order)
        for uid in units:
            if uid not in manual_set:
                order.append(uid)

        return TopoSortResult(
            unit_order=order,
            recycle_edges=[],
            has_non_recycle_cycle=False,
            warnings=warnings,
        )

    # Build dependency graph
    adjacency, recycle_edges = _build_dependency_graph(
        units, connections, recycle_stream_ids
    )

    # Compute in-degrees (excluding recycle edges)
    in_degree: Dict[str, int] = {uid: 0 for uid in units}
    for from_unit, downstream in adjacency.items():
        for to_unit in downstream:
            if to_unit in in_degree:
                in_degree[to_unit] += 1

    # Kahn's algorithm
    queue = deque()
    for uid, degree in in_degree.items():
        if degree == 0:
            queue.append(uid)

    order = []
    while queue:
        unit_id = queue.popleft()
        order.append(unit_id)

        for downstream in adjacency.get(unit_id, []):
            if downstream in in_degree:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

    # Check for cycles
    has_cycle = len(order) != len(units)

    if has_cycle:
        remaining = [uid for uid in units if uid not in order]
        warnings.append(
            f"Non-recycle cycle detected involving units: {remaining}. "
            "Check connections or add to recycle_stream_ids."
        )

        # Add remaining units at end (best effort)
        order.extend(remaining)

    return TopoSortResult(
        unit_order=order,
        recycle_edges=recycle_edges,
        has_non_recycle_cycle=has_cycle,
        warnings=warnings,
    )


def detect_recycle_streams(
    units: Dict[str, any],  # unit_id -> UnitConfig
    connections: List[any],  # List of ConnectionConfig
) -> Set[str]:
    """
    Auto-detect potential recycle streams by finding back-edges.

    Uses DFS to find edges that point to already-visited nodes.
    These are candidates for recycle streams.

    Args:
        units: Dict of unit_id -> UnitConfig
        connections: List of ConnectionConfig

    Returns:
        Set of stream IDs that appear to be recycles
    """
    # Build full adjacency (including potential recycles)
    adjacency: Dict[str, Set[str]] = defaultdict(set)

    for unit_id, config in units.items():
        for input_ref in config.inputs:
            try:
                ref = parse_port_notation(input_ref)
                if ref.port_type != "stream":
                    adjacency[ref.unit_id].add(unit_id)
            except ValueError:
                pass

    for conn in connections:
        try:
            from_ref = parse_port_notation(conn.from_port)
            to_ref = parse_port_notation(conn.to_port)
            if from_ref.port_type != "stream" and to_ref.port_type != "stream":
                adjacency[from_ref.unit_id].add(to_ref.unit_id)
        except ValueError:
            pass

    # DFS to find back-edges
    visited = set()
    rec_stack = set()  # Nodes in current DFS path
    back_edges: Set[Tuple[str, str]] = set()

    def dfs(node: str):
        visited.add(node)
        rec_stack.add(node)

        for neighbor in adjacency.get(node, []):
            if neighbor in rec_stack:
                # Back edge found (cycle)
                back_edges.add((node, neighbor))
            elif neighbor not in visited:
                dfs(neighbor)

        rec_stack.remove(node)

    for unit_id in units:
        if unit_id not in visited:
            dfs(unit_id)

    # Convert back edges to stream IDs (heuristic)
    recycle_streams = set()
    for from_unit, to_unit in back_edges:
        # Look for named connections
        for conn in connections:
            try:
                from_ref = parse_port_notation(conn.from_port)
                to_ref = parse_port_notation(conn.to_port)
                if (from_ref.unit_id == from_unit and
                    to_ref.unit_id == to_unit and
                    conn.stream_id):
                    recycle_streams.add(conn.stream_id)
            except ValueError:
                pass

        # Also check unit inputs
        if to_unit in units:
            for inp in units[to_unit].inputs:
                if "-" not in inp:  # Likely a stream name
                    recycle_streams.add(inp)

    logger.info(f"Detected {len(back_edges)} back-edges, {len(recycle_streams)} recycle streams")
    return recycle_streams


def validate_flowsheet_connectivity(
    units: Dict[str, any],  # unit_id -> UnitConfig
    streams: Dict[str, any],  # stream_id -> StreamConfig
    connections: List[any],  # List of ConnectionConfig
) -> Tuple[List[str], List[str]]:
    """
    Validate flowsheet connectivity.

    Checks:
    - All units are connected
    - All streams are used
    - No dangling references

    Args:
        units: Dict of unit_id -> UnitConfig
        streams: Dict of stream_id -> StreamConfig
        connections: List of ConnectionConfig

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    # Track referenced units and streams
    referenced_units = set()
    referenced_streams = set()

    # Check unit inputs
    for unit_id, config in units.items():
        for input_ref in config.inputs:
            try:
                ref = parse_port_notation(input_ref)
                if ref.port_type == "stream":
                    referenced_streams.add(ref.unit_id)
                else:
                    referenced_units.add(ref.unit_id)
            except ValueError as e:
                errors.append(f"Unit '{unit_id}' has invalid input '{input_ref}': {e}")

    # Check connections
    for conn in connections:
        try:
            from_ref = parse_port_notation(conn.from_port)
            if from_ref.port_type != "stream":
                referenced_units.add(from_ref.unit_id)
        except ValueError as e:
            errors.append(f"Invalid connection from '{conn.from_port}': {e}")

        try:
            to_ref = parse_port_notation(conn.to_port)
            if to_ref.port_type != "stream":
                referenced_units.add(to_ref.unit_id)
        except ValueError as e:
            errors.append(f"Invalid connection to '{conn.to_port}': {e}")

    # Check for missing units
    for ref_unit in referenced_units:
        if ref_unit not in units:
            errors.append(f"Referenced unit '{ref_unit}' not found in session")

    # Check for missing streams
    for ref_stream in referenced_streams:
        if ref_stream not in streams:
            # Could be a recycle stream created later
            warnings.append(
                f"Referenced stream '{ref_stream}' not found in session. "
                "Ensure it's created or is a recycle stream."
            )

    # Check for unused streams (excluding recycle streams)
    unused_streams = set(streams.keys()) - referenced_streams
    for stream_id in unused_streams:
        if streams[stream_id].stream_type != "recycle":
            warnings.append(f"Stream '{stream_id}' is not used by any unit")

    # Check for disconnected units (no inputs)
    for unit_id, config in units.items():
        if not config.inputs:
            # Only warn if not a source unit
            warnings.append(
                f"Unit '{unit_id}' has no inputs. "
                "Ensure this is intentional (source unit)."
            )

    return errors, warnings


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'TopoSortResult',
    'topological_sort',
    'detect_recycle_streams',
    'validate_flowsheet_connectivity',
]
