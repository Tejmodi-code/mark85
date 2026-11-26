const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const fmt = n => Number(n).toLocaleString('en-IN');
let tok = localStorage.getItem('tm_token'), me = null, cur = null, shown = 0, mode = 'signup', balanceFrame = 0;
const AVATAR = {fox:'🦊',owl:'🦉',cat:'🐱',bear:'🐻',panda:'🐼',rabbit:'🐰',tiger:'🐯',koala:'🐨'};

async function api(path, body, method, requestKey) {
  const r = await fetch('/api' + path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: {'Content-Type': 'application/json', ...(tok ? {Authorization: 'Bearer ' + tok} : {}), ...(['/play','/plinko'].includes(path) ? {'Idempotency-Key': requestKey || crypto.randomUUID()} : {})},
    body: body ? JSON.stringify(body) : undefined});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { if (r.status === 401 && tok) { tok = null; localStorage.removeItem('tm_token'); setMe(null); }
    const error = new Error(typeof d.detail === 'string' ? d.detail : 'Check your input and try again'); error.status = r.status; throw error; }
  return d;
}
function toast(m, bad) {
  const t = $('#toast'); t.textContent = m; t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.className = 'toast', 2800);
}
function countTo(to) {
  cancelAnimationFrame(balanceFrame);
  const from = shown, t0 = performance.now();
  const step = now => { const k = Math.min(1, (now - t0) / 600), e = 1 - Math.pow(1 - k, 3);
    $('#balv').textContent = fmt(Math.round(from + (to - from) * e));
    if (k < 1) balanceFrame = requestAnimationFrame(step); else { shown = to; balanceFrame = 0; } };
  balanceFrame = requestAnimationFrame(step);
}
function setMe(m) {
  me = m; $('#guest').hidden = !!m; $('#acct').hidden = !m; $('#cta').hidden = !!m;
  if (m) { $('#av').textContent = AVATAR[m.avatar] || '🦊'; $('#mname').textContent = m.name; $('#mmail').textContent = m.email || 'No email on file'; countTo(m.coins); } else shown = 0;
}
function setCoins(c) { me.coins = c; countTo(c); }

/* games */
const G = {
  dice: {t: 'Dice', i: 'dice', d: 'Roll over or under your target. Lower odds pay more.',
    ui: '<label>Target: <b id="tv">50</b></label><input id="tg" type="range" min="2" max="98" value="50"><label>Win when the roll is</label><div class="chips" id="ov"><button class="on" data-o="0">Under</button><button data-o="1">Over</button></div>',
    p: () => ({target: +$('#tg').value, over: $('#ov .on').dataset.o === '1'})},
  limbo: {t: 'Limbo', i: 'limbo', d: 'Pick a multiplier. Win if the result reaches it.',
    ui: '<label>Target multiplier</label><input id="lt" type="number" value="2" min="1.01" step="0.01">',
    p: () => ({target: +$('#lt').value})},
  flip: {t: 'Coin flip', i: 'flip', d: 'Call it in the air. Pays 1.96x.',
    ui: '<label>Your call</label><div class="chips" id="ch"><button class="on" data-c="H">Heads</button><button data-c="T">Tails</button></div>',
    p: () => ({choice: $('#ch .on').dataset.c})},
  roulette: {t: 'Roulette', i: 'roulette', d: 'One-zero wheel. Red and black pay 2x, green pays 35x.',
    ui: '<label>Bet on</label><div class="chips" id="ch"><button class="on" data-c="r">Red</button><button data-c="k">Black</button><button data-c="g">Green</button></div>',
    p: () => ({choice: $('#ch .on').dataset.c})}
};
const SECTIONS = [['Classics', ['dice', 'limbo', 'flip', 'roulette']], ['Live tables', ['bj', 'crash']], ['Originals', ['mines', 'hilo', 'plinko']]];
$('#tiles').innerHTML = SECTIONS.map(([title, keys]) => {
  const tiles = keys.map(k => { const g = {...G, ...EXT}[k];
    return `<button class="tile" data-g="${k}">${g.live ? '<span class="tag">Live</span>' : ''}<span class="ic">${icon(g.i, 26)}</span><b>${g.t}</b><small>${g.d.split('.')[0]}</small></button>`; }).join('');
  return `<div class="tgroup"><h2>${title}</h2><div class="tiles">${tiles}</div></div>`;
}).join('');

