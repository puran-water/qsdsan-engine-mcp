"""Select reporting evidence without interpreting absent components as zeros."""


def select_component_evidence(data, stream_ids=None, components=None):
    trajectories = data.get("streams", {})
    zeros = data.get("omitted_zero_components", {})
    unavailable = data.get("unavailable_components", {})
    known_streams = set(trajectories) | set(zeros) | set(unavailable)
    selected = set(stream_ids) if stream_ids else known_streams
    requested = set(components) if components else None
    missing_streams = set(data.get("unavailable_streams", []))
    if stream_ids:
        missing_streams = (missing_streams & selected) | (selected - known_streams)
    selected_zeros, selected_unavailable = {}, {}
    for stream_id in sorted(selected & known_streams):
        zero_ids = set(zeros.get(stream_id, []))
        missing_ids = set(unavailable.get(stream_id, []))
        if requested is not None:
            missing_ids = (missing_ids & requested) | (
                requested - set(trajectories.get(stream_id, {})) - zero_ids
            )
            zero_ids &= requested
        if zero_ids:
            selected_zeros[stream_id] = sorted(zero_ids)
        if missing_ids:
            selected_unavailable[stream_id] = sorted(missing_ids)
    return {
        "component_evidence_available": data.get("component_evidence_version") == 1,
        "omitted_zero_components": selected_zeros,
        "unavailable_streams": sorted(missing_streams),
        "unavailable_components": selected_unavailable,
        "component_findings": [
            row for row in data.get("component_findings", [])
            if row["stream_id"] in selected
            and (requested is None or row["component_id"] in requested)
        ],
        "findings_basis": "full_recorded_trajectory_before_downsampling",
    }
