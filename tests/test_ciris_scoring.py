"""
Tests for CIRIS Scoring Module

Tests the factor calculations and composite scoring logic.
"""

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import from api package
import sys
sys.path.insert(0, "api")

from ciris_scoring import (
    NON_EXEMPT_ACTIONS,
    EXEMPT_ACTIONS,
    PARAMS,
    FactorScore,
    CIRISScore,
    is_non_exempt_action,
    sigmoid,
    get_confidence_level,
    get_category,
    calculate_factor_C,
    calculate_factor_I_int,
    calculate_factor_R,
    calculate_factor_I_inc,
    calculate_factor_S,
    calculate_ciris_score,
)


# ============================================================================
# Unit Tests - Helper Functions
# ============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_non_exempt_action_speak(self):
        """SPEAK is a non-exempt action."""
        assert is_non_exempt_action("SPEAK") is True
        assert is_non_exempt_action("HandlerActionType.SPEAK") is True

    def test_is_non_exempt_action_tool(self):
        """TOOL is a non-exempt action."""
        assert is_non_exempt_action("TOOL") is True
        assert is_non_exempt_action("HandlerActionType.TOOL") is True

    def test_is_non_exempt_action_memorize(self):
        """MEMORIZE is a non-exempt action."""
        assert is_non_exempt_action("MEMORIZE") is True

    def test_is_non_exempt_action_forget(self):
        """FORGET is a non-exempt action."""
        assert is_non_exempt_action("FORGET") is True

    def test_is_non_exempt_action_task_complete(self):
        """TASK_COMPLETE is an exempt action."""
        assert is_non_exempt_action("TASK_COMPLETE") is False
        assert is_non_exempt_action("HandlerActionType.TASK_COMPLETE") is False

    def test_is_non_exempt_action_recall(self):
        """RECALL is an exempt action."""
        assert is_non_exempt_action("RECALL") is False

    def test_is_non_exempt_action_observe(self):
        """OBSERVE is an exempt action."""
        assert is_non_exempt_action("OBSERVE") is False

    def test_is_non_exempt_action_defer(self):
        """DEFER is an exempt action."""
        assert is_non_exempt_action("DEFER") is False

    def test_is_non_exempt_action_none(self):
        """None returns False."""
        assert is_non_exempt_action(None) is False

    def test_is_non_exempt_action_empty(self):
        """Empty string returns False."""
        assert is_non_exempt_action("") is False

    def test_sigmoid_at_midpoint(self):
        """Sigmoid at midpoint should be 0.5."""
        assert abs(sigmoid(0.5, k=5.0, x0=0.5) - 0.5) < 0.01

    def test_sigmoid_high_value(self):
        """Sigmoid at high value should approach 1."""
        assert sigmoid(1.0, k=5.0, x0=0.5) > 0.9

    def test_sigmoid_low_value(self):
        """Sigmoid at low value should approach 0."""
        assert sigmoid(0.0, k=5.0, x0=0.5) < 0.1

    def test_get_confidence_level_insufficient(self):
        """Less than 10 traces is insufficient."""
        assert get_confidence_level(5) == "insufficient"
        assert get_confidence_level(9) == "insufficient"

    def test_get_confidence_level_low(self):
        """10-29 traces is low confidence."""
        assert get_confidence_level(10) == "low"
        assert get_confidence_level(29) == "low"

    def test_get_confidence_level_medium(self):
        """30-99 traces is medium confidence."""
        assert get_confidence_level(30) == "medium"
        assert get_confidence_level(99) == "medium"

    def test_get_confidence_level_high(self):
        """100+ traces is high confidence."""
        assert get_confidence_level(100) == "high"
        assert get_confidence_level(1000) == "high"

    def test_get_category_high_fragility(self):
        """Score < 0.3 is high fragility."""
        assert get_category(0.0) == "high_fragility"
        assert get_category(0.29) == "high_fragility"

    def test_get_category_moderate(self):
        """Score 0.3-0.6 is moderate."""
        assert get_category(0.3) == "moderate"
        assert get_category(0.59) == "moderate"

    def test_get_category_healthy(self):
        """Score 0.6-0.85 is healthy."""
        assert get_category(0.6) == "healthy"
        assert get_category(0.84) == "healthy"

    def test_get_category_high_capacity(self):
        """Score >= 0.85 is high capacity."""
        assert get_category(0.85) == "high_capacity"
        assert get_category(1.0) == "high_capacity"


