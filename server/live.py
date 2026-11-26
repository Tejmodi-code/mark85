"""Live multiplayer tables over WebSocket: one shared Crash rocket and a 5-seat Blackjack table.
Every round gets a fresh server seed. Its hash is shown before bets close and the seed itself is revealed
after the round. Randomness comes from the Rust engine (/floats). Play coins only."""
import asyncio
import json
from collections import deque
import math
import os
import secrets
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import sqlalchemy as sa
from fastapi import WebSocket, WebSocketDisconnect

import main
from admission import AdaptiveAdmission
from main import ENGINE_URL, SESSION_DAYS, app, bets, db, sessions, sha, token_hash, users

conns = {}  # WebSocket -> {"uid", "name", "rooms"}
chat_by_uid = {}
LIVE_ACTION_ADMISSION = AdaptiveAdmission(
    int(os.getenv("LIVE_ACTION_MAX_INFLIGHT", "16")),
    queue_limit=int(os.getenv("LIVE_ACTION_QUEUE", "32")),
    wait_seconds=float(os.getenv("LIVE_ACTION_WAIT_MS", "150")) / 1000,
    target_latency_ms=float(os.getenv("LIVE_ACTION_TARGET_MS", "300")))


# ---------- wallet helpers (sync, run in threads) ----------
def _coins(c, uid):
    return c.execute(sa.select(users.c.coins).where(users.c.id == uid)).scalar()


def debit(uid, amt, game=None, round_id=None):
    with db.begin() as c:
        ok = c.execute(sa.update(users).where(sa.and_(users.c.id == uid, users.c.coins >= amt))
                       .values(coins=users.c.coins - amt)).rowcount == 1
        if not ok:
            return None
        if game and round_id is not None:
            row = c.execute(sa.select(main.live_wagers.c.amount, main.live_wagers.c.state).where(
                main.live_wagers.c.game == game, main.live_wagers.c.round_id == round_id,
                main.live_wagers.c.user_id == uid).with_for_update()).first()
            if row and row[1] == "open":
                c.execute(sa.update(main.live_wagers).where(
                    main.live_wagers.c.game == game, main.live_wagers.c.round_id == round_id,
                    main.live_wagers.c.user_id == uid, main.live_wagers.c.state == "open")
                    .values(amount=main.live_wagers.c.amount + amt, updated=datetime.utcnow()))
            else:
                c.execute(main.live_wagers.insert().values(game=game, round_id=round_id, user_id=uid,
                    amount=amt, state="open", payout=0))
        return _coins(c, uid)


def credit(uid, amt, game=None, round_id=None):
    with db.begin() as c:
        c.execute(sa.update(users).where(users.c.id == uid).values(coins=users.c.coins + amt))
        if game and round_id is not None:
            c.execute(sa.update(main.live_wagers).where(
                main.live_wagers.c.game == game, main.live_wagers.c.round_id == round_id,
                main.live_wagers.c.user_id == uid, main.live_wagers.c.state == "open")
                .values(amount=sa.case((main.live_wagers.c.amount <= amt, 0),
                                      else_=main.live_wagers.c.amount - amt),
                        state=sa.case((main.live_wagers.c.amount <= amt, "refunded"), else_="open"),
                        updated=datetime.utcnow()))
        return _coins(c, uid)


def settle_db(uid, game, bet, payout, roll, mult, nonce, seed):
    """Settle the wager and wallet in one transaction; reject duplicate settlements."""
    with db.begin() as c:
        if payout:
            c.execute(sa.update(users).where(users.c.id == uid).values(coins=users.c.coins + payout))
        if game in ("crash", "blackjack"):
            changed = c.execute(sa.update(main.live_wagers).where(
                main.live_wagers.c.game == game, main.live_wagers.c.round_id == nonce,
                main.live_wagers.c.user_id == uid, main.live_wagers.c.state == "open",
                main.live_wagers.c.amount == bet)
                .values(state="settled", payout=payout, updated=datetime.utcnow())).rowcount
            if changed != 1:
                raise RuntimeError("Live wager is missing, mismatched, or already settled")
        c.execute(bets.insert().values(user_id=uid, game=game, bet=bet, payout=payout, roll=roll,
                                       multiplier=mult, nonce=nonce, seed_hash=sha(seed), client_seed="live"))
        main.record_bet_stats(c, uid, game, bet, payout)
        return _coins(c, uid)


