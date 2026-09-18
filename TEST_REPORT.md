# Test report

| Suite | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Hamilton integration | 18 | 0 | 0 |
| MetricFlow engine, non-slow selection | 1168 | 0 | 50 |
| MetricFlow semantic interfaces | 464 | 0 | 2 |
| MetricFlow semantics | 252 | 1 | 2 |

Integration assertions cover real Hamilton DAG nodes, native compiler parity,
joined aggregates, ratio/derived/cumulative metrics, filters, ordering, time bounds,
manifest round trips, invalid requests and explain without execution.

The unmodified upstream `test_create_new` timing benchmark failed its performance
factor threshold (required >=0.4; observed below that threshold). An isolated rerun
of the singleton test file also produced 5 passes and the same benchmark failure.
That rerun is separate evidence, not additional unique coverage. This is not an
all-green upstream test result. Suites run separately because their pytest options
overlap. Complete MetricFlow source: 5,181 hash-verified files.

## Reproduction and scope

Run `hatch run test` for the added integration and
`hatch run python scripts/run_upstream_tests.py` for the documented upstream selection.
JUnit XML files are in `reports/`; `environment.txt` records installed versions.
Installed wheel CLI entry points were smoke-tested from outside the source tree.
`python scripts/verify_sources.py` validates every copied upstream file, including symlinks.

Full source copies do not mean all upstream tests were executed. External warehouse,
cloud service, dbt end-to-end, slow/performance and complete cross-platform matrices
were not validated. Tests ran on macOS ARM64 with Python 3.11 and local DuckDB.
Deprecation warnings remain in upstream dependencies. No upstream code was changed
to suppress failures. GitHub CI runs the new integration tests and source verification.
