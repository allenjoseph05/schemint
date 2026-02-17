"""Migration utilities — checksum computation and ID generation.

These are pure functions with no side effects, used by both the
store (duplicate detection) and the planning layer (migration creation).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


def compute_migration_checksum(sql: str) -> str:
    """Compute a SHA256 checksum of whitespace-normalized SQL.

    Normalization: collapse all whitespace runs to single space, strip,
    lowercase. This ensures that formatting differences don't produce
    different checksums for semantically identical migrations.
    """
    normalized = re.sub(r"\s+", " ", sql.strip()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_migration_id(prefix: str = "migration") -> str:
    """Generate a timestamp-based migration ID.

    Format: YYYYMMDD_HHMMSS_<prefix>
    Example: 20260215_143022_add_phone
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    # Sanitize prefix: lowercase, replace non-alphanumeric with underscore
    safe_prefix = re.sub(r"[^a-z0-9_]", "_", prefix.lower().strip())
    if not safe_prefix:
        safe_prefix = "migration"
    return f"{timestamp}_{safe_prefix}"
