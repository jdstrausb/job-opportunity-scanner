## Step 6 — Normalization & Matching Detailed Design

### Goals & Scope
- Convert `RawJob` instances from ATS adapters into canonical `Job` domain models with deterministic keys, hashes, and timestamp metadata.
- Evaluate each normalized job against user-defined `SearchCriteria` and capture a reusable match rationale for downstream alerting.
- Provide deterministic orchestration hooks so the scheduler/pipeline (Step 8) can chain adapters → normalization → persistence → matching → notification without rereading the PRD.

### Assumptions
- Remote-first filtering is expressed via search criteria keywords (e.g., `"remote"`, `"distributed"`) and by examining the normalized `location` field; no additional config schema is introduced in Step 6.
- Step 6 runs within the same transaction boundary that persists jobs (Step 4) to ensure `first_seen_at`/`last_seen_at` are consistent; parallel pipeline execution is out of scope for v1.0.

### Integration Points
- `app/domain/models.py`: `RawJob` (input) and `Job` (output) definitions that the normalizer must populate.
- `app/utils/hashing.py`: `compute_job_key` and `compute_content_hash` used while generating normalized jobs.
- `app/utils/timestamps.py`: `utc_now` and `ensure_utc` for timestamp orchestration.
- `app/config/models.py`: `SearchCriteria` contract consumed by the matcher; terms arrive pre-normalized to lowercase.
- `app/utils/highlighting.py`: `normalize_for_matching`, `extract_snippets_with_keywords`, `format_matched_terms` leveraged when building rationale.
- `app/persistence/repositories.py`: `JobRepository` for lookup/upsert and `AlertRepository` (later) for alert deduplication; normalization must be aware of existing records.
- `app/adapters/*`: Produce `RawJob` payloads; Step 6 should not mutate adapter outputs in place.
- `app/notifications` (Step 7 placeholder): Will consume match rationale strings/snippets prepared here.
- `tests/fixtures/` sample payloads: supply realistic adapter outputs for unit tests.

### Data Contracts
- `NormalizationContext` (new, `app/normalization/models.py`): immutable context containing `SourceConfig`, scan timestamp, and optionally an existing `Job` from persistence.
- `NormalizationResult`: dataclass with
  - `job: Job`
  - `existing_job: Job | None`
  - `is_new: bool`
  - `content_changed: bool`
  - `matchable_text: MatchableText`
  - `raw_job: RawJob` (kept for debugging/logging)
- `MatchableText` (shared between normalization/matching): original strings and pre-normalized variants for `title`, `description`, and `location`, plus concatenated `full_text` for quick substring checks.
- `MatchResult` (new, `app/matching/models.py`): encapsulates
  - `is_match: bool`
  - `matched_required_terms: set[str]`
  - `missing_required_terms: set[str]`
  - `matched_keyword_groups: list[set[str]]` (indexed to original group order)
  - `missing_keyword_groups: list[int]`
  - `matched_exclude_terms: set[str]` (must be empty for a pass)
  - `matched_fields: dict[str, set[str]]` (`title` / `description` / `location`)
  - `snippets: list[str]`
  - `summary: str` (preformatted via `format_matched_terms`)
- `CandidateMatch` (new coordination struct, `app/matching/models.py` or `app/normalization/models.py`): packages `NormalizationResult`, `MatchResult`, and any persistence actions the pipeline must perform (e.g., `should_upsert`, `should_notify`).

### Normalization Utilities

#### Responsibilities
- Ensure every `RawJob` is transformed into a `Job` with stable keys, sanitized text, and accurate timestamps.
- Detect whether a job is new, unchanged, or an updated version by comparing content hash and stored timestamps.
- Produce `MatchableText` payloads that preserve original casing for presentation and normalized casing for keyword comparisons.
- Surface validation/logging hooks for missing mandatory adapter fields without leaking into adapter packages.

#### Implementation Blueprint
1. **Create `app/normalization/models.py`:**
   - Define `NormalizationContext`, `MatchableText`, and `NormalizationResult` dataclasses with type hints and docstrings.
   - Provide helper constructors (e.g., `MatchableText.from_job(job: Job)`) that build normalized strings via `normalize_for_matching`.
