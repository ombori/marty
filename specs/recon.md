# Project Recon - Marty Specification

## Responsibilities

1. Fetch Wise transactions across 11 entities
2. Cache in local PostgreSQL
3. Match against GL data from Spectre
4. Generate suggestions with confidence scores
5. Submit to Spectre for approval
6. Notify accounting via Slack
7. Learn from approved patterns (Qdrant vectors)
8. Enrich NetSuite records with full Wise data

## Entities

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
| Ombori Services Limited | Hong Kong | 49911299 |
| OMBORI GROUP SWEDEN AB | Sweden | 52034148 |

## Database Schema (PostgreSQL)

### wise_transactions

Working copy of Wise transactions for matching.

```sql
CREATE TABLE wise_transactions (
    id TEXT PRIMARY KEY,                    -- Wise reference (TRANSFER-1950972714)
    profile_id INTEGER NOT NULL,
    entity_name TEXT NOT NULL,

    type TEXT NOT NULL,                     -- DEBIT/CREDIT
    transaction_type TEXT NOT NULL,         -- TRANSFER, DEPOSIT, CARD, etc.
    date TIMESTAMPTZ NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    currency TEXT NOT NULL,

    description TEXT,
    payment_reference TEXT,
    counterparty_name TEXT,
    counterparty_account TEXT,

    -- FX details
    from_amount DECIMAL(15,2),
    from_currency TEXT,
    exchange_rate DECIMAL(12,8),

    -- Fees
    total_fees DECIMAL(15,2),

    -- Card transaction details
    merchant_name TEXT,
    merchant_category TEXT,
    card_last_four TEXT,
    card_holder_name TEXT,

    -- Running balance
    running_balance DECIMAL(15,2),

    -- Matching state
    match_status TEXT DEFAULT 'pending',    -- pending, submitted, matched, unmatched
    last_match_attempt TIMESTAMPTZ,
    match_attempts INTEGER DEFAULT 0,
    best_confidence DECIMAL(3,2),

    -- Spectre reference
    spectre_suggestion_id UUID,

    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_wise_tx_date ON wise_transactions(date);
CREATE INDEX idx_wise_tx_status ON wise_transactions(match_status);
CREATE INDEX idx_wise_tx_entity ON wise_transactions(entity_name, date);
CREATE INDEX idx_wise_tx_profile ON wise_transactions(profile_id, date);
```

### sync_metadata

Tracks sync state per profile/currency.

```sql
CREATE TABLE sync_metadata (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    currency TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    balance_id INTEGER,
    last_sync_at TIMESTAMPTZ,
    last_sync_end_date DATE,
    sync_status TEXT DEFAULT 'idle',        -- idle, syncing, error
    error_message TEXT,
    transactions_synced INTEGER DEFAULT 0,

    UNIQUE(profile_id, currency)
);
```

### match_candidates

Temporary working table for match scoring.

```sql
CREATE TABLE match_candidates (
    id SERIAL PRIMARY KEY,
    wise_transaction_id TEXT REFERENCES wise_transactions(id) ON DELETE CASCADE,

    -- Candidate GL entry from Spectre
    netsuite_transaction_id TEXT,
    netsuite_line_id INTEGER,
    netsuite_type TEXT,
    netsuite_amount DECIMAL(15,2),
    netsuite_date DATE,
    netsuite_entity TEXT,
    netsuite_memo TEXT,

    -- Match scoring
    confidence_score DECIMAL(3,2),
    match_type TEXT,                        -- exact, fuzzy, llm, pattern
    match_reasons JSONB,                    -- Array of reason strings

    -- Selection
    is_selected BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_candidates_tx ON match_candidates(wise_transaction_id);
CREATE INDEX idx_candidates_selected ON match_candidates(is_selected) WHERE is_selected;
```

## Wise API Client

### Configuration

```python
WISE_API_TOKEN: str          # Single token for all profiles
WISE_PRIVATE_KEY_PATH: str   # Path to SCA signing key
WISE_API_BASE: str = "https://api.wise.com"
```

### Endpoints Used

