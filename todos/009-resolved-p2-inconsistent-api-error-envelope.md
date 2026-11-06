---
status: resolved
priority: p2
area: spec compliance / api
files: app/api/app.py, app/api/routes/sources.py, tests/test_api.py
source: specialized-review of working tree vs 441dc2a (2026-11-06)
---

# Some 422s, and all non-422 errors, skip the `{detail, errors}` envelope

- **Priority:** P2
- **Axis:** Spec Compliance

## Description

`app/api/app.py:68` states the contract: "One error shape for every 422:
{detail, errors}". Two paths break it:

1. Registry validation: `put_registry` and `add_registry_company` convert
   `ValueError` to `HTTPException(422, detail=str(e))`
   (`app/api/routes/sources.py:271`, `:291`). The body is `{"detail": "..."}` with no
   `errors`. The UI field-error rendering (`ApiError.errors`) gets an empty list, so a
   duplicate or blank identifier shows no field message.
2. Every `HTTPException` (404, 409, 502, 503) returns FastAPI's default
   `{"detail": ...}`. The frontend handles this (`api.ts` defaults `errors` to `[]`),
   but the envelope differs per status code.
3. Unhandled exceptions, such as a corrupt registry JSON
   (`CompanyRegistry._read_document` raises `JSONDecodeError`), return a plain-text
   500 with no `detail`.

## Remediation Plan

1. In `create_app()`, register an `HTTPException` handler (Starlette's) that returns
   `{"detail": exc.detail, "errors": []}` with the same status and headers.
2. In `routes/sources.py`, change the two `ValueError` branches to
   `raise ConfigurationError("Invalid registry update", errors=[str(e)], suggestions=[...])`
   so they use the existing 422 handler.
3. Wrap `_read_document` JSON errors as `ConfigurationError` (or add a generic
   `Exception` handler that returns `{"detail": "Internal error", "errors": []}` with
   status 500 and logs `extra={"event": "api.unhandled_error"}`).
4. Tests in `tests/test_api.py`: assert `"errors" in body` for a duplicate registry
   identifier (422), an unknown job (404), a trigger while running (409), and a
   corrupt registry file (500 or 422).
