# Wise API Integration

## Entities

| Entity | Jurisdiction | Profile Type |
|--------|--------------|--------------|
| Phygrid Limited | UK | Business |
| Phygrid S.A. | EU | Business |
| Phygrid Inc | US | Business |
| PHYGRID AB (PUBL) | Sweden | Business |
| Ombori, Inc | US | Business |
| Ombori AG | Switzerland | Business |
| Fendops Limited | UK | Business |
| Fendops Kft | Hungary | Business |

---

## API Token Setup

### Prerequisites

- 2-step verification enabled on each business profile
- Business account must be verified
- Must use website (not mobile app)

### Step 1: Navigate to API Tokens

For **each** business profile:

1. Go to [wise.com](https://wise.com) and log in
2. Select the business profile from the profile switcher
3. Go to **Settings** → **Integrations and tools**
4. Click **API tokens**
5. Click **Add new token**

### Step 2: Configure Token Permissions

Select read-only permissions for reconciliation:

- ✅ Read your profile info
- ✅ Read your balances
- ✅ Read your account statements
- ✅ Read your transactions
- ❌ Create and manage transfers (not needed)
- ❌ Create and manage recipients (not needed)

Name the token something identifiable (e.g., `marty-reconciliation`).

### Step 3: Generate SCA Key Pair

Wise uses Strong Customer Authentication (SCA) for protected endpoints. Generate a key pair once and use it across all profiles.

```bash
# Generate private key (keep this secret)
openssl genrsa -out wise_private.pem 2048

# Extract public key (upload to Wise)
openssl rsa -in wise_private.pem -pubout -out wise_public.pem
```

### Step 4: Register Public Key

For **each** business profile:

1. Go to **Settings** → **Integrations and tools** → **API tokens**
2. Click **Manage public keys**
3. Click **Add new key**
4. Paste contents of `wise_public.pem`
5. Save

Use the same key pair across all profiles.

### Step 5: Get Profile IDs

After creating a token, fetch the profile ID:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "https://api.wise.com/v1/profiles"
```

Response includes `id` (the profile ID) and `type` (should be `BUSINESS`).

---

## Environment Configuration

Create `.env` file with tokens and profile IDs:

```env
# Wise API Configuration
WISE_PRIVATE_KEY_PATH=./secrets/wise_private.pem

# Phygrid Limited (UK)
WISE_TOKEN_PHYGRID_LTD=
WISE_PROFILE_PHYGRID_LTD=

# Phygrid S.A. (EU)
WISE_TOKEN_PHYGRID_SA=
WISE_PROFILE_PHYGRID_SA=

# Phygrid Inc (US)
WISE_TOKEN_PHYGRID_INC=
WISE_PROFILE_PHYGRID_INC=

# PHYGRID AB (PUBL) (Sweden)
WISE_TOKEN_PHYGRID_AB=
WISE_PROFILE_PHYGRID_AB=

# Ombori, Inc (US)
WISE_TOKEN_OMBORI_INC=
WISE_PROFILE_OMBORI_INC=

# Ombori AG (Switzerland)
WISE_TOKEN_OMBORI_AG=
WISE_PROFILE_OMBORI_AG=

# Fendops Limited (UK)
WISE_TOKEN_FENDOPS_LTD=
WISE_PROFILE_FENDOPS_LTD=

# Fendops Kft (Hungary)
WISE_TOKEN_FENDOPS_KFT=
WISE_PROFILE_FENDOPS_KFT=
```

---

## API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/profiles` | Get profile ID |
| `GET /v4/profiles/{profileId}/balances?types=STANDARD` | Get all currency balances |
| `GET /v1/profiles/{profileId}/balance-statements/{balanceId}/statement` | Get transactions for a balance |

---

## PSD2 Limitations (EU/UK entities)

Due to PSD2 regulations, personal API tokens cannot:
- Fund transfers
- Access full balance statements without SCA

The SCA key pair setup above addresses statement access.

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
- [Auth & Security Guide](https://docs.wise.com/guides/developer/auth-and-security)
