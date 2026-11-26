"""Tej Money API (play coins only). Auth, wallet, bet history, provably fair seeds.
Outcomes are resolved by the Rust engine. DB: SQLite by default, PostgreSQL via DATABASE_URL.

ROLE decides what a process runs:  all (default, local dev)  |  api (stateless, run many)  |  live (the one process
that owns the Crash and Blackjack tables).  Schema changes are applied by migrate.py, not at import time."""
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta

import httpx
import sqlalchemy as sa
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from admission import AdaptiveAdmission, AdaptiveAdmissionMiddleware

DB_URL = os.getenv("DATABASE_URL", "sqlite:///tejmoney.db")
ENGINE_URL = os.getenv("ENGINE_URL", "http://127.0.0.1:8081")
LIVE_URL = os.getenv("LIVE_URL", "http://127.0.0.1:8000")
ROLE = os.getenv("ROLE", "all")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
PROD = os.getenv("ENV") == "prod"
if PROD and DB_URL.startswith("sqlite"):
    raise RuntimeError("Production requires PostgreSQL; SQLite is for local development only")
if PROD and len(os.getenv("ADMIN_KEY", "")) < 32:
    raise RuntimeError("Production ADMIN_KEY must contain at least 32 characters")
BONUS = 10_000
GAMES = {"dice", "limbo", "flip", "roulette"}

if DB_URL.startswith("sqlite"):
    db = sa.create_engine(DB_URL, pool_pre_ping=True, connect_args={"check_same_thread": False})
else:
    db = sa.create_engine(DB_URL, pool_pre_ping=True, pool_recycle=1800,
                          pool_size=int(os.getenv("DB_POOL", "10")), max_overflow=int(os.getenv("DB_OVERFLOW", "10")))

