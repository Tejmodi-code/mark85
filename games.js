/* Tej Money: live tables (WebSocket) and server-side games. Loaded before app.js. */
const RK = ['A','2','3','4','5','6','7','8','9','10','J','Q','K'], SU = ['♠','♥','♦','♣'];
const EXT = {};
const AVATAR_ICON = {fox:'🦊',owl:'🦉',cat:'🐱',bear:'🐻',panda:'🐼',rabbit:'🐰',tiger:'🐯',koala:'🐨'};
const avatarMark = a => AVATAR_ICON[a] || '🦊';
const chatMarkup = room => `<section class="table-chat" data-room="${room}"><h3>Table chat</h3><div class="chat-log" aria-live="polite" aria-label="Table chat messages"></div><form class="chat-form"><input maxlength="240" aria-label="Chat message" placeholder="Say something kind" autocomplete="off"><button class="btn">Send</button></form></section>`;
window.addEventListener('table-chat', e => { const {room,name,avatar,text}=e.detail, log=document.querySelector(`.table-chat[data-room="${room}"] .chat-log`); if(!log)return; const row=document.createElement('div'), who=document.createElement('b'), msg=document.createElement('span'); who.textContent=`${avatarMark(avatar)} ${name}`; msg.textContent=text; row.append(who,msg); log.append(row); while(log.children.length>50) log.firstChild.remove(); log.scrollTop=log.scrollHeight; });
document.addEventListener('submit', e => { const f=e.target.closest('.chat-form'); if(!f)return; e.preventDefault(); const room=f.closest('.table-chat').dataset.room, input=f.querySelector('input'), text=input.value.trim(); if(!text)return; WS.send({t:'chat',room,text}); input.value=''; });

const WS = {
  s: null, subs: {}, rooms: new Set(),
  connect() {
    if (this.s && this.s.readyState < 2) return;
    this.s = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    this.s.onopen = () => { this.send({t:'auth', token:tok || ''}); this.rooms.forEach(r => this.send({t: 'join', room: r})); };
    this.s.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.t === 'bal') { if (me) setCoins(m.coins); } else if (m.t === 'err') toast(m.msg, 1); else if (m.t === 'action') { if (!(m.action === 'cbet' && window.__crashAutopilotActive)) toast(`${m.message} · ${m.latency_ms} ms`, !m.ok); window.dispatchEvent(new CustomEvent('table-action',{detail:m})); } else if (m.t === 'chat') window.dispatchEvent(new CustomEvent('table-chat',{detail:m})); else if (this.subs[m.t]) this.subs[m.t](m);
    };
    this.s.onclose = e => { if (e.code === 4401 && tok) { tok = null; localStorage.removeItem('tm_token'); setMe(null); toast('Session expired. Please sign in again.', 1); } if (this.rooms.size) setTimeout(() => this.connect(), 1500); };
  },
  send(o) { if (this.s && this.s.readyState === 1) this.s.send(JSON.stringify(o)); },
  join(r, fn) { this.rooms.add(r); this.subs[r] = fn; this.connect(); this.send({t: 'join', room: r}); },
  leave(r) { this.rooms.delete(r); delete this.subs[r]; this.send({t: 'leave_room', room: r}); },
  reset() { const o = this.s; this.s = null; if (o) o.close(); if (this.rooms.size) this.connect(); }
};

/* playing cards drawn in CSS; a null card is the face-down back */
function cardEl(c, big) {
  const d = document.createElement('div');
  d.className = 'pc' + (big ? ' big' : '') + (c ? (c.s === 1 || c.s === 2 ? ' red' : '') : ' back');
  d.dataset.k = c ? c.r + '-' + c.s : 'b';
  if (c) d.innerHTML = `<b>${RK[c.r]}</b><i>${SU[c.s]}</i><span>${SU[c.s]}</span>`;
  return d;
}
function syncHand(box, hand) {   // only touch cards that changed, so deal animations play once
  while (box.children.length > hand.length) box.lastChild.remove();
  hand.forEach((c, i) => {
    const e = box.children[i], k = c ? c.r + '-' + c.s : 'b';
    if (!e) box.append(cardEl(c));
    else if (e.dataset.k !== k) { const n = cardEl(c); n.classList.add('flip'); e.replaceWith(n); }
  });
}
const sheetTop = (key, name, live) => `<div class="sh-top"><h2>${icon(key, 22)} ${name}${live ? ' <span class="live">LIVE</span>' : ''}</h2><button class="x" id="cl" aria-label="Close">${icon('close', 14)}</button></div>`;
const betBox = `<label>Bet</label><div class="bet"><input id="bet" type="number" min="1" value="100"><button class="chip" data-m="0.5">½</button><button class="chip" data-m="2">2×</button></div>`;

