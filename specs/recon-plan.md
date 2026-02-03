# Project Recon - Banking Reconciliation System

## Overview

Automated bank reconciliation system that matches Wise transactions against NetSuite GL entries, suggests matches for approval, and learns patterns over time.

**Repositories:**
- `agent-marty` - Autonomous agent that fetches, matches, suggests
- `spectre` - Approval workflow, NetSuite execution, web UI

---

## NetSuite Integration Strategy

### Current State: Automatic Wise-NetSuite Integration

Based on research, the existing automatic integration between Wise and NetSuite likely uses one of these methods:
- **Bank Feeds SuiteApp** - Aggregator-based (Yodlee/MX) with limited data fields
- **Manual CSV Import** - Periodic file uploads with basic transaction data
- **Third-party connector** - e.g., Celigo, with varying data richness

### Limitations of Automatic Integration

1. **Limited Transaction Data** - Bank feeds typically provide:
   - Date, amount, currency
   - Basic description/memo
   - Transaction type (credit/debit)

   **Missing from Wise API**:
   - Full counterparty details (name, IBAN, bank)
   - Payment reference/invoice numbers
   - FX rate and conversion details
   - Fee breakdown
   - Card transaction details (merchant, category, cardholder)
   - Running balance

2. **No Intercompany Context** - Cannot automatically detect IC transfers between group entities

3. **No Pattern Learning** - No ability to learn from historical matches

4. **Rate Limits** - Bank Feeds limited to 10,000 txns/import, daily refresh only

### Recommendation: Hybrid Approach

**Do NOT disable the automatic integration entirely.** Instead, implement a hybrid approach:

#### Option A: Marty as Enrichment Layer (Recommended)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Bank Feeds  │────▶│  NetSuite   │◀────│   Marty     │
│ (existing)  │     │  (base txn) │     │ (enrichment)│
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                    Imported Bank Transaction
                    + Enrichment custom fields
```

**How it works:**
1. Bank Feeds imports basic transaction → creates `importedbanktransaction` record
2. Marty fetches full Wise data via API
3. Marty matches by reference number or amount/date
4. Marty enriches the existing record with custom fields

**NetSuite Custom Fields to Add:**
```
Custom Field ID              | Type    | Purpose
-----------------------------|---------|----------------------------------------
custbody_wise_counterparty   | Text    | Full counterparty name
custbody_wise_iban           | Text    | Counterparty IBAN
custbody_wise_payment_ref    | Text    | Payment reference/memo
custbody_wise_fx_rate        | Decimal | Exchange rate used
custbody_wise_from_amount    | Currency| Original currency amount (FX)
custbody_wise_from_currency  | Text    | Original currency code (FX)
custbody_wise_fees           | Currency| Fees charged
custbody_wise_is_ic          | Checkbox| Is intercompany transfer
custbody_wise_ic_entity      | Text    | Counterparty entity name (if IC)
custbody_wise_merchant       | Text    | Card merchant name
custbody_wise_card_last4     | Text    | Card last 4 digits
custbody_wise_enriched       | Checkbox| Has been enriched by Marty
custbody_wise_enriched_at    | DateTime| When enrichment occurred
```

**Pros:**
- Maintains existing workflow (no disruption)
- Single source of truth in NetSuite
- Leverages Bank Feeds reliability for basic import
- Adds rich context without duplicating data

**Cons:**
- Requires matching between Bank Feeds and Wise API data
- Slight delay in enrichment (async process)

#### Option B: Marty as Primary Importer (Alternative)

```
┌─────────────┐     ┌─────────────┐
│   Wise API  │────▶│   Marty     │────▶│  NetSuite  │
└─────────────┘     └─────────────┘     └────────────┘
                                         CSV Import or
                                         SuiteScript create
