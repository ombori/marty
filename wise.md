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
