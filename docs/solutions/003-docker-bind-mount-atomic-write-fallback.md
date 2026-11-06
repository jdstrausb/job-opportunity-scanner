---
id: "SOL-003"
title: "Atomic File Replacement with Linux Docker Bind-Mount Fallback"
date: 2026-11-06
tags:
  - docker
  - persistence
  - data-integrity
  - filesystem
---

## Context

The config editor (`PUT /api/config`) rewrites `config.yaml` through a `.tmp` sibling and `os.replace()`. The documented Docker run mounts the config as a single file:

```bash
docker run -d -v $(pwd)/config.yaml:/app/config.yaml ...
```

On Linux, the first write from the UI failed with `OSError: [Errno 16] Device or resource busy` (`EBUSY`). The error surfaced as a `ConfigurationError` and a 422, so every config save failed in the shipped container. Nothing was written, so no data was lost, but the profile, guardrail and source editors did not work in Docker.

## Root Cause

`rename(2)` cannot replace a path that is itself a mount point. A single-file bind mount makes `/app/config.yaml` a mount point, so the kernel rejects the rename onto it. Cross-device moves fail the same way with `EXDEV`. Replacing the file's directory entry is not possible, so `os.replace()` is not atomic in this layout.

Registry writes were never affected, because `data/` is a directory mount. The `.tmp` file and its target share a mount there.

## Solution Pattern

`app/utils/atomic_write.py` provides `atomic_write_text(target, text)`:

1. Write the full text to `<target>.tmp`, then `flush()` and `os.fsync()`.
2. Try `os.replace(tmp, target)`. This is the normal, atomic path.
3. If that raises `EBUSY` or `EXDEV`, log a warning with `event=file.write.in_place_fallback`. Then copy the `.tmp` contents into the existing target, keeping its inode, and `fsync` it.
4. In a `finally` block, always remove the `.tmp` file with `missing_ok=True`.

Any other `OSError` is re-raised.

```python
try:
    os.replace(tmp_path, target_path)
except OSError as e:
    if e.errno not in (errno.EBUSY, errno.EXDEV):
        raise
    logger.warning(..., extra={"event": "file.write.in_place_fallback"})
    with open(tmp_path, "r") as src, open(target_path, "w") as dst:
        dst.write(src.read())
        dst.flush()
        os.fsync(dst.fileno())
finally:
    tmp_path.unlink(missing_ok=True)
```

`ConfigManager` serializes the YAML to a string before calling this helper, so a serialization error never touches the disk.

## Friction Points & Gotchas

- **The fallback is not atomic.** `open(target, "w")` truncates the file before the copy. If the process dies during the copy, `config.yaml` can be left empty or partial. Atomic behaviour is kept on normal volumes only. Single-file mounts keep the risk until the mount is changed to a directory.
- **Use the helper, not raw `os.replace()`.** Any new writer for a user-editable file that can be bind-mounted needs the same fallback.
- **Read the source before debugging.** The first symptom looked like a permissions problem. The real cause was the mount type.

## Prevention Rule

Never assume `os.replace()` is atomic on containerized deployments that mount single files. Write through `atomic_write_text()`, and mount a directory when config durability matters more than the single-file layout.

## Reusable Insight

Atomic rename depends on the filesystem layout. Test replacement behaviour under the same mount layout that production uses, and keep a documented fallback for mount points that cannot be renamed over.
