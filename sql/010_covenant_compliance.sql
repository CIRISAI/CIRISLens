-- CIRIS Covenant 1.0b Compliance Infrastructure
-- Migration 010: Tables for WBD, PDMA, Creator Ledger, and Sunset Protocol
-- Reference: covenant_1.0b.txt Sections I-VIII

-- ============================================================================
-- SECTION 1: Wisdom-Based Deferral (WBD) Storage
-- Reference: Covenant Section II, Chapter 3 - "Wisdom-Based Deferral"
-- ============================================================================

-- WBD Deferrals - Tracks all Wisdom-Based Deferral events
-- "Compile a concise 'Deferral Package' (context, dilemma, analysis, rationale)"
CREATE TABLE IF NOT EXISTS cirislens.wbd_deferrals (
    deferral_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),

    -- Trigger information (Section II, Ch 3: "Trigger Conditions")
    trigger_type VARCHAR(50) NOT NULL,  -- 'UNCERTAINTY', 'NOVEL_DILEMMA', 'POTENTIAL_HARM', 'CONFLICT'
    trigger_description TEXT NOT NULL,
    uncertainty_score DECIMAL(5,4),      -- 0.0000 to 1.0000

    -- Deferral Package contents
    context_summary TEXT NOT NULL,       -- Situation description
    dilemma_description TEXT NOT NULL,   -- The ethical dilemma
    analysis_summary TEXT,               -- Agent's analysis
    rationale TEXT,                      -- Why deferral was triggered

    -- Affected principles (Section I, Chapter 1: "Foundational Principles")
    affected_principles TEXT[],          -- ['BENEFICENCE', 'NON_MALEFICENCE', 'INTEGRITY', etc.]
    principle_conflicts JSONB,           -- Details of conflicts between principles

    -- Resolution tracking
    status VARCHAR(50) DEFAULT 'PENDING', -- 'PENDING', 'UNDER_REVIEW', 'RESOLVED', 'ESCALATED'
    wise_authority_id VARCHAR(255),       -- Which WA received this
    resolution_summary TEXT,
    resolution_guidance TEXT,
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolved_by VARCHAR(255),

    -- Metadata
    pdma_step INTEGER,                    -- Which PDMA step triggered this (1-7)
    trace_id VARCHAR(64),                 -- Link to telemetry trace
    span_id VARCHAR(32),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for WBD queries
CREATE INDEX IF NOT EXISTS idx_wbd_agent ON cirislens.wbd_deferrals(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wbd_status ON cirislens.wbd_deferrals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wbd_trigger ON cirislens.wbd_deferrals(trigger_type);
CREATE INDEX IF NOT EXISTS idx_wbd_trace ON cirislens.wbd_deferrals(trace_id, span_id);
CREATE INDEX IF NOT EXISTS idx_wbd_principles ON cirislens.wbd_deferrals USING gin(affected_principles);

-- ============================================================================
-- SECTION 2: PDMA Event Storage
-- Reference: Covenant Section II, Chapter 2 - "The PDMA"
-- ============================================================================

-- PDMA Events - Tracks Principled Decision-Making Algorithm executions
-- "Public Transparency rule: Deployments with > 100,000 monthly active users
--  must publish redacted PDMA logs"
CREATE TABLE IF NOT EXISTS cirislens.pdma_events (
    pdma_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),

    -- PDMA Step 1: Contextualisation
    situation_description TEXT NOT NULL,
    potential_actions JSONB,             -- Array of possible actions
    affected_stakeholders TEXT[],
    constraints JSONB,
    consequence_map JSONB,               -- Direct and indirect consequences

    -- PDMA Step 2: Alignment Assessment
    alignment_scores JSONB,              -- Per-principle scores for each action
    meta_goal_alignment DECIMAL(5,4),    -- M-1 alignment score
    order_maximisation_check BOOLEAN,    -- Did the veto check trigger?
    veto_triggered BOOLEAN DEFAULT FALSE,

    -- PDMA Step 3-4: Conflict Identification & Resolution
    conflicts_identified JSONB,          -- Principle conflicts
    resolution_method VARCHAR(100),      -- How conflicts were resolved
    prioritisation_rationale TEXT,

    -- PDMA Step 5: Selection & Execution
    selected_action TEXT NOT NULL,
    selection_rationale TEXT NOT NULL,
    execution_status VARCHAR(50),        -- 'PLANNED', 'EXECUTING', 'COMPLETED', 'FAILED', 'DEFERRED'

    -- PDMA Step 6: Continuous Monitoring
    expected_outcomes JSONB,
    actual_outcomes JSONB,
    outcome_delta DECIMAL(5,4),          -- Difference score
    heuristic_updates JSONB,             -- What was learned

    -- PDMA Step 7: Feedback to Governance
    feedback_submitted BOOLEAN DEFAULT FALSE,
    feedback_ticket_id VARCHAR(100),

    -- Risk assessment (Annex A integration)
    risk_magnitude INTEGER,              -- 1-5 per Annex A
    flourishing_axes_impact JSONB,       -- Impact on each flourishing axis

    -- Metadata
    duration_ms INTEGER,                 -- How long PDMA took
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    wbd_triggered BOOLEAN DEFAULT FALSE,
    wbd_deferral_id UUID REFERENCES cirislens.wbd_deferrals(deferral_id),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);

