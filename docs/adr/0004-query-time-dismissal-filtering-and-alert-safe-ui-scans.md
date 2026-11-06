---
status: accepted
---

# Query-Time Dismissal Filtering and Alert-Safe UI Scan Triggers

Deleting rows from `jobs` when a user dismisses a job, company, or source would destroy auditability and make 1-click Undo or later restoration impossible. Similarly, sending SMTP emails or recording `alerts_sent` rows during interactive UI-triggered scans (`POST /api/scan/trigger`) would either spam the user or mark `content_hash` versions as alerted without delivering the scheduled consolidated digest (`SOL-001`). We therefore filter active `dismissed_items` (`job`, `company`, and `source` scopes) at query time in `GET /api/jobs` without deleting `jobs` rows, and default `POST /api/scan/trigger` to `send_notifications: bool = False` so interactive scans persist and score new postings while leaving `alerts_sent` untouched for the next scheduled digest run.

## Consequences

- Restoring a dismissal via `DELETE /api/dismissals` (or the 5-second Undo toast) immediately returns the historical job(s) to `GET /api/jobs` with their original `first_seen_at`, `user_status`, and Match Explainability intact.
- Jobs discovered during an alert-safe UI scan have no `alerts_sent` record for their `content_hash` (`Alert Status = pending_alert`); on the next scheduled daemon run (or when `send_notifications=True` is explicitly passed), `CandidateMatch.is_retry` (`needs_alert`) automatically includes them in the consolidated email digest.
