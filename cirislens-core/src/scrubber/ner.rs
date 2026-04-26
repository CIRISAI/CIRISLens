//! NER inference path — XLM-RoBERTa NER via candle.
//!
//! Inference backend: [`candle`](https://github.com/huggingface/candle),
//! HuggingFace's pure-Rust ML framework. Selected over ort/ONNX after a
//! cross-comparison (see FSD §10): native XLM-R support, no native deps,
//! HF-maintained, optional CUDA/Metal acceleration, immune to the
//! ort/ort-sys version-skew issues.
//!
//! Behind the `ner` feature flag (see `Cargo.toml`). Default builds skip
//! candle entirely; CI stays fast.
//!
//! ## Status
//!
//! [`is_configured`] currently returns `false` even when the feature is on
//! — the candle XLM-R backend is scaffolded with the right API surface
//! but not yet loading model weights. Per FSD §6, this means full_traces
//! traces are correctly rejected with `ScrubError::NerNotConfigured`.
//!
//! The remaining work to flip `is_configured` to `true`:
//!
//! 1. Add a `xlm_r_loader` submodule that wraps
//!    `candle_transformers::models::xlm_roberta::XLMRobertaModel`
//!    plus a token-classification head.
//! 2. Lazy-load weights from `safetensors` or HF Hub via `hf-hub` crate.
//! 3. Tokenize with `tokenizers::Tokenizer`.
//! 4. Forward pass: `model.forward(input_ids, attention_mask)?` →
//!    classifier head → logits → argmax.
//! 5. BIO collapse → span replacement (the [`collapse_bio`] +
//!    [`replace_spans`] helpers in this module already do this; they're
//!    framework-agnostic).
//!
//! The post-inference logic (BIO collapse, char-offset span replacement,
//! per-tag counter) is unit-tested here and ready for whichever backend
//! produces the per-token label IDs.

use super::{ScrubError, ScrubStats};

/// Returns `true` only when an NER backend is fully loaded and ready.
/// Caller (see `scrubber::scrub_trace`) must reject `FullTraces` traces
/// when this returns `false`.
#[cfg(feature = "ner")]
pub fn is_configured() -> bool {
    backend::is_configured()
}

#[cfg(not(feature = "ner"))]
pub fn is_configured() -> bool {
    false
}

/// Run NER over a single text and replace entity spans with placeholders.
/// Returns `Err(ScrubError::NerNotConfigured)` when the `ner` feature is
/// disabled or the backend isn't ready.
pub fn scrub_with_ner(text: &str, stats: &mut ScrubStats) -> Result<String, ScrubError> {
    if !is_configured() {
        return Err(ScrubError::NerNotConfigured);
    }

    #[cfg(feature = "ner")]
    {
        backend::scrub(text, stats)
    }
    #[cfg(not(feature = "ner"))]
    {
        let _ = (text, stats);
        Err(ScrubError::NerNotConfigured)
    }
}

// ───────────────────────────────────────────────────────────────────────────
// Framework-agnostic post-inference helpers (BIO collapse, span replacement)
// ───────────────────────────────────────────────────────────────────────────
//
// These work regardless of which inference framework produces the per-token
// labels. They live outside the `backend` module so they're available to
// both unit tests and any future framework swap.

/// Collapse BIO-tagged sub-tokens into character-offset entity spans.
/// Returns `(start_byte, end_byte, tag)` triples in token order.
#[cfg_attr(not(any(feature = "ner", test)), allow(dead_code))]
pub(crate) fn collapse_bio(
    label_ids: &[usize],
    offsets: &[(usize, usize)],
    labels: &[String],
) -> Vec<(usize, usize, String)> {
    let mut spans: Vec<(usize, usize, String)> = Vec::new();
    let mut current: Option<(usize, usize, String)> = None;

    for (i, &lid) in label_ids.iter().enumerate() {
        let (start, end) = offsets.get(i).copied().unwrap_or((0, 0));
        if start == end {
            // Special token (CLS, SEP, PAD) — break any in-progress span.
            if let Some(span) = current.take() {
                spans.push(span);
            }
            continue;
        }

        let label = labels.get(lid).map(String::as_str).unwrap_or("O");
        if label == "O" {
            if let Some(span) = current.take() {
                spans.push(span);
            }
            continue;
        }

        let (prefix, tag) = label.split_once('-').unwrap_or(("I", label));
        let tag = tag.to_string();

        match (&mut current, prefix) {
            (Some((_, ce, ct)), "I") if *ct == tag => {
                *ce = end; // continue current span
            }
            _ => {
                if let Some(span) = current.take() {
                    spans.push(span);
                }
                current = Some((start, end, tag));
            }
        }
    }
    if let Some(span) = current.take() {
        spans.push(span);
    }
    spans
}