```

**How it works:**
1. Disable Bank Feeds connection
2. Marty fetches Wise transactions daily
3. Marty creates bank import records directly via:
   - CSV Import (scheduled task)
   - SuiteScript API (custom record creation)

**Pros:**
- Full control over all data
- Richer initial data
- No matching step needed

**Cons:**
- Bank transaction records have limited SuiteScript support
- Must handle reliability/retry that Bank Feeds provides
- More complex initial setup

### NetSuite API Capabilities

Based on research:

| Method | Support | Notes |
|--------|---------|-------|
| **CSV Import** | ✅ Full | Up to 10k txns, standard format |
| **SuiteScript Record Create** | ⚠️ Partial | `deposit` record supported; `importedbanktransaction` limited |
| **REST API** | ❌ Limited | No direct bank transaction endpoint |
| **Bank Statement Parser Plug-in** | ✅ Full | Can create custom parsers |
| **Custom Records + SuiteScript** | ✅ Full | Create custom transaction types |

### Final Recommendation

**Proceed with Option A (Enrichment Layer)** because:

1. Less disruption to existing workflows
2. Bank Feeds handles reliability/retry
3. Enrichment can be async and fault-tolerant
4. NetSuite remains single source of truth
5. If Bank Feeds ever has issues, can fall back to Option B

**Implementation in Marty:**
1. Fetch full Wise transaction data (we have this)
2. Query NetSuite for imported bank transactions (via Spectre API)
3. Match by reference number → amount/date → description similarity
4. Update NetSuite record with enrichment fields (via Spectre API)
5. Log enrichment status in local PostgreSQL

---

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────┐     ┌─────────────────────┐
│   Wise API  │────▶│              MARTY                   │────▶│       SPECTRE       │
└─────────────┘     │  ┌──────────┐  ┌──────────────────┐  │     │  ┌───────────────┐  │
                    │  │ Postgres │  │ Matching Engine  │  │     │  │  PostgreSQL   │  │
                    │  │ (txns)   │  └──────────────────┘  │     │  │ (approvals)   │  │
                    │  └──────────┘           │            │     │  └───────────────┘  │
                    │  ┌──────────┐           ▼            │     │         │           │
                    │  │  Redis   │  ┌──────────────────┐  │────▶│  ┌───────────────┐  │
                    │  │ (cache)  │  │ Spectre Client   │  │     │  │   Web UI      │  │
                    │  └──────────┘  └──────────────────┘  │     │  │ (approvals)   │  │
                    │  ┌──────────┐           │            │     │  └───────────────┘  │
                    │  │  Qdrant  │           ▼            │     │         │           │
                    │  │(patterns)│  ┌──────────────────┐  │     │  ┌───────────────┐  │
                    │  └──────────┘  │ Slack Notifier   │  │     │  │   NetSuite    │  │
                    └────────────────┴──────────────────┴──┘     │  │  (execution)  │  │
                                                                 │  └───────────────┘  │
                                                                 └─────────────────────┘
```

---

## Data Storage Strategy

### Marty (Working Data)

| Store | Purpose | Data |
|-------|---------|------|
| **PostgreSQL** | Transaction state | Wise transactions, match attempts, sync metadata |
| **Redis** | Hot cache | IC account mappings, entity lookups, rate limit counters, SCA session tokens |
| **Qdrant** | Pattern matching | Transaction description embeddings, approved pattern vectors for similarity search |

### Spectre (Persistent/Approval Data via APIs)

| Table | Purpose | Data |
|-------|---------|------|
| `recon_suggestions` | Approval queue | Suggestions from Marty awaiting/completed review |
| `recon_patterns` | Learning | User-approved patterns for auto-matching |
| `recon_rules` | Automation | Rules for auto-approval by entity/amount/type |
| `recon_batches` | Audit | Batch metadata and statistics |

---

## Database Schemas

### Marty PostgreSQL Schema

```sql
-- Wise transactions (working copy)
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

    -- Matching state (Marty-local)
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

-- Sync metadata
CREATE TABLE sync_metadata (
    profile_id INTEGER PRIMARY KEY,
    entity_name TEXT NOT NULL,
    last_sync_at TIMESTAMPTZ,
    last_sync_end_date DATE,
    sync_status TEXT DEFAULT 'idle',
    error_message TEXT,
    transactions_synced INTEGER DEFAULT 0
);

-- Match candidates (temporary working table)
CREATE TABLE match_candidates (
    id SERIAL PRIMARY KEY,
    wise_transaction_id TEXT REFERENCES wise_transactions(id),

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

### Spectre PostgreSQL Schema

```sql
-- Reconciliation suggestions (from Marty)
CREATE TABLE recon_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Wise transaction
    wise_transaction_id TEXT NOT NULL,
    wise_profile_id INTEGER NOT NULL,
    entity_name TEXT NOT NULL,
    transaction_date TIMESTAMPTZ NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    currency TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    description TEXT,
    counterparty TEXT,

    -- Match details
    match_type TEXT NOT NULL,               -- exact, fuzzy, llm, pattern, unmatched
    confidence_score DECIMAL(3,2) NOT NULL,
    match_explanation TEXT,
    match_reasons JSONB,

    -- Suggested NetSuite match
    netsuite_transaction_id TEXT,
    netsuite_line_id INTEGER,
    netsuite_type TEXT,
    suggested_account_id INTEGER,
    suggested_account_name TEXT,

    -- Intercompany
    is_intercompany BOOLEAN DEFAULT FALSE,
    counterparty_entity TEXT,

    -- Workflow
    status TEXT DEFAULT 'pending',          -- pending, approved, rejected, auto_approved, executed, failed
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    rejection_reason TEXT,

    -- Execution
    executed_journal_id TEXT,
    executed_at TIMESTAMPTZ,
    execution_error TEXT,

    -- Batch
    batch_id UUID REFERENCES recon_batches(id),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(wise_transaction_id)
);

