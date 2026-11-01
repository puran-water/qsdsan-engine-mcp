"""
Custom wrappers around QSDsan's ADM1p <-> mASM2d A1 junction units.

These wrappers keep the upstream A1 conversion logic as the canonical basis
while fixing two practical issues for this engine:
1. Safe default model injection for flowsheet construction and standalone use.
2. Read-only stoichiometry arrays in upstream ``mASM2dtoADM1p._compile_reactions``.
"""

from __future__ import annotations

import logging
from typing import Iterable
from warnings import warn

import numpy as np

# Runtime / BioSTEAM-TicToc patches MUST run before the first ``import
# qsdsan`` — upstream ``qsdsan.utils.misc`` imports ``biosteam.utils.TicToc``
# at package-load time, which no longer exists in modern BioSTEAM.
from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc, ensure_qsdsan_processes_load_from_file

apply_runtime_compat()
ensure_biosteam_tictoc()
ensure_qsdsan_processes_load_from_file()

import qsdsan as qs
from qsdsan_engine_mcp.core.adm1p_projection import get_madm1_adm1p_shared_ids
from qsdsan import CompiledProcesses
from qsdsan.unit_operations import ADM1ptomASM2d, Junction, mASM2dtoADM1p
import qsdsan.process_models as pc


logger = logging.getLogger(__name__)

__all__ = [
    "ADM1ptomASM2d_custom",
    "mASM2dtoADM1p_custom",
    "get_canonical_adm1p_cmps",
    "get_canonical_masm2d_cmps",
    "get_canonical_adm1p_model",
    "get_canonical_masm2d_model",
    "ADM1ptomASM2d_full_custom",
    "mASM2dtoMADM1_custom",
]


def _default_adm1p_model():
    return get_canonical_adm1p_model()


def _default_masm2d_model():
    return get_canonical_masm2d_model()


def _resolve_thermo(downstream, thermo):
    if thermo is not None:
        return thermo
    if hasattr(downstream, "thermo"):
        return downstream.thermo
    if isinstance(downstream, Iterable) and not isinstance(downstream, (str, bytes)):
        for stream in downstream:
            if hasattr(stream, "thermo"):
                return stream.thermo
    return None


def get_canonical_adm1p_cmps():
    return pc.create_adm1p_cmps(set_thermo=False)


def get_canonical_masm2d_cmps():
    return pc.create_masm2d_cmps(set_thermo=False)


def get_canonical_adm1p_model():
    cmps = get_canonical_adm1p_cmps()
    qs.set_thermo(cmps)
    return pc.ADM1p(components=cmps)


def get_canonical_masm2d_model():
    cmps = get_canonical_masm2d_cmps()
    qs.set_thermo(cmps)
    return pc.mASM2d(components=cmps)


def _make_dummy_stream(cmps, ID: str):
    current = None
    try:
        current = qs.get_thermo()
    except RuntimeError:
        current = None

    try:
        qs.set_thermo(cmps)
        return qs.WasteStream(ID=ID)
    finally:
        if current is not None:
            qs.set_thermo(current.chemicals)


def _compile_bridge_reaction(base_unit_cls, *, in_cmps, out_cmps, adm1_model, asm2d_model):
    upstream = _make_dummy_stream(in_cmps, f"_tmp_{base_unit_cls.__name__}_in")
    downstream = _make_dummy_stream(out_cmps, f"_tmp_{base_unit_cls.__name__}_out")
    bridge = base_unit_cls(
        ID=f"_tmp_{base_unit_cls.__name__}",
        upstream=upstream,
        downstream=downstream,
        thermo=downstream.thermo,
        adm1_model=adm1_model,
        asm2d_model=asm2d_model,
    )
    return bridge.reactions


