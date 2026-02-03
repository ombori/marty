"""SQLAlchemy models for reconciliation."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class WiseTransaction(Base):
    """Wise transaction record."""

    __tablename__ = "wise_transactions"

    # Primary key - Wise reference number (e.g., TRANSFER-1950972714)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)

    # Entity identification
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Transaction basics
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # DEBIT/CREDIT
    transaction_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # TRANSFER, DEPOSIT, etc.
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Description fields
    description: Mapped[str | None] = mapped_column(Text)
    payment_reference: Mapped[str | None] = mapped_column(String(500))

    # Counterparty
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    counterparty_account: Mapped[str | None] = mapped_column(String(100))

    # FX details
    from_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    from_currency: Mapped[str | None] = mapped_column(String(3))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))

    # Fees
    total_fees: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Card transaction details
    merchant_name: Mapped[str | None] = mapped_column(String(255))
    merchant_category: Mapped[str | None] = mapped_column(String(100))
    card_last_four: Mapped[str | None] = mapped_column(String(4))
    card_holder_name: Mapped[str | None] = mapped_column(String(255))

    # Running balance
    running_balance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Matching state
    match_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, submitted, matched, unmatched
    last_match_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_attempts: Mapped[int] = mapped_column(Integer, default=0)
    best_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))

    # Spectre reference
    spectre_suggestion_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    # Timestamps
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    match_candidates: Mapped[list["MatchCandidate"]] = relationship(
        "MatchCandidate", back_populates="wise_transaction", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_wise_tx_date", "date"),
        Index("idx_wise_tx_status", "match_status"),
        Index("idx_wise_tx_entity", "entity_name", "date"),
        Index("idx_wise_tx_profile", "profile_id", "date"),
    )

    def __repr__(self) -> str:
        return f"<WiseTransaction {self.id} {self.type} {self.amount} {self.currency}>"


class SyncMetadata(Base):
    """Tracks sync state per profile/currency."""

    __tablename__ = "sync_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    balance_id: Mapped[int | None] = mapped_column(Integer)

    # Sync state
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # Changed from DATE to TIMESTAMPTZ for simplicity
    sync_status: Mapped[str] = mapped_column(String(20), default="idle")  # idle, syncing, error
    error_message: Mapped[str | None] = mapped_column(Text)
    transactions_synced: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("idx_sync_profile_currency", "profile_id", "currency", unique=True),)

    def __repr__(self) -> str:
        return f"<SyncMetadata {self.profile_id} {self.currency} {self.sync_status}>"


class MatchCandidate(Base):
    """Temporary working table for match scoring."""

    __tablename__ = "match_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wise_transaction_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("wise_transactions.id", ondelete="CASCADE"), nullable=False
    )

    # Candidate GL entry from Spectre
    netsuite_transaction_id: Mapped[str | None] = mapped_column(String(100))
    netsuite_line_id: Mapped[int | None] = mapped_column(Integer)
    netsuite_type: Mapped[str | None] = mapped_column(String(50))
    netsuite_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    netsuite_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    netsuite_entity: Mapped[str | None] = mapped_column(String(255))
    netsuite_memo: Mapped[str | None] = mapped_column(Text)

    # Match scoring
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    match_type: Mapped[str | None] = mapped_column(String(20))  # exact, fuzzy, llm, pattern
    match_reasons: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Selection
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    wise_transaction: Mapped["WiseTransaction"] = relationship(
        "WiseTransaction", back_populates="match_candidates"
    )

    __table_args__ = (
        Index("idx_candidates_tx", "wise_transaction_id"),
        Index(
            "idx_candidates_selected",
            "is_selected",
            postgresql_where=(is_selected == True),  # noqa: E712
        ),
    )

    def __repr__(self) -> str:
        return f"<MatchCandidate {self.id} {self.confidence_score}>"
