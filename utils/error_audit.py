"""DigiPath Error Audit Engine.

Provides centralized persistence of application, ML, and payment failures
into the `system_error_logs` table for administrative review.
"""

from __future__ import annotations

import datetime
import logging
import traceback
from typing import Optional

from sqlalchemy.orm import Session
import models

log = logging.getLogger("digipath.error_audit")


def log_system_error(
    db: Session,
    module_name: str,
    error_message: str,
    stack_trace: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[models.SystemErrorLog]:
    """
    Record an error occurrence in the system audit ledger.
    Commits safely without propagating database exceptions to callers.
    """
    try:
        if stack_trace is None and traceback.format_exc() != "NoneType: None\n":
            stack_trace = traceback.format_exc()

        entry = models.SystemErrorLog(
            user_id=user_id,
            module_name=str(module_name).upper(),
            error_message=str(error_message)[:2000],
            stack_trace=str(stack_trace) if stack_trace else None,
            status="UNRESOLVED",
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        log.info("[ERROR AUDIT] Recorded failure in module %s (ID: %s)", module_name, entry.id)
        return entry
    except Exception as exc:
        db.rollback()
        log.warning("[ERROR AUDIT] Failed to record system error: %s", exc)
        return None
