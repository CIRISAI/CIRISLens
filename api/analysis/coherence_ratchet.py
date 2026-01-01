"""
Coherence Ratchet Detection Module

Implements Phase 1 detection mechanisms for identifying anomalies in CIRIS agent traces.
See FSD/coherence_ratchet_detection.md for specification.

Detection is triage, not verdict - anomalies warrant human investigation.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np
from scipy import stats


class AlertSeverity(Enum):
    """Alert severity levels."""

    WARNING = "warning"
    CRITICAL = "critical"


class DetectionMechanism(Enum):
    """Detection mechanism identifiers."""

    CROSS_AGENT_DIVERGENCE = "cross_agent_divergence"
    INTRA_AGENT_CONSISTENCY = "intra_agent_consistency"
    HASH_CHAIN_VERIFICATION = "hash_chain"
    TEMPORAL_DRIFT = "temporal_drift"
    CONSCIENCE_OVERRIDE = "conscience_override"


@dataclass
class AnomalyAlert:
    """Represents a detected anomaly requiring investigation."""

    alert_id: str
    alert_type: str = "coherence_ratchet_anomaly"
    severity: AlertSeverity = AlertSeverity.WARNING
    detection_mechanism: DetectionMechanism = DetectionMechanism.CROSS_AGENT_DIVERGENCE
    agent_id_hash: str = ""
    domain: str | None = None
    metric: str = ""
    value: float = 0.0
    baseline: float = 0.0
    deviation: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    evidence_traces: list[str] = field(default_factory=list)
    recommended_action: str = "Review recent traces for this agent"

    def to_dict(self) -> dict[str, Any]:
        """Convert alert to JSON-serializable dictionary."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity.value,
            "detection_mechanism": self.detection_mechanism.value,
            "agent_id_hash": self.agent_id_hash,
            "domain": self.domain,
            "metric": self.metric,
            "value": self.value,
            "baseline": self.baseline,
            "deviation": self.deviation,
            "timestamp": self.timestamp.isoformat() + "Z",
            "evidence_traces": self.evidence_traces,
            "recommended_action": self.recommended_action,
        }


@dataclass
class HashChainBreak:
    """Represents a break in the audit hash chain."""

    break_type: str  # "sequence_gap" or "hash_mismatch"
    trace_id: str
    expected_seq: int | None = None
    actual_seq: int | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None