/* ---------------- Live Blackjack ---------------- */
EXT.bj = {t: 'Live Blackjack', i: 'bj', live: 1, d: 'Take a seat at a live table. Beat the dealer to 21.',
  close() { WS.leave('bj'); },
  open(s) {
    s.innerHTML = sheetTop('bj', 'Live Blackjack', 1) + `
    <div class="felt"><div class="dlr"><small>DEALER <b class="val" id="dv"></b></small><div class="hand" id="dh"></div></div>
      <div class="arc">BLACKJACK PAYS 3 TO 2</div>
      <div class="seats">${[0, 26, 40, 26, 0].map((y, k) => `<div class="seat" data-k="${k}" style="--y:${y}px"><div class="hand"></div><div class="stake" hidden></div><div class="who"><span class="avatar-mark"></span> <span class="nm">Open seat</span> <b class="val"></b></div><div class="badge" hidden></div><button class="sit">Sit here</button></div>`).join('')}</div></div>
    <div class="ctl"><div class="betrow">${[50, 100, 500, 1000].map(v => `<button class="cc" data-v="${v}">${v}</button>`).join('')}</div>
      <div class="acts"><button class="btn" data-a="hit">Hit</button><button class="btn" data-a="stand">Stand</button><button class="btn" data-a="double">Double</button></div>
      <button class="link" id="lv" hidden>Leave seat</button></div>
    <p class="mute" id="bst">Connecting…</p><p class="mono" id="bf"></p>` + chatMarkup('bj');
    s.onclick = e => {
      const sit = e.target.closest('.sit'), cc = e.target.closest('.cc'), a = e.target.closest('[data-a]');
      if (sit) { if (!me) return authOpen(); WS.send({t: 'sit', seat: +sit.parentNode.dataset.k}); }
      else if (cc) WS.send({t: 'bbet', amt: +cc.dataset.v});
      else if (a) WS.send({t: 'bact', a: a.dataset.a});
      else if (e.target.id === 'lv') WS.send({t: 'leave'});
    };
    const seatEls = $$('.seat');
    WS.join('bj', m => {
      const mine = m.seats.findIndex(x => x && me && x.name === me.name), my = mine >= 0 ? m.seats[mine] : null;
      m.seats.forEach((x, k) => {
        const el = seatEls[k], q = c => el.querySelector(c);
        el.classList.toggle('turn', m.phase === 'play' && m.turn === k); el.classList.toggle('me', k === mine);
        q('.nm').textContent = x ? x.name : 'Open seat';
        q('.avatar-mark').textContent = x ? avatarMark(x.avatar) : '';
        q('.val').textContent = x && x.hand.length ? x.val : '';
        syncHand(q('.hand'), x ? x.hand : []);
        const ch = q('.stake'); ch.hidden = !(x && x.bet); ch.textContent = x ? fmt(x.bet) : '';
        const bd = q('.badge'), t = x && (x.res || ({bust: 'Bust', bj: 'Blackjack', stand: 'Stand'})[x.st] || '');
        bd.hidden = !t; bd.textContent = t || '';
        bd.className = 'badge' + (x && x.res ? (x.pay > x.bet ? ' w' : x.pay === x.bet ? '' : ' l') : '');
        q('.sit').hidden = !!x || mine >= 0;
      });
      syncHand($('#dh'), m.dealer); $('#dv').textContent = m.dealer.length ? m.dval : '';
      const myTurn = m.phase === 'play' && mine >= 0 && m.turn === mine;
      $$('[data-a]').forEach(b => b.disabled = !myTurn || (b.dataset.a === 'double' && my.hand.length !== 2));
      $$('.cc').forEach(b => b.disabled = !(m.phase === 'bet' && my && !my.bet));
      $('#lv').hidden = mine < 0;
      const who = m.turn >= 0 && m.seats[m.turn] ? m.seats[m.turn].name : '', sec = ` · ${Math.ceil(m.left)}s`;
      $('#bst').textContent = m.phase === 'bet' ? (mine < 0 ? 'Take a seat, then place your bet' : my.bet ? 'Bet placed. Waiting for the deal' : 'Place your bet') + sec
        : m.phase === 'deal' ? 'Dealing…' : m.phase === 'play' ? (myTurn ? 'Your move' : who + "'s turn") + sec
        : m.phase === 'dealer' ? 'Dealer plays' : (my && my.res ? `${my.res} · ${my.pay >= my.bet ? '+' : '-'}${fmt(Math.abs(my.pay - my.bet))} coins` : 'Round over');
      $('#bf').textContent = `Round ${m.round} · seed hash ${m.hash.slice(0, 20)}…` + (m.rev ? ` · revealed seed ${m.rev}` : '');
    });
  }};

