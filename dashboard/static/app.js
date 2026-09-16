/* Dashboard front end. Every message from the server is a full snapshot of
 * the bot's state, and render() just redraws from it. */
'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  status: $('status-pill'), statusText: $('status-text'),
  board: $('board'), tray: $('tray'), plan: $('plan'), planHint: $('plan-hint'),
  log: $('log'), pips: $('combo-pips'),
  speed: $('speed'), speedOut: $('speed-out'),
  btnStart: $('btn-start'), btnPause: $('btn-pause'), btnStop: $('btn-stop'),
  modeDemo: $('mode-demo'), modeLive: $('mode-live'),
};

let selectedMode = 'demo';
let lastLogId = 0;
let hiddenLogBefore = 0;   // "Clear" only hides what's already on screen
let running = false;

/* board */

const cells = [];
for (let i = 0; i < 64; i++) {
  const c = document.createElement('div');
  c.className = 'cell';
  el.board.appendChild(c);
  cells.push(c);
}

function renderBoard(board, plan, activeOrder) {
  // Outline the planned moves that haven't happened yet.
  const ghosts = new Map();
  for (const step of plan || []) {
    if (activeOrder !== null && step.order < activeOrder) continue;
    for (const [r, c] of step.cells) {
      const key = r * 8 + c;
      if (!ghosts.has(key)) ghosts.set(key, step);
    }
  }

  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const i = r * 8 + c;
      const cell = cells[i];
      const color = board?.[r]?.[c] || null;
      const ghost = color ? null : ghosts.get(i);

      cell.className = 'cell' + (color ? ' filled' : '') +
        (ghost ? (ghost.order === activeOrder ? ' ghost next' : ' ghost') : '');
      cell.style.background = color || '';
      cell.style.setProperty('--ghost', ghost ? ghost.color : 'transparent');

      const wantsBadge = ghost && ghost.cells[0][0] === r && ghost.cells[0][1] === c;
      const badge = cell.firstChild;
      if (wantsBadge) {
        if (badge) badge.textContent = ghost.order;
        else {
          const b = document.createElement('span');
          b.className = 'badge';
          b.textContent = ghost.order;
          cell.appendChild(b);
        }
      } else if (badge) {
        cell.removeChild(badge);
      }
    }
  }
}

/* tray */

function renderTray(tray, activeOrder) {
  el.tray.replaceChildren();
  for (let i = 0; i < 3; i++) {
    const slot = document.createElement('div');
    slot.className = 'slot';
    const piece = tray?.[i];

    if (!piece) {
      slot.classList.add('empty');
      slot.textContent = 'empty';
      el.tray.appendChild(slot);
      continue;
    }
    if (piece.placed) slot.classList.add('played');
    if (piece.order && piece.order === activeOrder && !piece.placed) slot.classList.add('active');

    if (piece.order) {
      const badge = document.createElement('span');
      badge.className = 'order';
      badge.textContent = piece.order;
      slot.appendChild(badge);
    }

    const grid = document.createElement('div');
    grid.className = 'piece';
    grid.style.gridTemplateColumns = `repeat(${piece.cells[0].length}, 13px)`;
    for (const row of piece.cells) {
      for (const on of row) {
        const b = document.createElement('i');
        if (on) { b.className = 'on'; b.style.background = piece.color; }
        grid.appendChild(b);
      }
    }
    slot.appendChild(grid);
    el.tray.appendChild(slot);
  }
}

/* plan */

function renderPlan(plan, activeOrder, comboMaintained, comboCounter) {
  el.plan.replaceChildren();
  if (!plan || !plan.length) {
    const li = document.createElement('li');
    li.className = 'none';
    li.textContent = running ? 'Planning the next batch…' : 'No plan yet. Press Start.';
    el.plan.appendChild(li);
    el.planHint.textContent = '';
    el.planHint.style.color = 'var(--dim)';
    return;
  }
  const noCombo = comboCounter >= 3;
  el.planHint.textContent = noCombo ? 'no combo going' : comboMaintained ? 'combo can be kept' : 'combo breaks this batch';
  el.planHint.style.color = noCombo || comboMaintained ? 'var(--dim)' : 'var(--gold)';

  const names = ['left', 'middle', 'right'];
  for (const step of plan) {
    const li = document.createElement('li');
    if (activeOrder !== null && step.order < activeOrder) li.classList.add('done');
    if (step.order === activeOrder) li.classList.add('active');

    const n = document.createElement('span');
    n.className = 'step-n'; n.textContent = step.order;

    const sw = document.createElement('span');
    sw.className = 'swatch'; sw.style.background = step.color;

    const label = document.createElement('span');
    label.textContent = `${names[step.slot]} piece`;

    const where = document.createElement('span');
    where.className = 'where grow';
    where.textContent = `→ r${step.row} c${step.col}`;

    li.append(n, sw, label, where);
    if (step.lines_cleared) {
      const badge = document.createElement('span');
      badge.className = 'clears';
      badge.textContent = `clears ${step.lines_cleared}`;
      li.appendChild(badge);
    }
    el.plan.appendChild(li);
  }
}

/* log */

