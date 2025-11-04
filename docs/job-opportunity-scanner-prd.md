## Job Opportunity Scanner — Product Requirements (v1.0)

### Overview
- Purpose: Monitor target companies’ career pages (via public ATS APIs) to detect new, relevant job postings and notify the user quickly to gain first‑mover advantage.
- Primary user: Individual job seeker configuring and running a small daemon-like service.
- Guiding principles: API-first (avoid scraping), reliability, low‑ops, fast signal, minimal noise.

### Problem Statement
- New roles often appear on company ATS pages before job boards; early applicants have an edge.
- Manually checking multiple sources is time‑consuming and error‑prone.
- Users need timely, accurate alerts with minimal duplicates and clear reasons for matches.

### Goals and Non-Goals
- Goals (v1.0)
  - Monitor multiple ATS sources (Greenhouse, Lever, Ashby) for configured companies.
  - Filter postings via structured, explicit keyword logic.
  - Detect new/updated jobs, de‑duplicate, and persist state.
  - Send actionable email notifications for matches (single alert per job version).
  - Operate reliably with strong logging; continue on partial failures.
- Non‑Goals (v1.0)
  - UI for configuration (YAML + env vars only).
  - Advanced semantic matching or scoring (boolean pass/fail only).
  - Additional notifiers (e.g., SMS, RSS); Slack is future/optional.
  - Failure self‑notifications.
  - Multiple search profiles.

### Personas
- Job Seeker (primary): technically comfortable; can edit YAML and set env vars; wants fast and accurate alerts.

### Assumptions
- Public ATS endpoints exist and are rate‑limit friendly at modest polling intervals.
- SMTP credentials are available for sending email.
- A single user and single search profile for v1.0.

### User Stories and Acceptance Criteria

#### 1) Configure Sources
As a job seeker, I can define the companies and ATS details to scan in a single YAML file so the system knows where to look.
- Acceptance criteria
  - Supports companies with fields: `name`, `type` in {`greenhouse`,`lever`,`ashby`}, and `identifier` per ATS (e.g., Greenhouse `board_token`, Lever company handle, Ashby org ID).
  - Invalid or missing required fields cause a clear startup error with actionable message.
  - A global `scan_interval` is supported; default 15 minutes if absent.
  - Configuration reload requires restart in v1.0 (no hot reload).

#### 2) Define Search Criteria
As a job seeker, I can specify required terms, grouped OR terms (AND between groups), and an exclusion list to precisely define relevance.
- Acceptance criteria
  - `required_terms`: all must appear in title or description.
  - `keyword_groups`: job must match at least one term from each group; AND across groups, OR within a group.
  - `exclude_terms`: any match disqualifies the job.
  - Matching is case‑insensitive and trims punctuation; document which fields are searched (title + description at minimum).
  - Provide example config and input validation errors when schema is malformed.

Example YAML shape:
```yaml
sources:
  - name: ExampleCo
    type: greenhouse
    identifier: exampleco_token
scan_interval: "15m"
search_criteria:
  required_terms: ["full-time"]
  keyword_groups:
    - ["health benefits", "401k", "benefits"]
    - ["javascript", "python", "ruby on rails", "sql", "postgresql", "database"]
    - ["web development", "full-stack", "backend", "frontend"]
  exclude_terms: ["intern", "junior", "principal"]
```

#### 3) Poll ATS Sources
As a job seeker, I want the system to run on a schedule and gather postings from all configured sources without one failure stopping the rest.
- Acceptance criteria
  - Scheduler triggers at the configured interval.
  - Greenhouse, Lever, and Ashby adapters call public endpoints with respectful defaults.
  - One source failing logs a structured error and does not abort other sources.
  - Raw results are normalized into a common `Job` structure.

#### 4) Normalize, Detect Changes, and Persist
As a job seeker, I want the system to know what is new and avoid duplicate noise.
- Acceptance criteria
  - Unique key = hash of source identifier + external job ID (e.g., `sha256("greenhouse:exampleco|12345")`).
  - Distinguishes New vs Updated using ATS `updated_at` (when available).
  - Persists `first_seen_at`, `last_seen_at`, key attributes, and alert state in SQLite.
  - Restarts do not re‑alert already‑alerted job versions.