2. **Implement `JobNormalizer` in `app/normalization/service.py`:**
   - Constructor accepts `JobRepository` (or callable lookup), `scan_timestamp: datetime`, and logger instance.
   - `normalize(raw_job: RawJob, source_config: SourceConfig) -> NormalizationResult` steps:
     1. Compute `job_key` using `compute_job_key(source_config.type, source_config.identifier, raw_job.external_id)`.
     2. Pull existing job via `JobRepository.get_by_key`.
     3. Sanitize/trim `title`, `description`, and `location`; collapse whitespace; default missing description to empty string and log warning.
     4. Derive timestamps:
        - `posted_at` / `updated_at` from `RawJob` (already UTC via domain validators).
        - `seen_at = ensure_utc(scan_timestamp or utc_now())`.
        - `first_seen_at` inherits from existing job or uses `seen_at`.
        - `last_seen_at` always set to `seen_at`.
     5. Compute `content_hash` from sanitized title/description/location via `compute_content_hash`.
     6. Populate `Job` and compare (`existing_job.content_hash` vs new hash, fallback to timestamp comparison when prior hash missing).
     7. Build `MatchableText` containing:
        - `title_original`, `title_normalized`
        - `description_original`, `description_normalized`
        - `location_original`, `location_normalized` (empty string when None)
        - `full_text_normalized` concatenation for fast substring checks.
     8. Set flags:
        - `is_new = existing_job is None`
        - `content_changed = is_new or content_hash != existing_job.content_hash`
        - `should_upsert = is_new or content_changed`
     9. Return `NormalizationResult`.
3. **Batch Support (`process_batch`)**:
   - Add generator/iterator that accepts iterable of `(RawJob, SourceConfig)` pairs and yields `NormalizationResult` while reusing the same scan timestamp.
   - Log per-job errors but continue processing remaining jobs.
4. **Update `app/normalization/__init__.py`:**
   - Export `JobNormalizer`, `NormalizationResult`, and `MatchableText`.
5. **Instrument Logging:**
   - Use structured logs (`logger.info` / `logger.debug`) with job key, company, `is_new`, and `content_changed` fields.
6. **Documentation & Type Safety:**
   - Add docstrings describing invariants (e.g., `matchable_text.title_normalized` is always lowercase, punctuation-stripped).

### Keyword Matching Engine

#### Responsibilities
- Evaluate normalized jobs against `SearchCriteria` while being case- and punctuation-insensitive.
- Track exactly which terms/groups/exclusions matched and where they were found.
- Provide snippets and formatted reasoning for notification templates.

#### Implementation Blueprint
1. **Create `app/matching/models.py`:**
   - Define `MatchResult` and `CandidateMatch` (or place `CandidateMatch` alongside normalization if preferable).
   - Provide helper methods like `MatchResult.should_notify()` that returns `is_match and not matched_exclude_terms`.
2. **Implement `KeywordMatcher` in `app/matching/engine.py`:**
   - Constructor accepts `SearchCriteria` and optional `logger`.
   - `evaluate(job: Job, matchable_text: MatchableText) -> MatchResult` algorithm:
     1. Precompute searchable corpora:
        - Use `matchable_text.title_normalized`, `description_normalized`, `location_normalized`.
        - Build `field_index: dict[str, str]` for quick membership checks.
     2. Required terms:
        - For each term in `search_criteria.required_terms`, check membership in any field (substring match on normalized strings).
        - Record matched/missing sets and field hits.
     3. Keyword groups:
        - For each group (list of terms), determine which terms appear in any field.
        - Append matched terms per group; track missing group indices.
     4. Exclude terms:
        - If any term appears in any field, flag failure (`matched_exclude_terms`) and short-circuit to `is_match = False`.
     5. Overall decision:
        - `is_match` when all required terms present, every group has ≥1 match, and no exclude terms found.
     6. Snippets & summary:
        - Build `all_matched_terms` from required + selected group terms (lowercase terms).
        - Generate snippets from original description (`matchable_text.description_original`) via `extract_snippets_with_keywords`.
        - Use `format_matched_terms` to compose summary string (pass matched required terms, matched terms per group, matched excludes list).
        - Include location-specific rationale if matches occurred only in location (e.g., prefix summary line `"Location matched: Remote"`).
     7. Return `MatchResult`.
3. **Utility Helpers (`app/matching/utils.py`, optional):**
   - Shared functions for scanning normalized fields, grouping matches, and building snippet keyword sets (dedupe longer phrases before shorter ones).
4. **Expose API via `app/matching/__init__.py`:**
   - Export `KeywordMatcher`, `MatchResult`, and `CandidateMatch`.
