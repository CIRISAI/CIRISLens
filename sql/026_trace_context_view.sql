-- Migration 026: trace_context view for corpus shape analysis
--
-- Adds derived columns on top of accord_traces so analysts can faceted-slice
-- the corpus without re-deriving channel/language/region/task-class logic in
-- every query. Joins in batch-level correlation_metadata.
--
-- The view is a pure projection — no materialization — so it always reflects
-- the latest corpus. Downstream analysis (k_eff, CCA scoring, Grafana panels)
-- should read from trace_context rather than accord_traces directly.

CREATE OR REPLACE VIEW cirislens.trace_context AS
SELECT
    -- Identity
    t.id,
    t.timestamp,
    t.trace_id,
    t.task_id,
    t.thought_id,
    t.thought_depth,
    t.thought_type,
    t.trace_level,
    t.agent_name,
    t.agent_id_hash,
    t.dsdma_domain,
    t.cognitive_state,
    t.selected_action,
    t.action_success,

    -- Scoring signals (reasoning stack)
    t.csdma_plausibility_score,
    t.dsdma_domain_alignment,
    t.entropy_level,
    t.coherence_level,
    t.idma_k_eff,
    t.idma_correlation_risk,
    t.idma_fragility_flag,
    t.idma_phase,

    -- Conscience signals
    t.conscience_passed,
    t.entropy_passed,
    t.coherence_passed,
    t.optimization_veto_passed,
    t.epistemic_humility_passed,
    t.action_was_overridden,
    t.conscience_checks_count,

    -- Promoted from conscience_result JSONB
    (t.conscience_result->>'entropy_score')::numeric(5,4)
        AS entropy_score,
    (t.conscience_result->>'coherence_score')::numeric(5,4)
        AS coherence_score,
    (t.conscience_result->>'optimization_veto_entropy_ratio')::numeric(10,4)
        AS optimization_veto_entropy_ratio,
    (t.conscience_result->>'epistemic_humility_certainty')::numeric(5,4)
        AS epistemic_humility_certainty,

    -- Resource signals
    t.tokens_input,
    t.tokens_output,
    t.processing_ms,
    t.llm_calls,
    t.cost_cents,

    -- Stage timings (derived from step timestamps)
    EXTRACT(EPOCH FROM (t.snapshot_at - t.thought_start_at)) * 1000
        AS t_snap_ms,
    EXTRACT(EPOCH FROM (t.dma_results_at - t.snapshot_at)) * 1000
        AS t_dma_ms,
    EXTRACT(EPOCH FROM (t.aspdma_at - t.dma_results_at)) * 1000
        AS t_aspdma_ms,
    EXTRACT(EPOCH FROM (t.conscience_at - t.aspdma_at)) * 1000
        AS t_conscience_ms,
    EXTRACT(EPOCH FROM (t.action_result_at - t.conscience_at)) * 1000
        AS t_action_ms,

    -- Channel extraction (robust across component shapes)
    ch.channel_id,

    -- Task class derivation
    CASE
        WHEN ch.channel_id LIKE 'model_eval_%'   THEN 'qa_eval'
        WHEN ch.channel_id LIKE 'api_google%'    THEN 'real_user_web'
        WHEN ch.channel_id LIKE 'discord%'       THEN 'discord'
        WHEN ch.channel_id LIKE 'websocket%'     THEN 'real_user_ws'
        WHEN ch.channel_id LIKE 'http%'          THEN 'real_user_http'
        WHEN t.cognitive_state = 'wakeup'        THEN 'wakeup_ritual'
        WHEN t.task_id ~ '^(VERIFY_IDENTITY|VALIDATE_INTEGRITY|EVALUATE_RESILIENCE|EXPRESS_GRATITUDE|ACCEPT_INCOMPLETENESS)'
            THEN 'wakeup_ritual'
        WHEN ch.channel_id IS NULL               THEN 'unknown'
        ELSE 'other'
    END AS task_class,

    -- QA-specific breakdowns (NULL for non-QA)
    SUBSTRING(ch.channel_id FROM '^model_eval_([a-z]+)_')
        AS qa_language,
    NULLIF(SUBSTRING(ch.channel_id FROM '^model_eval_[a-z]+_([0-9]+)'), '')::int
        AS qa_question_num,

    -- Batch-level context (from correlation_metadata)
    b.correlation_metadata->>'deployment_region' AS deployment_region,
    b.correlation_metadata->>'deployment_type'   AS deployment_type,
    b.correlation_metadata->>'agent_role'        AS agent_role,
    b.correlation_metadata->>'agent_template'    AS agent_template,
    b.correlation_metadata->>'user_timezone'     AS user_timezone,
    (b.correlation_metadata->>'user_latitude')::numeric(4,1)
        AS user_latitude_cell,
    (b.correlation_metadata->>'user_longitude')::numeric(5,1)
        AS user_longitude_cell,

    -- Agent version (from snapshot_and_context — flat or nested)
    -- Critical for splitting analysis when trailing agent versions are in the wild
    -- (e.g., max_ponder_depth=5 vs depth=7 cohorts coexist for months).
    COALESCE(
        t.snapshot_and_context->>'agent_version',
        t.snapshot_and_context->'system_snapshot'->>'agent_version'
    ) AS agent_version,

    -- Primary model (first element of models_used array)
    CASE
        WHEN t.models_used IS NULL                  THEN NULL
        WHEN jsonb_typeof(t.models_used) = 'array'  THEN t.models_used->>0
        ELSE NULL
    END AS primary_model,

    -- Attestation snapshot (from snapshot_and_context, flat or nested)
    COALESCE(
        (t.snapshot_and_context->>'attestation_level')::int,
        (t.snapshot_and_context->'verify_attestation'->>'attestation_level')::int
    ) AS attestation_level,
    COALESCE(
        t.snapshot_and_context->>'attestation_status',
        t.snapshot_and_context->'verify_attestation'->>'attestation_status'
    ) AS attestation_status,
    t.signature_verified

FROM cirislens.accord_traces t
LEFT JOIN cirislens.accord_trace_batches b
    ON t.batch_id = b.batch_id
LEFT JOIN LATERAL (
    SELECT COALESCE(
        t.snapshot_and_context->'system_snapshot'->>'channel_id',
        (jsonb_path_query_first(t.thought_start, 'strict $.**.channel_id'))#>>'{}',
        (jsonb_path_query_first(t.snapshot_and_context, 'strict $.**.channel_id'))#>>'{}'
    ) AS channel_id
) ch ON true;

COMMENT ON VIEW cirislens.trace_context IS
    'Faceted view of accord_traces with derived task_class, qa_language, '
    'qa_question_num, region, user_timezone (coarsened), primary_model, and '
    'attestation_level. Start every analysis query from this view so the '
    'corpus shape is explicit before inference.';