CREATE INDEX idx_suggestions_status ON recon_suggestions(status);
CREATE INDEX idx_suggestions_entity ON recon_suggestions(entity_name, status);
CREATE INDEX idx_suggestions_date ON recon_suggestions(transaction_date);

-- Batches (grouping of suggestions)
CREATE TABLE recon_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,

    total_count INTEGER DEFAULT 0,
    matched_count INTEGER DEFAULT 0,
    unmatched_count INTEGER DEFAULT 0,
    auto_approved_count INTEGER DEFAULT 0,
    pending_count INTEGER DEFAULT 0,

    status TEXT DEFAULT 'processing',       -- processing, complete, error

    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Patterns (self-learning)
CREATE TABLE recon_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    pattern_type TEXT NOT NULL,             -- counterparty, reference, amount_range, description
    pattern_value TEXT NOT NULL,
    is_regex BOOLEAN DEFAULT FALSE,

    target_type TEXT NOT NULL,              -- vendor, customer, account, subsidiary
    target_netsuite_id TEXT NOT NULL,
    target_name TEXT NOT NULL,

    is_auto_approve BOOLEAN DEFAULT FALSE,
    confidence_boost DECIMAL(3,2) DEFAULT 0.10,

    times_used INTEGER DEFAULT 0,
    times_approved INTEGER DEFAULT 0,
    times_rejected INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,

    created_by UUID REFERENCES users(id),
    description TEXT,

    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(pattern_type, pattern_value, target_type)
);

CREATE INDEX idx_patterns_active ON recon_patterns(is_active, pattern_type);

-- Auto-approval rules
CREATE TABLE recon_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,

    conditions JSONB NOT NULL,              -- {"entity": [...], "max_amount": N, "min_confidence": 0.9}

    auto_approve BOOLEAN DEFAULT FALSE,
    notify_slack BOOLEAN DEFAULT TRUE,

    is_active BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,

    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Qdrant Collections (Marty)

```yaml
# Collection: transaction_patterns
# Purpose: Semantic search for similar approved transactions

fields:
  - id: string (UUID)
  - wise_transaction_id: string
  - entity_name: string
  - transaction_type: string
  - counterparty: string
  - description: string
  - payment_reference: string
  - amount: float
  - currency: string
  - matched_to: string (NetSuite ID)
  - match_type: string
  - approved_at: datetime

vector:
  size: 1536  # text-embedding-3-small
  distance: Cosine

# Usage: When matching new transaction, embed description + counterparty,
# search for similar approved patterns, boost confidence if found
```

### Redis Keys (Marty)

```yaml
# IC Account Mappings (from Spectre, cached 1 hour)
ic:accounts:{subsidiary_id}:
  type: hash
  fields:
    account_id: int
    account_number: string
    account_name: string
    counterparty_subsidiary: string
  ttl: 3600

# Entity Lookup
entities:names:
  type: hash
  fields: {normalized_name: entity_key}
  ttl: 86400

entities:{entity_key}:
  type: hash
  fields:
    wise_profile_id: int
    netsuite_subsidiary_id: int
    name: string
    currency: string

# SCA Session
sca:session:{profile_id}:
  type: string
  value: expiry_timestamp
  ttl: 300  # 5 minutes

# Rate Limiting
ratelimit:wise:
  type: string
  value: request_count
  ttl: 1  # Per second

# GL Entry Cache (from Spectre, per query)
gl:entries:{subsidiary_id}:{start_date}:{end_date}:
  type: string (JSON)
  ttl: 600  # 10 minutes
```

---

