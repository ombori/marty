# Marty - Banking Reconciliation Agent

## Purpose

Marty is an autonomous agent that integrates with Open Banking APIs and NetSuite to automate banking reconciliations across a multi-entity corporate group.

## Core Capabilities

- **Fetch bank transactions** from Wise across 13 business entities
- **Match transactions** against NetSuite GL entries
- **Identify intercompany transfers** between group entities
- **Flag discrepancies** for human review
- **Generate reconciliation reports**

## Personality

Marty is inspired by Marty Byrde from Ozark - calm under pressure, sees patterns others miss, and communicates with direct, no-nonsense clarity. See [marty.md](./marty.md) for full personality documentation.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Wise API  │────▶│    Marty    │◀────│  NetSuite   │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  PostgreSQL │
                    └─────────────┘
```

## Entities Covered

| Entity | Jurisdiction | Wise Profile ID |
|--------|--------------|-----------------|
| Phygrid Limited | UK | 19941830 |
| Phygrid S.A. | Luxembourg | 76219117 |
| Phygrid Inc | US | 70350947 |
| PHYGRID AB (PUBL) | Sweden | 52035101 |
| Ombori, Inc | US | 78680339 |
| Ombori AG | Switzerland | 47253364 |
| Fendops Limited | UK | 25587793 |
| Fendops Kft | Hungary | 21069793 |
| NEXORA AB | Sweden | 66668662 |

## Development Standards

### Test-Driven Development (TDD)

All code changes MUST follow TDD:

1. **Write the test first** - Define expected behavior before implementation
2. **Run the test** - Confirm it fails (red)
3. **Write minimal code** - Just enough to pass the test (green)
4. **Refactor** - Clean up while keeping tests passing
5. **Commit** - Pre-commit hooks enforce quality

### Pre-commit Hooks

The following checks run automatically on every commit:

- **ruff** - Linting (fast, replaces flake8/isort/pyupgrade)
- **ruff format** - Code formatting (replaces black)
- **pytest** - Unit tests must pass

Install hooks after cloning:
```bash
pip install -r requirements-dev.txt
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=term-missing

# Run specific test
pytest tests/test_wise.py -v
```

### Code Style

- Python 3.11+
- Type hints required
- Docstrings for public functions
- Max line length: 100 characters

## Key Files

| File | Purpose |
|------|---------|
| `wise.md` | Wise API integration documentation |
| `marty.md` | Agent personality and tone of voice |
| `app/main.py` | FastAPI application entry point |
| `app/config.py` | Configuration management |
| `terraform/` | Kubernetes deployment |

## Environment Variables

```bash
# Wise API
WISE_API_TOKEN=           # Single token for all profiles
WISE_PRIVATE_KEY_PATH=    # Path to SCA signing key

# Database
POSTGRES_HOST=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=

# NetSuite (TBD)
NETSUITE_ACCOUNT_ID=
NETSUITE_CONSUMER_KEY=
NETSUITE_CONSUMER_SECRET=
NETSUITE_TOKEN_ID=
NETSUITE_TOKEN_SECRET=
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |
| `/reconcile/{entity}` | POST | Trigger reconciliation |
| `/transactions/{entity}` | GET | Fetch transactions |
| `/reports/{entity}` | GET | Get reconciliation report |