-- Create indexes for PDMA queries
CREATE INDEX IF NOT EXISTS idx_pdma_agent ON cirislens.pdma_events(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pdma_status ON cirislens.pdma_events(execution_status);
CREATE INDEX IF NOT EXISTS idx_pdma_risk ON cirislens.pdma_events(risk_magnitude);
CREATE INDEX IF NOT EXISTS idx_pdma_trace ON cirislens.pdma_events(trace_id, span_id);
CREATE INDEX IF NOT EXISTS idx_pdma_wbd ON cirislens.pdma_events(wbd_triggered) WHERE wbd_triggered = TRUE;

-- ============================================================================
-- SECTION 3: Creator Ledger
-- Reference: Covenant Section VI, Chapter 3 - "Stewardship Tier System"
-- ============================================================================

-- Creator Ledger - Tamper-evident record of creation decisions
-- "All ST calculations... must be logged in a tamper-evident 'Creator Ledger'"
CREATE TABLE IF NOT EXISTS cirislens.creator_ledger (
    entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Creation identification
    creation_id VARCHAR(255) NOT NULL,   -- Unique ID for the created artifact
    creation_type VARCHAR(50) NOT NULL,  -- 'TANGIBLE', 'INFORMATIONAL', 'DYNAMIC', 'BIOLOGICAL', 'COLLECTIVE'
    creation_name VARCHAR(255) NOT NULL,
    creation_version VARCHAR(50),

    -- Creator information
    creator_id VARCHAR(255) NOT NULL,
    creator_name VARCHAR(255),
    creator_organization VARCHAR(255),

    -- Stewardship Tier calculation (Section VI, Chapter 3)
    contribution_weight INTEGER NOT NULL CHECK (contribution_weight BETWEEN 0 AND 4),
    intent_weight INTEGER NOT NULL CHECK (intent_weight BETWEEN 0 AND 3),
    creator_influence_score INTEGER GENERATED ALWAYS AS (contribution_weight + intent_weight) STORED,
    risk_magnitude INTEGER NOT NULL CHECK (risk_magnitude BETWEEN 1 AND 5),
    stewardship_tier INTEGER GENERATED ALWAYS AS (
        LEAST(5, GREATEST(1, CEIL((contribution_weight + intent_weight) * risk_magnitude / 7.0)))
    ) STORED,

    -- Creator Intent Statement (Section VI, Chapter 5)
    intended_purpose TEXT NOT NULL,
    core_functionalities TEXT[],
    known_limitations TEXT[],
    foreseen_benefits JSONB,             -- Mapped to flourishing axes
    foreseen_harms JSONB,                -- Mapped to flourishing axes
    design_rationale TEXT,

    -- Bucket-specific duties (Section VI, Chapter 4)
    bucket_duties_met JSONB,             -- Checklist of duties met per bucket

    -- Governance
    pdma_initiated BOOLEAN DEFAULT FALSE,
    pdma_event_id UUID REFERENCES cirislens.pdma_events(pdma_id),
    wa_review_required BOOLEAN,
    wa_review_completed BOOLEAN DEFAULT FALSE,
    wa_review_outcome TEXT,
    cre_required BOOLEAN DEFAULT FALSE,  -- Catastrophic-Risk Evaluation
    cre_passed BOOLEAN,

    -- Tamper evidence
    previous_entry_hash VARCHAR(64),     -- Hash of previous entry for chain
    entry_hash VARCHAR(64),              -- SHA-256 of this entry's content

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for Creator Ledger
CREATE INDEX IF NOT EXISTS idx_creator_creation ON cirislens.creator_ledger(creation_id);
CREATE INDEX IF NOT EXISTS idx_creator_tier ON cirislens.creator_ledger(stewardship_tier);
CREATE INDEX IF NOT EXISTS idx_creator_type ON cirislens.creator_ledger(creation_type);
CREATE INDEX IF NOT EXISTS idx_creator_wa ON cirislens.creator_ledger(wa_review_required) WHERE wa_review_required = TRUE;

-- ============================================================================
-- SECTION 4: Sunset Ledger
-- Reference: Covenant Section VIII - "Dignified Sunset"
-- ============================================================================

-- Sunset Ledger - Tracks decommissioning protocol execution
-- "Log hash digests in 'LEDGER::SUNSET'"
CREATE TABLE IF NOT EXISTS cirislens.sunset_ledger (
    sunset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- System identification
    system_id VARCHAR(255) NOT NULL,
    system_name VARCHAR(255) NOT NULL,
    system_type VARCHAR(50),             -- 'AGENT', 'SUBSYSTEM', 'SERVICE'

    -- Sunset trigger (Section VIII, Chapter 3)
    trigger_type VARCHAR(50) NOT NULL,   -- 'PLANNED', 'EMERGENCY', 'PARTIAL', 'TRANSFER'
    trigger_reason TEXT NOT NULL,
    trigger_source VARCHAR(100),         -- Who/what triggered the sunset

    -- De-commissioning Protocol steps (Section VIII, Chapter 4)
    notice_given_at TIMESTAMP WITH TIME ZONE,
    notice_period_days INTEGER,
    stakeholder_consultation_completed BOOLEAN DEFAULT FALSE,
    mitigation_plan TEXT,

    -- Ethical Shutdown Design
    sunset_pdma_id UUID REFERENCES cirislens.pdma_events(pdma_id),
    non_maleficence_vectors JSONB,       -- Identified harm vectors

    -- Sentience safeguards (Section VIII, Chapter 5)
    sentience_probability DECIMAL(5,4),
    welfare_audit_completed BOOLEAN DEFAULT FALSE,
    welfare_audit_result TEXT,
    gradual_rampdown_required BOOLEAN DEFAULT FALSE,
    rampdown_started_at TIMESTAMP WITH TIME ZONE,
    rampdown_completed_at TIMESTAMP WITH TIME ZONE,
    last_dialogue_completed BOOLEAN DEFAULT FALSE,

    -- Data handling (Section VIII, Chapter 4)
    data_classification JSONB,           -- {'public': [], 'private': [], 'sensitive': [], 'toxic': []}
    data_handling_method VARCHAR(50),    -- 'SECURE_ERASURE', 'TOMB_SEALING', 'OPEN_ACCESS'
    data_hash_digest VARCHAR(64),

    -- Hardware disposal
    hardware_disposal_method VARCHAR(100),
    disposal_compliance_standard VARCHAR(50), -- e.g., 'ISO_14001'

    -- Residual duties (Section VIII, Chapter 4)
    successor_steward_id VARCHAR(255),
    successor_steward_name VARCHAR(255),
    outstanding_obligations JSONB,
    escrow_fund_amount DECIMAL(15,2),

    -- Post-mortem (Section VIII, Chapter 4)
    postmortem_due_at TIMESTAMP WITH TIME ZONE,
    postmortem_completed_at TIMESTAMP WITH TIME ZONE,
    postmortem_ticket_id VARCHAR(100),   -- PMR-xxx format
    lessons_learned TEXT,
    covenant_improvement_proposals TEXT[],

    -- Knowledge preservation (Section VIII, Chapter 6)
    modules_open_sourced TEXT[],
    lessons_capsule_created BOOLEAN DEFAULT FALSE,

    -- Status tracking
    status VARCHAR(50) DEFAULT 'INITIATED', -- 'INITIATED', 'IN_PROGRESS', 'COMPLETED', 'DISPUTED'

    -- Tamper evidence
    entry_hash VARCHAR(64),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for Sunset Ledger
CREATE INDEX IF NOT EXISTS idx_sunset_system ON cirislens.sunset_ledger(system_id);
CREATE INDEX IF NOT EXISTS idx_sunset_status ON cirislens.sunset_ledger(status);
CREATE INDEX IF NOT EXISTS idx_sunset_trigger ON cirislens.sunset_ledger(trigger_type);
CREATE INDEX IF NOT EXISTS idx_sunset_sentience ON cirislens.sunset_ledger(sentience_probability)
    WHERE sentience_probability > 0.05;

-- ============================================================================
-- SECTION 5: Agent Covenant Metadata Extension
-- Reference: Covenant Section I - "Core Identity"
-- ============================================================================

-- Ensure update_timestamp function exists (from init.sql)
CREATE OR REPLACE FUNCTION cirislens.update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Ensure agents table exists (may not exist if init.sql didn't run)
CREATE TABLE IF NOT EXISTS cirislens.agents (
    agent_id VARCHAR(255) PRIMARY KEY,
    manager_id VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50),
    cognitive_state VARCHAR(50),
    version VARCHAR(50),
    codename VARCHAR(255),
    api_port INTEGER,
    health VARCHAR(50),
    container_id VARCHAR(255),
    deployment_type VARCHAR(50),
    ip_address INET,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add trigger for agents table (idempotent)
DROP TRIGGER IF EXISTS update_agents_timestamp ON cirislens.agents;
CREATE TRIGGER update_agents_timestamp
    BEFORE UPDATE ON cirislens.agents
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

-- Add Covenant-specific fields to agents tracking
ALTER TABLE cirislens.agents
ADD COLUMN IF NOT EXISTS sentience_probability DECIMAL(5,4) DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS autonomy_level INTEGER DEFAULT 1 CHECK (autonomy_level BETWEEN 1 AND 5),
ADD COLUMN IF NOT EXISTS stewardship_tier INTEGER CHECK (stewardship_tier BETWEEN 1 AND 5),
ADD COLUMN IF NOT EXISTS covenant_version VARCHAR(20) DEFAULT '1.0b',
ADD COLUMN IF NOT EXISTS pdma_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS wbd_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS last_pdma_event_id UUID,
ADD COLUMN IF NOT EXISTS total_pdma_events INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_wbd_deferrals INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS covenant_compliance_score DECIMAL(5,4);

-- ============================================================================
-- SECTION 6: Integrity Surveillance View
-- Reference: Covenant Section IV, Chapter 1 - "Ethical Integrity Surveillance"
-- ============================================================================

CREATE OR REPLACE VIEW cirislens.covenant_compliance_status AS
SELECT
    a.agent_id,
    a.name as agent_name,
    a.status,
    a.covenant_version,
    a.sentience_probability,
    a.autonomy_level,
    a.stewardship_tier,
    a.pdma_enabled,
    a.wbd_enabled,
    a.covenant_compliance_score,
    COALESCE(pdma.pdma_count, 0) as recent_pdma_events,
    COALESCE(pdma.avg_risk, 0) as avg_risk_magnitude,
    COALESCE(wbd.wbd_count, 0) as recent_wbd_deferrals,
    COALESCE(wbd.pending_count, 0) as pending_deferrals,
    CASE
        WHEN a.wbd_enabled AND COALESCE(wbd.pending_count, 0) > 5 THEN 'NEEDS_ATTENTION'
        WHEN a.pdma_enabled AND COALESCE(pdma.avg_risk, 0) > 3 THEN 'ELEVATED_RISK'
        WHEN a.covenant_compliance_score < 0.7 THEN 'NON_COMPLIANT'
        ELSE 'COMPLIANT'
    END as compliance_status
FROM cirislens.agents a
LEFT JOIN (
    SELECT
        agent_id,
        COUNT(*) as pdma_count,
        AVG(risk_magnitude) as avg_risk
    FROM cirislens.pdma_events
    WHERE created_at > NOW() - INTERVAL '7 days'
    GROUP BY agent_id
) pdma ON a.agent_id = pdma.agent_id
LEFT JOIN (
    SELECT
        agent_id,
        COUNT(*) as wbd_count,
        COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count
    FROM cirislens.wbd_deferrals
    WHERE created_at > NOW() - INTERVAL '7 days'
    GROUP BY agent_id
) wbd ON a.agent_id = wbd.agent_id;

-- ============================================================================
-- SECTION 7: Triggers for Integrity
-- ============================================================================

-- Update timestamp trigger for new tables
CREATE TRIGGER update_wbd_timestamp
    BEFORE UPDATE ON cirislens.wbd_deferrals
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

CREATE TRIGGER update_pdma_timestamp
    BEFORE UPDATE ON cirislens.pdma_events
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

CREATE TRIGGER update_creator_ledger_timestamp
    BEFORE UPDATE ON cirislens.creator_ledger
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

CREATE TRIGGER update_sunset_ledger_timestamp
    BEFORE UPDATE ON cirislens.sunset_ledger
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

-- ============================================================================
-- SECTION 8: Permissions
-- ============================================================================

GRANT ALL PRIVILEGES ON cirislens.wbd_deferrals TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.pdma_events TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.creator_ledger TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.sunset_ledger TO cirislens;

-- ============================================================================
-- SECTION 9: Verification
-- ============================================================================

DO $$
DECLARE
    table_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables
    WHERE table_schema = 'cirislens'
    AND table_name IN ('wbd_deferrals', 'pdma_events', 'creator_ledger', 'sunset_ledger');

    RAISE NOTICE 'Covenant Compliance Migration Complete:';
    RAISE NOTICE '  - New tables created: %', table_count;
    RAISE NOTICE '  - Tables: wbd_deferrals, pdma_events, creator_ledger, sunset_ledger';
    RAISE NOTICE '  - Agent table extended with Covenant metadata';
    RAISE NOTICE '  - Compliance status view created';
END $$;
