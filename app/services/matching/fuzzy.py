"""Fuzzy matching for transactions."""

import logging
import re
from decimal import Decimal

from app.models.recon import WiseTransaction
from app.services.spectre import GLEntry

from .confidence import MatchResult, MatchType

logger = logging.getLogger(__name__)


class FuzzyMatcher:
    """Fuzzy matching engine for bank transactions.

    Tier 2 matching with confidence 0.70-0.94:
    - Amount within tolerance (same currency: ±0.01, cross-currency: ±2%)
    - Date within 5 days
    - Counterparty name similarity > 85% OR
    - Payment reference partial match OR
    - Amount + entity match with no conflicts
    """

    # Amount tolerances
    SAME_CURRENCY_TOLERANCE = Decimal("0.01")
    CROSS_CURRENCY_TOLERANCE_PERCENT = Decimal("2.0")

    # Maximum date difference
    MAX_DATE_DIFF_DAYS = 5

    # Name similarity threshold
    NAME_SIMILARITY_THRESHOLD = 0.85

    def match(
        self,
        transaction: WiseTransaction,
        gl_entries: list[GLEntry],
    ) -> MatchResult | None:
        """Attempt fuzzy match for a transaction.

        Args:
            transaction: Wise transaction to match
            gl_entries: List of GL entries to match against

        Returns:
            MatchResult if fuzzy match found, None otherwise
        """
        best_match: MatchResult | None = None
        best_score = Decimal("0.00")

        for entry in gl_entries:
            result = self._try_match(transaction, entry)
            if result is not None and result.confidence > best_score:
                best_match = result
                best_score = result.confidence

        return best_match

    def _try_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
    ) -> MatchResult | None:
        """Try to fuzzy match a transaction against a GL entry."""
        reasons = []

        # Check amount match (within tolerance)
        is_cross_currency = transaction.from_currency is not None
        amount_match, amount_reason = self._check_amount_match(
            transaction, entry, is_cross_currency
        )
        if not amount_match:
            return None
        reasons.append(amount_reason)

        # Check date match (within 5 days)
        date_match, date_reason = self._check_date_match(transaction, entry)
        if not date_match:
            return None
        reasons.append(date_reason)

        # Now check for fuzzy match criteria
        match_type = MatchType.FUZZY_MEDIUM  # Default to medium

        # Check counterparty name similarity
        name_sim = self._calculate_name_similarity(
            transaction.counterparty_name or "", entry.memo or ""
        )
        if name_sim >= self.NAME_SIMILARITY_THRESHOLD:
            reasons.append(f"name_similarity_{int(name_sim * 100)}%")
            match_type = MatchType.FUZZY_HIGH

        # Check payment reference partial match
        elif self._check_reference_partial_match(transaction, entry):
            reasons.append("reference_partial_match")
            match_type = MatchType.FUZZY_HIGH

        # Amount + entity match (already have amount, just add entity context)
        else:
            reasons.append("amount_entity_match")

        return MatchResult(
            match_type=match_type,
            confidence=self._get_confidence(match_type, is_cross_currency),
            reasons=reasons,
            netsuite_transaction_id=entry.transaction_id,
            netsuite_line_id=entry.line_id,
            netsuite_type=entry.transaction_type,
            suggested_account_id=entry.account_id,
            suggested_account_name=entry.account_name,
        )

    def _check_amount_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
        is_cross_currency: bool,
    ) -> tuple[bool, str]:
        """Check if amounts match within fuzzy tolerance."""
        tx_amount = abs(transaction.amount)
        gl_amount = abs(entry.amount)

        if is_cross_currency:
            # Use percentage tolerance for cross-currency
            if gl_amount == 0:
                return False, ""
            variance_percent = abs((tx_amount - gl_amount) / gl_amount * 100)
            if variance_percent <= self.CROSS_CURRENCY_TOLERANCE_PERCENT:
                return True, f"amount_within_{variance_percent:.1f}%"
            return False, ""
        else:
            # Use absolute tolerance for same currency
            diff = abs(tx_amount - gl_amount)
            if diff <= self.SAME_CURRENCY_TOLERANCE:
                return True, "amount_exact"
            return False, ""

    def _check_date_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
    ) -> tuple[bool, str]:
        """Check if dates are within fuzzy range."""
        tx_date = transaction.date.date()
        gl_date = entry.date.date()

        diff = abs((tx_date - gl_date).days)
        if diff <= self.MAX_DATE_DIFF_DAYS:
            return True, f"date_within_{diff}_days"
        return False, ""

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two names using Jaccard index on tokens."""
        if not name1 or not name2:
            return 0.0

        # Normalize: lowercase, remove punctuation, split into tokens
        def tokenize(s: str) -> set[str]:
            s = s.lower()
            s = re.sub(r"[^\w\s]", " ", s)
            tokens = s.split()
            # Filter short tokens
            return {t for t in tokens if len(t) > 1}

        tokens1 = tokenize(name1)
        tokens2 = tokenize(name2)

        if not tokens1 or not tokens2:
            return 0.0

        intersection = tokens1 & tokens2
        union = tokens1 | tokens2

        return len(intersection) / len(union)

    def _check_reference_partial_match(
        self,
        transaction: WiseTransaction,
        entry: GLEntry,
    ) -> bool:
        """Check for partial match in payment reference."""
        if not transaction.payment_reference:
            return False

        ref = transaction.payment_reference.upper()
        memo = (entry.memo or "").upper()

        # Check for common patterns
        # Extract potential invoice/reference numbers
        ref_patterns = re.findall(r"[A-Z]{2,4}[-_]?\d{4,}", ref)
        memo_patterns = re.findall(r"[A-Z]{2,4}[-_]?\d{4,}", memo)

        if ref_patterns and memo_patterns:
            # Normalize patterns (remove separators)
            ref_normalized = {re.sub(r"[-_]", "", p) for p in ref_patterns}
            memo_normalized = {re.sub(r"[-_]", "", p) for p in memo_patterns}
            if ref_normalized & memo_normalized:
                return True

        # Check for numeric overlap (at least 4 consecutive digits)
        ref_numbers = set(re.findall(r"\d{4,}", ref))
        memo_numbers = set(re.findall(r"\d{4,}", memo))

        return bool(ref_numbers & memo_numbers)

    def _get_confidence(self, match_type: MatchType, is_cross_currency: bool) -> Decimal:
        """Get confidence score for match type."""
        scores = {
            MatchType.FUZZY_HIGH: Decimal("0.85"),
            MatchType.FUZZY_MEDIUM: Decimal("0.75"),
        }
        base = scores.get(match_type, Decimal("0.75"))

        # Reduce confidence for cross-currency matches
        if is_cross_currency:
            base -= Decimal("0.05")

        return base
