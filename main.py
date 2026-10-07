"""
Minimal authenticating reverse proxy for the Google BigQuery remote MCP server.

Langdock  --(static API key)-->  this proxy  --(short-lived service-account token)-->  BigQuery MCP

Environment variables:
  PROXY_API_KEYS   Required. One or more accepted keys, comma-separated (allows zero-downtime rotation).
  UPSTREAM_URL     Optional. Default: https://bigquery.googleapis.com/mcp
  BILLING_PROJECT  Optional. If set, sent as x-goog-user-project (quota/billing project).
  PORT             Provided by Cloud Run. Default 8080.
"""
import asyncio
import hmac
import logging
import os

import aiohttp
import google.auth
from aiohttp import web
from google.auth.transport.requests import Request as GoogleRequest

UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "https://bigquery.googleapis.com/mcp")
BILLING_PROJECT = os.environ.get("BILLING_PROJECT", "").strip()
API_KEYS = [k.strip() for k in os.environ.get("PROXY_API_KEYS", "").split(",") if k.strip()]
SCOPES = ["https://www.googleapis.com/auth/bigquery"]

# Headers we pass through (everything else, esp. Authorization, is replaced or dropped).
REQ_HEADERS = {"content-type", "accept", "mcp-protocol-version", "mcp-session-id",
               "mcp-method", "last-event-id"}
RESP_HEADERS = {"content-type", "mcp-session-id", "cache-control"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bq-mcp-proxy")

if not API_KEYS:
    raise SystemExit("PROXY_API_KEYS is not set")

# On Cloud Run this resolves to the attached service account via the metadata server (no key file).
_creds, _ = google.auth.default(scopes=SCOPES)
_token_lock = asyncio.Lock()


async def get_token() -> str:
    async with _token_lock:
        if not _creds.valid:  # google-auth refreshes a few minutes before expiry
            await asyncio.get_running_loop().run_in_executor(None, _creds.refresh, GoogleRequest())
        return _creds.token


def is_authorized(request: web.Request) -> bool:
    supplied = request.headers.get("X-API-Key", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip() or supplied
    return any(hmac.compare_digest(supplied.encode(), k.encode()) for k in API_KEYS)


async def proxy(request: web.Request) -> web.StreamResponse:
    if not is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401,
                                 headers={"WWW-Authenticate": "Bearer"})

    headers = {k: v for k, v in request.headers.items() if k.lower() in REQ_HEADERS}
    headers["Authorization"] = f"Bearer {await get_token()}"
    if BILLING_PROJECT:
        headers["x-goog-user-project"] = BILLING_PROJECT

    body = await request.read()
    session: aiohttp.ClientSession = request.app["http"]
    try:
        async with session.request(request.method, UPSTREAM_URL, headers=headers,
                                   data=body or None) as upstream:
            resp = web.StreamResponse(
                status=upstream.status,
                headers={k: v for k, v in upstream.headers.items() if k.lower() in RESP_HEADERS},
            )
            await resp.prepare(request)
            async for chunk in upstream.content.iter_any():  # streams SSE as it arrives
                await resp.write(chunk)
            await resp.write_eof()
            if upstream.status >= 400:
                log.warning("upstream %s for %s", upstream.status, request.method)
            return resp
    except aiohttp.ClientError as exc:
        log.error("upstream error: %s", exc)
        return web.json_response({"error": "bad gateway"}, status=502)


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def on_startup(app: web.Application) -> None:
    app["http"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=300))


async def on_cleanup(app: web.Application) -> None:
    await app["http"].close()


def make_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/healthz", health)
    app.router.add_route("*", "/mcp", proxy)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