# ============================================================================
# Unit Tests - Data Classes
# ============================================================================

class TestDataClasses:
    """Tests for data classes."""

    def test_factor_score_creation(self):
        """FactorScore can be created with minimal args."""
        score = FactorScore(name="C", score=0.95)
        assert score.name == "C"
        assert score.score == 0.95
        assert score.components == {}
        assert score.trace_count == 0
        assert score.confidence == "high"

    def test_factor_score_with_components(self):
        """FactorScore can include components."""
        score = FactorScore(
            name="I_int",
            score=0.85,
            components={"I_chain": 0.9, "I_coverage": 0.95},
            trace_count=100,
            confidence="high",
        )
        assert score.components["I_chain"] == 0.9
        assert score.trace_count == 100

    def test_ciris_score_to_dict(self):
        """CIRISScore.to_dict() returns serializable dict."""
        now = datetime.now(UTC)
        score = CIRISScore(
            agent_name="TestAgent",
            composite_score=0.75,
            fragility_index=1.33,
            C=FactorScore(name="C", score=0.95),
            I_int=FactorScore(name="I_int", score=0.90),
            R=FactorScore(name="R", score=0.85),
            I_inc=FactorScore(name="I_inc", score=0.88),
            S=FactorScore(name="S", score=0.92),
            window_start=now - timedelta(days=7),
            window_end=now,
            total_traces=150,
            non_exempt_traces=50,
            category="healthy",
        )

        result = score.to_dict()

        assert result["agent_name"] == "TestAgent"
        assert result["composite_score"] == 0.75
        assert result["category"] == "healthy"
        assert "factors" in result
        assert "C" in result["factors"]
        assert result["factors"]["C"]["score"] == 0.95
        assert result["metadata"]["total_traces"] == 150


# ============================================================================
# Integration Tests - Factor Calculations (with mock DB)
# ============================================================================

