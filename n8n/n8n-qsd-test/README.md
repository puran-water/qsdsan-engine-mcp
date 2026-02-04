# QSDsan Wastewater Simulation - n8n Workflow

This n8n workflow implements the same wastewater simulation logic as the Claude Desktop skill, transposed into n8n nodes.

## Version

**v1.0**

## Overview

The workflow orchestrates wastewater treatment simulations via the QSDsan REST API, including:
- Health check verification
- Simulation submission
- Status polling with timeout handling
- Result processing and report generation

## Workflow Structure

```
Manual Trigger
    ↓
Set Parameters (configure inputs)
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
│             Generate Report (Markdown)
│                   ↓
│             ┌─────┴─────┐
│             ↓           ↓
│         Convert to   Output JSON
│           PDF           ↓
│             ↓         End (Success)
│         Output PDF
│             ↓
│         End (Success)
```

## Configuration

Edit the **Set Parameters** node to configure:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `server_ip` | 35.225.205.140 | QSDsan server IP address |
| `server_port` | 8080 | Server port |
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

## Available Templates

| Template | Description |
|----------|-------------|
| `mle_mbr_asm2d` | MLE-MBR for nitrogen removal |
| `a2o_mbr_asm2d` | A2O-MBR for N+P removal (EBPR) |
| `ao_mbr_asm2d` | Simple A/O-MBR |
| `anaerobic_cstr_madm1` | Anaerobic CSTR |

## Exit Conditions

The workflow exits the polling loop when:

1. **Completed** - Simulation finished successfully
2. **Failed** - Simulation encountered an error
3. **Timeout** - Elapsed time >= timeout_seconds (retrieves partial results)
4. **Hard Cancel** - Elapsed time >= timeout + 120s (terminates job, retrieves results)

## Output

The workflow produces:
- **PDF report** with professional formatting (via Gotenberg)
- **JSON file** containing all raw data

### PDF Conversion

The workflow uses [Gotenberg](https://gotenberg.dev/) to convert the Markdown report to a styled PDF. The PDF includes:
- Professional typography with Arial font
- Color-coded headers and tables
- Assessment badges with status colors (Excellent=green, Poor=red, etc.)
- Warning callouts for incomplete simulations

## Installation

1. Open n8n
2. Go to **Workflows** → **Import**
3. Select `qsdsan-wastewater-simulation.json`
4. Configure the **Set Parameters** node with your values
5. Save and activate

## Requirements

- n8n v1.0+
- Gotenberg service (for PDF conversion)
- Network access to QSDsan VM (35.225.205.140:8080)
- VM must be running

## Running with Docker Compose

The easiest way to run n8n with Gotenberg is using Docker Compose:

```bash
cd n8n/n8n-qsd-test
docker-compose up -d
```

This starts:
- **n8n** at http://localhost:5678 (admin/changeme)
- **Gotenberg** at http://localhost:3000 (internal API)

## Starting the Server

If the health check fails:

```bash
gcloud compute instances start qsdsan-vm \
    --zone=us-central1-a \
    --project=lotsawatts
```

Wait 60 seconds for startup.

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Server health check |
| `/api/simulate_system` | POST | Start simulation |
| `/api/get_job_status` | GET | Poll status |
| `/api/get_job_results` | GET | Get results |
| `/api/terminate_job` | POST | Cancel job |

## Nodes Overview

| Node | Type | Purpose |
|------|------|---------|
| Manual Trigger | Trigger | Start workflow |
| Set Parameters | Set | Configure inputs |
| Health Check | HTTP Request | Verify server |
| Server Healthy? | If | Branch on health |
| Prepare Simulation | Code | Build payload |
| Submit Simulation | HTTP Request | POST to API |
| Store Job ID | Code | Save job_id |
| Wait 60s | Wait | Polling interval |
| Check Job Status | HTTP Request | GET status |
| Evaluate Status | Code | Check conditions |
| Continue Polling? | If | Loop decision |
| Loop Back | Code | Continue loop |
| Need Terminate? | If | Hard cancel check |
| Terminate Job | HTTP Request | POST terminate |
| Get Results | HTTP Request | GET results |
| Process Results | Code | Parse response |
| Generate Report | Code | Create markdown |
| Convert to PDF | HTTP Request | Gotenberg Markdown→PDF |
| Output PDF | Convert to File | Export PDF file |
| Output JSON | Convert to File | Export raw data |
