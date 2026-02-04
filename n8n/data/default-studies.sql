-- Default Studies for QSDsan Wastewater Simulation
-- Run this in Supabase SQL Editor after creating the studies table

-- Template 1: Aerobic MBR Base Template
INSERT INTO studies (study_id, name, description, category, is_template, config) VALUES (
  'template_aerobic_mbr',
  'Aerobic MBR Base Template',
  'Base template for aerobic MBR systems treating high-strength organic wastewater. Suitable for food & beverage applications.',
  'template',
  true,
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 500,
      "simplified": {
        "COD_mg_L": 2000,
        "NH4_mg_L": 50,
        "TP_mg_L": 15,
        "TSS_mg_L": 800,
        "temperature_C": 25
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 60
    }
  }'::jsonb
);

-- Template 2: Anaerobic CSTR Base Template
INSERT INTO studies (study_id, name, description, category, is_template, config) VALUES (
  'template_anaerobic_cstr',
  'Anaerobic CSTR Base Template',
  'Base template for anaerobic digestion of high-strength organic wastewater with biogas production.',
  'template',
  true,
  '{
    "simulation": {
      "template": "anaerobic_cstr_madm1",
      "model_type": "mADM1",
      "timeout_seconds": 900
    },
    "influent": {
      "flow_m3_d": 100,
      "temperature_K": 308.15,
      "concentrations": {
        "S_su": 2.0,
        "S_aa": 1.5,
        "S_fa": 0.5,
        "S_ac": 0.2,
        "S_IC": 0.05,
        "S_IN": 0.2,
        "S_IP": 0.02,
        "X_ch": 5.0,
        "X_pr": 3.0,
        "X_li": 2.0,
        "X_su": 0.01,
        "X_aa": 0.01,
        "X_fa": 0.01,
        "X_ac": 0.01,
        "X_h2": 0.01
      }
    },
    "reactor_config": {
      "V_liq": 2000,
      "V_gas": 200,
      "T": 308.15
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.05,
      "max_duration_days": 60
    }
  }'::jsonb
);

-- Study 1: Dairy Processing Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'dairy_baseline',
  'Dairy Processing Baseline',
  'Typical dairy processing wastewater with high fat content and variable organic loading. Based on cheese/yogurt production facilities.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 800,
      "simplified": {
        "COD_mg_L": 4500,
        "NH4_mg_L": 80,
        "TP_mg_L": 25,
        "TSS_mg_L": 1200,
        "temperature_C": 30,
        "cod_distribution": {
          "f_soluble": 0.6,
          "f_fermentable": 0.7,
          "f_acetate": 0.1
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 80
    },
    "srt_control": {
      "target_srt_days": 20,
      "srt_tolerance": 0.1
    }
  }'::jsonb
);

-- Study 2: Brewery Wastewater Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'brewery_baseline',
  'Brewery Wastewater Baseline',
  'Brewery wastewater with high carbohydrate content, acidic pH, and seasonal variation. Suitable for craft to mid-size breweries.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 400,
      "simplified": {
        "COD_mg_L": 3500,
        "NH4_mg_L": 30,
        "TP_mg_L": 20,
        "TSS_mg_L": 600,
        "temperature_C": 28,
        "cod_distribution": {
          "f_soluble": 0.7,
          "f_fermentable": 0.8,
          "f_acetate": 0.2
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 60
    },
    "srt_control": {
      "target_srt_days": 15,
      "srt_tolerance": 0.1
    }
  }'::jsonb
);

-- Study 3: Winery Wastewater Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'winery_baseline',
  'Winery Wastewater Baseline',
  'Winery wastewater with high seasonal variation (crush season vs. off-season). High sugars and organic acids.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 200,
      "simplified": {
        "COD_mg_L": 6000,
        "NH4_mg_L": 20,
        "TP_mg_L": 10,
        "TSS_mg_L": 400,
        "temperature_C": 22,
        "cod_distribution": {
          "f_soluble": 0.8,
          "f_fermentable": 0.9,
          "f_acetate": 0.15
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 60
    }
  }'::jsonb
);

