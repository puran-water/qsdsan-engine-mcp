"""Canonical component catalog for public QSDsan stream-state contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from qsdsan_engine_mcp.core.model_types import ModelType

ComponentBasis = Literal["mass", "COD", "N", "P", "C", "S"]


@dataclass(frozen=True)
class ComponentSpec:
    """Public metadata and contract basis for one model component."""

    id: str
    name: str
    category: str
    basis: ComponentBasis = "mass"
    description: str | None = None
    typical_domestic_mg_L: float | None = None
    typical_range: tuple[float, float] | None = None
    required: bool = True

    def to_tool_dict(self, *, include_typical_values: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "basis": self.basis,
        }
        if include_typical_values and self.typical_domestic_mg_L is not None:
            payload["typical_domestic_mg_L"] = self.typical_domestic_mg_L
        return payload

    def to_resource_dict(self, *, units: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or self.name,
            "category": self.category,
            "basis": self.basis,
            "units": units,
            "typical_domestic_mg_L": self.typical_domestic_mg_L,
            "typical_range": self.typical_range,
            "required": self.required,
        }


MODEL_COMPONENTS: dict[str, tuple[str, ...]] = {
    "mADM1": (
        "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac", "S_h2", "S_ch4",
        "S_IC", "S_IN", "S_IP", "S_I", "X_ch", "X_pr", "X_li", "X_su", "X_aa",
        "X_fa", "X_c4", "X_pro", "X_ac", "X_h2", "X_I", "X_PHA", "X_PP", "X_PAO",
        "S_K", "S_Mg", "S_SO4", "S_IS", "X_hSRB", "X_aSRB", "X_pSRB", "X_c4SRB",
        "S_S0", "S_Fe3", "S_Fe2", "X_HFO_H", "X_HFO_L", "X_HFO_old", "X_HFO_HP",
        "X_HFO_LP", "X_HFO_HP_old", "X_HFO_LP_old", "S_Ca", "S_Al", "X_CCM",
        "X_ACC", "X_ACP", "X_HAP", "X_DCPD", "X_OCP", "X_struv", "X_newb",
        "X_magn", "X_kstruv", "X_FeS", "X_Mackinawite", "X_Fe3PO42",
        "X_Siderite", "X_AlPO4", "S_Na", "S_Cl",
        "S_Co", "S_Ni", "S_Se", "H2O",
    ),
    "ASM2d": (
        "S_O2", "S_F", "S_A", "S_I", "S_NH4", "S_N2", "S_NO3", "S_PO4", "S_ALK",
        "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AUT", "X_MeOH", "X_MeP",
        "H2O",
    ),
    "ASM1": (
        "S_I", "S_S", "X_I", "X_S", "X_BH", "X_BA", "X_P", "S_O", "S_NO", "S_NH",
        "S_ND", "X_ND", "S_ALK",
    ),
    "mASM2d": (
        "S_O2", "S_N2", "S_NH4", "S_NO3", "S_PO4", "S_F", "S_A", "S_I", "S_IC",
        "S_K", "S_Mg", "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AUT",
        "S_Ca", "S_Na", "S_Cl", "X_CaCO3", "X_struv", "X_newb", "X_ACP", "X_MgCO3",
        "X_AlOH", "X_AlPO4", "X_FeOH", "X_FePO4", "H2O",
    ),
    # Two-step nitrification variant: +S_NO2, X_AUT -> X_AOB + X_NOB. Order matches
    # models.masm2d.create_masm2d_2n_components (the runtime component order).
    "mASM2d2N": (
        "S_O2", "S_N2", "S_NH4", "S_NO3", "S_NO2", "S_PO4", "S_F", "S_A", "S_I", "S_IC",
        "S_K", "S_Mg", "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AOB", "X_NOB",
        "S_Ca", "X_CaCO3", "X_struv", "X_newb", "X_ACP", "X_MgCO3", "X_AlOH", "X_AlPO4",
        "X_FeOH", "X_FePO4", "S_Na", "S_Cl", "H2O",
    ),
    # Two-step nitrification + AOB N2O variant: +S_NH2OH, S_NO, S_N2O (AOB electron-chain
    # intermediates) after S_NO2. Order matches models.masm2d_n2o.create_masm2d_2n_n2o_components.
    "mASM2d2N_N2O": (
        "S_O2", "S_N2", "S_NH4", "S_NO3", "S_NO2", "S_NH2OH", "S_NO", "S_N2O", "S_PO4",
        "S_F", "S_A", "S_I", "S_IC", "S_K", "S_Mg", "X_I", "X_S", "X_H", "X_PAO", "X_PP",
        "X_PHA", "X_AOB", "X_NOB", "S_Ca", "X_CaCO3", "X_struv", "X_newb", "X_ACP",
        "X_MgCO3", "X_AlOH", "X_AlPO4", "X_FeOH", "X_FePO4", "S_Na", "S_Cl", "H2O",
    ),
}

def madm1_component_ids(*, organic_sulfur: bool = False, microaeration: bool = False) -> tuple[str, ...]:
    "``microaeration=True`` is the OPT-IN micro-aeration variant (``process_model_schema.madm1_microaeration``,\n    PUBLIC-RULE-6dce80fc160d): its liquid component (S_O2) follows X_Sorg, still before H2O. It\n    stacks on the organic-sulfur variant (the kernel's statement), so asking for it alone is refused."
    core = MODEL_COMPONENTS["mADM1"]
    if microaeration is True and organic_sulfur is not True:
        raise ValueError("the micro-aeration variant stacks on the organic-sulfur variant: ask for both "
                         "(organic_sulfur=True, microaeration=True)")
    if organic_sulfur is not True:
        return core
    from puran_mcp_support.schema.madm1_organic_sulfur import MADM1_ORGANIC_SULFUR_EXTENSION

    extension_ids = MADM1_ORGANIC_SULFUR_EXTENSION.component_ids
    if microaeration is True:
        from puran_mcp_support.schema.madm1_microaeration import MADM1_MICROAERATION_EXTENSION

        extension_ids = (*extension_ids, *MADM1_MICROAERATION_EXTENSION.component_ids)
    return (*core[:-1], *extension_ids, core[-1])


# mADM1 trace-micronutrient metals — conservative, OPTIONAL (required=False), QSDsan-only. Absent in a
# state means truly 0 (no fabricated micronutrient): feed setters MUST NOT apply the usual tiny numeric
# floor to these, and the validator MUST treat them as not-required. Single source of truth here.
TRACE_METAL_IDS: tuple[str, ...] = ("S_Co", "S_Ni", "S_Se")

MODEL_BIOGAS_COMPONENTS: dict[str, tuple[str, ...] | None] = {
    "mADM1": ("S_h2", "S_ch4", "S_IC", "S_IS"),
    "ADM1": ("S_h2", "S_ch4", "S_IC"),
    "ASM2d": None,
    "mASM2d": None,
    "mASM2d2N": None,
    "mASM2d2N_N2O": None,
    "ASM1": None,
}

MODEL_BIOMASS_COMPONENTS: dict[str, tuple[str, ...]] = {
    "mADM1": (
        "X_su", "X_aa", "X_fa", "X_c4", "X_pro", "X_ac", "X_h2",
        "X_PAO", "X_hSRB", "X_aSRB", "X_pSRB", "X_c4SRB",
    ),
    "ASM2d": ("X_H", "X_PAO", "X_AUT", "X_MeOH"),
    "mASM2d": ("X_H", "X_PAO", "X_AUT"),
    "mASM2d2N": ("X_H", "X_PAO", "X_AOB", "X_NOB"),
    "mASM2d2N_N2O": ("X_H", "X_PAO", "X_AOB", "X_NOB"),
    "ASM1": ("X_BH", "X_BA"),
}

_MODEL_ALIASES = {
    "madm1": "mADM1",
    "adm1p": "mADM1",
    "adm1_p_extension": "mADM1",
    "adm1": "ADM1",
    "asm2d": "ASM2d",
    "masm2d": "mASM2d",
    "masm2d2n": "mASM2d2N",
    "masm2d_2n": "mASM2d2N",
    "masm2d2n_n2o": "mASM2d2N_N2O",
    "masm2d_2n_n2o": "mASM2d2N_N2O",
    "asm1": "ASM1",
}

# Element-basis tokens for N/P/C/S nutrient species (mirrors QSDsan measured_as).
# This table only resolves ids that unambiguously carry a nutrient basis; every
# other component MUST be stamped explicitly in _EXPLICIT_METADATA — there is no
# heuristic fallback (see _build_catalog, which fails closed on gaps).
_BASIS_BY_TOKEN: tuple[tuple[str, ComponentBasis], ...] = (
    ("S_NH", "N"),
    ("S_NO", "N"),
    ("S_N2", "N"),
    ("S_ND", "N"),
    ("X_ND", "N"),
    ("S_IN", "N"),
    ("S_PO4", "P"),
    ("S_IP", "P"),
    ("X_PP", "P"),
    ("S_IC", "C"),
    ("S_ALK", "C"),
    ("S_SO4", "S"),
    ("S_IS", "S"),
    ("S_S0", "S"),
)


def _basis_for(component_id: str) -> ComponentBasis | None:
    """Resolve a nutrient-token basis, or None when no rule applies."""
    for token, basis in _BASIS_BY_TOKEN:
        if component_id.startswith(token) or token in component_id:
            return basis
    return None


_EXPLICIT_METADATA: dict[str, dict[str, dict[str, Any]]] = {
    "ASM2d": {
        # S_O2 is plain dissolved O2 mass (QSDsan measured_as=None), not a COD credit.
        "S_O2": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_F": {"name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75, "basis": "COD"},
        "S_A": {"name": "Acetate (VFA)", "category": "soluble", "typical_domestic_mg_L": 20, "basis": "COD"},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 30, "basis": "COD"},
        "S_NH4": {"name": "Ammonium-N", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_N2": {"name": "Dinitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO3": {"name": "Nitrate-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_PO4": {"name": "Phosphate-P", "category": "soluble", "typical_domestic_mg_L": 9},
        "S_ALK": {"name": "Alkalinity", "category": "soluble", "typical_domestic_mg_L": 300},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 50, "basis": "COD"},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 125, "basis": "COD"},
        "X_H": {"name": "Heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 30, "basis": "COD"},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": 0, "basis": "COD"},
        "X_PP": {"name": "Poly-phosphate", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": 0, "basis": "COD"},
        "X_AUT": {"name": "Autotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 0, "basis": "COD"},
        # QSDsan tracks both metal precipitates on species mass (measured_as=None).
        "X_MeOH": {"name": "Metal-hydroxides", "category": "particulate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_MeP": {"name": "Metal-phosphates", "category": "particulate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None, "basis": "mass"},
    },
    "mADM1": {
        "S_su": {"name": "Sugars", "category": "soluble", "typical_domestic_mg_L": 50, "basis": "COD"},
        "S_aa": {"name": "Amino acids", "category": "soluble", "typical_domestic_mg_L": 50, "basis": "COD"},
        "S_fa": {"name": "Fatty acids", "category": "soluble", "typical_domestic_mg_L": 100, "basis": "COD"},
        "S_va": {"name": "Valerate", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_bu": {"name": "Butyrate", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_pro": {"name": "Propionate", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_ac": {"name": "Acetate", "category": "soluble", "typical_domestic_mg_L": 200, "basis": "COD"},
        "S_h2": {"name": "Dissolved hydrogen", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_ch4": {"name": "Dissolved methane", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_IC": {"name": "Inorganic carbon", "category": "soluble", "typical_domestic_mg_L": 50, "basis": "C"},
        "S_IN": {"name": "Inorganic nitrogen", "category": "soluble", "typical_domestic_mg_L": 200, "basis": "N"},
        "S_IP": {"name": "Inorganic phosphorus", "category": "soluble", "typical_domestic_mg_L": 50, "basis": "P"},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_ch": {"name": "Carbohydrates", "category": "particulate", "typical_domestic_mg_L": 1000, "basis": "COD"},
        "X_pr": {"name": "Proteins", "category": "particulate", "typical_domestic_mg_L": 2000, "basis": "COD"},
        "X_li": {"name": "Lipids", "category": "particulate", "typical_domestic_mg_L": 1000, "basis": "COD"},
        "X_su": {"name": "Sugar-degrading biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_aa": {"name": "Amino-acid-degrading biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_fa": {"name": "LCFA-degrading biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_c4": {"name": "Valerate/butyrate-degrading biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_pro": {"name": "Propionate-degrading biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_ac": {"name": "Acetoclastic methanogens", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_h2": {"name": "Hydrogenotrophic methanogens", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "S_K": {"name": "Potassium", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Mg": {"name": "Magnesium", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_SO4": {"name": "Sulfate", "category": "soluble", "typical_domestic_mg_L": 100, "basis": "S"},
        "S_IS": {"name": "Dissolved sulfide", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "S"},
        "X_hSRB": {"name": "Sulfate-reducing biomass (H2)", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_aSRB": {"name": "Sulfate-reducing biomass (acetate)", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_pSRB": {"name": "Sulfate-reducing biomass (propionate)", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        "X_c4SRB": {"name": "Sulfate-reducing biomass (butyrate/valerate)", "category": "biomass", "typical_domestic_mg_L": None, "basis": "COD"},
        # Iron ions are measured as elemental Fe in QSDsan; catalog basis is element mass.
        "S_Fe3": {"name": "Iron (III)", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Fe2": {"name": "Iron (II)", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        # HFO sorbent phases: QSDsan carries them measured_as='Fe' (kg Fe/m3), so the
        # public basis is element mass, never COD (they are minerals, not organics).
        "X_HFO_H": {"name": "Hydrous ferric oxide, high active sites (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_L": {"name": "Hydrous ferric oxide, low active sites (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_old": {"name": "Inactive hydrous ferric oxide (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_HP": {"name": "X_HFO_H with P-bound sites (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_LP": {"name": "X_HFO_L with P-bound sites (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_HP_old": {"name": "Old X_HFO_HP (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HFO_LP_old": {"name": "Old X_HFO_LP (as Fe)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Ca": {"name": "Calcium", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Al": {"name": "Aluminum", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        # Mineral phases are tracked on species mass in QSDsan (measured_as=None).
        "X_CCM": {"name": "Calcite", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_ACC": {"name": "Aragonite", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_ACP": {"name": "Amorphous calcium phosphate", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_HAP": {"name": "Hydroxylapatite", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_DCPD": {"name": "Dicalcium phosphate", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_OCP": {"name": "Octacalcium phosphate", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_struv": {"name": "Struvite (MgNH4PO4)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_newb": {"name": "Newberyite (MgHPO4)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_magn": {"name": "Magnesite", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_kstruv": {"name": "K-struvite (MgKPO4)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_FeS": {"name": "Amorphous iron sulfide", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_Mackinawite": {"name": "Mackinawite iron sulfide", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_Fe3PO42": {"name": "Vivianite (dry formula basis)", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_Siderite": {"name": "Siderite", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "X_AlPO4": {"name": "Aluminum phosphate", "category": "precipitate", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Na": {"name": "Sodium", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        "S_Cl": {"name": "Chloride", "category": "ion", "typical_domestic_mg_L": None, "basis": "mass"},
        # Trace micronutrient metals (QSDsan-only, conservative soluble tracers driving the
        # trace-element limitation). measured_as the element; OPTIONAL (required=False) so a state
        # omitting them is valid (they default to 0) and they are never reported as "missing".
        "S_Co": {"name": "Cobalt (trace)", "category": "trace_metal", "typical_domestic_mg_L": None, "basis": "mass", "required": False},
        "S_Ni": {"name": "Nickel (trace)", "category": "trace_metal", "typical_domestic_mg_L": None, "basis": "mass", "required": False},
        "S_Se": {"name": "Selenium (trace; Se/W/Mo formate-dehydrogenase cofactor proxy)", "category": "trace_metal", "typical_domestic_mg_L": None, "basis": "mass", "required": False},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None, "basis": "mass"},
    },
    "ASM1": {
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 30, "basis": "COD"},
        "S_S": {"name": "Readily biodegradable substrate", "category": "soluble", "typical_domestic_mg_L": 70, "basis": "COD"},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 50, "basis": "COD"},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 125, "basis": "COD"},
        "X_BH": {"name": "Active heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 30, "basis": "COD"},
        "X_BA": {"name": "Active autotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 0, "basis": "COD"},
        "X_P": {"name": "Particulate products from decay", "category": "particulate", "typical_domestic_mg_L": 0, "basis": "COD"},
        # S_O is plain dissolved O2 mass (QSDsan measured_as=None), not a COD credit.
        "S_O": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_NO": {"name": "Nitrate+nitrite nitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH": {"name": "Ammonia nitrogen", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_ND": {"name": "Soluble biodegradable organic N", "category": "soluble", "typical_domestic_mg_L": 8},
        "X_ND": {"name": "Particulate biodegradable organic N", "category": "particulate", "typical_domestic_mg_L": 10},
        "S_ALK": {"name": "Alkalinity", "category": "soluble", "typical_domestic_mg_L": 300},
    },
    "mASM2d": {
        "S_O2": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_N2": {"name": "Dissolved nitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH4": {"name": "Ammonium-N", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_NO3": {"name": "Nitrate-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_PO4": {"name": "Phosphate-P", "category": "soluble", "typical_domestic_mg_L": 9},
        "S_F": {"name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75, "basis": "COD"},
        "S_A": {"name": "Acetate (VFA)", "category": "soluble", "typical_domestic_mg_L": 20, "basis": "COD"},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 10, "basis": "COD"},
        "S_IC": {"name": "Inorganic carbon", "category": "soluble", "typical_domestic_mg_L": 48, "basis": "C"},
        "S_K": {"name": "Potassium", "category": "ion", "typical_domestic_mg_L": 15, "basis": "mass"},
        "S_Mg": {"name": "Magnesium", "category": "ion", "typical_domestic_mg_L": 10, "basis": "mass"},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 120, "basis": "COD"},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 100, "basis": "COD"},
        "X_H": {"name": "Heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PP": {"name": "Poly-phosphate", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": 10, "basis": "COD"},
        "X_AUT": {"name": "Autotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "S_Ca": {"name": "Calcium", "category": "ion", "typical_domestic_mg_L": 50, "basis": "mass"},
        "S_Na": {"name": "Sodium", "category": "ion", "typical_domestic_mg_L": 80, "basis": "mass"},
        "S_Cl": {"name": "Chloride", "category": "ion", "typical_domestic_mg_L": 100, "basis": "mass"},
        "X_CaCO3": {"name": "Calcite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_struv": {"name": "Struvite (MgNH4PO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_newb": {"name": "Newberyite (MgHPO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_ACP": {"name": "Amorphous calcium phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_MgCO3": {"name": "Magnesite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlOH": {"name": "Aluminum hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlPO4": {"name": "Aluminum phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FeOH": {"name": "Iron hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FePO4": {"name": "Iron phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None, "basis": "mass"},
    },
    "mASM2d2N": {
        "S_O2": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_N2": {"name": "Dissolved nitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH4": {"name": "Ammonium-N", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_NO3": {"name": "Nitrate-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO2": {"name": "Nitrite-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_PO4": {"name": "Phosphate-P", "category": "soluble", "typical_domestic_mg_L": 9},
        "S_F": {"name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75, "basis": "COD"},
        "S_A": {"name": "Acetate (VFA)", "category": "soluble", "typical_domestic_mg_L": 20, "basis": "COD"},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 10, "basis": "COD"},
        "S_IC": {"name": "Inorganic carbon", "category": "soluble", "typical_domestic_mg_L": 48, "basis": "C"},
        "S_K": {"name": "Potassium", "category": "ion", "typical_domestic_mg_L": 15, "basis": "mass"},
        "S_Mg": {"name": "Magnesium", "category": "ion", "typical_domestic_mg_L": 10, "basis": "mass"},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 120, "basis": "COD"},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 100, "basis": "COD"},
        "X_H": {"name": "Heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PP": {"name": "Poly-phosphate", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": 10, "basis": "COD"},
        "X_AOB": {"name": "Ammonia-oxidizing bacteria", "category": "biomass", "typical_domestic_mg_L": 3, "basis": "COD"},
        "X_NOB": {"name": "Nitrite-oxidizing bacteria", "category": "biomass", "typical_domestic_mg_L": 3, "basis": "COD"},
        "S_Ca": {"name": "Calcium", "category": "ion", "typical_domestic_mg_L": 50, "basis": "mass"},
        "X_CaCO3": {"name": "Calcite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_struv": {"name": "Struvite (MgNH4PO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_newb": {"name": "Newberyite (MgHPO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_ACP": {"name": "Amorphous calcium phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_MgCO3": {"name": "Magnesite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlOH": {"name": "Aluminum hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlPO4": {"name": "Aluminum phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FeOH": {"name": "Iron hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FePO4": {"name": "Iron phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_Na": {"name": "Sodium", "category": "ion", "typical_domestic_mg_L": 80, "basis": "mass"},
        "S_Cl": {"name": "Chloride", "category": "ion", "typical_domestic_mg_L": 100, "basis": "mass"},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None, "basis": "mass"},
    },
    "mASM2d2N_N2O": {
        "S_O2": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_N2": {"name": "Dissolved nitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH4": {"name": "Ammonium-N", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_NO3": {"name": "Nitrate-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO2": {"name": "Nitrite-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH2OH": {"name": "Hydroxylamine-N (AOB intermediate)", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO": {"name": "Nitric oxide-N (AOB intermediate)", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_N2O": {"name": "Nitrous oxide-N (dissolved)", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_PO4": {"name": "Phosphate-P", "category": "soluble", "typical_domestic_mg_L": 9},
        "S_F": {"name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75, "basis": "COD"},
        "S_A": {"name": "Acetate (VFA)", "category": "soluble", "typical_domestic_mg_L": 20, "basis": "COD"},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 10, "basis": "COD"},
        "S_IC": {"name": "Inorganic carbon", "category": "soluble", "typical_domestic_mg_L": 48, "basis": "C"},
        "S_K": {"name": "Potassium", "category": "ion", "typical_domestic_mg_L": 15, "basis": "mass"},
        "S_Mg": {"name": "Magnesium", "category": "ion", "typical_domestic_mg_L": 10, "basis": "mass"},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 120, "basis": "COD"},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 100, "basis": "COD"},
        "X_H": {"name": "Heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": 5, "basis": "COD"},
        "X_PP": {"name": "Poly-phosphate", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": 10, "basis": "COD"},
        "X_AOB": {"name": "Ammonia-oxidizing bacteria", "category": "biomass", "typical_domestic_mg_L": 3, "basis": "COD"},
        "X_NOB": {"name": "Nitrite-oxidizing bacteria", "category": "biomass", "typical_domestic_mg_L": 3, "basis": "COD"},
        "S_Ca": {"name": "Calcium", "category": "ion", "typical_domestic_mg_L": 50, "basis": "mass"},
        "X_CaCO3": {"name": "Calcite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_struv": {"name": "Struvite (MgNH4PO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_newb": {"name": "Newberyite (MgHPO4)", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_ACP": {"name": "Amorphous calcium phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_MgCO3": {"name": "Magnesite", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlOH": {"name": "Aluminum hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_AlPO4": {"name": "Aluminum phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FeOH": {"name": "Iron hydroxide", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "X_FePO4": {"name": "Iron phosphate", "category": "precipitate", "typical_domestic_mg_L": 0, "basis": "mass"},
        "S_Na": {"name": "Sodium", "category": "ion", "typical_domestic_mg_L": 80, "basis": "mass"},
        "S_Cl": {"name": "Chloride", "category": "ion", "typical_domestic_mg_L": 100, "basis": "mass"},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None, "basis": "mass"},
    },
}


def normalize_model_type(model_type: str | ModelType) -> str:
    """Normalize a model identifier without importing model_registry."""
    if isinstance(model_type, ModelType):
        return model_type.value
    if not isinstance(model_type, str):
        raise TypeError(f"model_type must be a string, got {type(model_type).__name__}")
    candidate = model_type.strip()
    if candidate in MODEL_COMPONENTS or candidate == "ADM1":
        return candidate
    canonical = _MODEL_ALIASES.get(candidate.lower())
    if canonical:
        return canonical
    valid = ", ".join(list(MODEL_COMPONENTS) + ["ADM1"])
    raise ValueError(f"Unknown model type: {model_type}. Valid: {valid}")


def _default_category(component_id: str) -> str:
    if component_id == "H2O":
        return "solvent"
    if component_id.startswith("X_") and any(token in component_id for token in ("struv", "newb", "CO3", "OH", "PO4", "FeS", "CCM", "ACP", "HAP", "DCPD", "OCP", "magn")):
        return "precipitate"
    if component_id.startswith("X_") and any(token in component_id for token in ("H", "AUT", "PAO", "su", "aa", "fa", "c4", "pro", "ac", "h2", "SRB", "BH", "BA")):
        return "biomass"
    if component_id.startswith("X_"):
        return "particulate"
    if component_id in {"S_K", "S_Mg", "S_Ca", "S_Na", "S_Cl", "S_Fe2", "S_Fe3", "S_Al"}:
        return "ion"
    return "soluble"


def _build_catalog() -> dict[str, dict[str, ComponentSpec]]:
    catalog: dict[str, dict[str, ComponentSpec]] = {}
    for model_type, component_ids in MODEL_COMPONENTS.items():
        model_specs: dict[str, ComponentSpec] = {}
        metadata = _EXPLICIT_METADATA.get(model_type, {})
        for component_id in component_ids:
            meta = metadata.get(component_id, {})
            name = str(meta.get("name") or component_id)
            basis = meta.get("basis") or _basis_for(component_id)
            if basis is None:
                raise ValueError(
                    f"Component {component_id!r} of model {model_type!r} has no explicit basis. "
                    "Stamp a 'basis' entry in _EXPLICIT_METADATA (or a nutrient token in "
                    "_BASIS_BY_TOKEN) matching the QSDsan component's measured_as."
                )
            model_specs[component_id] = ComponentSpec(
                id=component_id,
                name=name,
                category=str(meta.get("category") or _default_category(component_id)),
                basis=basis,
                description=meta.get("description") or name,
                typical_domestic_mg_L=meta.get("typical_domestic_mg_L"),
                typical_range=meta.get("typical_range"),
                required=bool(meta.get("required", True)),
            )
        catalog[model_type] = model_specs
    return catalog


COMPONENT_CATALOG = _build_catalog()

STREAM_STATE_EXTRA_COMPONENTS: dict[str, dict[str, ComponentSpec]] = {
    "mASM2d": {
        "S_SO4": ComponentSpec(
            id="S_SO4",
            name="Sulfate",
            category="ion",
            basis="S",
            description="Sulfate carried only at strict handoff boundaries; QSDsan mASM2d does not solve it.",
            typical_domestic_mg_L=60,
            required=False,
        )
    }
}

def get_component_ids(model_type: str | ModelType) -> tuple[str, ...]:
    canonical = normalize_model_type(model_type)
    if canonical not in MODEL_COMPONENTS:
        raise ValueError(f"Component list not defined for {canonical}")
    return MODEL_COMPONENTS[canonical]


def get_stream_state_component_ids(model_type: str | ModelType) -> tuple[str, ...]:
    """Return components accepted at external StreamState boundaries."""

    canonical = normalize_model_type(model_type)
    extras = tuple(STREAM_STATE_EXTRA_COMPONENTS.get(canonical, {}))
    return get_component_ids(canonical) + extras


def get_component_specs(model_type: str | ModelType) -> tuple[ComponentSpec, ...]:
    canonical = normalize_model_type(model_type)
    return tuple(COMPONENT_CATALOG[canonical][component_id] for component_id in get_component_ids(canonical))


def get_handoff_only_component_specs(model_type: str | ModelType) -> tuple[ComponentSpec, ...]:
    """Return components accepted only at StreamState handoff boundaries."""

    canonical = normalize_model_type(model_type)
    return tuple(STREAM_STATE_EXTRA_COMPONENTS.get(canonical, {}).values())


def get_component_spec(model_type: str | ModelType, component_id: str) -> ComponentSpec:
    canonical = normalize_model_type(model_type)
    try:
        return COMPONENT_CATALOG[canonical][component_id]
    except KeyError as exc:
        raise ValueError(f"Component {component_id!r} is not valid for {canonical}") from exc


def get_stream_state_component_spec(model_type: str | ModelType, component_id: str) -> ComponentSpec:
    """Return model-native or handoff-extra component metadata."""

    canonical = normalize_model_type(model_type)
    if component_id in COMPONENT_CATALOG[canonical]:
        return COMPONENT_CATALOG[canonical][component_id]
    try:
        return STREAM_STATE_EXTRA_COMPONENTS[canonical][component_id]
    except KeyError as exc:
        raise ValueError(f"Component {component_id!r} is not valid for {canonical}") from exc


def get_concentration_units(model_type: str | ModelType) -> str:
    canonical = normalize_model_type(model_type)
    return "kg/m3" if canonical in {"mADM1", "ADM1"} else "mg/L"


def validate_component_ids(model_type: str | ModelType, component_ids: Iterable[str]) -> tuple[list[str], list[str]]:
    allowed = set(get_component_ids(model_type))
    provided = set(component_ids)
    missing = sorted(allowed - provided)
    extra = sorted(provided - allowed)
    return missing, extra


def basis_map_for(model_type: str | ModelType, component_ids: Iterable[str]) -> dict[str, ComponentBasis]:
    canonical = normalize_model_type(model_type)
    return {
        component_id: get_stream_state_component_spec(canonical, component_id).basis
        for component_id in component_ids
    }


__all__ = [
    "ComponentBasis",
    "ComponentSpec",
    "COMPONENT_CATALOG",
    "MODEL_BIOGAS_COMPONENTS",
    "MODEL_BIOMASS_COMPONENTS",
    "MODEL_COMPONENTS",
    "TRACE_METAL_IDS",
    "STREAM_STATE_EXTRA_COMPONENTS",
    "basis_map_for",
    "get_component_ids",
    "get_component_spec",
    "get_component_specs",
    "get_handoff_only_component_specs",
    "get_concentration_units",
    "get_stream_state_component_ids",
    "get_stream_state_component_spec",
    "madm1_component_ids",
    "normalize_model_type",
    "validate_component_ids",
]
