"""Transaction sync service - fetches Wise transactions and stores them."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ENTITIES
from app.models.recon import SyncMetadata
from app.models.recon import WiseTransaction as WiseTransactionModel
from app.services.wise import WiseClient, WiseTransaction

logger = logging.getLogger(__name__)


class TransactionSyncService:
    """Service to sync Wise transactions to PostgreSQL."""

    def __init__(self, session: AsyncSession, wise_client: WiseClient):
        self.session = session
        self.wise_client = wise_client

    async def sync_profile(
        self,
        profile_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        force_full_sync: bool = False,
    ) -> int:
        """Sync transactions for a single profile.

        Args:
            profile_id: Wise profile ID
            start_date: Start date (defaults to last sync or 30 days ago)
            end_date: End date (defaults to now)
            force_full_sync: If True, ignore last sync date

        Returns:
            Number of transactions synced
        """
        entity_name = self.wise_client.get_entity_name(profile_id)
        logger.info(f"Starting sync for {entity_name} (profile {profile_id})")

        # Get balances for this profile
        balances = await self.wise_client.get_balances(profile_id)
        total_synced = 0

        for balance in balances:
            synced = await self._sync_balance(
                profile_id=profile_id,
                entity_name=entity_name,
                balance_id=balance.id,
                currency=balance.currency,
                start_date=start_date,
                end_date=end_date,
                force_full_sync=force_full_sync,
            )
            total_synced += synced

        logger.info(f"Completed sync for {entity_name}: {total_synced} transactions")
        return total_synced

    async def _sync_balance(
        self,
        profile_id: int,
        entity_name: str,
        balance_id: int,
        currency: str,
        start_date: datetime | None,
        end_date: datetime | None,
        force_full_sync: bool,
    ) -> int:
        """Sync transactions for a single currency balance."""
        # Get or create sync metadata
        metadata = await self._get_or_create_metadata(profile_id, currency, entity_name, balance_id)

        # Determine date range
        if end_date is None:
            end_date = datetime.now(UTC)

        if start_date is None:
            if force_full_sync or metadata.last_sync_end_date is None:
                # Default to 90 days back for full sync
                start_date = end_date - timedelta(days=90)
            else:
                # Incremental sync from last sync date (with 1 day overlap)
                start_date = metadata.last_sync_end_date - timedelta(days=1)

        # Update metadata to syncing
        await self._update_metadata_status(metadata.id, "syncing")

        try:
            # Fetch transactions from Wise
            transactions = await self.wise_client.get_transactions(
                profile_id=profile_id,
                balance_id=balance_id,
                currency=currency,
                start_date=start_date,
                end_date=end_date,
            )

            # Store transactions
            synced_count = await self._store_transactions(transactions, profile_id, entity_name)

            # Update metadata on success
            await self._update_metadata_success(metadata.id, end_date, synced_count)

            logger.info(f"Synced {synced_count} {currency} transactions for {entity_name}")
            return synced_count

        except Exception as e:
            logger.error(f"Sync failed for {entity_name} {currency}: {e}")
            await self._update_metadata_error(metadata.id, str(e))
            raise

    async def _get_or_create_metadata(
        self, profile_id: int, currency: str, entity_name: str, balance_id: int
    ) -> SyncMetadata:
        """Get or create sync metadata record."""
        # Try to find existing
        result = await self.session.execute(
            select(SyncMetadata).where(
                SyncMetadata.profile_id == profile_id,
                SyncMetadata.currency == currency,
            )
        )
        metadata = result.scalar_one_or_none()

        if metadata is None:
            # Create new
            metadata = SyncMetadata(
                profile_id=profile_id,
                currency=currency,
                entity_name=entity_name,
                balance_id=balance_id,
            )
            self.session.add(metadata)
            await self.session.flush()

        return metadata

    async def _update_metadata_status(self, metadata_id: int, status: str) -> None:
        """Update sync status."""
        await self.session.execute(
            update(SyncMetadata)
            .where(SyncMetadata.id == metadata_id)
            .values(sync_status=status, error_message=None)
        )
        await self.session.flush()

    async def _update_metadata_success(
        self, metadata_id: int, end_date: datetime, count: int
    ) -> None:
        """Update metadata after successful sync."""
        await self.session.execute(
            update(SyncMetadata)
            .where(SyncMetadata.id == metadata_id)
            .values(
                sync_status="idle",
                last_sync_at=datetime.now(UTC),
                last_sync_end_date=end_date,
                transactions_synced=SyncMetadata.transactions_synced + count,
                error_message=None,
            )
        )
        await self.session.flush()

    async def _update_metadata_error(self, metadata_id: int, error: str) -> None:
        """Update metadata after failed sync."""
        await self.session.execute(
            update(SyncMetadata)
            .where(SyncMetadata.id == metadata_id)
            .values(sync_status="error", error_message=error)
        )
        await self.session.flush()

    async def _store_transactions(
        self,
        transactions: list[WiseTransaction],
        profile_id: int,
        entity_name: str,
    ) -> int:
        """Store transactions using upsert (insert or update on conflict)."""
        if not transactions:
            return 0

        # Build values for upsert
        values = [
            {
                "id": tx.reference_number,
                "profile_id": profile_id,
                "entity_name": entity_name,
                "type": tx.type,
                "transaction_type": tx.transaction_type,
                "date": tx.date,
                "amount": tx.amount,
                "currency": tx.currency,
                "description": tx.description,
                "payment_reference": tx.payment_reference,
                "counterparty_name": tx.counterparty_name,
                "counterparty_account": tx.counterparty_account,
                "from_amount": tx.from_amount,
                "from_currency": tx.from_currency,
                "exchange_rate": tx.exchange_rate,
                "total_fees": tx.total_fees,
                "merchant_name": tx.merchant_name,
                "merchant_category": tx.merchant_category,
                "card_last_four": tx.card_last_four,
                "card_holder_name": tx.card_holder_name,
                "running_balance": tx.running_balance,
                "fetched_at": datetime.now(UTC),
            }
            for tx in transactions
        ]

        # PostgreSQL upsert - update existing records, insert new ones
        stmt = insert(WiseTransactionModel).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "description": stmt.excluded.description,
                "payment_reference": stmt.excluded.payment_reference,
                "counterparty_name": stmt.excluded.counterparty_name,
                "counterparty_account": stmt.excluded.counterparty_account,
                "from_amount": stmt.excluded.from_amount,
                "from_currency": stmt.excluded.from_currency,
                "exchange_rate": stmt.excluded.exchange_rate,
                "total_fees": stmt.excluded.total_fees,
                "running_balance": stmt.excluded.running_balance,
                "fetched_at": stmt.excluded.fetched_at,
                "updated_at": datetime.now(UTC),
            },
        )

        await self.session.execute(stmt)
        await self.session.flush()

        return len(transactions)

    async def sync_all_profiles(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        force_full_sync: bool = False,
    ) -> dict[int, int]:
        """Sync transactions for all configured profiles.

        Returns:
            Dict mapping profile_id to number of transactions synced
        """
        results = {}
        for profile_id in ENTITIES:
            try:
                count = await self.sync_profile(
                    profile_id=profile_id,
                    start_date=start_date,
                    end_date=end_date,
                    force_full_sync=force_full_sync,
                )
                results[profile_id] = count
            except Exception as e:
                logger.error(f"Failed to sync profile {profile_id}: {e}")
                results[profile_id] = -1  # Indicate error

        return results

    async def get_unsynced_transactions(self, limit: int = 100) -> list[WiseTransactionModel]:
        """Get transactions that haven't been matched yet.

        Args:
            limit: Maximum number of transactions to return

        Returns:
            List of unmatched transaction models
        """
        result = await self.session.execute(
            select(WiseTransactionModel)
            .where(WiseTransactionModel.match_status == "pending")
            .order_by(WiseTransactionModel.date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
