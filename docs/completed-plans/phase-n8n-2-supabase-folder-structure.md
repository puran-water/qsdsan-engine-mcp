# Phase N8N-2: Supabase Hierarchical Folder Structure

**Status:** Complete
**Created:** 2026-02-04
**Author:** Claude Code
**Depends On:** Phase N8N-1 (Dynamic JSON Input)

---

## Executive Summary

This plan introduces a hierarchical folder structure for Supabase file uploads, enabling organized storage by session and analysis type. Two new environment parameters (`session_id`, `analysis_type`) drive the folder structure, with files named using the session ID and template name for clear traceability.

---

## Current State Analysis

### Current Folder Structure (v7)

Files are stored in **flat, type-based folders**:

```
{supabase_bucket}/
├── pdfs/
│   └── wastewater_simulation_{job_id}.pdf
├── csv/
│   └── wastewater_simulation_{job_id}.csv
├── json/
│   └── wastewater_simulation_{job_id}.json
└── analysis/
    └── wastewater_simulation_{job_id}_ai_analysis.md
```

### Current Limitations

1. **No session grouping** - Files from the same workflow run are scattered across folders
2. **No analysis type categorization** - All wastewater results mixed together
3. **Job ID naming** - Uses internal job_id rather than meaningful session identifier
4. **Flat structure** - Difficult to navigate when many simulations exist
5. **No upstream traceability** - Cannot link results back to originating process

---

## Proposed Solution

### New Hierarchical Folder Structure

```
{supabase_bucket}/
├── {session_id}/
│   └── {analysis_type}/
│       ├── {session_id}-{template}.csv
│       ├── {session_id}-{template}.json
│       ├── {session_id}-{template}.pdf
│       └── {session_id}-AI_Analysis.md
```

### Concrete Example

```
panicleDevelop_1/
├── Test_Session-2026-02-04_12-23/
│   └── WasteWater/
│       ├── Test_Session-2026-02-04_12-23-mle_mbr_asm2d.csv
│       ├── Test_Session-2026-02-04_12-23-mle_mbr_asm2d.json
│       ├── Test_Session-2026-02-04_12-23-mle_mbr_asm2d.pdf
│       └── Test_Session-2026-02-04_12-23-AI_Analysis.md
```

---

## New Environment Parameters

### Updated Env Parameters Node

Add two new fields to the existing Env Parameters node:

```json
{
  "server_ip": "34.28.104.162",
  "server_port": "8080",
  "gotenberg_url": "http://34.28.104.162:3000",
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "eyJ...",
  "supabase_bucket": "panicleDevelop_1",
  "ai_model": "gpt-4o",
  "override_prompt": "",

  "session_id": "",
  "analysis_type": "WasteWater"
}
```

### Parameter Definitions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_id` | string | Auto-generated | Session identifier from upstream process, or auto-generated for standalone testing |
| `analysis_type` | string | "WasteWater" | Category folder for grouping related analyses |

### Session ID Default Generation

When `session_id` is empty or not provided, generate a default value:

```javascript
// Format: "Test_Session-{ccyy-mm-dd}_{hh-mm}" (hyphen in time, not colon)
const now = new Date();
const year = now.getFullYear();
const month = String(now.getMonth() + 1).padStart(2, '0');
const day = String(now.getDate()).padStart(2, '0');
const hours = String(now.getHours()).padStart(2, '0');
const minutes = String(now.getMinutes()).padStart(2, '0');

const session_id = `Test_Session-${year}-${month}-${day}_${hours}-${minutes}`;
// Example: "Test_Session-2026-02-04_12-23"
```

### Session ID Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| Maximum length | 60 characters | Prevents excessively long paths |
| Allowed characters | Alphanumeric, hyphen, underscore | Path-safe across all platforms |
| Colon replacement | Hyphen | Windows compatibility |

---

## File Path Composition

### Path Templates

| File Type | Current Path | New Path |
|-----------|--------------|----------|
| CSV | `csv/wastewater_simulation_{job_id}.csv` | `{session_id}/{analysis_type}/{session_id}-{template}.csv` |
| JSON | `json/wastewater_simulation_{job_id}.json` | `{session_id}/{analysis_type}/{session_id}-{template}.json` |
| PDF | `pdfs/wastewater_simulation_{job_id}.pdf` | `{session_id}/{analysis_type}/{session_id}-{template}.pdf` |
| AI Analysis | `analysis/wastewater_simulation_{job_id}_ai_analysis.md` | `{session_id}/{analysis_type}/{session_id}-AI_Analysis.md` |