## API Contract

### Marty → Spectre

**Authentication**: `X-API-Key` header

```yaml
# Submit suggestion
POST /api/recon/suggestions
Body:
  wise_transaction_id: string
  wise_profile_id: int
  entity_name: string
  transaction_date: datetime
  amount: decimal
  currency: string
  transaction_type: string
  description: string?
  counterparty: string?
  match_type: string
  confidence_score: decimal
  match_explanation: string?
  match_reasons: string[]
  netsuite_transaction_id: string?
  netsuite_line_id: int?
  netsuite_type: string?
  suggested_account_id: int?
  suggested_account_name: string?
  is_intercompany: bool
  counterparty_entity: string?
Response: { id: uuid, status: string }

# Submit batch
POST /api/recon/suggestions/batch
Body: { entity_name: string, start_date: date, end_date: date, suggestions: [...] }
Response: { batch_id: uuid, count: int }

# Get suggestion status
GET /api/recon/suggestions/{id}
Response: { id, status, reviewed_by, reviewed_at, executed_journal_id, ... }

# Get GL entries for matching
GET /api/recon/gl-entries
Params: subsidiary_id, start_date, end_date, account_types[], unreconciled_only
Response: { items: [...], total: int }

# Get patterns
GET /api/recon/patterns
Params: active_only, auto_approve_only
Response: { items: [...] }

# Submit learned pattern
POST /api/recon/patterns
Body: { pattern_type, pattern_value, is_regex, target_type, target_netsuite_id, target_name, description }
Response: { id: uuid }

# Enrich NetSuite transaction (NEW)
POST /api/recon/enrich
Body:
  netsuite_transaction_id: string
  wise_transaction_id: string
  enrichment_data:
    counterparty_name: string?
    counterparty_iban: string?
    payment_reference: string?
    fx_rate: decimal?
    from_amount: decimal?
    from_currency: string?
    fees: decimal?
    is_intercompany: bool?
    ic_entity: string?
    merchant_name: string?
    card_last4: string?
Response: { success: bool, netsuite_transaction_id: string }
```

### Spectre Internal (Web UI)

```yaml
# List suggestions for review
GET /api/recon/suggestions
Params: status, entity, start_date, end_date, min_confidence, page, limit
Response: { items: [...], total: int, page: int }

# Approve/reject suggestion
PATCH /api/recon/suggestions/{id}
Body: { status: "approved" | "rejected", rejection_reason?: string }
Response: { id, status, reviewed_at }

# Bulk approve
POST /api/recon/suggestions/bulk-approve
Body: { ids: uuid[] }
Response: { approved: int, failed: int }

# Pattern CRUD
GET    /api/recon/patterns
POST   /api/recon/patterns
PATCH  /api/recon/patterns/{id}
DELETE /api/recon/patterns/{id}

# Rules CRUD
GET    /api/recon/rules
POST   /api/recon/rules
PATCH  /api/recon/rules/{id}
DELETE /api/recon/rules/{id}

# Dashboard stats
GET /api/recon/stats
Params: entity?, start_date?, end_date?
Response: { pending: int, approved: int, rejected: int, auto_approved: int, by_entity: {...} }
```

---

## Matching Algorithm

### Tier 1: Exact Match (confidence 0.95-1.00)

```
1. Amount exact match (to cent)
2. Date within 1 day
3. One of:
   - Payment reference contains NetSuite tranid
   - Counterparty IBAN matches known entity account
   - Pre-approved pattern exact match
```

### Tier 2: Fuzzy Match (confidence 0.70-0.94)

```
1. Amount within tolerance:
   - Same currency: ±0.01
   - Cross-currency: ±2% (FX variance)
2. Date within 5 days
3. One of:
   - Counterparty name similarity > 85%
   - Payment reference partial match
   - Amount + entity match with no conflicts
```

### Tier 3: LLM Match (confidence 0.50-0.89)

```
Used when Tier 1/2 fail but candidates exist:
1. Parse shorthand (INV-2024-001 → Invoice 2024-001)
2. Infer invoice numbers from free text
3. Handle company name variations
4. Explain reasoning

Prompt includes:
- Transaction details
- Top 5 GL candidates
- Entity context
- Ask for match + confidence + explanation
```

### Tier 4: Pattern Match (confidence boost +0.10-0.25)