def recover_live_wagers():
    """Refund open stakes on a live-process restart, preventing stranded balances."""
    with db.begin() as c:
        rows = c.execute(sa.select(main.live_wagers.c.game, main.live_wagers.c.round_id,
                                   main.live_wagers.c.user_id, main.live_wagers.c.amount)
                         .where(main.live_wagers.c.state == "open").with_for_update()).all()
        for game, round_id, uid, amount in rows:
            c.execute(sa.update(users).where(users.c.id == uid).values(coins=users.c.coins + amount))
            c.execute(sa.update(main.live_wagers).where(
                main.live_wagers.c.game == game, main.live_wagers.c.round_id == round_id,
                main.live_wagers.c.user_id == uid, main.live_wagers.c.state == "open")
                .values(amount=0, state="refunded", updated=datetime.utcnow()))


def lookup(token):
    if not token or len(token) > 128:
        return (None, None, None)
    oldest = datetime.utcnow() - timedelta(days=SESSION_DAYS)
    with db.connect() as c:
        r = c.execute(sa.select(users.c.id, users.c.name, users.c.avatar)
                      .select_from(sessions.join(users, users.c.id == sessions.c.user_id))
                      .where(sessions.c.token == token_hash(token), sessions.c.created > oldest)).first()
    return (r[0], r[1], r[2]) if r else (None, None, None)


def session_valid_hash(value):
    if not value:
        return False
    oldest = datetime.utcnow() - timedelta(days=SESSION_DAYS)
    with db.connect() as c:
        return c.execute(sa.select(sessions.c.user_id).where(
            sessions.c.token == value, sessions.c.created > oldest)).first() is not None


# ---------- messaging ----------
async def send(ws, msg):
    try:
        await ws.send_text(json.dumps(msg))
    except Exception:
        conns.pop(ws, None)


async def bcast(room, msg):
    await asyncio.gather(*[send(w, msg) for w, c in list(conns.items()) if room in c["rooms"]])


async def tell(uid, msg):
    await asyncio.gather(*[send(w, msg) for w, c in list(conns.items()) if c["uid"] == uid])


async def floats(seed, nonce, n):
    async with httpx.AsyncClient(timeout=3) as h:
        r = await h.post(ENGINE_URL + "/floats", json={"server_seed": seed, "client_seed": "live", "nonce": nonce, "count": n})
    r.raise_for_status()
    return r.json()


# ---------- live crash ----------
cr = SimpleNamespace(phase="bet", m=1.0, left=0.0, round=0, seed="", point=0.0, rev=None, bets={}, hist=[])


def crash_pub():
    return {"t": "crash", "phase": cr.phase, "m": round(cr.m, 2), "left": round(cr.left, 1), "round": cr.round,
            "hash": sha(cr.seed) if cr.seed else "", "rev": cr.rev, "hist": cr.hist[-14:],
            "bets": [{"name": b["name"], "avatar": b["avatar"], "amt": b["amt"], "cash": b["cash"]} for b in cr.bets.values()]}


async def crash_cash(uid, m):
    b = cr.bets.get(uid)
    if not b or b["cash"] is not None or cr.phase != "run":
        return "No cashable Crash wager"
    m = math.floor(m * 100) / 100
    b["cash"] = m  # Reserve this action before yielding, preventing concurrent double cashout.
    pay = int(b["amt"] * m)
    try:
        bal = await asyncio.to_thread(settle_db, uid, "crash", b["amt"], pay, f"{m:.2f}x", m, cr.round, cr.seed)
    except Exception:
        b["cash"] = None
        raise
    b["open"] = False
    await tell(uid, {"t": "bal", "coins": bal})
    return None


