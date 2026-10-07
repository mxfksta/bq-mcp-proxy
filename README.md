# bq-mcp-proxy

Minimal authenticating reverse proxy for Google's managed BigQuery MCP server
(`https://bigquery.googleapis.com/mcp`), intended for Cloud Run.

Clients (e.g. Langdock) authenticate with a static API key; the proxy runs as a
service account and forwards requests with short-lived, auto-refreshed Google
access tokens. No JSON key file is needed.

## Environment variables

| Variable          | Description                                                                 |
| ----------------- | --------------------------------------------------------------------------- |
| `PROXY_API_KEYS`  | Required. Accepted keys, comma-separated (allows zero-downtime rotation).   |
| `UPSTREAM_URL`    | Optional. Default `https://bigquery.googleapis.com/mcp`.                    |
| `BILLING_PROJECT` | Optional. Sent as `x-goog-user-project`.                                    |
| `PORT`            | Provided by Cloud Run. Default `8080`.                                      |

## Deploy

```bash
gcloud run deploy bq-mcp-proxy \
  --source . \
  --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --set-secrets "PROXY_API_KEYS=${SECRET}:latest" \
  --allow-unauthenticated \
  --max-instances 3 --timeout 300 --memory 256Mi
```

Endpoints: `/mcp` (proxied, requires `Authorization: Bearer <key>` or `X-API-Key`), `/health`.