class ADM1ptomASM2d_custom(ADM1ptomASM2d):
    """Default-model wrapper around upstream ``ADM1ptomASM2d``."""

    def __init__(
        self,
        ID: str = "",
        upstream=None,
        downstream=(),
        thermo=None,
        init_with: str = "WasteStream",
        F_BM_default=None,
        isdynamic: bool = False,
        adm1_model=None,
        asm2d_model=None,
        **kwargs,
    ):
        self._adm1_model = None
        self._asm2d_model = None
        self._reactions = None
        self._AE = None

        Junction.__init__(
            ID=ID,
            self=self,
            upstream=upstream,
            downstream=downstream,
            thermo=_resolve_thermo(downstream, thermo),
            init_with=init_with,
            F_BM_default=F_BM_default,
            isdynamic=isdynamic,
            reactions=lambda values: values,
        )
        self._reactions = None
        self.rtol = kwargs.pop("rtol", self.rtol)
        self.atol = kwargs.pop("atol", self.atol)
        self.adm1_model = adm1_model or _default_adm1p_model()
        self.asm2d_model = asm2d_model or _default_masm2d_model()
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def asm2d_model(self):
        return self._asm2d_model

    @asm2d_model.setter
    def asm2d_model(self, model):
        if model is not None and not isinstance(model, (pc.ASM2d, pc.mASM2d)):
            raise ValueError(
                f"`asm2d_model` must be an `ASM2d`/`mASM2d` object, got {type(model).__name__}."
            )
        self._asm2d_model = model

    @property
    def adm1_model(self):
        return self._adm1_model

    @adm1_model.setter
    def adm1_model(self, model):
        if model is not None and not isinstance(model, (pc.ADM1_p_extension, pc.ADM1p, CompiledProcesses)):
            raise ValueError(
                f"`adm1_model` must be an `ADM1p`-compatible CompiledProcesses object, got {type(model).__name__}."
            )
        self._adm1_model = model