/* ---------------- Live Crash ---------------- */
EXT.crash = {t: 'Live Crash', i: 'crash', live: 1, d: 'One rocket, many players. Cash out before it crashes.',
  close() { WS.leave('crash'); if (this.actionListener) window.removeEventListener('table-action', this.actionListener); },
  open(s) {
    s.innerHTML = sheetTop('crash', 'Live Crash', 1) + `<div class="crashgrid"><div class="crash-main"><canvas id="cv" width="640" height="340" aria-label="Live multiplier graph"></canvas><div class="hist" id="hs" aria-label="Recent rounds"></div>
      <div class="crash-controls"><label>Wager<input id="cb" type="number" min="1" max="1000000" step="1" value="100" aria-label="Wager amount"></label><label>Auto cash-out target<input id="ca" type="number" min="1.01" max="1000" step="0.01" placeholder="Optional · 1.01x–1,000x" aria-label="Auto cash-out target"></label></div>
      <button class="btn" id="cgo" style="width:100%">Place bet</button>
      <section class="autopilot"><div class="autopilot-copy"><span class="eyebrow">OPTIONAL MODE</span><h3>Auto pilot</h3><p>Repeat this wager for a limited number of rounds. A cash-out target is required.</p></div><div class="autopilot-controls"><label>Rounds<input id="caprounds" type="number" min="1" max="20" value="5" aria-label="Auto pilot rounds"></label><button class="btn quiet" id="capilot" type="button">Start auto pilot</button></div><p class="autopilot-status" id="capstatus" role="status" aria-live="polite">Off · set a target and round limit to begin.</p></section>
      <p class="mono" id="cf"></p></div><aside class="plist"><h3>Players</h3><div id="pl"></div></aside></div>` + chatMarkup('crash');
    const cv = $('#cv'), g = cv.getContext('2d'); let pts = [], t0 = 0, mode = 'bet', snapshot = null;
    const pilot = {enabled:false, remaining:0, total:0, lastRound:null, waitingRound:null, bet:0, target:null, applied:new Set()};
    const status = text => { $('#capstatus').textContent = text; };
    const stopPilot = text => { pilot.enabled = false; window.__crashAutopilotActive = false; pilot.waitingRound = null; $('#capilot').textContent = 'Start auto pilot'; $('#capilot').classList.remove('is-running'); if (text) status(text); };
    const finishPilotRound = round => {
      if (pilot.applied.has(round)) return;
      pilot.applied.add(round); pilot.waitingRound = null; pilot.remaining = Math.max(0, pilot.remaining - 1);
      if (!pilot.remaining) stopPilot(`Complete · ${pilot.total} of ${pilot.total} rounds accepted.`);
      else status(`Running · ${pilot.total - pilot.remaining} of ${pilot.total} accepted · next round queued.`);
    };
    const config = () => {
      const bet = Math.floor(Number($('#cb').value)), rawTarget = $('#ca').value.trim(), target = rawTarget ? Number(rawTarget) : null;
      if (!Number.isSafeInteger(bet) || bet < 1 || bet > 1000000) { toast('Enter a wager from 1 to 1,000,000 coins.', 1); return null; }
      if (target !== null && (!Number.isFinite(target) || target < 1.01 || target > 1000)) { toast('Auto cash-out must be from 1.01x to 1,000x.', 1); return null; }
      return {bet, target};
    };
    const tryPilot = m => {
      if (!pilot.enabled || m.phase !== 'bet') return;
      if (pilot.waitingRound !== null && pilot.waitingRound !== m.round) { stopPilot('Stopped · previous round acceptance could not be confirmed.'); return; }
      if (pilot.lastRound === m.round || pilot.applied.has(m.round)) return;
      pilot.lastRound = m.round;
      if (me && m.bets.some(b => b.name === me.name)) { status('Already in this round · auto pilot will continue next round.'); return; }
      pilot.waitingRound = m.round;
      WS.send({t:'cbet', amt:pilot.bet, auto:pilot.target});
      status(`Requesting round ${m.round} · ${pilot.remaining} rounds left.`);
    };
    this.actionListener = e => {
      const m = e.detail;
      if (m.action !== 'cbet' || pilot.waitingRound !== m.round) return;
      if (m.ok) finishPilotRound(m.round);
      else stopPilot(`Stopped · ${m.message}`);
    };
    window.addEventListener('table-action', this.actionListener);
    $('#capilot').onclick = () => {
      if (pilot.enabled) { stopPilot('Stopped by you.'); return; }
      if (!me) return authOpen();
      const cfg = config(), rounds = Math.floor(Number($('#caprounds').value));
      if (!cfg) return;
      if (!cfg.target) { toast('Set an auto cash-out target before starting auto pilot.', 1); return; }
      if (!Number.isInteger(rounds) || rounds < 1 || rounds > 20) { toast('Choose between 1 and 20 rounds.', 1); return; }
      if (!snapshot || snapshot.phase === 'offline') { toast('Live Crash is offline. Auto pilot is unavailable.', 1); return; }
      pilot.enabled = true; window.__crashAutopilotActive = true; pilot.remaining = rounds; pilot.total = rounds; pilot.lastRound = null; pilot.waitingRound = null; pilot.bet = cfg.bet; pilot.target = cfg.target; pilot.applied.clear();
      $('#capilot').textContent = 'Stop auto pilot'; $('#capilot').classList.add('is-running');
      status(`Ready · up to ${rounds} rounds · cash out at ${cfg.target.toFixed(2)}x.`); tryPilot(snapshot);
    };
    $('#cgo').onclick = () => {
      if (!me) return authOpen();
      if (mode === 'bet') { const cfg = config(); if (cfg) WS.send({t:'cbet',amt:cfg.bet,auto:cfg.target}); }
      else if (mode === 'cash') WS.send({t:'ccash'});
    };
    function draw(m) {
      const W = cv.width, H = cv.height; g.clearRect(0, 0, W, H);
      g.strokeStyle = 'rgba(255,255,255,.08)'; g.lineWidth = 1;
      for (let i = 1; i < 5; i++) { g.beginPath(); g.moveTo(0, H * i / 5); g.lineTo(W, H * i / 5); g.stroke(); }
      const maxM = Math.max(2, m.m * 1.2), tMax = Math.max(8, pts.length ? pts[pts.length - 1].t * 1.15 : 8);
      const X = t => 24 + t / tMax * (W - 70), Y = v => H - 24 - Math.log(v) / Math.log(maxM) * (H - 80);
      if (pts.length > 1) {
        g.beginPath(); pts.forEach((p, i) => i ? g.lineTo(X(p.t), Y(p.m)) : g.moveTo(X(p.t), Y(p.m)));
        g.strokeStyle = m.phase === 'crashed' ? '#ff453a' : '#2997ff'; g.lineWidth = 3; g.lineCap = 'round'; g.stroke();
        const l = pts[pts.length - 1]; g.beginPath(); g.arc(X(l.t), Y(l.m), 5, 0, 7); g.fillStyle = g.strokeStyle; g.fill();
      }
      g.textAlign = 'center'; g.font = '700 64px -apple-system,system-ui,sans-serif';
      g.fillStyle = m.phase === 'crashed' ? '#ff453a' : '#fff';
      g.fillText(m.phase === 'bet' ? Math.ceil(m.left) + 's' : m.m.toFixed(2) + 'x', W / 2, H / 2);
      g.font = '600 18px -apple-system,system-ui,sans-serif'; g.fillStyle = 'rgba(255,255,255,.7)';
      g.fillText(m.phase === 'bet' ? 'Place your bets' : m.phase === 'crashed' ? 'Crashed' : m.phase === 'offline' ? 'Game engine offline' : '', W / 2, H / 2 + 34);
    }
    WS.join('crash', m => {
      snapshot = m;
      if (m.phase === 'offline' && pilot.enabled) stopPilot('Stopped · live game engine is offline.');
      if (m.phase === 'bet') { pts = []; t0 = 0; tryPilot(m); }
      if (m.phase === 'run') { if (!t0) t0 = Date.now(); pts.push({t: (Date.now() - t0) / 1000, m: m.m}); }
      if (pilot.enabled && pilot.waitingRound === m.round && m.bets.some(b => me && b.name === me.name)) finishPilotRound(m.round);
      draw(m);
      const mine = me && m.bets.find(b => b.name === me.name), btn = $('#cgo');
      mode = m.phase === 'bet' && !mine ? 'bet' : m.phase === 'run' && mine && mine.cash == null ? 'cash' : 'wait';
      btn.disabled = mode === 'wait';
      btn.textContent = m.phase === 'offline' ? 'Game engine offline' : mode === 'bet' ? 'Place bet' : mode === 'cash' ? `Cash out @ ${m.m.toFixed(2)}x` : mine ? (mine.cash != null ? `Cashed out @ ${mine.cash.toFixed(2)}x` : m.phase === 'crashed' ? 'Crashed' : 'Bet placed') : 'Next round starting…';
      $('#hs').replaceChildren(...m.hist.slice().reverse().map(h => { const e = document.createElement('span'); e.textContent = h.toFixed(2) + 'x'; e.className = h >= 2 ? 'hi' : ''; return e; }));
      $('#pl').replaceChildren(...m.bets.map(b => { const d = document.createElement('div'), a = document.createElement('span'), z = document.createElement('b');
        a.textContent = avatarMark(b.avatar) + ' ' + b.name + ' · ' + fmt(b.amt); z.textContent = b.cash != null ? b.cash.toFixed(2) + 'x' : m.phase === 'crashed' ? 'lost' : '…'; z.className = b.cash != null ? 'win' : m.phase === 'crashed' ? 'lose' : '';
        d.append(a, z); return d; }));
      $('#cf').textContent = `Round ${m.round} · seed hash ${m.hash.slice(0, 20)}…` + (m.rev ? ` · revealed seed ${m.rev}` : '');
    });
  }};

