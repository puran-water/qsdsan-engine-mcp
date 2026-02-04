# QSDsan n8n Workflow - Test Files

This folder contains the n8n workflow files and testing utilities for the QSDsan wastewater simulation engine.

## Workflow Versions

| File | Version | Description |
|------|---------|-------------|
| `qsdsan-simulation-v8.json` | v8.0 | Hierarchical storage, hardcoded parameters |
| `qsdsan-simulation-v9.json` | v9.0 | Dynamic JSON input, study management, webhook support |

## v9 Features

- **Study Mode**: Fetch predefined configurations from Supabase
- **Direct Mode**: Pass full JSON simulation configuration
- **Legacy Mode**: Backward compatible with v8 flat parameters
- **Overrides**: Customize any parameter in any mode
- **Webhook Trigger**: External system integration

## Test Files

| File | Description |
|------|-------------|
| `webhook-test-examples.ps1` | PowerShell script with interactive menu (Windows) |
| `webhook-test-examples.sh` | Bash/curl script with interactive menu (Linux/Mac) |
| `webhook-payloads.json` | JSON reference for all payload examples |

## Quick Start

### 1. Import Workflow into n8n

1. Open n8n
2. Go to **Workflows** → **Import from File**
3. Select `qsdsan-simulation-v9.json`
4. Update **Env Parameters** node with your credentials

### 2. Test with Manual Trigger

1. Click **Execute Workflow** - runs with default parameters

### 3. Test with Webhook

1. Click on **Webhook Trigger** node
2. Click **"Listen for Test Event"**
3. Run test script:

**PowerShell (Windows):**
```powershell
# Update webhook URL in script first
.\webhook-test-examples.ps1
```

**Bash (Linux/Mac/Git Bash):**
```bash
chmod +x webhook-test-examples.sh
./webhook-test-examples.sh
```

## Input Modes

### Study Mode
```json
{
  "study_id": "dairy_baseline"
}
```

### Study Mode with Overrides
```json
{
  "study_id": "dairy_baseline",
  "overrides": {
    "influent": {
      "flow_m3_d": 1500
    }
  }
}
```

### Direct Mode
```json
{
  "simulation": {
    "template": "mle_mbr_asm2d",
    "model_type": "ASM2d"
  },
  "influent": {
    "flow_m3_d": 4000,
    "simplified": {
      "COD_mg_L": 350,
      "NH4_mg_L": 25,
      "TP_mg_L": 8,
      "TSS_mg_L": 220,
      "temperature_C": 20
    }
  }
}
```

### Legacy Mode (v8 Compatible)
```json
{
  "template": "mle_mbr_asm2d",
  "flow_m3_d": 4000,
  "COD_mg_L": 350,
  "NH4_mg_L": 25,
  "TP_mg_L": 8,
  "TSS_mg_L": 220,
  "temperature_C": 20
}
```

## Available Studies

| Study ID | Description |
|----------|-------------|
| `template_aerobic_mbr` | Template - Aerobic MBR (ASM2d) |
| `template_anaerobic_cstr` | Template - Anaerobic CSTR (mADM1) |
| `dairy_baseline` | Dairy Processing - Baseline |
| `brewery_baseline` | Brewery - Baseline |
| `winery_baseline` | Winery - Baseline |
| `soft_drink_baseline` | Soft Drink Manufacturing |
| `meat_processing_baseline` | Meat Processing |
| `fruit_vegetable_baseline` | Fruit & Vegetable Processing |
| `dairy_anaerobic` | Dairy - Anaerobic Treatment (mADM1) |

## Available Templates

| Template | Description |
|----------|-------------|
| `mle_mbr_asm2d` | MLE MBR - Modified Ludzack-Ettinger |
| `ao_mbr_asm2d` | A/O MBR - Anaerobic/Oxic |
| `a2o_mbr_asm2d` | A2O MBR - Anaerobic/Anoxic/Oxic |
| `anaerobic_cstr_madm1` | Anaerobic CSTR with mADM1 |

---

## v8 Reference (Legacy)

### Overview

