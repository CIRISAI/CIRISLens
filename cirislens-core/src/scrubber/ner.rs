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
///
/// Prefer [`scrub_batch`] when scrubbing more than one text — a single
/// forward pass over a batch of texts is 5–10× faster than this loop.
pub fn scrub_with_ner(text: &str, stats: &mut ScrubStats) -> Result<String, ScrubError> {
    if !is_configured() {
        return Err(ScrubError::NerNotConfigured);
    }

    #[cfg(feature = "ner")]
    {
        // Single-text path: just route through the batched scrub with batch=1.
        let mut out = backend::scrub_batch(&[text.to_string()], stats)?;
        Ok(out.pop().unwrap_or_default())
    }
    #[cfg(not(feature = "ner"))]
    {
        let _ = (text, stats);
        Err(ScrubError::NerNotConfigured)
    }
}

/// Run NER over a batch of texts in a single forward pass. Returns the
/// scrubbed strings in the same order as the input. Long texts are
/// chunked transparently. The whole batch (across all texts and chunks)
/// goes through one forward call, so the caller should pass as many
/// strings as fit in a reasonable padded tensor — typically the entire
/// SCRUB_FIELDS-eligible content of a single trace at once.
pub fn scrub_batch(texts: &[String], stats: &mut ScrubStats) -> Result<Vec<String>, ScrubError> {
    if !is_configured() {
        return Err(ScrubError::NerNotConfigured);
    }
    #[cfg(feature = "ner")]
    {
        backend::scrub_batch(texts, stats)
    }
    #[cfg(not(feature = "ner"))]
    {
        let _ = (texts, stats);
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
                let msg = format!(
                    "NER backend ready (candle / XLM-R): {} labels",
                    model.labels.len()
                );
                log::info!("{msg}");
                // Also surface to stderr so it's visible without a configured
                // logger (PyO3 callers don't always wire one up).
                eprintln!("[cirislens_core] {msg}");
                Some(Mutex::new(Backend { model, tokenizer }))
            }
            Err(e) => {
                let msg = format!("NER backend load failed: {e:#}");
                log::error!("{msg}");
                eprintln!("[cirislens_core] {msg}");
                None
            }
        }
    }

    pub fn is_configured() -> bool {
        BACKEND.get_or_init(init).is_some()
    }

    /// XLM-R hard caps at 514 positional embeddings (12 above the 512
    /// content tokens). Texts longer than that get chunked.
    ///
    /// The window has to leave generous margin against re-tokenization
    /// variance: when we slice the original text on a token boundary
    /// from the *full* encoding and then re-encode that slice, BPE can
    /// produce a different (usually larger) token count because the
    /// missing left context changes how subwords merge. Reproducible
    /// real-world case: a 232-char Amharic field that the full encoding
    /// resolves to ~210 tokens re-encodes to >280. With a 510-token
    /// window plus 2 special tokens the re-encode pushes past 514 and
    /// the forward pass errors out. A 384-token window leaves >100
    /// tokens of safety; the tokenizer-side `with_truncation(512)`
    /// added in the loader is the second backstop.
    const MAX_TOKENS_PER_CHUNK: usize = 384;

    /// Cap on the number of chunks per forward call. Padding cost grows
    /// with batch size × longest sequence; 64 chunks of 384 tokens each
    /// keeps the per-call hidden state under ~75 MB even at fp32.
    const MAX_BATCH_CHUNKS: usize = 64;

    /// Pre-chunk a single text into MAX_TOKENS_PER_CHUNK windows. Returns
    /// `(byte_offset_in_text, chunk_str)` pairs — the offsets are the
    /// byte positions of each chunk's start in the original text, used
    /// to translate chunk-local entity spans back to global positions.
    fn pre_chunk<'a>(
        text: &'a str,
        tokenizer: &Tokenizer,
    ) -> Result<Vec<(usize, &'a str)>, ScrubError> {
        let full = tokenizer
            .encode(text, false)
            .map_err(|e| ScrubError::NerFailed(format!("tokenize: {e}")))?;
        let total = full.get_ids().len();
        if total == 0 {
            return Ok(Vec::new());
        }
        let offsets = full.get_offsets();

        let mut chunks = Vec::new();
        let mut tok_start = 0usize;
        while tok_start < total {
            let tok_end = (tok_start + MAX_TOKENS_PER_CHUNK).min(total);
            let byte_start = offsets[tok_start].0;
            let byte_end = offsets[tok_end - 1].1;
            if byte_end > byte_start {
                chunks.push((byte_start, &text[byte_start..byte_end]));
            }
            tok_start = tok_end;
        }
        Ok(chunks)
    }

    pub fn scrub_batch(
        texts: &[String],
        stats: &mut ScrubStats,
    ) -> Result<Vec<String>, ScrubError> {
        let backend = BACKEND
            .get_or_init(init)
            .as_ref()
            .ok_or(ScrubError::NerNotConfigured)?;
        let backend = backend.lock();

        if texts.is_empty() {
            return Ok(Vec::new());
        }

        // 1. Pre-chunk every text. `flat_chunks[i] = (origin_text_idx, byte_offset_in_origin, chunk_str)`.
        let mut flat_chunks: Vec<(usize, usize, &str)> = Vec::new();
        for (i, text) in texts.iter().enumerate() {
            for (offset, chunk) in pre_chunk(text, &backend.tokenizer)? {
                flat_chunks.push((i, offset, chunk));
            }
        }

        // Per-text accumulator of entity spans (offsets are global to each text).
        let mut per_text_spans: Vec<Vec<(usize, usize, String)>> = vec![Vec::new(); texts.len()];

        // 2. Process chunks in mini-batches of up to MAX_BATCH_CHUNKS so the
        //    padded tensor never blows up memory on pathological inputs.
        for batch in flat_chunks.chunks(MAX_BATCH_CHUNKS) {
            run_batch(&backend, batch, &mut per_text_spans)?;
        }

        // 3. Per text, replace its accumulated spans on the original string.
        let mut out = Vec::with_capacity(texts.len());
        for (i, text) in texts.iter().enumerate() {
            out.push(replace_spans(text, &per_text_spans[i], stats));
        }
        Ok(out)
    }

    /// Run one batched forward pass over `batch` chunks. Each chunk's
    /// entity spans get appended to `per_text_spans[origin_idx]` with
    /// global byte offsets (chunk-local span + chunk's byte_offset_in_origin).
    fn run_batch(
        backend: &Backend,
        batch: &[(usize, usize, &str)],
        per_text_spans: &mut [Vec<(usize, usize, String)>],
    ) -> Result<(), ScrubError> {
        if batch.is_empty() {
            return Ok(());
        }

        // Tokenize all chunks together. encode_batch handles per-text encoding
        // independently; we'll pad ourselves so we can build a single tensor.
        let chunk_strs: Vec<&str> = batch.iter().map(|(_, _, s)| *s).collect();
        let encodings = backend
            .tokenizer
            .encode_batch(chunk_strs, true)
            .map_err(|e| ScrubError::NerFailed(format!("encode_batch: {e}")))?;

        // Pad to the longest sequence in the batch (right-pad with the
        // tokenizer's pad token; tokenizers crate fills in pad ids only when
        // padding is configured globally — we pad manually here using ids
        // from the model config to avoid a stateful tokenizer setup).
        let pad_id = backend
            .tokenizer
            .token_to_id("<pad>")
            .or_else(|| backend.tokenizer.token_to_id("[PAD]"))
            .unwrap_or(1);

        let max_len = encodings.iter().map(|e| e.get_ids().len()).max().unwrap_or(0);
        if max_len == 0 {
            return Ok(());
        }
        let batch_size = encodings.len();

        let mut ids_flat = Vec::with_capacity(batch_size * max_len);
        let mut mask_flat = Vec::with_capacity(batch_size * max_len);
        for enc in &encodings {
            let ids = enc.get_ids();
            let mask = enc.get_attention_mask();
            ids_flat.extend(ids.iter().map(|&x| x as i64));
            mask_flat.extend(mask.iter().map(|&x| x as i64));
            // Right-pad
            for _ in ids.len()..max_len {
                ids_flat.push(pad_id as i64);
                mask_flat.push(0);
            }
        }

        let device = &backend.model.device;
        let input_ids = Tensor::from_vec(ids_flat, (batch_size, max_len), device)
            .map_err(|e| ScrubError::NerFailed(format!("ids tensor: {e}")))?;
        let attention_mask = Tensor::from_vec(mask_flat, (batch_size, max_len), device)
            .map_err(|e| ScrubError::NerFailed(format!("mask tensor: {e}")))?;

        // Forward → logits [batch, seq_len, num_labels]
        let logits = backend
            .model
            .forward(&input_ids, &attention_mask)
            .map_err(|e| ScrubError::NerFailed(format!("forward: {e:#}")))?;

        // Argmax over label axis (dim=2) → [batch, seq_len]
        let label_ids_tensor = logits
            .argmax(2)
            .map_err(|e| ScrubError::NerFailed(format!("argmax: {e}")))?;
        let label_ids_2d: Vec<Vec<u32>> = label_ids_tensor
            .to_vec2::<u32>()
            .map_err(|e| ScrubError::NerFailed(format!("argmax to_vec: {e}")))?;

        // Per chunk: BIO collapse → translate offsets to global.
        for (i, &(origin_idx, byte_offset_in_origin, _)) in batch.iter().enumerate() {
            let label_ids: Vec<usize> = label_ids_2d[i].iter().map(|&x| x as usize).collect();
            let offsets = encodings[i].get_offsets();
            let chunk_spans = collapse_bio(&label_ids, offsets, &backend.model.labels);
            for (s, e, tag) in chunk_spans {
                per_text_spans[origin_idx]
                    .push((s + byte_offset_in_origin, e + byte_offset_in_origin, tag));
            }
        }
        Ok(())
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
