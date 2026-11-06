---
status: resolved
priority: p2
area: data integrity / deployment
files: app/config/manager.py, tests/test_config_manager.py, README.md, CLAUDE.md
source: specialized-review of working tree vs 441dc2a (2026-11-06)
---

# Saving config from the UI fails when config.yaml is a single-file Docker bind mount

- **Priority:** P2
- **Axis:** Data Integrity

## Description

`ConfigManager._write_atomically()` (`app/config/manager.py:270-281`) writes
`config.yaml.tmp` and then calls `os.replace(tmp_path, self.config_path)`.

The documented Docker usage (`CLAUDE.md` "Docker" section) mounts the config as a
single file: `-v $(pwd)/config.yaml:/app/config.yaml`. On Linux, `rename(2)` onto a
bind-mount target fails with `EBUSY` ("Device or resource busy"). The `OSError`
becomes `ConfigurationError("Failed to write configuration file: ...")`, and the
handler returns 422.

Result: in the shipped Docker deployment (Slice 9, `EXPOSE 8000`, `UI_HOST=0.0.0.0`),
every `PUT /api/config` fails. US stories for Search Profile, Guardrail and Source
editing do not work there. Disk and runtime stay consistent (nothing is written), so
no data is lost, but the feature is broken for the main deployment target.

Registry writes are not affected: `data/` is a directory volume, so the `.tmp` file
and the target are on the same mount.

## Remediation Plan

1. In `_write_atomically()`, serialize the document to a string first
   (`io.StringIO` + `self._yaml().dump`). This keeps the "nothing is written if
   serialization fails" guarantee.
2. Try the `.tmp` + `os.replace()` path. On `OSError` with
   `errno in (errno.EBUSY, errno.EXDEV)`, fall back to an in-place write:
   `open(self.config_path, "r+")`, `truncate(0)`, write the string, `flush()`,
   `os.fsync()`. Log a warning with
   `extra={"event": "config.write.in_place_fallback"}`.
3. Add a test in `tests/test_config_manager.py` that patches `os.replace` to raise
   `OSError(errno.EBUSY, ...)` and asserts the file content is updated, comments are
   kept, and the `.tmp` file is removed.
4. Docs: in `README.md` and `CLAUDE.md`, recommend mounting a config **directory**
   (`-v $(pwd)/config:/app/config` + `--config /app/config/config.yaml`) so the atomic
   path is used, and note the in-place fallback for single-file mounts.
