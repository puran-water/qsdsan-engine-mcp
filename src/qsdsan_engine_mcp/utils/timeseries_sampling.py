"""Bound retrieval sampling without changing retained engineering artifacts."""
from typing import Annotated

from pydantic import Field

MAX_TIMESERIES_POINTS = 2000
SamplingFactor = Annotated[int, Field(strict=True, ge=1)]
PointLimit = Annotated[int, Field(strict=True, ge=1, le=MAX_TIMESERIES_POINTS)]


def validate_sampling_controls(downsample_factor, max_points):
    """Validate direct callers too; legacy None selects the hard point cap."""
    if type(downsample_factor) is not int or downsample_factor < 1:
        raise ValueError("downsample_factor must be a positive integer")
    if max_points is None:
        max_points = MAX_TIMESERIES_POINTS
    if type(max_points) is not int or not 1 <= max_points <= MAX_TIMESERIES_POINTS:
        raise ValueError(f"max_points must be an integer from 1 to {MAX_TIMESERIES_POINTS}, or None for the hard cap")
    return downsample_factor, max_points


def bounded_stride(point_count, downsample_factor, max_points):
    """Coarsen by whole multiples of the requested stride, preserving alignment."""
    sampled_count = (point_count + downsample_factor - 1) // downsample_factor
    extra_factor = max(1, (sampled_count + max_points - 1) // max_points)
    return downsample_factor * extra_factor
