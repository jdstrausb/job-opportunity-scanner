---
id: SOL-001
title: Run-Wide Digest Alert Retry via needs_alert
date: 2026-04-06
tags: [pipeline, notifications, sqlite, resilience]
---

## Context

When evaluating job postings across multiple ATS sources, the pipeline computes a `content_hash` (derived from normalized title, description, and location metadata). In the initial architecture, postings were matched and queued for notification strictly when `content_changed` was true. Furthermore, jobs were committed to the `jobs` SQLite table before the notification dispatch completed.

When batch digest dispatch failed (e.g., SMTP timeout, network partition, or invalid relay credentials), the jobs had already been persisted with their latest `content_hash`. On subsequent pipeline runs, the normalizer flagged those jobs as unchanged (`content_changed = False`), causing the pipeline to skip keyword matching and notification. Consequently, every job match in that batch was permanently lost from alert notifications without human intervention or manual database resets.

## Solution

The pipeline decouples database persistence state from external delivery acknowledgement:

1. **Unchanged Candidate Matching & Retry Detection**: In `ScanPipeline._process_source()`, the pipeline evaluates keyword criteria on all newly fetched postings as well as existing unchanged postings. When a posting matches the criteria:
   ```python
   if match_result.is_match:
       is_retry = (
           not norm_result.should_re_match
           and not alert_repo.has_been_sent(
               norm_result.job.job_key, norm_result.job.content_hash
           )
       )
   ```
2. **`needs_alert` Property on CandidateMatch**: The `CandidateMatch` domain model provides a consolidated gate:
   ```python
   @property
   def needs_alert(self) -> bool:
       """Whether this candidate is eligible for an alert (changed, or retry of a lost alert)."""
       return self.content_changed or self.is_retry
   ```
3. **Delivery Filtering & Acknowledgement**: Both `send_digest()` and `send_notifications()` inspect `candidate.needs_alert`. Unchanged jobs with prior successful alerts (`is_retry=False`) are excluded from notification payloads. Only when SMTP dispatch succeeds are corresponding records written to `alerts_sent` via `alert_repo.record_alert()`.

If an alert fails to send, the database stores the latest job snapshot, but `alerts_sent` lacks an entry for `(job_key, content_hash)`. On the very next scan, `is_retry` evaluates to `True`, triggering re-inclusion in the digest until delivery succeeds.

## Friction Points & Gotchas

- **Query Overhead on Repeated Runs**: Checking `has_been_sent()` individually for every job could create database contention in SQLite. By gating the lookup only on matching jobs (`if match_result.is_match:`), the lookup cost is limited to the subset of matching candidates.
- **Deduplication vs. False Alarms**: Without checking against `content_hash`, a retry mechanism risks spamming users on every run. Checking both `job_key` AND `content_hash` in `alert_repo.has_been_sent(job_key, content_hash)` ensures that successfully delivered postings are never duplicated on subsequent runs.
- **Aggregates & Metrics**: Run statistics needed to distinguish between freshly discovered matches and retried candidates so metrics (`stats.matched_count`, `stats.notified_count`) stay accurate without double-counting pipeline throughput.

## Reusable Insight

Never treat database persistence as proof of external delivery. Separate persistence acknowledgements from external transmission acknowledgements, and ensure pipeline query loops identify unacknowledged records on subsequent runs.
