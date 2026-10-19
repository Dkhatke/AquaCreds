"""Re-export: the hash-chained audit log lives in aqua_core so API and workers share one writer."""

from aqua_core.audit import (
    AUDIT_LOCK_KEY,
    ChainReport,
    compute_entry_hash,
    record,
    verify_chain,
)

__all__ = ["AUDIT_LOCK_KEY", "ChainReport", "compute_entry_hash", "record", "verify_chain"]
