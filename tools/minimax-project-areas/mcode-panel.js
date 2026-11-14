// Панель MiniMax Code — области проектов: живой вход, очки, смена аккаунта.
//
// Что умеет (2026-11-14):
//   * показывает области p-<проект> с ЖИВЫМ статусом (панель MiniMax, не файл:
//     auth-state.json врёт — у мёртвого токена написано "authenticated");
//   * «Новый аккаунт» — берёт пару токенов у КАРМАНА и сажает её в область
//     (ключ записи пересчитывается под путь области), область перезапускается;
//   * «Перенести из…» — переставить аккаунт из другой области (одна копия!);
//   * ТИХИЙ РЕМОНТ: запущенная область без живого входа чинится сама
//     (владелец нажал «Выйти из аккаунта» — дальше без него);
//   * незапущенные области НЕ трогаются вообще.
//
// Факты, на которых стоит механика:
//   * ключ записи токена = base64url(sha256(<область>/auth + "\0" + "mcode-public"));
//   * refresh-токен MiniMax одноразовый → две копии авторизации ломают друг друга;
//   * остаток очков MiniMax по API НЕ отдаётся → «кончились очки» ловится
//     либо по кнопке, либо по ручному «Выйти из аккаунта».
//
// Запуск: node mcode-panel.js [порт]
// Логика — в minimax-switch.py (единственная реализация механики).

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, execFile } = require('child_process');

const PORT = parseInt(process.argv[2] || '8099', 10);
const HOME = process.env.USERPROFILE;
const BIN = path.join(HOME, 'bin');
const TOOL = path.join(BIN, 'minimax-switch.py');
const PROJECTS_FILE = path.join(BIN, 'mcode-project-areas.json');
const AREAS_ROOT = path.join(HOME, '.minimax-areas');
const MCODE = path.join(HOME, 'AppData', 'Roaming', 'npm', 'mcode.cmd');
const JOURNAL_DIR = path.join(process.env.LOCALAPPDATA || path.join(HOME, 'AppData', 'Local'), 'mcode-web');
const JOURNAL_FILE = path.join(JOURNAL_DIR, 'journal.log');

const AUTO = { enabled: true, last: 0, busy: false };
const journal = [];
const stateCache = { ts: 0, data: null };
const STATE_TTL_MS = 45000;   // опрос панели MiniMax небыстрый — держим кэш

// ────────────────────────────── утилиты ──────────────────────────────

function readJson(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; } }

function log(line) {
  const t = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const entry = t + ' — ' + line;
  journal.unshift(entry);
  if (journal.length > 60) journal.pop();
  try {
    fs.mkdirSync(JOURNAL_DIR, { recursive: true });
    fs.appendFileSync(JOURNAL_FILE, entry + '\n');
  } catch {}
  console.log(entry);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { windowsHide: true, ...opts });
    let out = '';
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { out += d.toString(); });
    child.on('error', (e) => resolve({ code: -1, out: String(e) }));
    child.on('close', (code) => resolve({ code, out }));
  });
}

function tool(args) { return run('python', [TOOL, ...args]); }

// ────────────────────────────── состояние ──────────────────────────────

function projectAreas() {
  const cfg = readJson(PROJECTS_FILE);
  if (!cfg || !Array.isArray(cfg.projects)) return [];
  return cfg.projects
    .map((p) => ({
      slug: p.slug,
      title: p.title || p.slug,
      folder: p.folder,
      dir: path.join(AREAS_ROOT, 'p-' + p.slug),
    }))
    .filter((a) => fs.existsSync(path.join(a.dir, 'auth')));
}

async function liveState(force) {
  if (!force && stateCache.data && Date.now() - stateCache.ts < STATE_TTL_MS) return stateCache.data;
  const r = await tool(['--json', 'areas']);
  let areas = [];
  try { areas = JSON.parse(r.out || '[]'); } catch { areas = []; }
  const meta = new Map(projectAreas().map((a) => [a.dir, a]));
  const list = areas.map((a) => {
    const m = meta.get(path.join(AREAS_ROOT, a.area));
    return Object.assign(a, { title: m ? m.title : a.slug, folder: m ? m.folder : '', slug: a.slug || a.area.replace(/^p-/, '') });
  }).filter((a) => meta.has(path.join(AREAS_ROOT, a.area)));
  stateCache.ts = Date.now();
  stateCache.data = list;
  return list;
}

