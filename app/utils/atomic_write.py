"""Atomic text-file writes with a fallback for Docker single-file bind mounts."""

import errno
import logging
import os
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

_FALLBACK_ERRNOS = (errno.EBUSY, errno.EXDEV)


def atomic_write_text(target: Union[str, Path], text: str) -> None:
    """Write ``text`` to ``target`` through a ``.tmp`` file and ``os.replace()``.

    ``rename(2)`` onto a single-file bind mount fails with ``EBUSY`` (or ``EXDEV``
    across devices). In that case the content is copied into the existing file in
    place, keeping its inode. The temporary file is always removed.

    Raises:
        OSError: If writing fails for any other reason
    """
    target_path = Path(target)
    tmp_path = target_path.with_name(target_path.name + ".tmp")
    try:
        with open(tmp_path, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, target_path)
        except OSError as e:
            if e.errno not in _FALLBACK_ERRNOS:
                raise
            logger.warning(
                "Atomic replace failed (%s); writing %s in place",
                e,
                target_path,
                extra={"event": "file.write.in_place_fallback"},
            )
            with open(tmp_path, "r") as src, open(target_path, "w") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
    finally:
        tmp_path.unlink(missing_ok=True)
