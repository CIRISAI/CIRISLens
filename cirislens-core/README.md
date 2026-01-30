# CIRISLens Core

High-performance trace ingestion pipeline for CIRISLens, built with Rust and exposed to Python via PyO3.

## Features

- **Schema Validation**: DB-driven schema detection and validation
- **Signature Verification**: Ed25519 signature verification
- **Security Sanitization**: XSS, SQLi, command injection detection
- **Field Extraction**: Dynamic field extraction from schema rules
- **PII Scrubbing**: Regex-based PII detection and removal

## Building

```bash
maturin develop
```

## Testing

```bash
cargo test
```