// Живые аккаунты — это те же области (второй опрос не нужен: медленно и лишний трафик).
async function accounts() {
  const list = await liveState(false);
  return list.filter((a) => a.live).map((a) => Object.assign({}, a, { source: a.area }));
}

// ────────────────────────────── действия ──────────────────────────────

function areaDir(slug) { return path.join(AREAS_ROOT, 'p-' + slug); }

async function restartArea(slug) {
  const stop = await run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    path.join(BIN, 'mcode-project-stop.ps1'), '-Project', slug]);
  await new Promise((r) => setTimeout(r, 1500));
  const start = await run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    path.join(BIN, 'mcode-project-start.ps1'), '-Project', slug]);
  return { stop: (stop.out || '').trim().split('\n').slice(-1)[0] || '', start: (start.out || '').trim().split('\n').slice(-1)[0] || '' };
}

// Поставить в область новую пару из Кармана и перезапустить область.
async function newAccount(slug, tier) {
  const args = ['pull', 'p-' + slug, '--apply', '--report'];
  if (tier) args.push('--tier', tier);
  const r = await tool(args);
  const ok = !/готовых пар нет|no_tokens_ready|не удалось|ERROR/i.test(r.out);
  if (!ok) {
    log('p-' + slug + ': новый аккаунт не получен — ' + (r.out || '').trim().split('\n').slice(-1)[0]);
    return { ok: false, out: r.out };
  }
  log('p-' + slug + ': пара от КАРМАНА посажена');
  const rs = await restartArea(slug);
  log('p-' + slug + ': область перезапущена (' + (rs.start || 'ок') + ')');
  return { ok: true, out: r.out };
}

// Переставить аккаунт из другой области (одна копия авторизации!)
async function swapAccount(from, to) {
  const r = await tool(['move', 'p-' + from, 'p-' + to, '--apply']);
  const ok = /записано в/i.test(r.out);
  log(ok ? ('аккаунт перенесён: ' + from + ' → ' + to) : ('перенос не удался: ' + (r.out || '').trim().split('\n').slice(-1)[0]));
  if (ok) await restartArea(to);
  return { ok, out: r.out };
}

// Тихий ремонт: только ЗАПУЩЕННЫЕ области без живого входа.
async function autoRepair() {
  if (AUTO.busy) return;
  AUTO.busy = true;
  try {
    const list = await liveState(true);
    const broken = list.filter((a) => a.running && !a.live);
    for (const a of broken) {
      log('p-' + a.slug + ': запущена, но входа нет — чиню молча');
      await newAccount(a.slug);
    }
  } finally {
    AUTO.busy = false;
    AUTO.last = Date.now();
  }
}

setInterval(() => {
  if (AUTO.enabled && Date.now() - AUTO.last > 45000) autoRepair().catch(() => {});
}, 20000);

// ────────────────────────────── интерфейс ──────────────────────────────

