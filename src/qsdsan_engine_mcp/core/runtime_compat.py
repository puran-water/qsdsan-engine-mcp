"""
Runtime patches for current third-party library behavior.

The current QSDsan release still imports ``biosteam.utils.TicToc`` while newer
BioSTEAM releases no longer export it. This module patches the missing symbol
before QSDsan modules are imported.
"""

from __future__ import annotations

import logging
import time
import warnings


_PATCHED = False


class _PintNoiseFilter(logging.Filter):
    """Drop known high-volume Pint warning chatter from upstream libs."""

    _SUPPRESSED_PREFIXES = (
        "Calling the getitem method from a UnitRegistry is deprecated.",
        "Redefining '",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(message.startswith(prefix) for prefix in self._SUPPRESSED_PREFIXES)


def apply_runtime_compat() -> None:
    """Apply in-process runtime patches exactly once."""
    global _PATCHED
    if _PATCHED:
        return

    warnings.filterwarnings(
        "ignore",
        message="pkg_resources is deprecated as an API.*",
        category=UserWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message="The property `characterization_factors` is used in biosteam/thermosteam.*",
        category=UserWarning,
    )

    pint_logger = logging.getLogger("pint.util")
    if not any(isinstance(f, _PintNoiseFilter) for f in pint_logger.filters):
        pint_logger.addFilter(_PintNoiseFilter())

    # thermosteam 0.53 defaults ID_magic=True: any Stream/System created with
    # ID='' is named by inspecting the CALLER'S SOURCE LINE for a variable
    # assignment. Source-derived names make stream IDs depend on how the code
    # was written rather than creation order — nondeterministic across
    # refactors, different from every pre-0.53 checkpoint's ticket-style IDs,
    # and paid for with per-call frame inspection. Pin ticket naming.
    try:
        import thermosteam as tmo

        if hasattr(tmo.settings, "ID_magic"):
            tmo.settings.ID_magic = False
    except Exception:
        pass

    try:
        import biosteam.utils as bst_utils
    except Exception:
        bst_utils = None

    if bst_utils is not None and not hasattr(bst_utils, "TicToc"):
        class TicToc:
            """Minimal timing object matching the QSDsan call pattern."""

            def __init__(self) -> None:
                self._start: float | None = None

            def tic(self) -> float:
                self._start = time.perf_counter()
                return self._start

            @property
            def elapsed_time(self) -> float:
                if self._start is None:
                    return 0.0
                return time.perf_counter() - self._start

        bst_utils.TicToc = TicToc
        if hasattr(bst_utils, "__all__") and "TicToc" not in bst_utils.__all__:
            bst_utils.__all__ = (*bst_utils.__all__, "TicToc")

    _PATCHED = True


__all__ = ["apply_runtime_compat"]