# One keep-alive connection pool per upstream instead of a new TCP connection for every bet.
ENGINE = httpx.Client(base_url=ENGINE_URL, timeout=3, limits=httpx.Limits(max_connections=100, max_keepalive_connections=50))
LIVE = httpx.Client(base_url=LIVE_URL, timeout=0.6)
md = sa.MetaData()
users = sa.Table(
    "users", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(24), unique=True, nullable=False),
    sa.Column("pw", sa.String(200), nullable=False),
    sa.Column("email", sa.String(120)),
    sa.Column("avatar", sa.String(12), nullable=False, server_default="fox"),
    sa.Column("coins", sa.BigInteger, nullable=False),
    sa.Column("server_seed", sa.String(64), nullable=False),
    sa.Column("client_seed", sa.String(40), nullable=False),
    sa.Column("nonce", sa.Integer, nullable=False, default=0),
    sa.Column("created", sa.DateTime, server_default=sa.func.now()),
)
bets = sa.Table(
    "bets", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.ForeignKey("users.id"), index=True, nullable=False),
    sa.Column("game", sa.String(16), nullable=False),
    sa.Column("bet", sa.BigInteger, nullable=False),
    sa.Column("payout", sa.BigInteger, nullable=False),
    sa.Column("roll", sa.String(32), nullable=False),
    sa.Column("multiplier", sa.Float, nullable=False),
    sa.Column("nonce", sa.Integer, nullable=False),
    sa.Column("seed_hash", sa.String(64), nullable=False),
    sa.Column("client_seed", sa.String(40), nullable=False),
    sa.Column("created", sa.DateTime, server_default=sa.func.now()),
)
sa.Index("ix_bets_user_recent", bets.c.user_id, bets.c.id)
ledger = sa.Table(
    "ledger", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.ForeignKey("users.id"), index=True, nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("created", sa.DateTime, server_default=sa.func.now()),
)
sessions = sa.Table(
    "sessions", md,
    sa.Column("token", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.ForeignKey("users.id"), index=True, nullable=False),
    sa.Column("created", sa.DateTime, nullable=False, default=datetime.utcnow),
)
live_wagers = sa.Table(
    "live_wagers", md,
    sa.Column("game", sa.String(16), primary_key=True),
    sa.Column("round_id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("state", sa.String(12), nullable=False),
    sa.Column("payout", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("updated", sa.DateTime, nullable=False, server_default=sa.func.now()),
)
request_results = sa.Table(
    "request_results", md,
    sa.Column("user_id", sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("endpoint", sa.String(40), primary_key=True),
    sa.Column("key_hash", sa.String(64), primary_key=True),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("response", sa.Text, nullable=False),
    sa.Column("created", sa.DateTime, nullable=False, server_default=sa.func.now()),
)
user_game_stats = sa.Table(
    "user_game_stats", md,
    sa.Column("user_id", sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("game", sa.String(16), primary_key=True),
    sa.Column("bet_count", sa.BigInteger, nullable=False),
    sa.Column("wagered", sa.BigInteger, nullable=False),
    sa.Column("paid", sa.BigInteger, nullable=False),
    sa.Column("biggest_win", sa.BigInteger, nullable=False),
)
daily_stats = sa.Table(
    "daily_stats", md,
    sa.Column("user_id", sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("game", sa.String(16), primary_key=True),
    sa.Column("bet_count", sa.BigInteger, nullable=False),
    sa.Column("wagered", sa.BigInteger, nullable=False),
    sa.Column("paid", sa.BigInteger, nullable=False),
)
service_leases = sa.Table(
    "service_leases", md,
    sa.Column("name", sa.String(32), primary_key=True),
    sa.Column("owner", sa.String(64), nullable=False),
    sa.Column("expires", sa.DateTime, nullable=False),
)
sponsors = sa.Table(
    "sponsors", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(40), nullable=False),
    sa.Column("tagline", sa.String(80), nullable=False),
    sa.Column("cta", sa.String(20), nullable=False),
    sa.Column("url", sa.String(300), nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
    sa.Column("clicks", sa.Integer, nullable=False, default=0),
)
AVATARS = {"fox", "owl", "cat", "bear", "panda", "rabbit", "tiger", "koala"}
DEMO_SPONSORS = [  # fictional placeholders; replace them through the admin API
    ("Zephyr Run", "Lace up. Go further.", "Shop"),
    ("Masala Mile", "Street food, delivered hot.", "Order"),
    ("Orbit Mobile", "Unlimited data, zero fuss.", "Get plan"),
    ("Lumo Audio", "Hear every detail.", "Listen"),
    ("Saffron Escapes", "Weekend trips from Rs 4,999.", "Explore"),
    ("Pixel Forge", "Indie games, big ideas.", "Play"),
    ("Your brand here", "Reach Tej Money players.", "Advertise"),
]


def migrate():
    """Create tables and seed the demo sponsors. Run once per release (migrate.py), never from every replica."""
    md.create_all(db)
    # Additive migration for databases created by earlier ZIP versions.
    cols = {x["name"] for x in sa.inspect(db).get_columns("users")}
    if "avatar" not in cols:
        with db.begin() as c:
            c.execute(sa.text("ALTER TABLE users ADD COLUMN avatar VARCHAR(12) NOT NULL DEFAULT 'fox'"))
    # Build indexed wallet summaries once for existing bet history.
    with db.begin() as c:
        if c.execute(sa.select(sa.func.count()).select_from(user_game_stats)).scalar() == 0:
            grouped = c.execute(sa.select(bets.c.user_id, bets.c.game, sa.func.count(),
                sa.func.sum(bets.c.bet), sa.func.sum(bets.c.payout),
                sa.func.max(bets.c.payout - bets.c.bet)).group_by(bets.c.user_id, bets.c.game)).all()
            for uid, game, n, wagered, paid, best in grouped:
                c.execute(user_game_stats.insert().values(user_id=uid, game=game, bet_count=n,
                    wagered=int(wagered or 0), paid=int(paid or 0), biggest_win=max(int(best or 0), 0)))
        if c.execute(sa.select(sa.func.count()).select_from(daily_stats)).scalar() == 0:
            grouped = c.execute(sa.select(bets.c.user_id, sa.func.date(bets.c.created), bets.c.game,
                sa.func.count(), sa.func.sum(bets.c.bet), sa.func.sum(bets.c.payout))
                .group_by(bets.c.user_id, sa.func.date(bets.c.created), bets.c.game)).all()
            for uid, day, game, n, wagered, paid in grouped:
                day = day if hasattr(day, "year") else datetime.fromisoformat(str(day)).date()
                c.execute(daily_stats.insert().values(user_id=uid, day=day, game=game, bet_count=n,
                    wagered=int(wagered or 0), paid=int(paid or 0)))
    # Convert legacy raw bearer tokens to hashes so database reads cannot expose live credentials.
    with db.begin() as c:
        for row in c.execute(sa.select(sessions.c.token)).all():
            value = row[0]
            if len(value) != 64 or not re.fullmatch(r"[0-9a-f]{64}", value):
                c.execute(sa.update(sessions).where(sessions.c.token == value).values(token=sha(value)))
        if os.getenv("ENV") != "prod" and os.getenv("DEMO_SPONSORS", "0") == "1" and c.execute(sa.select(sa.func.count()).select_from(sponsors)).scalar() == 0:
            for name, tagline, cta in DEMO_SPONSORS:
                url = "mailto:ads@tejmoney.example" if name.startswith("Your") else "https://example.com/?ref=tejmoney"
                c.execute(sponsors.insert().values(name=name, tagline=tagline, cta=cta, url=url, active=True, clicks=0))


@contextlib.asynccontextmanager
async def lifespan(_app):
    tasks = []
    if ROLE in ("live", "all"):
        import live
        tasks = live.start()
    yield
    for t in tasks:
        t.cancel()
    if tasks:
        await __import__("asyncio").gather(*tasks, return_exceptions=True)
        await __import__("asyncio").to_thread(live.release_lease)


app = FastAPI(title="Tej Money", lifespan=lifespan,
              docs_url=None if PROD else "/docs", redoc_url=None, openapi_url=None if PROD else "/openapi.json")
_db_worker_capacity = int(os.getenv("DB_POOL", "10")) + int(os.getenv("DB_OVERFLOW", "10"))
API_ADMISSION_MAX = int(os.getenv("API_ADMISSION_MAX", str(min(32, max(8, _db_worker_capacity)))))
API_ADMISSION = AdaptiveAdmission(API_ADMISSION_MAX,
    queue_limit=int(os.getenv("API_ADMISSION_QUEUE", str(API_ADMISSION_MAX * 2))),
    wait_seconds=float(os.getenv("API_ADMISSION_WAIT_MS", "150")) / 1000,
    target_latency_ms=float(os.getenv("API_ADMISSION_TARGET_MS", "350")))
app.add_middleware(AdaptiveAdmissionMiddleware, controller=API_ADMISSION)
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, key = stored.split(":")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got.hex(), key)
    except (ValueError, TypeError):
        return False


_DUMMY_PW = hash_pw("constant-time-login-sentinel")


def token_hash(token: str) -> str:
    return sha(token)


def record_bet_stats(c, uid: int, game: str, bet: int, payout: int):
    """Update bounded wallet aggregates in the same transaction as the bet row."""
    c.execute(sa.select(users.c.id).where(users.c.id == uid).with_for_update()).first()
    row = c.execute(sa.select(user_game_stats).where(
        user_game_stats.c.user_id == uid, user_game_stats.c.game == game).with_for_update()).mappings().first()
    values = dict(bet_count=1, wagered=bet, paid=payout, biggest_win=max(payout - bet, 0))
    if row:
        c.execute(sa.update(user_game_stats).where(user_game_stats.c.user_id == uid,
            user_game_stats.c.game == game).values(
                bet_count=user_game_stats.c.bet_count + 1,
                wagered=user_game_stats.c.wagered + bet, paid=user_game_stats.c.paid + payout,
                biggest_win=sa.case((user_game_stats.c.biggest_win < max(payout - bet, 0), max(payout - bet, 0)), else_=user_game_stats.c.biggest_win)))
    else:
        c.execute(user_game_stats.insert().values(user_id=uid, game=game, **values))
    today = datetime.utcnow().date()
    daily = c.execute(sa.select(daily_stats).where(daily_stats.c.user_id == uid,
        daily_stats.c.day == today, daily_stats.c.game == game).with_for_update()).first()
    if daily:
        c.execute(sa.update(daily_stats).where(daily_stats.c.user_id == uid,
            daily_stats.c.day == today, daily_stats.c.game == game).values(
                bet_count=daily_stats.c.bet_count + 1,
                wagered=daily_stats.c.wagered + bet, paid=daily_stats.c.paid + payout))
    else:
        c.execute(daily_stats.insert().values(user_id=uid, day=today, game=game,
                                              bet_count=1, wagered=bet, paid=payout))


def auth(authorization: str = Header(default="")) -> int:
    token = authorization.removeprefix("Bearer ").strip()
    if not token or len(token) > 128:
        raise HTTPException(401, "Please sign in")
    oldest = datetime.utcnow() - timedelta(days=SESSION_DAYS)
    with db.connect() as c:
        row = c.execute(sa.select(sessions.c.user_id).where(
            sessions.c.token == token_hash(token), sessions.c.created > oldest)).first()
    if not row:
        raise HTTPException(401, "Please sign in")
    return row[0]


def view(u) -> dict:
    return {"name": u["name"], "email": u["email"], "avatar": u.get("avatar", "fox"), "coins": u["coins"], "seed_hash": sha(u["server_seed"]),
            "client_seed": u["client_seed"], "nonce": u["nonce"]}


def new_session(c, uid: int) -> str:
    token = secrets.token_urlsafe(32)
    c.execute(sessions.insert().values(token=token_hash(token), user_id=uid))
    return token


class Creds(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    password: str = Field(min_length=1, max_length=128)


class Signup(Creds):
    password: str = Field(min_length=10, max_length=128)
    email: str = ""
    avatar: str = "fox"


@app.post("/api/signup")
def signup(b: Signup):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", b.name):
        raise HTTPException(400, "Username: 3-24 letters, numbers or underscores")
    if not 10 <= len(b.password) <= 128:
        raise HTTPException(400, "Password must be 10 to 128 characters")
    if b.email and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}", b.email):
        raise HTTPException(400, "That email address does not look right")
    if b.avatar not in AVATARS:
        raise HTTPException(400, "Choose a supported avatar")
    try:
        with db.begin() as c:
            uid = c.execute(users.insert().values(
                name=b.name, email=b.email or None, pw=hash_pw(b.password), avatar=b.avatar, coins=BONUS, nonce=0,
                server_seed=secrets.token_hex(32), client_seed=secrets.token_hex(8))).inserted_primary_key[0]
            c.execute(ledger.insert().values(user_id=uid, kind="bonus", amount=BONUS))
            token = new_session(c, uid)
            u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
    except sa.exc.IntegrityError:
        raise HTTPException(409, "That name is taken")
    return {"token": token, "user": view(u)}


@app.post("/api/login")
def login(b: Creds):
    with db.begin() as c:
        u = c.execute(sa.select(users).where(users.c.name == b.name)).mappings().first()
        if not u:
            # Keep unknown-user and bad-password work similar to reduce username timing leaks.
            check_pw(b.password, _DUMMY_PW)
            raise HTTPException(401, "Wrong name or password")
        if not check_pw(b.password, u["pw"]):
            raise HTTPException(401, "Wrong name or password")
        return {"token": new_session(c, u["id"]), "user": view(u)}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Please sign in")
    with db.begin() as c:
        c.execute(sessions.delete().where(sessions.c.token == token_hash(token)))
    return {"ok": True}


@app.get("/api/me")
def me(uid: int = Depends(auth)):
    with db.connect() as c:
        return view(c.execute(sa.select(users).where(users.c.id == uid)).mappings().one())


class AvatarIn(BaseModel):
    avatar: str


@app.put("/api/avatar")
def change_avatar(b: AvatarIn, uid: int = Depends(auth)):
    if b.avatar not in AVATARS:
        raise HTTPException(400, "Choose a supported avatar")
    with db.begin() as c:
        c.execute(sa.update(users).where(users.c.id == uid).values(avatar=b.avatar))
        u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
    return view(u)


class Play(BaseModel):
    game: str
    bet: int = Field(ge=1, le=1_000_000)
    target: float = 50
    over: bool = False
    choice: str = ""


@app.post("/api/play")
def play(p: Play, uid: int = Depends(auth), idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    if p.game not in GAMES:
        raise HTTPException(400, "Unknown game")
    if not 16 <= len(idempotency_key) <= 128:
        raise HTTPException(400, "A 16 to 128 character Idempotency-Key is required")
    if p.bet < 1:
        raise HTTPException(400, "Bet must be at least 1 coin")
    key_hash = sha(idempotency_key)
    fingerprint = sha(json.dumps(p.model_dump(), sort_keys=True, separators=(",", ":")))
    try:
        with db.begin() as c:
            c.execute(request_results.insert().values(user_id=uid, endpoint="play", key_hash=key_hash,
                fingerprint=fingerprint, response=""))
            spent = c.execute(sa.update(users).where(users.c.id == uid, users.c.coins >= p.bet)
                              .values(coins=users.c.coins - p.bet, nonce=users.c.nonce + 1))
            if spent.rowcount == 0:
                raise HTTPException(400, "Not enough coins")
            u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
            nonce = u["nonce"] - 1
            try:
                r = ENGINE.post("/resolve", json={
                    "game": p.game, "server_seed": u["server_seed"], "client_seed": u["client_seed"],
                    "nonce": nonce, "target": p.target, "over": p.over, "choice": p.choice})
                r.raise_for_status()
            except httpx.HTTPError:
                raise HTTPException(503, "Game engine is unavailable")
            o = r.json()
            payout = int(p.bet * o["multiplier"]) if o["win"] else 0
            if payout:
                c.execute(sa.update(users).where(users.c.id == uid).values(coins=users.c.coins + payout))
            c.execute(bets.insert().values(
                user_id=uid, game=p.game, bet=p.bet, payout=payout, roll=o["roll"], multiplier=o["multiplier"],
                nonce=nonce, seed_hash=sha(u["server_seed"]), client_seed=u["client_seed"]))
            record_bet_stats(c, uid, p.game, p.bet, payout)
            result = {"roll": o["roll"], "win": o["win"], "payout": payout,
                      "coins": u["coins"] - p.bet + payout, "nonce": nonce}
            c.execute(sa.update(request_results).where(request_results.c.user_id == uid,
                request_results.c.endpoint == "play", request_results.c.key_hash == key_hash)
                .values(response=json.dumps(result, separators=(",", ":"))))
        return result
    except sa.exc.IntegrityError:
        with db.connect() as c:
            prior = c.execute(sa.select(request_results.c.fingerprint, request_results.c.response).where(
                request_results.c.user_id == uid, request_results.c.endpoint == "play",
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


@app.post("/api/topup")
def topup(uid: int = Depends(auth)):
    with db.begin() as c:
        ok = c.execute(sa.update(users).where(users.c.id == uid, users.c.coins < 1000)
                       .values(coins=users.c.coins + 1000))
        if ok.rowcount == 0:
            raise HTTPException(400, "Top-up works when you have under 1,000 coins")
        c.execute(ledger.insert().values(user_id=uid, kind="topup", amount=1000))
    return {"ok": True}


class Rotate(BaseModel):
    client_seed: str = ""


@app.post("/api/seed/rotate")
def rotate(b: Rotate, uid: int = Depends(auth)):
    new_client = re.sub(r"[^A-Za-z0-9]", "", b.client_seed)[:40] or secrets.token_hex(8)
    with db.begin() as c:
        u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
        c.execute(sa.update(users).where(users.c.id == uid).values(
            server_seed=secrets.token_hex(32), client_seed=new_client, nonce=0))
        fresh = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
    return {"revealed": {"server_seed": u["server_seed"], "client_seed": u["client_seed"],
                         "bets": u["nonce"]}, "user": view(fresh)}


def escrow(uid: int):
    """Coins sitting on a table or in an unfinished round. None when the live service cannot be reached."""
    import solo
    held = solo.stakes(uid)
    if ROLE == "api":
        try:
            return held + int(LIVE.get(f"/internal/in_play/{uid}").json()["coins"])
        except (httpx.HTTPError, ValueError, KeyError):
            return None
    import live
    return held + live.in_play(uid)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    try:
        with db.connect() as c:
            c.execute(sa.text("SELECT 1"))
        if ENGINE.get("/health").status_code != 200:
            raise RuntimeError
    except Exception:
        raise HTTPException(503, "not ready")
    return {"ok": True}


@app.get("/api/profile")
def profile(uid: int = Depends(auth)):
    with db.connect() as c:
        u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
        by_game = c.execute(sa.select(
            user_game_stats.c.game, user_game_stats.c.bet_count, user_game_stats.c.wagered, user_game_stats.c.paid)
            .where(user_game_stats.c.user_id == uid).order_by(user_game_stats.c.game)).all()
        hist = c.execute(sa.select(bets).where(bets.c.user_id == uid)
                         .order_by(bets.c.id.desc()).limit(50)).mappings().all()
        given = c.execute(sa.select(sa.func.coalesce(sa.func.sum(ledger.c.amount), 0))
                          .where(ledger.c.user_id == uid)).scalar()
    games = [{"game": g, "bets": n, "wagered": int(w), "paid": int(p)} for g, n, w, p in by_game]
    wagered, paid = sum(x["wagered"] for x in games), sum(x["paid"] for x in games)
    held = escrow(uid)
    return {
        "user": view(u), "by_game": games, "wagered": wagered, "paid": paid, "given": int(given),
        "balanced": None if held is None else int(given) + paid - wagered - held == u["coins"], "in_play": held or 0,
        "history": [{"time": h["created"].isoformat(), "game": h["game"], "bet": h["bet"],
                     "payout": h["payout"], "roll": h["roll"], "nonce": h["nonce"],
                     "seed_hash": h["seed_hash"]} for h in hist],
    }


@app.get("/api/wallet")
def wallet(uid: int = Depends(auth)):
    """Full money picture: what came in, where it went per game, daily result, ledger, coins on tables."""
    with db.connect() as c:
        u = c.execute(sa.select(users).where(users.c.id == uid)).mappings().one()
        by_game = c.execute(sa.select(user_game_stats.c.game, user_game_stats.c.bet_count,
            user_game_stats.c.wagered, user_game_stats.c.paid).where(user_game_stats.c.user_id == uid)).all()
        daily = c.execute(sa.select(daily_stats.c.day, sa.func.sum(daily_stats.c.bet_count),
            sa.func.sum(daily_stats.c.paid - daily_stats.c.wagered)).where(daily_stats.c.user_id == uid)
            .group_by(daily_stats.c.day).order_by(daily_stats.c.day.desc()).limit(14)).all()
        best = c.execute(sa.select(sa.func.max(user_game_stats.c.biggest_win))
                         .where(user_game_stats.c.user_id == uid)).scalar() or 0
        recv = c.execute(sa.select(ledger.c.kind, sa.func.sum(ledger.c.amount))
                         .where(ledger.c.user_id == uid).group_by(ledger.c.kind)).all()
        led = c.execute(sa.select(ledger).where(ledger.c.user_id == uid).order_by(ledger.c.id.desc()).limit(50)).mappings().all()
    games = [{"game": g, "bets": n, "wagered": int(w), "paid": int(p), "net": int(p) - int(w)} for g, n, w, p in by_game]
    games.sort(key=lambda x: -x["wagered"])
    wagered, paid = sum(x["wagered"] for x in games), sum(x["paid"] for x in games)
    received = {k: int(v) for k, v in recv}
    given = sum(received.values())
    held = escrow(uid)
    return {
        "balance": u["coins"], "received": received, "received_total": given, "wagered": wagered, "paid": paid,
        "net": paid - wagered, "in_play": held or 0, "bets": sum(x["bets"] for x in games), "biggest_win": max(int(best), 0),
        "balanced": None if held is None else given + paid - wagered - held == u["coins"], "by_game": games,
        "daily": [{"day": str(d), "bets": n, "net": int(v)} for d, n, v in reversed(daily)],
        "ledger": [{"time": r["created"].isoformat(), "kind": r["kind"], "amount": r["amount"]} for r in led],
    }


@app.get("/api/sponsors")
def list_sponsors():
    with db.connect() as c:
        rows = c.execute(sa.select(sponsors).where(sponsors.c.active == True).order_by(sponsors.c.id)).mappings().all()  # noqa: E712
    return [{k: r[k] for k in ("id", "name", "tagline", "cta", "url")} for r in rows]


@app.post("/api/sponsors/{sid}/click")
def sponsor_click(sid: int):
    with db.begin() as c:
        c.execute(sa.update(sponsors).where(sponsors.c.id == sid).values(clicks=sponsors.c.clicks + 1))
    return {"ok": True}


class SponsorIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    tagline: str = Field(max_length=80)
    cta: str = Field(default="Visit", max_length=20)
    url: str = Field(pattern=r"^(https?://|mailto:)\S{3,290}$")


def admin(x_admin_key: str = Header(default="")):
    key = os.getenv("ADMIN_KEY", "")  # admin API stays off until ADMIN_KEY is set
    if not key or not hmac.compare_digest(x_admin_key, key):
        raise HTTPException(403, "Admin key required")


@app.post("/api/admin/sponsors", dependencies=[Depends(admin)])
def add_sponsor(b: SponsorIn):
    with db.begin() as c:
        sid = c.execute(sponsors.insert().values(**b.model_dump(), active=True, clicks=0)).inserted_primary_key[0]
    return {"id": sid}


@app.get("/api/admin/sponsors", dependencies=[Depends(admin)])
def sponsor_stats():
    with db.connect() as c:
        return [dict(r) for r in c.execute(sa.select(sponsors).order_by(sponsors.c.id)).mappings().all()]


@app.delete("/api/admin/sponsors/{sid}", dependencies=[Depends(admin)])
def del_sponsor(sid: int):
    with db.begin() as c:
        c.execute(sa.update(sponsors).where(sponsors.c.id == sid).values(active=False))
    return {"ok": True}


if ROLE in ("api", "all"):
    import solo  # noqa: E402,F401  (registers /api/solo routes)
if ROLE in ("live", "all"):
    import live  # noqa: E402,F401  (registers /ws and the tables)
if os.getenv("AUTO_MIGRATE", "1") == "1":  # convenient locally; production runs migrate.py once per release
    import solo as _solo  # noqa: E402,F401  (its tables must exist before create_all)
    migrate()
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(WEB_DIR):  # in production nginx serves these files; the API containers do not ship them
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True))

