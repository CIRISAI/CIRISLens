# CIRISLens Testing Strategy

## Current State

| Metric | Current | Target |
|--------|---------|--------|
| Coverage | 5.5% | 80% |
| Test Files | 4 | ~15 |
| Test Cases | ~60 | ~400 |

### Uncovered Code by Priority

| File | Uncovered Lines | Priority |
|------|-----------------|----------|
| api/main.py | 691 | HIGH |
| api/otlp_collector.py | 262 | HIGH |
| tools/manager_validator.py | 209 | LOW |
| sdk/logshipper.py | 154 | MEDIUM |
| api/manager_collector.py | 122 | HIGH |
| api/token_manager.py | 107 | HIGH |
| api/log_ingest.py | 69 | MEDIUM |

---

## Testing Pyramid (Inverted)

```
         ▲
        /|\        E2E Tests (5%)
       / | \       - Full stack with real DB
      /  |  \      - Status page scenarios
     /   |   \
    /    |    \    Integration Tests (15%)
   /     |     \   - API endpoint tests
  /      |      \  - Database interaction
 /       |       \ - Multi-component flows
/        |        \
──────────────────── Unit Tests (80%)
                     - Pure functions
                     - Class methods
                     - Edge cases
                     - Error handling
```

### Why This Shape?

1. **Unit tests are fast** - Run in milliseconds, can run thousands
2. **Unit tests are stable** - No external dependencies to flake
3. **Unit tests pinpoint failures** - Know exactly what broke
4. **Integration tests verify contracts** - Ensure components work together
5. **E2E tests validate user scenarios** - Catch deployment issues

---

## Tooling

### Core Stack (Already Configured)

```toml
# pyproject.toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "httpx>=0.25.0",
]
```

### Recommended Additions

```toml
[project.optional-dependencies]
dev = [
    # ... existing ...
    "hypothesis>=6.92.0",        # Property-based testing
    "pytest-mock>=3.12.0",       # Better mocking
    "mutmut>=2.4.0",             # Mutation testing
    "faker>=22.0.0",             # Realistic test data
    "respx>=0.20.0",             # Mock httpx requests
    "pytest-timeout>=2.2.0",     # Prevent hanging tests
    "pytest-xdist>=3.5.0",       # Parallel test execution
]
```

### Tool Purposes