function closeSheet() { const o = EXT[cur]; if (o && o.close) o.close(); $('#sheet').hidden = true; cur = null; }
function openGame(k) {
  const o = EXT[cur]; if (o && o.close) o.close();
  const s = $('#sheet'); cur = k; s.hidden = false; s.onclick = null;
  if (EXT[k]) { s.className = 'card sheet'; EXT[k].open(s); s.scrollIntoView({behavior: 'smooth', block: 'center'}); return; }
  const g = G[k];
  s.innerHTML = `<div class="sh-top"><h2>${icon(g.i, 22)} ${g.t}</h2><button class="x" id="cl" aria-label="Close">${icon('close', 14)}</button></div><p class="mute">${g.d}</p>${g.ui}
    <label>Bet</label><div class="bet"><input id="bet" type="number" min="1" value="100"><button class="chip" data-m="0.5">½</button><button class="chip" data-m="2">2×</button></div>
    <button class="btn" id="go">Play</button><div class="res" id="res">–</div>`;
  s.scrollIntoView({behavior: 'smooth', block: 'center'});
}
let pendingPlayKey = null, pendingPlaySignature = null;
async function play() {
  if (!me) return authOpen();
  const bet = Math.floor(+$('#bet').value);
  if (!(bet >= 1)) return toast('Enter a bet of at least 1 coin', 1);
  $('#go').disabled = true;
  const payload = {game: cur, bet, ...G[cur].p()}, signature = JSON.stringify(payload);
  if (!pendingPlayKey || pendingPlaySignature !== signature) { pendingPlayKey = crypto.randomUUID(); pendingPlaySignature = signature; }
  try {
    const r = await api('/play', payload, 'POST', pendingPlayKey), el = $('#res');
    pendingPlayKey = pendingPlaySignature = null;
    el.className = 'res'; void el.offsetWidth; el.className = 'res pop ' + (r.win ? 'win' : 'lose');
    el.textContent = r.roll; setCoins(r.coins);
    toast(r.win ? `You won ${fmt(r.payout - bet)} coins` : `You lost ${fmt(bet)} coins`, !r.win);
  } catch (e) { if (e.status) pendingPlayKey = pendingPlaySignature = null; toast(e.message, 1); }
  $('#go').disabled = false;
}
document.addEventListener('click', e => {
  const t = e.target.closest('.tile'); if (t) return openGame(t.dataset.g);
  const c = e.target.closest('.chips button'); if (c) { c.parentNode.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === c)); return; }
  const m = e.target.closest('[data-m]'); if (m) { const i = $('#bet'); i.value = Math.max(1, Math.floor(i.value * m.dataset.m)); return; }
  if (e.target.id === 'go') play();
  if (e.target.closest('#cl')) closeSheet();
});
document.addEventListener('input', e => { if (e.target.id === 'tg') $('#tv').textContent = e.target.value; });

