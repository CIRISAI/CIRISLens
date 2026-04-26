//! NER inference path — XLM-RoBERTa NER via ONNX Runtime.
//!
//! This module is intentionally split out so the regex+walker plumbing can
//! land and be tested independently of the model weights.
//!
//! Current status: stub. `is_configured()` returns `false` until the ONNX
//! integration lands. Per FSD §6, full_traces traces will be rejected with
//! `ScrubError::NerNotConfigured` rather than silently passing through with
//! regex-only coverage. This is the correct fail-loud behavior.
//!
//! ## Wiring plan (next change)
//!
//! 1. Add `ort = { version = "2", features = ["load-dynamic"] }` and
//!    `tokenizers = "0.20"` to `Cargo.toml`.
//! 2. Bundle a quantized INT8 ONNX export of
//!    `Davlan/xlm-roberta-base-wikiann-ner` under `models/`.
//! 3. Initialize a per-process `ort::Session` at first use; pool size matches
//!    Uvicorn worker count.
//! 4. Implement `scrub_with_ner` to: tokenize input → run inference → align
//!    sub-tokens to character spans → replace each entity span with
//!    `[<TAG>_<n>]` (using a per-text counter so two PERSON entities become
//!    `[PER_1]` and `[PER_2]`).
//! 5. Update `is_configured()` to return `true` when the session is loaded.
//!
//! Until then this stub keeps the code path sound: the trace handler can
//! call `scrub_trace` and trust that no `Ok(...)` from FullTraces ever
//! returns from a stub-only build.

use super::ScrubError;
use super::ScrubStats;

/// Returns `true` only when an NER session is fully loaded and ready.
/// Caller (see `scrubber::scrub_trace`) must reject `FullTraces` traces
/// when this returns `false`.
pub fn is_configured() -> bool {
    // Wired up once `Session::new(...)` succeeds at startup.
    false
}

/// Run NER over a single text and replace entity spans with placeholders.
/// Stub returns the input unchanged but increments stats so tests can observe
/// the call. Real implementation lives behind `is_configured() == true`.
#[allow(unused_variables)]
pub fn scrub_with_ner(text: &str, stats: &mut ScrubStats) -> Result<String, ScrubError> {
    if !is_configured() {
        return Err(ScrubError::NerNotConfigured);
    }

    // TODO: real implementation. Until then this branch is unreachable
    // in production because `is_configured()` returns false.
    Ok(text.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stub_returns_not_configured() {
        let mut s = ScrubStats::default();
        assert!(matches!(
            scrub_with_ner("anything", &mut s),
            Err(ScrubError::NerNotConfigured)
        ));
    }

    #[test]
    fn is_configured_false_until_wired() {
        assert!(!is_configured());
    }
}