| Endpoint | Purpose | SCA Required |
|----------|---------|--------------|
| `GET /v2/profiles` | List all profiles | No |
| `GET /v4/profiles/{profileId}/balances?types=STANDARD` | Get balances | No |
| `GET /v1/profiles/{profileId}/balance-statements/{balanceId}/statement.json` | Get transactions | Yes |

### SCA Signing Flow

1. Initial request returns 403 with `x-2fa-approval` header (OTT)
2. Sign OTT with private key: SHA256 + PKCS1v15
3. Retry with `x-2fa-approval: {OTT}` and `X-Signature: {signature}`
4. Session valid for 5 minutes

### Transaction Types

- `TRANSFER` - Outgoing payment
- `DEPOSIT` - Incoming payment
- `CARD` - Card purchase
- `CONVERSION` - Currency exchange
- `MONEY_ADDED` - Top-up
- `INCOMING_CROSS_BALANCE` - Internal transfer in
- `OUTGOING_CROSS_BALANCE` - Internal transfer out
- `DIRECT_DEBIT` - Direct debit
- `BALANCE_INTEREST` - Interest earned
- `BALANCE_ADJUSTMENT` - Manual adjustment

## Spectre API Client

### Configuration

```python
SPECTRE_API_URL: str
SPECTRE_API_KEY: str
```

### Endpoints

```yaml
# Submit suggestion
POST /api/recon/suggestions

# Submit batch
POST /api/recon/suggestions/batch

# Get suggestion status
GET /api/recon/suggestions/{id}

# Get GL entries for matching
GET /api/recon/gl-entries

# Get patterns
GET /api/recon/patterns

# Submit learned pattern
POST /api/recon/patterns

# Enrich NetSuite transaction
POST /api/recon/enrich
```

## Matching Algorithm

### Tier 1: Exact Match (confidence 0.95-1.00)

- Amount exact match (to cent)
- Date within 1 day
- One of: payment reference contains NetSuite tranid, counterparty IBAN matches, pre-approved pattern

### Tier 2: Fuzzy Match (confidence 0.70-0.94)

- Amount within tolerance (same currency: ±0.01, cross-currency: ±2%)
- Date within 5 days
- One of: counterparty name similarity > 85%, payment reference partial match, amount + entity match

### Tier 3: LLM Match (confidence 0.50-0.89)

- Parse shorthand references
- Infer invoice numbers from free text
- Handle company name variations
- Explain reasoning

### Tier 4: Pattern Match (confidence boost +0.10-0.25)

- Embed description + counterparty + reference
- Search Qdrant for similar approved patterns
- Boost confidence if cosine similarity > 0.85

### Intercompany Detection

Transaction is IC if:
- Counterparty name matches entity list (normalized)
- Counterparty IBAN in known entity bank accounts
- Payment reference contains "IC" or entity name

## Confidence Scoring

```python
BASE_SCORES = {
    "exact_all": 1.00,
    "exact_amount_ref": 0.95,
    "exact_amount_date": 0.90,
    "fuzzy_high": 0.85,
    "fuzzy_medium": 0.75,
    "llm_confident": 0.80,
    "llm_uncertain": 0.60,
}

ADJUSTMENTS = {
    "is_intercompany": +0.05,
    "pattern_match": +0.10 to +0.25,
    "repeat_counterparty": +0.05,
    "fx_variance_high": -0.15,
    "date_drift_high": -0.10,
}

THRESHOLDS = {
    "auto_approve": 0.95,
    "suggest": 0.80,
    "review": 0.60,
    "manual": 0.00,
}
```

## File Structure

```
app/
├── models/
│   └── recon.py             # SQLAlchemy models
├── services/
│   ├── wise.py              # Wise API client
│   ├── spectre.py           # Spectre API client
│   ├── cache.py             # Redis cache layer
│   ├── vectors.py           # Qdrant client
│   ├── slack.py             # Slack notifier
│   ├── scheduler.py         # Cron scheduler
│   ├── reconcile.py         # Main orchestrator
│   ├── learning.py          # Pattern learner
│   └── matching/
│       ├── __init__.py
│       ├── exact.py
│       ├── fuzzy.py
│       ├── llm.py
│       ├── intercompany.py
│       └── confidence.py
└── api/
    └── reconcile.py         # Manual trigger endpoints
```
