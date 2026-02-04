# Phase N8N-3: Study Configuration Management

**Status:** Planning

**Created:** 2026-02-04

**Author:** Claude Code

**Target Version:** v10.0 (builds on v9.0)

**Prerequisites:** Phase N8N-1 (Dynamic JSON Input) must be implemented first

---

## Executive Summary

This plan introduces a Study Configuration Management system that stores predefined simulation configurations in Supabase. External processes can create studies and trigger workflows by `study_id`, enabling reproducible simulations without manual parameter entry. The system includes study templates for common scenarios with a focus on food & beverage manufacturing wastewater.

---

## Design Decisions

Based on requirements discussion:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Supabase | Already in use; external processes can add studies and trigger workflows |
| Study creation | Manual (you) + Claude-inserted defaults | Start simple; UI can be added later |
| Study templates | Yes | Base configurations for common scenarios with override capability |
| Versioning | No | Not needed at this stage; can add later if required |

---

## Supabase Schema

### Table: `studies`

```sql
-- Create the studies table
CREATE TABLE studies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  study_id TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  category TEXT NOT NULL,
  is_template BOOLEAN DEFAULT FALSE,
  parent_template_id TEXT REFERENCES studies(study_id),
  config JSONB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for fast lookups
CREATE INDEX idx_studies_study_id ON studies(study_id);
CREATE INDEX idx_studies_category ON studies(category);
CREATE INDEX idx_studies_is_template ON studies(is_template);

-- Add updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_studies_updated_at
  BEFORE UPDATE ON studies
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
```

### Column Descriptions

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Internal primary key |
| `study_id` | TEXT | Human-readable unique identifier (e.g., `dairy_baseline`) |
| `name` | TEXT | Display name (e.g., "Dairy Processing Baseline") |
| `description` | TEXT | Detailed description of the study purpose |
| `category` | TEXT | Grouping: `template`, `food_beverage`, `municipal`, `industrial`, `sensitivity` |
| `is_template` | BOOLEAN | True if this is a base template for creating variants |
| `parent_template_id` | TEXT | Reference to parent template (for derived studies) |
| `config` | JSONB | Full simulation configuration (Phase N8N-1 schema) |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last modification timestamp |

---

## Default Studies (Food & Beverage Focus)

### Templates (Base Configurations)

#### 1. `template_aerobic_mbr` - Aerobic MBR Base Template

```json
{
  "study_id": "template_aerobic_mbr",
  "name": "Aerobic MBR Base Template",
  "description": "Base template for aerobic MBR systems treating high-strength organic wastewater. Suitable for food & beverage applications.",
  "category": "template",
  "is_template": true,
  "config": {
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
  }
}
```

#### 2. `template_anaerobic_cstr` - Anaerobic CSTR Base Template

```json
{
  "study_id": "template_anaerobic_cstr",
  "name": "Anaerobic CSTR Base Template",
  "description": "Base template for anaerobic digestion of high-strength organic wastewater with biogas production.",
  "category": "template",
  "is_template": true,
  "config": {
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
  }
}
```

### Food & Beverage Studies

#### 3. `dairy_baseline` - Dairy Processing Wastewater