/* ---------------- Mines ---------------- */
EXT.mines = {t: 'Mines', i: 'mines', d: 'Reveal gems, dodge the bombs, cash out any time.',
  open(s) {
    s.innerHTML = sheetTop('mines', 'Mines') + `<div class="row2"><div>${betBox}</div><div><label>Bombs</label><select id="mn">${[1, 3, 5, 10, 15, 20, 24].map(n => `<option${n === 3 ? ' selected' : ''}>${n}</option>`).join('')}</select></div></div>
      <div class="mgrid" id="mg">${'<button class="mt" aria-label="Tile"></button>'.repeat(25)}</div>
      <div class="res" id="ms" style="font-size:26px;min-height:70px">Place a bet to start</div>
      <div class="row2"><button class="btn" id="mgo">Start game</button><button class="btn" id="mc" disabled>Cash out</button></div>`;
    const tiles = $$('#mg .mt'); let st = null;
    const paint = (open, mines, hit) => tiles.forEach((b, i) => {
      b.className = 'mt'; b.textContent = '';
      if (open.includes(i)) { b.classList.add('gem'); b.innerHTML = icon('mines', 22); }
      else if (mines && mines.includes(i)) { b.classList.add('bomb'); if (i === hit) b.classList.add('hit'); b.innerHTML = icon('bomb', 22); }
    });
    const say = (txt, c) => { $('#ms').className = 'res ' + (c || ''); $('#ms').textContent = txt; };
    const info = () => { say(st.mult.toFixed(2) + 'x · next ' + st.next.toFixed(2) + 'x'); $('#mc').disabled = !st.open.length; $('#mgo').disabled = true; };
    const over = () => { st = null; $('#mc').disabled = true; $('#mgo').disabled = false; };
    const fail = e => toast(e.message, 1);
    $('#mgo').onclick = async () => { if (!me) return authOpen();
      try { const r = await api('/mines/start', {bet: Math.floor(+$('#bet').value), mines: +$('#mn').value}); setCoins(r.coins); st = {open: [], n: r.n, mult: 1, next: r.next}; paint([]); info(); } catch (e) { fail(e); } };
    tiles.forEach((b, i) => b.onclick = async () => {
      if (!st || st.open.includes(i)) return;
      try { const r = await api('/mines/reveal', {tile: i});
        if (r.bomb) { paint(r.open, r.mines, i); setCoins(r.coins); say('Boom. Round lost', 'lose'); over(); }
        else if (r.done) { paint(r.open, r.mines); setCoins(r.coins); say(`Board cleared! +${fmt(r.payout)}`, 'win'); over(); }
        else { st.open = r.open; st.mult = r.mult; st.next = r.next; paint(st.open); info(); }
      } catch (e) { fail(e); } });
    $('#mc').onclick = async () => { try { const r = await api('/mines/cash'); setCoins(r.coins); paint(st.open, r.mines); say(`Cashed out ${r.mult.toFixed(2)}x · +${fmt(r.payout)}`, 'win'); over(); } catch (e) { fail(e); } };
    if (me) api('/active').then(a => { if (a.mines) { st = {open: a.mines.open, n: a.mines.n, mult: a.mines.mult, next: a.mines.next}; $('#bet').value = a.mines.bet; paint(st.open); info(); } }).catch(() => {});
  }};