-- Study 4: Soft Drink / Beverage Bottling Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'soft_drink_baseline',
  'Soft Drink Bottling Baseline',
  'Beverage bottling facility wastewater. High sugars from product loss and CIP cleaning. Relatively low nutrients.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 600,
      "simplified": {
        "COD_mg_L": 2500,
        "NH4_mg_L": 15,
        "TP_mg_L": 8,
        "TSS_mg_L": 300,
        "temperature_C": 25,
        "cod_distribution": {
          "f_soluble": 0.85,
          "f_fermentable": 0.9,
          "f_acetate": 0.1
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 50
    }
  }'::jsonb
);

-- Study 5: Meat Processing / Slaughterhouse Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'meat_processing_baseline',
  'Meat Processing Baseline',
  'Slaughterhouse and meat processing wastewater. High protein/fat, blood, and suspended solids. Significant nitrogen load.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "a2o_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 900
    },
    "influent": {
      "flow_m3_d": 500,
      "simplified": {
        "COD_mg_L": 5000,
        "NH4_mg_L": 120,
        "TP_mg_L": 30,
        "TSS_mg_L": 1500,
        "temperature_C": 28,
        "cod_distribution": {
          "f_soluble": 0.4,
          "f_fermentable": 0.5,
          "f_acetate": 0.1
        },
        "tss_distribution": {
          "f_inert": 0.15,
          "f_biodegradable_factor": 0.7
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 100
    },
    "srt_control": {
      "target_srt_days": 25,
      "srt_tolerance": 0.1
    }
  }'::jsonb
);

-- Study 6: Fruit & Vegetable Processing Baseline
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'fruit_vegetable_baseline',
  'Fruit & Vegetable Processing Baseline',
  'Fruit and vegetable washing, peeling, and processing wastewater. High carbohydrates, seasonal variation, low nitrogen.',
  'food_beverage',
  false,
  'template_aerobic_mbr',
  '{
    "simulation": {
      "template": "mle_mbr_asm2d",
      "model_type": "ASM2d",
      "timeout_seconds": 600
    },
    "influent": {
      "flow_m3_d": 350,
      "simplified": {
        "COD_mg_L": 3000,
        "NH4_mg_L": 25,
        "TP_mg_L": 12,
        "TSS_mg_L": 800,
        "temperature_C": 20,
        "cod_distribution": {
          "f_soluble": 0.6,
          "f_fermentable": 0.75,
          "f_acetate": 0.1
        }
      }
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.1,
      "max_duration_days": 60
    }
  }'::jsonb
);

-- Study 7: Dairy with Anaerobic Pre-treatment
INSERT INTO studies (study_id, name, description, category, is_template, parent_template_id, config) VALUES (
  'dairy_anaerobic',
  'Dairy Processing - Anaerobic Digestion',
  'High-strength dairy wastewater treated via anaerobic digestion for biogas recovery. Suitable for large facilities with energy recovery goals.',
  'food_beverage',
  false,
  'template_anaerobic_cstr',
  '{
    "simulation": {
      "template": "anaerobic_cstr_madm1",
      "model_type": "mADM1",
      "timeout_seconds": 900
    },
    "influent": {
      "flow_m3_d": 150,
      "temperature_K": 308.15,
      "concentrations": {
        "S_su": 3.0,
        "S_aa": 2.0,
        "S_fa": 1.5,
        "S_ac": 0.3,
        "S_IC": 0.05,
        "S_IN": 0.3,
        "S_IP": 0.03,
        "X_ch": 4.0,
        "X_pr": 2.5,
        "X_li": 3.0,
        "X_su": 0.01,
        "X_aa": 0.01,
        "X_fa": 0.01,
        "X_ac": 0.01,
        "X_h2": 0.01
      }
    },
    "reactor_config": {
      "V_liq": 3000,
      "V_gas": 300,
      "T": 308.15
    },
    "convergence": {
      "run_to_convergence": true,
      "convergence_atol": 0.05,
      "max_duration_days": 80
    }
  }'::jsonb
);

-- Verify insertions
SELECT study_id, name, category, is_template FROM studies ORDER BY category, study_id;
