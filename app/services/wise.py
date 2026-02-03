"""Wise API client with SCA signing support."""

import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import ENTITIES, settings

logger = logging.getLogger(__name__)


@dataclass
class WiseBalance:
    """Wise balance for a currency."""

    id: int
    currency: str
    amount: Decimal
    reserved_amount: Decimal


@dataclass
class WiseProfile:
    """Wise business profile."""

    id: int
    type: str
    business_name: str
    registration_number: str | None = None


@dataclass
class WiseTransaction:
    """Wise transaction from statement."""

    reference_number: str  # Primary ID (e.g., TRANSFER-1950972714)
    type: str  # DEBIT or CREDIT
    transaction_type: str  # TRANSFER, DEPOSIT, CARD, etc.
    date: datetime
    amount: Decimal
    currency: str
    description: str | None = None
    payment_reference: str | None = None
    counterparty_name: str | None = None
    counterparty_account: str | None = None
    from_amount: Decimal | None = None
    from_currency: str | None = None
    exchange_rate: Decimal | None = None
    total_fees: Decimal | None = None
    running_balance: Decimal | None = None
    merchant_name: str | None = None
    merchant_category: str | None = None
    card_last_four: str | None = None
    card_holder_name: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], currency: str) -> "WiseTransaction":
        """Parse transaction from Wise API response."""
        details = data.get("details", {})
        amount_data = data.get("amount", {})
        exchange_details = data.get("exchangeDetails", {})
        running_balance = data.get("runningBalance", {})
        total_fees = data.get("totalFees", {})

        # Extract counterparty info based on transaction type
        counterparty_name = None
        counterparty_account = None
        merchant_name = None
        merchant_category = None
        card_last_four = None
        card_holder_name = None

        tx_type = details.get("type", "")

        if tx_type == "TRANSFER":
            recipient = details.get("recipient", {})
            counterparty_name = recipient.get("name")
            counterparty_account = recipient.get("bankAccount")
        elif tx_type == "DEPOSIT":
            counterparty_name = details.get("senderName")
            counterparty_account = details.get("senderAccount")
        elif tx_type == "CARD":
            merchant = details.get("merchant", {})
            merchant_name = merchant.get("name")
            merchant_category = merchant.get("category")
            card_last_four = details.get("cardLastFourDigits")
            card_holder_name = details.get("cardHolderFullName")

        # Extract FX details
        from_amount = None
        from_currency = None
        exchange_rate = None
        if exchange_details:
            from_data = exchange_details.get("fromAmount", {})
            from_amount = Decimal(str(from_data.get("value", 0))) if from_data else None
            from_currency = from_data.get("currency") if from_data else None
            exchange_rate = (
                Decimal(str(exchange_details.get("rate"))) if exchange_details.get("rate") else None
            )

        return cls(
            reference_number=data.get("referenceNumber", ""),
            type=data.get("type", ""),
            transaction_type=tx_type,
            date=datetime.fromisoformat(data.get("date", "").replace("Z", "+00:00")),
            amount=Decimal(str(amount_data.get("value", 0))),
            currency=currency,
            description=details.get("description"),
            payment_reference=details.get("paymentReference"),
            counterparty_name=counterparty_name,
            counterparty_account=counterparty_account,
            from_amount=from_amount,
            from_currency=from_currency,
            exchange_rate=exchange_rate,
            total_fees=Decimal(str(total_fees.get("value", 0))) if total_fees else None,
            running_balance=(
                Decimal(str(running_balance.get("value", 0))) if running_balance else None
            ),
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            card_last_four=card_last_four,
            card_holder_name=card_holder_name,
        )


class WiseClientError(Exception):
    """Base exception for Wise client errors."""

    pass


class WiseSCAError(WiseClientError):
    """SCA signing failed."""

    pass


