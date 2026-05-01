-- 027_widen_span_id.sql
-- Widen span_id columns from VARCHAR(32) to TEXT.
--
-- Background: bridge investigation 2026-05-01 found POST /wbd/deferrals
-- 500-erroring with `asyncpg.StringDataRightTruncationError: value too
-- long for type character varying(32)` masked behind starlette 0.27.x's
-- BaseHTTPMiddleware EndOfStream wrapper. Long-standing schema bug
-- (predates the persist cutover by months); only surfaced now because
-- agents started sending span_ids longer than 32 chars.
--
-- The original VARCHAR(32) reflects the OpenTelemetry classical
-- format (8-byte span id, 16 hex chars × 2 = 32). CIRIS agents
-- routinely produce richer span identifiers — UUIDs (36 chars),
-- prefixed hex strings, structured ids. Widening to TEXT removes the
-- truncation hazard without sacrificing query performance — Postgres
-- TEXT and VARCHAR(N) share the same storage / index codepath.
--
-- Same hazard exists on pdma_events.span_id (also VARCHAR(32)).
-- Widened in the same migration so we don't have to come back.

ALTER TABLE cirislens.wbd_deferrals
    ALTER COLUMN span_id TYPE TEXT;

ALTER TABLE cirislens.pdma_events
    ALTER COLUMN span_id TYPE TEXT;

-- Indexes on (trace_id, span_id) survive the type change but get
-- rebuilt with the new type — Postgres handles this automatically.