#### 5) Matching and Alert Decision
As a job seeker, I only receive alerts for postings that pass all rules.
- Acceptance criteria
  - Boolean pass/fail matching: required terms present, at least one term in each keyword group, and no excluded terms.
  - Only one alert per job version.
  - For updated jobs, alert only if content changes and still passes rules and hasn’t been alerted for that version.

#### 6) Email Notifications
As a job seeker, I receive timely, informative emails for matches.
- Acceptance criteria
  - SMTP settings and recipient are provided via env vars: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `ALERT_TO_EMAIL` (initial deployment value may be set to `jstrausb86@gmail.com`).
  - Email includes: Job Title, Company, Location, direct URL, and a brief “matched because” summary listing triggered terms/groups.
  - Sending failure logs error without crashing the process.

#### 7) Observability and Reliability
As an operator, I can diagnose issues from logs and trust the service to keep running.
- Acceptance criteria
  - Structured logs to stdout for: scan start/finish per source, counts fetched/matched, adapter errors, email outcomes, and DB interactions (at summary level).
  - Log levels: info for normal ops, warning for transient issues, error for failures.
  - No user‑facing failure notifications in v1.0; logs are the primary diagnostic.

#### 8) Packaging and Deployment
As an operator, I can run the service easily and keep data persisted.
- Acceptance criteria
  - Ships as a single Docker image; container runs scheduler and scanner.
  - SQLite DB persisted via mounted volume; default path documented.
  - Minimal configuration documented for running on a small VPS.

### Data Model (v1.0)
- jobs
  - `job_key` (pk), `source_type`, `source_id`, `external_id`, `title`, `company`, `location`, `url`, `posted_at`, `updated_at`, `first_seen_at`, `last_seen_at`, `last_hash`
- sources
  - `source_id` (pk), `name`, `type`, `identifier`, `enabled`
- alerts_sent
  - `job_key` (pk), `alert_version_hash`, `sent_at`

Notes
- `alert_version_hash` enables “one alert per job version” when content changes.

### Non-Functional Requirements
- Persistence: SQLite for low‑ops local storage.
- Security: All secrets via environment variables; no secrets in code or YAML.
- Observability: Structured logs to stdout as primary diagnostic surface.
- Deployment: Single Docker image; recommended small VPS with DB volume.
- Compliance: No specific compliance needs identified for v1.0.

### Out of Scope (v1.0)
- UI for config, semantic/vectored matching, non‑email notifiers, self‑failure alerts, multiple profiles.

### Success Metrics
- Median detection delay from ATS post/update to alert: ≤ 1 polling interval.
- Duplicate alert rate: 0 per job version.
- False‑positive match rate: < 10% in first month (tunable via criteria).
- Mean time to recover from single‑source failure: next scheduled run.

### Risks and Mitigations
- ATS API variability/rate limits → Backoff, cache ETags/timestamps, resilient error handling.
- Over‑filtering or under‑filtering → Provide examples, clear logs on match reasons, iterate on criteria.
- Email deliverability → Support TLS/ports; document provider specifics; retry on transient failures.

### Open Questions
- Exact ATS endpoints/versioning to target for each adapter? Any rate‑limit constraints to bake in?
- Email provider defaults (port, TLS/STARTTLS) and retry strategy expectations?
- Should location filtering (e.g., remote‑only, regions) be supported in v1.0?
  - Answer: Yes, we need location filtering. I am targeting remote-only or remote-compatible positions. I live in Philadelphia, Pennsylvania which has limited technical opportunities compared to tech hubs like NYC and San Francisco.
- Are description fields always available across sources, or title‑only fallback logic needed?
- How should we treat cross‑listed postings across multiple sources (if any)?
- Do we need configurable case/word‑boundary behavior for matching?

