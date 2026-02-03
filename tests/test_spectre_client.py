"""Tests for Spectre API client."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.spectre import (
    BatchResponse,
    GLEntry,
    ReconPattern,
    SpectreClient,
    SpectreClientError,
    SuggestionResponse,
)


@pytest.fixture
def spectre_client():
    """Create SpectreClient with test configuration."""
    return SpectreClient(
        base_url="https://spectre.test.com",
        api_key="test-api-key",
    )


class TestSpectreClient:
    """Tests for SpectreClient."""

    @pytest.mark.asyncio
    async def test_submit_suggestion(self, spectre_client):
        """Test submitting a suggestion."""
        mock_response = {
            "id": str(uuid4()),
            "status": "pending",
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                spectre_client,
                "_request",
                AsyncMock(return_value=mock_response),
            )

            async with spectre_client:
                result = await spectre_client.submit_suggestion(
                    wise_transaction_id="TRANSFER-123",
                    wise_profile_id=19941830,
                    entity_name="Phygrid Limited",
                    transaction_date=datetime.now(UTC),
                    amount=Decimal("1000.00"),
                    currency="EUR",
                    transaction_type="TRANSFER",
                    match_type="exact",
                    confidence_score=Decimal("0.95"),
                )

            assert isinstance(result, SuggestionResponse)
            assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_submit_batch(self, spectre_client):
        """Test submitting a batch of suggestions."""
        mock_response = {
            "batch_id": str(uuid4()),
            "count": 5,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                spectre_client,
                "_request",
                AsyncMock(return_value=mock_response),
            )

            async with spectre_client:
                result = await spectre_client.submit_batch(
                    entity_name="Phygrid Limited",
                    start_date=datetime(2026, 1, 1, tzinfo=UTC),
                    end_date=datetime(2026, 1, 31, tzinfo=UTC),
                    suggestions=[{"id": "1"}, {"id": "2"}],
                )

            assert isinstance(result, BatchResponse)
            assert result.count == 5

    @pytest.mark.asyncio
    async def test_get_gl_entries(self, spectre_client):
        """Test fetching GL entries."""
        mock_response = {
            "items": [
                {
                    "transaction_id": "JE-12345",
                    "line_id": 1,
                    "transaction_type": "journalentry",
                    "date": "2026-01-15T00:00:00+00:00",
                    "amount": "1000.00",
                    "currency": "EUR",
                    "account_id": 1000,
                    "account_name": "Bank EUR",
                    "entity_id": 1,
                    "entity_name": "Phygrid Limited",
                    "memo": "Test memo",
                },
            ],
            "total": 1,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                spectre_client,
                "_request",
                AsyncMock(return_value=mock_response),
            )

            async with spectre_client:
                entries = await spectre_client.get_gl_entries(
                    subsidiary_id=1,
                    start_date=datetime(2026, 1, 1, tzinfo=UTC),
                    end_date=datetime(2026, 1, 31, tzinfo=UTC),
                )

            assert len(entries) == 1
            assert isinstance(entries[0], GLEntry)
            assert entries[0].transaction_id == "JE-12345"
            assert entries[0].amount == Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_get_patterns(self, spectre_client):
        """Test fetching patterns."""
        pattern_id = uuid4()
        mock_response = {
            "items": [
                {
                    "id": str(pattern_id),
                    "pattern_type": "counterparty",
                    "pattern_value": "Amazon",
                    "is_regex": False,
                    "target_type": "account",
                    "target_netsuite_id": "1234",
                    "target_name": "AWS Expense",
                    "is_auto_approve": True,
                    "confidence_boost": "0.15",
                    "times_used": 10,
                    "times_approved": 9,
                },
            ],
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                spectre_client,
                "_request",
                AsyncMock(return_value=mock_response),
            )

            async with spectre_client:
                patterns = await spectre_client.get_patterns()

            assert len(patterns) == 1
            assert isinstance(patterns[0], ReconPattern)
            assert patterns[0].pattern_value == "Amazon"
            assert patterns[0].is_auto_approve is True

    @pytest.mark.asyncio
    async def test_enrich_transaction(self, spectre_client):
        """Test enriching a transaction."""
        mock_response = {"success": True, "netsuite_transaction_id": "123"}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                spectre_client,
                "_request",
                AsyncMock(return_value=mock_response),
            )

            async with spectre_client:
                result = await spectre_client.enrich_transaction(
                    netsuite_transaction_id="123",
                    wise_transaction_id="TRANSFER-456",
                    counterparty_name="Test Company",
                    fx_rate=Decimal("1.0850"),
                )

            assert result is True

    @pytest.mark.asyncio
    async def test_client_not_initialized_error(self, spectre_client):
        """Test error when client not initialized."""
        with pytest.raises(SpectreClientError, match="not initialized"):
            await spectre_client._request("GET", "/test")