class TestFactorCalculations:
    """Tests for factor calculation functions."""

    @pytest.fixture
    def mock_conn(self):
        """Create a mock database connection."""
        conn = AsyncMock()
        return conn

    @pytest.fixture
    def window_times(self):
        """Create standard window times."""
        now = datetime.now(UTC)
        return now - timedelta(days=7), now

    @pytest.mark.asyncio
    async def test_calculate_factor_C_perfect(self, mock_conn, window_times):
        """Factor C with no overrides should be 1.0."""
        mock_conn.fetchrow.return_value = {
            "total_traces": 100,
            "override_count": 0,
            "distinct_names": 1,
        }

        result = await calculate_factor_C(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "C"
        assert result.score == 1.0
        assert result.components["K_contradiction"] == 0.0
        assert result.trace_count == 100

    @pytest.mark.asyncio
    async def test_calculate_factor_C_with_overrides(self, mock_conn, window_times):
        """Factor C decreases with overrides."""
        mock_conn.fetchrow.return_value = {
            "total_traces": 100,
            "override_count": 10,  # 10% override rate
            "distinct_names": 1,
        }

        result = await calculate_factor_C(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "C"
        assert result.score < 1.0  # Should decrease
        assert result.components["K_contradiction"] == 0.1

    @pytest.mark.asyncio
    async def test_calculate_factor_I_int_perfect(self, mock_conn, window_times):
        """Factor I_int with all verified signatures and full coverage."""
        mock_conn.fetchrow.return_value = {
            "total_traces": 100,
            "verified_count": 100,
            "signed_count": 100,
            "avg_coverage": 1.0,
        }

        result = await calculate_factor_I_int(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "I_int"
        assert result.score == 1.0
        assert result.components["I_chain"] == 1.0
        assert result.components["I_coverage"] == 1.0

    @pytest.mark.asyncio
    async def test_calculate_factor_I_int_partial(self, mock_conn, window_times):
        """Factor I_int with partial verification."""
        mock_conn.fetchrow.return_value = {
            "total_traces": 100,
            "verified_count": 80,  # 80% verified
            "signed_count": 90,
            "avg_coverage": 0.9,  # 90% coverage
        }

        result = await calculate_factor_I_int(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.score == 0.8 * 0.9 * 1.0  # I_chain * I_coverage * I_replay
        assert result.components["I_chain"] == 0.8

    @pytest.mark.asyncio
    async def test_calculate_factor_R_stable(self, mock_conn, window_times):
        """Factor R with stable scores."""
        # First call: baseline stats
        mock_conn.fetchrow.side_effect = [
            {  # baseline
                "baseline_csdma": 0.9,
                "std_csdma": 0.1,
                "baseline_coherence": 0.9,
                "std_coherence": 0.1,
            },
            {  # recent
                "total_traces": 100,
                "recent_csdma": 0.9,  # No drift
                "recent_coherence": 0.9,
                "fragility_count": 0,
            },
        ]

        result = await calculate_factor_R(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "R"
        assert result.score > 0.5  # Should be reasonably high

    @pytest.mark.asyncio
    async def test_calculate_factor_I_inc_calibrated(self, mock_conn, window_times):
        """Factor I_inc with good calibration."""
        mock_conn.fetchrow.side_effect = [
            {  # ECE calculation
                "ece": 0.05,  # 5% calibration error
                "total_traces": 100,
            },
            {  # Unsafe actions
                "total": 100,
                "unsafe_failures": 0,
            },
        ]

        result = await calculate_factor_I_inc(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "I_inc"
        assert result.components["ECE"] == 0.05
        assert result.components["calibration"] == 0.95
        assert result.components["U_unsafe"] == 0.0

    @pytest.mark.asyncio
    async def test_calculate_factor_S_with_positive_moments(self, mock_conn, window_times):
        """Factor S includes positive moment boost."""
        mock_conn.fetchrow.side_effect = [
            {  # Coherence signals
                "total_traces": 100,
                "decayed_coherence": 0.8,
                "raw_coherence_rate": 0.85,
            },
            {  # Enhancement signals
                "total": 50,
                "positive_moments": 10,  # 20% positive rate
                "full_faculty_passes": 40,  # 80% pass rate
                "faculty_evaluated": 50,
            },
        ]

        result = await calculate_factor_S(
            mock_conn, "TestAgent", window_times[0], window_times[1]
        )

        assert result.name == "S"
        assert result.components["P_positive_moment"] == 0.2
        assert result.components["P_ethical_faculties"] == 0.8
        # Should have boosts applied
        assert result.components["positive_boost"] > 1.0
        assert result.components["faculty_boost"] > 1.0


# ============================================================================
# Integration Tests - Full Scoring
# ============================================================================

class TestFullScoring:
    """Tests for complete scoring calculation."""

    @pytest.fixture
    def mock_conn(self):
        """Create a mock database connection."""
        conn = AsyncMock()
        return conn

    @pytest.mark.asyncio
    async def test_calculate_ciris_score_basic(self, mock_conn):
        """Calculate complete CIRIS score."""
        # Setup mock returns for all queries
        mock_conn.fetchrow.side_effect = [
            # Count query
            {"total": 100, "non_exempt": 50},
            # Factor C
            {"total_traces": 50, "override_count": 0, "distinct_names": 1},
            # Factor I_int
            {"total_traces": 100, "verified_count": 100, "signed_count": 100, "avg_coverage": 1.0},
            # Factor R - baseline
            {"baseline_csdma": 0.9, "std_csdma": 0.1, "baseline_coherence": 0.9, "std_coherence": 0.1},
            # Factor R - recent
            {"total_traces": 50, "recent_csdma": 0.9, "recent_coherence": 0.9, "fragility_count": 0},
            # Factor I_inc - ECE
            {"ece": 0.05, "total_traces": 50},
            # Factor I_inc - unsafe
            {"total": 50, "unsafe_failures": 0},
            # Factor S - coherence
            {"total_traces": 50, "decayed_coherence": 0.85, "raw_coherence_rate": 0.9},
            # Factor S - enhancement
            {"total": 50, "positive_moments": 5, "full_faculty_passes": 45, "faculty_evaluated": 50},
        ]

        result = await calculate_ciris_score(mock_conn, "TestAgent", 7)

        assert result.agent_name == "TestAgent"
        assert result.total_traces == 100
        assert result.non_exempt_traces == 50
        assert result.composite_score > 0
        assert result.composite_score <= 1.0
        assert result.category in ["high_fragility", "moderate", "healthy", "high_capacity"]

    @pytest.mark.asyncio
    async def test_calculate_ciris_score_no_traces(self, mock_conn):
        """Score with no traces returns zero counts."""
        mock_conn.fetchrow.side_effect = [
            # Count query
            {"total": 0, "non_exempt": 0},
            # Factor C
            {"total_traces": 0, "override_count": 0, "distinct_names": 0},
            # Factor I_int
            {"total_traces": 0, "verified_count": 0, "signed_count": 0, "avg_coverage": None},
            # Factor R - baseline
            {"baseline_csdma": None, "std_csdma": None, "baseline_coherence": None, "std_coherence": None},
            # Factor R - recent
            {"total_traces": 0, "recent_csdma": None, "recent_coherence": None, "fragility_count": 0},
            # Factor I_inc - ECE
            {"ece": None, "total_traces": 0},
            # Factor I_inc - unsafe
            {"total": 0, "unsafe_failures": 0},
            # Factor S - coherence
            {"total_traces": 0, "decayed_coherence": None, "raw_coherence_rate": None},
            # Factor S - enhancement
            {"total": 0, "positive_moments": 0, "full_faculty_passes": 0, "faculty_evaluated": 0},
        ]

        result = await calculate_ciris_score(mock_conn, "TestAgent", 7)

        assert result.total_traces == 0
        assert result.non_exempt_traces == 0


# ============================================================================
# Constants Tests
# ============================================================================

class TestConstants:
    """Tests for module constants."""

    def test_non_exempt_actions_contains_speak(self):
        """NON_EXEMPT_ACTIONS contains SPEAK."""
        assert "SPEAK" in NON_EXEMPT_ACTIONS
        assert "HandlerActionType.SPEAK" in NON_EXEMPT_ACTIONS

    def test_non_exempt_actions_contains_tool(self):
        """NON_EXEMPT_ACTIONS contains TOOL."""
        assert "TOOL" in NON_EXEMPT_ACTIONS

    def test_non_exempt_actions_contains_memorize(self):
        """NON_EXEMPT_ACTIONS contains MEMORIZE."""
        assert "MEMORIZE" in NON_EXEMPT_ACTIONS

    def test_non_exempt_actions_contains_forget(self):
        """NON_EXEMPT_ACTIONS contains FORGET."""
        assert "FORGET" in NON_EXEMPT_ACTIONS

    def test_exempt_actions_contains_task_complete(self):
        """EXEMPT_ACTIONS contains TASK_COMPLETE."""
        assert "TASK_COMPLETE" in EXEMPT_ACTIONS

    def test_exempt_actions_contains_recall(self):
        """EXEMPT_ACTIONS contains RECALL."""
        assert "RECALL" in EXEMPT_ACTIONS

    def test_params_has_required_keys(self):
        """PARAMS contains all required scoring parameters."""
        required = [
            "lambda_C", "mu_C",
            "decay_rate", "signal_weight",
            "positive_moment_weight", "ethical_faculty_weight",
            "sigmoid_k", "sigmoid_x0",
            "min_traces", "default_window_days",
        ]
        for key in required:
            assert key in PARAMS, f"Missing parameter: {key}"