const HTML = `<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>MiniMax — области проектов</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#14141a;color:#e6e6e6;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8b8b96;margin-bottom:18px}
 table{border-collapse:collapse;width:100%;max-width:1200px;background:#1c1c22;border-radius:8px;overflow:hidden;table-layout:auto}
 th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #2a2a33;vertical-align:top}
 th{background:#232329;color:#a0a0ac;font-weight:600;font-size:12px;text-transform:uppercase;white-space:nowrap}
 .st{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
 .on{background:#1f4d2b;color:#6ee7a0} .off{background:#4d1f1f;color:#ff9a9a} .pend{background:#4d421f;color:#ffd479} .none{background:#33333d;color:#9a9aa8}
 button{padding:4px 8px;border:1px solid #3a3a46;background:#2a2a33;color:#e6e6e6;border-radius:6px;cursor:pointer;margin:0 4px 4px 0;font-size:12px;white-space:nowrap}
 button:hover{background:#3a3a46} button.p{background:#1f4d2b;border-color:#2d6b3f} button.w{background:#4d421f;border-color:#6b5f2d}
 select{padding:3px 5px;border-radius:6px;border:1px solid #3a3a46;background:#2a2a33;color:#e6e6e6;font-size:12px;max-width:130px}
 code{background:#14141a;padding:2px 6px;border-radius:4px;font-size:12px;word-break:break-all}
 .path{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px}
 .pts{white-space:nowrap;font-size:12px}
 .box{max-width:1200px;margin-top:16px;padding:14px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;display:none}
 .box.show{display:block}
 .jrn{max-width:1200px;margin-top:16px;padding:12px 14px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;font-size:12.5px;color:#c9c9d2}
 .jrn div{padding:2px 0;border-bottom:1px solid #232329}
 .muted{color:#8b8b96}
 .row{display:flex;align-items:center;gap:12px;margin:10px 0}
 .sw{width:44px;height:22px;border-radius:11px;background:#33333d;position:relative;cursor:pointer;border:1px solid #3a3a46}
 .sw.on{background:#1f4d2b;border-color:#2d6b3f}
 .sw:after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#9a9aa8;transition:.15s}
 .sw.on:after{left:22px;background:#6ee7a0}
</style></head><body>
<h1>MiniMax Code — области проектов</h1>
<div class="sub">Каждая область — отдельная копия приложения: свой проект, свой вход, свои сессии. Статус берётся живым запросом к панели MiniMax.</div>
<div class="row">
  <div class="sw" id="auto" onclick="toggleAuto()"></div>
  <div><b>Тихий ремонт</b> <span class="muted">— запущенная область без входа чинится сама (незапущенные не трогаем)</span></div>
  <button onclick="repairAll()">Починить все сломанные</button>
  <button onclick="load()">Обновить</button>
</div>
<table><thead><tr>
<th>Область</th><th>Проект</th><th>Вход</th><th>Очки</th><th>Запущена</th><th>Действия</th>
</tr></thead><tbody id="tb"></tbody></table>
<div class="box" id="box"></div>
<div class="jrn" id="jrn"></div>
<script>
const tb=document.getElementById('tb'), box=document.getElementById('box');
let ACC=[];
function st(a){
  if(a.live) return ['жив','on'];
  if(a.state==='нет входа') return ['не вошёл','none'];
  if(a.state==='токен не отвечает') return ['токен мёртв','off'];
  return [a.state||'?','none'];
}
async function load(){
  const d=await (await fetch('/api/state')).json();
  ACC=d.accounts||[];
  document.getElementById('auto').className='sw'+(d.auto?' on':'');
  tb.innerHTML='';
  for(const a of d.areas){
    const [label,cls]=st(a);
    const opts=ACC.filter(x=>x.area!==a.area).map(x=>'<option value="'+x.area+'">'+x.area+(x.email?' ('+x.email+')':'')+'</option>').join('');
    const tr=document.createElement('tr');
    tr.innerHTML='<td><code>'+a.area+'</code></td>'+
      '<td>'+a.title+'<div class="path muted" title="'+(a.folder||'')+'">'+(a.folder||'')+'</div></td>'+
      '<td><span class="st '+cls+'">'+label+'</span>'+(a.email?'<div class="muted" style="font-size:11.5px">'+a.email+'</div>':'')+'</td>'+
      '<td class="pts">'+(a.live?('<b>'+(a.points_today||'-')+'</b> очков<div class="muted">кл. '+a.day_no+' · '+(a.claimed_today?'забрано':'след. '+(a.next_points||'-'))+'</div>'):'<span class="muted">—</span>')+'</td>'+
      '<td>'+(a.running?'<span class="st on">да</span>':'<span class="st none">нет</span>')+'</td>'+
      '<td>'+
        '<button class="p" onclick="newAcc(\\''+a.slug+'\\')">Новый аккаунт</button>'+
        (opts?'<select id="src_'+a.slug+'">'+opts+'</select><button onclick="swap(\\''+a.slug+'\\')">Перенести</button>':'')+
        '<button onclick="toggleArea(\\''+a.slug+'\\','+(a.running?'stop':'start')+'\\')">'+(a.running?'Остановить':'Запустить')+'</button>'+
      '</td>';
    tb.appendChild(tr);
  }
  const j=document.getElementById('jrn');
  j.innerHTML='<b>Журнал</b><br>'+(d.journal&&d.journal.length?d.journal.slice(0,14).map(x=>'<div>'+x+'</div>').join(''):'<div class="muted">пусто</div>');
}
async function newAcc(slug){
  box.classList.add('show');
  box.innerHTML='<b>'+slug+':</b> беру пару у КАРМАНА и перезапускаю область…';
  const r=await (await fetch('/api/repair?area='+slug)).json();
  box.innerHTML='<b>'+slug+':</b> '+(r.ok?'готово — область перезапущена, сессия на месте.':'не вышло: '+(r.error||''))+
    '<br><span class="muted">'+(r.out||'').replace(/\\n/g,'<br>')+'</span>';
  load();
}
async function swap(to){
  const src=document.getElementById('src_'+to).value;
  box.classList.add('show');
  box.innerHTML='<b>Перенос '+src+' → '+to+':</b> …';
  const r=await (await fetch('/api/swap?from='+src+'&to='+to)).json();
  box.innerHTML='<b>Перенос '+src+' → '+to+':</b> '+(r.ok?'готово.':'не вышло: '+(r.error||''));
  load();
}
async function toggleArea(slug, what){
  box.classList.add('show');
  box.innerHTML='<b>'+slug+':</b> '+what+'…';
  await fetch('/api/'+what+'?area='+slug);
  setTimeout(load,2500);
}
async function toggleAuto(){
  await fetch('/api/auto');
  load();
}
async function repairAll(){
  box.classList.add('show');
  box.innerHTML='<b>Ремонт:</b> чиню запущенные области без входа…';
  const r=await (await fetch('/api/repair')).json();
  box.innerHTML='<b>Ремонт:</b> '+(r.ok?'сделано.':'сделано частично: '+(r.error||''));
  load();
}
load(); setInterval(load,5000);
</script></body></html>`;