function renderLog(entries) {
  const fresh = (entries || []).filter((e) => e.id > lastLogId && e.id > hiddenLogBefore);
  if (!fresh.length) {
    if (!el.log.childElementCount) {
      const d = document.createElement('div');
      d.className = 'empty';
      d.textContent = 'waiting for the first move…';
      el.log.appendChild(d);
    }
    return;
  }
  const atBottom = el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 40;
  const empty = el.log.querySelector('.empty');
  if (empty) empty.remove();

  for (const e of fresh) {
    const row = document.createElement('div');
    row.className = 'lv-' + e.level;
    const t = document.createElement('span'); t.className = 't'; t.textContent = e.t;
    const m = document.createElement('span'); m.className = 'm'; m.textContent = e.msg;
    row.append(t, m);
    el.log.appendChild(row);
    lastLogId = e.id;
  }
  if (atBottom) el.log.scrollTop = el.log.scrollHeight;
}

/* chrome */

const STATUS_LABEL = {
  idle: 'Idle', starting: 'Starting', running: 'Running', paused: 'Paused',
  stopped: 'Stopped', stuck: 'Game over', calibrating: 'Calibrating', error: 'Error',
};

function renderChrome(s) {
  el.status.className = 'pill pill-' + (s.status in STATUS_LABEL ? s.status : 'idle');
  el.statusText.textContent = STATUS_LABEL[s.status] || s.status;

  // Use the run flag, not the status. Demo mode briefly shows "Game over"
  // between games while still running.
  running = !!s.running;
  el.btnStart.disabled = running;
  el.btnPause.disabled = !running;
  el.btnStop.disabled = !running;
  el.btnPause.textContent = s.status === 'paused' ? 'Resume' : 'Pause';
  el.modeDemo.disabled = running;
  el.modeLive.disabled = running;

  // Three placements without a clear breaks the combo.
  [...el.pips.children].forEach((pip, i) => {
    pip.className = i < s.combo_counter ? (s.combo_counter >= 2 && i === s.combo_counter - 1 ? 'spent last' : 'spent') : '';
  });

  const st = s.stats || {};
  $('s-pieces').textContent = st.pieces_placed ?? 0;
  $('s-lines').textContent = st.lines_cleared ?? 0;
  $('s-batches').textContent = st.batches ?? 0;
  $('s-streak').textContent = st.best_streak ?? 0;
  $('s-broken').textContent = st.combos_broken ?? 0;
  $('s-ppm').textContent = st.ppm ?? 0;
  const secs = Math.floor(st.elapsed ?? 0);
  $('s-elapsed').textContent = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

function render(s) {
  renderChrome(s);
  renderBoard(s.board, s.plan, s.active_order);
  renderTray(s.tray, s.active_order);
  renderPlan(s.plan, s.active_order, s.combo_maintained, s.combo_counter);
  renderLog(s.log);
  if (document.activeElement !== el.speed) {
    const ms = Math.round((s.speed ?? 0.45) * 1000);
    if (String(ms) !== el.speed.value) { el.speed.value = ms; el.speedOut.textContent = (ms / 1000).toFixed(2) + 's'; }
  }
}

/* transport */

async function post(body) {
  try {
    const res = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.state) render(data.state);
  } catch (err) {
    console.error('control failed', err);
  }
}

function connect() {
  // EventSource reconnects on its own if the stream drops.
  const source = new EventSource('/api/stream');
  source.onmessage = (ev) => render(JSON.parse(ev.data));
}

/* wiring */

el.modeDemo.onclick = () => setMode('demo');
el.modeLive.onclick = () => setMode('live');

function setMode(mode) {
  selectedMode = mode;
  el.modeDemo.classList.toggle('is-active', mode === 'demo');
  el.modeLive.classList.toggle('is-active', mode === 'live');
}

el.btnStart.onclick = () => post({ action: 'start', mode: selectedMode });
el.btnStop.onclick = () => post({ action: 'stop' });
el.btnPause.onclick = () => post({ action: el.btnPause.textContent === 'Pause' ? 'pause' : 'resume' });
$('btn-reset-combo').onclick = () => post({ action: 'reset_combo' });
$('btn-diagnose').onclick = () => post({ action: 'diagnose' });

$('btn-clear-log').onclick = () => {
  hiddenLogBefore = lastLogId;
  el.log.replaceChildren();
  const d = document.createElement('div');
  d.className = 'empty';
  d.textContent = 'log cleared';
  el.log.appendChild(d);
};

el.speed.oninput = () => { el.speedOut.textContent = (el.speed.value / 1000).toFixed(2) + 's'; };
el.speed.onchange = () => post({ action: 'speed', value: el.speed.value / 1000 });

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); if (running) el.btnPause.click(); else el.btnStart.click(); }
});

// The server embeds the current state in the page, so draw that straight away.
let initial = null;
try { initial = JSON.parse(document.getElementById('initial-state').textContent); } catch (err) { initial = null; }
if (initial) render(initial);
else fetch('/api/state').then((r) => r.json()).then(render).catch(() => {});

// Connect after load. An open stream would otherwise keep the page "loading".
if (document.readyState === 'complete') connect();
else window.addEventListener('load', connect);