/// Replace byte-offset spans with `[<TAG>_<n>]` placeholders. Per-tag
/// counter so two PERSON spans become `[PER_1]` and `[PER_2]`.
#[cfg_attr(not(any(feature = "ner", test)), allow(dead_code))]
pub(crate) fn replace_spans(
    text: &str,
    spans: &[(usize, usize, String)],
    stats: &mut ScrubStats,
) -> String {
    if spans.is_empty() {
        return text.to_string();
    }

    let mut counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    let mut out = String::with_capacity(text.len());
    let bytes = text.as_bytes();
    let mut cursor = 0usize;

    let mut sorted: Vec<&(usize, usize, String)> = spans.iter().collect();
    sorted.sort_by_key(|s| s.0);

    for (start, end, tag) in sorted {
        if *start < cursor || *end > bytes.len() {
            continue; // overlap or out-of-bounds — defensive
        }
        out.push_str(std::str::from_utf8(&bytes[cursor..*start]).unwrap_or(""));
        let n = counts.entry(tag.clone()).or_insert(0);
        *n += 1;
        out.push_str(&format!("[{}_{}]", tag, n));
        stats.entities_redacted += 1;
        cursor = *end;
    }
    if cursor < bytes.len() {
        out.push_str(std::str::from_utf8(&bytes[cursor..]).unwrap_or(""));
    }
    out
}

// ───────────────────────────────────────────────────────────────────────────
// candle backend (behind `ner` feature)
// ───────────────────────────────────────────────────────────────────────────

#[cfg(feature = "ner")]
mod backend {
    //! candle-based XLM-R NER inference.
    //!
    //! Lazy-loads model + tokenizer from HF Hub (or a local dir via
    //! `CIRISLENS_NER_MODEL_DIR`). On first call, attempts to construct
    //! the backend; on success caches it; on failure logs and stays
    //! unconfigured (full_traces traces will be rejected).

    use super::{collapse_bio, replace_spans, ScrubError, ScrubStats};
    use crate::scrubber::xlm_r_loader::{ModelSource, XLMRTokenClassifier};
    use candle_core::Tensor;
    use parking_lot::Mutex;
    use std::sync::OnceLock;
    use tokenizers::Tokenizer;

    /// Per-process backend. `OnceLock` ensures init runs at most once;
    /// `Mutex` serializes concurrent inference calls (candle is not
    /// thread-safe by default for shared models without external sync).
    static BACKEND: OnceLock<Option<Mutex<Backend>>> = OnceLock::new();

    struct Backend {
        model: XLMRTokenClassifier,
        tokenizer: Tokenizer,
    }

    fn init() -> Option<Mutex<Backend>> {
        let source = ModelSource::from_env();
        match source.load() {
            Ok((model, tokenizer)) => {
                log::info!(
                    "NER backend ready (candle / XLM-R): {} labels",
                    model.labels.len()
                );
                Some(Mutex::new(Backend { model, tokenizer }))
            }
            Err(e) => {
                log::error!("NER backend load failed: {e:#}");
                None
            }
        }
    }

    pub fn is_configured() -> bool {
        BACKEND.get_or_init(init).is_some()
    }

