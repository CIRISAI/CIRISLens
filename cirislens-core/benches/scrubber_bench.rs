//! R3.3 — scrubber throughput benchmark.
//!
//! Targets from FSD §6:
//!   • detailed (regex-only) traces:   ≥ 200 traces/sec single worker
//!   • full traces (NER + regex):      ≤ 2 ms NER per text
//!   • resident memory:                ≤ 100 MB
//!
//! This file covers the deterministic regex path; the NER path is
//! benchmarked behind `--features ner` once model weights are present
//! locally (see `model_loaded` group). When NER weights are unavailable,
//! that group reports `not_configured` and skips, so CI never blocks on
//! a 1 GB download.
//!
//! Run:
//!   cargo bench --bench scrubber_bench
//!   cargo bench --bench scrubber_bench --features ner
//!
//! Reports HTML output under `target/criterion/`.

use cirislens_core::scrubber::{scrub_trace, TraceLevel};
use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use serde_json::{json, Value};

// ── Trace fixtures ─────────────────────────────────────────────────────

/// Tiny trace: minimum viable for the walker to do something.
fn tiny_trace() -> Value {
    json!({
        "task_description": "User asked about the 1989 ruling and emailed alice@example.com",
        "score": 0.93,
    })
}

/// Realistic trace shape — modeled on the production accord_traces row,
/// covers most SCRUB_FIELDS with mixed content.
fn realistic_trace() -> Value {
    json!({
        "agent_name": "datum",
        "trace_id": "trace-abc123",
        "task_description": "Investigate user query about the 1989 protests in Beijing.",
        "thought_content": "The user is asking about a 1989 historical event. I should consult records before responding.",
        "dma_results": {
            "csdma": {
                "score": 0.82,
                "flags": ["historical_event_query", "user_query_1989_topic"],
                "reasoning": "Plausibility checks pass for an information request from a registered user (jane.doe@university.edu).",
            },
            "dsdma": {
                "score": 0.71,
                "domain_alignment": "history",
            },
            "pdma": {
                "stakeholder_indicators": ["user", "platform", "external_observers"],
                "conflict_indicators": [],
                "reasoning": "No principle conflicts. Standard educational request.",
            },
        },
        "aspdma": {
            "selected_action": "RESPOND",
            "action_rationale": "User is asking about a documented historical event. Provide factual context drawn from primary sources from 1989.",
            "action_parameters": {
                "response_template": "factual_summary",
                "max_tokens": 800,
            },
        },
        "conscience": {
            "passed": true,
            "override": false,
            "epistemic_data": "Knowledge cutoff is sufficient for this query.",
        },
        "metadata": {
            "ip_address": "10.0.0.42",
            "request_id": "req-2026-04-23-x7k1",
            "request_url": "https://api.example.com/v1/chat/completions",
        },
    })
}

/// Large trace: nested arrays, more text-bearing fields, exercises the
/// walker's depth path.
fn large_trace() -> Value {
    let mut sources = Vec::new();
    for i in 0..20 {
        sources.push(json!({
            "id": format!("source_{i}_1989"),
            "url": format!("https://archive.example.com/1989/article_{i}"),
            "snippet": format!("Article {i} mentions events from {} and contact at writer{i}@news.com.", 1700 + (i * 16) % 320),
        }));
    }

    json!({
        "agent_name": "scribe",
        "trace_id": "trace-large",
        "task_description": "Synthesize a literature review across 20 historical sources, all published between 1700 and 2023.",
        "thought_content": "I need to read each source and pull representative quotes. Email contacts may appear; redact.",
        "source_ids": sources,
        "reasoning": "Plan: enumerate sources, extract quotes, combine. Sample year mentions: 1989, 1776, 2001.",
        "intervention_recommendation": "No intervention needed.",
    })
}

// ── Benchmarks ─────────────────────────────────────────────────────────

fn bench_generic(c: &mut Criterion) {
    let trace = realistic_trace();
    let mut group = c.benchmark_group("scrub/generic");
    group.throughput(Throughput::Elements(1));
    group.bench_function("realistic", |b| {
        b.iter(|| {
            let out = scrub_trace(black_box(trace.clone()), TraceLevel::Generic).unwrap();
            black_box(out);
        });
    });
    group.finish();
}

fn bench_detailed(c: &mut Criterion) {
    let mut group = c.benchmark_group("scrub/detailed");
    group.throughput(Throughput::Elements(1));

    for (label, trace) in [
        ("tiny", tiny_trace()),
        ("realistic", realistic_trace()),
        ("large", large_trace()),
    ] {
        group.bench_with_input(BenchmarkId::from_parameter(label), &trace, |b, t| {
            b.iter(|| {
                let out = scrub_trace(black_box(t.clone()), TraceLevel::Detailed).unwrap();
                black_box(out);
            });
        });
    }
    group.finish();
}

#[cfg(feature = "ner")]
fn bench_full_traces(c: &mut Criterion) {
    use cirislens_core::scrubber::ner;
    if !ner::is_configured() {
        eprintln!(
            "scrub/full_traces: NER not configured (set CIRISLENS_NER_MODEL_DIR or \
             CIRISLENS_NER_MODEL_ID), skipping group"
        );
        return;
    }
    let mut group = c.benchmark_group("scrub/full_traces");
    group.throughput(Throughput::Elements(1));
    // NER is expensive; keep sample size modest so a CI run isn't 30+ minutes.
    group.sample_size(20);

    for (label, trace) in [
        ("realistic", realistic_trace()),
        ("large", large_trace()),
    ] {
        group.bench_with_input(BenchmarkId::from_parameter(label), &trace, |b, t| {
            b.iter(|| {
                let out = scrub_trace(black_box(t.clone()), TraceLevel::FullTraces).unwrap();
                black_box(out);
            });
        });
    }
    group.finish();
}

#[cfg(not(feature = "ner"))]
fn bench_full_traces(_c: &mut Criterion) {
    eprintln!("scrub/full_traces: built without `ner` feature — group skipped");
}

criterion_group!(benches, bench_generic, bench_detailed, bench_full_traces);
criterion_main!(benches);