### Variable Sources

| Variable | Source |
|----------|--------|
| `{supabase_bucket}` | Env Parameters node |
| `{session_id}` | Env Parameters node (or auto-generated) |
| `{analysis_type}` | Env Parameters node |
| `{template}` | Simulation Input JSON (`simulation.template`) |

### Path Construction Code

```javascript
// In "Prepare File Paths" node
const envParams = $('Env Parameters').item.json;
const simInput = $('Parse Input JSON').item.json;

// Get or generate session_id
let session_id = envParams.session_id;
if (!session_id || session_id.trim() === '') {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  session_id = `Test_Session-${year}-${month}-${day}_${hours}-${minutes}`;
}

// Validate and sanitize session_id
session_id = session_id
  .replace(/:/g, '-')           // Replace colons with hyphens
  .replace(/\s+/g, '_')         // Replace spaces with underscores
  .replace(/[<>"|?*]/g, '')     // Remove invalid path characters
  .substring(0, 60);            // Enforce 60 character max length

const analysis_type = envParams.analysis_type || 'WasteWater';
const template = simInput.simulation.template;

// Construct base path
const basePath = `${session_id}/${analysis_type}`;

// Construct file paths
const filePaths = {
  csv: `${basePath}/${session_id}-${template}.csv`,
  json: `${basePath}/${session_id}-${template}.json`,
  pdf: `${basePath}/${session_id}-${template}.pdf`,
  ai_analysis: `${basePath}/${session_id}-AI_Analysis.md`
};

return { json: { session_id, analysis_type, template, filePaths } };
```

---

## Implementation Plan

### Phase 1: Env Parameters Update

**Task 1.1: Add New Fields to Env Parameters**
- Add `session_id` field (empty default)
- Add `analysis_type` field (default: "WasteWater")

**Task 1.2: Create Session ID Generator Node**
- Add JavaScript code node after Env Parameters
- Generate default session_id if not provided
- Store in workflow context for downstream use

### Phase 2: Path Construction

**Task 2.1: Create "Prepare File Paths" Node**
- New JavaScript node after simulation completes
- Constructs all file paths using new hierarchy
- Outputs `filePaths` object for upload nodes

**Task 2.2: Update Upload URLs**
- Modify PDF upload URL to use `filePaths.pdf`
- Modify CSV upload URL to use `filePaths.csv`
- Modify JSON upload URL to use `filePaths.json`
- Modify AI Analysis upload URL to use `filePaths.ai_analysis`

### Phase 3: Upload Node Updates

**Task 3.1: Update PDF Upload Node**

Current:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/pdfs/wastewater_simulation_${job_id}.pdf`
```

New:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/${filePaths.pdf}`
```

**Task 3.2: Update CSV Upload Node**

Current:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/csv/wastewater_simulation_${job_id}.csv`
```

New:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/${filePaths.csv}`
```

**Task 3.3: Update JSON Upload Node**

Current:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/json/wastewater_simulation_${job_id}.json`
```

New:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/${filePaths.json}`
```

**Task 3.4: Update AI Analysis Upload Node**

Current:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/analysis/wastewater_simulation_${job_id}_ai_analysis.md`
```

New:
```javascript
url: `${supabase_url}/storage/v1/object/${supabase_bucket}/${filePaths.ai_analysis}`
```

### Phase 4: Public URL Updates

**Task 4.1: Update Public URL Generation**

Update all public URL constructions to use new paths:

```javascript
const publicUrl = `${supabase_url}/storage/v1/object/public/${supabase_bucket}/${filePaths.pdf}`;
```

### Phase 5: Testing & Validation

**Task 5.1: Test Standalone Mode**
- Run workflow without session_id
- Verify auto-generated session_id format
- Confirm folder creation in Supabase

**Task 5.2: Test Upstream Integration**
- Run workflow with provided session_id
- Verify correct folder path used
- Confirm all files grouped under session

---

## Supabase Folder Creation

### Note on Folder Auto-Creation

Supabase Storage **automatically creates folders** when uploading files to nested paths. No explicit folder creation step is required.

When uploading to:
```
Test_Session-2026-02-04_12:23/WasteWater/Test_Session-2026-02-04_12:23-mle_mbr_asm2d.csv
```

Supabase will automatically create:
- `Test_Session-2026-02-04_12:23/` (if not exists)
- `Test_Session-2026-02-04_12:23/WasteWater/` (if not exists)

---

## Workflow Node Changes

### Nodes to Modify

