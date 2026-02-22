"""Bankroll tracking model."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BankrollEntry(Base, TimestampMixin):
    """A bankroll transaction (deposit, withdrawal, or bet settlement)."""

    __tablename__ = "bankroll_entries"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Transaction type: deposit, withdrawal, bet_placed, bet_settled
    entry_type: Mapped[str] = mapped_column(String(20), index=True)

    # Amount (positive = money in, negative = money out)
    amount: Mapped[float] = mapped_column(Float)

    # Running balance after this entry
    balance_after: Mapped[float] = mapped_column(Float)

    # Optional link to recommendation (for bet entries)
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id"),
        nullable=True,
        index=True,
    )

    # Stake amount (for bet entries)
    stake: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamp of the transaction
    transacted_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return f"<BankrollEntry {self.entry_type} {self.amount:+.2f} bal={self.balance_after:.2f}>"
