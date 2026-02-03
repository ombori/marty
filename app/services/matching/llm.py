"""LLM-based matching for complex transactions."""

import json
import logging
from decimal import Decimal

import httpx

from app.config import settings
from app.models.recon import WiseTransaction
from app.services.spectre import GLEntry

from .confidence import MatchResult, MatchType

logger = logging.getLogger(__name__)


class LLMMatcher:
    """LLM-based matching engine for complex transactions.

    Tier 3 matching with confidence 0.50-0.89:
    Used when exact/fuzzy matching fails but candidates exist.
    - Parses shorthand references (INV-2024-001 → Invoice 2024-001)
    - Infers invoice numbers from free text
    - Handles company name variations
    - Explains reasoning
    """

    SYSTEM_PROMPT = """You are a financial reconciliation assistant. Your job is to match bank transactions with GL entries.

Given a bank transaction and a list of potential GL entry matches, determine:
1. Which GL entry (if any) is the best match
2. Your confidence level (0.0 to 1.0)
3. A brief explanation of your reasoning

Consider:
- Amount matching (exact or within FX tolerance)
- Date proximity
- Payment references and invoice numbers (may be abbreviated or formatted differently)
- Company name variations
- Transaction descriptions

Respond in JSON format:
{
  "match_index": <index of best match, or -1 if no match>,
  "confidence": <0.0 to 1.0>,
  "explanation": "<brief explanation>",
  "inferred_reference": "<any invoice/reference number you extracted, or null>"
}"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-haiku-20240307",
        api_base: str = "https://api.anthropic.com",
    ):
        """Initialize LLM matcher.

        Args:
            api_key: Anthropic API key
            model: Model to use
            api_base: API base URL
        """
        self.api_key = api_key or getattr(settings, "anthropic_api_key", "")
        self.model = model
        self.api_base = api_base

    async def match(
        self,
        transaction: WiseTransaction,
        gl_entries: list[GLEntry],
        max_candidates: int = 5,
    ) -> MatchResult | None:
        """Attempt LLM-based match for a transaction.

        Args:
            transaction: Wise transaction to match
            gl_entries: List of GL entries to match against
            max_candidates: Maximum candidates to send to LLM

        Returns:
            MatchResult if match found, None otherwise
        """
        if not gl_entries:
            return None

        if not self.api_key:
            logger.warning("LLM matching disabled: no API key configured")
            return None

        # Limit candidates
        candidates = gl_entries[:max_candidates]

        # Build prompt
        user_prompt = self._build_prompt(transaction, candidates)

        try:
            response = await self._call_llm(user_prompt)
            return self._parse_response(response, candidates)
        except Exception as e:
            logger.error(f"LLM matching failed: {e}")
            return None

    def _build_prompt(self, transaction: WiseTransaction, candidates: list[GLEntry]) -> str:
        """Build the user prompt for LLM matching."""
        tx_info = f"""Bank Transaction:
- Reference: {transaction.id}
- Date: {transaction.date.date()}
- Amount: {transaction.amount} {transaction.currency}
- Type: {transaction.transaction_type}
- Description: {transaction.description or "N/A"}
- Payment Reference: {transaction.payment_reference or "N/A"}
- Counterparty: {transaction.counterparty_name or "N/A"}"""

        if transaction.from_amount:
            tx_info += f"\n- Original Amount: {transaction.from_amount} {transaction.from_currency}"
            tx_info += f"\n- Exchange Rate: {transaction.exchange_rate}"

        candidates_info = "\nPotential GL Entry Matches:\n"
        for i, entry in enumerate(candidates):
            candidates_info += f"""
[{i}] {entry.transaction_id}
    - Date: {entry.date.date()}
    - Amount: {entry.amount} {entry.currency}
    - Type: {entry.transaction_type}
    - Account: {entry.account_name}
    - Memo: {entry.memo or "N/A"}
"""

        return tx_info + candidates_info

    async def _call_llm(self, user_prompt: str) -> dict:
        """Call the LLM API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 500,
                    "system": self.SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            # Extract text content
            content = data.get("content", [{}])[0].get("text", "{}")

            # Parse JSON from response
            # Handle potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            return json.loads(content.strip())

    def _parse_response(self, response: dict, candidates: list[GLEntry]) -> MatchResult | None:
        """Parse LLM response into MatchResult."""
        match_index = response.get("match_index", -1)
        confidence = response.get("confidence", 0.0)
        explanation = response.get("explanation", "")
        inferred_ref = response.get("inferred_reference")

        if match_index < 0 or match_index >= len(candidates):
            return None

        entry = candidates[match_index]

        # Determine match type based on confidence
        match_type = MatchType.LLM_CONFIDENT if confidence >= 0.8 else MatchType.LLM_UNCERTAIN

        reasons = ["llm_match"]
        if inferred_ref:
            reasons.append(f"inferred_reference:{inferred_ref}")

        return MatchResult(
            match_type=match_type,
            confidence=Decimal(str(confidence)),
            reasons=reasons,
            netsuite_transaction_id=entry.transaction_id,
            netsuite_line_id=entry.line_id,
            netsuite_type=entry.transaction_type,
            suggested_account_id=entry.account_id,
            suggested_account_name=entry.account_name,
            explanation=explanation,
        )


class MockLLMMatcher(LLMMatcher):
    """Mock LLM matcher for testing."""

    def __init__(self, mock_response: dict | None = None):
        """Initialize mock matcher.

        Args:
            mock_response: Response to return (default: no match)
        """
        super().__init__(api_key="mock")
        self.mock_response = mock_response or {"match_index": -1, "confidence": 0.0}
        self.calls: list[str] = []

    async def _call_llm(self, user_prompt: str) -> dict:
        """Return mock response."""
        self.calls.append(user_prompt)
        return self.mock_response