| Node | Change |
|------|--------|
| `Env Parameters` | Add `session_id` and `analysis_type` fields |
| `Upload PDF to Supabase` | Update URL path construction |
| `Upload CSV to Supabase` | Update URL path construction |
| `Upload JSON to Supabase` | Update URL path construction |
| `Upload AI Analysis to Supabase` | Update URL path construction |
| `PDF_Succeeded` | Update public URL construction |
| `CSV_Succeeded` | Update public URL construction |
| `JSON_Success` | Update public URL construction |
| `AI_Analysis_Succeeded` | Update public URL construction |

### Nodes to Add

| Node | Purpose |
|------|---------|
| `Generate Session ID` | Auto-generate session_id if not provided |
| `Prepare File Paths` | Construct all file paths using new hierarchy |

---

## Updated Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         n8n Workflow (v8)                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────┐    ┌─────────────────────────────────────┐ │
│  │   Env Parameters       │    │  Simulation Input (from upstream)   │ │
│  │   (Hardcoded Node)     │    │  (Webhook/Manual Trigger)           │ │
│  ├────────────────────────┤    ├─────────────────────────────────────┤ │
│  │ • server_ip            │    │ • simulation.template ──────────┐   │ │
│  │ • server_port          │    │ • influent                      │   │ │
│  │ • gotenberg_url        │    │ • reactor_config                │   │ │
│  │ • supabase_url         │    │ • kinetic_params                │   │ │
│  │ • supabase_key         │    │ • convergence                   │   │ │
│  │ • supabase_bucket ─────┼────┼─────────────────────────────┐   │   │ │
│  │ • ai_model             │    │                             │   │   │ │
│  │ • session_id ──────────┼────┼─────────────────────────┐   │   │   │ │
│  │ • analysis_type ───────┼────┼─────────────────────┐   │   │   │   │ │
│  └────────────────────────┘    └─────────────────────│───│───│───│───┘ │
│                                                      │   │   │   │     │
│                                                      ▼   ▼   ▼   ▼     │
│                                          ┌───────────────────────────┐ │
│                                          │   Prepare File Paths      │ │
│                                          ├───────────────────────────┤ │
│                                          │ basePath =                │ │
│                                          │   {session_id}/           │ │
│                                          │   {analysis_type}/        │ │
│                                          │                           │ │
│                                          │ filePaths.csv =           │ │
│                                          │   basePath +              │ │
│                                          │   {session_id}-           │ │
│                                          │   {template}.csv          │ │
│                                          └─────────────┬─────────────┘ │
│                                                        │               │
│                                                        ▼               │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │                     Supabase Uploads                              │ │
│  ├──────────────────────────────────────────────────────────────────┤ │
│  │                                                                   │ │
│  │  {bucket}/{session_id}/{analysis_type}/{session_id}-{template}   │ │
│  │                                                                   │ │
│  │  ├── .csv   (simulation data)                                    │ │
│  │  ├── .json  (full results + metadata)                            │ │
│  │  ├── .pdf   (formatted report)                                   │ │
│  │  └── -AI_Analysis.md (AI interpretation)                         │ │
│  │                                                                   │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Example Outputs

### Example 1: Standalone Test Run

**Input:**
- `session_id`: (empty - auto-generate)
- `analysis_type`: "WasteWater"
- `template`: "mle_mbr_asm2d"

**Generated Files:**
```
panicleDevelop_1/
└── Test_Session-2026-02-04_14-30/
    └── WasteWater/
        ├── Test_Session-2026-02-04_14-30-mle_mbr_asm2d.csv
        ├── Test_Session-2026-02-04_14-30-mle_mbr_asm2d.json
        ├── Test_Session-2026-02-04_14-30-mle_mbr_asm2d.pdf
        └── Test_Session-2026-02-04_14-30-AI_Analysis.md
```

### Example 2: Upstream Process Integration

**Input:**
- `session_id`: "ProjectAlpha_Run_042"
- `analysis_type`: "WasteWater"
- `template`: "a2o_mbr_asm2d"

**Generated Files:**
```
panicleDevelop_1/
└── ProjectAlpha_Run_042/
    └── WasteWater/
        ├── ProjectAlpha_Run_042-a2o_mbr_asm2d.csv
        ├── ProjectAlpha_Run_042-a2o_mbr_asm2d.json
        ├── ProjectAlpha_Run_042-a2o_mbr_asm2d.pdf
        └── ProjectAlpha_Run_042-AI_Analysis.md
```

