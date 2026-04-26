# Scrubbing v2 model artifacts

XLM-RoBERTa NER weights consumed by the `ner` feature. Not committed to
git (~1 GB FP32, ~280 MB INT8).

## Runtime path (default)

When the `ner` feature is enabled, the candle backend uses `hf-hub` to
fetch model weights from Hugging Face on first call. Files are cached
under the standard HF cache directory (`~/.cache/huggingface/hub/` by
default; override via `HF_HOME` env var).

No explicit setup is required for online deployments.

## Offline / air-gapped deployments

For environments where the edge can't reach `huggingface.co`, pre-fetch
the model artifacts:

```bash
python -m venv /tmp/scrub_export_venv
source /tmp/scrub_export_venv/bin/activate
pip install transformers safetensors

python -c "
from transformers import AutoModelForTokenClassification, AutoTokenizer
m = AutoModelForTokenClassification.from_pretrained('Davlan/xlm-roberta-base-wikiann-ner')
t = AutoTokenizer.from_pretrained('Davlan/xlm-roberta-base-wikiann-ner')
m.save_pretrained('cirislens-core/models/xlmr-ner')
t.save_pretrained('cirislens-core/models/xlmr-ner')
"
```

Then point the loader at the local directory:

```bash
export CIRISLENS_NER_MODEL_DIR=cirislens-core/models/xlmr-ner
```

## Legacy ONNX export script

`scripts/export_xlmr_ner.py` was written for an earlier ONNX-based plan.
It still produces a valid INT8 ONNX file (in case ONNX inference is ever
needed for a non-candle backend), but the live scrubber path uses candle
+ safetensors and does not consume ONNX artifacts.

See `FSD/CIRIS_SCRUBBING_V2.md` § Critical path Stage 1 for the full
design rationale.
