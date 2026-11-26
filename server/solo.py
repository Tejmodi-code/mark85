"""Solo games kept on the server: Mines, Hi-Lo and Plinko. Hidden state lives in the `rounds` table, so a
refresh or restart never loses a round. Randomness comes from the Rust engine (/floats)."""
import json

import httpx
import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field

from main import ENGINE, app, auth, bets, db, md, record_bet_stats, request_results, sha, users

rounds = sa.Table(
    "rounds", md,
    sa.Column("user_id", sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("game", sa.String(16), primary_key=True),
    sa.Column("bet", sa.BigInteger, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
)


class Bet(BaseModel):
    bet: int = Field(ge=1, le=1_000_000)


def stakes(uid):
    with db.connect() as c:
        return int(c.execute(sa.select(sa.func.coalesce(sa.func.sum(rounds.c.bet), 0)).where(rounds.c.user_id == uid)).scalar())


def begin(c, uid, bet, n):
    """Take the bet, advance the nonce, and fetch n floats from the engine, all inside one transaction."""
    spent = c.execute(sa.update(users).where(users.c.id == uid, users.c.coins >= bet)
                      .values(coins=users.c.coins - bet, nonce=users.c.nonce + 1))
    if spent.rowcount == 0:
        raise HTTPException(400, "Not enough coins")
    u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
    nonce = u["nonce"] - 1
    try:
        r = ENGINE.post("/floats", json={
            "server_seed": u["server_seed"], "client_seed": u["client_seed"], "nonce": nonce, "count": n})
        r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(503, "Game engine is unavailable")
    return u, nonce, r.json()


def active(c, uid, game):
    row = c.execute(sa.select(rounds.c.state).where(rounds.c.user_id == uid, rounds.c.game == game).with_for_update()).first()
    return json.loads(row[0]) if row else None


def save(c, uid, game, st):
    c.execute(rounds.delete().where(rounds.c.user_id == uid, rounds.c.game == game))
    c.execute(rounds.insert().values(user_id=uid, game=game, bet=st["bet"], state=json.dumps(st)))


def finish(c, uid, game, st, payout, roll, mult):
    if payout:
        c.execute(sa.update(users).where(users.c.id == uid).values(coins=users.c.coins + payout))
    c.execute(bets.insert().values(user_id=uid, game=game, bet=st["bet"], payout=payout, roll=roll, multiplier=mult,
                                   nonce=st["nonce"], seed_hash=st["hash"], client_seed=st["cs"]))
    record_bet_stats(c, uid, game, st["bet"], payout)
    c.execute(rounds.delete().where(rounds.c.user_id == uid, rounds.c.game == game))
    return c.execute(sa.select(users.c.coins).where(users.c.id == uid)).scalar()


def new_state(u, nonce, bet, **extra):
    return {"bet": bet, "nonce": nonce, "hash": sha(u["server_seed"]), "cs": u["client_seed"], **extra}


def balance(c, uid):
    return c.execute(sa.select(users.c.coins).where(users.c.id == uid)).scalar()


def lock_user(c, uid):
    c.execute(sa.select(users.c.id).where(users.c.id == uid).with_for_update()).first()


# ---------- Mines ----------
def mmult(k, n):
    m = 0.99
    for i in range(k):
        m *= (25 - i) / (25 - n - i)
    return m


class MStart(Bet):
    mines: int = Field(ge=1, le=24)


class Tile(BaseModel):
    tile: int = Field(ge=0, le=24)


@app.post("/api/mines/start")
def mines_start(b: MStart, uid: int = Depends(auth)):
    with db.begin() as c:
        lock_user(c, uid)
        if active(c, uid, "mines"):
            raise HTTPException(400, "Finish your current Mines round first")
        u, nonce, fl = begin(c, uid, b.bet, 24)
        tiles = list(range(25))
        for i in range(24, 0, -1):
            j = int(fl[24 - i] * (i + 1))
            tiles[i], tiles[j] = tiles[j], tiles[i]
        st = new_state(u, nonce, b.bet, n=b.mines, mines=tiles[:b.mines], open=[])
        save(c, uid, "mines", st)
        coins = balance(c, uid)
    return {"coins": coins, "n": b.mines, "next": round(mmult(1, b.mines), 2)}


@app.post("/api/mines/reveal")
def mines_reveal(b: Tile, uid: int = Depends(auth)):
    with db.begin() as c:
        st = active(c, uid, "mines")
        if not st:
            raise HTTPException(400, "No active Mines round")
        if b.tile in st["open"]:
            raise HTTPException(400, "Already opened")
        if b.tile in st["mines"]:
            coins = finish(c, uid, "mines", st, 0, f"bomb after {len(st['open'])} gems", 0)
            return {"bomb": True, "mines": st["mines"], "open": st["open"], "coins": coins}
        st["open"].append(b.tile)
        k, m = len(st["open"]), mmult(len(st["open"]), st["n"])
        if k == 25 - st["n"]:
            pay = int(st["bet"] * m)
            coins = finish(c, uid, "mines", st, pay, f"{k} gems", m)
            return {"done": True, "mines": st["mines"], "open": st["open"], "payout": pay, "coins": coins}
        save(c, uid, "mines", st)
    return {"open": st["open"], "mult": round(m, 2), "next": round(mmult(k + 1, st["n"]), 2)}


@app.post("/api/mines/cash")
def mines_cash(uid: int = Depends(auth)):
    with db.begin() as c:
        st = active(c, uid, "mines")
        if not st or not st["open"]:
            raise HTTPException(400, "Reveal at least one tile first")
        k = len(st["open"])
        m = mmult(k, st["n"])
        pay = int(st["bet"] * m)
        coins = finish(c, uid, "mines", st, pay, f"{k} gems", m)
    return {"payout": pay, "mult": round(m, 2), "mines": st["mines"], "coins": coins}


# ---------- Hi-Lo ----------
def hl_card(v):
    return {"r": v % 13, "s": v // 13}


def hilo_view(st):
    cur = st["seq"][st["i"]]
    r = cur % 13
    return {"card": hl_card(cur), "m": round(st["m"], 2), "bet": st["bet"], "steps": st["i"],
            "up": round(.99 / ((13 - r) / 13), 2), "dn": round(.99 / ((r + 1) / 13), 2)}


class Guess(BaseModel):
    up: bool


@app.post("/api/hilo/start")
def hilo_start(b: Bet, uid: int = Depends(auth)):
    with db.begin() as c:
        lock_user(c, uid)
        if active(c, uid, "hilo"):
            raise HTTPException(400, "Finish your current Hi-Lo round first")
        u, nonce, fl = begin(c, uid, b.bet, 40)
        st = new_state(u, nonce, b.bet, seq=[int(f * 52) for f in fl], i=0, m=1.0)
        save(c, uid, "hilo", st)
        coins = balance(c, uid)
    return {**hilo_view(st), "coins": coins}


@app.post("/api/hilo/guess")
def hilo_guess(b: Guess, uid: int = Depends(auth)):
    with db.begin() as c:
        st = active(c, uid, "hilo")
        if not st:
            raise HTTPException(400, "No active Hi-Lo round")
        cur, nxt_v = st["seq"][st["i"]] % 13, st["seq"][st["i"] + 1]
        nxt = nxt_v % 13
        p = (13 - cur) / 13 if b.up else (cur + 1) / 13
        if not (nxt >= cur if b.up else nxt <= cur):
            coins = finish(c, uid, "hilo", st, 0, f"lost after {st['i']} wins", 0)
            return {"lost": True, "card": hl_card(nxt_v), "coins": coins}
        st["m"] *= .99 / p
        st["i"] += 1
        if st["i"] >= len(st["seq"]) - 1:  # out of cards: cash out automatically
            pay = int(st["bet"] * st["m"])
            coins = finish(c, uid, "hilo", st, pay, f"{st['i']} wins", st["m"])
            return {"done": True, "card": hl_card(nxt_v), "payout": pay, "coins": coins}
        save(c, uid, "hilo", st)
    return hilo_view(st)


@app.post("/api/hilo/cash")
def hilo_cash(uid: int = Depends(auth)):
    with db.begin() as c:
        st = active(c, uid, "hilo")
        if not st or st["i"] < 1:
            raise HTTPException(400, "Win at least one guess first")
        pay = int(st["bet"] * st["m"])
        coins = finish(c, uid, "hilo", st, pay, f"{st['i']} wins", st["m"])
    return {"payout": pay, "mult": round(st["m"], 2), "coins": coins}


# ---------- Plinko ----------
PM = [5.6, 2.1, 1.1, 1, .5, 1, 1.1, 2.1, 5.6]


@app.post("/api/plinko")
def plinko(b: Bet, uid: int = Depends(auth), idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    if not 16 <= len(idempotency_key) <= 128:
        raise HTTPException(400, "A 16 to 128 character Idempotency-Key is required")
    key_hash = sha(idempotency_key)
    fingerprint = sha(json.dumps(b.model_dump(), sort_keys=True, separators=(",", ":")))
    try:
        with db.begin() as c:
            c.execute(request_results.insert().values(user_id=uid, endpoint="plinko", key_hash=key_hash,
                fingerprint=fingerprint, response=""))
            lock_user(c, uid)
            u, nonce, fl = begin(c, uid, b.bet, 8)
            path = [1 if f >= .5 else 0 for f in fl]
            k = sum(path)
            pay = int(b.bet * PM[k])
            coins = finish(c, uid, "plinko", new_state(u, nonce, b.bet), pay, f"slot {k + 1} ({PM[k]}x)", PM[k])
            result = {"path": path, "slot": k, "mult": PM[k], "payout": pay, "coins": coins}
            c.execute(sa.update(request_results).where(request_results.c.user_id == uid,
                request_results.c.endpoint == "plinko", request_results.c.key_hash == key_hash)
                .values(response=json.dumps(result, separators=(",", ":"))))
        return result
    except sa.exc.IntegrityError:
        with db.connect() as c:
            prior = c.execute(sa.select(request_results.c.fingerprint, request_results.c.response).where(
                request_results.c.user_id == uid, request_results.c.endpoint == "plinko",
                request_results.c.key_hash == key_hash)).first()
        if not prior:
            raise HTTPException(409, "Request could not be committed; retry with the same key")
        if prior[0] != fingerprint:
            raise HTTPException(409, "Idempotency-Key was already used for a different request")
        if not prior[1]:
            raise HTTPException(409, "The matching request is still processing; retry shortly")
        result = json.loads(prior[1])
        with db.connect() as c:
            result["coins"] = c.execute(sa.select(users.c.coins).where(users.c.id == uid)).scalar()
        return result


@app.get("/api/active")
def active_rounds(uid: int = Depends(auth)):
    with db.connect() as c:
        mi, hl = active(c, uid, "mines"), active(c, uid, "hilo")
    k = len(mi["open"]) if mi else 0
    return {"mines": mi and {"n": mi["n"], "open": mi["open"], "bet": mi["bet"],
                             "mult": round(mmult(k, mi["n"]), 2), "next": round(mmult(k + 1, mi["n"]), 2)},
            "hilo": hl and hilo_view(hl)}

