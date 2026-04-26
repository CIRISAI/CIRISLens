//! NER inference path — XLM-RoBERTa NER via ONNX Runtime.
//!
//! Behind the `ner` feature flag (see `Cargo.toml`). When the feature is
//! disabled, [`is_configured`] returns `false` and the trace handler
//! rejects `FullTraces` traces per FSD §6 (fail-loud, never partial-persist).
//!
//! When the feature is enabled, this module:
//!
//! 1. Lazy-loads an XLM-RoBERTa NER ONNX session and a SentencePiece-BPE
//!    tokenizer from configurable paths (`CIRISLENS_NER_MODEL_PATH`,
//!    `CIRISLENS_NER_TOKENIZER_PATH`).
//! 2. Tokenizes input → runs inference → applies argmax to logits to get
//!    per-token labels (BIO format: `B-PER`, `I-PER`, `B-ORG`, etc.).
//! 3. Collapses BIO sub-token spans to entity spans, mapping back to
//!    character offsets in the original string.
//! 4. Replaces each span with `[<TAG>_<n>]` where `<n>` increments per
//!    tag within a single text (so two PERSON entities become `[PER_1]`
//!    and `[PER_2]`).
//!
//! ## Configuration
//!
//! Both paths are required env vars when `ner` feature is on:
//! - `CIRISLENS_NER_MODEL_PATH` → `.onnx` file (XLM-R fine-tuned NER)
//! - `CIRISLENS_NER_TOKENIZER_PATH` → `tokenizer.json` (HF format)
//!
//! When either is missing or the file fails to load, [`is_configured`]
//! returns `false` and the system fails closed.
//!
//! ## Limitation: 3-class wikiann ontology
//!
//! Initial deployment uses `Davlan/xlm-roberta-base-wikiann-ner` which
//! emits only `PER`, `ORG`, `LOC`. Other categories from the FSD's full
//! redact set (GPE, FAC, NORP, DATE, TIME, EVENT, MISC) rely on regex
//! coverage (year, year-identifier patterns) plus `LOC` doing double duty
//! for FAC/GPE in the wikiann labeling. A richer multilingual fine-tune
//! (`CIRISAI/xlmr-29lang-ner`) is FSD §9 future work.

use super::{ScrubError, ScrubStats};

/// Returns `true` only when an NER session is fully loaded and ready.
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
///
/// Returns the input string unchanged when no entities are detected.
/// Returns `Err(ScrubError::NerNotConfigured)` when the `ner` feature is
/// disabled or the session failed to load — caller must propagate.
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
        let _ = (text, stats); // silence unused warnings
        Err(ScrubError::NerNotConfigured)
    }
}

// ───────────────────────────────────────────────────────────────────────────
// Implementation behind the `ner` feature flag
// ───────────────────────────────────────────────────────────────────────────

#[cfg(feature = "ner")]
mod backend {
    //! Real ONNX-Runtime-backed inference. Activated when the `ner` feature
    //! is on AND the model + tokenizer files load successfully at first call.

    use super::{ScrubError, ScrubStats};
    use ndarray::{s, Array, Array2, Axis};
    use ort::execution_providers::CPUExecutionProvider;
    use ort::session::{builder::GraphOptimizationLevel, Session};
    use ort::value::Tensor;
    use parking_lot::Mutex;
    use std::env;
    use std::path::PathBuf;
    use std::sync::OnceLock;
    use tokenizers::Tokenizer;

    /// Per-process session + tokenizer. Initialized lazily on first call;
    /// `OnceLock` so initialization happens at most once.
    static SESSION: OnceLock<Option<Mutex<NerBackend>>> = OnceLock::new();

    struct NerBackend {
        session: Session,
        tokenizer: Tokenizer,
        labels: Vec<String>,
    }

    /// Default labels for `Davlan/xlm-roberta-base-wikiann-ner`. Override
    /// via `CIRISLENS_NER_LABELS` (comma-separated, in id order) when
    /// using a different fine-tune.
    const DEFAULT_WIKIANN_LABELS: &[&str] = &[
        "O",      // 0
        "B-PER",  // 1
        "I-PER",  // 2
        "B-ORG",  // 3
        "I-ORG",  // 4
        "B-LOC",  // 5
        "I-LOC", // 6
    ];