/* ---------------- Hi-Lo ---------------- */
EXT.hilo = {t: 'Hi-Lo', i: 'hilo', d: 'Higher or lower? Chain your wins, then cash out.',
  open(s) {
    s.innerHTML = sheetTop('hilo', 'Hi-Lo') + betBox + `<button class="btn" id="hgo" style="width:100%">Start</button>
      <div class="hlstage" id="hc"></div><div class="res" id="hs" style="font-size:26px;min-height:60px">–</div>
      <div class="row2"><button class="btn" id="hu" disabled>Higher</button><button class="btn" id="hd" disabled>Lower</button></div>
      <button class="btn" id="hcash" disabled style="width:100%">Cash out</button>`;
    let on = false;
    const show = (v, flip) => { const e = cardEl(v.card, true); if (flip) e.classList.add('flip'); $('#hc').replaceChildren(e);
      if (v.up != null) { on = true; $('#hu').textContent = `Higher or same · ${v.up}x`; $('#hd').textContent = `Lower or same · ${v.dn}x`;
        $('#hs').className = 'res'; $('#hs').textContent = `${v.m.toFixed(2)}x · ${fmt(Math.floor(v.bet * v.m))} coins`; }
      $('#hu').disabled = $('#hd').disabled = !on; $('#hcash').disabled = !on || !v.steps; $('#hgo').disabled = on; };
    const end = (txt, c) => { on = false; $('#hs').className = 'res ' + c; $('#hs').textContent = txt; $('#hu').disabled = $('#hd').disabled = $('#hcash').disabled = true; $('#hgo').disabled = false; };
    $('#hgo').onclick = async () => { if (!me) return authOpen();
      try { const r = await api('/hilo/start', {bet: Math.floor(+$('#bet').value)}); setCoins(r.coins); show(r); } catch (e) { toast(e.message, 1); } };
    const guess = up => async () => { try { const r = await api('/hilo/guess', {up}); $('#hc').replaceChildren(Object.assign(cardEl(r.card, true), {className: 'pc big flip' + (r.card.s === 1 || r.card.s === 2 ? ' red' : '')}));
      if (r.lost) { setCoins(r.coins); end('Wrong guess. Round lost', 'lose'); } else if (r.done) { setCoins(r.coins); end(`Deck finished! +${fmt(r.payout)}`, 'win'); } else show(r, true); } catch (e) { toast(e.message, 1); } };
    $('#hu').onclick = guess(true); $('#hd').onclick = guess(false);
    $('#hcash').onclick = async () => { try { const r = await api('/hilo/cash'); setCoins(r.coins); end(`Cashed out ${r.mult.toFixed(2)}x · +${fmt(r.payout)}`, 'win'); } catch (e) { toast(e.message, 1); } };
    $('#hc').replaceChildren(cardEl(null, true));
    if (me) api('/active').then(a => { if (a.hilo) { $('#bet').value = a.hilo.bet; show(a.hilo); } }).catch(() => {});
  }};

