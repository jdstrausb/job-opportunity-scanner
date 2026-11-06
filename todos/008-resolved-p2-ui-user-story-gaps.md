---
status: resolved
priority: p2
area: spec compliance / frontend
files: frontend/src/routes/+page.svelte, frontend/src/lib/components/ScanControlBar.svelte, frontend/src/lib/components/DismissSplitButton.svelte, app/api/routes/sources.py
source: specialized-review of working tree vs 441dc2a (2026-11-06)
---

# Partially implemented user stories (US-3, US-10, US-13, US-26, US-31)

- **Priority:** P2
- **Axis:** Spec Compliance

## Description

The backend contracts match the spec. These acceptance criteria in
`docs/specs/sveltekit-frontend-and-fastapi-bridge-spec.md` are not met:

| Story | Spec asks | Current state |
|---|---|---|
| US-3 | filter the feed by `source_type`, `source_identifier` | API supports it (`routes/jobs.py:21-22`); feed has no control (`+page.svelte:75-86`) |
| US-10 | "Dismiss Job" button on **every job card** and the detail pane | `DismissSplitButton` only in detail pane (`+page.svelte:468`); `compact` prop unused |
| US-13 | Undo restores jobs "to their exact position" | Undo calls `reloadFeed()` from offset 0 (`+page.svelte:96,198-205`); "Load more" pages are lost |
| US-26 | Wildcard rollups "grouped by ATS type" | one rollup per wildcard config entry (`routes/sources.py:84-134`); two `greenhouse` `*` entries give two cards |
| US-31 | header shows the configured `scan_interval` | `ScanControlBar.svelte` shows only the countdown (`:119-121`) |

Copy differences (fix at the same time):
- US-11: the chip should read `"Comp/Location mismatch"`, not `'Comp/Location'` (`DismissSplitButton.svelte:13`).
- US-32: the checkbox should read `"Also send email notifications now"` (`ScanControlBar.svelte:191`).

## Remediation Plan

1. **US-3:** add `source_type` (select: greenhouse/lever/ashby/rss/serpapi_google)
   and `source_identifier` (text, debounced) to the feed filter bar. Pass both through
   the existing `JobQueryFilters` in `api.listJobs`.
2. **US-10:** render `<DismissSplitButton compact …>` in each job card's action row.
   Use `stopPropagation` so the click does not also select the card.
3. **US-13:** on Undo, re-fetch with `limit = currentItems.length` (capped at 200,
   paged if larger) from offset 0, and keep the selected `job_key` and scroll
   position. Do not reset to the first page.
4. **US-26:** in `get_sources`, merge the companies of every wildcard entry with the
   same `type` into one `WildcardRollupHealth`. Use `enabled = any(entry.enabled)`
   and de-duplicate companies by identifier. Add a test in `tests/test_api.py` with
   two greenhouse wildcard entries.
5. **US-31:** render `status.scan_interval` (e.g. "every 15m") next to the countdown.
6. Fix the two labels above.
7. Optional, P3: US-6 asks for "base weight" in the `score_breakdown` bars. This needs
   the engine to store pre-boost points per term (`app/matching/engine.py:214-218`).
   Track it separately if it is not done here.
