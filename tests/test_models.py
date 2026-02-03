"""Tests for database models."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.recon import MatchCandidate, SyncMetadata, WiseTransaction


class TestWiseTransactionModel:
    """Tests for WiseTransaction model."""

    def test_create_transaction(self, db_session):
        """Test creating a Wise transaction."""
        tx = WiseTransaction(
            id="TRANSFER-123456",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
            amount=Decimal("-1000.50"),
            currency="EUR",
            description="Sent money to Ombori AG",
            counterparty_name="Ombori AG",
        )
        db_session.add(tx)
        db_session.commit()

        # Retrieve and verify
        result = db_session.execute(
            select(WiseTransaction).where(WiseTransaction.id == "TRANSFER-123456")
        )
        saved = result.scalar_one()

        assert saved.id == "TRANSFER-123456"
        assert saved.profile_id == 19941830
        assert saved.amount == Decimal("-1000.50")
        assert saved.match_status == "pending"
        assert saved.match_attempts == 0

    def test_transaction_with_fx_details(self, db_session):
        """Test transaction with foreign exchange details."""
        tx = WiseTransaction(
            id="CARD-789",
            profile_id=47253364,
            entity_name="Ombori AG",
            type="DEBIT",
            transaction_type="CARD",
            date=datetime(2026, 1, 16, tzinfo=UTC),
            amount=Decimal("-390.49"),
            currency="EUR",
            from_amount=Decimal("452.26"),
            from_currency="USD",
            exchange_rate=Decimal("1.15830"),
            merchant_name="AWS",
            card_last_four="1234",
        )
        db_session.add(tx)
        db_session.commit()

        saved = db_session.get(WiseTransaction, "CARD-789")
        assert saved.from_amount == Decimal("452.26")
        assert saved.from_currency == "USD"
        assert saved.exchange_rate == Decimal("1.15830")
        assert saved.merchant_name == "AWS"

    def test_transaction_repr(self):
        """Test transaction string representation."""
        tx = WiseTransaction(
            id="TEST-1",
            profile_id=1,
            entity_name="Test",
            type="CREDIT",
            transaction_type="DEPOSIT",
            date=datetime.now(UTC),
            amount=Decimal("100.00"),
            currency="EUR",
        )
        assert "TEST-1" in repr(tx)
        assert "CREDIT" in repr(tx)
        assert "100.00" in repr(tx)


class TestSyncMetadataModel:
    """Tests for SyncMetadata model."""

    def test_create_sync_metadata(self, db_session):
        """Test creating sync metadata."""
        meta = SyncMetadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
            balance_id=100001,
        )
        db_session.add(meta)
        db_session.commit()

        saved = db_session.get(SyncMetadata, meta.id)
        assert saved.profile_id == 19941830
        assert saved.currency == "EUR"
        assert saved.sync_status == "idle"
        assert saved.transactions_synced == 0

    def test_sync_metadata_unique_constraint(self, db_session):
        """Test that profile_id + currency is unique."""
        meta1 = SyncMetadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
        )
        db_session.add(meta1)
        db_session.commit()

        # Adding same profile_id + currency should fail
        meta2 = SyncMetadata(
            profile_id=19941830,
            currency="EUR",
            entity_name="Phygrid Limited",
        )
        db_session.add(meta2)

        with pytest.raises(Exception):  # noqa: B017 - IntegrityError
            db_session.commit()


class TestMatchCandidateModel:
    """Tests for MatchCandidate model."""

    def test_create_match_candidate(self, db_session):
        """Test creating a match candidate."""
        # First create the transaction it references
        tx = WiseTransaction(
            id="TRANSFER-999",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime.now(UTC),
            amount=Decimal("-500.00"),
            currency="EUR",
        )
        db_session.add(tx)
        db_session.commit()

        # Create match candidate
        candidate = MatchCandidate(
            wise_transaction_id="TRANSFER-999",
            netsuite_transaction_id="JE-12345",
            netsuite_type="journalentry",
            netsuite_amount=Decimal("-500.00"),
            confidence_score=Decimal("0.95"),
            match_type="exact",
            match_reasons={"reasons": ["amount_match", "date_match"]},
        )
        db_session.add(candidate)
        db_session.commit()

        saved = db_session.get(MatchCandidate, candidate.id)
        assert saved.wise_transaction_id == "TRANSFER-999"
        assert saved.confidence_score == Decimal("0.95")
        assert saved.match_type == "exact"
        assert saved.match_reasons == {"reasons": ["amount_match", "date_match"]}

    def test_cascade_delete(self, db_session):
        """Test that deleting transaction cascades to candidates."""
        tx = WiseTransaction(
            id="DELETE-TEST",
            profile_id=19941830,
            entity_name="Test",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime.now(UTC),
            amount=Decimal("-100.00"),
            currency="EUR",
        )
        db_session.add(tx)
        db_session.commit()

        candidate = MatchCandidate(
            wise_transaction_id="DELETE-TEST",
            confidence_score=Decimal("0.80"),
        )
        db_session.add(candidate)
        db_session.commit()
        candidate_id = candidate.id

        # Delete the transaction
        db_session.delete(tx)
        db_session.commit()

        # Candidate should be deleted too
        assert db_session.get(MatchCandidate, candidate_id) is None

    def test_relationship_access(self, db_session):
        """Test accessing transaction through relationship."""
        tx = WiseTransaction(
            id="REL-TEST",
            profile_id=19941830,
            entity_name="Test",
            type="CREDIT",
            transaction_type="DEPOSIT",
            date=datetime.now(UTC),
            amount=Decimal("1000.00"),
            currency="EUR",
        )
        db_session.add(tx)
        db_session.commit()

        candidate = MatchCandidate(
            wise_transaction_id="REL-TEST",
            confidence_score=Decimal("0.75"),
        )
        db_session.add(candidate)
        db_session.commit()

        # Access transaction through relationship
        db_session.refresh(candidate)
        assert candidate.wise_transaction.id == "REL-TEST"
        assert candidate.wise_transaction.amount == Decimal("1000.00")

        # Access candidates through transaction
        db_session.refresh(tx)
        assert len(tx.match_candidates) == 1
        assert tx.match_candidates[0].confidence_score == Decimal("0.75")
