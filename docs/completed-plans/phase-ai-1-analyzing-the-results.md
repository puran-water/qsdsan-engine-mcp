# Phase AI-1: Server-Side AI Analysis of Simulation Results

**Status:** Complete
**Version:** 3.0.9
**Completed:** 2026-02-01

---

## Overview

This phase adds secure server-side AI analysis capabilities to the QSDsan Engine. The feature enables expert wastewater treatment analysis of simulation results using OpenAI's GPT models, with the API key securely stored on the server rather than exposed in client workflows.

---

## Problem Statement

In workflow v6, the OpenAI API key was stored directly in the n8n workflow's `Env Parameters` node. This posed a security risk:

- Anyone with access to the workflow could see and copy the API key
- The key could be abused for unauthorized purposes
- No audit trail of who used the API key

---

## Solution

Implemented a server-side AI analysis endpoint that:

1. Stores the OpenAI API key as a server environment variable (`OPENAI_API_KEY`)
2. Exposes a REST API endpoint (`/api/analyze_results`) for clients to request analysis
3. Processes analysis requests server-side, never exposing the API key to clients
4. Returns expert wastewater treatment analysis in markdown format

---

## Implementation Details

### New REST API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze_results` | POST | Submit simulation results for AI analysis |
| `/api/ai_status` | GET | Check if AI analysis is configured |

### `/api/analyze_results` Request Schema

```json
{
  "results": {
    "job_id": "string",
    "status": "string",
    "effluent": { ... },
    "removal": { ... }
  },
  "input_parameters": {
    "flow_m3_d": 4000,
    "COD_mg_L": 350
  },
  "model": "gpt-4o",
  "temperature": 0.7,
  "max_tokens": 4000
}
```

### `/api/analyze_results` Response Schema

```json
{
  "status": "success",
  "analysis": "# Executive Summary\n\n...(markdown content)...",
  "model_used": "gpt-4o",
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "total_tokens": 1801
  }
}
```

### `/api/ai_status` Response Schema

```json
{
  "ai_enabled": true,
  "message": "AI analysis is available",
  "supported_models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  "default_model": "gpt-4o"
}
```

---

## Files Modified

| File | Changes |
|------|---------|
| `server_rest.py` | Added `/api/analyze_results` and `/api/ai_status` endpoints |
| `.env.example` | Added `OPENAI_API_KEY` and `OPENAI_MODEL` configuration |
| `cloud/QSDsan cloud deployment Guide.md` | Documented new endpoints and security model |

## Files Created

| File | Description |
|------|-------------|
| `n8n/n8n-qsd-test/qsdsan-simulation-v7.json` | New workflow using server-side AI analysis |
| `docs/completed-plans/phase-ai-1-analyzing-the-results.md` | This plan document |

---

## n8n Workflow Versions

| Version | AI Analysis | API Key Location | Security |
|---------|-------------|------------------|----------|
| v5 | None | N/A | N/A |
| v6 | Yes | In workflow `Env Parameters` | **Low** - visible to workflow users |
| v7 | Yes | Server environment variable | **High** - hidden from workflow users |

### Key Changes in v7 Workflow

1. **Removed** `openai_api_key` from `Env Parameters` node
2. **Changed** AI analysis node from direct OpenAI API call to `/api/analyze_results` endpoint
3. **Updated** error handling for server-side API responses
4. **Added** "secure server-side API" notices in generated reports

---

## Server Configuration

### Environment Variable

```bash
# Required for AI analysis
export OPENAI_API_KEY=sk-your-api-key-here

# Optional: Override default model
export OPENAI_MODEL=gpt-4o
```

### Docker Deployment

The `OPENAI_API_KEY` must be passed to the container:

```bash
docker run -e OPENAI_API_KEY=sk-... -p 8080:8080 qsdsan-mcp
```

Or in `docker-compose.yaml`:

```yaml
services:
  qsdsan-mcp:
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
```

---

## AI Analysis Prompt

The server uses a specialized system prompt for wastewater treatment expertise:

```
You are an expert wastewater treatment specialist with deep knowledge of:
- Activated sludge processes (ASM1, ASM2d, ASM3)
- Anaerobic digestion (ADM1, mADM1)
- Membrane bioreactors (MBR)
- Nutrient removal (nitrogen, phosphorus)
- Process optimization and troubleshooting

Analyze the provided simulation results and input parameters. Provide:
1. Executive Summary - Key findings in 2-3 sentences
2. Treatment Performance Analysis - Evaluate COD, nitrogen, phosphorus removal
3. Process Observations - Note any concerning trends or excellent performance
4. Recommendations - Specific, actionable suggestions for optimization
5. Potential Issues - Any warnings or areas requiring attention
```

---

## Output Files

The v6/v7 workflows produce an additional output file:

| File | Location | Format |
|------|----------|--------|
| AI Analysis | `/analysis/wastewater_simulation_{job_id}_ai_analysis.md` | Markdown |

This file is uploaded to Supabase Storage alongside the PDF, CSV, and JSON outputs.

---

## Error Handling

| HTTP Status | Condition | Response |
|-------------|-----------|----------|
| 200 | Success | Analysis in markdown format |
| 503 | `OPENAI_API_KEY` not set | "AI analysis not configured" |
| 502 | Cannot connect to OpenAI | "Failed to connect to OpenAI API" |
| 504 | OpenAI timeout | "OpenAI API request timed out" |
| 4xx | OpenAI API error | Forwarded error message |

---

## Testing

### Check AI Status

```bash
curl http://34.28.104.162:8080/api/ai_status
```

Expected response when configured:
```json
{
  "ai_enabled": true,
  "message": "AI analysis is available",
  "supported_models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  "default_model": "gpt-4o"
}
```

### Test Analysis Endpoint

```bash
curl -X POST http://34.28.104.162:8080/api/analyze_results \
  -H "Content-Type: application/json" \
  -d '{
    "results": {
      "status": "Completed",
      "effluent": {"COD_mg_L": 15.2, "NH4_mg_L": 0.8},
      "removal": {"COD_pct": 95.7, "NH4_pct": 96.8}
    },
    "model": "gpt-4o"
  }'
```

---

## Security Considerations

1. **API Key Protection:** The OpenAI API key is never sent to or visible from client applications
2. **Server-Side Only:** All AI API calls are made from the server
3. **No Key Logging:** The API key is not logged or included in error messages
4. **Rate Limiting:** Consider adding rate limiting to prevent abuse (future enhancement)

---

## Future Enhancements

1. **Rate Limiting:** Add per-client rate limiting for the AI endpoint
2. **Caching:** Cache analysis results for identical inputs
3. **Multiple Providers:** Support additional AI providers (Anthropic, etc.)
4. **Custom Prompts:** Allow clients to customize the analysis prompt
5. **Streaming:** Support streaming responses for long analyses

---

## Dependencies

- `httpx` - Async HTTP client for OpenAI API calls (added to `server_rest.py`)
- OpenAI API access with valid API key

---

## Validation

- [x] `/api/ai_status` endpoint returns correct status
- [x] `/api/analyze_results` returns expert analysis
- [x] v7 workflow JSON validates correctly
- [x] Documentation updated
- [x] Error handling for missing API key works