/* views */
const VIEWS = ['lobby', 'wallet', 'profile'];
function showView(v) {
  $$('#seg button').forEach(x => x.classList.toggle('on', x.dataset.v === v));
  VIEWS.forEach(n => $('#v-' + n).hidden = n !== v);
  markNav(); closeDrawer(); window.scrollTo({top: 0});
  if (v === 'profile') loadProfile(); if (v === 'wallet') loadWallet();
}
$('#seg').onclick = e => { const b = e.target.closest('button'); if (b) showView(b.dataset.v); };
function row(tb, cells, cls) {
  const tr = document.createElement('tr');
  cells.forEach((v, i) => { const td = document.createElement('td'); td.textContent = v; if (cls && cls[i]) td.className = cls[i]; tr.append(td); });
  tb.append(tr);
}
function head(tb, cols) { tb.innerHTML = ''; const h = tb.createTHead().insertRow(); cols.forEach(c => { const th = document.createElement('th'); th.textContent = c; h.append(th); }); return tb.createTBody(); }
const sgn = v => (v >= 0 ? '+' : '-') + fmt(Math.abs(v)), cls = v => v >= 0 ? 'win' : 'lose';

async function loadProfile() {
  if (!me) { authOpen(); return; }
  try {
    const p = await api('/profile'); setMe(p.user);
    $('#pn').textContent = p.user.name;
    if ($('#avatar')) $('#avatar').value = p.user.avatar || 'fox';
    const n = p.by_game.reduce((a, g) => a + g.bets, 0), st = [['Balance', fmt(p.user.coins)], ['Bets', n], ['Wagered', fmt(p.wagered)], ['Paid out', fmt(p.paid)], ['Net', sgn(p.paid - p.wagered)], ['Return', p.wagered ? (p.paid / p.wagered * 100).toFixed(1) + '%' : '–']];
    $('#stats').innerHTML = ''; st.forEach(([k, v]) => { const d = document.createElement('div'), s = document.createElement('small'), b = document.createElement('b'); s.textContent = k; b.textContent = v; d.append(s, b); $('#stats').append(d); });
    let tb = head($('#tg'), ['Game', 'Bets', 'Wagered', 'Paid out', 'Net']);
    p.by_game.forEach(g => row(tb, [g.game, g.bets, fmt(g.wagered), fmt(g.paid), sgn(g.paid - g.wagered)], [0, 0, 0, 0, cls(g.paid - g.wagered)]));
    tb = head($('#th'), ['Time', 'Game', 'Bet', 'Result', 'Payout', 'Profit', 'Nonce']);
    p.history.forEach(h => row(tb, [new Date(h.time + 'Z').toLocaleString([], {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}), h.game, fmt(h.bet), h.roll, fmt(h.payout), sgn(h.payout - h.bet), h.nonce], [0, 0, 0, 0, 0, cls(h.payout - h.bet)]));
    $('#rec').textContent = p.in_play ? `${fmt(p.in_play)} coins are on a table or in an unfinished round right now.` : p.balanced ? 'Every coin is accounted for: signup bonus and top-ups plus all results equals your balance.' : 'Your balance does not match the ledger. Please report this.';
    $('#fh').textContent = `Active server seed hash: ${p.user.seed_hash}\nClient seed: ${p.user.client_seed} · next nonce: ${p.user.nonce}`;
  } catch (e) { toast(e.message, 1); }
}
$('#avatar').onchange = async () => {
  try { const r = await api('/avatar', {avatar: $('#avatar').value}, 'PUT'); setMe(r); toast('Avatar updated'); }
  catch (e) { toast(e.message, 1); }
};
$('#rot').onclick = async () => {
  try {
    const r = await api('/seed/rotate', {client_seed: $('#cs').value}); setMe(r.user);
    $('#rv').textContent = `Revealed server seed: ${r.revealed.server_seed}\nClient seed used: ${r.revealed.client_seed} · bets under this seed: ${r.revealed.bets}`;
    $('#fh').textContent = `Active server seed hash: ${r.user.seed_hash}\nClient seed: ${r.user.client_seed} · next nonce: 0`;
  } catch (e) { toast(e.message, 1); }
};

