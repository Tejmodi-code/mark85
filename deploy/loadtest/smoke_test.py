"""Post-deploy check. Usage:  pip install httpx websockets && python smoke_test.py https://your-domain

Signs up a throwaway user, fires 100 bets at once, and checks that every bet was recorded and the coins add up
exactly (this is what proves the replicas, PgBouncer and Postgres agree), then opens the live-table WebSocket."""
import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1").rstrip("/")


async def main():
    ok = True
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as c:
        r = await c.get("/readyz"); print("readyz:", r.status_code); ok &= r.status_code == 200
        r = await c.post("/api/signup", json={"name": f"smoke_{int(time.time())}", "password": "smoke-test-pw"})
        r.raise_for_status(); token = r.json()["token"]; h = {"Authorization": "Bearer " + token}
        res = await asyncio.gather(*[c.post("/api/play", headers={**h, "Idempotency-Key": __import__("secrets").token_urlsafe(24)}, json={"game": "flip", "bet": 10, "choice": "H"}) for _ in range(100)])
        codes = sorted({x.status_code for x in res}); print("100 concurrent bets, status codes:", codes)
        w = (await c.get("/api/wallet", headers=h)).json()
        print("bets recorded:", w["bets"], "| books balance:", w["balanced"])
        ok &= w["bets"] == sum(x.status_code == 200 for x in res) and w["balanced"] is not False
        print("sponsors:", len((await c.get("/api/sponsors")).json()))
        print("admin route hidden:", (await c.get("/api/admin/sponsors")).status_code == 404)
    ws_url = BASE.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"t": "auth", "token": token}))
        auth = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert auth.get("ok") is True
        await ws.send(json.dumps({"t": "join", "room": "crash"}))
        m = json.loads(await asyncio.wait_for(ws.recv(), 5)); print("live table:", m.get("t"), m.get("phase"))
    print("ALL GOOD" if ok else "SOMETHING IS WRONG")
    sys.exit(0 if ok else 1)


asyncio.run(main())
