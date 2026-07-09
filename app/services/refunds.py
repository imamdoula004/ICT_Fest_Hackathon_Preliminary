"""Refund bookkeeping.

When a booking is cancelled a refund is calculated from its price and the
applicable notice tier, then written to the refund ledger with a processed
status. Amounts are stored in whole cents.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Booking, RefundLog


def log_refund(db: Session, booking: Booking, percent: int) -> RefundLog:
    if percent == 100:
        amount_cents = booking.price_cents
    elif percent == 50:
        amount_cents = (booking.price_cents + 1) // 2
    else:
        amount_cents = 0
    entry = RefundLog(
        booking_id=booking.id,
        amount_cents=amount_cents,
        status="processed",
        processed_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
