"""Exact matching for transactions."""

import logging
import re
from decimal import Decimal

from app.models.recon import WiseTransaction
from app.services.spectre import GLEntry

from .confidence import MatchResult, MatchType

logger = logging.getLogger(__name__)


class ExactMatcher:
    """Exact matching engine for bank transactions.

    Tier 1 matching with confidence 0.95-1.00:
    - Amount exact match (to cent)
    - Date within 1 day
    - Payment reference contains NetSuite tranid OR
    - Counterparty IBAN matches known entity account OR
    - Pre-approved pattern exact match
    """

    # Amount tolerance for "exact" match (handles rounding)
    AMOUNT_TOLERANCE = Decimal("0.01")

    # Maximum date difference for exact match
    MAX_DATE_DIFF_DAYS = 1

    def __init__(self, known_entity_ibans: dict[str, str] | None = None):
        """Initialize exact matcher.

        Args:
            known_entity_ibans: Dict mapping IBAN to entity name
        """
        self.known_entity_ibans = known_entity_ibans or {}

    def match(
        self,
        transaction: WiseTransaction,
        gl_entries: list[GLEntry],
        patterns: list[dict] | None = None,
    ) -> MatchResult | None:
        """Attempt exact match for a transaction.

        Args:
            transaction: Wise transaction to match
            gl_entries: List of GL entries to match against
            patterns: Optional list of pre-approved patterns

        Returns:
            MatchResult if exact match found, None otherwise
        """
        patterns = patterns or []

        for entry in gl_entries:
            result = self._try_match(transaction, entry, patterns)
            if result is not None:
                return result

        return None

    def _try_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
        patterns: list[dict],
    ) -> MatchResult | None:
        """Try to match a single transaction against a GL entry."""
        reasons = []

        # Check amount match (within tolerance)
        amount_match = self._check_amount_match(transaction, entry)
        if not amount_match:
            return None
        reasons.append("amount_exact")

        # Check date match (within 1 day)
        date_match = self._check_date_match(transaction, entry)
        if not date_match:
            return None
        reasons.append("date_within_1_day")

        # Now check for additional exact match criteria
        match_type = None

        # Check payment reference contains tranid
        if self._check_reference_match(transaction, entry):
            reasons.append("reference_contains_tranid")
            match_type = MatchType.EXACT_ALL

        # Check counterparty IBAN
        elif self._check_iban_match(transaction):
            reasons.append("counterparty_iban_known")
            match_type = MatchType.EXACT_AMOUNT_REF

        # Check pattern match
        elif self._check_pattern_match(transaction, entry, patterns):
            reasons.append("pattern_exact_match")
            match_type = MatchType.EXACT_AMOUNT_REF

        # If we have amount + date but no additional criteria, it's still a good match
        if match_type is None:
            match_type = MatchType.EXACT_AMOUNT_DATE

        return MatchResult(
            match_type=match_type,
            confidence=self._get_confidence(match_type),
            reasons=reasons,
            netsuite_transaction_id=entry.transaction_id,
            netsuite_line_id=entry.line_id,
            netsuite_type=entry.transaction_type,
            suggested_account_id=entry.account_id,
            suggested_account_name=entry.account_name,
        )

    def _check_amount_match(self, transaction: WiseTransaction, entry: GLEntry) -> bool:
        """Check if amounts match within tolerance."""
        # Use absolute values for comparison (transaction might be negative)
        tx_amount = abs(transaction.amount)
        gl_amount = abs(entry.amount)

        return abs(tx_amount - gl_amount) <= self.AMOUNT_TOLERANCE

    def _check_date_match(self, transaction: WiseTransaction, entry: GLEntry) -> bool:
        """Check if dates are within acceptable range."""
        tx_date = transaction.date.date()
        gl_date = entry.date.date()

        diff = abs((tx_date - gl_date).days)
        return diff <= self.MAX_DATE_DIFF_DAYS

    def _check_reference_match(self, transaction: WiseTransaction, entry: GLEntry) -> bool:
        """Check if payment reference contains NetSuite transaction ID or matches memo."""
        if not transaction.payment_reference:
            return False

        ref = transaction.payment_reference.upper()
        tranid = entry.transaction_id.upper()

        # Direct containment of transaction ID in reference
        if tranid in ref:
            return True

        # Check if payment reference appears in memo (common pattern)
        if entry.memo:
            memo = entry.memo.upper()
            if ref in memo:
                return True

        # Check for common patterns like INV-12345, JE-12345, etc.
        # Extract numbers from both and compare
        ref_numbers = set(re.findall(r"\d+", ref))
        tranid_numbers = set(re.findall(r"\d+", tranid))

        if ref_numbers and tranid_numbers:
            return bool(ref_numbers & tranid_numbers)

        return False

    def _check_iban_match(self, transaction: WiseTransaction) -> bool:
        """Check if counterparty IBAN is a known entity account."""
        if not transaction.counterparty_account:
            return False

        # Normalize IBAN (remove spaces)
        iban = transaction.counterparty_account.replace(" ", "").upper()

        return iban in self.known_entity_ibans

    def _check_pattern_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
        patterns: list[dict],
    ) -> bool:
        """Check if transaction matches any pre-approved pattern."""
        for pattern in patterns:
            if self._pattern_matches(transaction, pattern):
                # Also verify pattern targets this account
                target_id = pattern.get("target_netsuite_id")
                if target_id and str(entry.account_id) == str(target_id):
                    return True
        return False

    def _pattern_matches(self, transaction: WiseTransaction, pattern: dict) -> bool:
        """Check if a single pattern matches the transaction."""
        pattern_type = pattern.get("pattern_type")
        pattern_value = pattern.get("pattern_value", "")
        is_regex = pattern.get("is_regex", False)

        if pattern_type == "counterparty":
            field_value = transaction.counterparty_name or ""
        elif pattern_type == "reference":
            field_value = transaction.payment_reference or ""
        elif pattern_type == "description":
            field_value = transaction.description or ""
        else:
            return False

        if is_regex:
            try:
                return bool(re.search(pattern_value, field_value, re.IGNORECASE))
            except re.error:
                logger.warning(f"Invalid regex pattern: {pattern_value}")
                return False
        else:
            return pattern_value.lower() in field_value.lower()

    def _get_confidence(self, match_type: MatchType) -> Decimal:
        """Get confidence score for match type."""
        scores = {
            MatchType.EXACT_ALL: Decimal("1.00"),
            MatchType.EXACT_AMOUNT_REF: Decimal("0.95"),
            MatchType.EXACT_AMOUNT_DATE: Decimal("0.90"),
        }
        return scores.get(match_type, Decimal("0.90"))