// ────────────────────────────── сервер ──────────────────────────────

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;
  const q = url.searchParams;
  const json = (code, obj) => {
    res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(obj));
  };

  if (p === '/' || p === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(HTML);
  }

  if (p === '/api/state') {
    const list = await liveState(false);
    const acc = await accounts();
    return json(200, { areas: list, accounts: acc, auto: AUTO.enabled, journal });
  }

  if (p === '/api/repair') {
    const slug = q.get('area');
    if (slug) {
      const r = await newAccount(slug, q.get('tier') || '');
      return json(200, { ok: r.ok, out: r.out });
    }
    const list = await liveState(true);
    const broken = list.filter((a) => a.running && !a.live);
    for (const a of broken) await newAccount(a.slug);
    return json(200, { ok: true, out: 'обработано: ' + (broken.map((a) => a.area).join(', ') || 'нечего') });
  }

  if (p === '/api/swap') {
    const from = q.get('from'), to = q.get('to');
    if (!from || !to) return json(400, { error: 'нужно from и to' });
    const r = await swapAccount(from, to);
    return json(200, { ok: r.ok, out: r.out });
  }

  if (p === '/api/auto') {
    AUTO.enabled = !AUTO.enabled;
    log('тихий ремонт ' + (AUTO.enabled ? 'включён' : 'выключен'));
    return json(200, { ok: true, auto: AUTO.enabled });
  }

  if (p === '/api/start' || p === '/api/stop') {
    const slug = q.get('area');
    if (!slug) return json(400, { error: 'нужен area' });
    const script = path.join(BIN, p === '/api/start' ? 'mcode-project-start.ps1' : 'mcode-project-stop.ps1');
    const r = await run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, '-Project', slug]);
    log(slug + ': ' + (p === '/api/start' ? 'запуск' : 'остановка'));
    return json(200, { ok: true, out: r.out });
  }

  if (p === '/api/login') {
    // ручной вход (device-flow) для аккаунта, которого ещё нет в пуле
    const slug = q.get('area');
    if (!slug) return json(400, { error: 'нужен area' });
    const dir = areaDir(slug);
    const env = {
      ...process.env,
      MCODE_CONFIG_DIR: path.join(dir, 'config'),
      MINIMAX_DATA_DIR: dir, MAVIS_DATA_DIR: dir,
      MCODE_LOG_FILE: path.join(dir, 'logs', 'mcode.log'),
      HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '', http_proxy: '', https_proxy: '', all_proxy: '',
    };
    const child = spawn('cmd.exe', ['/c', MCODE, 'login', '--region', 'global', '--no-browser'], { env, windowsHide: true });
    let buf = '';
    const done = (url, code) => {
      if (done.done) return; done.done = true;
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ url, code }));
    };
    child.stdout.on('data', (d) => { buf += d.toString(); const m = buf.match(/Open:\s*(\S+)/); if (m) done(m[1], (buf.match(/Code:\s*(\S+)/) || [])[1] || null); });
    child.stderr.on('data', (d) => { buf += d.toString(); const m = buf.match(/Open:\s*(\S+)/); if (m) done(m[1], (buf.match(/Code:\s*(\S+)/) || [])[1] || null); });
    child.on('close', () => done(null, null));
    return undefined;
  }

  res.writeHead(404);
  res.end('not found');
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('Панель MiniMax (области проектов): http://127.0.0.1:' + PORT);
});
