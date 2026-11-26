# Tej Money

Play-coin gaming website with account registration, wallet history, provably fair games, live Blackjack, live Crash and room chat. Coins have no cash value; the product does not accept deposits or withdrawals.

## What is included

- `web/`: responsive browser UI, account screens, avatar selection, live tables and chat.
- `server/`: FastAPI API, account/session data, wallet ledger, solo games and live-table service.
- `engine/`: stateless Rust outcome engine.
- `deploy/`: Docker build files, Nginx routing, Caddy-managed HTTPS and smoke/load checks.
- `docs/DEPLOY.md`: Linux deployment instructions.
- `docs/SCALING.md`: architecture limits and a measured-load plan. No throughput claim is made without a run on the target server.

## Deploy on a Linux server

Use a host with Docker Compose and a public DNS name. Point the domain at the host and allow inbound TCP 80/443 (UDP 443 is optional for HTTP/3). Run:

```sh
./deploy.sh
```

On first run, the script creates `.env` with random database and admin secrets, then stops so you can set `SITE_HOST` to your real domain. Re-run it after DNS is ready. Caddy provisions and renews the public TLS certificate. See [docs/DEPLOY.md](docs/DEPLOY.md).

## Local development

Install Rust and Python 3.12+, then in separate terminals:

```sh
cd engine && cargo run --release
cd server && pip install -r requirements.txt && uvicorn main:app --port 8000
```

Open `http://localhost:8000`. SQLite is supported for local development. Production startup requires PostgreSQL and a strong `ADMIN_KEY`.

## Operations notes

- Ordinary API requests are stateless and share PostgreSQL through PgBouncer. Nginx resolves Compose service addresses dynamically as replicas change.
- Keep exactly one live-table actor. A database lease prevents a second actor from starting. Accepted live wagers are durable; unresolved stakes are refunded when that actor restarts.
- User session tokens are hashed at rest. WebSocket credentials are sent in the first encrypted WebSocket message, not in the URL or access logs.
- API workers and live-table actions use bounded adaptive just-in-time admission with brief queues and explicit defer/reject responses when saturated. It uses measured latency and queue depth, not an opaque model to decide game outcomes.
- Live chat is room scoped, text-only, length limited and rate limited per account.
- Fictional sponsor examples are not seeded in production. Set `DEMO_SPONSORS=1` only for local development.
- Before public launch, run the target-server checks in `deploy/loadtest/README.md`; the supplied source does not certify throughput.