| Tool | Purpose | Use Case |
|------|---------|----------|
| [Hypothesis](https://hypothesis.readthedocs.io/) | Property-based testing | Find edge cases automatically |
| [mutmut](https://mutmut.readthedocs.io/) | Mutation testing | Verify test quality |
| [respx](https://lundberg.github.io/respx/) | Mock HTTP | Test external API calls |
| [Faker](https://faker.readthedocs.io/) | Test data generation | Realistic mock data |
| pytest-xdist | Parallel execution | Faster CI runs |

---

## Test Categories

### 1. Unit Tests (`tests/unit/`)

Pure function and method tests with no external dependencies.

```python
# tests/unit/test_token_manager.py
from api.token_manager import TokenManager

class TestTokenValidation:
    def test_valid_token_format(self):
        tm = TokenManager()
        assert tm.validate_format("svc_abc123def456")

    def test_invalid_token_rejected(self):
        tm = TokenManager()
        assert not tm.validate_format("invalid")

    @given(st.text(min_size=1, max_size=100))
    def test_no_crash_on_arbitrary_input(self, token):
        """Property: TokenManager never crashes on any input"""
        tm = TokenManager()
        # Should return bool, never raise
        result = tm.validate_format(token)
        assert isinstance(result, bool)
```

**Target modules:**
- `api/token_manager.py` - Token validation, generation
- `api/log_ingest.py` - PII sanitization, log parsing
- `sdk/logshipper.py` - Batching logic, retry logic

### 2. Integration Tests (`tests/integration/`)

Tests that verify component interactions with mocked external services.

```python
# tests/integration/test_api_endpoints.py
import pytest
from httpx import AsyncClient

class TestStatusEndpoints:
    async def test_health_check(self, client: AsyncClient):
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    async def test_status_aggregated_without_db(self, client: AsyncClient):
        """Status endpoint works even without database"""
        response = await client.get("/api/v1/status/aggregated")
        assert response.status_code in [200, 503]
```

**Target flows:**
- API endpoints with mocked DB
- OTLP ingestion pipeline
- Manager collector with mocked HTTP

### 3. E2E Tests (`tests/e2e/`)

Full stack tests requiring real services (run in CI with docker-compose).

```python
# tests/e2e/test_full_stack.py
@pytest.mark.e2e
@pytest.mark.slow
class TestFullStack:
    async def test_log_ingestion_flow(self, real_client):
        """Logs flow from SDK through API to database"""
        # 1. Send log via SDK
        # 2. Query database
        # 3. Verify log stored
```

---

## Property-Based Testing with Hypothesis

### What It Catches

Traditional tests check specific cases. Hypothesis finds edge cases you didn't think of.

```python
from hypothesis import given, strategies as st, settings

class TestLogSanitization:
    @given(st.text())
    def test_sanitize_never_crashes(self, message):
        """Property: sanitize_message handles any string"""
        from api.log_ingest import sanitize_message
        result = sanitize_message(message)
        assert result is None or isinstance(result, str)

    @given(st.emails())
    def test_emails_always_redacted(self, email):
        """Property: all emails are redacted"""
        from api.log_ingest import sanitize_message
        result = sanitize_message(f"User email is {email}")
        assert email not in result
        assert "[EMAIL]" in result

    @given(st.from_regex(r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'))
    def test_credit_cards_redacted(self, card):
        """Property: credit card patterns are redacted"""
        from api.log_ingest import sanitize_message
        result = sanitize_message(f"Card: {card}")
        assert card not in result
```

### Strategies for CIRISLens

```python
# tests/strategies.py
from hypothesis import strategies as st

# Valid service tokens
service_tokens = st.from_regex(r'svc_[a-f0-9]{32}', fullmatch=True)

# Log levels
log_levels = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

# ISO timestamps
timestamps = st.datetimes().map(lambda dt: dt.isoformat() + "Z")

# Status values
status_values = st.sampled_from(["operational", "degraded", "outage", "unknown"])

# Region codes
regions = st.sampled_from(["us", "eu", "global"])

# Log entries
log_entries = st.fixed_dictionaries({
    "timestamp": timestamps,
    "level": log_levels,
    "message": st.text(min_size=1, max_size=1000),
    "service_name": st.from_regex(r'[a-z]{3,20}', fullmatch=True),
})
```

---

## Mutation Testing with mutmut

### What It Does

Mutation testing modifies your code and checks if tests catch the change. If a mutation survives, your tests have a gap.

```bash
# Run mutation testing
mutmut run --paths-to-mutate=api/token_manager.py

# View surviving mutations
mutmut results

# Example output:
# Survived mutations:
#   api/token_manager.py:45 - changed '>' to '>='
#   api/token_manager.py:67 - removed 'not'
```

### Integration with CI

```yaml
# .github/workflows/mutation.yml
name: Mutation Testing
on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run mutation tests
        run: |
          pip install mutmut
          mutmut run --paths-to-mutate=api/ --CI
      - name: Check mutation score
        run: |
          SCORE=$(mutmut results --CI | grep "Mutation score" | awk '{print $3}')
          if (( $(echo "$SCORE < 70" | bc -l) )); then
            echo "Mutation score too low: $SCORE%"
            exit 1
          fi
```

---

## Test Organization

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── strategies.py            # Hypothesis strategies
├── mocks.py                 # Mock objects (existing)
│
├── unit/                    # 80% of tests
│   ├── __init__.py
│   ├── test_token_manager.py
│   ├── test_log_sanitization.py
│   ├── test_status_calculation.py
│   ├── test_logshipper.py
│   └── test_otlp_parsing.py
│
├── integration/             # 15% of tests
│   ├── __init__.py
│   ├── test_api_endpoints.py
│   ├── test_manager_collector.py
│   ├── test_otlp_collector.py
│   └── test_log_ingest.py
│
└── e2e/                     # 5% of tests
    ├── __init__.py
    ├── conftest.py          # E2E-specific fixtures
    └── test_full_flows.py
```

---

## Implementation Plan

### Phase 1: Foundation (Week 1)

1. **Add test dependencies**
   ```bash
   pip install hypothesis pytest-mock respx faker pytest-timeout
   ```

2. **Create test structure**
   ```bash
   mkdir -p tests/{unit,integration,e2e}
   touch tests/{unit,integration,e2e}/__init__.py
   ```

3. **Write Hypothesis strategies**
   - Service tokens
   - Log entries
   - Status values

### Phase 2: Unit Tests (Week 2-3)

| Module | Tests | Target Coverage |
|--------|-------|-----------------|
| token_manager.py | 20 | 90% |
| log_ingest.py | 15 | 85% |
| main.py (pure functions) | 50 | 60% |
| logshipper.py | 25 | 80% |

### Phase 3: Integration Tests (Week 4)

| Component | Tests | Focus |
|-----------|-------|-------|
| API endpoints | 30 | All routes |
| OTLP collector | 15 | Protocol handling |
| Manager collector | 10 | HTTP mocking |

### Phase 4: Quality Gates (Week 5)

1. **Set up mutation testing in CI**
2. **Add coverage gates** (already at 70% in pyproject.toml)
3. **Configure test parallelization**

---

## Running Tests

```bash
# All tests
pytest

# Unit tests only (fast)
pytest tests/unit -v

# With coverage report
pytest --cov=api --cov=sdk --cov-report=html

# Property-based tests with more examples
pytest --hypothesis-seed=0 --hypothesis-profile=ci

# Mutation testing (slow, run weekly)
mutmut run --paths-to-mutate=api/

# Parallel execution
pytest -n auto
```

---

## Metrics & Goals

| Metric | Current | Week 2 | Week 4 | Target |
|--------|---------|--------|--------|--------|
| Line Coverage | 5.5% | 40% | 70% | 80% |
| Branch Coverage | ~3% | 30% | 60% | 75% |
| Mutation Score | N/A | 50% | 65% | 70% |
| Test Count | ~60 | 150 | 300 | 400 |

---

## Sources

- [Hypothesis Property-Based Testing](https://pytest-with-eric.com/pytest-advanced/hypothesis-testing-python/)
- [pytest-cov Documentation](https://pypi.org/project/pytest-cov/)
- [Mutatest Documentation](https://mutatest.readthedocs.io/)
- [Mutation Testing with cosmic-ray](https://medium.com/agileactors/python-mutation-testing-with-cosmic-ray-or-how-i-stop-worrying-and-love-the-unit-tests-coverage-635cd0e23844)
