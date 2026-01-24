# Phase 11: Production Improvements and Documentation

**Status:** Complete

**Version:** 3.0.6

**Date:** January 2026

**Author:** Rainer Gaier / Claude Code (Opus 4.5)

---

## Overview

Phase 11 delivers production stability improvements and comprehensive documentation for the QSDsan Engine MCP server. Key features include configurable simulation timeouts, real-time progress reporting, and user-facing documentation.

---

## Changes Summary

| Category       | Change                                    | Impact                             |
| -------------- | ----------------------------------------- | ---------------------------------- |
| Timeout        | Configurable `timeout_seconds` parameter  | Prevents runaway simulations       |
| Progress       | `[PROGRESS]` log markers in all templates | Real-time job monitoring           |
| Logging        | Async stream readers for stdout/stderr    | Captures logs even on timeout      |
| Performance    | Fast `get_version` tool                   | Sub-second response (was ~18s)     |
| Infrastructure | Absolute path handling                    | Fixes CWD issues in Claude Desktop |
| Documentation  | USER_GUIDE.md, API_REFERENCE.md           | Comprehensive user documentation   |

---

## Feature 1: Simulation Timeout

### Problem

Simulations could run indefinitely with no way to terminate them automatically. Job `fdf6cc75` ran for 16+ minutes before manual termination.

### Solution

Added `timeout_seconds` parameter (default 300s/5 minutes) to `simulate_system` and `simulate_built_system` MCP tools.

### Implementation

**`utils/job_manager.py`:**

- Added `timeout_seconds` parameter to `execute()` method
- Implemented `asyncio.wait_for()` with timeout in `_monitor_job()`
- Graceful termination: SIGTERM first, then SIGKILL after 2s
- New job status: `"timeout"` (distinct from `"failed"`)

**`server.py`:**

- Added `timeout_seconds: float = 300.0` to `simulate_system()`
- Added `timeout_seconds: float = 300.0` to `simulate_built_system()`
- Timeout info included in job status response

### Usage

```python
# Default 5-minute timeout
result = await simulate_system(template="mle_mbr_asm2d", influent={...})

# Custom timeout for complex simulations
result = await simulate_system(
    template="anaerobic_cstr_madm1",
    influent={...},
    timeout_seconds=600  # 10 minutes
)

# No timeout (not recommended)
result = await simulate_system(..., timeout_seconds=0)
```

---

## Feature 2: Progress Reporting

### Problem

No visibility into simulation progress during execution. Users couldn't tell if a job was progressing or stuck.

### Solution

Added `[PROGRESS]` print statements to all templates, parsed by JobManager for status reporting.

### Implementation

**All templates now emit progress:**

```python
print(f"[PROGRESS] Starting MLE-MBR simulation: {duration_days} days", flush=True)
print(f"[PROGRESS] Running ODE solver (method=RK23)...", flush=True)
print(f"[PROGRESS] Simulation complete: 100%", flush=True)
```

**Templates modified:**

- `templates/aerobic/mle_mbr.py`
- `templates/aerobic/ao_mbr.py`
- `templates/aerobic/a2o_mbr.py`
- `templates/anaerobic/cstr.py`
- `utils/simulate_madm1.py`

**JobManager progress parsing:**

- `_parse_progress()` recognizes `[PROGRESS]` pattern
- Progress shown in `get_job_status()` response
- Last progress captured in stderr on timeout

### Status Response Example

```json
{
  "job_id": "abc12345",
  "status": "running",
  "elapsed_time_seconds": 45.2,
  "progress": {
    "message": "Running ODE solver (method=RK23)..."
  },
  "timeout_seconds": 300,
  "time_remaining_seconds": 254.8
}
```

---

## Feature 3: Real-Time Log Capture

### Problem

On Windows, subprocess stdout/stderr was empty until process completion. Timeout scenarios had no logs.

### Solution

Implemented async stream readers that capture output line-by-line in real-time.

### Implementation

**`utils/job_manager.py`:**

```python
async def _stream_to_file(
    self,
    stream: asyncio.StreamReader,
    file_path: Path,
    stop_event: asyncio.Event,
) -> None:
    """Read from async stream and write to file line by line."""
    with open(file_path, "wb") as f:
        while not stop_event.is_set():
            line = await asyncio.wait_for(stream.readline(), timeout=0.5)
            if line:
                f.write(line)
                f.flush()  # Immediate flush for real-time capture
```

**Key changes:**

- Subprocess uses `PIPE` instead of file handles
- `PYTHONUNBUFFERED=1` environment variable set
- Async tasks read streams and write to files with immediate flush
- Works on both Windows and Unix

---

