# qsdsan-engine-mcp

Build and simulate dynamic wastewater-treatment flowsheets using QSDsan and retrieve structured numerical results.

Experimental release series; `v0.1.0` is the initial pinned release and `main` follows current development. These tools return calculations and structured evidence; the calling LLM supplies explanations and reporting. Engineering review remains necessary.

## Use it in an engineering workflow

**A dynamic-treatment question.** Define a supported model and input basis, run the simulation, then use read_job_results and bounded time-series reads for numerical evidence.

Dynamic scenarios require appropriate kinetics and initialization. They are separate from the steady-state design route.

This is a public calculation component of [Puran Water's engineering toolchain](https://github.com/puran-water). The working system connects these tools to sizing-to-balance, proposal and BEP procedures and retained Studio review publications. Those internal workflows and project records are separate from this standalone package.

Have a project that needs engineering interpretation or an independent review? [Work with Puran Water](https://puranwater.com/contact/). To report a software defect, open a repository issue with a minimal synthetic or redacted input, package revision, expected result and actual result.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run with Python 3.12:

```sh
uvx --python 3.12 --with 'puran-mcp-support @ git+https://github.com/puran-water/fluids-mcp.git@v0.1.0#subdirectory=support/puran-mcp-support' --from 'git+https://github.com/puran-water/qsdsan-engine-mcp.git@v0.1.0' qsdsan-engine-mcp
```

The server uses local MCP stdio. It requires no Puran account or private package. uv shares its dependency cache between isolated tool environments. PyPI publishing has not been configured; the pinned public Git release is the installation path.

## MCP client configuration

Use this configuration in a client supporting local stdio MCP servers:

```json
{
  "mcpServers": {
    "qsdsan-engine-mcp": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--with",
        "puran-mcp-support @ git+https://github.com/puran-water/fluids-mcp.git@v0.1.0#subdirectory=support/puran-mcp-support",
        "--from",
        "git+https://github.com/puran-water/qsdsan-engine-mcp.git@v0.1.0",
        "qsdsan-engine-mcp"
      ]
    }
  }
}
```

Run the server in a user-owned working directory. Local input and result files belong to that user. Hosted HTTP authentication and managed employee access are outside this release.

## Known issues

- **Complete dynamic-engine regression qualification.** The broader source suite retained errors, failures, skips and incomplete cases. Representative public runtime checks are listed separately; a successful example does not qualify all dynamic models.
- **Extend resource-bound and cross-platform coverage.** Time-series reads are bounded, but complete work-budget, interruption, concurrency and cross-platform coverage remains open.
- **Qualify generic replacements for excluded application workflows.** Private site datasets, site-specific twin commissioning/calibration and private database publication are excluded. General flowsheet tools remain the public scope; additional general workflows require public input contracts and benchmarks.
- **Resolve remaining installed-artifact qualification findings.** The v0.1.0 source/wheel qualification completed MCP startup but retained these failed checks:

See [known issue details](KNOWN-ISSUES.md), [test results](TEST-RESULTS.md), [content scan](SCAN-REPORT.md), and [licensing inventory](LICENSE-TABLE.md). These limitations are disclosed for early feedback.

## License and attribution

See LICENSE, NOTICE and THIRD-PARTY-NOTICES for the wrapper and retained upstream material. External dependencies retain their own licenses.

Built by [Puran Water](https://puranwater.com).

Historical source-suite snapshot: 27 error, 1544 passed, 7 skipped, 39 failed. These counts predate the cleaned standalone export; they are not its qualification result. Later repairs covered selected subsets only. See [historical quality scope](HISTORICAL-QUALITY.md).

Bundled feedstock input presets are excluded. Supply a complete influent basis; classification names alone do not provide a design input dataset.

First startup can spend several minutes compiling upstream numerical kernels and creating caches. Allow for this when configuring the client startup timeout.

## Following current development

The `main` branch receives approved development fixes through a one-way source
mirror. Publication still checks secrets, private content, redistribution rights
and package boundaries. Version tags such as `v0.1.0` remain immutable. Installed
copies do not update automatically. To upgrade the server and its public support
package together from the current branches in your existing environment:

```sh
uv pip install --upgrade --reinstall \
  "puran-mcp-support[simulation] @ git+https://github.com/puran-water/fluids-mcp.git@main#subdirectory=support/puran-mcp-support" \
  "qsdsan-engine-mcp @ git+https://github.com/puran-water/qsdsan-engine-mcp.git@main"
```

Pin both Git commits when repeatability is more important than following changes.
The mirror records the source commit in `.puran-mirror.json`.