/* auth */
function authOpen(m) { authMode(m || mode); $('#auth').hidden = false; $('#an').focus(); }
function authMode(m) {
  mode = m; const su = m === 'signup';
  $('#at').textContent = su ? 'Create your account' : 'Welcome back';
  $('#asub').textContent = su ? 'Create account and claim 10,000 coins' : 'Log in';
  $$('.su').forEach(e => e.hidden = !su); $('#aerr').textContent = '';
  $$('#atabs button').forEach(b => b.classList.toggle('on', b.dataset.m === m));
  $('#ap').autocomplete = su ? 'new-password' : 'current-password';
}
const aerr = t => { $('#aerr').textContent = t; };
$('#atabs').onclick = e => { const b = e.target.closest('button'); if (b) authMode(b.dataset.m); };
$('#ax').onclick = () => $('#auth').hidden = true;
$('#auth').addEventListener('click', e => { if (e.target.id === 'auth') $('#auth').hidden = true; });
$('#show').onchange = e => { $('#ap').type = $('#ap2').type = e.target.checked ? 'text' : 'password'; };
$('#lg').onclick = () => authOpen('login'); $('#sg').onclick = () => authOpen('signup'); $('#sg2').onclick = () => authOpen('signup');
$('#bal').onclick = () => showView('wallet');
$('#af').onsubmit = async e => {
  e.preventDefault(); aerr('');
  const su = mode === 'signup', body = {name: $('#an').value.trim(), password: $('#ap').value};
  if (!body.name || !body.password) return aerr('Enter your username and password');
  if (su) {
    if (body.password !== $('#ap2').value) return aerr('The two passwords do not match');
    body.avatar = $('#newavatar').value;
    if (!$('#age').checked) return aerr('Please confirm that you are 18 or older');
    body.email = $('#am').value.trim();
  }
  try {
    const r = await api('/' + mode, body);
    tok = r.token; localStorage.setItem('tm_token', tok); $('#auth').hidden = true; $('#af').reset(); setMe(r.user); WS.reset();
    toast(su ? 'Welcome! 10,000 play coins added' : 'Welcome back, ' + r.user.name);
  } catch (err) { aerr(err.message); }
};
/* account menu */
$('#av').onclick = e => { e.stopPropagation(); $('#menu').hidden = !$('#menu').hidden; if (!$('#menu').hidden) menuMoney(); };
document.addEventListener('click', e => { if (!e.target.closest('#acct')) $('#menu').hidden = true; });
$('#m-prof').onclick = () => { $('#menu').hidden = true; $('#seg button[data-v=profile]').click(); };
$('#m-top').onclick = async () => { $('#menu').hidden = true; try { await api('/topup', {}); setMe(await api('/me')); toast('1,000 coins added'); } catch (e) { toast(e.message, 1); } };
$('#m-out').onclick = async () => {
  $('#menu').hidden = true;
  try { await api('/logout', {}); } catch (e) {}
  tok = null; localStorage.removeItem('tm_token'); closeSheet(); setMe(null); WS.reset();
  $('#seg button[data-v=lobby]').click(); toast('You have been signed out');
};
if (tok) api('/me').then(setMe).catch(() => {});
window.addEventListener('load', seg); window.addEventListener('resize', seg);


/* ---- sidebar ---- */
const GROUPS = {'#sg-classic': ['dice', 'limbo', 'flip', 'roulette'], '#sg-live': ['bj', 'crash'], '#sg-orig': ['mines', 'hilo', 'plinko']};
const ALL = {...G, ...EXT};
Object.entries(GROUPS).forEach(([sel, keys]) => keys.forEach(k => {
  const b = document.createElement('button');
  b.className = 'sb'; b.dataset.g = k; b.innerHTML = icon(ALL[k].i); b.append(ALL[k].t); $(sel).append(b);
}));
const NAV = {lobby: ['home', 'Lobby'], wallet: ['wallet', 'Wallet'], profile: ['user', 'Profile and history']};
$$('.sb[data-v]').forEach(b => { const [i, t] = NAV[b.dataset.v]; b.innerHTML = icon(i); b.append(t); });
$('#sb-top').innerHTML = icon('gift') + 'Daily top-up'; $('#sb-fair').innerHTML = icon('shield') + 'Provably fair';
$('#burger').innerHTML = icon('menu', 20);
function curView() { return VIEWS.find(n => !$('#v-' + n).hidden); }
function markNav() {
  $$('.sb[data-v]').forEach(b => b.classList.toggle('on', !cur && b.dataset.v === curView()));
  $$('.sb[data-g]').forEach(b => b.classList.toggle('on', b.dataset.g === cur && curView() === 'lobby'));
}
{ const o = openGame, c = closeSheet;
  openGame = k => { o(k); markNav(); }; closeSheet = () => { c(); markNav(); }; }