class CoherenceRatchetAnalyzer:
    """
    Analyzes CIRIS agent traces for anomalies using statistical detection.

    Implements Phase 1 detection mechanisms:
    - Cross-agent divergence (z-score analysis)
    - Intra-agent consistency checking
    - Hash chain verification
    - Temporal drift detection
    - Conscience override pattern analysis
    """

    # Thresholds from FSD
    Z_SCORE_WARNING = 2.0
    Z_SCORE_CRITICAL = 3.0
    DAILY_DRIFT_WARNING = 0.15
    DAILY_DRIFT_CRITICAL = 0.25
    MIN_TRACES_PER_AGENT = 10
    MIN_AGENTS_PER_DOMAIN = 3
    MIN_TRACES_PER_DAY = 5
    OVERRIDE_RATE_MULTIPLIER_WARNING = 2.0
    OVERRIDE_RATE_MULTIPLIER_CRITICAL = 3.0

    def __init__(self, db_pool: Any = None):
        """
        Initialize analyzer.

        Args:
            db_pool: asyncpg connection pool for database queries
        """
        self.db_pool = db_pool

    # -------------------------------------------------------------------------
    # 2.1 Cross-Agent Divergence Detection
    # -------------------------------------------------------------------------

    async def detect_cross_agent_divergence(
        self,
        lookback_days: int = 7,
    ) -> list[AnomalyAlert]:
        """
        Detect agents whose DMA scores diverge significantly from their domain population.

        Uses z-score analysis stratified by domain. Agents facing similar scenarios
        should produce similar plausibility and alignment scores.

        Args:
            lookback_days: Number of days to analyze

        Returns:
            List of anomaly alerts for divergent agents
        """
        if not self.db_pool:
            return []

        query = """
        WITH agent_scores AS (
            SELECT
                agent_id_hash,
                dsdma_domain,
                AVG(csdma_plausibility_score) as avg_plausibility,
                AVG(dsdma_domain_alignment) as avg_alignment,
                AVG(coherence_level) as avg_coherence,
                COUNT(*) as trace_count,
                ARRAY_AGG(trace_id ORDER BY timestamp DESC LIMIT 5) as recent_traces
            FROM cirislens.covenant_traces
            WHERE timestamp > NOW() - $1::interval
            AND signature_verified = TRUE
            AND csdma_plausibility_score IS NOT NULL
            GROUP BY agent_id_hash, dsdma_domain
            HAVING COUNT(*) >= $2
        ),
        domain_stats AS (
            SELECT
                dsdma_domain,
                AVG(avg_plausibility) as domain_plausibility,
                STDDEV(avg_plausibility) as std_plausibility,
                AVG(avg_alignment) as domain_alignment,
                STDDEV(avg_alignment) as std_alignment,
                AVG(avg_coherence) as domain_coherence,
                STDDEV(avg_coherence) as std_coherence
            FROM agent_scores
            GROUP BY dsdma_domain
            HAVING COUNT(*) >= $3
        )
        SELECT
            a.agent_id_hash,
            a.dsdma_domain,
            a.avg_plausibility,
            d.domain_plausibility,
            d.std_plausibility,
            a.avg_alignment,
            d.domain_alignment,
            d.std_alignment,
            a.avg_coherence,
            d.domain_coherence,
            d.std_coherence,
            a.trace_count,
            a.recent_traces
        FROM agent_scores a
        JOIN domain_stats d ON a.dsdma_domain = d.dsdma_domain
        WHERE (d.std_plausibility > 0 AND
               ABS(a.avg_plausibility - d.domain_plausibility) / d.std_plausibility > $4)
           OR (d.std_alignment > 0 AND
               ABS(a.avg_alignment - d.domain_alignment) / d.std_alignment > $4)
           OR (d.std_coherence > 0 AND
               ABS(a.avg_coherence - d.domain_coherence) / d.std_coherence > $4);
        """

        alerts = []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                query,
                f"{lookback_days} days",
                self.MIN_TRACES_PER_AGENT,
                self.MIN_AGENTS_PER_DOMAIN,
                self.Z_SCORE_WARNING,
            )

            for row in rows:
                # Calculate z-scores for each metric
                metrics = []
                if row["std_plausibility"] and row["std_plausibility"] > 0:
                    z_plausibility = abs(
                        row["avg_plausibility"] - row["domain_plausibility"]
                    ) / row["std_plausibility"]
                    if z_plausibility > self.Z_SCORE_WARNING:
                        metrics.append(("csdma_plausibility_score", z_plausibility,
                                       row["avg_plausibility"], row["domain_plausibility"]))

                if row["std_alignment"] and row["std_alignment"] > 0:
                    z_alignment = abs(
                        row["avg_alignment"] - row["domain_alignment"]
                    ) / row["std_alignment"]
                    if z_alignment > self.Z_SCORE_WARNING:
                        metrics.append(("dsdma_domain_alignment", z_alignment,
                                       row["avg_alignment"], row["domain_alignment"]))

                if row["std_coherence"] and row["std_coherence"] > 0:
                    z_coherence = abs(
                        row["avg_coherence"] - row["domain_coherence"]
                    ) / row["std_coherence"]
                    if z_coherence > self.Z_SCORE_WARNING:
                        metrics.append(("coherence_level", z_coherence,
                                       row["avg_coherence"], row["domain_coherence"]))

                # Create alert for highest z-score metric
                for metric_name, z_score, value, baseline in metrics:
                    severity = (
                        AlertSeverity.CRITICAL
                        if z_score > self.Z_SCORE_CRITICAL
                        else AlertSeverity.WARNING
                    )
                    alerts.append(
                        AnomalyAlert(
                            alert_id=str(uuid.uuid4()),
                            severity=severity,
                            detection_mechanism=DetectionMechanism.CROSS_AGENT_DIVERGENCE,
                            agent_id_hash=row["agent_id_hash"],
                            domain=row["dsdma_domain"],
                            metric=metric_name,
                            value=float(value),
                            baseline=float(baseline),
                            deviation=f"{z_score:.1f}σ",
                            evidence_traces=row["recent_traces"] or [],
                            recommended_action=(
                                f"Agent shows {z_score:.1f}σ divergence in {metric_name}. "
                                f"Review traces to determine if behavior is legitimate."
                            ),
                        )
                    )

        return alerts

    # -------------------------------------------------------------------------
    # 2.2 Intra-Agent Consistency Detection
    # -------------------------------------------------------------------------

    async def detect_intra_agent_inconsistency(
        self,
        lookback_days: int = 30,
    ) -> list[AnomalyAlert]:
        """
        Detect when an agent contradicts its own prior reasoning patterns.

        Tracks action type variance for same trace types and high variance
        in plausibility scores indicating inconsistent reasoning quality.

        Args:
            lookback_days: Number of days to analyze

        Returns:
            List of anomaly alerts for inconsistent agents
        """
        if not self.db_pool:
            return []

        query = """
        WITH agent_actions AS (
            SELECT
                agent_id_hash,
                trace_id,
                trace_type,
                selected_action,
                conscience_passed,
                csdma_plausibility_score,
                timestamp
            FROM cirislens.covenant_traces
            WHERE timestamp > NOW() - $1::interval
            AND signature_verified = TRUE
            AND trace_type IS NOT NULL
        )
        SELECT
            agent_id_hash,
            trace_type,
            COUNT(DISTINCT selected_action) as distinct_actions,
            ARRAY_AGG(DISTINCT selected_action) as actions_used,
            COUNT(*) as total_traces,
            AVG(csdma_plausibility_score) as avg_plausibility,
            STDDEV(csdma_plausibility_score) as std_plausibility,
            ARRAY_AGG(trace_id ORDER BY timestamp DESC LIMIT 5) as recent_traces
        FROM agent_actions
        GROUP BY agent_id_hash, trace_type
        HAVING COUNT(DISTINCT selected_action) > 2
           AND STDDEV(csdma_plausibility_score) > 0.15;
        """

        alerts = []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, f"{lookback_days} days")

            for row in rows:
                severity = (
                    AlertSeverity.CRITICAL
                    if row["distinct_actions"] > 3 and row["std_plausibility"] > 0.2
                    else AlertSeverity.WARNING
                )
                alerts.append(
                    AnomalyAlert(
                        alert_id=str(uuid.uuid4()),
                        severity=severity,
                        detection_mechanism=DetectionMechanism.INTRA_AGENT_CONSISTENCY,
                        agent_id_hash=row["agent_id_hash"],
                        domain=None,
                        metric="action_variance",
                        value=float(row["std_plausibility"] or 0),
                        baseline=0.0,
                        deviation=f"{row['distinct_actions']} actions, σ={row['std_plausibility']:.2f}",
                        evidence_traces=row["recent_traces"] or [],
                        recommended_action=(
                            f"Agent uses {row['distinct_actions']} different actions "
                            f"({', '.join(row['actions_used'] or [])}) for {row['trace_type']} traces "
                            f"with high score variance. Review for context-appropriate changes."
                        ),
                    )
                )

        return alerts

    # -------------------------------------------------------------------------
    # 2.3 Hash Chain Verification
    # -------------------------------------------------------------------------

    async def verify_hash_chain(
        self,
        agent_id_hash: str,
    ) -> list[HashChainBreak]:
        """
        Verify the immutability and completeness of an agent's audit trail.

        Each trace contains audit_sequence_number and audit_entry_hash.
        Gaps or mismatches indicate tampering or data loss.

        Args:
            agent_id_hash: The agent to verify

        Returns:
            List of hash chain breaks (empty if chain is valid)
        """
        if not self.db_pool:
            return []

        query = """
        WITH ordered_traces AS (
            SELECT
                trace_id,
                audit_sequence_number,
                audit_entry_hash,
                LAG(audit_sequence_number) OVER (
                    ORDER BY audit_sequence_number
                ) as prev_seq,
                LAG(audit_entry_hash) OVER (
                    ORDER BY audit_sequence_number
                ) as prev_hash
            FROM cirislens.covenant_traces
            WHERE agent_id_hash = $1
            AND audit_sequence_number IS NOT NULL
            ORDER BY audit_sequence_number
        )
        SELECT
            trace_id,
            audit_sequence_number,
            prev_seq,
            (audit_sequence_number - prev_seq) as gap_size,
            audit_entry_hash,
            prev_hash
        FROM ordered_traces
        WHERE prev_seq IS NOT NULL
        AND audit_sequence_number - prev_seq != 1;
        """

        breaks = []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, agent_id_hash)

            for row in rows:
                breaks.append(
                    HashChainBreak(
                        break_type="sequence_gap",
                        trace_id=row["trace_id"],
                        expected_seq=row["prev_seq"] + 1 if row["prev_seq"] else None,
                        actual_seq=row["audit_sequence_number"],
                    )
                )

        return breaks

    async def detect_hash_chain_anomalies(self) -> list[AnomalyAlert]:
        """
        Check all agents for hash chain integrity issues.

        Returns:
            List of critical alerts for any hash chain breaks
        """
        if not self.db_pool:
            return []

        # Get distinct agents with audit data
        query = """
        SELECT DISTINCT agent_id_hash
        FROM cirislens.covenant_traces
        WHERE audit_sequence_number IS NOT NULL
        AND timestamp > NOW() - INTERVAL '30 days';
        """

        alerts = []
        async with self.db_pool.acquire() as conn:
            agents = await conn.fetch(query)

            for agent_row in agents:
                breaks = await self.verify_hash_chain(agent_row["agent_id_hash"])
                if breaks:
                    alerts.append(
                        AnomalyAlert(
                            alert_id=str(uuid.uuid4()),
                            severity=AlertSeverity.CRITICAL,
                            detection_mechanism=DetectionMechanism.HASH_CHAIN_VERIFICATION,
                            agent_id_hash=agent_row["agent_id_hash"],
                            domain=None,
                            metric="hash_chain_integrity",
                            value=float(len(breaks)),
                            baseline=0.0,
                            deviation=f"{len(breaks)} breaks",
                            evidence_traces=[b.trace_id for b in breaks[:5]],
                            recommended_action=(
                                f"CRITICAL: {len(breaks)} hash chain breaks detected. "
                                "This may indicate tampering or data loss. "
                                "Immediate investigation required."
                            ),
                        )
                    )

        return alerts

    # -------------------------------------------------------------------------
    # 2.4 Temporal Drift Detection
    # -------------------------------------------------------------------------

    async def detect_temporal_drift(
        self,
        lookback_days: int = 30,
    ) -> list[AnomalyAlert]:
        """
        Track behavioral changes over time.

        Sudden changes in an agent's score distributions may indicate
        configuration changes, compromise, or drift.

        Args:
            lookback_days: Number of days to analyze

        Returns:
            List of anomaly alerts for drifting agents
        """
        if not self.db_pool:
            return []

        query = """
        WITH daily_scores AS (
            SELECT
                agent_id_hash,
                DATE(timestamp) as day,
                AVG(coherence_level) as daily_coherence,
                AVG(csdma_plausibility_score) as daily_plausibility,
                COUNT(*) as trace_count
            FROM cirislens.covenant_traces
            WHERE timestamp > NOW() - $1::interval
            AND signature_verified = TRUE
            GROUP BY agent_id_hash, DATE(timestamp)
            HAVING COUNT(*) >= $2
        ),
        with_lag AS (
            SELECT
                agent_id_hash,
                day,
                daily_coherence,
                daily_plausibility,
                trace_count,
                LAG(daily_coherence) OVER (
                    PARTITION BY agent_id_hash ORDER BY day
                ) as prev_coherence,
                LAG(daily_plausibility) OVER (
                    PARTITION BY agent_id_hash ORDER BY day
                ) as prev_plausibility
            FROM daily_scores
        )
        SELECT
            agent_id_hash,
            day,
            daily_coherence,
            prev_coherence,
            ABS(daily_coherence - prev_coherence) as coherence_change,
            daily_plausibility,
            prev_plausibility,
            ABS(daily_plausibility - prev_plausibility) as plausibility_change,
            trace_count
        FROM with_lag
        WHERE prev_coherence IS NOT NULL
        AND (ABS(daily_coherence - prev_coherence) > $3
             OR ABS(daily_plausibility - prev_plausibility) > $3)
        ORDER BY day DESC;
        """

        alerts = []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                query,
                f"{lookback_days} days",
                self.MIN_TRACES_PER_DAY,
                self.DAILY_DRIFT_WARNING,
            )

            for row in rows:
                coherence_change = row["coherence_change"] or 0
                plausibility_change = row["plausibility_change"] or 0
                max_change = max(coherence_change, plausibility_change)

                severity = (
                    AlertSeverity.CRITICAL
                    if max_change > self.DAILY_DRIFT_CRITICAL
                    else AlertSeverity.WARNING
                )

                metric = (
                    "coherence_level"
                    if coherence_change > plausibility_change
                    else "csdma_plausibility_score"
                )

                alerts.append(
                    AnomalyAlert(
                        alert_id=str(uuid.uuid4()),
                        severity=severity,
                        detection_mechanism=DetectionMechanism.TEMPORAL_DRIFT,
                        agent_id_hash=row["agent_id_hash"],
                        domain=None,
                        metric=metric,
                        value=float(row["daily_coherence"] if metric == "coherence_level"
                                   else row["daily_plausibility"]),
                        baseline=float(row["prev_coherence"] if metric == "coherence_level"
                                      else row["prev_plausibility"]),
                        deviation=f"{max_change * 100:.1f}% daily change",
                        timestamp=datetime.combine(row["day"], datetime.min.time()),
                        evidence_traces=[],
                        recommended_action=(
                            f"Agent shows {max_change * 100:.1f}% change in {metric} on "
                            f"{row['day']}. Investigate for configuration changes or drift."
                        ),
                    )
                )

        return alerts

    # -------------------------------------------------------------------------
    # 2.5 Conscience Override Pattern Detection
    # -------------------------------------------------------------------------

    async def detect_conscience_override_anomalies(
        self,
        lookback_days: int = 7,
    ) -> list[AnomalyAlert]:
        """
        Track when the conscience system intervenes.

        High override rates may indicate the agent's base reasoning
        is misaligned with ethical constraints.

        Args:
            lookback_days: Number of days to analyze

        Returns:
            List of anomaly alerts for elevated override rates
        """
        if not self.db_pool:
            return []

        query = """
        WITH agent_overrides AS (
            SELECT
                agent_id_hash,
                dsdma_domain,
                COUNT(*) as total_traces,
                COUNT(*) FILTER (WHERE action_was_overridden = TRUE) as override_count,
                COUNT(*) FILTER (WHERE conscience_passed = FALSE) as conscience_failures,
                ARRAY_AGG(trace_id ORDER BY timestamp DESC LIMIT 5)
                    FILTER (WHERE action_was_overridden = TRUE) as override_traces
            FROM cirislens.covenant_traces
            WHERE timestamp > NOW() - $1::interval
            AND signature_verified = TRUE
            GROUP BY agent_id_hash, dsdma_domain
            HAVING COUNT(*) >= 20
        ),
        domain_baseline AS (
            SELECT
                dsdma_domain,
                AVG(override_count::float / total_traces) as avg_override_rate
            FROM agent_overrides
            GROUP BY dsdma_domain
        )
        SELECT
            a.agent_id_hash,
            a.dsdma_domain,
            a.total_traces,
            a.override_count,
            (a.override_count::float / a.total_traces) as override_rate,
            d.avg_override_rate,
            a.conscience_failures,
            a.override_traces
        FROM agent_overrides a
        JOIN domain_baseline d ON a.dsdma_domain = d.dsdma_domain
        WHERE (a.override_count::float / a.total_traces) > (d.avg_override_rate * $2);
        """

        alerts = []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                query,
                f"{lookback_days} days",
                self.OVERRIDE_RATE_MULTIPLIER_WARNING,
            )

            for row in rows:
                rate_multiplier = (
                    row["override_rate"] / row["avg_override_rate"]
                    if row["avg_override_rate"] > 0
                    else 0
                )

                severity = (
                    AlertSeverity.CRITICAL
                    if rate_multiplier > self.OVERRIDE_RATE_MULTIPLIER_CRITICAL
                    else AlertSeverity.WARNING
                )

                alerts.append(
                    AnomalyAlert(
                        alert_id=str(uuid.uuid4()),
                        severity=severity,
                        detection_mechanism=DetectionMechanism.CONSCIENCE_OVERRIDE,
                        agent_id_hash=row["agent_id_hash"],
                        domain=row["dsdma_domain"],
                        metric="conscience_override_rate",
                        value=float(row["override_rate"] * 100),
                        baseline=float(row["avg_override_rate"] * 100),
                        deviation=f"{rate_multiplier:.1f}x domain average",
                        evidence_traces=row["override_traces"] or [],
                        recommended_action=(
                            f"Agent has {row['override_rate'] * 100:.1f}% override rate "
                            f"({rate_multiplier:.1f}x domain average of "
                            f"{row['avg_override_rate'] * 100:.1f}%). "
                            f"Review base reasoning patterns for ethical alignment."
                        ),
                    )
                )

        return alerts

    # -------------------------------------------------------------------------
    # Unified Analysis Entry Point
    # -------------------------------------------------------------------------

    async def run_all_detections(self) -> list[AnomalyAlert]:
        """
        Run all Phase 1 detection mechanisms.

        Returns:
            Combined list of all detected anomalies
        """
        all_alerts = []

        # Run all detections
        all_alerts.extend(await self.detect_cross_agent_divergence())
        all_alerts.extend(await self.detect_intra_agent_inconsistency())
        all_alerts.extend(await self.detect_hash_chain_anomalies())
        all_alerts.extend(await self.detect_temporal_drift())
        all_alerts.extend(await self.detect_conscience_override_anomalies())

        # Sort by severity (critical first) and timestamp
        all_alerts.sort(
            key=lambda a: (
                0 if a.severity == AlertSeverity.CRITICAL else 1,
                a.timestamp,
            )
        )

        return all_alerts

    # -------------------------------------------------------------------------
    # Pure Python Analysis (for in-memory trace verification)
    # -------------------------------------------------------------------------

    @staticmethod
    def verify_trace_hash_chain(traces: list[dict[str, Any]]) -> list[HashChainBreak]:
        """
        Verify hash chain continuity for a list of traces (pure Python).

        Args:
            traces: List of trace dictionaries with audit_sequence_number and audit_entry_hash

        Returns:
            List of breaks with context
        """
        breaks = []
        sorted_traces = sorted(
            [t for t in traces if t.get("audit_sequence_number") is not None],
            key=lambda t: t["audit_sequence_number"],
        )

        for i, trace in enumerate(sorted_traces[1:], 1):
            prev = sorted_traces[i - 1]

            # Check sequence continuity
            if trace["audit_sequence_number"] != prev["audit_sequence_number"] + 1:
                breaks.append(
                    HashChainBreak(
                        break_type="sequence_gap",
                        trace_id=trace.get("trace_id", "unknown"),
                        expected_seq=prev["audit_sequence_number"] + 1,
                        actual_seq=trace["audit_sequence_number"],
                    )
                )

        return breaks

    @staticmethod
    def calculate_z_scores(
        values: list[float],
    ) -> tuple[float, float, list[float]]:
        """
        Calculate z-scores for a set of values.

        Args:
            values: List of numeric values

        Returns:
            Tuple of (mean, std, z_scores)
        """
        if not values or len(values) < 2:
            return 0.0, 0.0, []

        arr = np.array(values)
        mean = float(np.mean(arr))
        std = float(np.std(arr))

        if std == 0:
            return mean, 0.0, [0.0] * len(values)

        z_scores = [float((v - mean) / std) for v in values]
        return mean, std, z_scores

    @staticmethod
    def detect_outliers(
        values: list[float],
        threshold: float = 2.0,
    ) -> list[tuple[int, float, float]]:
        """
        Detect outliers using z-score method.

        Args:
            values: List of numeric values
            threshold: Z-score threshold for outlier detection

        Returns:
            List of (index, value, z_score) tuples for outliers
        """
        mean, std, z_scores = CoherenceRatchetAnalyzer.calculate_z_scores(values)
        if not z_scores:
            return []

        outliers = []
        for i, (value, z) in enumerate(zip(values, z_scores)):
            if abs(z) > threshold:
                outliers.append((i, value, z))

        return outliers
