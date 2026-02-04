# QSDsan Cloud Usage Guide

**Version:** 3.0.9

**Last Updated:** January 2026

**Author:** Rainer Gaier

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Architecture](#2-system-architecture)
3. [Service Locations](#3-service-locations)
4. [Managing Services](#4-managing-services)
5. [IP Addresses and ngrok](#5-ip-addresses-and-ngrok)
6. [API Endpoints Reference](#6-api-endpoints-reference)
7. [Using the API Directly](#7-using-the-api-directly)
8. [n8n Workflow Configuration](#8-n8n-workflow-configuration)
9. [Workflow Execution Results](#9-workflow-execution-results)
10. [Supabase Storage](#10-supabase-storage)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Overview

The QSDsan Engine is a wastewater treatment simulation system that models anaerobic (mADM1) and aerobic (ASM2d) treatment processes. The system consists of three main components:

| Component         | Purpose                            | Technology                       |
| ----------------- | ---------------------------------- | -------------------------------- |
| **QSDsan Engine** | Wastewater simulation calculations | Python/QSDsan on GCP Cloud Run   |
| **Gotenberg**     | HTML to PDF conversion             | Docker container on GCP VM       |
| **n8n Workflow**  | Automation and orchestration       | n8n.io cloud or self-hosted      |
| **Supabase**      | Output file storage                | Supabase Storage (S3-compatible) |

### What the System Does

1. Accepts wastewater influent parameters (flow, COD, NH4, etc.)
2. Runs dynamic simulations using biochemical process models
3. Generates performance reports (PDF, CSV, JSON)
4. Stores outputs in Supabase for retrieval

---

## 2. System Architecture

```
┌─────────────────┐     ┌─────────────────────────────────────────────┐
│   n8n Workflow  │     │              Google Cloud Platform           │
│  (Automation)   │     │                                             │
│                 │     │  ┌─────────────────┐  ┌──────────────────┐  │
│  ┌───────────┐  │     │  │  Cloud Run      │  │   Compute Engine │  │
│  │ Manual    │──┼────►│  │  (QSDsan API)   │  │   (Gotenberg)    │  │
│  │ Trigger   │  │     │  │  Port: 8080     │  │   Port: 3000     │  │
│  └───────────┘  │     │  │                 │  │                  │  │
│       │         │     │  │  REST API:      │  │  HTML → PDF      │  │
│       ▼         │     │  │  /api/*         │  │  Converter       │  │
│  ┌───────────┐  │     │  └────────┬────────┘  └────────┬─────────┘  │
│  │ Simulate  │◄─┼─────┼───────────┘                    │            │
│  │ & Poll    │  │     │                                │            │
│  └───────────┘  │     └────────────────────────────────┼────────────┘
│       │         │                                      │
│       ▼         │                                      │
│  ┌───────────┐  │     ┌────────────────────────────────┘
│  │ Generate  │──┼────►│
│  │ Report    │  │     │
│  └───────────┘  │     │
│       │         │     │
│       ▼         │     ▼
│  ┌───────────┐  │  ┌─────────────────┐
│  │ Upload to │──┼─►│    Supabase     │
│  │ Supabase  │  │  │    Storage      │
│  └───────────┘  │  │  /pdfs/         │
│                 │  │  /csv/          │
└─────────────────┘  │  /json/         │
                     └─────────────────┘
```

---

## 3. Service Locations

### Current Production Services

| Service             | URL / IP                                   | Port | Status Check                            |
| ------------------- | ------------------------------------------ | ---- | --------------------------------------- |
| **QSDsan REST API** | `34.28.104.162`                            | 8080 | `curl http://34.28.104.162:8080/health` |
| **Gotenberg (PDF)** | `34.28.104.162`                            | 3000 | `curl http://34.28.104.162:3000/health` |
| **Supabase**        | `https://egrzvwnjrtpwwmqzimff.supabase.co` | 443  | N/A (always available)                  |
| **n8n**             | Your n8n instance                          | -    | Via n8n dashboard                       |

### API Documentation

- **Interactive API Docs (Swagger):** `http://34.28.104.162:8080/docs`
- **OpenAPI Schema:** `http://34.28.104.162:8080/openapi.json`

---

## 4. Managing Services

### 4.1 Checking Service Status

**QSDsan Engine:**

```bash
# Health check - returns {"status": "healthy", "version": "3.0.8", ...}
curl http://34.28.104.162:8080/health

# Readiness check - includes tool count
curl http://34.28.104.162:8080/ready
```

**Gotenberg:**

```bash
# Health check - returns {"status": "up"}
curl http://34.28.104.162:3000/health
```

### 4.2 Starting Google Cloud Services

The QSDsan Engine runs on **Google Cloud Run**, which **scales to zero** when idle. This means:

- **First request after idle:** May take 15-30 seconds (cold start)
- **Subsequent requests:** Fast response (~1-2 seconds)
- **Cost savings:** No charges when not in use

**To "wake up" the service:**

```bash
curl http://34.28.104.162:8080/health
```

**To check Cloud Run service status:**

```bash
# Requires Google Cloud SDK and authentication
gcloud run services describe qsdsan-engine-mcp --region=us-central1
```

### 4.3 Stopping Google Cloud Services

> ⚠️ **IMPORTANT: Cost Management**
>
> Google Cloud Run charges based on:
>
> - **CPU time** during request processing
> - **Memory allocation** during active instances
> - **Network egress** for data transfer
>
> **To minimize costs:**
>
> - Services automatically scale to zero when idle
> - No manual stopping required for cost savings
> - If you need to fully disable, delete the service (see below)

**To temporarily disable the service:**

```bash
# Set minimum instances to 0 (allows scale-to-zero)
gcloud run services update qsdsan-engine-mcp \
    --region=us-central1 \
    --min-instances=0
```

**To completely stop (delete) the service:**

```bash
# WARNING: This deletes the service entirely
gcloud run services delete qsdsan-engine-mcp --region=us-central1
```

### 4.4 Managing Gotenberg

Gotenberg runs on a GCP Compute Engine VM. Connect via SSH to manage:

```bash
# SSH into the VM (requires gcloud authentication)
gcloud compute ssh <vm-name> --zone=<zone>

# Check Docker containers
docker ps

# Start Gotenberg
docker start gotenberg

# Stop Gotenberg
docker stop gotenberg

# View logs
docker logs gotenberg --tail 100
```

---

## 5. IP Addresses and ngrok

### Static vs Dynamic IPs

| Service               | IP Type                    | Notes                                                              |
| --------------------- | -------------------------- | ------------------------------------------------------------------ |
| **Cloud Run**         | Static (via Load Balancer) | IP: `34.28.104.162` - Does not change unless service is redeployed |
| **Compute Engine VM** | Ephemeral by default       | Can be made static via GCP Console                                 |
| **ngrok**             | Dynamic                    | Changes on each ngrok restart                                      |

### When Does the IP Change?

1. **Cloud Run:** IP remains stable. The URL may change if the service is deleted and recreated.
1. **Compute Engine VM:**

   - Ephemeral IP changes when VM is stopped/started
   - Static IP (if configured) remains permanent
   - To reserve a static IP:

     ```bash
     gcloud compute addresses create qsdsan-static-ip --region=us-central1
     ```
1. **ngrok:**

   - IP/URL changes every time ngrok is restarted
   - Free tier provides random subdomains
   - Paid plans can reserve custom subdomains

### Role of ngrok

ngrok is used for **development and testing** to expose local services to the internet:

- **Use case:** Testing n8n workflows against local QSDsan server
- **Not required for production:** Cloud Run provides public HTTPS endpoints
- **When to use:** Local development, demos, testing before deployment

**Example ngrok usage:**

```bash
# Expose local port 8080
ngrok http 8080

# Output example:
# Forwarding: https://abc123.ngrok.io -> http://localhost:8080
```

---

## 6. API Endpoints Reference

### 6.1 Categories

The REST API provides **31 endpoints** organized into categories:

| Category        | Endpoints | Description                                    |
| --------------- | --------- | ---------------------------------------------- |
| **Discovery**   | 4         | Version info, templates, units, components     |
| **Simulation**  | 2         | Run simulations (template or custom flowsheet) |
| **Jobs**        | 5         | List, status, results, terminate, timeseries   |
| **Utility**     | 2         | State validation and conversion                |
| **Flowsheet**   | 5         | Session creation, streams, units, connections  |
| **Session**     | 4         | List, get, clone, delete sessions              |
| **Mutation**    | 5         | Update/delete streams, units, connections      |
| **Analysis**    | 2         | Validate flowsheet, suggest recycles           |
| **Results**     | 2         | Timeseries data, artifact retrieval            |
| **AI Analysis** | 2         | Server-side AI analysis (secure API key)       |

### 6.2 Key Endpoints

| Endpoint                | Method | Description                                |
| ----------------------- | ------ | ------------------------------------------ |
| `/health`               | GET    | Service health check                       |
| `/docs`                 | GET    | Interactive Swagger documentation          |
| `/api/get_version`      | GET    | Server version and environment info        |
| `/api/list_templates`   | GET    | Available simulation templates             |
| `/api/simulate_system`  | POST   | Submit simulation job                      |
| `/api/get_job_status`   | GET    | Check job progress                         |
| `/api/get_job_results`  | GET    | Retrieve completed results                 |
| `/api/terminate_job`    | POST   | Cancel a running job                       |
| `/api/analyze_results`  | POST   | AI analysis of results (secure, server-side) |
| `/api/ai_status`        | GET    | Check if AI analysis is configured         |

---

## 7. Using the API Directly

### 7.1 Accessing Swagger Documentation

Open your browser and navigate to:

```
http://34.28.104.162:8080/docs
```

This provides:

- Interactive endpoint testing
- Request/response schemas
- Example payloads
- Try-it-out functionality

### 7.2 Example API Calls

**Get Server Version:**

```bash
curl http://34.28.104.162:8080/api/get_version
```

**List Available Templates:**

```bash
curl http://34.28.104.162:8080/api/list_templates
```

**List Model Components:**

```bash
curl "http://34.28.104.162:8080/api/get_model_components?model_type=ASM2d"
```

**Submit a Simulation:**

```bash
curl -X POST http://34.28.104.162:8080/api/simulate_system \
  -H "Content-Type: application/json" \
  -d '{
    "template": "mle_mbr_asm2d",
    "influent": {
      "model_type": "ASM2d",
      "flow_m3_d": 4000,
      "temperature_K": 293.15,
      "concentrations": {
        "S_F": 87.5,
        "S_A": 26.25,
        "S_I": 10,
        "S_NH4": 25,
        "S_NO3": 0,
        "S_PO4": 8,
        "S_ALK": 300,
        "X_I": 55,
        "X_S": 205,
        "X_H": 30,
        "X_PAO": 0,
        "X_AUT": 0,
        "H2O": 1000
      }
    },
    "duration_days": 1.0
  }'
```

**Check Job Status:**

```bash
curl "http://34.28.104.162:8080/api/get_job_status?job_id=YOUR_JOB_ID"
```

**Get Job Results:**

```bash
curl "http://34.28.104.162:8080/api/get_job_results?job_id=YOUR_JOB_ID"
```

**Check AI Analysis Status:**

```bash
curl http://34.28.104.162:8080/api/ai_status
```

**Submit AI Analysis (server-side, secure):**

```bash
curl -X POST http://34.28.104.162:8080/api/analyze_results \
  -H "Content-Type: application/json" \
  -d '{
    "results": {
      "job_id": "sim_abc123",
      "status": "Completed",
      "effluent": {"COD_mg_L": 15.2, "NH4_mg_L": 0.8},
      "removal": {"COD_pct": 95.7, "NH4_pct": 96.8}
    },
    "input_parameters": {
      "flow_m3_d": 4000,
      "COD_mg_L": 350
    },
    "model": "gpt-4o"
  }'
```

> **Note:** The `/api/analyze_results` endpoint keeps the OpenAI API key secure on the server. Clients don't need to provide or configure API keys.

---

## 8. n8n Workflow Configuration

### 8.1 Parameter Overview

The workflow uses two parameter nodes:

#### **Env Parameters** (Environment/Connection Settings)

| Parameter         | Description                     | Example Value                              | v6      | v7      |
| ----------------- | ------------------------------- | ------------------------------------------ | ------- | ------- |
| `server_ip`       | QSDsan API server IP            | `34.28.104.162`                            | Required| Required|
| `server_port`     | QSDsan API port                 | `8080`                                     | Required| Required|
| `gotenberg_url`   | Gotenberg PDF service URL       | `http://34.28.104.162:3000`                | Required| Required|
| `supabase_url`    | Supabase project URL            | `https://egrzvwnjrtpwwmqzimff.supabase.co` | Required| Required|
| `supabase_key`    | Supabase service role key (JWT) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`  | Required| Required|
| `supabase_bucket` | Storage bucket name             | `panicleDevelop_1`                         | Required| Required|
| `openai_api_key`  | OpenAI API key                  | `sk-...`                                   | Required| **NOT needed** |
| `ai_model`        | AI model for analysis           | `gpt-4o`                                   | Optional| Optional|

> **Security Note (v7):** In workflow v7, the OpenAI API key is stored securely on the server via the `OPENAI_API_KEY` environment variable. The workflow does not need to contain the API key, preventing exposure to workflow users.

#### **WW Parameters** (Wastewater/Simulation Settings)

| Parameter                | Description                    | Default         | Range/Options             |
| ------------------------ | ------------------------------ | --------------- | ------------------------- |
| `template`               | Simulation template            | `mle_mbr_asm2d` | See `/api/list_templates` |
| `timeout_seconds`        | Max wait time for results      | `300`           | 60-600                    |
| `check_interval_seconds` | Poll frequency                 | `60`            | 10-120                    |
| `hard_cancel_buffer`     | Extra time before force-cancel | `120`           | 60-300                    |
| `flow_m3_d`              | Influent flow rate (m³/day)    | `4000`          | 100-100000                |
| `temperature_C`          | Temperature (Celsius)          | `20`            | 10-35                     |
| `COD_mg_L`               | Chemical Oxygen Demand (mg/L)  | `350`           | 100-1000                  |
| `NH4_mg_L`               | Ammonia-Nitrogen (mg/L)        | `25`            | 10-100                    |
| `TP_mg_L`                | Total Phosphorus (mg/L)        | `8`             | 2-20                      |
| `TSS_mg_L`               | Total Suspended Solids (mg/L)  | `220`           | 100-500                   |

### 8.2 Configuring Parameters in n8n

1. **Open the workflow** in n8n
2. **Double-click "Env Parameters"** node
3. **Edit the values** as needed (especially `supabase_key`)
4. **Double-click "WW Parameters"** node
5. **Adjust simulation parameters** for your scenario
6. **Save the workflow**

### 8.3 Important Notes on Supabase Key

> ⚠️ **CRITICAL: Supabase Key Format**
>
> The `supabase_key` must be a **JWT token** starting with `eyJ...`
>
> **WRONG:** `sb_secret_XXXXXXXXXXXXXXXXXXXXXXXXXXXX`
>
> **CORRECT:** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1...`
>
> Find your JWT key at: Supabase Dashboard → Settings → API → `service_role` key

---

## 9. Workflow Execution Results

### 9.1 What to Expect After Running

1. **Health Check** (2-5 seconds)

   - Verifies QSDsan server is accessible
   - Shows server version and environment
1. **Simulation Submission** (1-2 seconds)

   - Returns a `job_id` for tracking
   - Simulation runs in background
1. **Polling Loop** (30-300 seconds typically)

   - Checks status every 60 seconds
   - Displays progress messages
1. **Result Processing** (5-10 seconds)

   - Extracts performance metrics
   - Calculates removal efficiencies
1. **Report Generation** (10-30 seconds)

   - Creates HTML report
   - Converts to PDF via Gotenberg
   - Generates CSV summary
1. **Supabase Upload** (5-15 seconds)

   - Uploads PDF, CSV, and JSON files
   - Returns public URLs

### 9.2 Expected Output Data

The workflow produces these result fields:

| Field                | Description                  | Example                            |
| -------------------- | ---------------------------- | ---------------------------------- |
| `job_id`             | Unique simulation identifier | `sim_abc123`                       |
| `status`             | Final status                 | `Completed`, `Timed Out`, `Failed` |
| `elapsed_seconds`    | Total runtime                | `245`                              |
| `assessment`         | Overall rating               | `Excellent`, `Good`, `Poor`        |
| **Effluent Quality** |                              |                                    |
| `effluent.COD_mg_L`  | Effluent COD                 | `15.2`                             |
| `effluent.NH4_mg_L`  | Effluent ammonia             | `0.8`                              |
| `effluent.TSS_mg_L`  | Effluent TSS                 | `5.1`                              |
| **Removal Rates**    |                              |                                    |
| `removal.COD_pct`    | COD removal                  | `95.7%`                            |
| `removal.NH4_pct`    | NH4 removal                  | `96.8%`                            |
| `nitrification_pct`  | Nitrification efficiency     | `98.2%`                            |

---

## 10. Supabase Storage

### 10.1 File Organization

Files are stored in the Supabase bucket with this structure:

```
panicleDevelop_1/
├── pdfs/
│   └── wastewater_simulation_{job_id}.pdf
├── csv/
│   └── wastewater_simulation_{job_id}.csv
├── json/
│   └── wastewater_simulation_{job_id}.json
└── analysis/
    └── wastewater_simulation_{job_id}_ai_analysis.md
```

### 10.2 Files Created

| File Type         | Location                                               | Contents                                         |
| ----------------- | ------------------------------------------------------ | ------------------------------------------------ |
| **PDF Report**    | `/pdfs/wastewater_simulation_{job_id}.pdf`             | Formatted report with tables, charts, assessment |
| **CSV Summary**   | `/csv/wastewater_simulation_{job_id}.csv`              | Tabular data for spreadsheet analysis            |
| **JSON Data**     | `/json/wastewater_simulation_{job_id}.json`            | Complete structured results                      |
| **AI Analysis**   | `/analysis/wastewater_simulation_{job_id}_ai_analysis.md` | Expert AI analysis in markdown format (v6/v7)    |

### 10.3 Accessing Files

**Via Supabase Dashboard:**

1. Go to https://app.supabase.com
2. Select your project
3. Navigate to Storage → `panicleDevelop_1`
4. Browse folders and download files

**Via Public URLs:**

After upload, files are accessible at:

```
https://egrzvwnjrtpwwmqzimff.supabase.co/storage/v1/object/public/panicleDevelop_1/pdfs/wastewater_simulation_{job_id}.pdf
```

**Via Supabase CLI:**

```bash
# Install Supabase CLI
npm install -g supabase

# Login
supabase login

# List files
supabase storage ls panicleDevelop_1/pdfs/

# Download file
supabase storage cp supabase://panicleDevelop_1/pdfs/wastewater_simulation_abc123.pdf ./local_copy.pdf
```

---

## 11. Troubleshooting

### Common Issues

| Problem                      | Possible Cause           | Solution                                      |
| ---------------------------- | ------------------------ | --------------------------------------------- |
| Server not responding        | Cold start               | Wait 15-30 seconds, retry                     |
| 400 error on Supabase upload | Invalid JWT key          | Use `eyJ...` format key                       |
| PDF corrupted                | Binary encoding issue    | Ensure HTTP Request node used (not Code node) |
| Simulation timeout           | Complex simulation       | Increase `timeout_seconds`                    |
| "fetch is not defined"       | n8n Code node limitation | Use `this.helpers.httpRequest()` instead      |

### Diagnostic Commands

```bash
# Check all services
echo "=== QSDsan Engine ===" && curl -s http://34.28.104.162:8080/health | jq
echo "=== Gotenberg ===" && curl -s http://34.28.104.162:3000/health | jq

# Test Supabase connection
curl -s "https://egrzvwnjrtpwwmqzimff.supabase.co/storage/v1/bucket" \
  -H "Authorization: Bearer YOUR_JWT_KEY" | jq

# View Cloud Run logs
gcloud logs read --service=qsdsan-engine-mcp --region=us-central1 --limit=20
```

### Getting Help

- **API Documentation:** http://34.28.104.162:8080/docs
- **Project Repository:** Check CLAUDE.md for development context
- **n8n Workflows:**
  - v5: `n8n/n8n-qsd-test/qsdsan-simulation-v5.json` (No AI analysis)
  - v6: `n8n/n8n-qsd-test/qsdsan-simulation-v6.json` (AI analysis - API key in workflow)
  - v7: `n8n/n8n-qsd-test/qsdsan-simulation-v7.json` (AI analysis - secure server-side API key) **RECOMMENDED**

---

## Appendix: Quick Reference Card

```
╔═══════════════════════════════════════════════════════════════════════╗
║                      QSDsan Quick Reference                            ║
╠═══════════════════════════════════════════════════════════════════════╣
║ ENDPOINTS                                                              ║
║   Health:      curl http://34.28.104.162:8080/health                  ║
║   Docs:        http://34.28.104.162:8080/docs                         ║
║   Gotenberg:   curl http://34.28.104.162:3000/health                  ║
║   AI Status:   curl http://34.28.104.162:8080/api/ai_status           ║
║                                                                        ║
║ SUPABASE                                                               ║
║   URL:    https://egrzvwnjrtpwwmqzimff.supabase.co                    ║
║   Bucket: panicleDevelop_1                                             ║
║   Key:    Must be JWT format (eyJ...)                                 ║
║                                                                        ║
║ N8N WORKFLOWS                                                          ║
║   v5: qsdsan-simulation-v5.json (no AI)                               ║
║   v6: qsdsan-simulation-v6.json (AI - key in workflow)                ║
║   v7: qsdsan-simulation-v7.json (AI - secure server-side) RECOMMENDED ║
║   Configure: Env Parameters & WW Parameters nodes                      ║
║                                                                        ║
║ OUTPUT FILES                                                           ║
║   PDF:      /pdfs/wastewater_simulation_{job_id}.pdf                  ║
║   CSV:      /csv/wastewater_simulation_{job_id}.csv                   ║
║   JSON:     /json/wastewater_simulation_{job_id}.json                 ║
║   Analysis: /analysis/wastewater_simulation_{job_id}_ai_analysis.md   ║
║                                                                        ║
║ API KEY SECURITY (v7)                                                  ║
║   Server env var: OPENAI_API_KEY (not exposed to workflow users)      ║
╚═══════════════════════════════════════════════════════════════════════╝
```