class mASM2dtoADM1p_custom(mASM2dtoADM1p):
    """Default-model wrapper around upstream ``mASM2dtoADM1p`` with stoichiometry-copy fixes."""

    def __init__(
        self,
        ID: str = "",
        upstream=None,
        downstream=(),
        thermo=None,
        init_with: str = "WasteStream",
        F_BM_default=None,
        isdynamic: bool = False,
        adm1_model=None,
        asm2d_model=None,
        **kwargs,
    ):
        self._adm1_model = None
        self._asm2d_model = None
        self._reactions = None
        self._AE = None

        Junction.__init__(
            ID=ID,
            self=self,
            upstream=upstream,
            downstream=downstream,
            thermo=_resolve_thermo(downstream, thermo),
            init_with=init_with,
            F_BM_default=F_BM_default,
            isdynamic=isdynamic,
            reactions=lambda values: values,
        )
        self._reactions = None
        self.xs_to_li = kwargs.pop("xs_to_li", self.xs_to_li)
        self.rtol = kwargs.pop("rtol", self.rtol)
        self.atol = kwargs.pop("atol", self.atol)
        self.adm1_model = adm1_model or _default_adm1p_model()
        self.asm2d_model = asm2d_model or _default_masm2d_model()
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def asm2d_model(self):
        return self._asm2d_model

    @asm2d_model.setter
    def asm2d_model(self, model):
        if model is not None and not isinstance(model, (pc.ASM2d, pc.mASM2d)):
            raise ValueError(
                f"`asm2d_model` must be an `ASM2d`/`mASM2d` object, got {type(model).__name__}."
            )
        self._asm2d_model = model

    @property
    def adm1_model(self):
        return self._adm1_model

    @adm1_model.setter
    def adm1_model(self, model):
        if model is not None and not isinstance(model, (pc.ADM1_p_extension, pc.ADM1p, CompiledProcesses)):
            raise ValueError(
                f"`adm1_model` must be an `ADM1p`-compatible CompiledProcesses object, got {type(model).__name__}."
            )
        self._adm1_model = model

    def _compile_reactions(self):
        """
        Copy the upstream A1 logic, but use writable stoichiometry arrays.

        QSDsan currently pulls several rows from pandas-backed stoichiometry
        tables and mutates them in place. In this environment those arrays are
        read-only, so we make explicit copies before scaling.
        """
        cmps_asm = self.ins[0].components
        cmps_adm = self.outs[0].components
        self.check_component_properties(cmps_asm, cmps_adm)

        _asm_ids = cmps_asm.indices(["S_F", "X_S", "X_H", "S_I", "X_I"])
        _adm_ids = cmps_adm.indices(["S_aa", "S_su", "X_pr", "X_li", "X_ch"])
        C_SF, C_XS, C_XB, C_SI, C_XI = cmps_asm.i_C[_asm_ids]
        C_aa, C_su, C_pr, C_li, C_ch = cmps_adm.i_C[_adm_ids]

        N_SF, N_XS, N_XB, N_SI, N_XI = cmps_asm.i_N[_asm_ids]
        N_aa, N_su, N_pr, N_li, N_ch = cmps_adm.i_N[_adm_ids]

        P_SF, P_XS, P_XB, P_SI, P_XI = cmps_asm.i_P[_asm_ids]
        P_aa, P_su, P_pr, P_li, P_ch = cmps_adm.i_P[_adm_ids]

        S_O2_idx, S_NO3_idx, S_A_idx, S_F_idx, X_S_idx = cmps_asm.indices(
            ["S_O2", "S_NO3", "S_A", "S_F", "X_S"]
        )

        asm = self.asm2d_model
        adm = self.adm1_model
        p1a_stoichio = np.asarray(asm.stoichiometry.loc["hetero_growth_S_A"], dtype=float).copy()
        p1a_stoichio /= abs(p1a_stoichio[S_O2_idx])
        p1f_stoichio = np.asarray(asm.stoichiometry.loc["hetero_growth_S_F"], dtype=float).copy()
        p1f_stoichio /= abs(p1f_stoichio[S_O2_idx])
        p2a_stoichio = np.asarray(asm.stoichiometry.loc["denitri_S_A"], dtype=float).copy()
        p2a_stoichio /= abs(p2a_stoichio[S_NO3_idx])
        p2f_stoichio = np.asarray(asm.stoichiometry.loc["denitri_S_F"], dtype=float).copy()
        p2f_stoichio /= abs(p2f_stoichio[S_NO3_idx])
        p3_stoichio = np.array([adm.parameters[f"f_{k}_xb"] for k in ("sI", "ch", "pr", "li", "xI")], dtype=float)

        _mmp_idx = cmps_adm.indices(
            ("X_CaCO3", "X_struv", "X_newb", "X_ACP", "X_MgCO3", "X_AlPO4", "X_FePO4")
        )
        mmp_ic = cmps_adm.i_C[_mmp_idx]
        mmp_in = cmps_adm.i_N[_mmp_idx]
        mmp_ip = cmps_adm.i_P[_mmp_idx]
        cac_sto = np.asarray(adm.stoichiometry.loc["CaCO3_precipitation_dissolution"], dtype=float).copy()
        struv_sto = np.asarray(adm.stoichiometry.loc["struvite_precipitation_dissolution"], dtype=float).copy()
        newb_sto = np.asarray(adm.stoichiometry.loc["newberyite_precipitation_dissolution"], dtype=float).copy()
        acp_sto = np.asarray(adm.stoichiometry.loc["ACP_precipitation_dissolution"], dtype=float).copy()
        mgc_sto = np.asarray(adm.stoichiometry.loc["MgCO3_precipitation_dissolution"], dtype=float).copy()
        alp_sto = np.asarray(adm.stoichiometry.loc["AlPO4_precipitation_dissolution"], dtype=float).copy()
        fep_sto = np.asarray(adm.stoichiometry.loc["FePO4_precipitation_dissolution"], dtype=float).copy()

        xs_to_li = self.xs_to_li

        def masm2d2adm1p(asm_vals):
            _asm_vals = asm_vals.copy()

            O2_coddm = _asm_vals[S_O2_idx]
            NO3_coddm = _asm_vals[S_NO3_idx]

            _asm_vals += O2_coddm * p1a_stoichio
            if _asm_vals[S_A_idx] > 0:
                _asm_vals += NO3_coddm * p2a_stoichio
                if _asm_vals[S_A_idx] < 0:
                    _asm_vals -= (_asm_vals[S_A_idx] / p2a_stoichio[S_A_idx]) * p2a_stoichio
                    NO3_coddm = _asm_vals[S_NO3_idx]
                    _asm_vals += NO3_coddm * p2f_stoichio
                    subst_cod = _asm_vals[X_S_idx] + _asm_vals[S_F_idx]
                    if subst_cod < 0:
                        _asm_vals -= (subst_cod / p2f_stoichio[S_F_idx]) * p2f_stoichio
                        _asm_vals[[S_F_idx, X_S_idx]] = 0
                        warn("not enough S_A, S_F, X_S for complete denitrification of S_NO3")
                    elif _asm_vals[S_F_idx] < 0:
                        _asm_vals[X_S_idx] += _asm_vals[S_F_idx]
                        _asm_vals[S_F_idx] = 0
            else:
                _asm_vals -= (_asm_vals[S_A_idx] / p1a_stoichio[S_A_idx]) * p1a_stoichio
                O2_coddm = _asm_vals[S_O2_idx]
                _asm_vals += O2_coddm * p1f_stoichio
                subst_cod = _asm_vals[X_S_idx] + _asm_vals[S_F_idx]
                if subst_cod < 0:
                    _asm_vals -= (subst_cod / p1f_stoichio[S_F_idx]) * p1f_stoichio
                    _asm_vals[[S_F_idx, X_S_idx]] = 0
                    warn("not enough S_A, S_F, X_S for complete consumption of S_O2 and S_NO3")
                else:
                    _asm_vals += NO3_coddm * p2f_stoichio
                    subst_cod = _asm_vals[X_S_idx] + _asm_vals[S_F_idx]
                    if subst_cod < 0:
                        _asm_vals -= (subst_cod / p2f_stoichio[S_F_idx]) * p2f_stoichio
                        _asm_vals[[S_F_idx, X_S_idx]] = 0
                        warn("not enough S_A, S_F, X_S for complete denitrification of S_NO3")
                    elif _asm_vals[S_F_idx] < 0:
                        _asm_vals[X_S_idx] += _asm_vals[S_F_idx]
                        _asm_vals[S_F_idx] = 0

            (
                S_O2,
                S_N2,
                S_NH4,
                S_NO3,
                S_PO4,
                S_F,
                S_A,
                S_I,
                S_IC,
                S_K,
                S_Mg,
                X_I,
                X_S,
                X_H,
                X_PAO,
                X_PP,
                X_PHA,
                X_AUT,
                S_Ca,
                X_CaCO3,
                X_struv,
                X_newb,
                X_ACP,
                X_MgCO3,
                X_AlOH,
                X_AlPO4,
                X_FeOH,
                X_FePO4,
                S_Na,
                S_Cl,
                H2O,
            ) = _asm_vals

            S_IN = S_NH4
            S_IP = S_PO4

            S_ND = S_F * N_SF
            req_scod = S_ND / N_aa
            if S_F < req_scod:
                S_aa = S_F
                S_su = 0
            else:
                S_aa = req_scod
                S_su = S_F - S_aa

            S_IN += S_ND - S_aa * N_aa
            S_IC += S_F * C_SF - (S_aa * C_aa + S_su * C_su)
            S_IP += S_F * P_SF

            bio = X_H + X_AUT
            _si, _ch, _pr, _li, _xi = bio * p3_stoichio
            S_IC += bio * C_XB - (_si * C_SI + _ch * C_ch + _pr * C_pr + _li * C_li + _xi * C_XI)
            S_IN += bio * N_XB - (_si * N_SI + _ch * N_ch + _pr * N_pr + _li * N_li + _xi * N_XI)
            S_IP += bio * P_XB - (_si * P_SI + _ch * P_ch + _pr * P_pr + _li * P_li + _xi * P_XI)

            X_ND = X_S * N_XS
            req_xcod = X_ND / N_pr
            if X_S < req_xcod:
                X_pr = X_S
                X_li = X_ch = 0
            else:
                X_pr = req_xcod
                X_li = xs_to_li * (X_S - X_pr)
                X_ch = (X_S - X_pr) - X_li

            S_IN += X_ND - X_pr * N_pr
            S_IC += X_S * C_XS - (X_pr * C_pr + X_li * C_li + X_ch * C_ch)
            S_IP += X_S * P_XS - (X_pr * P_pr + X_li * P_li + X_ch * P_ch)

            X_pr += _pr
            X_li += _li
            X_ch += _ch
            S_I += _si
            X_I += _xi

            S_ac = S_A

            adm_vals = np.array(
                [
                    S_su,
                    S_aa,
                    0,
                    0,
                    0,
                    0,
                    S_ac,
                    0,
                    0,
                    S_IC,
                    S_IN,
                    S_IP,
                    S_I,
                    X_ch,
                    X_pr,
                    X_li,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    X_I,
                    X_PHA,
                    X_PP,
                    X_PAO,
                    S_K,
                    S_Mg,
                    S_Ca,
                    X_CaCO3,
                    X_struv,
                    X_newb,
                    X_ACP,
                    X_MgCO3,
                    X_AlOH,
                    X_AlPO4,
                    X_FeOH,
                    X_FePO4,
                    S_Na,
                    S_Cl,
                    H2O,
                ]
            )

            if S_IC < 0:
                xc_mmp = sum(adm_vals[_mmp_idx] * mmp_ic)
                if xc_mmp > 0:
                    fraction_dissolve = max(0, min(1, -S_IC / xc_mmp))
                    adm_vals -= fraction_dissolve * X_CaCO3 * cac_sto
                    adm_vals -= fraction_dissolve * X_MgCO3 * mgc_sto
                if adm_vals[9] < 0:
                    adm_vals[9] = 0
            if S_IN < 0:
                xn_mmp = sum(adm_vals[_mmp_idx] * mmp_in)
                if xn_mmp > 0:
                    fraction_dissolve = max(0, min(1, -S_IN / xn_mmp))
                    adm_vals -= fraction_dissolve * X_struv * struv_sto
            if S_IP < 0:
                xp_mmp = sum(adm_vals[_mmp_idx] * mmp_ip)
                if xp_mmp > 0:
                    fraction_dissolve = max(0, min(1, -S_IP / xp_mmp))
                    adm_vals -= fraction_dissolve * X_struv * struv_sto
                    adm_vals -= fraction_dissolve * X_newb * newb_sto
                    adm_vals -= fraction_dissolve * X_ACP * acp_sto
                    adm_vals -= fraction_dissolve * X_AlPO4 * alp_sto
                    adm_vals -= fraction_dissolve * X_FePO4 * fep_sto

            return adm_vals

        self._reactions = masm2d2adm1p