The v8 workflow orchestrates wastewater treatment simulations via the QSDsan REST API, including:
- Health check verification
- Simulation submission
- Status polling with timeout handling
- Result processing and report generation
- Hierarchical Supabase storage

### Workflow Structure (v8)

```
Manual Trigger
    ↓
Env Parameters (configure infrastructure)
    ↓
WW Parameters (configure simulation - REMOVED in v9)
    ↓
Generate Session ID
    ↓
Health Check (GET /health)
    ↓
Server Healthy? ──No──→ Server Error → End (Error)
    ↓ Yes
Prepare Simulation (build request payload)
    ↓
Submit Simulation (POST /api/simulate_system)
    ↓
Store Job ID
    ↓
┌─→ Wait 60s
│   ↓
│   Check Job Status (GET /api/get_job_status)
│   ↓
│   Evaluate Status
│   ↓
│   Continue Polling? ──Yes──→ Loop Back ─┘
│   ↓ No
│   Need Terminate? ──Yes──→ Terminate Job
│   ↓ No                          ↓
│   Skip Terminate ←─────────── After Terminate
│   ↓                             ↓
│   └────────→ Merge Paths ←──────┘
│                   ↓
│             Get Results (GET /api/get_job_results)
│                   ↓
│             Process Results
│                   ↓
│             AI Analysis (server-side)
│                   ↓
│             Generate Report
│                   ↓
│         ┌────┬────┬────┐
│         ↓    ↓    ↓    ↓
│       PDF  CSV  JSON  AI.md
│         ↓    ↓    ↓    ↓
│     Upload to Supabase (hierarchical paths)
│         ↓
│     End (Success)
```

### Configuration (v8)

Edit the **WW Parameters** node to configure:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `template` | mle_mbr_asm2d | Simulation template |
| `timeout_seconds` | 300 | Simulation timeout (5 min) |
| `check_interval_seconds` | 60 | Status polling interval |
| `hard_cancel_buffer` | 120 | Extra time before hard cancel |
| `flow_m3_d` | 4000 | Influent flow rate |
| `temperature_C` | 20 | Temperature in Celsius |
| `COD_mg_L` | 350 | Influent COD |
| `NH4_mg_L` | 25 | Influent ammonia |
| `TP_mg_L` | 8 | Influent total phosphorus |
| `TSS_mg_L` | 220 | Influent TSS |

### Exit Conditions

The workflow exits the polling loop when:

1. **Completed** - Simulation finished successfully
2. **Failed** - Simulation encountered an error
3. **Timeout** - Elapsed time >= timeout_seconds (retrieves partial results)
4. **Hard Cancel** - Elapsed time >= timeout + 120s (terminates job, retrieves results)

### Output

The workflow produces (uploaded to Supabase):
- **PDF report** with professional formatting (via Gotenberg)
- **CSV file** with summary data
- **JSON file** containing all raw data
- **AI Analysis** markdown file

Storage path: `{session_id}/{analysis_type}/{filename}`

---

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Server health check |
| `/api/simulate_system` | POST | Start simulation |
| `/api/get_job_status` | GET | Poll status |
| `/api/get_job_results` | GET | Get results |
| `/api/terminate_job` | POST | Cancel job |
| `/api/analyze_results` | POST | Server-side AI analysis |

---

## Related Documentation

- [Phase N8N-1 Plan](../../docs/completed-plans/phase-n8n-1-dynamic-json-input.md) - Dynamic JSON & Study Management
- [Phase N8N-2 Plan](../../docs/completed-plans/phase-n8n-2-supabase-folder-structure.md) - Hierarchical storage
- [Phase N8N-3 Plan](../../docs/completed-plans/phase-n8n-3-study-configuration-management.md) - Study management

---

## Running with Docker Compose

The easiest way to run n8n with Gotenberg is using Docker Compose:

```bash
cd n8n/n8n-qsd-test
docker-compose up -d
```

This starts:
- **n8n** at https://n8n.panicle.org
- **Gotenberg** at http://localhost:3000 (internal API)

## Starting the QSDsan Server

If the health check fails:

```bash
gcloud compute instances start qsdsan-vm \
    --zone=us-central1-a \
    --project=lotsawatts
```

Wait 60 seconds for startup.
