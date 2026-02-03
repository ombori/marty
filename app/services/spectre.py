"""Spectre API client for reconciliation workflow."""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GLEntry:
    """General Ledger entry from NetSuite via Spectre."""

    transaction_id: str
    line_id: int
    transaction_type: str
    date: datetime
    amount: Decimal
    currency: str
    account_id: int
    account_name: str
    entity_id: int
    entity_name: str
    memo: str | None = None
    is_reconciled: bool = False


@dataclass
class ReconPattern:
    """Reconciliation pattern for auto-matching."""

    id: UUID
    pattern_type: str  # counterparty, reference, amount_range, description
    pattern_value: str
    is_regex: bool
    target_type: str  # vendor, customer, account, subsidiary
    target_netsuite_id: str
    target_name: str
    is_auto_approve: bool
    confidence_boost: Decimal
    times_used: int
    times_approved: int


@dataclass
class SuggestionResponse:
    """Response from submitting a suggestion."""

    id: UUID
    status: str


@dataclass
class BatchResponse:
    """Response from submitting a batch."""

    batch_id: UUID
    count: int


class SpectreClientError(Exception):
    """Base exception for Spectre client errors."""

    pass


class SpectreAPIError(SpectreClientError):
    """API request failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SpectreClient:
    """Async client for Spectre reconciliation API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.base_url = base_url or settings.spectre_api_url
        self.api_key = api_key or settings.spectre_api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SpectreClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": self.api_key,
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make API request."""
        if self._client is None:
            raise SpectreClientError("Client not initialized. Use async with context manager.")

        response = await self._client.request(method, path, params=params, json=json)

        if response.status_code >= 400:
            raise SpectreAPIError(
                f"API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def submit_suggestion(
        self,
        wise_transaction_id: str,
        wise_profile_id: int,
        entity_name: str,
        transaction_date: datetime,
        amount: Decimal,
        currency: str,
        transaction_type: str,
        match_type: str,
        confidence_score: Decimal,
        description: str | None = None,
        counterparty: str | None = None,
        match_explanation: str | None = None,
        match_reasons: list[str] | None = None,
        netsuite_transaction_id: str | None = None,
        netsuite_line_id: int | None = None,
        netsuite_type: str | None = None,
        suggested_account_id: int | None = None,
        suggested_account_name: str | None = None,
        is_intercompany: bool = False,
        counterparty_entity: str | None = None,
    ) -> SuggestionResponse:
        """Submit a reconciliation suggestion.

        Args:
            wise_transaction_id: Wise transaction reference
            wise_profile_id: Wise profile ID
            entity_name: Entity name
            transaction_date: Transaction date
            amount: Transaction amount
            currency: Currency code
            transaction_type: Type (TRANSFER, DEPOSIT, etc.)
            match_type: Match type (exact, fuzzy, llm, pattern, unmatched)
            confidence_score: Confidence score (0-1)
            description: Transaction description
            counterparty: Counterparty name
            match_explanation: Human-readable match explanation
            match_reasons: List of match reason strings
            netsuite_transaction_id: Matched NetSuite transaction ID
            netsuite_line_id: Matched NetSuite line ID
            netsuite_type: NetSuite transaction type
            suggested_account_id: Suggested GL account ID
            suggested_account_name: Suggested GL account name
            is_intercompany: Whether this is an IC transfer
            counterparty_entity: IC counterparty entity name

        Returns:
            SuggestionResponse with ID and status
        """
        payload = {
            "wise_transaction_id": wise_transaction_id,
            "wise_profile_id": wise_profile_id,
            "entity_name": entity_name,
            "transaction_date": transaction_date.isoformat(),
            "amount": str(amount),
            "currency": currency,
            "transaction_type": transaction_type,
            "match_type": match_type,
            "confidence_score": str(confidence_score),
            "description": description,
            "counterparty": counterparty,
            "match_explanation": match_explanation,
            "match_reasons": match_reasons or [],
            "netsuite_transaction_id": netsuite_transaction_id,
            "netsuite_line_id": netsuite_line_id,
            "netsuite_type": netsuite_type,
            "suggested_account_id": suggested_account_id,
            "suggested_account_name": suggested_account_name,
            "is_intercompany": is_intercompany,
            "counterparty_entity": counterparty_entity,
        }

        data = await self._request("POST", "/api/recon/suggestions", json=payload)
        return SuggestionResponse(id=UUID(data["id"]), status=data["status"])

    async def submit_batch(
        self,
        entity_name: str,
        start_date: datetime,
        end_date: datetime,
        suggestions: list[dict[str, Any]],
    ) -> BatchResponse:
        """Submit a batch of suggestions.

        Args:
            entity_name: Entity name for the batch
            start_date: Batch start date
            end_date: Batch end date
            suggestions: List of suggestion dictionaries

        Returns:
            BatchResponse with batch ID and count
        """
        payload = {
            "entity_name": entity_name,
            "start_date": start_date.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "suggestions": suggestions,
        }

        data = await self._request("POST", "/api/recon/suggestions/batch", json=payload)
        return BatchResponse(batch_id=UUID(data["batch_id"]), count=data["count"])

    async def get_suggestion_status(self, suggestion_id: UUID) -> dict[str, Any]:
        """Get status of a suggestion.

        Args:
            suggestion_id: Suggestion UUID

        Returns:
            Full suggestion details including status
        """
        return await self._request("GET", f"/api/recon/suggestions/{suggestion_id}")

    async def get_gl_entries(
        self,
        subsidiary_id: int,
        start_date: datetime,
        end_date: datetime,
        account_types: list[str] | None = None,
        unreconciled_only: bool = True,
    ) -> list[GLEntry]:
        """Get GL entries for matching.

        Args:
            subsidiary_id: NetSuite subsidiary ID
            start_date: Start date
            end_date: End date
            account_types: Filter by account types
            unreconciled_only: Only return unreconciled entries

        Returns:
            List of GLEntry objects
        """
        params: dict[str, Any] = {
            "subsidiary_id": subsidiary_id,
            "start_date": start_date.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "unreconciled_only": unreconciled_only,
        }
        if account_types:
            params["account_types"] = ",".join(account_types)

        data = await self._request("GET", "/api/recon/gl-entries", params=params)

        return [
            GLEntry(
                transaction_id=item["transaction_id"],
                line_id=item["line_id"],
                transaction_type=item["transaction_type"],
                date=datetime.fromisoformat(item["date"]),
                amount=Decimal(str(item["amount"])),
                currency=item["currency"],
                account_id=item["account_id"],
                account_name=item["account_name"],
                entity_id=item["entity_id"],
                entity_name=item["entity_name"],
                memo=item.get("memo"),
                is_reconciled=item.get("is_reconciled", False),
            )
            for item in data.get("items", [])
        ]

    async def get_patterns(
        self,
        active_only: bool = True,
        auto_approve_only: bool = False,
    ) -> list[ReconPattern]:
        """Get reconciliation patterns.

        Args:
            active_only: Only return active patterns
            auto_approve_only: Only return auto-approve patterns

        Returns:
            List of ReconPattern objects
        """
        params = {
            "active_only": active_only,
            "auto_approve_only": auto_approve_only,
        }

        data = await self._request("GET", "/api/recon/patterns", params=params)

        return [
            ReconPattern(
                id=UUID(item["id"]),
                pattern_type=item["pattern_type"],
                pattern_value=item["pattern_value"],
                is_regex=item.get("is_regex", False),
                target_type=item["target_type"],
                target_netsuite_id=item["target_netsuite_id"],
                target_name=item["target_name"],
                is_auto_approve=item.get("is_auto_approve", False),
                confidence_boost=Decimal(str(item.get("confidence_boost", "0.10"))),
                times_used=item.get("times_used", 0),
                times_approved=item.get("times_approved", 0),
            )
            for item in data.get("items", [])
        ]

    async def submit_pattern(
        self,
        pattern_type: str,
        pattern_value: str,
        target_type: str,
        target_netsuite_id: str,
        target_name: str,
        is_regex: bool = False,
        description: str | None = None,
    ) -> UUID:
        """Submit a new learned pattern.

        Args:
            pattern_type: Type (counterparty, reference, amount_range, description)
            pattern_value: Pattern value or regex
            target_type: Target type (vendor, customer, account, subsidiary)
            target_netsuite_id: NetSuite internal ID
            target_name: Human-readable name
            is_regex: Whether pattern_value is a regex
            description: Pattern description

        Returns:
            UUID of created pattern
        """
        payload = {
            "pattern_type": pattern_type,
            "pattern_value": pattern_value,
            "is_regex": is_regex,
            "target_type": target_type,
            "target_netsuite_id": target_netsuite_id,
            "target_name": target_name,
            "description": description,
        }

        data = await self._request("POST", "/api/recon/patterns", json=payload)
        return UUID(data["id"])

    async def enrich_transaction(
        self,
        netsuite_transaction_id: str,
        wise_transaction_id: str,
        counterparty_name: str | None = None,
        counterparty_iban: str | None = None,
        payment_reference: str | None = None,
        fx_rate: Decimal | None = None,
        from_amount: Decimal | None = None,
        from_currency: str | None = None,
        fees: Decimal | None = None,
        is_intercompany: bool | None = None,
        ic_entity: str | None = None,
        merchant_name: str | None = None,
        card_last4: str | None = None,
    ) -> bool:
        """Enrich a NetSuite transaction with Wise data.

        Args:
            netsuite_transaction_id: NetSuite transaction ID
            wise_transaction_id: Wise transaction reference
            counterparty_name: Full counterparty name
            counterparty_iban: Counterparty IBAN
            payment_reference: Payment reference/memo
            fx_rate: Exchange rate
            from_amount: Original currency amount
            from_currency: Original currency code
            fees: Transaction fees
            is_intercompany: Whether this is IC
            ic_entity: IC counterparty entity name
            merchant_name: Card merchant name
            card_last4: Card last 4 digits

        Returns:
            True if successful
        """
        enrichment_data = {}
        if counterparty_name is not None:
            enrichment_data["counterparty_name"] = counterparty_name
        if counterparty_iban is not None:
            enrichment_data["counterparty_iban"] = counterparty_iban
        if payment_reference is not None:
            enrichment_data["payment_reference"] = payment_reference
        if fx_rate is not None:
            enrichment_data["fx_rate"] = str(fx_rate)
        if from_amount is not None:
            enrichment_data["from_amount"] = str(from_amount)
        if from_currency is not None:
            enrichment_data["from_currency"] = from_currency
        if fees is not None:
            enrichment_data["fees"] = str(fees)
        if is_intercompany is not None:
            enrichment_data["is_intercompany"] = is_intercompany
        if ic_entity is not None:
            enrichment_data["ic_entity"] = ic_entity
        if merchant_name is not None:
            enrichment_data["merchant_name"] = merchant_name
        if card_last4 is not None:
            enrichment_data["card_last4"] = card_last4

        payload = {
            "netsuite_transaction_id": netsuite_transaction_id,
            "wise_transaction_id": wise_transaction_id,
            "enrichment_data": enrichment_data,
        }

        data = await self._request("POST", "/api/recon/enrich", json=payload)
        return data.get("success", False)