## Feature 4: Fast `get_version` Tool

### Problem

`get_version` took ~18 seconds because it imported qsdsan/biosteam modules.

### Solution

Use `importlib.metadata.version()` to read package versions without importing.

### Implementation

**`server.py`:**

```python
@mcp.tool()
async def get_version() -> Dict[str, Any]:
    from importlib.metadata import version, PackageNotFoundError

    try:
        qsdsan_version = version("qsdsan")
    except PackageNotFoundError:
        qsdsan_version = "not installed"

    # ... same for biosteam ...
```

**`core/version.py`:**

- New file as single source of truth for version
- `get_version_info()` helper uses same fast lookup

### Performance

- Before: ~18 seconds (cold start importing heavy modules)
- After: <1 second (reads package metadata only)

---

## Feature 5: Absolute Path Handling

### Problem

Jobs failed when MCP server was invoked from different working directories (e.g., Claude Desktop).

### Solution

Use absolute paths derived from `__file__` for all file operations.

### Implementation

**`server.py`:**

```python
# Use absolute paths relative to this file
_BASE_DIR = Path(__file__).parent.absolute()
_JOBS_DIR = _BASE_DIR / "jobs"

job_manager = JobManager(max_concurrent_jobs=3, jobs_base_dir=str(_JOBS_DIR))
session_manager = FlowsheetSessionManager(sessions_dir=_JOBS_DIR)
```

---

## Documentation Added

### USER_GUIDE.md (697 lines)

Comprehensive user documentation covering:

- Architecture overview
- Typical workflows (template-based and custom flowsheet)
- Use case walkthrough (MLE-MBR simulation)
- Jobs folder structure
- Output files reference
- MCP tools quick reference
- Environment configuration
- Troubleshooting guide

### API_REFERENCE.md (901 lines)

Complete API reference for all 30 MCP tools:

- Tool signatures with parameters
- Return value schemas
- Usage examples
- Error handling

---

## Files Modified

| File                           | Lines Changed | Description                          |
| ------------------------------ | ------------- | ------------------------------------ |
| `pyproject.toml`               | 1             | Version 3.0.5 → 3.0.6                |
| `server.py`                    | +105          | get_version, timeout, absolute paths |
| `utils/job_manager.py`         | +260          | Timeout, async streams, progress     |
| `utils/simulate_madm1.py`      | +6            | Progress reporting                   |
| `utils/flowsheet_builder.py`   | +3            | Path handling                        |
| `templates/aerobic/mle_mbr.py` | +9            | Progress reporting                   |
| `templates/aerobic/ao_mbr.py`  | +9            | Progress reporting                   |
| `templates/aerobic/a2o_mbr.py` | +8            | Progress reporting                   |
| `templates/anaerobic/cstr.py`  | +4            | Minor adjustments                    |

## Files Added

| File                                                | Lines | Description             |
| --------------------------------------------------- | ----- | ----------------------- |
| `core/version.py`                                   | 41    | Version source of truth |
| `USER_GUIDE.md`                                     | 697   | User documentation      |
| `API_REFERENCE.md`                                  | 901   | API reference           |

---

## API Changes

### New Tool

- `get_version()` - Returns engine and dependency versions

### Modified Tools

- `simulate_system()` - Added `timeout_seconds: float = 300.0`
- `simulate_built_system()` - Added `timeout_seconds: float = 300.0`

### New Job Status

- `"timeout"` - Job exceeded time limit and was terminated

### Backward Compatibility

All changes are backward compatible. The `timeout_seconds` parameter has a default value.

---

## Testing

### Verification Performed

1. Timeout test: 20-second timeout correctly terminates 30-day simulation
2. Log capture: stdout.log contains `[PROGRESS]` messages
3. Timeout stderr: Contains "Last progress" message
4. get_version: Returns in <1 second
5. Existing 280 tests continue to pass

### Test Commands

```bash
# Run all tests
venv\Scripts\python.exe -m pytest tests/ -v

# Skip slow tests
venv\Scripts\python.exe -m pytest tests/ -v -m "not slow"
```

---

## Version History

| Version | Date       | Changes                                      |
| ------- | ---------- | -------------------------------------------- |
| 3.0.6   | 2026-01-24 | Phase 11: Timeout, progress, documentation   |
| 3.0.5   | 2026-01-22 | Async stream readers for Windows log capture |
| 3.0.4   | 2026-01-22 | Initial timeout with file handle approach    |
| 3.0.3   | 2026-01-21 | Initial timeout implementation               |

---

## Related Documents

- [USER_GUIDE.md](../../USER_GUIDE.md) - User documentation
- [API_REFERENCE.md](../../API_REFERENCE.md) - API reference