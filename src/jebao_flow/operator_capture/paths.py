"""Shared validation for durable private operator state paths."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def durable_private_path(value: Path | str, description: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{description} must be an absolute durable path")
    resolved = Path(os.path.realpath(path))
    ephemeral_roots = {
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/tmp"),
        Path(os.path.realpath(tempfile.gettempdir())),
    }
    if any(resolved == root or root in resolved.parents for root in ephemeral_roots):
        raise ValueError(f"{description} must not use an ephemeral temp path")
    # Use the same resolved target that was checked. Returning the unresolved spelling would let a
    # caller validate one location and then open a symlinked location with different durability.
    return resolved


__all__ = ["durable_private_path"]
