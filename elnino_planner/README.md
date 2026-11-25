# planner (WP-0 skeleton)

Everything for work package 0 lives in this folder. Nothing outside it was changed.

| Path | What |
|---|---|
| `settings.py` | Env-var settings. `PLANNER_CONTACT`, `PLANNER_HTTP_TIMEOUT_S`, `PLANNER_HTTP_RETRIES`, `PLANNER_LOG_LEVEL`, `PLANNER_COUNTY_FIPS`, `PLANNER_LLM_MODEL` |
| `logging_setup.py` | Privacy-safe one-line `key=value` logging |
| `tools/result.py` | `ToolResult` envelope, `Status`, `FailureReason` |
| `tools/http_client.py` | Timeout, one retry, TTL cache, typed failures |
| `tools/dummy.py` | Template that real tools copy |
| `config/` | County pack schema, loader, `counties/06085.yaml` |
| `scripts/` | Troubleshooting commands (below) |
| `tests/` | `unittest`, no network |

Run from the repo root:

```bash
uv run python -m unittest discover -s planner/tests -t . -v   # all tests
uv run python -m planner.scripts.check_env                    # settings + county pack health
uv run python -m planner.scripts.demo_dummy_tool              # watch every failure state
```

Troubleshooting: set `PLANNER_LOG_LEVEL=DEBUG`; every HTTP call logs one line
(`tool= status= duration_ms= reason= attempts= cached= host= http_status=`). `reason` is the
`FailureReason`. Coordinates and query strings are never logged.
