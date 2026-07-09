"""Refund bookkeeping.

When a booking is cancelled a refund is calculated from its price and the
applicable notice tier, then written to the refund ledger with a processed
status. Amounts are stored in whole cents.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Booking, RefundLog

from decimal import Decimal, ROUND_HALF_UP

def calc_refund_cents(price_cents: int, percent: int) -> int:
    amount = Decimal(price_cents) * Decimal(percent) / Decimal(100)
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def log_refund(db: Session, booking: Booking, percent: int) -> RefundLog:
    dollars = booking.price_cents / 100.0
    refund_dollars = dollars * (percent / 100.0)
    amount_cents = calc_refund_cents(booking.price_cents, percent)
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
