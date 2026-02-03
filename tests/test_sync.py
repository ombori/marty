"""Tests for transaction sync service."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.recon import Base, SyncMetadata
from app.models.recon import WiseTransaction as WiseTransactionModel
from app.services.sync import TransactionSyncService
from app.services.wise import WiseBalance, WiseClient, WiseTransaction


@pytest.fixture
async def async_db_engine():
    """Create async SQLite database for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def async_session(async_db_engine):
    """Create async database session for testing."""
    async_session_maker = async_sessionmaker(
        async_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def mock_wise_client():
    """Create mock WiseClient."""
    client = MagicMock(spec=WiseClient)
    client.get_entity_name.return_value = "Phygrid Limited"
    return client


class TestTransactionSyncService:
    """Tests for TransactionSyncService."""

    @pytest.mark.asyncio
    async def test_store_transactions(self, async_session, mock_wise_client):
        """Test storing transactions in database."""
        service = TransactionSyncService(async_session, mock_wise_client)

        transactions = [
            WiseTransaction(
                reference_number="TRANSFER-001",
                type="DEBIT",
                transaction_type="TRANSFER",
                date=datetime(2026, 1, 15, tzinfo=UTC),
                amount=Decimal("-1000.00"),
                currency="EUR",
                description="Test transfer 1",
                counterparty_name="Test Company",
            ),
            WiseTransaction(
                reference_number="DEPOSIT-002",
                type="CREDIT",
                transaction_type="DEPOSIT",
                date=datetime(2026, 1, 16, tzinfo=UTC),
                amount=Decimal("5000.00"),
                currency="EUR",
                description="Test deposit",
                payment_reference="INV-2026-001",
            ),
        ]

        count = await service._store_transactions(
            transactions, profile_id=19941830, entity_name="Phygrid Limited"
        )

        assert count == 2

        # Verify stored in database
        result = await async_session.execute(select(WiseTransactionModel))
        stored = list(result.scalars().all())

        assert len(stored) == 2
        assert stored[0].id == "TRANSFER-001"
        assert stored[0].amount == Decimal("-1000.00")
        assert stored[1].id == "DEPOSIT-002"
        assert stored[1].payment_reference == "INV-2026-001"

    @pytest.mark.asyncio
    async def test_store_transactions_upsert(self, async_session, mock_wise_client):
        """Test that storing existing transactions updates them."""
        service = TransactionSyncService(async_session, mock_wise_client)

        # First insert
        tx1 = WiseTransaction(
            reference_number="UPSERT-001",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime(2026, 1, 15, tzinfo=UTC),
            amount=Decimal("-100.00"),
            currency="EUR",
            description="Original description",
        )
        await service._store_transactions([tx1], 19941830, "Phygrid Limited")

        # Update with new description
        tx2 = WiseTransaction(
            reference_number="UPSERT-001",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime(2026, 1, 15, tzinfo=UTC),
            amount=Decimal("-100.00"),
            currency="EUR",
            description="Updated description",
        )
        await service._store_transactions([tx2], 19941830, "Phygrid Limited")

        # Should still have just one record with updated description
        result = await async_session.execute(
            select(WiseTransactionModel).where(WiseTransactionModel.id == "UPSERT-001")
        )
        stored = result.scalar_one()

        assert stored.description == "Updated description"

    @pytest.mark.asyncio
    async def test_get_or_create_metadata_creates_new(self, async_session, mock_wise_client):
        """Test creating new sync metadata."""
        service = TransactionSyncService(async_session, mock_wise_client)

        metadata = await service._get_or_create_metadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
            balance_id=100001,
        )

        assert metadata.profile_id == 19941830
        assert metadata.currency == "EUR"
        assert metadata.sync_status == "idle"

    @pytest.mark.asyncio
    async def test_get_or_create_metadata_returns_existing(self, async_session, mock_wise_client):
        """Test returning existing sync metadata."""
        service = TransactionSyncService(async_session, mock_wise_client)

        # Create first
        meta1 = await service._get_or_create_metadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
            balance_id=100001,
        )
        await async_session.commit()

        # Get again - should return same record
        meta2 = await service._get_or_create_metadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
            balance_id=100001,
        )

        assert meta1.id == meta2.id

    @pytest.mark.asyncio
    async def test_sync_balance_success(self, async_session, mock_wise_client):
        """Test syncing a single balance."""
        # Setup mock to return transactions
        mock_wise_client.get_transactions = AsyncMock(
            return_value=[
                WiseTransaction(
                    reference_number="SYNC-001",
                    type="DEBIT",
                    transaction_type="TRANSFER",
                    date=datetime(2026, 1, 15, tzinfo=UTC),
                    amount=Decimal("-500.00"),
                    currency="EUR",
                ),
            ]
        )

        service = TransactionSyncService(async_session, mock_wise_client)

        count = await service._sync_balance(
            profile_id=19941830,
            entity_name="Phygrid Limited",
            balance_id=100001,
            currency="EUR",
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            end_date=datetime(2026, 1, 31, tzinfo=UTC),
            force_full_sync=False,
        )

        assert count == 1

        # Verify metadata was updated
        result = await async_session.execute(
            select(SyncMetadata).where(
                SyncMetadata.profile_id == 19941830,
                SyncMetadata.currency == "EUR",
            )
        )
        metadata = result.scalar_one()
        assert metadata.sync_status == "idle"
        assert metadata.transactions_synced == 1

    @pytest.mark.asyncio
    async def test_sync_balance_error_updates_metadata(self, async_session, mock_wise_client):
        """Test that sync errors are recorded in metadata."""
        mock_wise_client.get_transactions = AsyncMock(side_effect=Exception("API Error"))

        service = TransactionSyncService(async_session, mock_wise_client)

        with pytest.raises(Exception, match="API Error"):
            await service._sync_balance(
                profile_id=19941830,
                entity_name="Phygrid Limited",
                balance_id=100001,
                currency="EUR",
                start_date=datetime(2026, 1, 1, tzinfo=UTC),
                end_date=datetime(2026, 1, 31, tzinfo=UTC),
                force_full_sync=False,
            )

        # Verify error was recorded
        result = await async_session.execute(
            select(SyncMetadata).where(
                SyncMetadata.profile_id == 19941830,
                SyncMetadata.currency == "EUR",
            )
        )
        metadata = result.scalar_one()
        assert metadata.sync_status == "error"
        assert "API Error" in metadata.error_message

    @pytest.mark.asyncio
    async def test_sync_profile(self, async_session, mock_wise_client):
        """Test syncing all balances for a profile."""
        # Setup mocks
        mock_wise_client.get_balances = AsyncMock(
            return_value=[
                WiseBalance(
                    id=100001, currency="EUR", amount=Decimal("10000"), reserved_amount=Decimal("0")
                ),
                WiseBalance(
                    id=100002, currency="USD", amount=Decimal("5000"), reserved_amount=Decimal("0")
                ),
            ]
        )
        mock_wise_client.get_transactions = AsyncMock(
            return_value=[
                WiseTransaction(
                    reference_number="SYNC-EUR-001",
                    type="DEBIT",
                    transaction_type="TRANSFER",
                    date=datetime(2026, 1, 15, tzinfo=UTC),
                    amount=Decimal("-100.00"),
                    currency="EUR",
                ),
            ]
        )

        service = TransactionSyncService(async_session, mock_wise_client)

        count = await service.sync_profile(
            profile_id=19941830,
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            end_date=datetime(2026, 1, 31, tzinfo=UTC),
        )

        # Should have synced from both balances (1 tx each = 2 total)
        assert count == 2
        assert mock_wise_client.get_balances.call_count == 1
        assert mock_wise_client.get_transactions.call_count == 2

    @pytest.mark.asyncio
    async def test_get_unsynced_transactions(self, async_session, mock_wise_client):
        """Test retrieving unsynced transactions."""
        # Add some transactions with different statuses
        tx1 = WiseTransactionModel(
            id="PENDING-001",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime(2026, 1, 15, tzinfo=UTC),
            amount=Decimal("-100.00"),
            currency="EUR",
            match_status="pending",
        )
        tx2 = WiseTransactionModel(
            id="MATCHED-001",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="CREDIT",
            transaction_type="DEPOSIT",
            date=datetime(2026, 1, 16, tzinfo=UTC),
            amount=Decimal("500.00"),
            currency="EUR",
            match_status="matched",
        )
        tx3 = WiseTransactionModel(
            id="PENDING-002",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="CARD",
            date=datetime(2026, 1, 17, tzinfo=UTC),
            amount=Decimal("-50.00"),
            currency="EUR",
            match_status="pending",
        )
        async_session.add_all([tx1, tx2, tx3])
        await async_session.commit()

        service = TransactionSyncService(async_session, mock_wise_client)
        unsynced = await service.get_unsynced_transactions(limit=10)

        # Should only return pending transactions
        assert len(unsynced) == 2
        assert all(tx.match_status == "pending" for tx in unsynced)