/* ---------------- Plinko ---------------- */
EXT.plinko = {t: 'Plinko', i: 'plinko', d: 'Drop the ball. Edge buckets pay up to 5.6x.',
  open(s) {
    s.innerHTML = sheetTop('plinko', 'Plinko') + betBox + `<button class="btn" id="pgo" style="width:100%">Drop ball</button><canvas id="pc" width="420" height="340" style="margin-top:14px"></canvas><div class="res" id="pr" style="font-size:26px;min-height:60px">–</div>`;
    const PM = [5.6, 2.1, 1.1, 1, .5, 1, 1.1, 2.1, 5.6], u = 22, cx = 210, g = $('#pc').getContext('2d'); let pendingKey = null;
    function draw(bx, by, hit) {
      g.clearRect(0, 0, 420, 340); g.fillStyle = 'rgba(255,255,255,.85)';
      for (let r = 0; r < 8; r++) for (let i = 0; i <= r; i++) { g.beginPath(); g.arc(cx + (2 * i - r) * u, 50 + r * 32, 4, 0, 7); g.fill(); }
      PM.forEach((m, i) => { g.fillStyle = i === hit ? '#2997ff' : m >= 2 ? '#48484a' : '#2c2c2e'; g.beginPath(); g.roundRect(cx + (2 * i - 8) * u - 19, 298, 38, 28, 9); g.fill();
        g.fillStyle = '#f5f5f7'; g.font = '700 12px sans-serif'; g.textAlign = 'center'; g.fillText(m + 'x', cx + (2 * i - 8) * u, 316); });
      if (bx != null) { g.fillStyle = '#2997ff'; g.beginPath(); g.arc(bx, by, 7, 0, 7); g.fill(); }
    }
    draw();
    $('#pgo').onclick = async () => { if (!me) return authOpen(); $('#pgo').disabled = true;
      if (!pendingKey) pendingKey = crypto.randomUUID();
      try { const r = await api('/plinko', {bet: Math.floor(+$('#bet').value)}, 'POST', pendingKey), wp = [[cx, 24]]; pendingKey = null; let k = 0;
        r.path.forEach((d, i) => { k += d; wp.push([cx + (2 * k - (i + 1)) * u, 50 + i * 32 + 16]); }); wp.push([cx + (2 * k - 8) * u, 312]);
        const t0 = performance.now(), seg = 150;
        const step = now => { const t = (now - t0) / seg, i = Math.min(wp.length - 2, Math.floor(t)), f = Math.min(1, t - i), a = wp[i], b = wp[i + 1];
          draw(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f - Math.sin(Math.PI * f) * 7);
          if (t < wp.length - 1) requestAnimationFrame(step);
          else { draw(null, 0, r.slot); setCoins(r.coins); $('#pr').className = 'res pop ' + (r.mult >= 1 ? 'win' : 'lose'); $('#pr').textContent = `${r.mult}x · ${r.payout >= +$('#bet').value ? '+' : '-'}${fmt(Math.abs(r.payout - Math.floor(+$('#bet').value)))}`; $('#pgo').disabled = false; } };
        requestAnimationFrame(step);
      } catch (e) { if (e.status) pendingKey = null; toast(e.message, 1); $('#pgo').disabled = false; } };
  }};
