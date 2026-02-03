"""Tests for matching engine."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.recon import WiseTransaction
from app.services.matching import (
    ConfidenceScorer,
    ExactMatcher,
    FuzzyMatcher,
    IntercompanyDetector,
    MatchType,
)
from app.services.spectre import GLEntry


@pytest.fixture
def sample_transaction():
    """Create a sample transaction."""
    return WiseTransaction(
        id="TRANSFER-123",
        profile_id=19941830,
        entity_name="Phygrid Limited",
        type="DEBIT",
        transaction_type="TRANSFER",
        date=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        amount=Decimal("-1000.00"),
        currency="EUR",
        description="Payment to supplier",
        payment_reference="INV-2026-001",
        counterparty_name="Test Supplier Ltd",
        counterparty_account="DE89370400440532013000",
    )


@pytest.fixture
def sample_gl_entry():
    """Create a sample GL entry."""
    return GLEntry(
        transaction_id="JE-12345",
        line_id=1,
        transaction_type="journalentry",
        date=datetime(2026, 1, 15, tzinfo=UTC),
        amount=Decimal("-1000.00"),
        currency="EUR",
        account_id=1000,
        account_name="Bank Account EUR",
        entity_id=1,
        entity_name="Phygrid Limited",
        memo="INV-2026-001 Payment",
    )


class TestExactMatcher:
    """Tests for ExactMatcher."""

    def test_exact_match_amount_and_reference(self, sample_transaction, sample_gl_entry):
        """Test exact match with amount and reference."""
        matcher = ExactMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is not None
        assert result.match_type in (MatchType.EXACT_ALL, MatchType.EXACT_AMOUNT_REF)
        assert result.confidence >= Decimal("0.90")
        assert "amount_exact" in result.reasons
        assert result.netsuite_transaction_id == "JE-12345"

    def test_no_match_different_amount(self, sample_transaction, sample_gl_entry):
        """Test no match when amounts differ."""
        sample_gl_entry.amount = Decimal("-2000.00")
        matcher = ExactMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is None

    def test_no_match_date_too_far(self, sample_transaction, sample_gl_entry):
        """Test no match when dates are too far apart."""
        sample_gl_entry.date = datetime(2026, 1, 20, tzinfo=UTC)  # 5 days later
        matcher = ExactMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is None

    def test_match_with_known_iban(self, sample_transaction, sample_gl_entry):
        """Test match with known entity IBAN."""
        # Clear payment reference so IBAN match is used instead of reference match
        sample_transaction.payment_reference = None
        sample_gl_entry.memo = None
        known_ibans = {"DE89370400440532013000": "Test Entity"}
        matcher = ExactMatcher(known_entity_ibans=known_ibans)
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is not None
        assert "counterparty_iban_known" in result.reasons


class TestFuzzyMatcher:
    """Tests for FuzzyMatcher."""

    def test_fuzzy_match_with_date_drift(self, sample_transaction, sample_gl_entry):
        """Test fuzzy match with acceptable date drift."""
        sample_gl_entry.date = datetime(2026, 1, 18, tzinfo=UTC)  # 3 days later
        matcher = FuzzyMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is not None
        assert result.match_type in (MatchType.FUZZY_HIGH, MatchType.FUZZY_MEDIUM)
        assert "date_within_3_days" in result.reasons

    def test_fuzzy_match_cross_currency(self, sample_transaction, sample_gl_entry):
        """Test fuzzy match with cross-currency tolerance."""
        sample_transaction.from_amount = Decimal("1100.00")
        sample_transaction.from_currency = "USD"
        sample_gl_entry.amount = Decimal("-990.00")  # Within 2% of 1000

        matcher = FuzzyMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is not None
        assert result.confidence < Decimal("0.85")  # Reduced for cross-currency

    def test_no_fuzzy_match_date_too_far(self, sample_transaction, sample_gl_entry):
        """Test no match when dates exceed fuzzy limit."""
        sample_gl_entry.date = datetime(2026, 1, 25, tzinfo=UTC)  # 10 days later
        matcher = FuzzyMatcher()
        result = matcher.match(sample_transaction, [sample_gl_entry])

        assert result is None


class TestIntercompanyDetector:
    """Tests for IntercompanyDetector."""

    def test_detect_ic_by_counterparty_name(self):
        """Test IC detection by counterparty name."""
        transaction = WiseTransaction(
            id="IC-001",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime.now(UTC),
            amount=Decimal("-5000.00"),
            currency="EUR",
            counterparty_name="Ombori AG",
        )

        detector = IntercompanyDetector()
        result = detector.detect(transaction)

        assert result.is_intercompany is True
        assert result.counterparty_entity == "Ombori AG"
        assert result.counterparty_profile_id == 47253364

    def test_detect_ic_by_payment_reference(self):
        """Test IC detection by payment reference."""
        transaction = WiseTransaction(
            id="IC-002",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime.now(UTC),
            amount=Decimal("-5000.00"),
            currency="EUR",
            counterparty_name="Unknown Company",
            payment_reference="IC Transfer to Fendops Limited",
        )

        detector = IntercompanyDetector()
        result = detector.detect(transaction)

        assert result.is_intercompany is True
        assert "Fendops" in (result.counterparty_entity or "")

    def test_not_ic_external_counterparty(self):
        """Test that external counterparties are not flagged as IC."""
        transaction = WiseTransaction(
            id="EXT-001",
            profile_id=19941830,
            entity_name="Phygrid Limited",
            type="DEBIT",
            transaction_type="TRANSFER",
            date=datetime.now(UTC),
            amount=Decimal("-1000.00"),
            currency="EUR",
            counterparty_name="Amazon Web Services",
            payment_reference="AWS Invoice 12345",
        )

        detector = IntercompanyDetector()
        result = detector.detect(transaction)

        assert result.is_intercompany is False


class TestConfidenceScorer:
    """Tests for ConfidenceScorer."""

    def test_base_scores(self):
        """Test base confidence scores."""
        scorer = ConfidenceScorer()

        assert scorer.get_base_score(MatchType.EXACT_ALL) == Decimal("1.00")
        assert scorer.get_base_score(MatchType.FUZZY_HIGH) == Decimal("0.85")
        assert scorer.get_base_score(MatchType.LLM_UNCERTAIN) == Decimal("0.60")

    def test_apply_ic_adjustment(self):
        """Test intercompany confidence boost."""
        scorer = ConfidenceScorer()
        base = Decimal("0.85")

        adjusted, reasons = scorer.apply_adjustments(base, is_intercompany=True)

        assert adjusted == Decimal("0.90")
        assert any("intercompany" in r for r in reasons)

    def test_apply_negative_adjustments(self):
        """Test negative confidence adjustments."""
        scorer = ConfidenceScorer()
        base = Decimal("0.85")

        adjusted, reasons = scorer.apply_adjustments(
            base,
            fx_variance_percent=Decimal("5.0"),  # High variance
            date_drift_days=7,  # High drift
        )

        assert adjusted < base
        assert any("FX variance" in r for r in reasons)
        assert any("date drift" in r for r in reasons)

    def test_get_action_thresholds(self):
        """Test action determination by threshold."""
        scorer = ConfidenceScorer()

        assert scorer.get_action(Decimal("0.98")) == "auto_approve"
        assert scorer.get_action(Decimal("0.85")) == "suggest"
        assert scorer.get_action(Decimal("0.65")) == "review"
        assert scorer.get_action(Decimal("0.30")) == "manual"

    def test_calculate_final_score(self):
        """Test full score calculation."""
        scorer = ConfidenceScorer()

        score, reasons, action = scorer.calculate_final_score(
            MatchType.FUZZY_HIGH,
            is_intercompany=True,
            pattern_confidence_boost=Decimal("0.10"),
        )

        assert score == Decimal("1.00")  # 0.85 + 0.05 + 0.10, clamped to 1.0
        assert action == "auto_approve"
        assert len(reasons) >= 3
