// Супервизор МиниГен — тихий, без окон.
//
// Зачем (15.11.2026, просьба владельца):
//   * окно терминала от автозапуска пугало: закрыть его = убить и сторожа, и
//     панель (оба висели на одном cmd-консоли). Теперь супервизор запускается
//     БЕЗ консоли (через VBS), и закрывать нечего;
//   * кнопки «пуск / стоп / перезапуск» самой панели: раньше их не было;
//   * панель поднимается сама, если процесс упал.
//
// Что делает:
//   * держит процесс панели (`node mcode-panel.js 8099`) и перезапускает его,
//     когда тот упал, — пока состояние «running»;
//   * состояние «running/stopped» лежит в файле: панель, остановленная
//     владельцем, не поднимается и после перезагрузки;
//   * слушает 127.0.0.1:8098: страница управления + /status /start /stop /restart.
//
// Запуск (тихо, без окна):  wscript.exe mcode-panel-supervisor.vbs

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const HOME = process.env.USERPROFILE;
const BIN = path.join(HOME, 'bin');
const PANEL_SCRIPT = path.join(BIN, 'mcode-panel.js');
const HEALER_SCRIPT = path.join(BIN, 'minimax-autheal.py');
const NETWATCH_SCRIPT = path.join(BIN, 'minimax-netwatch.py');
const SESSIONWATCH_SCRIPT = path.join(BIN, 'minimax-sessionwatch.py');
const PANEL_PORT = 8099;
const CTRL_PORT = 8098;
const LOCAL = process.env.LOCALAPPDATA || path.join(HOME, 'AppData', 'Local');
const LOG_DIR = path.join(LOCAL, 'mcode-web');
const LOG_FILE = path.join(LOG_DIR, 'supervisor.log');
const STATE_FILE = path.join(LOG_DIR, 'panel.state');
const LOCK_FILE = path.join(LOG_DIR, 'supervisor.lock');

function log(line) {
  const t = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const entry = t + ' — ' + line;
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(LOG_FILE, entry + '\n');
  } catch {}
}

function ensureDir() { try { fs.mkdirSync(LOG_DIR, { recursive: true }); } catch {} }

// Прокси-переменные для дочерних процессов.
// ⚠️ Супервизор унаследовал переменные от момента своего старта; если сетевой
// сторож починил мёртвый прокси (порт 10809 → 10808), новые процессы
// должны получить ИСПРАВЛЕННЫЕ значения, иначе `mcode` снова упадёт с
// «Connection error». Значения берём из файла, который пишет сторож.
const PROXY_ENV_FILE = path.join(LOG_DIR, 'proxy-env.json');

function childEnv() {
  const env = { ...process.env };
  try {
    const px = JSON.parse(fs.readFileSync(PROXY_ENV_FILE, 'utf8'));
    for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY']) {
      if (px[k]) env[k] = px[k];
    }
  } catch {}
  return env;
}

// ────────────────────────────── состояние ──────────────────────────────

function desiredState() {
  try {
    const raw = fs.readFileSync(STATE_FILE, 'utf8').trim();
    return raw === 'stopped' ? 'stopped' : 'running';
  } catch { return 'running'; }
}

function setState(value) {
  ensureDir();
  try { fs.writeFileSync(STATE_FILE, value); } catch {}
}

// ────────────────────────────── экземплярность ──────────────────────────────
// Проверяется по порту управления внизу (probeControl): pid в Windows
// переиспользуется, и lock-файл с pid однажды «выдал» чужой процесс (G633).
// Lock-файл оставлен только для справки — кто поднял супервизор.

function writeLock() {
  ensureDir();
  try { fs.writeFileSync(LOCK_FILE, String(process.pid)); } catch {}
}

// ────────────────────────────── панель ──────────────────────────────

let panel = null;

function panelAlive() {
  return !!(panel && panel.exitCode === null && !panel.killed);
}

function startPanel() {
  if (panelAlive()) return { ok: true, already: true, pid: panel.pid };
  setState('running');
  panel = spawn(process.execPath, [PANEL_SCRIPT, String(PANEL_PORT)], {
    cwd: BIN,
    windowsHide: true,          // окна не создаём
    stdio: 'ignore',
    env: childEnv(),
  });
  const pid = panel.pid;
  log('панель запущена, pid ' + pid);
  startHealer();
  startWatchers();
  panel.on('exit', (code, signal) => {
    log('панель завершилась (code=' + code + ', signal=' + signal + ')');
    panel = null;
    if (desiredState() === 'running') {
      log('панель упала — поднимаю заново через 3 с');
      setTimeout(startPanel, 3000);
    } else {
      log('состояние «stopped» — не поднимаю');
    }
  });
  return { ok: true, pid };
}

