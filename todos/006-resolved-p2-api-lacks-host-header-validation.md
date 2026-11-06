---
status: resolved
priority: p2
area: security / api
files: app/api/app.py, app/main.py, tests/test_api.py, README.md
source: specialized-review of working tree vs 441dc2a (2026-11-06)
---

# Unauthenticated write API has no Host-header check (DNS rebinding)

- **Priority:** P2
- **Axis:** Security

## Description

The spec scopes out authentication (spec "Out of Scope": single-user, `127.0.0.1` or
private LAN/VPN). That is acceptable, but the API now listens by default and exposes
state-changing routes:

- `PUT /api/config` rewrites `config.yaml` (sources, profiles, `scan_interval`).
- `PUT|POST /api/registries/{ats}` rewrites registry JSON files.
- `POST /api/dismissals`, `PATCH /api/jobs/{key}/status`.
- `POST /api/scan/trigger` with `{"send_notifications": true}` sends real email.
- `POST /api/sources/test-query` spends SerpAPI quota.

`create_app()` (`app/api/app.py:48`) adds no `TrustedHostMiddleware` and no Host
check. FastAPI 0.162's `strict_content_type=True` blocks classic cross-site CSRF with
`text/plain` bodies, and PUT/PATCH/DELETE need a CORS preflight. A **DNS-rebinding**
page, however, becomes same-origin with `http://127.0.0.1:8000` and can call every
route above while the user has the page open. Secrets are not readable (`env_status`
has only booleans), so the impact is config tampering, email sending and quota use,
not secret disclosure.

## Remediation Plan

1. In `create_app()`, add a new `allowed_hosts: Optional[List[str]] = None` parameter.
   When it is set, add
   `app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)`.
2. In `app/main.py`, read `UI_ALLOWED_HOSTS` (comma-separated). Default:
   `["localhost", "127.0.0.1", "[::1]"]` plus `args.host` when it is not `0.0.0.0`.
   When `UI_HOST=0.0.0.0` (Docker), log a warning at startup if `UI_ALLOWED_HOSTS` is
   unset, and tell the user to list the LAN hostname/IP they use.
3. Tests in `tests/test_api.py`: a request with `Host: evil.example` returns 400; a
   request with `Host: 127.0.0.1:8000` returns 200. Keep the `TestClient` default
   host (`testserver`) working by passing `allowed_hosts=None` in existing fixtures.
4. Document `UI_ALLOWED_HOSTS` in `README.md` and the `CLAUDE.md` environment
   variable list.