class WiseAPIError(WiseClientError):
    """API request failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class WiseClient:
    """Async client for Wise API with SCA signing support."""

    def __init__(
        self,
        token: str | None = None,
        private_key_path: str | None = None,
        base_url: str | None = None,
    ):
        self.token = token or settings.wise_api_token
        self.private_key_path = Path(private_key_path or settings.wise_private_key_path)
        self.base_url = base_url or settings.wise_api_base
        self._private_key = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WiseClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _load_private_key(self):
        """Load RSA private key for SCA signing."""
        if self._private_key is None:
            if not self.private_key_path.exists():
                raise WiseSCAError(f"Private key not found: {self.private_key_path}")
            key_data = self.private_key_path.read_bytes()
            self._private_key = serialization.load_pem_private_key(key_data, password=None)
        return self._private_key

    def sign_ott(self, ott: str) -> str:
        """Sign one-time token for SCA.

        Args:
            ott: One-time token from x-2fa-approval header

        Returns:
            Base64-encoded signature
        """
        private_key = self._load_private_key()
        signature = private_key.sign(
            ott.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        requires_sca: bool = False,
    ) -> dict[str, Any]:
        """Make API request with optional SCA handling.

        Args:
            method: HTTP method
            path: API path
            params: Query parameters
            json: JSON body
            requires_sca: Whether endpoint requires SCA

        Returns:
            JSON response data

        Raises:
            WiseAPIError: If request fails
            WiseSCAError: If SCA signing fails
        """
        if self._client is None:
            raise WiseClientError("Client not initialized. Use async with context manager.")

        response = await self._client.request(method, path, params=params, json=json)

        # Handle SCA challenge
        if response.status_code == 403 and requires_sca:
            ott = response.headers.get("x-2fa-approval")
            if not ott:
                raise WiseSCAError("SCA required but no OTT token received")

            logger.debug(f"SCA challenge received, signing OTT: {ott[:20]}...")
            signature = self.sign_ott(ott)

            # Retry with signed headers
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers={
                    "x-2fa-approval": ott,
                    "X-Signature": signature,
                },
            )

        if response.status_code >= 400:
            raise WiseAPIError(
                f"API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def get_profiles(self) -> list[WiseProfile]:
        """Get all business profiles accessible to the token.

        Returns:
            List of WiseProfile objects
        """
        data = await self._request("GET", "/v2/profiles")
        return [
            WiseProfile(
                id=p["id"],
                type=p["type"],
                business_name=p.get("details", {}).get("name", ""),
                registration_number=p.get("details", {}).get("registrationNumber"),
            )
            for p in data
            if p["type"] == "BUSINESS"
        ]

    async def get_balances(self, profile_id: int) -> list[WiseBalance]:
        """Get all currency balances for a profile.

        Args:
            profile_id: Wise profile ID

        Returns:
            List of WiseBalance objects
        """
        data = await self._request(
            "GET", f"/v4/profiles/{profile_id}/balances", params={"types": "STANDARD"}
        )
        return [
            WiseBalance(
                id=b["id"],
                currency=b["currency"],
                amount=Decimal(str(b["amount"]["value"])),
                reserved_amount=Decimal(str(b.get("reservedAmount", {}).get("value", 0))),
            )
            for b in data
        ]

    async def get_transactions(
        self,
        profile_id: int,
        balance_id: int,
        currency: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[WiseTransaction]:
        """Get transactions from balance statement.

        Requires SCA signing.

        Args:
            profile_id: Wise profile ID
            balance_id: Balance ID for the currency
            currency: Currency code (e.g., EUR)
            start_date: Start of period
            end_date: End of period

        Returns:
            List of WiseTransaction objects
        """
        path = f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json"
        params = {
            "currency": currency,
            "intervalStart": start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "intervalEnd": end_date.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "type": "COMPACT",
        }

        data = await self._request("GET", path, params=params, requires_sca=True)

        transactions = []
        for tx in data.get("transactions", []):
            try:
                transactions.append(WiseTransaction.from_api_response(tx, currency))
            except Exception as e:
                logger.warning(f"Failed to parse transaction: {e}")
                continue

        return transactions

    async def get_all_transactions_for_profile(
        self,
        profile_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> list[WiseTransaction]:
        """Get transactions for all currencies in a profile.

        Args:
            profile_id: Wise profile ID
            start_date: Start of period
            end_date: End of period

        Returns:
            List of all transactions across all currencies
        """
        balances = await self.get_balances(profile_id)
        all_transactions = []

        for balance in balances:
            try:
                transactions = await self.get_transactions(
                    profile_id=profile_id,
                    balance_id=balance.id,
                    currency=balance.currency,
                    start_date=start_date,
                    end_date=end_date,
                )
                all_transactions.extend(transactions)
                logger.info(
                    f"Fetched {len(transactions)} transactions for "
                    f"profile {profile_id} currency {balance.currency}"
                )
            except WiseAPIError as e:
                if e.status_code == 404:
                    # No transactions for this balance
                    logger.debug(f"No transactions for balance {balance.id}")
                    continue
                raise

        return all_transactions

    def get_entity_name(self, profile_id: int) -> str:
        """Get entity name for a profile ID.

        Args:
            profile_id: Wise profile ID

        Returns:
            Entity name or "Unknown" if not found
        """
        entity_info = ENTITIES.get(profile_id)
        return entity_info["name"] if entity_info else "Unknown"