### Example 3: Multiple Analysis Types (Cross-Workflow)

Different workflows (WasteWater, EnergyPlus, BioTech) can share the same session_id, allowing related analyses to be grouped together in Supabase:

**WasteWater Workflow:**
- `session_id`: "Site_Assessment_2026Q1"
- `analysis_type`: "WasteWater"
- `template`: "mle_mbr_asm2d"

**EnergyPlus Workflow (separate n8n workflow):**
- `session_id`: "Site_Assessment_2026Q1"
- `analysis_type`: "EnergyPlus"
- `template`: "building_energy_model"

**BioTech Workflow (separate n8n workflow):**
- `session_id`: "Site_Assessment_2026Q1"
- `analysis_type`: "BioTech"
- `template`: "fermentation_model"

**Generated Structure (combined from multiple workflows):**
```
panicleDevelop_1/
└── Site_Assessment_2026Q1/
    ├── WasteWater/
    │   ├── Site_Assessment_2026Q1-mle_mbr_asm2d.csv
    │   ├── Site_Assessment_2026Q1-mle_mbr_asm2d.json
    │   ├── Site_Assessment_2026Q1-mle_mbr_asm2d.pdf
    │   └── Site_Assessment_2026Q1-AI_Analysis.md
    ├── EnergyPlus/
    │   ├── Site_Assessment_2026Q1-building_energy_model.csv
    │   ├── Site_Assessment_2026Q1-building_energy_model.json
    │   ├── Site_Assessment_2026Q1-building_energy_model.pdf
    │   └── Site_Assessment_2026Q1-AI_Analysis.md
    └── BioTech/
        ├── Site_Assessment_2026Q1-fermentation_model.csv
        ├── Site_Assessment_2026Q1-fermentation_model.json
        ├── Site_Assessment_2026Q1-fermentation_model.pdf
        └── Site_Assessment_2026Q1-AI_Analysis.md
```

> **Note:** Each workflow type (WasteWater, EnergyPlus, BioTech) is a separate n8n workflow with its own `analysis_type` default. The shared `session_id` enables grouping related analyses from different domains.

---

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `n8n/n8n-qsd-test/qsdsan-simulation-v8.json` | Modify | Add new params, update upload paths |
| `n8n/n8n-qsd-test/README.md` | Modify | Document new folder structure |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Special characters in session_id | Medium | Medium | Sanitize session_id (replace spaces, colons) |
| Session ID too long | Low | Low | Enforce 60 character maximum |
| Colon in timestamp (Windows) | Medium | Low | Use hyphen (`-`) instead of colon in timestamps |
| Old flat-structure files | Low | Low | User will remove manually in due course |

### Session ID Sanitization

To avoid path issues, sanitize session_id:

```javascript
// Sanitize session_id for safe path usage
const sanitizeSessionId = (id) => {
  return id
    .replace(/:/g, '-')           // Replace colons (Windows issue)
    .replace(/\s+/g, '_')         // Replace spaces with underscores
    .replace(/[<>"|?*]/g, '')     // Remove invalid path characters
    .substring(0, 60);            // Enforce 60 character max
};
```

**Auto-generated format:**
```
Test_Session-2026-02-04_12-23  // Using hyphen in time component
```

---

## Success Criteria

1. ✅ New `session_id` and `analysis_type` fields in Env Parameters
2. ✅ Auto-generation of session_id when not provided
3. ✅ All four file types uploaded to hierarchical path
4. ✅ Files grouped by session and analysis type
5. ✅ Public URLs correctly constructed
6. ✅ Standalone testing works with auto-generated session_id
7. ✅ Upstream integration works with provided session_id

---

## Approval Checklist

- [x] Folder hierarchy structure is appropriate
- [x] File naming convention is clear and consistent
- [x] Session ID format (with sanitization) is acceptable
- [x] Default `analysis_type` of "WasteWater" is correct
- [x] Auto-generated session_id format is acceptable (hyphen in time: `12-23`)
- [x] 60 character maximum for session_id
- [x] Cross-workflow grouping via shared session_id (WasteWater, EnergyPlus, BioTech)
- [x] Old flat-structure files: leave as-is (manual cleanup later)
- [x] Implementation approach is sound

---

## Decisions Made

| Question | Decision |
|----------|----------|
| Timestamp separator | Hyphen (`12-23`) - not colon or underscore |
| Session ID max length | 60 characters |
| Other analysis types | EnergyPlus, BioTech (separate workflows, same folder structure) |
| Migration of old files | Leave as-is; manual removal later |