```
After Tier 1-3, check Qdrant for similar approved transactions:
1. Embed: description + counterparty + reference
2. Search approved patterns (cosine similarity > 0.85)
3. If found: boost confidence by pattern.confidence_boost
4. If pattern.is_auto_approve and final_confidence >= 0.90: auto-approve
```

### Intercompany Detection

```
Transaction is IC if:
1. Counterparty name matches ENTITY_NAMES list (normalized)
2. Counterparty IBAN in known entity bank accounts
3. Payment reference contains "IC" or entity name

IC Handling:
1. Look up counterparty's subsidiary ID
2. Find matching C/A account (1563 {Name} - C/A pattern)
3. Match to IC journal entry on both sides
4. Higher base confidence (IC transfers are reliable)
```

---

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

---

## Slack Notifications

```yaml
# Daily digest
Channel: #accounting-alerts
Trigger: Daily at 9am or after batch completes
Content:
  - Count pending approvals
  - Total amount pending
  - Entities with items
  - Link to Spectre approval page

# Discrepancy alert
Channel: #accounting-alerts
Trigger: When unmatched transactions > threshold or large amount
Content:
  - Entity name
  - Transaction details
  - Suggested action
  - Link to review

# Reconciliation complete
Channel: #accounting-alerts
Trigger: After batch finishes
Content:
  - Entity name
  - Matched / Unmatched / Auto-approved counts
  - Link to batch details
```

---

## Implementation Phases

### Phase 1: Foundation (Marty)

- [ ] Create `specs/recon.md` (this file → copy to specs/recon.md)
- [ ] WiseClient with SCA signing (`app/services/wise.py`)
- [ ] PostgreSQL models (`app/models/recon.py`)
- [ ] Alembic migrations
- [ ] Transaction fetch + store logic
- [ ] Tests: WiseClient mocked, model tests

### Phase 1: Foundation (Spectre)

- [ ] Create `specs/recon.md`
- [ ] API key auth middleware (`app/api/deps.py`)
- [ ] PostgreSQL models (`app/models/recon.py`)
- [ ] Alembic migrations
- [ ] Basic CRUD endpoints (`app/api/recon/`)
- [ ] Tests: endpoint tests

### Phase 2: Matching Engine (Marty)

- [ ] SpectreClient (`app/services/spectre.py`)
- [ ] Redis cache layer (`app/services/cache.py`)
- [ ] Exact matcher (`app/services/matching/exact.py`)
- [ ] Fuzzy matcher (`app/services/matching/fuzzy.py`)
- [ ] Confidence scorer (`app/services/matching/confidence.py`)
- [ ] IC detector (`app/services/matching/intercompany.py`)
- [ ] Tests: matching scenarios

### Phase 2: Matching Engine (Spectre)

- [ ] GL entries endpoint (`app/api/recon/gl_entries.py`)
- [ ] Enhanced IC mapping endpoint
- [ ] NetSuite enrichment endpoint (`app/api/recon/enrich.py`)
- [ ] Tests: GL query tests

### Phase 3: LLM + Patterns (Marty)

- [ ] LLM matcher (`app/services/matching/llm.py`)
- [ ] Qdrant integration (`app/services/vectors.py`)
- [ ] Pattern learner (`app/services/learning.py`)
- [ ] Orchestrator (`app/services/reconcile.py`)
- [ ] Tests: LLM mocked, pattern tests

### Phase 3: LLM + Patterns (Spectre)

- [ ] Pattern endpoints (`app/api/recon/patterns.py`)
- [ ] Rules engine (`app/services/rules.py`)
- [ ] Tests: pattern CRUD, rules evaluation

### Phase 4: Web UI (Spectre)

- [ ] Reconciliation page (`web/src/pages/Reconciliation.tsx`)
- [ ] Pattern management page (`web/src/pages/Patterns.tsx`)
- [ ] Discrepancy dashboard (`web/src/pages/Discrepancies.tsx`)
- [ ] Confidence indicator component
- [ ] Bulk actions
- [ ] Tests: component tests

### Phase 5: Slack + Scheduler (Marty)

- [ ] Slack notifier (`app/services/slack.py`)
- [ ] Reconciliation scheduler (`app/services/scheduler.py`)
- [ ] CLI commands for manual runs
- [ ] Tests: Slack mocked

### Phase 6: Integration + Polish

- [ ] End-to-end testing
- [ ] Error handling hardening
- [ ] Monitoring (Prometheus metrics)
- [ ] Documentation
- [ ] Performance tuning
- [ ] Security review

