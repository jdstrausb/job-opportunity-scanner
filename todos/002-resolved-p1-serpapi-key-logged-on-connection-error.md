---
status: resolved
priority: p1
area: security / secret hygiene
files: app/adapters/base.py, app/adapters/serpapi_google.py
source: specialized-review of b2cf251..HEAD (2026-11-06)
---

> **Resolution:** Resolved in 27a7c3f: BaseAdapter._sanitize_url redacts api_key/token/secret params in every log line and exception message in _send_request/_make_request (exceptions raised `from None`). Covered by test_send_request_redacts_api_key_in_error_logs. Not done: scraper.py print() fix, and the 4xx/5xx/Timeout caplog assertions listed in Acceptance.

# SERPAPI_API_KEY is logged in plaintext when a connection-level error occurs

## Problem

`SerpApiSearchAdapter._search()` sends the key as the `api_key` query parameter.
On any `requests.RequestException` other than `Timeout`, such as `ConnectionError`,
a DNS failure, `SSLError`, or a too-many-redirects error, `BaseAdapter._send_request()`
logs the exception text at ERROR *before* the adapter can redact it:

```python
# app/adapters/base.py:251
logger.error(f"Request to {url} failed: {e}", ...)
```

The `str(e)` of a urllib3 `MaxRetryError` contains the full request path with the query string.
`_redact()` in the SerpAPI adapter cleans only the re-raised exception. The log line has
already been sent to stdout, JSON logs, or `docker logs`.

Verified by a probe (point `SEARCH_URL` at `http://127.0.0.1:9/search.json`):

```
raised: AdapterHTTPError key_in_exc= False
key_in_logs= True
  LEAK: ERROR app.adapters.base Request to http://127.0.0.1:9/search.json failed:
        HTTPConnectionPool(...): Max retries exceeded with url:
        /search.json?engine=google&q=...&api_key=SECR...
```

The current tests do not catch this: they mock `_make_request` or `Session.request` with
canned responses and do not assert on `caplog`.

Minor related issue: `ats_identifier_scraper/scraper.py:36` uses `print(f"Error searching for {company_name}: {e}")`
on exceptions from the `serpapi` client, and that output can echo the request URL.

## Remediation

- In `BaseAdapter`, add a redaction hook such as `_secrets() -> tuple[str, ...]`
  (default `()`), and a `_scrub(text)` helper that every log message and every
  exception message in `_send_request`/`_make_request` passes through.
  `SerpApiSearchAdapter` overrides `_secrets()` to return `(self.api_key,)`.
  Then delete the after-the-fact `_redact()` path, or keep it as defense in depth.
- Or log only `type(e).__name__` for `RequestException`, never `str(e)`.
  Note that the `exc_info` traceback also carries the URL, so do not add `exc_info`.
- In the scraper, log `type(e).__name__` only.

## Acceptance

- [ ] New test `tests/test_adapters.py::TestSerpApiSearchAdapter::test_connection_error_never_logs_api_key`.
  It mocks `Session.request` to raise `requests.ConnectionError("... url: /search.json?...&api_key=SECRET ...")`
  and asserts that `"SECRET"` is in no `caplog.records` message or formatted traceback,
  and is not in the raised exception.
- [ ] The same assertion passes for the HTTP 4xx/5xx and Timeout branches.
