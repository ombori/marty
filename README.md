# Agent Template

Generic agent template for multi-cluster deployment.

## Quick Start

1. **Fork this repo** to your GitHub org (ombori, fendops, or gallyn-com)

2. **Edit `agent.yaml`** - set your agent name:
   ```yaml
   name: my-agent
   ```

3. **Push to main** - the workflow automatically:
   - Builds and pushes image to cluster registry
   - Creates DNS record (`my-agent.{domain}`)
   - Configures Cloudflare tunnel
   - Deploys to Kubernetes

## How It Works

The workflow detects which GitHub org it's running in and derives everything:

| Org | Domain | Runner Group | Cluster |
|-----|--------|--------------|---------|
| ombori | ombori.com | dso-ombori | ombori |
| fendops | fendops.com | dso-fendops | fendops |
| gallyn-com | gallyn.com | dso-gallyn | gallyn |

## Required Org Secrets

These secrets must be set at the **organization level**:

| Secret | Description |
|--------|-------------|
| `KUBECONFIG_<ORG>` | Base64-encoded kubeconfig (e.g., `KUBECONFIG_OMBORI`, `KUBECONFIG_GALLYN`) |
| `CLOUDFLARE_ZONE_ID_<ORG>` | Zone ID for the domain (e.g., `CLOUDFLARE_ZONE_ID_OMBORI`) |
| `CLOUDFLARE_API_TOKEN_DNS` | Cloudflare API token (shared) |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (shared) |
| `REGISTRY_USERNAME` | Registry username (usually `admin`) |
| `REGISTRY_PASSWORD` | Registry password |

**Org suffix mapping:**
- `ombori` → `_OMBORI`
- `fendops` → `_FENDOPS`
- `gallyn-com` → `_GALLYN`

## Project Structure

```
agent.yaml              # <- EDIT THIS: set agent name
app/
  main.py               # FastAPI application
  config.py             # Environment config (DB credentials from secrets)
  health.py             # Health check endpoints
terraform/
  main.tf               # Kubernetes resources (deployment, service, ingress)
  variables.tf          # Variables (agent_name, domain set by workflow)
Dockerfile              # Multi-stage Python build
```

## Available Services

The agent has access to these services in the `agents` namespace:

- **PostgreSQL**: `postgresql.agents.svc.cluster.local:5432`
- **Redis**: `redis-master.agents.svc.cluster.local:6379`
- **Qdrant**: `qdrant.agents.svc.cluster.local:6333`

Credentials are automatically injected as environment variables.

## Endpoints

- `GET /` - Root endpoint
- `GET /health` - Liveness probe
- `GET /health/ready` - Readiness probe (checks dependencies)
- `GET /health/full` - Full health check with details
