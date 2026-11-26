# Production deployment

## Requirements

- A Linux host with Docker Engine and the Docker Compose plugin.
- A DNS name you control, pointed to the host.
- TCP ports 80 and 443 open. UDP 443 is optional for HTTP/3.
- A backup destination outside this host.

Caddy is the only published web-facing container. It obtains and renews certificates for a public DNS name and redirects HTTP to HTTPS. The origin stack keeps Nginx, FastAPI, the Rust engine, PostgreSQL and PgBouncer on the private Compose network. Caddy's automatic HTTPS behavior is documented at [caddyserver.com/docs/automatic-https](https://caddyserver.com/docs/automatic-https).

## First deployment

From the unpacked `tej` directory:

```sh
./deploy.sh
```

The first run writes `.env` with random PostgreSQL and admin secrets, then exits. Edit `SITE_HOST` to the DNS name that points to this host. Do not commit `.env`. Re-run `./deploy.sh` after DNS resolves and ports 80/443 reach the server. The script builds the images, runs the additive database migration once, starts four API replicas and two stateless game engines by default, keeps one live-table actor, and checks the HTTPS `/readyz` endpoint.

The sample `SITE_HOST=example.com` is deliberately not accepted for deployment. If you need a local developer stack, set `SITE_HOST=localhost` and run `docker compose up`; Caddy will use a local certificate that browsers may not trust until its local CA is installed.

## What the stack exposes

```text
Internet -> Caddy HTTPS edge -> private Nginx
                               -> API replicas -> PgBouncer -> PostgreSQL
                               -> one live-table actor
API replicas and live actor -> stateless Rust engine replicas
```

Only Caddy publishes host ports. Nginx logs method, path without query parameters, response code, request duration, selected upstream and upstream duration. The Nginx API upstream uses Docker DNS refresh; its `resolve` upstream parameter is available in open-source Nginx from 1.27.3 and later (see [nginx.org upstream documentation](https://nginx.org/en/docs/http/ngx_http_upstream_module.html)).

## Configuration

- `SITE_HOST`: the public DNS name. Set before public deployment.
- `POSTGRES_PASSWORD`: generated at first deploy; retain it in the secret manager.
- `ADMIN_KEY`: generated at first deploy; keep it private.
- `API_REPLICAS`: stateless API replica count.
- `ENGINE_REPLICAS`: stateless Rust engine count.
- `WEB_CONCURRENCY`: Gunicorn workers per API container; live remains one worker.
- `API_ADMISSION_MAX`, `API_ADMISSION_QUEUE`, `API_ADMISSION_WAIT_MS`, `API_ADMISSION_TARGET_MS`: per-worker request concurrency, bounded queue depth, wait deadline and latency target. Defaults are 10, 20, 150 ms and 350 ms.
- `LIVE_ACTION_MAX_INFLIGHT`, `LIVE_ACTION_QUEUE`, `LIVE_ACTION_WAIT_MS`, `LIVE_ACTION_TARGET_MS`: equivalent live-table action controls. Defaults are 16, 32, 150 ms and 300 ms. Tune these against PostgreSQL/PgBouncer limits and measured load; they are not global hard caps across all replicas.

Do not scale `live` above one. The service lease rejects a second live-table process. The actor numbers rounds from persisted wagers and refunds open stakes at startup before opening a new round. A restarted round does not resume cards or a rocket trajectory; it cancels the interrupted round and refunds unresolved wagers.

## Verify

After deployment:

```sh
docker compose ps
docker compose logs --tail=100 caddy nginx api live
python deploy/loadtest/smoke_test.py https://YOUR_DOMAIN
```

The smoke test creates a throwaway account, performs 100 concurrent distinct bets with idempotency keys, checks the wallet totals, and joins the live WebSocket. Delete the throwaway account and related data using your operational retention policy after the check. Run load tests only on a staging database and staging domain.

## Backups and recovery

Take a daily PostgreSQL backup and test restoring it on a separate host:

```sh
docker compose exec -T postgres pg_dump -U tej tej > backup.sql
```

Back up `.env` securely. Keep the Caddy data volume so certificates and ACME state survive host replacement. Restore both database and configuration before directing production traffic. Never store backups beside the live database volume only.

## Deployment limits

This stack does not provide database replication/failover, multi-region routing, Redis-backed table sharding, moderation operations, or a high-availability live table. It includes one PostgreSQL primary and one live-table actor. Capacity and recovery objectives must be load-tested and reviewed against the actual hosting environment before accepting real users. The app is for play coins only.