function stopPanel() {
  setState('stopped');
  if (panel) {
    try { panel.kill(); } catch {}
    panel = null;
  }
  stopHealer();
  stopWatchers();
  log('панель остановлена по команде владельца');
  return { ok: true };
}

// ────────────────────────────── авто-лечение областей ──────────────────────────────
// Живёт рядом с панелью: поднимается вместе с ней, умирает вместе с ней и
// поднимается заново, если упал. Решение владельца 16.11: лечим область, когда
// у неё кончились очки (отказ «insufficient balance»), — новый аккаунт, бонус
// активируется, области перезапускаются, в сессию уходит «Продолжай».
let healer = null;

function healerAlive() {
  return !!(healer && healer.exitCode === null && !healer.killed);
}

function startHealer() {
  if (healerAlive()) return;
  try {
    healer = spawn('python', [HEALER_SCRIPT], {
      cwd: BIN,
      windowsHide: true,
      stdio: 'ignore',
      env: childEnv(),
    });
    log('авто-лечение запущено, pid ' + healer.pid);
    healer.on('exit', (code, signal) => {
      log('авто-лечение завершилось (code=' + code + ', signal=' + signal + ')');
      healer = null;
      if (desiredState() === 'running') setTimeout(startHealer, 5000);
    });
  } catch (e) {
    log('авто-лечение не поднялось: ' + e.message);
  }
}

function stopHealer() {
  if (healer) {
    try { healer.kill(); } catch {}
    healer = null;
    log('авто-лечение остановлено');
  }
}

// ── Сторожа сети и сессий (21.11.2026, заказ владельца) ────────────────────
// Живут рядом с панелью: поднимаются вместе с ней, перезапускаются при падении.
//
// Отдельные процессы, а не часть лечащего: лечащий лечит АККАУНТЫ (отказ по
// очкам), сетевой сторож — СЕТЬ (интернет, VPN, доступ к модели), сторож
// сессий — СЕССИИ (работа стоит / контекст забит). Разные кулдауны, разные
// правила, разные журналы — смешивать их в одном цикле нельзя.
let watchers = [];

function watcherAlive(w) { return !!(w && w.child && w.child.exitCode === null && !w.child.killed); }

function startWatchers() {
  const specs = [
    { name: 'сетевой сторож', script: NETWATCH_SCRIPT },
    { name: 'сторож сессий', script: SESSIONWATCH_SCRIPT },
  ];
  for (const spec of specs) {
    if (watchers.some((w) => w.name === spec.name && watcherAlive(w))) continue;
    if (!fs.existsSync(spec.script)) {
      log(spec.name + ': файла нет — ' + spec.script);
      continue;
    }
    try {
      const child = spawn('python', [spec.script], {
        cwd: BIN,
        windowsHide: true,
        stdio: 'ignore',
        env: childEnv(),
      });
      log(spec.name + ' запущен, pid ' + child.pid);
      child.on('exit', (code, signal) => {
        log(spec.name + ' завершился (code=' + code + ', signal=' + signal + ')');
        watchers = watchers.filter((w) => w.child !== child);
        if (desiredState() === 'running') setTimeout(startWatchers, 5000);
      });
      watchers.push({ name: spec.name, child });
    } catch (e) {
      log(spec.name + ' не поднялся: ' + e.message);
    }
  }
}

function stopWatchers() {
  for (const w of watchers) { try { w.child.kill(); } catch {} }
  watchers = [];
  log('сторожа сети и сессий остановлены');
}

// Перезапуск ПАНЕЛИ и ЛЕЧАЩЕГО без остановки сторожей.
// Нужен сетевому сторожу: когда он меняет прокси, старый процесс новых
// переменных окружения не наследует — контур надо переподнять. Самого
// сетевого сторожа останавливать нельзя: он на середине восстановления.
function restartContour() {
  if (panel) { try { panel.kill(); } catch {} panel = null; }
  stopHealer();
  setState('running');
  log('перезапуск контура (панель + лечащий) по команде сторожа');
  setTimeout(startPanel, 1200);
  return { ok: true };
}

function restartPanel() {
  setState('stopped');
  if (panel) { try { panel.kill(); } catch {} panel = null; }
  setState('running');
  log('перезапуск панели по команде владельца');
  setTimeout(startPanel, 1200);
  return { ok: true };
}

// ────────────────────────────── страница управления ──────────────────────────────

