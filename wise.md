# Wise API Integration

## Entities

| Entity | Profile ID | Jurisdiction |
|--------|-----------|--------------|
| Phygrid Limited | 19941830 | UK |
| Phygrid S.A. | 76219117 | Luxembourg |
| Phygrid Inc | 70350947 | US |
| PHYGRID AB (PUBL) | 52035101 | Sweden |
| Ombori, Inc | 78680339 | US |
| Ombori AG | 47253364 | Switzerland |
| Fendops Limited | 25587793 | UK |
| Fendops Kft | 21069793 | Hungary |
| NEXORA AB | 66668662 | Sweden |
| Ombori Services Sweden AB | 1098953 | Sweden |
| Ombori Services Limited | 49911299 | Hong Kong |
| OMBORI GROUP SWEDEN AB | 52034148 | Sweden |
| OMB OPERATIONS AB | 52329081 | Sweden |

---

## API Token Setup

### Key Discovery: One Token, All Profiles

A single personal API token provides access to **all business profiles** the user has permissions on. The token is user-scoped, not profile-scoped.

### Prerequisites

- 2-step verification enabled on your Wise account
- Business accounts must be verified
- Must use website (not mobile app)

### Step 1: Create API Token (once)

1. Go to [wise.com](https://wise.com) and log in
2. Select any business profile from the profile switcher
3. Go to **Settings** → **Integrations and tools**
4. Click **API tokens**
5. Click **Add new token**

The same token will be visible and work across all your business profiles.

### Step 2: Configure Token Permissions

Select read-only permissions for reconciliation:

- ✅ Read your profile info
- ✅ Read your balances
- ✅ Read your account statements
- ✅ Read your transactions
- ❌ Create and manage transfers (not needed)
- ❌ Create and manage recipients (not needed)

Name the token something identifiable (e.g., `marty-reconciliation`).

### Step 3: Generate SCA Key Pair (once)

Wise uses Strong Customer Authentication (SCA) for protected endpoints.

```bash
# Generate private key (keep this secret)
openssl genrsa -out wise_private.pem 2048

# Extract public key (upload to Wise)
openssl rsa -in wise_private.pem -pubout -out wise_public.pem
```

### Step 4: Register Public Key

Register the public key once - it applies to all profiles:

1. Go to **Settings** → **Integrations and tools** → **API tokens**
2. Click **Manage public keys**
3. Click **Add new key**
4. Paste contents of `wise_public.pem`
5. Save

### Step 5: Get All Profile IDs

Fetch all profiles accessible to your token:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "https://api.wise.com/v2/profiles" | jq '.[] | {id, businessName, type}'
```

**Important:** Use `/v2/profiles` (not v1) to get all profiles.

---

## Environment Configuration

Create `.env` file with single token and all profile IDs:

```env
# Wise API Configuration
# Single token provides access to all profiles
WISE_PRIVATE_KEY_PATH=./wise_private.pem
WISE_API_TOKEN=<your-token>

# Profile IDs - Primary entities
WISE_PROFILE_PHYGRID_LTD=19941830
WISE_PROFILE_PHYGRID_SA=76219117
WISE_PROFILE_PHYGRID_INC=70350947
WISE_PROFILE_PHYGRID_AB=52035101
WISE_PROFILE_OMBORI_INC=78680339
WISE_PROFILE_OMBORI_AG=47253364
WISE_PROFILE_FENDOPS_LTD=25587793
WISE_PROFILE_FENDOPS_KFT=21069793
WISE_PROFILE_NEXORA=66668662

# Profile IDs - Additional entities
WISE_PROFILE_OMBORI_SERVICES_SE=1098953
WISE_PROFILE_OMBORI_SERVICES_HK=49911299
WISE_PROFILE_OMBORI_GROUP_SE=52034148
WISE_PROFILE_OMB_OPERATIONS=52329081
```

---

## API Endpoints

| Endpoint | Purpose | SCA Required |
|----------|---------|--------------|
| `GET /v2/profiles` | List all profiles for user | No |
| `GET /v4/profiles/{profileId}/balances?types=STANDARD` | Get all currency balances | No |
| `GET /v1/profiles/{profileId}/balance-statements/{balanceId}/statement.json` | Get transactions | Yes |

**Note:** Use `/v2/profiles` (not v1) to get all profiles the user has access to.

---

## SCA (Strong Customer Authentication)

The balance statement endpoint requires SCA for EU/UK profiles. The flow:

1. **Initial request returns 403** with `x-2fa-approval` header containing a one-time token (OTT)
2. **Sign the OTT** with your private key using SHA256 + PKCS1v15
3. **Retry request** with signed headers

### Signing Example (bash)

```bash
# Get the OTT from the 403 response header
OTT="<value from x-2fa-approval header>"

# Sign with private key
SIGNATURE=$(echo -n "$OTT" | openssl dgst -sha256 -sign wise_private.pem | base64 | tr -d '\n')

# Retry with signed headers
curl -H "Authorization: Bearer $TOKEN" \
     -H "x-2fa-approval: $OTT" \
     -H "X-Signature: $SIGNATURE" \
     "https://api.wise.com/v1/profiles/{profileId}/balance-statements/{balanceId}/statement.json?..."
```

### SCA Session Duration

Once authenticated, the SCA session is valid for **5 minutes**. Subsequent requests within this window don't need re-signing.

---

## Transaction Data Structure

### Statement Response

```json
{
  "accountHolder": {
    "type": "BUSINESS",
    "profileId": 25587793,
    "businessName": "Fendops Limited",
    "registrationNumber": "12742757"
  },
  "bankDetails": [{
    "accountNumbers": [{"accountType": "IBAN", "accountNumber": "BE10 9672 5829 5404"}],
    "bankCodes": [{"scheme": "Swift/BIC", "value": "TRWIBEB1XXX"}]
  }],
  "transactions": [...],
  "startOfStatementBalance": {"value": 11470.65, "currency": "EUR"},
  "endOfStatementBalance": {"value": 630.16, "currency": "EUR"}
}
```

### Transaction Fields (all types)

| Field | Description |
|-------|-------------|
| `type` | DEBIT or CREDIT |
| `date` | ISO 8601 timestamp |
| `amount.value` | Transaction amount (negative for debits) |
| `amount.currency` | Currency code |
| `totalFees.value` | Fees charged |
| `runningBalance.value` | Balance after transaction |
| `referenceNumber` | Unique ID (e.g., `TRANSFER-1950972714`) |
| `details.type` | Transaction type: TRANSFER, DEPOSIT, CARD, CONVERSION, etc. |
| `details.description` | Human-readable description |
| `details.paymentReference` | Payment reference / memo |
| `exchangeDetails` | FX rate info (when currency conversion occurs) |

### Transaction Types

#### TRANSFER (outgoing payments)

```json
{
  "details": {
    "type": "TRANSFER",
    "description": "Sent money to Ombori AG",
    "recipient": {
      "name": "Ombori AG",
      "bankAccount": "BE82967831096568"
    },
    "paymentReference": "Fendops UK/AG"
  }
}
```

#### DEPOSIT (incoming payments)

```json
{
  "details": {
    "type": "DEPOSIT",
    "description": "Received money from KLARNA BANK AB",
    "senderName": "KLARNA BANK AB",
    "senderAccount": "(NDEASESSXXX) SE8595000099602608824831",
    "paymentReference": "INVOL202458/1000087004"
  }
}
```

#### CARD (card transactions)

```json
{
  "details": {
    "type": "CARD",
    "description": "Card transaction of 564.58 USD",
    "merchant": {
      "name": "Vouch Insurance",
      "city": "VOUCH.US",
      "country": "US",
      "category": "6300 R Insurance Sales, Underwri"
    },
    "cardLastFourDigits": "3021",
    "cardHolderFullName": "Andreas Hassellöf"
  },
  "exchangeDetails": {
    "toAmount": {"value": 452.26, "currency": "USD"},
    "fromAmount": {"value": 390.49, "currency": "EUR"},
    "rate": 1.16350
  }
}
```

### All Transaction Types

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

---

## Reconciliation Value

**Intercompany transfers are clearly identifiable:**
- Recipient/sender name matches group entities
- Bank accounts can be cross-referenced
- Payment references provide context

**Example intercompany flow detected:**
```
Ombori AG → Fendops Limited (€32,200, ref: 825840)
Fendops Limited → Fendops Kft (€25,000, ref: Fendops Limited)
```

---

## Query Parameters

```
GET /v1/profiles/{profileId}/balance-statements/{balanceId}/statement.json
  ?currency=EUR
  &intervalStart=2026-01-01T00:00:00.000Z
  &intervalEnd=2026-02-03T23:59:59.999Z
  &type=COMPACT
```

| Parameter | Description |
|-----------|-------------|
| `currency` | Currency code (must match balance) |
| `intervalStart` | Start date (ISO 8601) |
| `intervalEnd` | End date (ISO 8601) |
| `type` | `COMPACT` or `FLAT` |

**Limit:** Maximum 469 days between start and end.

**Formats:** Replace `.json` with `.csv`, `.pdf`, `.xlsx`, `.xml` (CAMT.053), `.mt940`, or `.qif`.

---

## Security Notes

- Never commit `.env` or `wise_private.pem` to git
- Add to `.gitignore`:
  ```
  .env
  *.pem
  secrets/
  ```
- Tokens are read-only - no payment risk
- Rotate tokens periodically

---

## References

- [Wise API Getting Started](https://wise.com/help/articles/2958107/getting-started-with-the-api)
- [Wise API Reference](https://docs.wise.com/api-reference)
- [Balance Statement API](https://docs.wise.com/api-reference/balance-statement)
- [SCA Over API](https://docs.wise.com/guides/developer/auth-and-security/sca-over-api)
- [SCA Signing Examples (GitHub)](https://github.com/transferwise/digital-signatures-examples)
