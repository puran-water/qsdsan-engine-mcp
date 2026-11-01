# Known issues

## Complete dynamic-engine regression qualification

The broader source suite retained errors, failures, skips and incomplete cases. Representative public runtime checks are listed separately; a successful example does not qualify all dynamic models.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

Historical native-source snapshot: 1,544 passed, 39 failed, 27 errors and 7 skipped. Those counts predate this cleaned public export. Later repairs exercised subsets only; TEST-RESULTS.md reports the separate installed-artifact qualification. The full source suite has not been repeated against the standalone public package.

## Extend resource-bound and cross-platform coverage

Time-series reads are bounded, but complete work-budget, interruption, concurrency and cross-platform coverage remains open.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Qualify generic replacements for excluded application workflows

Private site datasets, site-specific twin commissioning/calibration and private database publication are excluded. General flowsheet tools remain the public scope; additional general workflows require public input contracts and benchmarks.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

Bundled influent input presets are also excluded. Supply the full influent basis explicitly; choosing a feedstock classification does not supply a dataset.

## Resolve remaining installed-artifact qualification findings

The v0.1.0 source/wheel qualification completed MCP startup but retained these failed checks:

- `compile-mixer-system` on `build_system`.
- `read-synthetic-mixer-results` on `read_job_results`.

See TEST-RESULTS.md. These failures are disclosed for early feedback; they are not claimed as passing validation.

The build_system result declares streams_created as an integer but returns a list, which strict clients reject. The numerical-job check remains subject to the recorded completion/timeout result.
