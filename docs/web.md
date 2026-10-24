# Web UI on the platform API (#16)

`web/` is a Vite + React + TypeScript single-page app for the hosted platform (`services/platform`, API under `/api/v1`). It has no UI kit, plain CSS with tokens (light and dark by `prefers-color-scheme`), and four runtime dependencies besides React: `react-router-dom`, `marked` and `dompurify` (the REPORT.md view). The browser never gets a shell or an agent: everything goes through the API.

## Views

| Route | What |
|---|---|
| `/login`, `/signup`, `/verify?token=` | Sign in; sign up (with `HWPLATFORM_DEV_AUTO_VERIFY=true` the account exists at once and the UI signs in, otherwise it asks for the e-mailed token); verify |
| header | Tenant switch (asks for the password: the API issues tenant-scoped tokens by `login` with `tenant_id`), sign out |
| `/projects`, `/projects/:id` | Project list and create; design versions (drag and drop or choose `design.yaml`, `requirements.yaml`, `*.md` notes, a `.tar.gz` bundle), start a run, runs with status and verdict (refreshed from the project SSE stream) |
| `/runs/:id` | Live stage progress and log over SSE, open questions with an answer form, cancel, then the rendered `REPORT.md` (sanitized) and the artifact list with downloads |
| `/settings` | Owner/admin only: Anthropic key for BYOK (store, status as `...last4`, delete; the key is cleared from the page after the request and never read back), members and invitations |
| `/accept-invite?token=` | Join a tenant from an invitation |

## Auth and the XSS trade-off

Login returns an access JWT (15 min) and an opaque refresh token in the JSON body; the API sets no cookie. The access token lives only in memory. The refresh token is kept in memory and in `sessionStorage` (per tab, gone when the tab closes), so a reload keeps the session. Refresh is single-flight: the token is single-use and a second use revokes the whole family on the server. A 401 triggers one refresh and one retry.

The cost: script running on the page (XSS) could read `sessionStorage`. The defences are the strict CSP set by nginx (`script-src 'self'`, no inline script, no `unsafe-eval`, `frame-ancestors 'none'`), React escaping, and DOMPurify on the only place that renders HTML (reports; raw HTML, images, forms and `javascript:` links are removed). A stolen refresh token is also detected when the real client refreshes next (reuse detection). An httpOnly cookie would need API changes (CSRF protection included); not done in M1.

## SSE

`EventSource` cannot send an `Authorization` header, so `src/api/sse.ts` reads the stream with `fetch` and a small spec-conforming parser. It sends `Last-Event-ID` on reconnect (the API replays from the outbox), reconnects with exponential backoff and jitter (0.5 s doubling to 15 s, 50-100 %) after a network error, a 5xx or a clean end of stream, refreshes the token once on a 401, gives up on 403 and 404, and stops at `run.completed|failed|cancelled`.

## Downloads

Artifact downloads use `fetch` (so the bearer token is sent) and save the blob. The API either streams the object (no `HWPLATFORM_S3_PUBLIC_URL`, the dev default) or answers 302 to a presigned URL. In the second case the browser follows the redirect with a plain cross-origin request, so the bucket needs a CORS rule allowing `GET` from the web origin, and the web container needs that origin in `connect-src`: set `WEB_CSP_CONNECT_EXTRA=https://s3.example.com` (compose) or the `CSP_CONNECT_EXTRA` env of the image.

## Typed API client

`src/api/schema.d.ts` is generated from `services/platform/openapi.json` (`npm run gen:api`, openapi-typescript) and committed. CI runs `npm run check:api`, which regenerates it and fails on a diff, so an API change cannot land without updating the types. `src/api/client.ts` is the thin fetch wrapper (bearer token, refresh, `ApiError`), `src/api/api.ts` the typed endpoint functions.

## Develop

```
cd web
npm ci
npm run dev            # http://localhost:5173, /api proxied to 127.0.0.1:8080 (hwplatform-api, see services/platform/README.md)
npm run typecheck && npm test && npm run build
```

## Serve: the compose stack

`deploy/compose/platform.yaml` has a `web` profile with `api` (image from `deploy/compose/api.Dockerfile`: `python:3.12-slim` and `uv sync --frozen --package hwplatform`; it runs `hwplatform-migrate` and then `hwplatform-api`), a one-shot that registers the Temporal search attributes the API sets (`HwTenantId`, `HwProjectId`, `HwRunId`, `HwRunKind`), and `web` (`web/Dockerfile`: node build, then `nginxinc/nginx-unprivileged:1.29.3-alpine-slim`, about 20 MB). nginx serves the SPA, proxies `/api/` to the API with buffering off for SSE, and sets CSP, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `nosniff` and a restrictive `Permissions-Policy`.

```
cp deploy/compose/.env.example deploy/compose/.env
# in .env: HWPLATFORM_JWT_SECRET=$(openssl rand -hex 32)
#          HWPLATFORM_MASTER_KEY=$(openssl rand -base64 32)
#          HWPLATFORM_DEV_AUTO_VERIFY=true     # local trial only, skips e-mail proof
COMPOSE_PROFILES=web scripts/platform.sh up    # then http://127.0.0.1:8088 (WEB_PORT)
```

No worker runs in this stack (workflows are #9/#15), so a started run stays `queued`; cancel returns 202 and the status changes only when a worker handles it. Several stacks can coexist with `COMPOSE_PROJECT_NAME` and different `*_PORT`/`WEB_PORT`.

## Tests

- Unit (vitest, in CI): the SSE parser (chunk boundaries, CRLF, comments), reconnect with `Last-Event-ID` and backoff, 401 refresh, single-flight token refresh, error mapping, upload part mapping, Markdown sanitizing, event lines.
- e2e (Playwright, local only; a browser download and the full stack are too heavy for CI): `cd web && npx playwright install chromium && E2E_BASE_URL=http://127.0.0.1:8088 npm run e2e`. It signs up, creates a project, uploads a design version, starts a run and sees it queued, cancels it, checks settings and a reload, and drives the question panel against mocked API answers (no workflow produces questions yet). `E2E_SHOTS=../docs/screenshots/web` also writes screenshots.

## The old local UI

`ui/server.py` and `ui/static/` (the local UI with no login that drives `claude -p`) are deprecated and will be removed once the platform runs the agent (#15). Its useful views live on here: report rendering, questions, artifacts. The shell and agent access of `ui/server.py` is deliberately not ported.