class ADM1ptomASM2d_full_custom(ADM1ptomASM2d_custom):
    """Flowsheet wrapper that accepts full mADM1 input and projects to ADM1p core."""

    def _compile_reactions(self):
        full_cmps = self.ins[0].components
        asm_cmps = self.outs[0].components
        adm1p_cmps = get_canonical_adm1p_cmps()

        base_rxns = _compile_bridge_reaction(
            ADM1ptomASM2d_custom,
            in_cmps=adm1p_cmps,
            out_cmps=asm_cmps,
            adm1_model=self.adm1_model,
            asm2d_model=self.asm2d_model,
        )

        shared_ids = tuple(get_madm1_adm1p_shared_ids())
        input_pairs = tuple(
            (full_cmps.index(component_id), adm1p_cmps.index(component_id))
            for component_id in shared_ids
            if component_id in full_cmps.IDs
        )

        def madm1_to_masm2d(full_vals):
            adm_vals = np.zeros(len(adm1p_cmps), dtype=float)
            source = np.asarray(full_vals, dtype=float)
            for full_idx, adm_idx in input_pairs:
                adm_vals[adm_idx] = source[full_idx]
            return np.asarray(base_rxns(adm_vals), dtype=float)

        self._reactions = madm1_to_masm2d


class mASM2dtoMADM1_custom(mASM2dtoADM1p_custom):
    """Flowsheet wrapper that uses upstream A1 logic but emits full mADM1."""

    def _compile_reactions(self):
        asm_cmps = self.ins[0].components
        full_madm1_cmps = self.outs[0].components
        adm1p_cmps = get_canonical_adm1p_cmps()

        base_rxns = _compile_bridge_reaction(
            mASM2dtoADM1p_custom,
            in_cmps=asm_cmps,
            out_cmps=adm1p_cmps,
            adm1_model=self.adm1_model,
            asm2d_model=self.asm2d_model,
        )

        shared_ids = tuple(get_madm1_adm1p_shared_ids())
        output_pairs = tuple(
            (full_madm1_cmps.index(component_id), adm1p_cmps.index(component_id))
            for component_id in shared_ids
            if component_id in full_madm1_cmps.IDs
        )

        def masm2d_to_madm1(asm_vals):
            adm_core_vals = np.asarray(base_rxns(np.asarray(asm_vals, dtype=float)), dtype=float)
            full_vals = np.zeros(len(full_madm1_cmps), dtype=float)
            for full_idx, adm_idx in output_pairs:
                full_vals[full_idx] = adm_core_vals[adm_idx]
            return full_vals

        self._reactions = masm2d_to_madm1