5. **Logging & Telemetry:**
   - Log match decisions at `INFO` (matches) or `DEBUG` (non-matches) with job key, company, matched counts, and reason for rejection (e.g., `"missing_required_terms": ["python"]`).

### Match Rationale & Notification Support
- `MatchResult.summary` feeds directly into email body; ensure summary references original casing for readability by using `highlight_keywords` on the original title/location when building human-readable lines.
- Store a lightweight rationale object alongside match results, including:
  - `matched_terms_by_field` for future HTML formatting.
  - `snippets` ready for inclusion in notification templates (Step 7).
- Provide helper `build_notification_payload(job: Job, match_result: MatchResult) -> dict` (place in matching utils) that assembles title, company, url, matched summary, and snippets for notifications.

### Pipeline & Persistence Touchpoints
- **Normalization Flow:**
  1. Scheduler (Step 8) obtains `(RawJob, SourceConfig)` pairs from adapters.
  2. Invoke `JobNormalizer.normalize(...)` for each job.
  3. If `NormalizationResult.should_upsert`, persist via `JobRepository.upsert`.
  4. For unchanged jobs, call `JobRepository.update_last_seen` to refresh heartbeat.
- **Matching Flow:**
  1. For each `NormalizationResult` with `content_changed` (new or updated), call `KeywordMatcher.evaluate`.
  2. Combine outputs into `CandidateMatch` containing persistence decision (`should_notify = match_result.is_match`).
  3. Defer alert deduplication to Step 7 by checking `AlertRepository.has_been_sent(job_key, job.content_hash)`.
- **Logging & Metrics:**
  - Emit structured events summarizing counts per source: total normalized, new, updated, matched, skipped due to exclusions.
  - Record normalization timing (start/end) for observability.

### Testing Strategy
- **Unit Tests — Normalization (`tests/test_normalization.py`):**
  - Verify job key/content hash generation, timestamp handling, whitespace trimming, and `is_new` / `content_changed` logic.
  - Test behavior when adapters omit optional fields (location None, description empty).
  - Confirm `MatchableText` normalization preserves hyphens/apostrophes and lowercases everything else.
  - Ensure existing job `first_seen_at` is preserved.
- **Unit Tests — Matching (`tests/test_matching.py`):**
  - Cover required terms, keyword groups, exclusion precedence, multi-word phrases, punctuation variants, and location-only matches.
  - Validate remote keywords in location trigger matches while unrelated phrases do not.
  - Confirm `MatchResult.summary` contents and snippet generation.
- **Integration Tests (`tests/integration/test_normalization_matching.py`):**
  - Simulate adapter output → normalization → repository upsert (using in-memory SQLite fixture) → matching decision → alert eligibility.
  - Include scenario where job update changes description but retains title; ensure `content_changed=True` and re-match occurs.
- **Regression Fixtures:**
  - Reuse adapter fixtures to ensure normalization handles real payload structures.
  - Add fixture for remote-friendly job to validate location logic.

### Step-by-Step Implementation Checklist
1. Scaffold `app/normalization/models.py` and define `NormalizationContext`, `MatchableText`, and `NormalizationResult`.
2. Implement `JobNormalizer` in `app/normalization/service.py`, including batch helper and logging.
3. Update `app/normalization/__init__.py` exports and add module docstrings.
4. Scaffold `app/matching/models.py` with `MatchResult` and `CandidateMatch`.
5. Implement `KeywordMatcher` (and optional helpers) in `app/matching/engine.py`; tie into `app/utils/highlighting`.
6. Update `app/matching/__init__.py` exports.
7. Create helper to build notification payloads or rationale dictionaries for Step 7 consumers.
8. Extend persistence usage pattern documentation (if needed) to reflect normalization outputs.
9. Add comprehensive unit tests for normalization and matching; register fixtures under `tests/fixtures/`.
10. Add integration test covering adapter → normalization → matching pipeline happy path and update path.
11. Run `pytest` (entire suite) and ensure coverage thresholds unaffected.
12. Document any deviations/assumptions in this tech spec or README if implementation diverges.

### Open Items for Later Steps
- Integrate alert deduplication (`AlertRepository`) and email formatting once Step 7 begins.
- Consider caching normalized/matched results for idempotency in overlapping scheduler runs.
- Evaluate performance of substring checks on large descriptions; revisit tokenization if benchmarks indicate the need post-v1.0.
