"""Bankroll management API endpoints."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bankroll import BankrollEntry
from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response / Request models ───────────────────────────────────

class BankrollTransactionRequest(BaseModel):
    amount: float
    notes: str | None = None


class BankrollTransactionOut(BaseModel):
    id: int
    entry_type: str
    amount: float
    balance_after: float
    recommendation_id: int | None
    stake: float | None
    notes: str | None
    transacted_utc: str


class BankrollResponse(BaseModel):
    balance: float
    total_deposited: float
    total_withdrawn: float
    total_staked: float
    total_won: float
    transactions: list[BankrollTransactionOut]


# ── Endpoints ───────────────────────────────────────────────────

@router.get("/bankroll", response_model=BankrollResponse)
async def get_bankroll(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
):
    """Get bankroll balance and recent transactions."""
    result = await db.execute(
        select(BankrollEntry)
        .order_by(BankrollEntry.transacted_utc.desc())
        .limit(limit)
    )
    entries = result.scalars().all()

    # Current balance = latest entry's balance_after
    balance = entries[0].balance_after if entries else 0.0

    # Aggregate totals
    total_deposited = sum(e.amount for e in entries if e.entry_type == "deposit")
    total_withdrawn = sum(abs(e.amount) for e in entries if e.entry_type == "withdrawal")
    total_staked = sum(e.stake or 0 for e in entries if e.entry_type == "bet_placed")
    total_won = sum(e.amount for e in entries if e.entry_type == "bet_settled" and e.amount > 0)

    transactions = [
        BankrollTransactionOut(
            id=e.id,
            entry_type=e.entry_type,
            amount=e.amount,
            balance_after=e.balance_after,
            recommendation_id=e.recommendation_id,
            stake=e.stake,
            notes=e.notes,
            transacted_utc=str(e.transacted_utc),
        )
        for e in entries
    ]

    return BankrollResponse(
        balance=round(balance, 2),
        total_deposited=round(total_deposited, 2),
        total_withdrawn=round(total_withdrawn, 2),
        total_staked=round(total_staked, 2),
        total_won=round(total_won, 2),
        transactions=transactions,
    )


@router.post("/bankroll/deposit", response_model=BankrollTransactionOut)
@limiter.limit("10/minute")
async def deposit(
    request: Request,
    body: BankrollTransactionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Deposit funds into the bankroll."""
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Get current balance
    result = await db.execute(
        select(BankrollEntry)
        .order_by(BankrollEntry.transacted_utc.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    current_balance = latest.balance_after if latest else 0.0

    now = datetime.now(timezone.utc)
    entry = BankrollEntry(
        entry_type="deposit",
        amount=body.amount,
        balance_after=round(current_balance + body.amount, 2),
        notes=body.notes or "Deposit",
        transacted_utc=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    return BankrollTransactionOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        balance_after=entry.balance_after,
        recommendation_id=entry.recommendation_id,
        stake=entry.stake,
        notes=entry.notes,
        transacted_utc=str(entry.transacted_utc),
    )


@router.post("/bankroll/withdraw", response_model=BankrollTransactionOut)
@limiter.limit("10/minute")
async def withdraw(
    request: Request,
    body: BankrollTransactionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Withdraw funds from the bankroll."""
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Get current balance
    result = await db.execute(
        select(BankrollEntry)
        .order_by(BankrollEntry.transacted_utc.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    current_balance = latest.balance_after if latest else 0.0

    if body.amount > current_balance:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    now = datetime.now(timezone.utc)
    entry = BankrollEntry(
        entry_type="withdrawal",
        amount=-body.amount,
        balance_after=round(current_balance - body.amount, 2),
        notes=body.notes or "Withdrawal",
        transacted_utc=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    return BankrollTransactionOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        balance_after=entry.balance_after,
        recommendation_id=entry.recommendation_id,
        stake=entry.stake,
        notes=entry.notes,
        transacted_utc=str(entry.transacted_utc),
    )