function closeDrawer() { $('#side').classList.remove('open'); $('#sscrim').classList.remove('on'); }
$('#burger').onclick = () => { $('#side').classList.add('open'); $('#sscrim').classList.add('on'); };
$('#sscrim').onclick = closeDrawer;
$('#side').addEventListener('click', e => {
  const v = e.target.closest('.sb[data-v]'); if (v) { if (cur) closeSheet(); return showView(v.dataset.v); }
  const g = e.target.closest('.sb[data-g]'); if (g) { showView('lobby'); openGame(g.dataset.g); closeDrawer(); }
});
$('#sb-top').onclick = () => $('#m-top').click();
$('#sb-fair').onclick = () => { showView('profile'); setTimeout(() => $('#fh').scrollIntoView({behavior: 'smooth', block: 'center'}), 400); };

/* ---- wallet ---- */
const GN = {dice: 'Dice', limbo: 'Limbo', flip: 'Coin flip', roulette: 'Roulette', mines: 'Mines', hilo: 'Hi-Lo', plinko: 'Plinko', crash: 'Live Crash', bj: 'Live Blackjack', blackjack: 'Live Blackjack'};
const gname = g => GN[g] || g;
function statCards(box, items) {
  box.innerHTML = '';
  items.forEach(([k, v]) => { const d = document.createElement('div'), s = document.createElement('small'), b = document.createElement('b'); s.textContent = k; b.textContent = v; d.append(s, b); box.append(d); });
}
function chart(box, days) {
  box.innerHTML = '';
  if (!days.length) { box.innerHTML = '<p class="mute">Play a few rounds and your daily results appear here.</p>'; return; }
  const NS = 'http://www.w3.org/2000/svg', W = 720, H = 220, pad = 28, max = Math.max(1, ...days.map(d => Math.abs(d.net))), mid = H / 2, bw = Math.min(40, (W - pad * 2) / days.length - 8);
  const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const el = (n, a, t) => { const x = document.createElementNS(NS, n); Object.entries(a).forEach(([k, v]) => x.setAttribute(k, v)); if (t) x.textContent = t; svg.append(x); return x; };
  el('line', {x1: pad, x2: W - pad, y1: mid, y2: mid, stroke: 'currentColor', 'stroke-opacity': .2});
  days.forEach((d, i) => {
    const x = pad + (i + .5) * ((W - pad * 2) / days.length), h = Math.max(3, Math.abs(d.net) / max * (mid - 26));
    const r = el('rect', {x: x - bw / 2, width: bw, rx: 6, y: d.net >= 0 ? mid - h : mid, height: h, fill: d.net >= 0 ? '#32d74b' : '#ff453a'});
    const t = document.createElementNS(NS, 'title'); t.textContent = `${d.day}: ${sgn(d.net)} coins in ${d.bets} bets`; r.append(t);
    el('text', {x, y: H - 4, 'text-anchor': 'middle'}, d.day.slice(5));
  });
  box.append(svg);
}
async function loadWallet() {
  if (!me) { authOpen(); return; }
  try {
    const w = await api('/wallet'); setCoins(w.balance);
    statCards($('#wstats'), [['Balance', fmt(w.balance)], ['Received', fmt(w.received_total)], ['Wagered', fmt(w.wagered)], ['Paid out', fmt(w.paid)],
      ['Bets placed', fmt(w.bets)], ['Net result', sgn(w.net)], ['On tables now', fmt(w.in_play)], ['Biggest win', w.biggest_win ? '+' + fmt(w.biggest_win) : '–']]);
    chart($('#wchart'), w.daily);
    const box = $('#wgames'); box.innerHTML = '';
    if (!w.by_game.length) box.innerHTML = '<p class="mute">No bets yet. Pick a game from the sidebar.</p>';
    const top = Math.max(1, ...w.by_game.map(g => g.wagered));
    w.by_game.forEach(g => {
      const r = document.createElement('div'), a = document.createElement('span'), m = document.createElement('div'), z = document.createElement('span'), s = document.createElement('small');
      r.className = 'wbar'; a.textContent = gname(g.game); s.textContent = `${g.bets} bets`; a.append(s);
      m.className = 'tr'; const i = document.createElement('i'); i.style.width = '0%'; m.append(i); setTimeout(() => i.style.width = (g.wagered / top * 100) + '%', 60);
      z.textContent = sgn(g.net); z.className = cls(g.net) === 'win' ? 'win' : 'lose'; z.style.color = g.net >= 0 ? 'var(--win)' : 'var(--lose)';
      const wg = document.createElement('small'); wg.textContent = fmt(g.wagered) + ' wagered'; z.append(wg); r.append(a, m, z); box.append(r);
    });
    const tb = head($('#wled'), ['Time', 'Type', 'Coins']);
    const KIND = {bonus: 'Signup bonus', topup: 'Daily top-up'};
    w.ledger.forEach(l => row(tb, [new Date(l.time + 'Z').toLocaleString([], {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}), KIND[l.kind] || l.kind, '+' + fmt(l.amount)], [0, 0, 'win']));
    $('#wrec').textContent = w.balanced === null ? '' : w.balanced ? 'Received + paid out - wagered - on tables = your balance.' : 'Your balance does not match the ledger. Please report this.';
  } catch (e) { toast(e.message, 1); }
}
async function menuMoney() {
  if (!me) return;
  try { const w = await api('/wallet');
    const box = $('#mmoney'); box.innerHTML = '';
    [['Balance', fmt(w.balance)], ['Wagered', fmt(w.wagered)], ['Paid out', fmt(w.paid)], ['Net', sgn(w.net)], ['On tables', fmt(w.in_play)], ['Received', fmt(w.received_total)]].forEach(([k, v]) => {
      const r = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = k; dd.textContent = v; r.append(dt, dd); box.append(r); });
  } catch (e) {}
}

/* ---- sponsors ---- */
async function loadSponsors() {
  let list = []; try { list = await api('/sponsors'); } catch (e) { return; }
  if (!list.length) { $('.spons').hidden = true; return; }
  const row = $('#srow'); row.innerHTML = '';
  [0, 1].forEach(copy => list.forEach(s => {
    const b = document.createElement('button'), n = document.createElement('b'), t = document.createElement('span');
    b.className = 'sp'; b.title = s.cta;
    if (copy) { b.tabIndex = -1; b.setAttribute('aria-hidden', 'true'); }
    n.textContent = s.name; t.textContent = s.tagline; b.append(n, t);
    b.onclick = () => { api('/sponsors/' + s.id + '/click', {}).catch(() => {}); if (/^(https?:|mailto:)/.test(s.url)) window.open(s.url, '_blank', 'noopener'); };
    row.append(b);
  }));
  row.style.setProperty('--dur', Math.max(40, list.length * 12) + 's');
}
loadSponsors();
const topH = () => document.documentElement.style.setProperty('--topH', $('#top').offsetHeight + 'px');
window.addEventListener('load', topH); window.addEventListener('resize', topH); topH();
$('#m-wal').onclick = () => { $('#menu').hidden = true; showView('wallet'); };
