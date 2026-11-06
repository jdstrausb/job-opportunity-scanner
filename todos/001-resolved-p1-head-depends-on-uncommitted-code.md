---
status: resolved
priority: p1
area: repository / commit hygiene
source: specialized-review of b2cf251..HEAD (2026-11-06)
---

> **Resolution:** Resolved in 27a7c3f: dependencies committed on top of the feature commits (bisect across b2cf251..14b7fcd stays broken; history not rewritten). Clean worktree of HEAD imports app.pipeline/adapters/notifications and pytest collects with no import errors. Not done: filter-subsystem split of 4162b31, squash of 6baa1e9, Ashby fixture deletions decision.

# HEAD is not importable without uncommitted working-tree files

## Problem

The 11 commits in `6baa1e9~1..HEAD` reference code that exists only as
uncommitted or untracked files. A clean checkout of `HEAD` fails at import:

```
app/adapters/__init__.py:39: from .registry import CompanyRegistry, expand_sources_with_registry
E   ModuleNotFoundError: No module named 'app.adapters.registry'
```

To reproduce, run `git worktree add --detach /tmp/headwt HEAD`, then run `pytest` in `/tmp/headwt`.

Committed code that depends on uncommitted code:

| Committed caller | Uncommitted dependency |
| --- | --- |
| `app/adapters/__init__.py` (from 49d6bba/14b7fcd) | `app/adapters/registry.py` (untracked) |
| `app/pipeline/runner.py::_send_global_digest` calls `notification_service.send_digest` | `send_digest` exists only in modified `app/notifications/service.py` (+183 lines) |
| `app/notifications/templates.py` digest rendering | modified `templates.py` (+50 lines) and untracked `email_templates/digest_subject.j2` |
| `SerpApiSearchAdapter._hydrate_ashby` uses `AshbyAdapter.API_ENDPOINT + org` | At HEAD the endpoint is `https://jobs.ashby.com/api/graphql`. The `posting-api/job-board/` URL and the `secondaryLocations`/`isRemote`/`addressCountry` extraction (which Slice 1.1 relies on) exist only in modified `app/adapters/ashby.py` |
| Wildcard `identifier: "*"` sources | modified `app/config/loader.py` |

Related hygiene issues:
- `4162b31` ("add DomainFilter") also adds `age_filter.py`, `location_filter.py`,
  `role_filter.py`, `models.py`, `service.py`, and about 200 lines of `FilteringRules`
  (about 1,900 lines in total). Its tests (`tests/test_location_filter.py`,
  `tests/test_role_filter.py`) are still untracked.
- `b2cf251` and `6baa1e9` have the same subject line. `b2cf251` passes
  `secondary_locations=` to `Job`, but the field arrives on `Job` only in `ea836e3`.
  `git bisect` breaks on this range.

## Remediation

1. Put the dependencies in a prerequisite commit before the feature commits.
   You can also commit them on top, but that leaves bisect broken. The commit
   must include `app/adapters/registry.py`, `app/adapters/{ashby,greenhouse,lever}.py`,
   `app/config/loader.py`, `app/notifications/service.py`, `app/notifications/templates.py`,
   `app/notifications/email_templates/digest_subject.j2`, `tests/test_registry.py`,
   `tests/test_location_filter.py`, `tests/test_role_filter.py`, and
   `tests/integration/test_registry_integration.py`. Decide separately about the
   deleted Ashby GraphQL fixtures.
2. Optionally, rebase to split the Age/Location/Role filter subsystem out of
   `4162b31` and squash `6baa1e9` into `b2cf251`.
3. Gate: in a clean `git worktree` of the new HEAD, `uv run pytest` must collect
   with no import errors.

## Acceptance

- [ ] A clean checkout of the branch tip imports `app.pipeline` and runs the test suite.
- [ ] `git status` shows no tracked-path modifications that the committed code needs.
