---
status: resolved
priority: p2
area: data integrity / notifications
files: app/pipeline/runner.py, app/normalization/models.py, app/notifications/service.py
source: specialized-review of b2cf251..HEAD (2026-11-06)
---

> **Resolution:** Resolved in this commit via option 1: the pipeline now re-matches unchanged jobs and flags those with no alerts_sent row for the current content_hash as CandidateMatch.is_retry; send_digest/send_notifications accept them via needs_alert. Covered by test_failed_digest_re_alerts_on_subsequent_run_after_smtp_recovers (also asserts no re-send on run 3). Cost: one has_been_sent lookup per matched unchanged job. The todo's suggested test name/class differs from the one used.

# A failed global digest silently and permanently drops every match in the run

## Problem

The blueprint requires that jobs persist even when the digest fails. They do.
But a job is re-matched only when `NormalizationResult.should_re_match` is true,
and that is `content_changed`. After a failed digest, the next run sees each job
as unchanged, never re-matches it, and never notifies for it. `send_digest` also
skips candidates where `content_changed` is false.

Verified with a probe test. Run 1 had `send_digest` return `status="failed"`.
Run 2 had SMTP recovered and the same jobs:

```
PROBE send_digest calls after SMTP recovery: 1  matched run2: 0
```

The per-source design had the same issue. Slice 1.2 makes it worse: one SMTP failure
now loses the matches of *every* source in the run, not just one company.
`CLAUDE.md` ("Job will be re-notified on next scan if it still matches") does not
describe this behavior.

## Remediation options (choose one)

1. **Re-match un-alerted jobs (recommended).** In `_process_source`, also re-match an
   unchanged job when it has no `alerts_sent` row for its current `content_hash`.
   Use one batched `alerts_sent` lookup per source, not one query per job. Mark such
   candidates as notifiable so `send_digest` does not drop them because
   `content_changed` is false. Matching is pure in-memory work, so the cost is small.
2. **Pending-alert queue.** Persist matched-but-unsent `(job_key, content_hash)`
   pairs, and drain them at the start of the next digest.

Either way, update the "Notification Errors" paragraph in `CLAUDE.md`.

## Acceptance

- [ ] New test `tests/test_pipeline.py::TestScanPipeline::test_failed_digest_matches_are_retried_next_run`.
  It covers two runs, the first digest failing and the second succeeding, and asserts
  that the second run's `send_digest` receives both candidates.
- [ ] A successfully alerted job is still not re-sent on later unchanged runs.
