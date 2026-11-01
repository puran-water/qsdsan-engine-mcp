"""
Compatibility helpers for the current QSDsan/BioSTEAM stack.
"""

from __future__ import annotations

import time


def ensure_qsdsan_processes_load_from_file() -> None:
    """
    Patch ``Processes.load_from_file`` for Python 3 ``dict_keys`` semantics.

    Some QSDsan releases still index ``stoichio.keys()[0]``, which raises on
    modern Python because ``dict_keys`` is no longer subscriptable.
    """
    import pandas as pd
    from qsdsan import Process
    from qsdsan import _process as process_module

    current = process_module.Processes.load_from_file
    if getattr(current, "_qsdsan_engine_compat", False):
        return

    @classmethod
    def load_from_file_compat(
        cls,
        path=None,
        data=None,
        components=None,
        conserved_for=("COD", "N", "P", "charge"),
        parameters=(),
        use_default_data=False,
        store_data=False,
        compile=True,
        **compile_kwargs,
    ):
        if use_default_data and cls._default_data is not None:
            data = cls._default_data
        elif path:
            data = process_module.load_data(path=path, index_col=0, na_values=0)
        else:
            if data is None:
                return None

        cmps = process_module._load_components(components)

        cmp_ids = [i for i in data.columns if i in cmps.IDs]
        data.dropna(how="all", subset=cmp_ids, inplace=True)
        new = cls(())
        for process_id, proc in data.iterrows():
            stoichio = proc[cmp_ids]
            if data.columns[-1] in cmp_ids:
                rate_eq = None
            else:
                rate_eq = None if pd.isna(proc.iloc[-1]) else proc.iloc[-1]
            stoichio = stoichio[-pd.isna(stoichio)].to_dict()
            ref = None
            for key, value in stoichio.items():
                try:
                    value = stoichio[key] = float(value)
                    if ref is None and value in (-1, 1):
                        ref = key
                except Exception:
                    continue
            if ref is None:
                ref = next(iter(stoichio))
            process = Process(
                process_id,
                stoichio,
                ref_component=ref,
                rate_equation=rate_eq,
                components=cmps,
                conserved_for=conserved_for,
                parameters=parameters,
            )
            new.append(process)

        if store_data:
            cls._default_data = data

        if compile:
            new.compile(**compile_kwargs)
        return new

    load_from_file_compat._qsdsan_engine_compat = True
    process_module.Processes.load_from_file = load_from_file_compat


def ensure_biosteam_tictoc() -> None:
    """
    Recreate ``biosteam.utils.TicToc`` when newer BioSTEAM releases omit it.

    QSDsan still imports this symbol for timing decorators. The replacement only
    implements the tiny API QSDsan uses: ``tic()`` and ``elapsed_time``.
    """
    import biosteam.utils as biosteam_utils

    if hasattr(biosteam_utils, "TicToc"):
        return

    class TicToc:
        def __init__(self) -> None:
            self._tic: float | None = None

        def tic(self) -> None:
            self._tic = time.perf_counter()

        @property
        def elapsed_time(self) -> float:
            if self._tic is None:
                return 0.0
            return time.perf_counter() - self._tic

    biosteam_utils.TicToc = TicToc


__all__ = [
    "ensure_biosteam_tictoc",
    "ensure_qsdsan_processes_load_from_file",
]
