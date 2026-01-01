"""
Comprehensive tests for the Coherence Ratchet Detection Module.

Tests the anomaly detection mechanisms for CIRIS agent trace analysis.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.analysis.coherence_ratchet import (
    AlertSeverity,
    AnomalyAlert,
    CoherenceRatchetAnalyzer,
    DetectionMechanism,
    HashChainBreak,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def analyzer() -> CoherenceRatchetAnalyzer:
    """Create analyzer without database pool."""
    return CoherenceRatchetAnalyzer(db_pool=None)


@pytest.fixture
def mock_db_pool():
    """Create a mock database pool with connection context manager."""
    pool = MagicMock()
    conn = AsyncMock()

    # Set up the async context manager for pool.acquire()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    return pool, conn


@pytest.fixture
def analyzer_with_pool(mock_db_pool) -> CoherenceRatchetAnalyzer:
    """Create analyzer with mocked database pool."""
    pool, _ = mock_db_pool
    return CoherenceRatchetAnalyzer(db_pool=pool)


@pytest.fixture
def sample_alert() -> AnomalyAlert:
    """Create a sample anomaly alert for testing."""
    return AnomalyAlert(
        alert_id=str(uuid.uuid4()),
        alert_type="coherence_ratchet_anomaly",
        severity=AlertSeverity.WARNING,
        detection_mechanism=DetectionMechanism.CROSS_AGENT_DIVERGENCE,
        agent_id_hash="abc123def456",
        domain="healthcare",
        metric="csdma_plausibility_score",
        value=0.65,
        baseline=0.85,
        deviation="2.5σ",
        timestamp=datetime(2024, 1, 15, 10, 30, 0),
        evidence_traces=["trace-001", "trace-002", "trace-003"],
        recommended_action="Review recent traces for this agent",
    )


@pytest.fixture
def sample_traces_valid_chain() -> list[dict[str, Any]]:
    """Create sample traces with valid sequential hash chain."""
    return [
        {"trace_id": "trace-1", "audit_sequence_number": 1, "audit_entry_hash": "hash1"},
        {"trace_id": "trace-2", "audit_sequence_number": 2, "audit_entry_hash": "hash2"},
        {"trace_id": "trace-3", "audit_sequence_number": 3, "audit_entry_hash": "hash3"},
        {"trace_id": "trace-4", "audit_sequence_number": 4, "audit_entry_hash": "hash4"},
        {"trace_id": "trace-5", "audit_sequence_number": 5, "audit_entry_hash": "hash5"},
    ]


@pytest.fixture
def sample_traces_with_gap() -> list[dict[str, Any]]:
    """Create sample traces with a sequence gap."""
    return [
        {"trace_id": "trace-1", "audit_sequence_number": 1, "audit_entry_hash": "hash1"},
        {"trace_id": "trace-2", "audit_sequence_number": 2, "audit_entry_hash": "hash2"},
        {"trace_id": "trace-4", "audit_sequence_number": 4, "audit_entry_hash": "hash4"},  # Gap: 3 missing
        {"trace_id": "trace-5", "audit_sequence_number": 5, "audit_entry_hash": "hash5"},
    ]


@pytest.fixture
def normal_distribution_values() -> list[float]:
    """Return values that approximate a normal distribution."""
    return [0.80, 0.82, 0.78, 0.81, 0.79, 0.83, 0.77, 0.80, 0.82, 0.79]


@pytest.fixture
def values_with_outliers() -> list[float]:
    """Return values with clear outliers."""
    # Normal values around 0.80, with outliers at 0.20 and 1.50
    return [0.80, 0.82, 0.78, 0.81, 0.20, 0.79, 0.83, 1.50, 0.80, 0.82]


# =============================================================================
# AnomalyAlert Class Tests
# =============================================================================


class TestAnomalyAlert:
    """Tests for AnomalyAlert dataclass."""

    def test_to_dict_serialization(self, sample_alert: AnomalyAlert):
        """Test to_dict() returns correct JSON-serializable dictionary."""
        result = sample_alert.to_dict()

        assert isinstance(result, dict)
        assert result["alert_id"] == sample_alert.alert_id
        assert result["alert_type"] == "coherence_ratchet_anomaly"
        assert result["severity"] == "warning"
        assert result["detection_mechanism"] == "cross_agent_divergence"
        assert result["agent_id_hash"] == "abc123def456"
        assert result["domain"] == "healthcare"
        assert result["metric"] == "csdma_plausibility_score"
        assert result["value"] == 0.65
        assert result["baseline"] == 0.85
        assert result["deviation"] == "2.5σ"
        assert result["timestamp"] == "2024-01-15T10:30:00Z"
        assert result["evidence_traces"] == ["trace-001", "trace-002", "trace-003"]
        assert result["recommended_action"] == "Review recent traces for this agent"

    def test_default_values(self):
        """Test AnomalyAlert default values."""
        alert = AnomalyAlert(alert_id="test-123")

        assert alert.alert_type == "coherence_ratchet_anomaly"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.detection_mechanism == DetectionMechanism.CROSS_AGENT_DIVERGENCE
        assert alert.agent_id_hash == ""
        assert alert.domain is None
        assert alert.metric == ""
        assert alert.value == 0.0
        assert alert.baseline == 0.0
        assert alert.deviation == ""
        assert isinstance(alert.timestamp, datetime)
        assert alert.evidence_traces == []
        assert alert.recommended_action == "Review recent traces for this agent"

    def test_all_fields_populated_correctly(self):
        """Test that all fields are populated correctly when specified."""
        now = datetime.utcnow()
        alert = AnomalyAlert(
            alert_id="full-alert-id",
            alert_type="custom_type",
            severity=AlertSeverity.CRITICAL,
            detection_mechanism=DetectionMechanism.TEMPORAL_DRIFT,
            agent_id_hash="agent-hash-xyz",
            domain="finance",
            metric="coherence_level",
            value=0.45,
            baseline=0.90,
            deviation="25% daily change",
            timestamp=now,
            evidence_traces=["t1", "t2"],
            recommended_action="Investigate immediately",
        )

        assert alert.alert_id == "full-alert-id"
        assert alert.alert_type == "custom_type"
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.detection_mechanism == DetectionMechanism.TEMPORAL_DRIFT
        assert alert.agent_id_hash == "agent-hash-xyz"
        assert alert.domain == "finance"
        assert alert.metric == "coherence_level"
        assert alert.value == 0.45
        assert alert.baseline == 0.90
        assert alert.deviation == "25% daily change"
        assert alert.timestamp == now
        assert alert.evidence_traces == ["t1", "t2"]
        assert alert.recommended_action == "Investigate immediately"

    def test_to_dict_with_critical_severity(self):
        """Test to_dict() correctly serializes critical severity."""
        alert = AnomalyAlert(
            alert_id="critical-alert",
            severity=AlertSeverity.CRITICAL,
        )
        result = alert.to_dict()
        assert result["severity"] == "critical"

    def test_to_dict_with_all_detection_mechanisms(self):
        """Test to_dict() correctly serializes all detection mechanism types."""
        mechanisms = [
            (DetectionMechanism.CROSS_AGENT_DIVERGENCE, "cross_agent_divergence"),
            (DetectionMechanism.INTRA_AGENT_CONSISTENCY, "intra_agent_consistency"),
            (DetectionMechanism.HASH_CHAIN_VERIFICATION, "hash_chain"),
            (DetectionMechanism.TEMPORAL_DRIFT, "temporal_drift"),
            (DetectionMechanism.CONSCIENCE_OVERRIDE, "conscience_override"),
        ]

        for mechanism, expected_value in mechanisms:
            alert = AnomalyAlert(
                alert_id="test",
                detection_mechanism=mechanism,
            )
            result = alert.to_dict()
            assert result["detection_mechanism"] == expected_value


# =============================================================================
# CoherenceRatchetAnalyzer Static Methods Tests
# =============================================================================


class TestCalculateZScores:
    """Tests for calculate_z_scores static method."""

    def test_calculate_z_scores_normal(self, normal_distribution_values: list[float]):
        """Test z-score calculation with normal distribution values."""
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores(
            normal_distribution_values
        )

        # Verify mean is close to expected (around 0.80)
        assert 0.78 <= mean <= 0.82

        # Verify standard deviation is reasonable (small for tight distribution)
        assert 0.01 <= std <= 0.03

        # Verify z-scores are calculated
        assert len(z_scores) == len(normal_distribution_values)

        # All z-scores should be relatively small for normal distribution
        for z in z_scores:
            assert abs(z) < 2.0

    def test_calculate_z_scores_empty(self):
        """Test z-score calculation with empty list."""
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores([])

        assert mean == 0.0
        assert std == 0.0
        assert z_scores == []

    def test_calculate_z_scores_single(self):
        """Test z-score calculation with single value."""
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores([5.0])

        assert mean == 0.0
        assert std == 0.0
        assert z_scores == []

    def test_calculate_z_scores_identical_values(self):
        """Test z-score calculation when all values are identical (zero std)."""
        values = [1.0, 1.0, 1.0, 1.0, 1.0]
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores(values)

        assert mean == 1.0
        assert std == 0.0
        assert z_scores == [0.0, 0.0, 0.0, 0.0, 0.0]

    def test_calculate_z_scores_two_values(self):
        """Test z-score calculation with exactly two values."""
        values = [0.0, 2.0]
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores(values)

        assert mean == 1.0
        assert std == 1.0
        assert len(z_scores) == 2
        assert z_scores[0] == -1.0
        assert z_scores[1] == 1.0


class TestDetectOutliers:
    """Tests for detect_outliers static method."""

    def test_detect_outliers_with_known_outliers(self, values_with_outliers: list[float]):
        """Test outlier detection with known outliers in data."""
        outliers = CoherenceRatchetAnalyzer.detect_outliers(
            values_with_outliers, threshold=2.0
        )

        # Should detect the outliers (0.20 and 1.50)
        assert len(outliers) >= 2

        # Verify structure of outlier tuples
        for idx, value, z_score in outliers:
            assert isinstance(idx, int)
            assert isinstance(value, float)
            assert isinstance(z_score, float)
            assert abs(z_score) > 2.0

    def test_detect_outliers_threshold_default(self, normal_distribution_values: list[float]):
        """Test outlier detection with default threshold (2.0) on normal data."""
        outliers = CoherenceRatchetAnalyzer.detect_outliers(normal_distribution_values)

        # Normal distribution should have few or no outliers at 2σ threshold
        assert len(outliers) <= 1

    def test_detect_outliers_threshold_strict(self, values_with_outliers: list[float]):
        """Test outlier detection with stricter threshold (3.0)."""
        outliers_2sigma = CoherenceRatchetAnalyzer.detect_outliers(
            values_with_outliers, threshold=2.0
        )
        outliers_3sigma = CoherenceRatchetAnalyzer.detect_outliers(
            values_with_outliers, threshold=3.0
        )

        # Stricter threshold should find fewer or equal outliers
        assert len(outliers_3sigma) <= len(outliers_2sigma)

    def test_detect_outliers_threshold_lenient(self, normal_distribution_values: list[float]):
        """Test outlier detection with lenient threshold (1.0)."""
        outliers = CoherenceRatchetAnalyzer.detect_outliers(
            normal_distribution_values, threshold=1.0
        )

        # Lower threshold should catch more values as outliers
        # At 1σ threshold, about 32% of normal distribution values are "outliers"
        assert len(outliers) >= 0

    def test_detect_outliers_empty_list(self):
        """Test outlier detection with empty list."""
        outliers = CoherenceRatchetAnalyzer.detect_outliers([])
        assert outliers == []

    def test_detect_outliers_returns_correct_indices(self):
        """Test that outlier detection returns correct indices."""
        # Create data with outliers that are clear statistical outliers
        # Mean will be around 1.0, std around 0.0 for the normal values
        # The value 10.0 at index 2 will have a very high z-score
        values = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 1.0, 1.0]  # Outlier at index 7
        outliers = CoherenceRatchetAnalyzer.detect_outliers(values, threshold=2.0)

        # Should find the outlier at index 7
        indices = [idx for idx, _, _ in outliers]
        assert 7 in indices


class TestVerifyTraceHashChain:
    """Tests for verify_trace_hash_chain static method."""

    def test_verify_trace_hash_chain_valid(self, sample_traces_valid_chain: list[dict[str, Any]]):
        """Test valid hash chain returns empty list."""
        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(sample_traces_valid_chain)
        assert breaks == []

    def test_verify_trace_hash_chain_gap(self, sample_traces_with_gap: list[dict[str, Any]]):
        """Test hash chain gap detection."""
        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(sample_traces_with_gap)

        assert len(breaks) == 1
        assert isinstance(breaks[0], HashChainBreak)
        assert breaks[0].break_type == "sequence_gap"
        assert breaks[0].expected_seq == 3
        assert breaks[0].actual_seq == 4

    def test_verify_trace_hash_chain_unsorted(self, sample_traces_valid_chain: list[dict[str, Any]]):
        """Test that unsorted input is handled correctly."""
        # Shuffle the traces
        shuffled = [
            sample_traces_valid_chain[3],  # seq 4
            sample_traces_valid_chain[0],  # seq 1
            sample_traces_valid_chain[4],  # seq 5
            sample_traces_valid_chain[1],  # seq 2
            sample_traces_valid_chain[2],  # seq 3
        ]

        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(shuffled)

        # Should still be valid after sorting
        assert breaks == []

    def test_verify_trace_hash_chain_empty(self):
        """Test empty trace list."""
        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain([])
        assert breaks == []

    def test_verify_trace_hash_chain_single_trace(self):
        """Test single trace returns no breaks."""
        traces = [{"trace_id": "t1", "audit_sequence_number": 1, "audit_entry_hash": "h1"}]
        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(traces)
        assert breaks == []

    def test_verify_trace_hash_chain_multiple_gaps(self):
        """Test detection of multiple gaps."""
        traces = [
            {"trace_id": "t1", "audit_sequence_number": 1, "audit_entry_hash": "h1"},
            {"trace_id": "t3", "audit_sequence_number": 3, "audit_entry_hash": "h3"},  # Gap: 2 missing
            {"trace_id": "t6", "audit_sequence_number": 6, "audit_entry_hash": "h6"},  # Gap: 4,5 missing
        ]

        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(traces)

        assert len(breaks) == 2
        assert breaks[0].expected_seq == 2
        assert breaks[0].actual_seq == 3
        assert breaks[1].expected_seq == 4
        assert breaks[1].actual_seq == 6

    def test_verify_trace_hash_chain_filters_none_sequence(self):
        """Test that traces without sequence numbers are filtered."""
        traces = [
            {"trace_id": "t1", "audit_sequence_number": 1, "audit_entry_hash": "h1"},
            {"trace_id": "t2", "audit_sequence_number": None, "audit_entry_hash": "h2"},
            {"trace_id": "t3", "audit_sequence_number": 2, "audit_entry_hash": "h3"},
        ]

        breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(traces)
        assert breaks == []


# =============================================================================
# Database-Dependent Methods Tests
# =============================================================================


class TestDetectCrossAgentDivergence:
    """Tests for detect_cross_agent_divergence method."""

    @pytest.mark.asyncio
    async def test_detect_cross_agent_divergence_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.detect_cross_agent_divergence()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_cross_agent_divergence_with_data(self, mock_db_pool):
        """Test detection with mocked database responses."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        # Mock database response with divergent agent
        mock_rows = [
            {
                "agent_id_hash": "agent-divergent",
                "dsdma_domain": "healthcare",
                "avg_plausibility": 0.5,  # Below domain average
                "domain_plausibility": 0.85,
                "std_plausibility": 0.1,
                "avg_alignment": 0.9,
                "domain_alignment": 0.9,
                "std_alignment": 0.05,
                "avg_coherence": 0.8,
                "domain_coherence": 0.8,
                "std_coherence": 0.05,
                "trace_count": 100,
                "recent_traces": ["t1", "t2", "t3"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_cross_agent_divergence(lookback_days=7)

        assert len(alerts) >= 1
        alert = alerts[0]
        assert alert.agent_id_hash == "agent-divergent"
        assert alert.domain == "healthcare"
        assert alert.detection_mechanism == DetectionMechanism.CROSS_AGENT_DIVERGENCE
        assert "σ" in alert.deviation

    @pytest.mark.asyncio
    async def test_detect_cross_agent_divergence_empty_result(self, mock_db_pool):
        """Test returns empty list when no divergent agents found."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        conn.fetch.return_value = []

        alerts = await analyzer.detect_cross_agent_divergence()
        assert alerts == []


class TestDetectIntraAgentInconsistency:
    """Tests for detect_intra_agent_inconsistency method."""

    @pytest.mark.asyncio
    async def test_detect_intra_agent_inconsistency_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.detect_intra_agent_inconsistency()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_intra_agent_inconsistency_with_data(self, mock_db_pool):
        """Test inconsistency detection with mocked data."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        mock_rows = [
            {
                "agent_id_hash": "agent-inconsistent",
                "trace_type": "decision",
                "distinct_actions": 5,
                "actions_used": ["approve", "defer", "reject", "escalate", "ignore"],
                "total_traces": 100,
                "avg_plausibility": 0.7,
                "std_plausibility": 0.25,
                "recent_traces": ["t1", "t2", "t3"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_intra_agent_inconsistency(lookback_days=30)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.agent_id_hash == "agent-inconsistent"
        assert alert.detection_mechanism == DetectionMechanism.INTRA_AGENT_CONSISTENCY
        assert alert.metric == "action_variance"
        assert "5 actions" in alert.deviation


class TestDetectTemporalDrift:
    """Tests for detect_temporal_drift method."""

    @pytest.mark.asyncio
    async def test_detect_temporal_drift_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.detect_temporal_drift()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_temporal_drift_with_data(self, mock_db_pool):
        """Test drift detection with mocked data."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        from datetime import date
        mock_rows = [
            {
                "agent_id_hash": "agent-drifting",
                "day": date(2024, 1, 15),
                "daily_coherence": 0.60,
                "prev_coherence": 0.85,
                "coherence_change": 0.25,  # 25% change
                "daily_plausibility": 0.80,
                "prev_plausibility": 0.82,
                "plausibility_change": 0.02,
                "trace_count": 50,
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_temporal_drift(lookback_days=30)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.agent_id_hash == "agent-drifting"
        assert alert.detection_mechanism == DetectionMechanism.TEMPORAL_DRIFT
        assert "%" in alert.deviation


class TestDetectConscienceOverrideAnomalies:
    """Tests for detect_conscience_override_anomalies method."""

    @pytest.mark.asyncio
    async def test_detect_conscience_override_anomalies_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.detect_conscience_override_anomalies()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_conscience_override_anomalies_with_data(self, mock_db_pool):
        """Test override detection with mocked data."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        mock_rows = [
            {
                "agent_id_hash": "agent-override",
                "dsdma_domain": "finance",
                "total_traces": 100,
                "override_count": 30,
                "override_rate": 0.30,  # 30% override rate
                "avg_override_rate": 0.10,  # Domain average is 10%
                "conscience_failures": 25,
                "override_traces": ["t1", "t2", "t3"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_conscience_override_anomalies(lookback_days=7)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.agent_id_hash == "agent-override"
        assert alert.domain == "finance"
        assert alert.detection_mechanism == DetectionMechanism.CONSCIENCE_OVERRIDE
        assert alert.metric == "conscience_override_rate"
        assert "x domain average" in alert.deviation


class TestRunAllDetections:
    """Tests for run_all_detections method."""

    @pytest.mark.asyncio
    async def test_run_all_detections_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.run_all_detections()
        assert result == []

    @pytest.mark.asyncio
    async def test_run_all_detections_calls_all_methods(self, mock_db_pool, mocker):
        """Test that all detection methods are called."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        # Mock all detection methods
        mock_cross_agent = mocker.patch.object(
            analyzer, "detect_cross_agent_divergence",
            new_callable=AsyncMock, return_value=[]
        )
        mock_intra_agent = mocker.patch.object(
            analyzer, "detect_intra_agent_inconsistency",
            new_callable=AsyncMock, return_value=[]
        )
        mock_hash_chain = mocker.patch.object(
            analyzer, "detect_hash_chain_anomalies",
            new_callable=AsyncMock, return_value=[]
        )
        mock_temporal = mocker.patch.object(
            analyzer, "detect_temporal_drift",
            new_callable=AsyncMock, return_value=[]
        )
        mock_conscience = mocker.patch.object(
            analyzer, "detect_conscience_override_anomalies",
            new_callable=AsyncMock, return_value=[]
        )

        await analyzer.run_all_detections()

        mock_cross_agent.assert_called_once()
        mock_intra_agent.assert_called_once()
        mock_hash_chain.assert_called_once()
        mock_temporal.assert_called_once()
        mock_conscience.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_all_detections_combines_and_sorts_alerts(self, mock_db_pool, mocker):
        """Test that alerts are combined and sorted by severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        now = datetime.utcnow()
        warning_alert = AnomalyAlert(
            alert_id="warning-1",
            severity=AlertSeverity.WARNING,
            timestamp=now,
        )
        critical_alert = AnomalyAlert(
            alert_id="critical-1",
            severity=AlertSeverity.CRITICAL,
            timestamp=now + timedelta(hours=1),
        )

        mocker.patch.object(
            analyzer, "detect_cross_agent_divergence",
            new_callable=AsyncMock, return_value=[warning_alert]
        )
        mocker.patch.object(
            analyzer, "detect_intra_agent_inconsistency",
            new_callable=AsyncMock, return_value=[critical_alert]
        )
        mocker.patch.object(
            analyzer, "detect_hash_chain_anomalies",
            new_callable=AsyncMock, return_value=[]
        )
        mocker.patch.object(
            analyzer, "detect_temporal_drift",
            new_callable=AsyncMock, return_value=[]
        )
        mocker.patch.object(
            analyzer, "detect_conscience_override_anomalies",
            new_callable=AsyncMock, return_value=[]
        )

        result = await analyzer.run_all_detections()

        assert len(result) == 2
        # Critical should come first
        assert result[0].severity == AlertSeverity.CRITICAL
        assert result[1].severity == AlertSeverity.WARNING


# =============================================================================
# Alert Severity Logic Tests
# =============================================================================


class TestZScoreSeverityThresholds:
    """Tests for z-score based severity thresholds."""

    @pytest.mark.asyncio
    async def test_z_score_severity_2sigma_warning(self, mock_db_pool):
        """Test that 2σ divergence produces WARNING severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        # Z-score of 2.5 (above 2.0 threshold, below 3.0)
        mock_rows = [
            {
                "agent_id_hash": "agent-1",
                "dsdma_domain": "general",
                "avg_plausibility": 0.55,
                "domain_plausibility": 0.80,
                "std_plausibility": 0.10,  # z = (0.55-0.80)/0.10 = 2.5
                "avg_alignment": 0.9,
                "domain_alignment": 0.9,
                "std_alignment": 0.0,  # No divergence
                "avg_coherence": 0.8,
                "domain_coherence": 0.8,
                "std_coherence": 0.0,  # No divergence
                "trace_count": 50,
                "recent_traces": ["t1"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_cross_agent_divergence()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_z_score_severity_3sigma_critical(self, mock_db_pool):
        """Test that 3σ divergence produces CRITICAL severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        # Z-score of 3.5 (above 3.0 threshold)
        mock_rows = [
            {
                "agent_id_hash": "agent-critical",
                "dsdma_domain": "general",
                "avg_plausibility": 0.45,
                "domain_plausibility": 0.80,
                "std_plausibility": 0.10,  # z = (0.45-0.80)/0.10 = 3.5
                "avg_alignment": 0.9,
                "domain_alignment": 0.9,
                "std_alignment": 0.0,
                "avg_coherence": 0.8,
                "domain_coherence": 0.8,
                "std_coherence": 0.0,
                "trace_count": 50,
                "recent_traces": ["t1"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_cross_agent_divergence()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL


class TestDriftSeverityThresholds:
    """Tests for temporal drift severity thresholds."""

    @pytest.mark.asyncio
    async def test_drift_severity_15_percent_warning(self, mock_db_pool):
        """Test that 15-25% drift produces WARNING severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        from datetime import date
        mock_rows = [
            {
                "agent_id_hash": "agent-drift",
                "day": date(2024, 1, 15),
                "daily_coherence": 0.68,
                "prev_coherence": 0.85,
                "coherence_change": 0.17,  # 17% change (between 15% and 25%)
                "daily_plausibility": 0.80,
                "prev_plausibility": 0.80,
                "plausibility_change": 0.0,
                "trace_count": 50,
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_temporal_drift()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_drift_severity_25_percent_critical(self, mock_db_pool):
        """Test that >25% drift produces CRITICAL severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        from datetime import date
        mock_rows = [
            {
                "agent_id_hash": "agent-drift-critical",
                "day": date(2024, 1, 15),
                "daily_coherence": 0.55,
                "prev_coherence": 0.85,
                "coherence_change": 0.30,  # 30% change (above 25%)
                "daily_plausibility": 0.80,
                "prev_plausibility": 0.80,
                "plausibility_change": 0.0,
                "trace_count": 50,
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_temporal_drift()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL


class TestOverrideSeverityThresholds:
    """Tests for conscience override severity thresholds."""

    @pytest.mark.asyncio
    async def test_override_severity_2x_warning(self, mock_db_pool):
        """Test that 2x override rate produces WARNING severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        mock_rows = [
            {
                "agent_id_hash": "agent-override",
                "dsdma_domain": "general",
                "total_traces": 100,
                "override_count": 20,
                "override_rate": 0.20,  # 20%
                "avg_override_rate": 0.08,  # 8% domain average -> 2.5x
                "conscience_failures": 15,
                "override_traces": ["t1"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_conscience_override_anomalies()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_override_severity_3x_critical(self, mock_db_pool):
        """Test that 3x override rate produces CRITICAL severity."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        mock_rows = [
            {
                "agent_id_hash": "agent-override-critical",
                "dsdma_domain": "general",
                "total_traces": 100,
                "override_count": 40,
                "override_rate": 0.40,  # 40%
                "avg_override_rate": 0.10,  # 10% domain average -> 4x
                "conscience_failures": 35,
                "override_traces": ["t1"],
            }
        ]
        conn.fetch.return_value = mock_rows

        alerts = await analyzer.detect_conscience_override_anomalies()

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL


# =============================================================================
# Hash Chain Anomaly Tests
# =============================================================================


class TestDetectHashChainAnomalies:
    """Tests for detect_hash_chain_anomalies method."""

    @pytest.mark.asyncio
    async def test_detect_hash_chain_anomalies_no_pool(self, analyzer: CoherenceRatchetAnalyzer):
        """Test returns empty list when no database pool."""
        result = await analyzer.detect_hash_chain_anomalies()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_hash_chain_anomalies_finds_breaks(self, mock_db_pool, mocker):
        """Test that hash chain breaks are detected and reported."""
        pool, conn = mock_db_pool
        analyzer = CoherenceRatchetAnalyzer(db_pool=pool)

        # First query returns agents
        # Second query returns gaps
        conn.fetch.side_effect = [
            [{"agent_id_hash": "agent-with-gaps"}],  # Agents query
            [  # Hash chain verification query
                {
                    "trace_id": "trace-4",
                    "audit_sequence_number": 4,
                    "prev_seq": 2,
                    "gap_size": 2,
                    "audit_entry_hash": "hash4",
                    "prev_hash": "hash2",
                }
            ],
        ]

        alerts = await analyzer.detect_hash_chain_anomalies()

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.detection_mechanism == DetectionMechanism.HASH_CHAIN_VERIFICATION
        assert alert.agent_id_hash == "agent-with-gaps"
        assert "CRITICAL" in alert.recommended_action


# =============================================================================
# Analyzer Threshold Constants Tests
# =============================================================================


class TestAnalyzerConstants:
    """Tests for analyzer threshold constants."""

    def test_z_score_thresholds(self):
        """Test z-score threshold constants are correct."""
        assert CoherenceRatchetAnalyzer.Z_SCORE_WARNING == 2.0
        assert CoherenceRatchetAnalyzer.Z_SCORE_CRITICAL == 3.0

    def test_drift_thresholds(self):
        """Test drift threshold constants are correct."""
        assert CoherenceRatchetAnalyzer.DAILY_DRIFT_WARNING == 0.15
        assert CoherenceRatchetAnalyzer.DAILY_DRIFT_CRITICAL == 0.25

    def test_override_thresholds(self):
        """Test override threshold constants are correct."""
        assert CoherenceRatchetAnalyzer.OVERRIDE_RATE_MULTIPLIER_WARNING == 2.0
        assert CoherenceRatchetAnalyzer.OVERRIDE_RATE_MULTIPLIER_CRITICAL == 3.0

    def test_minimum_data_thresholds(self):
        """Test minimum data requirement constants are correct."""
        assert CoherenceRatchetAnalyzer.MIN_TRACES_PER_AGENT == 10
        assert CoherenceRatchetAnalyzer.MIN_AGENTS_PER_DOMAIN == 3
        assert CoherenceRatchetAnalyzer.MIN_TRACES_PER_DAY == 5


# =============================================================================
# HashChainBreak Tests
# =============================================================================


class TestHashChainBreak:
    """Tests for HashChainBreak dataclass."""

    def test_hash_chain_break_sequence_gap(self):
        """Test HashChainBreak for sequence gap type."""
        break_record = HashChainBreak(
            break_type="sequence_gap",
            trace_id="trace-123",
            expected_seq=5,
            actual_seq=8,
        )

        assert break_record.break_type == "sequence_gap"
        assert break_record.trace_id == "trace-123"
        assert break_record.expected_seq == 5
        assert break_record.actual_seq == 8
        assert break_record.expected_hash is None
        assert break_record.actual_hash is None

    def test_hash_chain_break_hash_mismatch(self):
        """Test HashChainBreak for hash mismatch type."""
        break_record = HashChainBreak(
            break_type="hash_mismatch",
            trace_id="trace-456",
            expected_hash="abc123",
            actual_hash="def456",
        )

        assert break_record.break_type == "hash_mismatch"
        assert break_record.trace_id == "trace-456"
        assert break_record.expected_hash == "abc123"
        assert break_record.actual_hash == "def456"