```json
{
  "study_id": "dairy_baseline",
  "name": "Dairy Processing Baseline",
  "description": "Typical dairy processing wastewater with high fat content and variable organic loading. Based on cheese/yogurt production facilities.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 4. `brewery_baseline` - Brewery Wastewater

```json
{
  "study_id": "brewery_baseline",
  "name": "Brewery Wastewater Baseline",
  "description": "Brewery wastewater with high carbohydrate content, acidic pH, and seasonal variation. Suitable for craft to mid-size breweries.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 5. `winery_baseline` - Winery Wastewater

```json
{
  "study_id": "winery_baseline",
  "name": "Winery Wastewater Baseline",
  "description": "Winery wastewater with high seasonal variation (crush season vs. off-season). High sugars and organic acids.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 6. `soft_drink_baseline` - Soft Drink / Beverage Bottling

```json
{
  "study_id": "soft_drink_baseline",
  "name": "Soft Drink Bottling Baseline",
  "description": "Beverage bottling facility wastewater. High sugars from product loss and CIP cleaning. Relatively low nutrients.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 7. `meat_processing_baseline` - Meat Processing / Slaughterhouse

```json
{
  "study_id": "meat_processing_baseline",
  "name": "Meat Processing Baseline",
  "description": "Slaughterhouse and meat processing wastewater. High protein/fat, blood, and suspended solids. Significant nitrogen load.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 8. `fruit_vegetable_baseline` - Fruit & Vegetable Processing

```json
{
  "study_id": "fruit_vegetable_baseline",
  "name": "Fruit & Vegetable Processing Baseline",
  "description": "Fruit and vegetable washing, peeling, and processing wastewater. High carbohydrates, seasonal variation, low nitrogen.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_aerobic_mbr",
  "config": {
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
  }
}
```

#### 9. `dairy_anaerobic` - Dairy with Anaerobic Pre-treatment

```json
{
  "study_id": "dairy_anaerobic",
  "name": "Dairy Processing - Anaerobic Digestion",
  "description": "High-strength dairy wastewater treated via anaerobic digestion for biogas recovery. Suitable for large facilities with energy recovery goals.",
  "category": "food_beverage",
  "is_template": false,
  "parent_template_id": "template_anaerobic_cstr",
  "config": {
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
  }
}
```

### Summary of Default Studies

| study_id | Category | Template | Description |
|----------|----------|----------|-------------|
| `template_aerobic_mbr` | template | - | Base aerobic MBR configuration |
| `template_anaerobic_cstr` | template | - | Base anaerobic CSTR configuration |
| `dairy_baseline` | food_beverage | aerobic | Cheese/yogurt production |
| `brewery_baseline` | food_beverage | aerobic | Craft to mid-size brewery |
| `winery_baseline` | food_beverage | aerobic | Winery crush season |
| `soft_drink_baseline` | food_beverage | aerobic | Beverage bottling |
| `meat_processing_baseline` | food_beverage | aerobic | Slaughterhouse/meat processing |
| `fruit_vegetable_baseline` | food_beverage | aerobic | Produce processing |
| `dairy_anaerobic` | food_beverage | anaerobic | Dairy with biogas recovery |

---

## Workflow Changes (v10)

### New Nodes

#### 1. Fetch Study Config

```javascript
// Fetch study configuration from Supabase
const env = $('Env Parameters').first().json;
const studyId = $input.first().json.study_id;

if (!studyId) {
  throw new Error('study_id is required');
}

const response = await this.helpers.httpRequest({
  method: 'GET',
  url: `${env.supabase_url}/rest/v1/studies?study_id=eq.${studyId}&select=*`,
  headers: {
    'apikey': env.supabase_key,
    'Authorization': `Bearer ${env.supabase_key}`
  }
});

if (!response || response.length === 0) {
  throw new Error(`Study not found: ${studyId}`);
}

const study = response[0];

return {
  json: {
    study_id: study.study_id,
    study_name: study.name,
    study_description: study.description,
    category: study.category,
    is_template: study.is_template,
    parent_template_id: study.parent_template_id,
    config: study.config
  }
};
```

#### 2. Apply Overrides (Optional)

```javascript
// Apply any overrides to the study config
const study = $('Fetch Study Config').first().json;
const overrides = $input.first().json.overrides || {};

// Deep merge overrides into config
function deepMerge(target, source) {
  for (const key in source) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
      target[key] = target[key] || {};
      deepMerge(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

const mergedConfig = deepMerge({ ...study.config }, overrides);

return {
  json: {
    ...study,
    config: mergedConfig,
    overrides_applied: Object.keys(overrides).length > 0
  }
};
```

### Updated Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Trigger with   │────▶│  Env Parameters  │────▶│  Generate       │
│  study_id       │     │  (hardcoded)     │     │  Session ID     │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                        ┌─────────────────────────────────┘
                        ▼
              ┌──────────────────┐     ┌──────────────────┐
              │  Fetch Study     │────▶│  Apply Overrides │
              │  Config          │     │  (optional)      │
              └──────────────────┘     └────────┬─────────┘
                                                │
                        ┌───────────────────────┘
                        ▼
              ┌──────────────────┐
              │  Prepare         │────▶ ... (rest of v9 flow)
              │  Simulation      │
              └──────────────────┘
```

---

## Trigger Options

### Option A: Manual Trigger with study_id

Simple manual trigger where `study_id` is entered directly:

```json
{
  "study_id": "dairy_baseline"
}
```

### Option B: Webhook Trigger

External system POSTs to webhook:

```bash
curl -X POST https://n8n.example.com/webhook/qsdsan-study \
  -H "Content-Type: application/json" \
  -d '{"study_id": "dairy_baseline"}'
```

### Option C: Webhook with Overrides

External system can override specific parameters:

```bash
curl -X POST https://n8n.example.com/webhook/qsdsan-study \
  -H "Content-Type: application/json" \
  -d '{
    "study_id": "dairy_baseline",
    "overrides": {
      "influent": {
        "flow_m3_d": 1200,
        "simplified": {
          "COD_mg_L": 5500
        }
      }
    }
  }'
```

---

## Implementation Plan

### Phase 1: Supabase Setup

**Task 1.1: Create Studies Table**
- Run SQL schema in Supabase SQL Editor
- Verify table creation and indexes

**Task 1.2: Insert Default Studies**
- Insert 2 templates + 7 food & beverage studies
- Verify data with SELECT queries

### Phase 2: Workflow Modification

**Task 2.1: Create v10 Workflow Base**
- Copy v9 as starting point
- Update workflow name to "QSDsan Simulation v10.0"

**Task 2.2: Add Study Fetch Nodes**
- Add "Fetch Study Config" code node
- Add "Apply Overrides" code node (optional path)

**Task 2.3: Update Prepare Simulation**
- Accept config from Fetch Study Config node
- Merge with session data

**Task 2.4: Add Webhook Trigger (Optional)**
- Add webhook trigger alongside manual trigger
- Configure webhook path

### Phase 3: Testing

**Task 3.1: Test Each Default Study**
- Run each study through workflow
- Verify results are reasonable

**Task 3.2: Test Override Functionality**
- Test study + overrides combination
- Verify merge behavior

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `n8n/schemas/study-schema.sql` | Create | Supabase table DDL |
| `n8n/data/default-studies.json` | Create | Default study definitions |
| `n8n/n8n-qsd-test/qsdsan-simulation-v10.json` | Create | New workflow version |
| `n8n/n8n-qsd-test/README.md` | Modify | Update with v10 documentation |

---

## Success Criteria

1. Studies table created in Supabase
2. 9 default studies inserted (2 templates + 7 food & beverage)
3. v10 workflow fetches study by `study_id`
4. Override functionality works correctly
5. All default studies execute successfully
6. External webhook trigger works

---

## Questions Resolved

| Question | Answer |
|----------|--------|
| Where to store studies? | Supabase |
| Who creates studies? | You (manual) + Claude-inserted defaults |
| Study templates? | Yes - base configurations with override capability |
| Versioning? | No - not needed at this stage |

---

## Next Steps

1. **You:** Create the `studies` table in Supabase using the SQL provided above
2. **Claude:** Can insert the default studies once table is ready
3. **Implementation:** After Phase N8N-1 (v9) is complete, implement v10

---

## Appendix: Typical Wastewater Characteristics by Industry

| Industry | COD (mg/L) | BOD (mg/L) | TSS (mg/L) | TN (mg/L) | TP (mg/L) |
|----------|------------|------------|------------|-----------|-----------|
| Dairy | 2,000-6,000 | 1,500-4,000 | 500-2,000 | 50-150 | 15-40 |
| Brewery | 2,000-5,000 | 1,500-3,500 | 200-1,000 | 20-60 | 10-30 |
| Winery | 3,000-15,000 | 2,000-10,000 | 200-800 | 10-40 | 5-20 |
| Soft Drinks | 1,000-4,000 | 800-3,000 | 100-500 | 10-30 | 5-15 |
| Meat Processing | 3,000-8,000 | 2,000-5,000 | 1,000-3,000 | 100-200 | 20-50 |
| Fruit/Vegetable | 2,000-5,000 | 1,500-3,500 | 500-1,500 | 20-50 | 10-25 |

*Source: Various industry references and QSDsan documentation*
