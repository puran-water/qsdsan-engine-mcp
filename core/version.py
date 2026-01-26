"""
Version information for QSDsan Engine MCP.

Single source of truth for version number, importable by all modules.
"""

__version__ = "3.0.8"


def get_version_info() -> dict:
    """
    Get version information for the engine and dependencies.

    Uses importlib.metadata for fast package version lookup without
    importing heavy simulation modules (avoids ~18s cold start).

    Returns:
        Dict with engine_version, qsdsan_version, biosteam_version
    """
    import sys
    from importlib.metadata import version, PackageNotFoundError

    # Get QSDsan version from package metadata (fast - no module import)
    try:
        qsdsan_version = version("qsdsan")
    except PackageNotFoundError:
        qsdsan_version = "not installed"

    # Get BioSTEAM version from package metadata (fast - no module import)
    try:
        biosteam_version = version("biosteam")
    except PackageNotFoundError:
        biosteam_version = "not installed"

    return {
        "engine_version": __version__,
        "qsdsan_version": qsdsan_version,
        "biosteam_version": biosteam_version,
        "python_version": sys.version.split()[0],
    }