    fn init() -> Option<Mutex<NerBackend>> {
        let model_path = env::var("CIRISLENS_NER_MODEL_PATH").ok().map(PathBuf::from);
        let tok_path = env::var("CIRISLENS_NER_TOKENIZER_PATH").ok().map(PathBuf::from);

        let (Some(model_path), Some(tok_path)) = (model_path, tok_path) else {
            log::warn!(
                "NER backend not configured: set CIRISLENS_NER_MODEL_PATH and \
                 CIRISLENS_NER_TOKENIZER_PATH to enable full_traces scrubbing"
            );
            return None;
        };

        let session = match Session::builder()
            .and_then(|b| b.with_optimization_level(GraphOptimizationLevel::Level3))
            .and_then(|b| b.with_execution_providers([CPUExecutionProvider::default().build()]))
            .and_then(|b| b.commit_from_file(&model_path))
        {
            Ok(s) => s,
            Err(e) => {
                log::error!("NER session load failed ({}): {}", model_path.display(), e);
                return None;
            }
        };

        let tokenizer = match Tokenizer::from_file(&tok_path) {
            Ok(t) => t,
            Err(e) => {
                log::error!("Tokenizer load failed ({}): {}", tok_path.display(), e);
                return None;
            }
        };

        let labels: Vec<String> = match env::var("CIRISLENS_NER_LABELS") {
            Ok(s) => s.split(',').map(|s| s.trim().to_string()).collect(),
            Err(_) => DEFAULT_WIKIANN_LABELS.iter().map(|s| s.to_string()).collect(),
        };

        log::info!(
            "NER backend ready: model={} tokenizer={} labels={}",
            model_path.display(),
            tok_path.display(),
            labels.len()
        );

        Some(Mutex::new(NerBackend {
            session,
            tokenizer,
            labels,
        }))
    }

    pub fn is_configured() -> bool {
        SESSION.get_or_init(init).is_some()
    }

    pub fn scrub(text: &str, stats: &mut ScrubStats) -> Result<String, ScrubError> {
        let backend = SESSION
            .get_or_init(init)
            .as_ref()
            .ok_or(ScrubError::NerNotConfigured)?;
        let mut backend = backend.lock();

        // 1. Tokenize. Capture offsets so we can map sub-tokens back to chars.
        let encoding = backend
            .tokenizer
            .encode(text, true)
            .map_err(|e| ScrubError::NerFailed(format!("tokenize: {e}")))?;

        let ids = encoding.get_ids();
        let attention = encoding.get_attention_mask();
        let offsets = encoding.get_offsets();
        let n = ids.len();
        if n == 0 {
            return Ok(text.to_string());
        }

        // 2. Build input tensors. XLM-R takes `input_ids` and `attention_mask`,
        //    both shape [batch=1, seq_len].
        let input_ids: Array2<i64> =
            Array::from_shape_vec((1, n), ids.iter().map(|&x| x as i64).collect())
                .map_err(|e| ScrubError::NerFailed(format!("ids tensor: {e}")))?;
        let attention_mask: Array2<i64> = Array::from_shape_vec(
            (1, n),
            attention.iter().map(|&x| x as i64).collect(),
        )
        .map_err(|e| ScrubError::NerFailed(format!("mask tensor: {e}")))?;

        // 3. Run inference.
        let outputs = backend
            .session
            .run(ort::inputs![
                "input_ids" => Tensor::from_array(input_ids).map_err(|e| ScrubError::NerFailed(format!("ids: {e}")))?,
                "attention_mask" => Tensor::from_array(attention_mask).map_err(|e| ScrubError::NerFailed(format!("mask: {e}")))?,
            ])
            .map_err(|e| ScrubError::NerFailed(format!("inference: {e}")))?;

        // 4. Extract logits — shape [1, seq_len, num_labels] — and argmax.
        let logits = outputs
            .iter()
            .next()
            .ok_or_else(|| ScrubError::NerFailed("no outputs".into()))?
            .1
            .try_extract_array::<f32>()
            .map_err(|e| ScrubError::NerFailed(format!("extract: {e}")))?;
        let view = logits.view();
        let logits_2d = view.index_axis(Axis(0), 0); // shape [seq_len, num_labels]

        let label_ids: Vec<usize> = (0..n)
            .map(|t| {
                let row = logits_2d.slice(s![t, ..]);
                row.iter()
                    .enumerate()
                    .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
                    .map(|(idx, _)| idx)
                    .unwrap_or(0)
            })
            .collect();

        // 5. Collapse BIO labels into entity spans (character offsets).
        let spans = collapse_bio(&label_ids, offsets, &backend.labels);

        // 6. Replace each span with [<TAG>_<n>], counting per-tag occurrences.
        Ok(replace_spans(text, &spans, stats))
    }

