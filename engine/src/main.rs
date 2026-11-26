//! Tej Money game engine: stateless, provably fair outcome resolver.
//! outcome = HMAC-SHA256(server_seed, "client_seed:nonce"), first 4 bytes / 2^32.
use axum::{http::StatusCode, routing::{get, post}, Json, Router};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

#[derive(Deserialize)]
struct Req {
    game: String,
    server_seed: String,
    client_seed: String,
    nonce: u64,
    #[serde(default)]
    target: f64,
    #[serde(default)]
    over: bool,
    #[serde(default)]
    choice: String,
}

#[derive(Serialize)]
struct Res {
    roll: String,
    win: bool,
    multiplier: f64,
}

const RED: [u32; 18] = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36];

fn digest(server: &str, msg: &str) -> f64 {
    let mut mac = Hmac::<Sha256>::new_from_slice(server.as_bytes()).expect("hmac key");
    mac.update(msg.as_bytes());
    let b = mac.finalize().into_bytes();
    u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as f64 / 4_294_967_296.0
}

fn unit(server: &str, client: &str, nonce: u64) -> f64 {
    digest(server, &format!("{client}:{nonce}"))
}

#[derive(Deserialize)]
struct FReq {
    server_seed: String,
    client_seed: String,
    nonce: u64,
    count: usize,
}

/// Stream of floats: float i = HMAC-SHA256(server_seed, "client_seed:nonce:i"). Used for shuffles and live rounds.
async fn floats(Json(r): Json<FReq>) -> Result<Json<Vec<f64>>, (StatusCode, String)> {
    if r.count == 0 || r.count > 128 {
        return Err((StatusCode::BAD_REQUEST, "count must be 1-128".into()));
    }
    Ok(Json((0..r.count).map(|i| digest(&r.server_seed, &format!("{}:{}:{}", r.client_seed, r.nonce, i))).collect()))
}

fn resolve(r: &Req) -> Result<Res, String> {
    let u = unit(&r.server_seed, &r.client_seed, r.nonce);
    match r.game.as_str() {
        "dice" => {
            if !(2.0..=98.0).contains(&r.target) {
                return Err("target must be between 2 and 98".into());
            }
            let roll = (u * 10_000.0).floor() / 100.0;
            let chance = if r.over { 100.0 - r.target } else { r.target };
            let win = if r.over { roll > r.target } else { roll < r.target };
            Ok(Res { roll: format!("{roll:.2}"), win, multiplier: 99.0 / chance })
        }
        "limbo" => {
            if !(1.01..=1000.0).contains(&r.target) {
                return Err("target must be between 1.01 and 1000".into());
            }
            let x = ((0.99 / (1.0 - u)).clamp(1.0, 1000.0) * 100.0).floor() / 100.0;
            Ok(Res { roll: format!("{x:.2}x"), win: x >= r.target, multiplier: r.target })
        }
        "flip" => {
            let call = match r.choice.as_str() {
                "H" => true,
                "T" => false,
                _ => return Err("choice must be H or T".into()),
            };
            let heads = u < 0.5;
            let face = if heads { "Heads" } else { "Tails" };
            Ok(Res { roll: face.to_string(), win: heads == call, multiplier: 1.96 })
        }
        "roulette" => {
            let n = (u * 37.0) as u32;
            let (col, name) = if n == 0 {
                ("g", "green")
            } else if RED.contains(&n) {
                ("r", "red")
            } else {
                ("k", "black")
            };
            let mult = match r.choice.as_str() {
                "r" | "k" => 2.0,
                "g" => 35.0,
                _ => return Err("choice must be r, k or g".into()),
            };
            Ok(Res { roll: format!("{n} {name}"), win: col == r.choice, multiplier: mult })
        }
        _ => Err("unknown game".into()),
    }
}

async fn handle(Json(r): Json<Req>) -> Result<Json<Res>, (StatusCode, String)> {
    resolve(&r).map(Json).map_err(|e| (StatusCode::BAD_REQUEST, e))
}

#[tokio::main]
async fn main() {
    let addr = std::env::var("ENGINE_ADDR").unwrap_or_else(|_| "127.0.0.1:8081".into());
    let app = Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/resolve", post(handle))
        .route("/floats", post(floats));
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    println!("engine listening on {addr}");
    axum::serve(listener, app).await.expect("serve");
}
