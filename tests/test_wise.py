"""Tests for Wise API client."""

import base64
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.wise import (
    WiseClient,
    WiseClientError,
    WiseSCAError,
    WiseTransaction,
)


@pytest.fixture
def mock_private_key(tmp_path):
    """Generate a test RSA private key."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    key_path = tmp_path / "test_private.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(key_path)


@pytest.fixture
def wise_client(mock_private_key):
    """Create WiseClient with test configuration."""
    return WiseClient(
        token="test-token",
        private_key_path=mock_private_key,
        base_url="https://api.wise.com",
    )


class TestWiseTransaction:
    """Tests for WiseTransaction parsing."""

    def test_parse_transfer_transaction(self):
        """Test parsing a TRANSFER transaction."""
        data = {
            "referenceNumber": "TRANSFER-123456",
            "type": "DEBIT",
            "date": "2026-01-15T10:30:00Z",
            "amount": {"value": -1000.50, "currency": "EUR"},
            "totalFees": {"value": 2.50, "currency": "EUR"},
            "runningBalance": {"value": 5000.00, "currency": "EUR"},
            "details": {
                "type": "TRANSFER",
                "description": "Sent money to Ombori AG",
                "recipient": {
                    "name": "Ombori AG",
                    "bankAccount": "CH1234567890",
                },
                "paymentReference": "INV-2026-001",
            },
        }

        tx = WiseTransaction.from_api_response(data, "EUR")

        assert tx.reference_number == "TRANSFER-123456"
        assert tx.type == "DEBIT"
        assert tx.transaction_type == "TRANSFER"
        assert tx.amount == Decimal("-1000.50")
        assert tx.currency == "EUR"
        assert tx.counterparty_name == "Ombori AG"
        assert tx.counterparty_account == "CH1234567890"
        assert tx.payment_reference == "INV-2026-001"
        assert tx.total_fees == Decimal("2.50")
        assert tx.running_balance == Decimal("5000.00")

    def test_parse_deposit_transaction(self):
        """Test parsing a DEPOSIT transaction."""
        data = {
            "referenceNumber": "DEPOSIT-789012",
            "type": "CREDIT",
            "date": "2026-01-16T14:00:00Z",
            "amount": {"value": 5000.00, "currency": "EUR"},
            "runningBalance": {"value": 10000.00, "currency": "EUR"},
            "details": {
                "type": "DEPOSIT",
                "description": "Received money from KLARNA BANK AB",
                "senderName": "KLARNA BANK AB",
                "senderAccount": "SE8595000099602608824831",
                "paymentReference": "INVOL202458/1000087004",
            },
        }

        tx = WiseTransaction.from_api_response(data, "EUR")

        assert tx.reference_number == "DEPOSIT-789012"
        assert tx.type == "CREDIT"
        assert tx.transaction_type == "DEPOSIT"
        assert tx.amount == Decimal("5000.00")
        assert tx.counterparty_name == "KLARNA BANK AB"
        assert tx.counterparty_account == "SE8595000099602608824831"
        assert tx.payment_reference == "INVOL202458/1000087004"

    def test_parse_card_transaction(self):
        """Test parsing a CARD transaction with FX."""
        data = {
            "referenceNumber": "CARD-345678",
            "type": "DEBIT",
            "date": "2026-01-17T09:15:00Z",
            "amount": {"value": -390.49, "currency": "EUR"},
            "details": {
                "type": "CARD",
                "description": "Card transaction of 452.26 USD",
                "merchant": {
                    "name": "Vouch Insurance",
                    "city": "VOUCH.US",
                    "country": "US",
                    "category": "6300 R Insurance Sales",
                },
                "cardLastFourDigits": "3021",
                "cardHolderFullName": "Andreas Hassellöf",
            },
            "exchangeDetails": {
                "toAmount": {"value": 452.26, "currency": "USD"},
                "fromAmount": {"value": 390.49, "currency": "EUR"},
                "rate": 1.16350,
            },
        }

        tx = WiseTransaction.from_api_response(data, "EUR")

        assert tx.reference_number == "CARD-345678"
        assert tx.type == "DEBIT"
        assert tx.transaction_type == "CARD"
        assert tx.amount == Decimal("-390.49")
        assert tx.merchant_name == "Vouch Insurance"
        assert tx.merchant_category == "6300 R Insurance Sales"
        assert tx.card_last_four == "3021"
        assert tx.card_holder_name == "Andreas Hassellöf"
        assert tx.from_amount == Decimal("390.49")
        assert tx.from_currency == "EUR"
        assert tx.exchange_rate == Decimal("1.16350")


class TestWiseClientSCA:
    """Tests for SCA signing."""

    def test_sign_ott_produces_valid_signature(self, wise_client):
        """Test that OTT signing produces a valid base64 signature."""
        ott = "test-one-time-token-12345"
        signature = wise_client.sign_ott(ott)

        # Should be valid base64
        decoded = base64.b64decode(signature)
        assert len(decoded) > 0

        # Signature should be consistent
        signature2 = wise_client.sign_ott(ott)
        assert signature == signature2

    def test_sign_ott_different_tokens_different_signatures(self, wise_client):
        """Test that different OTTs produce different signatures."""
        sig1 = wise_client.sign_ott("token-1")
        sig2 = wise_client.sign_ott("token-2")
        assert sig1 != sig2

    def test_sign_ott_missing_key_raises_error(self, tmp_path):
        """Test that missing private key raises WiseSCAError."""
        client = WiseClient(
            token="test-token",
            private_key_path=str(tmp_path / "nonexistent.pem"),
        )
        with pytest.raises(WiseSCAError, match="Private key not found"):
            client.sign_ott("test-ott")


class TestWiseClientRequests:
    """Tests for API requests."""

    @pytest.mark.asyncio
    async def test_get_profiles_returns_business_profiles(self, wise_client):
        """Test that get_profiles returns business profiles only."""
        mock_response = [
            {
                "id": 19941830,
                "type": "BUSINESS",
                "details": {
                    "name": "Phygrid Limited",
                    "registrationNumber": "12345678",
                },
            },
            {
                "id": 99999999,
                "type": "PERSONAL",
                "details": {"name": "Personal Account"},
            },
        ]

        with patch.object(wise_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response

            async with wise_client:
                profiles = await wise_client.get_profiles()

            assert len(profiles) == 1
            assert profiles[0].id == 19941830
            assert profiles[0].business_name == "Phygrid Limited"
            assert profiles[0].type == "BUSINESS"
            mock_req.assert_called_once_with("GET", "/v2/profiles")

    @pytest.mark.asyncio
    async def test_get_balances_returns_currency_balances(self, wise_client):
        """Test that get_balances returns all currency balances."""
        mock_response = [
            {
                "id": 100001,
                "currency": "EUR",
                "amount": {"value": 10000.50, "currency": "EUR"},
                "reservedAmount": {"value": 0, "currency": "EUR"},
            },
            {
                "id": 100002,
                "currency": "USD",
                "amount": {"value": 5000.25, "currency": "USD"},
                "reservedAmount": {"value": 100.00, "currency": "USD"},
            },
        ]

        with patch.object(wise_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response

            async with wise_client:
                balances = await wise_client.get_balances(19941830)

            assert len(balances) == 2
            assert balances[0].currency == "EUR"
            assert balances[0].amount == Decimal("10000.50")
            assert balances[1].currency == "USD"
            assert balances[1].reserved_amount == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_get_transactions_with_sca(self, wise_client):
        """Test that get_transactions handles SCA and returns transactions."""
        mock_response = {
            "transactions": [
                {
                    "referenceNumber": "TRANSFER-123",
                    "type": "DEBIT",
                    "date": "2026-01-15T10:00:00Z",
                    "amount": {"value": -500.00, "currency": "EUR"},
                    "details": {"type": "TRANSFER", "description": "Test transfer"},
                },
            ],
        }

        with patch.object(wise_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response

            async with wise_client:
                transactions = await wise_client.get_transactions(
                    profile_id=19941830,
                    balance_id=100001,
                    currency="EUR",
                    start_date=datetime(2026, 1, 1, tzinfo=UTC),
                    end_date=datetime(2026, 1, 31, tzinfo=UTC),
                )

            assert len(transactions) == 1
            assert transactions[0].reference_number == "TRANSFER-123"
            mock_req.assert_called_once()
            # Verify SCA flag was set
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["requires_sca"] is True

    @pytest.mark.asyncio
    async def test_client_not_initialized_raises_error(self, wise_client):
        """Test that using client without context manager raises error."""
        with pytest.raises(WiseClientError, match="Client not initialized"):
            await wise_client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_get_entity_name(self, wise_client):
        """Test entity name lookup."""
        assert wise_client.get_entity_name(19941830) == "Phygrid Limited"
        assert wise_client.get_entity_name(47253364) == "Ombori AG"
        assert wise_client.get_entity_name(99999999) == "Unknown"


class TestWiseClientSCAFlow:
    """Tests for full SCA challenge-response flow."""

    @pytest.mark.asyncio
    async def test_sca_challenge_response(self, wise_client):
        """Test that 403 with OTT triggers signature and retry."""
        # First response: 403 with OTT header
        first_response = MagicMock()
        first_response.status_code = 403
        first_response.headers = {"x-2fa-approval": "test-ott-token-12345"}

        # Second response: 200 success
        second_response = MagicMock()
        second_response.status_code = 200
        second_response.json.return_value = {"success": True}

        mock_client = AsyncMock()
        mock_client.request.side_effect = [first_response, second_response]

        async with wise_client:
            wise_client._client = mock_client
            result = await wise_client._request("GET", "/test", requires_sca=True)

        assert result == {"success": True}
        assert mock_client.request.call_count == 2

        # Verify second call had SCA headers
        second_call = mock_client.request.call_args_list[1]
        headers = second_call[1]["headers"]
        assert headers["x-2fa-approval"] == "test-ott-token-12345"
        assert "X-Signature" in headers

    @pytest.mark.asyncio
    async def test_sca_403_without_ott_raises_error(self, wise_client):
        """Test that 403 without OTT header raises WiseSCAError."""
        response = MagicMock()
        response.status_code = 403
        response.headers = {}  # No OTT header

        mock_client = AsyncMock()
        mock_client.request.return_value = response

        async with wise_client:
            wise_client._client = mock_client
            with pytest.raises(WiseSCAError, match="no OTT token received"):
                await wise_client._request("GET", "/test", requires_sca=True)
