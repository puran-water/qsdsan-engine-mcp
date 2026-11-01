"Pure functions; no engine imports. The AD state arrives component-keyed in\nthe reactor's internal kg/m3 basis (checkpoint units.AD state zipped with\ncomponent_ids — see the OH smoke's _ad_state helper)."

from typing import Any, Dict, Optional

__all__ = ["digester_effluent_to_basin_feed"]

#: soluble COD components the UF passes (feed-side + fermentation products)
_SOLUBLE_COD_KEYS = ("S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac",
                     "S_I")


def digester_effluent_to_basin_feed(ad_state_kg_m3: Dict[str, float],
                                    flow_m3_d: float,
                                    labbases: Optional[Dict[str, Any]] = None
                                    ) -> Dict[str, Any]:
    """Map a digester twin state to the basin-twin feed record.

    Returns the per-day feed fields the nitrification twin's forcing bundle
    consumes (``feed_daily`` vocabulary): flow, NH4-N, alkalinity, plus the
    ortho-P / sulfide / sCOD riders the FeCl3 what-ifs move. Alkalinity comes
    from the model's like-basis lab value when ``labbases`` is supplied (the
    titration the plant actually compares against), else from a charge-free
    S_IC bicarbonate approximation — labelled accordingly.
    """
    def kg(key):
        return float(ad_state_kg_m3.get(key, 0.0) or 0.0)

    if labbases and labbases.get("alk_titre_mg_CaCO3_L") is not None:
        # the titre to pH 4.30 — the LIKE-BASIS number a plant lab reports
        # (codex closure audit: alk_mg_CaCO3_L is TOTAL model ANC, a
        # different, larger quantity — it was mislabeled like-basis here)
        alk = float(labbases["alk_titre_mg_CaCO3_L"])
        alk_basis = "model alkalinity titre to pH 4.30 (like-basis)"
    elif labbases and labbases.get("alk_mg_CaCO3_L") is not None:
        alk = float(labbases["alk_mg_CaCO3_L"])
        alk_basis = "total model ANC (NOT a lab-titre like-basis value)"
    else:
        # S_IC is kg C/m3; near pH 7 essentially all inorganic carbon is
        # HCO3- => ~50.04/12.011 mg CaCO3 per mg C. A screening approximation.
        alk = kg("S_IC") * 1000.0 * (50.04 / 12.011)
        alk_basis = "S_IC bicarbonate approximation (screening)"

    scod = sum(kg(k) for k in _SOLUBLE_COD_KEYS) * 1000.0
    out = {
        "flow_m3_d": float(flow_m3_d),
        "nh4_n_mg_L": kg("S_IN") * 1000.0,
        "alkalinity_mg_CaCO3_L": alk,
        "alkalinity_basis": alk_basis,
        "ortho_p_mg_L": kg("S_IP") * 1000.0,
        "sulfide_mg_S_L": kg("S_IS") * 1000.0,
        "scod_mg_L": scod,
        "n_load_kg_d": kg("S_IN") * float(flow_m3_d),
        "alk_load_kg_CaCO3_d": alk * float(flow_m3_d) / 1000.0,
        "basis_note": ("anaerobic UF permeate = basin influent; SOLUBLE slice "
                       "only (particulates + minerals retained by the membrane)"),
    }
    # the number the alkalinity-short basin cares about: CaCO3 per mg N at the
    # 7.14 nitrification demand (>=7.14 means the permeate carries enough)
    if out["nh4_n_mg_L"] > 0:
        out["alk_to_n_ratio"] = alk / out["nh4_n_mg_L"]
    else:
        out["alk_to_n_ratio"] = None
    return out