    /// Collapse BIO-tagged sub-tokens into character-offset entity spans.
    /// Returns `(start_char, end_char, tag)` triples, sorted by start.
    fn collapse_bio(
        label_ids: &[usize],
        offsets: &[(usize, usize)],
        labels: &[String],
    ) -> Vec<(usize, usize, String)> {
        let mut spans: Vec<(usize, usize, String)> = Vec::new();
        let mut current: Option<(usize, usize, String)> = None;

        for (i, &lid) in label_ids.iter().enumerate() {
            // Skip special tokens with (0,0) offsets (CLS, SEP, PAD).
            let (start, end) = offsets.get(i).copied().unwrap_or((0, 0));
            if start == end {
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
                    // Continue current span.
                    *ce = end;
                }
                _ => {
                    // Begin a new span (B- or label change).
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

    /// Replace character spans with `[<TAG>_<n>]` placeholders. Per-text
    /// counter so two PERSON spans become `[PER_1]` and `[PER_2]`.
    fn replace_spans(
        text: &str,
        spans: &[(usize, usize, String)],
        stats: &mut ScrubStats,
    ) -> String {
        if spans.is_empty() {
            return text.to_string();
        }

        let mut counts: std::collections::HashMap<String, usize> =
            std::collections::HashMap::new();
        let mut out = String::with_capacity(text.len());
        let bytes = text.as_bytes();
        let mut cursor = 0usize;

        // spans are produced in token order which is byte-order for the XLM-R
        // tokenizer (offsets are byte offsets into the original UTF-8 string).
        let mut sorted: Vec<&(usize, usize, String)> = spans.iter().collect();
        sorted.sort_by_key(|s| s.0);

        for (start, end, tag) in sorted {
            if *start < cursor {
                continue; // overlap — already covered by an earlier span
            }
            if *end > bytes.len() {
                continue; // defensive
            }
            // Push pre-span text
            out.push_str(std::str::from_utf8(&bytes[cursor..*start]).unwrap_or(""));
            let n = counts.entry(tag.clone()).or_insert(0);
            *n += 1;
            out.push_str(&format!("[{}_{}]", tag, n));
            stats.entities_redacted += 1;
            cursor = *end;
        }
        // Push tail
        if cursor < bytes.len() {
            out.push_str(std::str::from_utf8(&bytes[cursor..]).unwrap_or(""));
        }
        out
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn collapse_bio_simple_per() {
            // Token 0 = O (CLS), 1 = B-PER, 2 = I-PER, 3 = O (SEP)
            let labels: Vec<String> = super::DEFAULT_WIKIANN_LABELS.iter().map(|s| s.to_string()).collect();
            let label_ids = vec![0, 1, 2, 0];
            let offsets = vec![(0, 0), (5, 10), (10, 15), (0, 0)];
            let spans = collapse_bio(&label_ids, &offsets, &labels);
            assert_eq!(spans.len(), 1);
            assert_eq!(spans[0], (5, 15, "PER".to_string()));
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
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stub_returns_not_configured_without_feature() {
        // Whether or not the `ner` feature is on, without the env vars
        // pointing at a real model, this must return NerNotConfigured —
        // the fail-closed default.
        let mut s = ScrubStats::default();
        let result = scrub_with_ner("Alice met Bob in Paris.", &mut s);
        // We don't assert is_configured here because in CI with feature on
        // it might be true if env vars are set. We just assert that without
        // setup, scrub returns the right error.
        if !is_configured() {
            assert!(matches!(result, Err(ScrubError::NerNotConfigured)));
        }
    }
}