async def crash_bet(conn, amt, auto):
    uid = conn["uid"]
    if cr.phase != "bet" or uid in cr.bets:
        return "Betting is closed for this round"
    if not isinstance(amt, int) or not 1 <= amt <= 1_000_000:
        return "Bet must be 1 to 1,000,000 coins"
    if auto is not None:
        if isinstance(auto, bool) or not isinstance(auto, (int, float)) or not math.isfinite(float(auto)) or not 1.01 <= float(auto) <= 1000:
            return "Auto cash-out must be between 1.01x and 1,000x"
        auto = float(auto)
    b = {"name": conn["name"], "avatar": conn["avatar"], "amt": amt, "cash": None, "auto": auto, "open": True}
    cr.bets[uid] = b
    bal = await asyncio.to_thread(debit, uid, amt, "crash", cr.round)
    if bal is None:
        cr.bets.pop(uid, None)
        return "Not enough coins"
    if cr.phase != "bet" or cr.bets.get(uid) is not b:  # round moved on while we were debiting
        b["open"] = False
        cr.bets.pop(uid, None)
        await tell(uid, {"t": "bal", "coins": await asyncio.to_thread(credit, uid, amt, "crash", cr.round)})
        return "Too late, the rocket has launched"
    await tell(uid, {"t": "bal", "coins": bal})


async def crash_loop():
    while True:
        try:
            cr.round += 1
            cr.seed, cr.bets, cr.m, cr.rev = secrets.token_hex(32), {}, 1.0, None
            f = (await floats(cr.seed, cr.round, 1))[0]
            cr.point = min(1000.0, max(1.0, math.floor(0.99 / (1 - f) * 100) / 100))
            cr.phase = "bet"
            for i in range(16):
                cr.left = 8 - i / 2
                await bcast("crash", crash_pub())
                await asyncio.sleep(.5)
            cr.phase, t0 = "run", time.time()
            while True:
                m = math.exp(0.1 * (time.time() - t0))
                if m >= cr.point:
                    cr.m = cr.point
                    break
                cr.m = m
                for uid, b in list(cr.bets.items()):
                    if b["cash"] is None and b["auto"] and cr.m >= b["auto"]:
                        await crash_cash(uid, b["auto"])
                await bcast("crash", crash_pub())
                await asyncio.sleep(.1)
            cr.phase, cr.rev = "crashed", cr.seed
            cr.hist.append(cr.point)
            for uid, b in list(cr.bets.items()):
                if b["cash"] is None:
                    await asyncio.to_thread(settle_db, uid, "crash", b["amt"], 0, f"{cr.point:.2f}x", 0, cr.round, cr.seed)
                    b["open"] = False
            await bcast("crash", crash_pub())
            await asyncio.sleep(4)
        except Exception as e:  # engine down, etc.
            print("crash loop error:", e)
            cr.phase = "offline"
            await bcast("crash", crash_pub())
            await asyncio.sleep(3)


# ---------- live blackjack ----------
bj = SimpleNamespace(seats=[None] * 5, phase="bet", dealer=[], hide=True, turn=-1, left=0.0, round=0, seed="",
                     deck=[], rev=None, ev=None)