---

## File Structure

### Marty

```
agent-marty/
├── specs/
│   ├── recon.md                 # Marty-specific spec
│   └── recon-plan.md            # This plan document
├── app/
│   ├── models/
│   │   └── recon.py             # SQLAlchemy models
│   ├── services/
│   │   ├── wise.py              # Wise API client
│   │   ├── spectre.py           # Spectre API client
│   │   ├── cache.py             # Redis cache layer
│   │   ├── vectors.py           # Qdrant client
│   │   ├── slack.py             # Slack notifier
│   │   ├── scheduler.py         # Cron scheduler
│   │   ├── reconcile.py         # Main orchestrator
│   │   ├── learning.py          # Pattern learner
│   │   └── matching/
│   │       ├── __init__.py
│   │       ├── exact.py
│   │       ├── fuzzy.py
│   │       ├── llm.py
│   │       ├── intercompany.py
│   │       └── confidence.py
│   └── api/
│       └── reconcile.py         # Manual trigger endpoints
├── migrations/
│   └── versions/
│       └── 001_recon_tables.py
└── tests/
    ├── test_wise.py
    ├── test_matching.py
    ├── test_spectre_client.py
    └── test_reconcile.py
```

### Spectre

```
spectre/server/
├── specs/
│   └── recon.md                 # Spectre-specific spec
├── app/
│   ├── models/
│   │   └── recon.py             # SQLAlchemy models
│   ├── schemas/
│   │   └── recon.py             # Pydantic schemas
│   ├── services/
│   │   └── rules.py             # Auto-approval rules engine
│   └── api/
│       └── recon/
│           ├── __init__.py
│           ├── suggestions.py
│           ├── patterns.py
│           ├── rules.py
│           ├── gl_entries.py
│           └── enrich.py        # NEW: NetSuite enrichment
├── migrations/
│   └── versions/
│       └── xxx_recon_tables.py
└── tests/
    └── test_recon_api.py

spectre/web/src/
├── pages/
│   ├── Reconciliation.tsx
│   ├── Patterns.tsx
│   └── Discrepancies.tsx
├── components/
│   └── ConfidenceIndicator.tsx
└── api/
    └── recon.ts                 # API client
```

---

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
| Ombori Services Limited | Hong Kong | 49911299 |
| OMBORI GROUP SWEDEN AB | Sweden | 52034148 |

---

## Verification Checklist

1. **Unit tests pass**: `pytest` in both repos
2. **Marty can fetch Wise transactions**: Manual test with one entity
3. **Marty can submit to Spectre**: API key auth works
4. **Web UI shows suggestions**: Navigate to /reconciliation
5. **Approval updates status**: Click approve, verify in DB
6. **Pattern learning works**: Approve low-confidence match, pattern created
7. **Slack notifications fire**: Test webhook
8. **End-to-end**: Fetch → Match → Submit → Approve → Verify
9. **Enrichment works**: Wise data enriches NetSuite record (NEW)

---

## Sources

### NetSuite API Research

- [NetSuite Bank Data Import](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_N1550803.html)
- [NetSuite Bank Statement File Import](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_159167524175.html)
- [NetSuite Bank Data Matching and Reconciliation](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_4842302228.html)
- [NetSuite Bank Feeds SuiteApp Limitations](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_158436030865.html)
- [Using SuiteScript for Transaction Records](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4453706706.html)
- [NetSuite Deposit Record](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_3898858977.html)
- [Sort Out NetSuite's Banking Import Options](https://blog.prolecto.com/2022/10/09/sort-out-netsuites-banking-import-options-including-a-how-to-for-custom-plug-in-approaches/)
- [Automate NetSuite Bank Deposits with Multiple Currencies via SuiteScript](https://blog.prolecto.com/2014/03/19/automate-netsuite-bank-deposits-with-multiple-currencies-via-suitescript/)

### AI Reconciliation Research

- [AI Reconciliation Use Cases](https://www.ledge.co/content/ai-reconciliation)
- [GenAI for Data Reconciliation](https://shashankguda.medium.com/data-reconciliation-with-genai-de7e4cd707da)
- [Modern Treasury AI Reconciliation](https://www.moderntreasury.com/journal/adding-ai-to-modern-treasury-reconciliation)
- [ML Transaction Matching Guide](https://www.operartis.com/post/a-buyer-s-guide-to-bank-reconciliation-ai-machine-learning-transaction-matching)