    pub fn scrub(text: &str, stats: &mut ScrubStats) -> Result<String, ScrubError> {
        let backend = BACKEND
            .get_or_init(init)
            .as_ref()
            .ok_or(ScrubError::NerNotConfigured)?;
        let backend = backend.lock();

        // 1. Tokenize. Capture char offsets for span mapping.
        let encoding = backend
            .tokenizer
            .encode(text, true)
            .map_err(|e| ScrubError::NerFailed(format!("tokenize: {e}")))?;
        let ids: Vec<i64> = encoding.get_ids().iter().map(|&x| x as i64).collect();
        let mask: Vec<i64> = encoding
            .get_attention_mask()
            .iter()
            .map(|&x| x as i64)
            .collect();
        let n = ids.len();
        if n == 0 {
            return Ok(text.to_string());
        }

        // 2. Build input tensors. Shape: [batch=1, seq_len].
        let device = &backend.model.device;
        let input_ids = Tensor::from_vec(ids, (1, n), device)
            .map_err(|e| ScrubError::NerFailed(format!("ids tensor: {e}")))?;
        let attention_mask = Tensor::from_vec(mask, (1, n), device)
            .map_err(|e| ScrubError::NerFailed(format!("mask tensor: {e}")))?;

        // 3. Forward → logits [seq_len, num_labels].
        let logits = backend
            .model
            .forward(&input_ids, &attention_mask)
            .map_err(|e| ScrubError::NerFailed(format!("forward: {e:#}")))?;

        // 4. Argmax over label axis → label IDs per token.
        let label_ids_tensor = logits
            .argmax(1)
            .map_err(|e| ScrubError::NerFailed(format!("argmax: {e}")))?;
        let label_ids_u32: Vec<u32> = label_ids_tensor
            .to_vec1::<u32>()
            .map_err(|e| ScrubError::NerFailed(format!("argmax to_vec: {e}")))?;
        let label_ids: Vec<usize> = label_ids_u32.iter().map(|&x| x as usize).collect();

        // 5. BIO collapse → byte-offset entity spans → placeholder replacement.
        let offsets = encoding.get_offsets();
        let spans = collapse_bio(&label_ids, offsets, &backend.model.labels);
        Ok(replace_spans(text, &spans, stats))
    }
}

// ───────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collapse_bio_simple_per() {
        let labels: Vec<String> = ["O", "B-PER", "I-PER", "B-ORG", "I-ORG", "B-LOC", "I-LOC"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let label_ids = vec![0, 1, 2, 0]; // O B-PER I-PER O
        let offsets = vec![(0, 0), (5, 10), (10, 15), (0, 0)];
        let spans = collapse_bio(&label_ids, &offsets, &labels);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0], (5, 15, "PER".to_string()));
    }

    #[test]
    fn collapse_bio_multi_entity() {
        let labels: Vec<String> = ["O", "B-PER", "I-PER", "B-ORG", "I-ORG", "B-LOC", "I-LOC"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        // O B-PER O B-LOC I-LOC O
        let label_ids = vec![0, 1, 0, 5, 6, 0];
        let offsets = vec![(0, 0), (5, 10), (10, 14), (15, 20), (20, 25), (0, 0)];
        let spans = collapse_bio(&label_ids, &offsets, &labels);
        assert_eq!(spans.len(), 2);
        assert_eq!(spans[0], (5, 10, "PER".to_string()));
        assert_eq!(spans[1], (15, 25, "LOC".to_string()));
    }

    #[test]
    fn replace_spans_counts_per_tag() {
        let text = "Alice and Bob met in Paris.";
        let spans = vec![
            (0, 5, "PER".to_string()),
            (10, 13, "PER".to_string()),
            (21, 26, "LOC".to_string()),
        ];
        let mut stats = ScrubStats::default();
        let out = replace_spans(text, &spans, &mut stats);
        assert_eq!(out, "[PER_1] and [PER_2] met in [LOC_1].");
        assert_eq!(stats.entities_redacted, 3);
    }

    #[test]
    fn replace_spans_handles_empty() {
        let mut stats = ScrubStats::default();
        let out = replace_spans("nothing", &[], &mut stats);
        assert_eq!(out, "nothing");
        assert_eq!(stats.entities_redacted, 0);
    }

    #[test]
    fn stub_returns_not_configured_without_setup() {
        let mut s = ScrubStats::default();
        let result = scrub_with_ner("Alice met Bob in Paris.", &mut s);
        if !is_configured() {
            assert!(matches!(result, Err(ScrubError::NerNotConfigured)));
        }
    }
}
