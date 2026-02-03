"""Confidence scoring for transaction matches."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class MatchType(str, Enum):
    """Types of matches."""

    EXACT_ALL = "exact_all"
    EXACT_AMOUNT_REF = "exact_amount_ref"
    EXACT_AMOUNT_DATE = "exact_amount_date"
    FUZZY_HIGH = "fuzzy_high"
    FUZZY_MEDIUM = "fuzzy_medium"
    LLM_CONFIDENT = "llm_confident"
    LLM_UNCERTAIN = "llm_uncertain"
    PATTERN = "pattern"
    UNMATCHED = "unmatched"


@dataclass
class MatchResult:
    """Result of a matching attempt."""

    match_type: MatchType
    confidence: Decimal
    reasons: list[str] = field(default_factory=list)
    netsuite_transaction_id: str | None = None
    netsuite_line_id: int | None = None
    netsuite_type: str | None = None
    suggested_account_id: int | None = None
    suggested_account_name: str | None = None
    is_intercompany: bool = False
    counterparty_entity: str | None = None
    explanation: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "match_type": self.match_type.value,
            "confidence": str(self.confidence),
            "reasons": self.reasons,
            "netsuite_transaction_id": self.netsuite_transaction_id,
            "netsuite_line_id": self.netsuite_line_id,
            "netsuite_type": self.netsuite_type,
            "suggested_account_id": self.suggested_account_id,
            "suggested_account_name": self.suggested_account_name,
            "is_intercompany": self.is_intercompany,
            "counterparty_entity": self.counterparty_entity,
            "explanation": self.explanation,
        }


class ConfidenceScorer:
    """Calculates and adjusts confidence scores."""

    # Base scores by match type
    BASE_SCORES: dict[MatchType, Decimal] = {
        MatchType.EXACT_ALL: Decimal("1.00"),
        MatchType.EXACT_AMOUNT_REF: Decimal("0.95"),
        MatchType.EXACT_AMOUNT_DATE: Decimal("0.90"),
        MatchType.FUZZY_HIGH: Decimal("0.85"),
        MatchType.FUZZY_MEDIUM: Decimal("0.75"),
        MatchType.LLM_CONFIDENT: Decimal("0.80"),
        MatchType.LLM_UNCERTAIN: Decimal("0.60"),
        MatchType.PATTERN: Decimal("0.85"),
        MatchType.UNMATCHED: Decimal("0.00"),
    }

    # Adjustment factors
    ADJUSTMENTS = {
        "is_intercompany": Decimal("0.05"),
        "pattern_match_low": Decimal("0.10"),
        "pattern_match_high": Decimal("0.25"),
        "repeat_counterparty": Decimal("0.05"),
        "fx_variance_high": Decimal("-0.15"),
        "date_drift_high": Decimal("-0.10"),
        "multiple_candidates": Decimal("-0.05"),
    }

    # Thresholds
    THRESHOLDS = {
        "auto_approve": Decimal("0.95"),
        "suggest": Decimal("0.80"),
        "review": Decimal("0.60"),
        "manual": Decimal("0.00"),
    }

    def get_base_score(self, match_type: MatchType) -> Decimal:
        """Get base confidence score for a match type."""
        return self.BASE_SCORES.get(match_type, Decimal("0.00"))

    def apply_adjustments(
        self,
        base_score: Decimal,
        *,
        is_intercompany: bool = False,
        pattern_confidence_boost: Decimal | None = None,
        is_repeat_counterparty: bool = False,
        fx_variance_percent: Decimal | None = None,
        date_drift_days: int = 0,
        candidate_count: int = 1,
    ) -> tuple[Decimal, list[str]]:
        """Apply adjustments to base score.

        Args:
            base_score: Starting confidence score
            is_intercompany: Is this an IC transfer
            pattern_confidence_boost: Boost from pattern match (0.10-0.25)
            is_repeat_counterparty: Have we seen this counterparty before
            fx_variance_percent: FX variance as percentage (e.g., 2.5 for 2.5%)
            date_drift_days: Days between transaction and GL entry
            candidate_count: Number of potential matches

        Returns:
            Tuple of (adjusted_score, list of adjustment reasons)
        """
        score = base_score
        reasons = []

        # Positive adjustments
        if is_intercompany:
            score += self.ADJUSTMENTS["is_intercompany"]
            reasons.append(f"+{self.ADJUSTMENTS['is_intercompany']} (intercompany)")

        if pattern_confidence_boost is not None:
            score += pattern_confidence_boost
            reasons.append(f"+{pattern_confidence_boost} (pattern match)")

        if is_repeat_counterparty:
            score += self.ADJUSTMENTS["repeat_counterparty"]
            reasons.append(f"+{self.ADJUSTMENTS['repeat_counterparty']} (repeat counterparty)")

        # Negative adjustments
        if fx_variance_percent is not None and fx_variance_percent > Decimal("2.0"):
            score += self.ADJUSTMENTS["fx_variance_high"]
            reasons.append(
                f"{self.ADJUSTMENTS['fx_variance_high']} (high FX variance: {fx_variance_percent}%)"
            )

        if date_drift_days > 5:
            score += self.ADJUSTMENTS["date_drift_high"]
            reasons.append(
                f"{self.ADJUSTMENTS['date_drift_high']} (date drift: {date_drift_days} days)"
            )

        if candidate_count > 1:
            penalty = self.ADJUSTMENTS["multiple_candidates"] * (candidate_count - 1)
            score += penalty
            reasons.append(f"{penalty} ({candidate_count} candidates)")

        # Clamp to 0-1 range
        score = max(Decimal("0.00"), min(Decimal("1.00"), score))

        return score, reasons

    def get_action(self, confidence: Decimal) -> str:
        """Determine action based on confidence score.

        Returns:
            One of: 'auto_approve', 'suggest', 'review', 'manual'
        """
        if confidence >= self.THRESHOLDS["auto_approve"]:
            return "auto_approve"
        elif confidence >= self.THRESHOLDS["suggest"]:
            return "suggest"
        elif confidence >= self.THRESHOLDS["review"]:
            return "review"
        else:
            return "manual"

    def calculate_final_score(
        self,
        match_type: MatchType,
        **adjustment_kwargs,
    ) -> tuple[Decimal, list[str], str]:
        """Calculate final confidence score with adjustments.

        Args:
            match_type: Type of match
            **adjustment_kwargs: Keyword arguments for apply_adjustments

        Returns:
            Tuple of (final_score, all_reasons, recommended_action)
        """
        base_score = self.get_base_score(match_type)
        reasons = [f"Base: {base_score} ({match_type.value})"]

        final_score, adjustment_reasons = self.apply_adjustments(base_score, **adjustment_kwargs)
        reasons.extend(adjustment_reasons)

        action = self.get_action(final_score)
        reasons.append(f"Final: {final_score} -> {action}")

        return final_score, reasons, action
