# Job Opportunity Scanner

Automated job discovery, filtering, multi-track affinity matching, notification, and interactive triage context for monitoring Applicant Tracking Systems (ATS), feeds, and search discovery channels.

## Language

### Discovery & Sources

**Source**:
A configured origin of job postings polled by the scanner, identified uniquely by the pair of ATS/channel type and source identifier.
_Avoid_: Board, provider, feed, endpoint

**Company Registry**:
A curated per-ATS catalog of company identifiers, display names, and enablement flags used for bulk source onboarding.
_Avoid_: Company list, tenant table, directory

**Wildcard Source**:
A single source configuration entry (`identifier: "*"`) that expands at runtime into concrete sources for every enabled company in the corresponding ATS Company Registry.
_Avoid_: Glob source, bulk source, catch-all source

**Wildcard Source Rollup**:
An aggregated health summary grouping all registry-expanded sources for an ATS type into total, healthy, failing, and unscanned counts with drill-down access to individual company statuses.
_Avoid_: Source group, ATS cluster, registry summary

### Filtering & Suppression

**Pre-Persistence Guardrail**:
A deterministic rule evaluated after normalization and before database storage (`AgeFilter`, `LocationFilter`, `RoleFilter`, `DomainFilter`) that rejects ineligible postings so they never enter storage or matching.
_Avoid_: Post-filter, search filter, soft filter

**Reactive Dismissal**:
A persistent user mute (`DismissedItem`) applied at `job`, `company`, or `source` scope that suppresses matching postings from future pipeline ingestion and hides matching stored jobs from the triage feed at query time without deleting historical records.
_Avoid_: Hard delete, blocklist entry, archive

### Matching & Scoring

**Search Profile**:
A named, independently evaluated search track combining boolean keyword gates (required terms, OR keyword groups, exclusions, word-boundary terms, and contextual terms) with weighted affinity scoring parameters.
_Avoid_: Search query, saved search, filter preset

**Affinity Score**:
A numeric relevance score computed for a job that passes a Search Profile's boolean gates, summing matched term weights with a configurable multiplier for terms appearing in the job title.
_Avoid_: Match percentage, rank score, confidence score

**Match Explainability**:
The persisted breakdown attached to an evaluated job recording whether it matched (`is_match`), the winning `primary_profile`, all `matched_profiles`, the `affinity_score`, per-term `score_breakdown`, `matched_fields`, and contextual `match_snippets`.
_Avoid_: Match debug log, raw match output, trace

**Zero-Network Config Preview**:
An in-process dry-run evaluation of candidate Search Profiles against already-persisted jobs that returns match counts, score shifts, and sample hits without polling external ATS endpoints.
_Avoid_: Live test scan, simulation run, mock scan

### Triage & Notification Lifecycle

**Triage Status** (`user_status`):
The user-managed workflow state of a persisted job (`new`, `saved`, or `applied`), preserved across content-hash updates when a posting's description or metadata is revised.
_Avoid_: Job stage, application pipeline status, read state

**Alert Status**:
The notification delivery state of a matching job version derived from `alerts_sent` (`alerted` when the current `content_hash` has a recorded notification, or `pending_alert` when matching and unalerted).
_Avoid_: Email flag, notification boolean

**Digest Retry** (`needs_alert`):
The recovery condition where an unchanged matching job whose current `content_hash` lacks an `alerts_sent` record (`is_retry`) is automatically re-queued for the next notification dispatch.
_Avoid_: Manual resend, dead-letter replay

**Alert-Safe Scan Trigger**:
An on-demand pipeline execution initiated from the UI that fetches, normalizes, filters, matches, and persists jobs with notifications disabled by default (`send_notifications=False`), leaving `alerts_sent` untouched so scheduled digest runs still deliver newly discovered matches.
_Avoid_: Silent scan, dry-run scan, test scan

### Configuration Management

**Config Round-Trip Hot-Reload**:
The atomic update cycle that patches raw `config.yaml` in place (preserving YAML comments and unexpanded `identifier: "*"` Wildcard Sources), validates the candidate configuration, swaps the file atomically, and updates the live pipeline, matcher, and scheduler under the pipeline lock without restarting the process.
_Avoid_: Config overwrite, daemon restart, raw YAML dump
