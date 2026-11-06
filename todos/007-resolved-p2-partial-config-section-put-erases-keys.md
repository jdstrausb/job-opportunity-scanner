---
status: resolved
priority: p2
area: data integrity / config
files: app/config/manager.py, tests/test_config_manager.py, tests/test_api.py
source: specialized-review of working tree vs 441dc2a (2026-11-06)
---

# A partial section in `PUT /api/config` silently erases the section's other keys

- **Priority:** P2
- **Axis:** Data Integrity (also Spec Compliance)

## Description

Spec Section 7 lists `PUT /api/config` as "partial or full". Spec Section 4 step 2
says the manager "applies only the modified sections".

`ConfigManager._apply_payload()` (`app/config/manager.py:243-268`) dumps each section
with `model_dump(mode="json", exclude_unset=True)`. `_patch()` (`:83-95`) then
**deletes every key of the existing mapping that is not in the new value**. So:

```
PUT /api/config  {"filtering": {"require_remote": false}}
```

removes `acceptable_countries`, `blocked_companies`, `blocked_domains`,
`max_job_age_days` and the rest from `config.yaml`. Validation passes because every
field has a default, so the user gets 200 and the guardrails silently reset to
defaults. Their comments are lost too.

The shipped UI is safe today only because each page sends the whole section it
loaded from `GET /api/config`. Any other client (curl, a future page, a test) can
destroy hand-written config.

## Remediation Plan

1. Pick merge semantics for mapping sections: a key that is absent from the payload
   section is **kept**, not deleted. In `_patch()`, remove the delete loop for the
   top-level section call. To support real removal, keep the delete loop only for
   nested mappings that the payload fully replaces, or document that clearing a field
   means sending its default value explicitly.
   - Simplest correct change: `_apply_payload` calls a `_merge()` variant that skips
     deletion. `_patch()` (with deletion) stays for list items, which are replaced
     wholesale anyway.
2. Keep `exclude_unset=True`. With merge semantics it is now the right choice,
   because only the keys the client sent are written.
3. Tests:
   - `tests/test_config_manager.py`: a config with a commented `filtering` block, then
     `update_config(ConfigUpdatePayload(filtering=FilteringRules(require_remote=False)))`.
     Assert that `acceptable_countries` and its comment are still on disk.
   - `tests/test_api.py`: the same through `PUT /api/config`.
4. Update the `ConfigUpdatePayload` docstring to say "absent keys inside a section
   are kept".
