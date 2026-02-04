# Anaerobic CSTR mADM1 Process Parameters

This document provides a comprehensive reference for the important parameters used in the anaerobic CSTR (Continuously Stirred Tank Reactor) simulation using the modified Anaerobic Digestion Model No. 1 (mADM1).

## Table of Contents

- [Reactor Design Parameters](#reactor-design-parameters)
- [Kinetic Parameters](#kinetic-parameters)
  - [Hydrolysis Rates](#hydrolysis-rates)
  - [Maximum Uptake Rates](#maximum-uptake-rates)
  - [Half-Saturation Constants](#half-saturation-constants)
  - [Yield Coefficients](#yield-coefficients)
  - [Decay Rates](#decay-rates)
  - [Inhibition Parameters](#inhibition-parameters)
  - [SRB Parameters](#srb-sulfate-reducing-bacteria-parameters)
  - [Stoichiometric Fractions](#stoichiometric-fractions)
  - [Mass Transfer Parameters](#mass-transfer-parameters)
- [Parameter Ranges](#typical-parameter-ranges)
- [JSON Schema](#json-format)

---

## Reactor Design Parameters

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| V_liq | Liquid volume | m³ | Q × HRT |
| V_gas | Gas headspace volume | m³ | V_liq × 0.1 |
| HRT_days | Hydraulic retention time | days | 20 |
| T | Operating temperature | K | 308.15 (35°C) |
| P | Headspace pressure | Pa | 101325 |

---

## Kinetic Parameters

### Hydrolysis Rates

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| q_ch_hyd | Carbohydrate hydrolysis rate | d⁻¹ | 10.0 |
| q_pr_hyd | Protein hydrolysis rate | d⁻¹ | 10.0 |
| q_li_hyd | Lipid hydrolysis rate | d⁻¹ | 10.0 |

### Maximum Uptake Rates

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| k_su | Sugar uptake rate | d⁻¹ | 30.0 |
| k_aa | Amino acid uptake rate | d⁻¹ | 50.0 |
| k_fa | LCFA uptake rate | d⁻¹ | 6.0 |
| k_c4 | Butyrate/valerate uptake rate | d⁻¹ | 20.0 |
| k_pro | Propionate uptake rate | d⁻¹ | 13.0 |
| k_ac | Acetate uptake rate | d⁻¹ | 8.0 |
| k_h2 | Hydrogen uptake rate | d⁻¹ | 35.0 |

### Half-Saturation Constants

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| K_su | Sugar half-saturation | kg COD/m³ | 0.5 |
| K_aa | Amino acid half-saturation | kg COD/m³ | 0.3 |
| K_fa | LCFA half-saturation | kg COD/m³ | 0.4 |
| K_c4 | C4 half-saturation | kg COD/m³ | 0.2 |
| K_pro | Propionate half-saturation | kg COD/m³ | 0.1 |
| K_ac | Acetate half-saturation | kg COD/m³ | 0.15 |
| K_h2 | Hydrogen half-saturation | kg COD/m³ | 7e-6 |

### Yield Coefficients

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| Y_su | Sugar degrader yield | kg COD/kg COD | 0.10 |
| Y_aa | Amino acid degrader yield | kg COD/kg COD | 0.08 |
| Y_fa | LCFA degrader yield | kg COD/kg COD | 0.06 |
| Y_c4 | C4 degrader yield | kg COD/kg COD | 0.06 |
| Y_pro | Propionate degrader yield | kg COD/kg COD | 0.04 |
| Y_ac | Acetoclastic methanogen yield | kg COD/kg COD | 0.05 |
| Y_h2 | Hydrogenotrophic methanogen yield | kg COD/kg COD | 0.06 |

### Decay Rates

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| b_su | Sugar degrader decay | d⁻¹ | 0.02 |
| b_aa | Amino acid degrader decay | d⁻¹ | 0.02 |
| b_fa | LCFA degrader decay | d⁻¹ | 0.02 |
| b_c4 | C4 degrader decay | d⁻¹ | 0.02 |
| b_pro | Propionate degrader decay | d⁻¹ | 0.02 |
| b_ac | Acetoclastic methanogen decay | d⁻¹ | 0.02 |
| b_h2 | Hydrogenotrophic methanogen decay | d⁻¹ | 0.02 |

### Inhibition Parameters

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| KI_h2_fa | H₂ inhibition on LCFA degraders | kg COD/m³ | 5e-6 |
| KI_h2_c4 | H₂ inhibition on C4 degraders | kg COD/m³ | 1e-5 |
| KI_h2_pro | H₂ inhibition on propionate degraders | kg COD/m³ | 3.5e-6 |
| KI_nh3 | Free ammonia inhibition | M | 1.8e-3 |
| KI_h2s_ac | H₂S inhibition on acetogens | kg S/m³ | 0.460 |
| KI_h2s_h2 | H₂S inhibition on H₂ methanogens | kg S/m³ | 0.400 |

### SRB (Sulfate-Reducing Bacteria) Parameters

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| k_hSRB | H₂-utilizing SRB uptake rate | d⁻¹ | 41.125 |
| k_aSRB | Acetate-utilizing SRB uptake rate | d⁻¹ | 10.0 |
| k_pSRB | Propionate-utilizing SRB uptake rate | d⁻¹ | 16.25 |
| k_c4SRB | C4-utilizing SRB uptake rate | d⁻¹ | 23.0 |
| Y_hSRB | H₂-utilizing SRB yield | kg COD/kg COD | 0.05 |
| Y_aSRB | Acetate-utilizing SRB yield | kg COD/kg COD | 0.05 |
| Y_pSRB | Propionate-utilizing SRB yield | kg COD/kg COD | 0.04 |
| Y_c4SRB | C4-utilizing SRB yield | kg COD/kg COD | 0.06 |
| K_hSRB | H₂ half-saturation for SRB | kg COD/m³ | 5.96e-6 |
| K_aSRB | Acetate half-saturation for SRB | kg COD/m³ | 0.176 |
| K_pSRB | Propionate half-saturation for SRB | kg COD/m³ | 0.088 |
| K_c4SRB | C4 half-saturation for SRB | kg COD/m³ | 0.1739 |

### Stoichiometric Fractions

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| f_ch_xb | Carbohydrates from biomass decay | - | 0.275 |
| f_pr_xb | Proteins from biomass decay | - | 0.275 |
| f_li_xb | Lipids from biomass decay | - | 0.35 |
| f_xI_xb | Inerts from biomass decay | - | 0.10 |
| f_fa_li | Fatty acids from lipid hydrolysis | - | 0.95 |
| f_bu_su | Butyrate from sugar uptake | - | 0.1328 |
| f_pro_su | Propionate from sugar uptake | - | 0.2691 |
| f_ac_su | Acetate from sugar uptake | - | 0.4076 |
| f_va_aa | Valerate from amino acid uptake | - | 0.23 |
| f_bu_aa | Butyrate from amino acid uptake | - | 0.26 |
| f_pro_aa | Propionate from amino acid uptake | - | 0.05 |
| f_ac_aa | Acetate from amino acid uptake | - | 0.40 |

### Mass Transfer Parameters

| Parameter | Description | Units | Default Value |
|-----------|-------------|-------|---------------|
| kLa | Gas-liquid mass transfer coefficient | d⁻¹ | 200.0 |

---

## Typical Parameter Ranges

This table provides typical upper and lower bounds for key parameters based on literature values and operational experience.

| Parameter | Lower Bound | Upper Bound | Units | Notes |
|-----------|-------------|-------------|-------|-------|
| **Reactor Design** |||||
| V_liq | 10 | 10,000 | m³ | Depends on flow and HRT |
| HRT_days | 10 | 40 | days | Shorter for high-rate, longer for stability |
| T | 293.15 (20°C) | 328.15 (55°C) | K | Mesophilic: 30-40°C; Thermophilic: 50-55°C |
| **Hydrolysis Rates** |||||
| q_ch_hyd | 0.5 | 30 | d⁻¹ | Temperature dependent |
| q_pr_hyd | 0.5 | 30 | d⁻¹ | Temperature dependent |
| q_li_hyd | 0.1 | 15 | d⁻¹ | Slower than carbs/proteins |
| **Uptake Rates** |||||
| k_su | 10 | 70 | d⁻¹ | Fast-growing sugar degraders |
| k_aa | 20 | 100 | d⁻¹ | Fast amino acid degradation |
| k_fa | 2 | 12 | d⁻¹ | Slow LCFA degradation |
| k_c4 | 8 | 40 | d⁻¹ | Moderate C4 degradation |
| k_pro | 5 | 25 | d⁻¹ | Slow propionate degradation |
| k_ac | 3 | 16 | d⁻¹ | Slow acetoclastic methanogenesis |
| k_h2 | 15 | 70 | d⁻¹ | Fast H₂ utilization |
| **Half-Saturation Constants** |||||
| K_su | 0.1 | 1.0 | kg COD/m³ | Substrate affinity |
| K_aa | 0.1 | 0.6 | kg COD/m³ | |
| K_fa | 0.1 | 0.8 | kg COD/m³ | |
| K_c4 | 0.05 | 0.4 | kg COD/m³ | |
| K_pro | 0.03 | 0.2 | kg COD/m³ | |
| K_ac | 0.05 | 0.3 | kg COD/m³ | |
| K_h2 | 1e-7 | 1e-4 | kg COD/m³ | Very low H₂ affinity |
| **Yield Coefficients** |||||
| Y_su | 0.05 | 0.15 | kg COD/kg COD | Biomass yield varies with conditions |
| Y_aa | 0.04 | 0.12 | kg COD/kg COD | |
| Y_fa | 0.03 | 0.08 | kg COD/kg COD | |
| Y_c4 | 0.03 | 0.08 | kg COD/kg COD | |
| Y_pro | 0.02 | 0.06 | kg COD/kg COD | Lower yields for propionic |
| Y_ac | 0.02 | 0.08 | kg COD/kg COD | |
| Y_h2 | 0.03 | 0.10 | kg COD/kg COD | |
| **Decay Rates** |||||
| b (all groups) | 0.01 | 0.05 | d⁻¹ | Slower at lower temperatures |
| **Inhibition Constants** |||||
| KI_h2_fa | 1e-6 | 1e-5 | kg COD/m³ | Lower = more sensitive |
| KI_h2_c4 | 5e-6 | 5e-5 | kg COD/m³ | |
| KI_h2_pro | 1e-6 | 1e-5 | kg COD/m³ | Propionate highly H₂-sensitive |
| KI_nh3 | 5e-4 | 5e-3 | M | Free ammonia inhibition |
| KI_h2s_ac | 0.1 | 0.6 | kg S/m³ | Sulfide toxicity |
| KI_h2s_h2 | 0.1 | 0.5 | kg S/m³ | |
| **Operating Conditions** |||||
| pH | 6.5 | 8.0 | - | Optimal: 6.8-7.4 |
| OLR | 1 | 10 | kg COD/m³/d | Higher for high-rate systems |
| **Influent Characteristics** |||||
| Total COD | 2 | 50 | kg COD/m³ | Typical municipal: 5-15 |
| S_SO4 | 0.01 | 0.5 | kg S/m³ | Higher in industrial waste |
| S_IN | 0.1 | 1.0 | kg N/m³ | Nitrogen content |
| S_IP | 0.01 | 0.1 | kg P/m³ | Phosphorus content |

---

## JSON Format

```json
{
  "reactor_design": {
    "V_liq": {"description": "Liquid volume", "units": "m³", "default": "Q × HRT"},
    "V_gas": {"description": "Gas headspace volume", "units": "m³", "default": "V_liq × 0.1"},
    "HRT_days": {"description": "Hydraulic retention time", "units": "days", "default": 20},
    "T": {"description": "Operating temperature", "units": "K", "default": 308.15},
    "P": {"description": "Headspace pressure", "units": "Pa", "default": 101325}
  },
  "hydrolysis_rates": {
    "q_ch_hyd": {"description": "Carbohydrate hydrolysis rate", "units": "d⁻¹", "default": 10.0},
    "q_pr_hyd": {"description": "Protein hydrolysis rate", "units": "d⁻¹", "default": 10.0},
    "q_li_hyd": {"description": "Lipid hydrolysis rate", "units": "d⁻¹", "default": 10.0}
  },
  "uptake_rates": {
    "k_su": {"description": "Sugar uptake rate", "units": "d⁻¹", "default": 30.0},
    "k_aa": {"description": "Amino acid uptake rate", "units": "d⁻¹", "default": 50.0},
    "k_fa": {"description": "LCFA uptake rate", "units": "d⁻¹", "default": 6.0},
    "k_c4": {"description": "Butyrate/valerate uptake rate", "units": "d⁻¹", "default": 20.0},
    "k_pro": {"description": "Propionate uptake rate", "units": "d⁻¹", "default": 13.0},
    "k_ac": {"description": "Acetate uptake rate", "units": "d⁻¹", "default": 8.0},
    "k_h2": {"description": "Hydrogen uptake rate", "units": "d⁻¹", "default": 35.0}
  },
  "half_saturation_constants": {
    "K_su": {"description": "Sugar half-saturation", "units": "kg COD/m³", "default": 0.5},
    "K_aa": {"description": "Amino acid half-saturation", "units": "kg COD/m³", "default": 0.3},
    "K_fa": {"description": "LCFA half-saturation", "units": "kg COD/m³", "default": 0.4},
    "K_c4": {"description": "C4 half-saturation", "units": "kg COD/m³", "default": 0.2},
    "K_pro": {"description": "Propionate half-saturation", "units": "kg COD/m³", "default": 0.1},
    "K_ac": {"description": "Acetate half-saturation", "units": "kg COD/m³", "default": 0.15},
    "K_h2": {"description": "Hydrogen half-saturation", "units": "kg COD/m³", "default": 7e-6}
  },
  "yield_coefficients": {
    "Y_su": {"description": "Sugar degrader yield", "units": "kg COD/kg COD", "default": 0.10},
    "Y_aa": {"description": "Amino acid degrader yield", "units": "kg COD/kg COD", "default": 0.08},
    "Y_fa": {"description": "LCFA degrader yield", "units": "kg COD/kg COD", "default": 0.06},
    "Y_c4": {"description": "C4 degrader yield", "units": "kg COD/kg COD", "default": 0.06},
    "Y_pro": {"description": "Propionate degrader yield", "units": "kg COD/kg COD", "default": 0.04},
    "Y_ac": {"description": "Acetoclastic methanogen yield", "units": "kg COD/kg COD", "default": 0.05},
    "Y_h2": {"description": "Hydrogenotrophic methanogen yield", "units": "kg COD/kg COD", "default": 0.06}
  },
  "decay_rates": {
    "b_su": {"description": "Sugar degrader decay", "units": "d⁻¹", "default": 0.02},
    "b_aa": {"description": "Amino acid degrader decay", "units": "d⁻¹", "default": 0.02},
    "b_fa": {"description": "LCFA degrader decay", "units": "d⁻¹", "default": 0.02},
    "b_c4": {"description": "C4 degrader decay", "units": "d⁻¹", "default": 0.02},
    "b_pro": {"description": "Propionate degrader decay", "units": "d⁻¹", "default": 0.02},
    "b_ac": {"description": "Acetoclastic methanogen decay", "units": "d⁻¹", "default": 0.02},
    "b_h2": {"description": "Hydrogenotrophic methanogen decay", "units": "d⁻¹", "default": 0.02}
  },
  "inhibition_parameters": {
    "KI_h2_fa": {"description": "H₂ inhibition on LCFA degraders", "units": "kg COD/m³", "default": 5e-6},
    "KI_h2_c4": {"description": "H₂ inhibition on C4 degraders", "units": "kg COD/m³", "default": 1e-5},
    "KI_h2_pro": {"description": "H₂ inhibition on propionate degraders", "units": "kg COD/m³", "default": 3.5e-6},
    "KI_nh3": {"description": "Free ammonia inhibition", "units": "M", "default": 0.0018},
    "KI_h2s_ac": {"description": "H₂S inhibition on acetogens", "units": "kg S/m³", "default": 0.460},
    "KI_h2s_h2": {"description": "H₂S inhibition on H₂ methanogens", "units": "kg S/m³", "default": 0.400}
  },
  "srb_parameters": {
    "k_hSRB": {"description": "H₂-utilizing SRB uptake rate", "units": "d⁻¹", "default": 41.125},
    "k_aSRB": {"description": "Acetate-utilizing SRB uptake rate", "units": "d⁻¹", "default": 10.0},
    "k_pSRB": {"description": "Propionate-utilizing SRB uptake rate", "units": "d⁻¹", "default": 16.25},
    "k_c4SRB": {"description": "C4-utilizing SRB uptake rate", "units": "d⁻¹", "default": 23.0},
    "Y_hSRB": {"description": "H₂-utilizing SRB yield", "units": "kg COD/kg COD", "default": 0.05},
    "Y_aSRB": {"description": "Acetate-utilizing SRB yield", "units": "kg COD/kg COD", "default": 0.05},
    "Y_pSRB": {"description": "Propionate-utilizing SRB yield", "units": "kg COD/kg COD", "default": 0.04},
    "Y_c4SRB": {"description": "C4-utilizing SRB yield", "units": "kg COD/kg COD", "default": 0.06},
    "K_hSRB": {"description": "H₂ half-saturation for SRB", "units": "kg COD/m³", "default": 5.96e-6},
    "K_aSRB": {"description": "Acetate half-saturation for SRB", "units": "kg COD/m³", "default": 0.176},
    "K_pSRB": {"description": "Propionate half-saturation for SRB", "units": "kg COD/m³", "default": 0.088},
    "K_c4SRB": {"description": "C4 half-saturation for SRB", "units": "kg COD/m³", "default": 0.1739}
  },
  "stoichiometric_fractions": {
    "f_ch_xb": {"description": "Carbohydrates from biomass decay", "units": "-", "default": 0.275},
    "f_pr_xb": {"description": "Proteins from biomass decay", "units": "-", "default": 0.275},
    "f_li_xb": {"description": "Lipids from biomass decay", "units": "-", "default": 0.35},
    "f_xI_xb": {"description": "Inerts from biomass decay", "units": "-", "default": 0.10},
    "f_fa_li": {"description": "Fatty acids from lipid hydrolysis", "units": "-", "default": 0.95},
    "f_bu_su": {"description": "Butyrate from sugar uptake", "units": "-", "default": 0.1328},
    "f_pro_su": {"description": "Propionate from sugar uptake", "units": "-", "default": 0.2691},
    "f_ac_su": {"description": "Acetate from sugar uptake", "units": "-", "default": 0.4076},
    "f_va_aa": {"description": "Valerate from amino acid uptake", "units": "-", "default": 0.23},
    "f_bu_aa": {"description": "Butyrate from amino acid uptake", "units": "-", "default": 0.26},
    "f_pro_aa": {"description": "Propionate from amino acid uptake", "units": "-", "default": 0.05},
    "f_ac_aa": {"description": "Acetate from amino acid uptake", "units": "-", "default": 0.40}
  },
  "mass_transfer": {
    "kLa": {"description": "Gas-liquid mass transfer coefficient", "units": "d⁻¹", "default": 200.0}
  }
}
```

---

## Important Notes

### Concentration Units

**mADM1 uses kg/m³ (NOT mg/L)**

- Influent state files use kg/m³
- Internal QSDsan streams use mg/L
- Conversion: 1 kg/m³ = 1000 mg/L
- Typical COD ranges:
  - mADM1 influent: 5-20 kg COD/m³
  - Effluent: 0.5-3 kg COD/m³

### Temperature

- Always specified in Kelvin (K) internally
- Typical mesophilic: 308.15 K (35°C)
- Typical thermophilic: 328.15 K (55°C)
- Valid range: 293.15-328.15 K (20-55°C)

### CH4 Calculation

Use **molar basis** (`imol`) NOT mass basis (`imass`) for methane calculations:

```python
ch4_mol = gas.imol['S_ch4']  # kmol/hr
ch4_flow = ch4_mol * 22.414 * 24  # Nm³/d at STP
```

The `imass['S_ch4']` returns COD-equivalent mass (not actual CH4 mass) due to `i_mass = 0.25067`. Using `imass` directly causes a 4× overestimate.

---

## References

- Batstone, D.J., et al. (2002). "The IWA Anaerobic Digestion Model No 1 (ADM1)." Water Science and Technology, 45(10), 65-73.
- Rosen, C., & Jeppsson, U. (2006). "Aspects on ADM1 Implementation within the BSM2 Framework." Technical Report, Lund University.
- QSDsan Documentation: https://qsdsan.readthedocs.io/
