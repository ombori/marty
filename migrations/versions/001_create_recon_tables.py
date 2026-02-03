"""Create recon tables.

Revision ID: 001
Revises:
Create Date: 2026-02-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create wise_transactions table
    op.create_table(
        "wise_transactions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("entity_name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payment_reference", sa.String(500), nullable=True),
        sa.Column("counterparty_name", sa.String(255), nullable=True),
        sa.Column("counterparty_account", sa.String(100), nullable=True),
        sa.Column("from_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("from_currency", sa.String(3), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(12, 8), nullable=True),
        sa.Column("total_fees", sa.Numeric(15, 2), nullable=True),
        sa.Column("merchant_name", sa.String(255), nullable=True),
        sa.Column("merchant_category", sa.String(100), nullable=True),
        sa.Column("card_last_four", sa.String(4), nullable=True),
        sa.Column("card_holder_name", sa.String(255), nullable=True),
        sa.Column("running_balance", sa.Numeric(15, 2), nullable=True),
        sa.Column("match_status", sa.String(20), server_default="pending"),
        sa.Column("last_match_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("match_attempts", sa.Integer(), server_default="0"),
        sa.Column("best_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("spectre_suggestion_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create indexes for wise_transactions
    op.create_index("idx_wise_tx_date", "wise_transactions", ["date"])
    op.create_index("idx_wise_tx_status", "wise_transactions", ["match_status"])
    op.create_index("idx_wise_tx_entity", "wise_transactions", ["entity_name", "date"])
    op.create_index("idx_wise_tx_profile", "wise_transactions", ["profile_id", "date"])

    # Create sync_metadata table
    op.create_table(
        "sync_metadata",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("entity_name", sa.String(255), nullable=False),
        sa.Column("balance_id", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(20), server_default="idle"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("transactions_synced", sa.Integer(), server_default="0"),
    )

    # Create unique index for sync_metadata
    op.create_index(
        "idx_sync_profile_currency",
        "sync_metadata",
        ["profile_id", "currency"],
        unique=True,
    )

    # Create match_candidates table
    op.create_table(
        "match_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "wise_transaction_id",
            sa.String(100),
            sa.ForeignKey("wise_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("netsuite_transaction_id", sa.String(100), nullable=True),
        sa.Column("netsuite_line_id", sa.Integer(), nullable=True),
        sa.Column("netsuite_type", sa.String(50), nullable=True),
        sa.Column("netsuite_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("netsuite_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("netsuite_entity", sa.String(255), nullable=True),
        sa.Column("netsuite_memo", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("match_type", sa.String(20), nullable=True),
        sa.Column("match_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_selected", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create indexes for match_candidates
    op.create_index("idx_candidates_tx", "match_candidates", ["wise_transaction_id"])
    op.create_index(
        "idx_candidates_selected",
        "match_candidates",
        ["is_selected"],
        postgresql_where=sa.text("is_selected = true"),
    )


def downgrade() -> None:
    op.drop_table("match_candidates")
    op.drop_table("sync_metadata")
    op.drop_table("wise_transactions")
