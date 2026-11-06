---
status: accepted
---

# Persist Match Explainability on Jobs and Re-Evaluate on Startup or Profile Change

Evaluating multi-track `SearchProfile` rules, word-boundary regexes, contextual terms, and snippet extraction dynamically on every `GET /api/jobs` request would prevent indexed SQL filtering/sorting by `primary_profile` and `affinity_score` and degrade triage feed latency. Instead, we persist Match Explainability (`is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `match_snippets`, `matched_fields`) and Triage Status (`user_status`) directly on the `jobs` table, execute `KeywordMatcher.evaluate()` before `JobRepository.upsert()` during pipeline scans, preserve existing `user_status` across `content_hash` updates, and run an in-process SQLite re-evaluation pass on startup (for legacy rows where `is_match IS NULL`) and whenever `search_profiles` are updated via the API.

## Consequences

- `_JOBS_MIGRATION_COLUMNS` in `app/persistence/schema.py` idempotently adds the 8 nullable/defaulted columns to pre-existing SQLite databases under `with engine.begin() as conn:`.
- `JobRepository.upsert()` explicitly preserves `existing.user_status` when an existing job's `content_hash` changes so a user's `saved` or `applied` state is never reset to `new` by an ATS description edit.
- Updating `search_profiles` through `PUT /api/config` re-evaluates all stored jobs in SQLite so the triage feed immediately reflects updated scores and profile assignments without waiting for an external network scan.
