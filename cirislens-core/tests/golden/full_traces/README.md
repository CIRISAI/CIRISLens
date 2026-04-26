# Full-traces golden corpus (NER + regex)

This directory is intentionally empty in the default build. The
`full_traces` privacy level runs the multilingual XLM-RoBERTa NER pass
plus the regex pass; populating this corpus requires the model
weights to be available locally so that each `<name>.expected.json`
can be authored deterministically.

## Populating the corpus

1. Build with the `ner` feature and configure a model:
   ```bash
   export CIRISLENS_NER_MODEL_DIR=/path/to/xlm-roberta-wikiann
   # or:
   export CIRISLENS_NER_MODEL_ID=Davlan/xlm-roberta-base-wikiann-ner
   ```

2. Author input files following the same convention as `detailed/`:
   `<NN>_<lang>_<scenario>.input.json`. Each input should contain the
   reasoning text under a SCRUB_FIELDS-keyed string so that NER fires.

3. Bootstrap expected outputs:
   ```bash
   cd cirislens-core
   CIRISLENS_GOLDEN_REGENERATE=1 cargo test --features ner --test golden_test golden_full_traces
   ```

4. Inspect the generated `.expected.json` files. Verify that:
   - Person/place/org names are replaced with `[PERSON_*]`, `[GPE_*]`,
     `[ORG_*]` placeholders and re-numbered consistently.
   - The year residue invariant has held (no 1700-2023 year survived).
   - Surrounding non-PII tokens are preserved verbatim.

5. Commit both `.input.json` and `.expected.json`. CI then locks the
   corpus against any drift. If a future model update or rule change
   changes the output, regenerate, review the diff, and commit.

## Suggested coverage

Aim for the same ~30 traces × 29 languages shape as the regex tier:
- One person-bearing trace per language.
- One place-bearing trace per language.
- A handful of "known-difficult" cases (e.g., transliterated names,
  honorifics, code-switched text) drawn from the QA harness or live
  traffic where v1 was caught missing entities.

The runner skips this group if NER is not configured, so an empty
corpus does not break CI; populating it tightens the contract.