const HTML = `<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>МиниГен — управление</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#14141a;color:#e6e6e6;margin:0;padding:28px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8b8b96;margin-bottom:20px;max-width:760px}
 .card{max-width:760px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;padding:16px}
 button{padding:8px 14px;border:1px solid #3a3a46;background:#2a2a33;color:#e6e6e6;border-radius:6px;cursor:pointer;margin:0 8px 8px 0;font-size:13px}
 button:hover{background:#3a3a46} button.p{background:#1f4d2b;border-color:#2d6b3f} button.w{background:#4d421f;border-color:#6b5f2d} button.r{background:#4d1f1f;border-color:#6b2d2d}
 .st{color:#8b8b96;margin-bottom:14px} .ok{color:#6ee7a0}
 code{background:#14141a;padding:2px 6px;border-radius:4px}
 a{color:#8ab4f8}
</style></head><body>
<h1>МиниГен — управление</h1>
<div class="sub">Эта страница работает всегда, даже когда сама панель остановлена или упала. Кнопки: остановить, запустить, перезапустить. «Остановлена» переживает перезагрузку компьютера — вернуть можно этой же страницей.</div>
<div class="card">
  <div class="st" id="st">состояние: …</div>
  <button class="p" onclick="act('start')">Запустить панель</button>
  <button class="w" onclick="act('stop')">Остановить панель</button>
  <button class="r" onclick="act('restart')">Перезапустить панель</button>
  <div class="st" id="out"></div>
  <div class="st">Панель наблюдения: <a href="http://127.0.0.1:8099" target="_blank">http://127.0.0.1:8099</a></div>
</div>
<script>
async function act(what){
  const out=document.getElementById('out');
  out.textContent='делаю: '+what+'…';
  try{
    const r=await (await fetch('/'+what)).json();
    out.innerHTML=(r.ok?'<span class="ok">сделано</span>':'не вышло: '+(r.error||''))+'<br>панель: pid '+(r.pid||'—')+', состояние '+r.state;
    setTimeout(refresh,1500);
  }catch(e){ out.textContent='супервизор не отвечает'; }
}
async function refresh(){
  try{
    const r=await (await fetch('/status')).json();
    document.getElementById('st').innerHTML='состояние: <b>'+r.state+'</b> · панель '+(r.running?'<span class="ok">работает</span> (pid '+r.pid+')':'остановлена')+' · супервизор: pid '+r.supervisor+
      '<br>сторожа: '+((r.watchers&&r.watchers.length)?r.watchers.map(w=>w.name+' (pid '+w.pid+')').join(', '):'нет');
  }catch(e){ document.getElementById('st').textContent='супервизор не отвечает'; }
}
refresh(); setInterval(refresh,5000);
</script></body></html>`;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${CTRL_PORT}`);
  const p = url.pathname;
  const json = (code, obj) => {
    res.writeHead(code, {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',   // страница панели живёт на 8099
    });
    res.end(JSON.stringify(obj));
  };
  const status = () => ({
    running: panelAlive(),
    state: desiredState(),
    pid: panel ? panel.pid : null,
    supervisor: process.pid,
    watchers: watchers.filter(watcherAlive).map((w) => ({ name: w.name, pid: w.child.pid })),
  });

  if (p === '/' || p === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(HTML);
  }
  if (p === '/status') return json(200, status());
  if (p === '/start') { const r = startPanel(); return json(200, Object.assign(r, status())); }
  if (p === '/stop') { const r = stopPanel(); return json(200, Object.assign(r, status())); }
  if (p === '/restart') { const r = restartPanel(); return json(200, Object.assign(r, status())); }
  // Перезапуск контура (панель + лечащий) без остановки сторожей — зовёт
  // сетевой сторож после смены прокси (старый процесс не наследует новые
  // переменные окружения).
  if (p === '/restart-contour') { const r = restartContour(); return json(200, Object.assign(r, status())); }
  res.writeHead(404);
  res.end('not found');
});

// ────────────────────────────── старт ──────────────────────────────

ensureDir();
log('супервизор стартует, pid ' + process.pid);

// Экземплярность — по самому порту, а не по pid в файле: pid в Windows
// переиспользуется, и старый lock-файл мог «выдать» чужой процесс (G633).
function probeControl() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: CTRL_PORT, path: '/status', timeout: 1500 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

probeControl().then((alive) => {
  if (alive) {
    log('на порту ' + CTRL_PORT + ' уже отвечает другой супервизор — выхожу');
    process.exit(0);
  }
  writeLock();
  process.on('exit', () => { try { fs.unlinkSync(LOCK_FILE); } catch {} });
  server.listen(CTRL_PORT, '127.0.0.1', () => {
    log('супервизор поднят, порт ' + CTRL_PORT + ', pid ' + process.pid);
    if (desiredState() === 'running') startPanel();
    else log('состояние «stopped» — панель не поднимаю (владелец остановил её)');
  });
});

server.on('error', (e) => {
  log('ошибка сервера управления: ' + (e && e.message ? e.message : e));
  process.exit(1);
});
