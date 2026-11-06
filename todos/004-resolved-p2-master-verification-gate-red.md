---
status: resolved
priority: p2
area: testing / spec compliance (blueprint §4 Master Verification Gate)
source: specialized-review of b2cf251..HEAD (2026-11-06)
---

> **Resolution:** `uv run pytest` now reports 561 passed, 0 failed, 0 errors. Fixtures given permissive FilteringRules or legacy mode, `SMTPClient.send` patched, `get_session()` used as a context manager, Ashby fixture recreated. Code fixes: legacy-mode send failures now set `had_errors`; `SchedulerService.start()` is idempotent; not-found error suggests config.example.yaml. NOT done: ruff/mypy cleanup and the default-on filtering decision for existing configs (README/CHANGELOG or flip defaults).

# The master verification gate fails: 21 tests fail and 7 error

## Problem

Blueprint §4 requires `uv run pytest -v` to pass cleanly. In the current working
tree, which includes the uncommitted files from todo 001, the result is:

```
21 failed, 535 passed, 7 errors
```

Triage:

| Group | Tests | Likely cause | Introduced by this range? |
| --- | --- | --- | --- |
| Pipeline integration | `tests/integration/test_end_validation.py` (3), `tests/integration/test_pipeline_notifications.py` (8) | `total_upserted == 0`: the fixtures do not pass the default `FilteringRules` (`require_remote`, seniority/role filters). Filtering was wired into `ScanPipeline` in this range, and Slice 1.2 updated only `tests/test_pipeline.py` fixtures. Some tests also patch the removed `SMTPClient.send_email` | **Yes**, at least partly |
| Notification integration | `tests/integration/test_notifications.py` (5 fail and 5 error) | `'_GeneratorContextManager' object has no attribute 'execute'`: the fixture uses `get_session()` as a session | Probably not |
| Ashby adapter | `tests/test_adapters.py::TestAshbyAdapter` (2 errors) | Fixture files `tests/fixtures/ats_responses/ashby_*.json` are deleted in the working tree | Working-tree change |
| Config/main/scheduler | `test_config_file_not_found`, two `TestLoadRuntimeConfig` tests, two `TestSchedulerService` tests | Message wording, Pydantic `ValidationError` vs `ConfigurationError`, APScheduler restart | Probably not |

Also, `ruff check .` (716 findings) and `mypy app` (79 errors) both fail. Most of these
come before this range (legacy `Column` assignments, f-string logging, `typing.List`),
but the new code follows the same patterns.

Behavior change to confirm: the defaults in `FilteringRules` are on
(`require_remote=True`, seniority, role, and experience filters). Now that filtering
runs in the pipeline, any existing `config.yaml` without a `filtering:` block will
silently drop non-remote, senior, and non-engineering roles. That is the cause of the
integration-test failures above.

## Remediation

1. Do todo 001 first, so that the suite runs against committed code.
2. Update the integration fixtures so that sample jobs pass the default filters, or
   give those tests an explicit permissive `filtering:` block. Replace the
   `send_email` patches with the current `SMTPClient` API.
3. Fix or quarantine the failures that came before this range, with an explicit marker
   that links to an issue. Do not leave them red silently.
4. Decide whether the default-on filtering is intended for upgraded configs. If it is,
   document it in `README.md`/`CHANGELOG`. If not, default `require_remote` and the
   role/seniority toggles to `False`.
5. Optional: run `ruff check --fix` on the files this range touched, and add ruff/mypy
   to the gate only for new files.

## Acceptance

- [ ] `uv run pytest` reports 0 failed and 0 errors, or only explicitly marked xfails linked to issues.
- [ ] `uv run python verify_config.py` passes.
