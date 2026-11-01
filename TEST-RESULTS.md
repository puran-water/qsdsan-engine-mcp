# Installed-artifact qualification

Python 3.12 on Linux x86-64. Each source distribution and wheel was installed in an isolated uv environment with shared cached dependencies. No private package or database credential was supplied. The stdio client initialized the installed entrypoint and called the tools below on synthetic inputs.

These are targeted packaging and representative domain checks, not a full numerical or cross-platform qualification. Historical broader test counts are in [HISTORICAL-QUALITY.md](HISTORICAL-QUALITY.md).

## Source installation

MCP discovery: PASS; 45 tools.

| Check | Tool | Result |
|---|---|---|
| component-properties | `get_model_components` | PASS |
| create-session | `create_session` | PASS |
| create-synthetic-stream | `create_stream` | PASS |
| invalid-negative-flow | `create_stream` | PASS |
| create-dilution-stream | `create_stream` | PASS |
| build-mixer-unit | `create_unit` | PASS |
| compile-mixer-system | `build_system` | KNOWN ISSUE |
| simulate-synthetic-mixer | `simulate_built_system` | PASS |
| stop-budgeted-test-job | `terminate_job` | PASS |
| read-synthetic-mixer-results | `read_job_results` | KNOWN ISSUE |
| timeseries-limit-rejection | `get_flowsheet_timeseries` | PASS |
| missing-job-pure-read | `read_job_results` | PASS |

## Wheel installation

MCP discovery: PASS; 45 tools.

| Check | Tool | Result |
|---|---|---|
| component-properties | `get_model_components` | PASS |
| create-session | `create_session` | PASS |
| create-synthetic-stream | `create_stream` | PASS |
| invalid-negative-flow | `create_stream` | PASS |
| create-dilution-stream | `create_stream` | PASS |
| build-mixer-unit | `create_unit` | PASS |
| compile-mixer-system | `build_system` | KNOWN ISSUE |
| simulate-synthetic-mixer | `simulate_built_system` | PASS |
| stop-budgeted-test-job | `terminate_job` | PASS |
| read-synthetic-mixer-results | `read_job_results` | KNOWN ISSUE |
| timeseries-limit-rejection | `get_flowsheet_timeseries` | PASS |
| missing-job-pure-read | `read_job_results` | PASS |
