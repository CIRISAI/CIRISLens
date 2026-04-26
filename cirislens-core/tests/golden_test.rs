//! R3.2 — golden corpus runner.
//!
//! Each pair `tests/golden/<level>/<name>.input.json` +
//! `tests/golden/<level>/<name>.expected.json` is a frozen contract:
//! running `scrub_trace(input, level)` must produce `expected` byte-for-byte
//! (after pretty-print canonicalization). Any drift fails the test —
//! intentional rule changes are pushed through by setting
//! `CIRISLENS_GOLDEN_REGENERATE=1`, which rewrites the `.expected.json`
//! files in-place; review the diff and commit.
//!
//! The `detailed/` corpus runs on the default build. The `full_traces/`
//! corpus is gated behind the `ner` feature and skips when the model
//! backend is not configured (no weights = no test, not a failure).
//!
//! Structure:
//!   tests/golden/
//!     detailed/
//!       <lang>_<scenario>.input.json
//!       <lang>_<scenario>.expected.json
//!     full_traces/
//!       (scaffolded — populated when NER weights are available)

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use cirislens_core::scrubber::{scrub_trace, TraceLevel};
use serde_json::Value;

fn corpus_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/golden")
}

/// Walk a level directory; return the (input, expected) pairs as
/// `(stem, input_path, expected_path)`. Returns empty if the directory
/// is missing — that's a scaffolded-but-empty state, not a failure.
/// Missing expected files are tolerated when `regenerate()` is true
/// (bootstrap path); otherwise they fail the test loudly.
fn pairs_in(dir: &Path) -> Vec<(String, PathBuf, PathBuf)> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };

    let regen = regenerate();
    let mut pairs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|s| s.to_str()) else {
            continue;
        };
        let Some(stem) = name.strip_suffix(".input.json") else {
            continue;
        };
        let expected = dir.join(format!("{stem}.expected.json"));
        assert!(
            expected.exists() || regen,
            "golden input has no .expected.json sibling: {}\n\
             Either author the expected output or set CIRISLENS_GOLDEN_REGENERATE=1.",
            path.display()
        );
        pairs.push((stem.to_string(), path, expected));
    }
    pairs.sort_by(|a, b| a.0.cmp(&b.0));
    pairs
}

fn read_json(p: &Path) -> Value {
    let bytes = fs::read(p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()));
    serde_json::from_slice(&bytes).unwrap_or_else(|e| panic!("parse {}: {e}", p.display()))
}

fn write_json(p: &Path, v: &Value) {
    let pretty = serde_json::to_string_pretty(v).unwrap();
    fs::write(p, pretty + "\n").unwrap_or_else(|e| panic!("write {}: {e}", p.display()));
}

fn regenerate() -> bool {
    env::var("CIRISLENS_GOLDEN_REGENERATE")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
}

fn run_level(level: TraceLevel, dir: &str) {
    let dir = corpus_root().join(dir);
    let pairs = pairs_in(&dir);
    if pairs.is_empty() {
        eprintln!(
            "golden_test: no pairs in {} (scaffold present? populate to activate)",
            dir.display()
        );
        return;
    }

    let mut failures = Vec::new();
    let regen = regenerate();

    for (stem, input_path, expected_path) in pairs {
        let input = read_json(&input_path);
        let scrubbed = match scrub_trace(input, level) {
            Ok(out) => out.value,
            Err(e) => {
                failures.push(format!("{stem}: scrub_trace errored: {e}"));
                continue;
            }
        };

        if regen {
            write_json(&expected_path, &scrubbed);
            eprintln!("regenerated {}", expected_path.display());
            continue;
        }

        let expected = read_json(&expected_path);
        if scrubbed != expected {
            // Provide a useful diff hint without dumping huge JSON.
            let scrubbed_pretty = serde_json::to_string_pretty(&scrubbed).unwrap();
            let expected_pretty = serde_json::to_string_pretty(&expected).unwrap();
            failures.push(format!(
                "{stem}: scrubbed != expected\n  expected: {}\n  scrubbed: {}\n  hint: \
                 set CIRISLENS_GOLDEN_REGENERATE=1 if the rule change is intentional",
                expected_pretty.lines().take(4).collect::<Vec<_>>().join(" / "),
                scrubbed_pretty.lines().take(4).collect::<Vec<_>>().join(" / "),
            ));
        }
    }

    assert!(failures.is_empty(), "golden corpus drift:\n{}", failures.join("\n"));
}

#[test]
fn golden_detailed() {
    run_level(TraceLevel::Detailed, "detailed");
}

/// Generic level — a sanity rail that the no-op pass-through is bytewise
/// stable across all of the detailed-tier inputs. (We don't keep a
/// separate generic corpus; the same inputs serve.)
#[test]
fn golden_generic_passes_through() {
    let dir = corpus_root().join("detailed");
    for (stem, input_path, _) in pairs_in(&dir) {
        let input = read_json(&input_path);
        let out = scrub_trace(input.clone(), TraceLevel::Generic).unwrap();
        assert_eq!(out.value, input, "generic mutated input for {stem}");
    }
}

#[cfg(feature = "ner")]
#[test]
fn golden_full_traces() {
    use cirislens_core::scrubber::ner;
    if !ner::is_configured() {
        eprintln!(
            "golden_full_traces: NER not configured \
             (set CIRISLENS_NER_MODEL_DIR or CIRISLENS_NER_MODEL_ID), skipping"
        );
        return;
    }
    run_level(TraceLevel::FullTraces, "full_traces");
}