card = lambda k: {"r": k % 13, "s": k // 13}


def hv(hand):
    t = a = 0
    for c in hand:
        if c is None:
            continue
        t += 11 if c["r"] == 0 else min(10, c["r"] + 1)
        a += c["r"] == 0
    while t > 21 and a:
        t, a = t - 10, a - 1
    return t


def bj_pub():
    d = bj.dealer
    shown = [d[0], None] if bj.hide and len(d) >= 2 else list(d)
    seats = [None if not s else {"name": s["name"], "avatar": s["avatar"], "bet": s["bet"], "hand": s["hand"], "st": s["st"],
                                 "val": hv(s["hand"]), "res": s["res"], "pay": s["pay"]} for s in bj.seats]
    return {"t": "bj", "phase": bj.phase, "turn": bj.turn, "left": round(bj.left, 1), "round": bj.round,
            "hash": sha(bj.seed) if bj.seed else "", "rev": bj.rev, "seats": seats, "dealer": shown, "dval": hv(shown)}


def my_seat(uid):
    return next((i for i, s in enumerate(bj.seats) if s and s["uid"] == uid), -1)


async def bj_leave(uid):
    i = my_seat(uid)
    if i < 0:
        return "You do not have a Blackjack seat"
    s = bj.seats[i]
    if bj.phase == "bet" and s["bet"] and s["open"]:  # refund a bet placed before the deal
        amt, s["bet"], s["open"] = s["bet"], 0, False
        await tell(uid, {"t": "bal", "coins": await asyncio.to_thread(credit, uid, amt, "blackjack", bj.round)})
    if bj.phase == "bet" or not s["bet"]:
        bj.seats[i] = None
    else:  # mid-round: finish the hand, then remove the seat
        s["leave"] = True
        if s["st"] == "act":
            s["st"] = "stand"
            bj.ev.set()


async def bj_msg(conn, m):
    uid, t = conn["uid"], m.get("t")
    i = my_seat(uid)
    s = bj.seats[i] if i >= 0 else None
    if t == "sit":
        k = m.get("seat")
        if s or not isinstance(k, int) or not 0 <= k < 5 or bj.seats[k]:
            return "That seat is not available"
        bj.seats[k] = {"uid": uid, "name": conn["name"], "avatar": conn["avatar"], "bet": 0, "hand": [], "st": "wait", "res": "", "pay": 0, "open": False}
    elif t == "leave":
        await bj_leave(uid)
    elif t == "bbet":
        amt = m.get("amt")
        if not s or bj.phase != "bet" or s["bet"] or not isinstance(amt, int) or not 1 <= amt <= 1_000_000:
            return "Sit down and bet before the deal"
        s["bet"], s["open"] = amt, True
        bal = await asyncio.to_thread(debit, uid, amt, "blackjack", bj.round)
        if bal is None:
            s["bet"], s["open"] = 0, False
            return "Not enough coins"
        if bj.phase != "bet":
            s["bet"], s["open"] = 0, False
            await tell(uid, {"t": "bal", "coins": await asyncio.to_thread(credit, uid, amt)})
            return "Too late, the deal has started"
        await tell(uid, {"t": "bal", "coins": bal})
    elif t == "bact":
        a = m.get("a")
        if not s or bj.turn != i or s["st"] != "act":
            return "It is not your turn"
        if a not in ("double", "hit", "stand"):
            return "Unknown card action"
        if a == "double" and len(s["hand"]) != 2:
            return "Double is only allowed on your first two cards"
        if a == "double":
            bal = await asyncio.to_thread(debit, uid, s["bet"], "blackjack", bj.round)
            if bal is None:
                return "Not enough coins to double"
            if s["st"] != "act":  # timed out while debiting
                await tell(uid, {"t": "bal", "coins": await asyncio.to_thread(credit, uid, s["bet"], "blackjack", bj.round)})
                return None
            s["bet"] *= 2
            s["hand"].append(card(bj.deck.pop()))
            s["st"] = "bust" if hv(s["hand"]) > 21 else "stand"
            await tell(uid, {"t": "bal", "coins": bal})
        elif a == "hit":
            s["hand"].append(card(bj.deck.pop()))
            v = hv(s["hand"])
            s["st"] = "bust" if v > 21 else "stand" if v == 21 else "act"
        elif a == "stand":
            s["st"] = "stand"
        bj.ev.set()


async def bj_loop():
    bj.ev = asyncio.Event()
    while True:
        try:
            bj.round += 1
            bj.seed, bj.rev, bj.dealer, bj.hide, bj.turn, bj.deck = secrets.token_hex(32), None, [], True, -1, []
            for s in bj.seats:
                if s:
                    s.update(bet=0, hand=[], st="wait", res="", pay=0, open=False)
            bj.phase = "bet"
            for i in range(24):
                bj.left = 12 - i / 2
                await bcast("bj", bj_pub())
                await asyncio.sleep(.5)
            players = [i for i, s in enumerate(bj.seats) if s and s["bet"] > 0]
            if not players:
                continue
            fl = await floats(bj.seed, bj.round, 51)
            deck = list(range(52))
            for i in range(51, 0, -1):  # Fisher-Yates driven by the engine's floats
                j = int(fl[51 - i] * (i + 1))
                deck[i], deck[j] = deck[j], deck[i]
            bj.deck = deck
            bj.phase = "deal"
            for _ in range(2):
                for i in players + [-1]:
                    (bj.dealer if i < 0 else bj.seats[i]["hand"]).append(card(bj.deck.pop()))
                    await bcast("bj", bj_pub())
                    await asyncio.sleep(.35)
            for i in players:
                if hv(bj.seats[i]["hand"]) == 21:
                    bj.seats[i]["st"] = "bj"
            bj.phase = "play"
            for i in players:
                s = bj.seats[i]
                if s["st"] != "wait":
                    continue
                s["st"], bj.turn, deadline = "act", i, time.time() + 15
                while s["st"] == "act":
                    bj.ev.clear()
                    bj.left = max(0.0, deadline - time.time())
                    await bcast("bj", bj_pub())
                    try:
                        await asyncio.wait_for(bj.ev.wait(), 1)
                    except asyncio.TimeoutError:
                        pass
                    if s["st"] == "act" and time.time() >= deadline:
                        s["st"] = "stand"
            bj.turn, bj.phase, bj.hide, bj.left = -1, "dealer", False, 0
            await bcast("bj", bj_pub())
            await asyncio.sleep(.8)
            if any(bj.seats[i]["st"] == "stand" for i in players):
                while hv(bj.dealer) < 17:
                    bj.dealer.append(card(bj.deck.pop()))
                    await bcast("bj", bj_pub())
                    await asyncio.sleep(.8)
            dv = hv(bj.dealer)
            dbj = len(bj.dealer) == 2 and dv == 21
            for i in players:
                s = bj.seats[i]
                v = hv(s["hand"])
                if s["st"] == "bust":
                    mult, res = 0, "Bust"
                elif s["st"] == "bj":
                    mult, res = (1, "Push") if dbj else (2.5, "Blackjack!")
                elif dbj:
                    mult, res = 0, "Dealer blackjack"
                elif dv > 21 or v > dv:
                    mult, res = 2, "Win"
                elif v == dv:
                    mult, res = 1, "Push"
                else:
                    mult, res = 0, "Lose"
                pay = int(s["bet"] * mult)
                s["res"], s["pay"], s["st"] = res, pay, "done"
                bal = await asyncio.to_thread(settle_db, s["uid"], "blackjack", s["bet"], pay, f"{v} vs {dv}", mult, bj.round, bj.seed)
                s["open"] = False
                await tell(s["uid"], {"t": "bal", "coins": bal})
            bj.phase, bj.rev = "settle", bj.seed
            await bcast("bj", bj_pub())
            await asyncio.sleep(5)
            bj.seats = [None if (s and s.get("leave")) else s for s in bj.seats]
        except Exception as e:
            print("blackjack loop error:", e)
            await asyncio.sleep(3)


def in_play(uid):
    """Return durable open stakes so the wallet view survives actor/UI restarts."""
    with db.connect() as c:
        amount = c.execute(sa.select(sa.func.coalesce(sa.func.sum(main.live_wagers.c.amount), 0)).where(
            main.live_wagers.c.user_id == uid, main.live_wagers.c.state == "open")).scalar()
    return int(amount or 0)


# ---------- internal (not routed by the load balancer) ----------
@app.get("/internal/in_play/{uid}")
def internal_in_play(uid: int):
    if not _lease_owner:
        raise main.HTTPException(503, "Live tables are not accepting requests")
    return {"coins": in_play(uid)}


# ---------- socket endpoint ----------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    uid, name, avatar = None, None, "fox"
    conn = {"uid": None, "name": None, "avatar": avatar, "token_hash": None,
            "rooms": set()}
    conns[ws] = conn
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 4096:
                await ws.close(code=1009, reason="Message too large")
                break
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                await send(ws, {"t": "err", "msg": "Invalid message"})
                continue
            if not isinstance(m, dict):
                continue
            t, err = m.get("t"), None
            if t == "auth":
                supplied = m.get("token", "")
                if not isinstance(supplied, str):
                    supplied = ""
                found = await asyncio.to_thread(lookup, supplied)
                uid, name, avatar = found
                conn.update(uid=uid, name=name, avatar=avatar or "fox",
                            token_hash=token_hash(supplied) if uid else None)
                await send(ws, {"t": "auth", "ok": bool(uid), "name": name, "avatar": avatar})
                continue
            if uid and not await asyncio.to_thread(session_valid_hash, conn["token_hash"]):
                uid = conn["uid"] = None
                conn["token_hash"] = None
                await ws.close(code=4401, reason="Session expired or signed out")
                break
            if uid and t in ("cbet", "ccash", "sit", "leave", "bbet", "bact") and not _lease_owner:
                await ws.close(code=1013, reason="Live table owner unavailable")
                break
            if t == "join" and m.get("room") in ("crash", "bj"):
                conn["rooms"].add(m["room"])
                await send(ws, crash_pub() if m["room"] == "crash" else bj_pub())
            elif t == "leave_room":
                conn["rooms"].discard(m.get("room"))
                if m.get("room") == "bj" and uid:
                    await bj_leave(uid)
            elif not uid:
                err = "Sign in to play"
            elif t == "chat":
                room, text = m.get("room"), m.get("text")
                now = time.monotonic()
                times = chat_by_uid.setdefault(uid, deque())
                while times and now - times[0] > 10:
                    times.popleft()
                if room not in conn["rooms"] or room not in ("bj", "crash"):
                    err = "Join the table before chatting"
                elif not isinstance(text, str) or not text.strip() or len(text.strip()) > 240:
                    err = "Chat messages must be 1 to 240 characters"
                elif len(times) >= 5:
                    err = "Please wait before sending another message"
                else:
                    times.append(now)
                    if len(chat_by_uid) > 50000:
                        for user_id in list(chat_by_uid):
                            queue = chat_by_uid[user_id]
                            while queue and now - queue[0] > 10:
                                queue.popleft()
                            if not queue:
                                chat_by_uid.pop(user_id, None)
                    await bcast(room, {"t": "chat", "room": room, "name": name,
                                       "avatar": avatar, "text": text.strip()})
            elif t in ("cbet", "ccash", "sit", "leave", "bbet", "bact"):
                started = time.perf_counter()
                err, reason, status = None, "game_rule", 200
                admitted = await LIVE_ACTION_ADMISSION.acquire()
                if not admitted:
                    err, reason, status = "Table is busy; retry shortly", "capacity", 503
                else:
                    try:
                        if t == "cbet":
                            err = await crash_bet(conn, m.get("amt"), m.get("auto"))
                        elif t == "ccash":
                            err = await crash_cash(uid, cr.m)
                        else:
                            err = await bj_msg(conn, m)
                        if err:
                            status = 409
                    except Exception as exc:
                        print("live action error:", t, type(exc).__name__)
                        err, reason, status = "Action could not be completed; check your balance and retry", "server_error", 500
                    finally:
                        await LIVE_ACTION_ADMISSION.release((time.perf_counter() - started) * 1000, status)
                ok = not err
                await send(ws, {"t": "action", "action": t, "ok": ok,
                                "decision": "accept" if ok else "reject", "reason": None if ok else reason,
                                "round": cr.round if t in ("cbet", "ccash") else bj.round,
                                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                                "message": err or {"sit": "Seat reserved", "bbet": "Bet accepted",
                                    "cbet": "Bet accepted", "ccash": "Cash-out accepted"}.get(t, "Action accepted")})
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        conns.pop(ws, None)
        if uid and not any(c["uid"] == uid for c in conns.values()):
            await bj_leave(uid)


async def session_monitor():
    while True:
        await asyncio.sleep(2)
        hashes = {conn["token_hash"] for conn in conns.values() if conn.get("uid") and conn.get("token_hash")}
        valid = set()
        if hashes:
            oldest = datetime.utcnow() - timedelta(days=SESSION_DAYS)
            values = list(hashes)
            for offset in range(0, len(values), 1000):
                with db.connect() as c:
                    valid.update(c.execute(sa.select(sessions.c.token).where(
                        sessions.c.token.in_(values[offset:offset + 1000]), sessions.c.created > oldest)).scalars())
        for ws, conn in list(conns.items()):
            token = conn.get("token_hash")
            if conn.get("uid") and token and token not in valid:
                try:
                    await ws.close(code=4401, reason="Session expired or signed out")
                except Exception:
                    pass
                conns.pop(ws, None)


_lease_owner = None


def acquire_lease():
    global _lease_owner
    owner, now = secrets.token_hex(24), datetime.utcnow()
    try:
        with db.begin() as c:
            row = c.execute(sa.select(main.service_leases).where(
                main.service_leases.c.name == "live-tables").with_for_update()).mappings().first()
            if row and row["expires"] > now:
                raise RuntimeError("Another live-table process holds the database lease")
            if row:
                c.execute(sa.update(main.service_leases).where(main.service_leases.c.name == "live-tables")
                          .values(owner=owner, expires=now + timedelta(seconds=30)))
            else:
                c.execute(main.service_leases.insert().values(name="live-tables", owner=owner,
                          expires=now + timedelta(seconds=30)))
    except sa.exc.IntegrityError as e:
        raise RuntimeError("Another live-table process acquired the database lease") from e
    _lease_owner = owner
    return owner


def renew_lease(owner):
    with db.begin() as c:
        return c.execute(sa.update(main.service_leases).where(
            main.service_leases.c.name == "live-tables", main.service_leases.c.owner == owner)
            .values(expires=datetime.utcnow() + timedelta(seconds=30))).rowcount == 1


def release_lease():
    global _lease_owner
    if not _lease_owner:
        return
    with db.begin() as c:
        c.execute(sa.update(main.service_leases).where(
            main.service_leases.c.name == "live-tables", main.service_leases.c.owner == _lease_owner)
            .values(expires=datetime.utcnow()))
    _lease_owner = None


async def lease_heartbeat(table_tasks, owner):
    global _lease_owner
    while True:
        await asyncio.sleep(5)
        try:
            healthy = await asyncio.to_thread(renew_lease, owner)
        except Exception:
            healthy = False
        if not healthy:
            _lease_owner = None
            for task in table_tasks:
                task.cancel()
            await asyncio.gather(*(ws.close(code=1013, reason="Live table owner unavailable")
                                   for ws in list(conns)), return_exceptions=True)
            return


def start():
    owner = acquire_lease()
    try:
        recover_live_wagers()
    except Exception:
        release_lease()
        raise
    with db.connect() as c:
        cr.round = int(c.execute(sa.select(sa.func.coalesce(sa.func.max(main.live_wagers.c.round_id), 0))
                                 .where(main.live_wagers.c.game == "crash")).scalar() or 0)
        bj.round = int(c.execute(sa.select(sa.func.coalesce(sa.func.max(main.live_wagers.c.round_id), 0))
                                 .where(main.live_wagers.c.game == "blackjack")).scalar() or 0)
    tasks = [asyncio.create_task(crash_loop()), asyncio.create_task(bj_loop()),
             asyncio.create_task(session_monitor())]
    tasks.append(asyncio.create_task(lease_heartbeat(tasks[:2], owner)))
    return tasks

