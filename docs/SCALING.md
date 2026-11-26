# Scaling and verification notes

## Architecture

Nginx refreshes API and live service addresses using the Docker embedded DNS resolver. Stateless API containers can scale horizontally and share PostgreSQL through PgBouncer. The Rust engine is stateless and may run as multiple containers. The live Blackjack and Crash tables use one leased actor to serialize requests and own each round. A second actor cannot take the lease while the first lease is valid.

The live actor writes each accepted stake to `live_wagers` in the same transaction as the wallet debit. Settlement changes the wager state and wallet in one transaction. On process restart, any remaining open wager is refunded and marked as refunded; round numbering continues above the last stored round. This preserves the coin balance, but it does not resume an interrupted hand or rocket trajectory.

## Just-in-time admission

Every HTTP API worker uses a bounded adaptive gate. Idle traffic takes the fast path immediately. During bursts the gate admits up to its current in-flight limit, holds a bounded queue for up to 150 ms, then returns HTTP 503 with `Retry-After` when full. It lowers its limit when completed request latency exceeds its target or server errors rise, and slowly raises the limit after a sustained healthy window. The live-table actor applies the same policy to seat, card, wager and cash-out actions and returns an explicit accept/reject decision with server decision time.

This is an explainable adaptive capacity controller, not an external AI model. It observes local queue depth and completion latency; it never selects cards, Mines tiles, winners or payouts. The normal transactional game rules decide whether a request is valid, affordable and still within the round. Because each API worker has its own gate, PostgreSQL/PgBouncer and the load balancer remain the shared cross-replica pressure points. Tune the environment values against production-like benchmarks; defaults are starting values, not capacity guarantees.

The source contains Blackjack, Crash, Mines, Hi-Lo and solo games. It has no Bingo implementation yet. The API gate will protect a future Bingo endpoint, but that endpoint needs its own game rules, durable card/round state, replay protection and fairness tests before it can accept play.

## Current measurements

No requests-per-second or concurrent-user figure is certified by the repository. The previous ZIP's scaling table had no run logs, percentile latency or repeatable hardware manifest, so those numbers have been removed. Local verification against SQLite cannot predict PostgreSQL/PgBouncer behavior.

## Load-test procedure

Use a disposable staging database, staging domain and fixed server shape. Record CPU, RAM, kernel, Docker version, image digests, PostgreSQL settings, API/engine replica counts and the generator's location. Run each workload separately, then together:

1. Static HTML/CSS/JS and sponsor reads.
2. Signup/login with realistic unique users.
3. Authenticated reads and wallet history.
4. Ordinary play and Plinko including retries with the same idempotency key.
5. 100+ synchronized bets from one user and from many users.
6. Blackjack seat contention at the betting-window boundary, table fanout and chat rate limiting.
7. WebSocket connection growth, reconnect and session revocation.
8. API replica restart, engine failure, database connection interruption and live actor restart during open wagers.

Record p50, p95, p99 and maximum latency, error status counts, database wait/lock time, pool saturation, WebSocket delivery lag, accepted/rejected seat counts, stake recovery count and balance/ledger reconciliation. Increase traffic gradually and stop if errors, queueing or database saturation grow without recovery.

The included `deploy/loadtest/smoke_test.py` is a correctness smoke test, not a benchmark. `wrk-play.lua` and `wrk-signup.lua` are load generators; use them only against a disposable staging environment.
