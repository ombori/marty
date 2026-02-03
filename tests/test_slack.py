"""Tests for Slack notifier."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.reconcile import ReconciliationResult
from app.services.slack import MockSlackNotifier, SlackNotifier


class TestSlackNotifier:
    """Tests for SlackNotifier."""

    @pytest.mark.asyncio
    async def test_send_daily_digest(self):
        """Test sending daily digest notification."""
        notifier = MockSlackNotifier()

        result = await notifier.send_daily_digest(
            pending_count=25,
            pending_amount=Decimal("15000.50"),
            by_entity={
                "Phygrid Limited": 10,
                "Ombori AG": 8,
                "Fendops Limited": 7,
            },
        )

        assert result is True
        assert len(notifier.messages) == 1

        message = notifier.messages[0]
        assert "25" in message.text
        assert "15,000.50" in message.text
        assert message.blocks is not None
        assert any("Daily" in str(b) for b in message.blocks)

    @pytest.mark.asyncio
    async def test_send_discrepancy_alert(self):
        """Test sending discrepancy alert."""
        notifier = MockSlackNotifier()

        result = await notifier.send_discrepancy_alert(
            entity_name="Phygrid Limited",
            unmatched_count=15,
            large_transactions=[
                {
                    "date": "2026-01-15",
                    "amount": 5000.00,
                    "currency": "EUR",
                    "counterparty": "Unknown Vendor",
                },
            ],
            threshold_exceeded=True,
        )

        assert result is True
        assert len(notifier.messages) == 1

        message = notifier.messages[0]
        assert "Phygrid Limited" in message.text
        assert "15" in message.text

    @pytest.mark.asyncio
    async def test_send_reconciliation_complete(self):
        """Test sending reconciliation complete notification."""
        notifier = MockSlackNotifier()

        result_data = ReconciliationResult(
            entity_name="Phygrid Limited",
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            end_date=datetime(2026, 1, 31, tzinfo=UTC),
            transactions_processed=100,
            exact_matches=50,
            fuzzy_matches=30,
            llm_matches=10,
            pattern_matches=5,
            unmatched=5,
            auto_approved=45,
            submitted_for_review=50,
            duration_seconds=120.5,
        )

        result = await notifier.send_reconciliation_complete(result_data)

        assert result is True
        assert len(notifier.messages) == 1

        message = notifier.messages[0]
        assert "Phygrid Limited" in message.text
        assert "90" in message.text  # 90% match rate

    @pytest.mark.asyncio
    async def test_send_error_alert(self):
        """Test sending error alert."""
        notifier = MockSlackNotifier()

        result = await notifier.send_error_alert(
            entity_name="Ombori AG",
            error_message="Connection timeout to Wise API",
            context="During daily reconciliation run",
        )

        assert result is True
        assert len(notifier.messages) == 1

        message = notifier.messages[0]
        assert "error" in message.text.lower()
        assert "Ombori AG" in message.text

    @pytest.mark.asyncio
    async def test_no_bot_token_configured(self):
        """Test that missing bot token is handled gracefully."""
        notifier = SlackNotifier(bot_token="")

        result = await notifier.send_daily_digest(
            pending_count=10,
            pending_amount=Decimal("1000"),
            by_entity={},
        )

        # Should return False but not raise
        assert result is False
