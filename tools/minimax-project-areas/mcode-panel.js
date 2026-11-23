// Панель MiniMax Code — области проектов: живой вход, очки, смена аккаунта.
//
// Что умеет (2026-11-16):
//   * показывает области p-<проект> с ЖИВЫМ статусом (панель MiniMax, не файл:
//     auth-state.json врёт — у мёртвого токена написано "authenticated");
//   * «Новый вход (токен)» — берёт пару токенов у КАРМАНА и сажает её в область
//     (ключ записи пересчитывается под путь области), область перезапускается;
//     ЛЕЧЕНИЕ РУЧНОЕ (решение владельца 15.11): автоматика убрана — она брала
//     у Кармана аккаунт за аккаунтом, пока область не оживёт;
//   * «Перенести из…» — переставить аккаунт из другой области (одна копия!);
//   * кнопки самой панели: Запустить / Остановить / Перезапустить — идут в
//     супервизор (порт 8098), который держит панель и поднимает её заново;
//   * страница управления супервизором: http://127.0.0.1:8098 — работает, даже
//     когда панель остановлена;
//   * незапущенные области НЕ трогаются вообще.
//
// Факты, на которых стоит механика:
//   * ключ записи токена = base64url(sha256(<область>/auth + "\0" + "mcode-public"));
//   * refresh-токен MiniMax одноразовый → две копии авторизации ломают друг друга;
//   * остаток очков MiniMax по API НЕ отдаётся → «кончились очки» ловится
//     либо по кнопке, либо по ручному «Выйти из аккаунта».
//
// Запуск: node mcode-panel.js [порт]   (в норме — через супервизор, тихо)
// Логика — в minimax-switch.py (единственная реализация механики).

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, execFile } = require('child_process');

const PORT = parseInt(process.argv[2] || '8099', 10);
const HOME = process.env.USERPROFILE;
const BIN = path.join(HOME, 'bin');
const TOOL = path.join(BIN, 'minimax-switch.py');
const HEALER = path.join(BIN, 'minimax-autheal.py');
const NETWATCH = path.join(BIN, 'minimax-netwatch.py');
const SESSIONWATCH = path.join(BIN, 'minimax-sessionwatch.py');
const PROJECTS_FILE = path.join(BIN, 'mcode-project-areas.json');
const AREAS_ROOT = path.join(HOME, '.minimax-areas');
const JOURNAL_DIR = path.join(process.env.LOCALAPPDATA || path.join(HOME, 'AppData', 'Local'), 'mcode-web');
const JOURNAL_FILE = path.join(JOURNAL_DIR, 'journal.log');
const HEAL_STATE_FILE = path.join(JOURNAL_DIR, 'autheal-state.json');
// Состояния сторожей (21.11.2026): сеть и сессии. Пишут их демоны,
// панель только читает — источник правды один.
const NET_STATE_FILE = path.join(JOURNAL_DIR, 'netwatch-state.json');
const SESSION_STATE_FILE = path.join(JOURNAL_DIR, 'sessionwatch-state.json');

// Расход пула лечащим: сколько аккаунтов область забрала за сутки и до какого
// времени она на паузе. Читаем файл состояния лечащего (он и пишет туда).
function healState() {
  try { return JSON.parse(fs.readFileSync(HEAL_STATE_FILE, 'utf8')); } catch { return {}; }
}
function pullsToday(state, slug) {
  const pulls = (state && state.pulls && state.pulls[slug]) || [];
  const dayAgo = Date.now() - 86400000;
  return pulls.filter((t) => Number(t) * 1000 > dayAgo).length;
}
function backoffUntil(state, slug) {
  const bo = (state && state.backoff && state.backoff[slug]) || {};
  return Number(bo.until || 0) * 1000;
}

// Авто-лечение вернулось 17.11 (D-225), но живёт отдельным демоном под
// супервизором (minimax-autheal.py) — панель только показывает состояние и
// даёт ручной путь «Новый вход (токен)». Раньше автоматика жила здесь и брала
// у Кармана новый аккаунт каждые 45 секунд, пока область не оживёт, — пул
// оборачивался зря (G636).
const AUTO = { reported: 0 };
const journal = [];
const stateCache = { ts: 0, data: null };
const STATE_TTL_MS = 45000;   // опрос панели MiniMax небыстрый — держим кэш

// Журнал подхватываем с диска: после перезапуска панели он иначе пустой
// (в памяти — только то, что написано при текущем процессе).
try {
  const raw = fs.readFileSync(JOURNAL_FILE, 'utf8').split('\n').filter(Boolean);
  for (const line of raw.slice(-60).reverse()) journal.push(line);
} catch {}

// ────────────────────────────── утилиты ──────────────────────────────

function readJson(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; } }

function log(line) {
  // Время МЕСТНОЕ, как у лечащего: тот пишет в тот же файл и по местному,
  // а UTC в одном журнале смотрелся как два разных часа (18.11).
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  const t = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
            ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  const entry = t + ' — ' + line;
  journal.unshift(entry);
  if (journal.length > 60) journal.pop();
  try {
    fs.mkdirSync(JOURNAL_DIR, { recursive: true });
    fs.appendFileSync(JOURNAL_FILE, entry + '\n');
  } catch {}
  console.log(entry);
}

// Журнал — с диска и СВЕЖИМ: лечащий пишет в тот же файл, а его строки
// иначе видны только после перезапуска панели (18.11: панель показывала
// «вчерашние» строки, пока лечащий вовсю писал новые).
function readJournal(limit) {
  const n = limit || 60;
  try {
    const raw = fs.readFileSync(JOURNAL_FILE, 'utf8').split('\n').filter(Boolean);
    return raw.slice(-n).reverse();          // файл хронологический — свежие первыми
  } catch {
    return journal.slice(0, n);
  }
}

// PowerShell печатает в кодировке консоли (cp1251), а панель читает UTF-8 —
// в результатах было «◆◆◆» (тот же класс, что был с инструментом).
// Имена параметров передаём как есть (иначе $Project получает '-Project'),
// значения — в кавычках.
function runPS(script, args) {
  const quoted = [script].concat(args).map((a, i) => {
    if (i === 0) return "'" + String(a).replace(/'/g, "''") + "'";
    const s = String(a);
    return s.startsWith('-') ? s : "'" + s.replace(/'/g, "''") + "'";
  }).join(' ');
  const cmd = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; & " + quoted;
  return run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', cmd], { timeout: 120000 });
}

// Добить всё, что осталось от десктопа области: окно могло зависнуть.
// Ищем по точному пути каталога области (--user-data-dir=...\p-<slug>\electron),
// чтобы не задеть соседние области и основной десктоп.
async function killLeftovers(slug) {
  const dir = path.join(AREAS_ROOT, 'p-' + slug, 'electron');
  const ps = `$ErrorActionPreference='SilentlyContinue';` +
    `$p=Get-CimInstance Win32_Process -Filter "Name='MiniMax Code.exe'" | Where-Object { $_.CommandLine -like '*${dir}*' };` +
    `$n=0; foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue; $n++ };` +
    `Start-Sleep -Milliseconds 700;` +
    `$rest=Get-CimInstance Win32_Process -Filter "Name='MiniMax Code.exe'" | Where-Object { $_.CommandLine -like '*${dir}*' };` +
    `Write-Output "$n|$($rest.Count)"`;
  const r = await run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + ps]);
  const m = /(\d+)\|(\d+)/.exec(r.out || '');
  if (m) return Number(m[2]);
  return 0;
}

function run(cmd, args, opts = {}) {
  const { timeout = 120000, ...spawnOpts } = opts;
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { windowsHide: true, ...spawnOpts });
    let out = '';
    let settled = false;
    const finish = (code, extra) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, out: out + (extra || '') });
    };
    // Таймаут: опрос панели MiniMax бывает вязким, и без него панель «залипает»
    // — запрос /api/state не отвечает, страница выглядит повисшей.
    const timer = setTimeout(() => {
      try { child.kill(); } catch {}
      finish(-2, '\n[таймаут ' + Math.round(timeout / 1000) + ' с — команда не ответила]');
    }, timeout);
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { out += d.toString(); });
    child.on('error', (e) => finish(-1, String(e)));
    child.on('close', (code) => finish(code));
  });
}

// Python печатает в UTF-8 (сам инструмент перенастроил вывод): без этого Windows
// отдаёт cp1251, панель читает байты как UTF-8 → кракозябры в статусах.
function tool(args) {
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
  // Один вызов инструмента за раз: два параллельных опроса MiniMax мешают друг
  // другу (лимиты вендора, взаимные блокировки) — из-за этого панель «глючила».
  const next = toolChain.then(() => run('python', [TOOL, ...args], { env, timeout: 150000 }));
  toolChain = next.then(() => {}, () => {});
  return next;
}

// Лечащий отдельным вызовом: «Новый вход (токен)» должен делать ровно то же,
// что и автоматическое лечение (заказ владельца 18.11) — поэтому зовём его
// общий путь `--area`, а не копируем механику в панель.
function healer(args) {
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
  const next = toolChain.then(() => run('python', [HEALER, ...args], { env, timeout: 180000 }));
  toolChain = next.then(() => {}, () => {});
  return next;
}
let toolChain = Promise.resolve();

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
      autoheal: !!p.autoheal,
    }))
    .filter((a) => fs.existsSync(path.join(a.dir, 'auth')));
}

// ── Переключатель «Автомат / Вручную» ──────────────────────────────────────
// Режим обслуживания области лежит в конфиге (поле `autoheal`): true —
// отказ по очкам лечится сам (лечащий под супервизором), false — вход меняет
// владелец руками, кнопкой «Новый вход (токен)». Лечащий перечитывает конфиг
// на каждом проходе (раз в 3 мин) — переключатель действует без перезапуска.
function renderConfig(cfg) {
  const lines = ['{'];
  const keys = Object.keys(cfg).filter((k) => k !== 'projects');
  keys.forEach((k) => {
    // Запятая после КАЖДОГО ключа: следом всегда идёт "projects" —
    // без запятой получается невалидный JSON (файл никто бы не прочитал).
    lines.push('  ' + JSON.stringify(k) + ': ' + JSON.stringify(cfg[k]) + ',');
  });
  lines.push('  "projects": [');
  cfg.projects.forEach((p, i) => {
    const flat = Object.keys(p).map((k) => JSON.stringify(k) + ': ' + JSON.stringify(p[k])).join(', ');
    lines.push('    {' + flat + '}' + (i < cfg.projects.length - 1 ? ',' : ''));
  });
  lines.push('  ]');
  lines.push('}');
  return lines.join('\n') + '\n';
}

function setMode(slug, mode) {
  const cfg = readJson(PROJECTS_FILE);
  if (!cfg || !Array.isArray(cfg.projects)) return { ok: false, error: 'конфиг не читается' };
  const proj = cfg.projects.find((p) => p.slug === slug);
  if (!proj) return { ok: false, error: 'нет области ' + slug };
  const want = mode === 'manual' ? false : true;
  if (!!proj.autoheal === want) return { ok: true, autoheal: want, out: 'уже так' };
  proj.autoheal = want;
  try {
    fs.copyFileSync(PROJECTS_FILE, PROJECTS_FILE + '.bak-mode');
    // Запись АТОМАРНАЯ: сначала во временный файл рядом, потом переименование.
    // Прямая запись обрывает файл на середине, и читатель (демон, новая
    // программа) ловит обрывок JSON: в журнале это выглядит как
    // «конфиг … не читается (Expecting ',' delimiter)» — 23.11, дважды.
    const tmp = PROJECTS_FILE + '.tmp';
    fs.writeFileSync(tmp, renderConfig(cfg), 'utf8');
    fs.renameSync(tmp, PROJECTS_FILE);
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
  log(slug + ': режим обслуживания — ' + (want ? 'АВТОМАТ (лечится сам)' : 'ВРУЧНУЮ (вход меняет владелец)'));
  return { ok: true, autoheal: want, out: 'записано' };
}

// Один опрос за раз: параллельные вызовы плодили процессы и тормозили опрос MiniMax.
let stateFlight = null;

async function liveState(force) {
  if (!force && stateCache.data && Date.now() - stateCache.ts < STATE_TTL_MS) return stateCache.data;
  if (stateFlight) return stateFlight;
  stateFlight = (async () => {
    const r = await tool(['--json', 'areas']);
    let areas = [];
    try { areas = JSON.parse(r.out || '[]'); } catch { areas = []; }
    if (r.code === -2) log('опрос областей не ответил (таймаут) — показываю прошлые данные');
    const meta = new Map(projectAreas().map((a) => [a.dir, a]));
    const hstate = healState();
    const list = areas.map((a) => {
      const m = meta.get(path.join(AREAS_ROOT, a.area));
      const slug = a.slug || a.area.replace(/^p-/, '');
      return Object.assign(a, {
        title: m ? m.title : a.slug,
        folder: m ? m.folder : '',
        slug: slug,
        autoheal: m ? !!m.autoheal : false,
        pulls_today: pullsToday(hstate, slug),
        backoff_until: backoffUntil(hstate, slug),
      });
    }).filter((a) => meta.has(path.join(AREAS_ROOT, a.area)));
    stateCache.ts = Date.now();
    stateCache.data = list;
    return list;
  })();
  try { return await stateFlight; } finally { stateFlight = null; }
}

// Живые аккаунты — это те же области (второй опрос не нужен: медленно и лишний трафик).
async function accounts() {
  const list = await liveState(false);
  return list.filter((a) => a.live).map((a) => Object.assign({}, a, { source: a.area }));
}

// ────────────────────────────── действия ──────────────────────────────

function areaDir(slug) { return path.join(AREAS_ROOT, 'p-' + slug); }

// «Новый вход (токен)» — тот же путь, что и автоматическое лечение:
// новый аккаунт от Кармана, установка БЕЗ перезапуска десктопа, затем
// «Продолжай» — сначала оборванным субагентам, потом основному чату.
// Заказ владельца 18.11. Делает лечащий (--area), чтобы не было двух
// реализаций механики.
async function newAccount(slug) {
  const r = await healer(['--once', '--area', slug]);
  const out = (r.out || '').trim();
  const ok = r.code === 0 && !/лечение сорвалось|ждём, повторов/i.test(out);
  if (ok) log('p-' + slug + ': новый вход посажен (без перезапуска), «Продолжай» отправлен');
  else log('p-' + slug + ': новый вход не получен — ' + (out.split('\n').slice(-1)[0] || 'нет вывода'));
  return { ok: ok, out: out };
}

// Тихий ремонт удалён по решению владельца (15.11):
// вход владельца в браузере, не оживает от нового токена — а панель брала у
// Кармана аккаунт за аккаунтом. Теперь новый вход даёт владелец руками.

setInterval(() => {
  if (Date.now() - (AUTO.reported || 0) > 45000) reportActivations().catch(() => {});
}, 20000);

// Доложить Карману про активации, которые мы увидели на панели MiniMax:
// клетка дня и её номинальные очки (остаток MiniMax не отдаёт — мы его не знаем).
// Токены не отправляем: учёт и статистика — в зоне Кармана.
async function reportActivations() {
  AUTO.reported = Date.now();
  const r = await tool(['report']);
  for (const line of (r.out || '').split('\n')) {
    if (/клетка/.test(line)) log(line.trim());
  }
}

// ────────────────────────────── интерфейс ──────────────────────────────

const HTML = `<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>МиниГен — области проектов</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#14141a;color:#e6e6e6;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8b8b96;margin-bottom:18px}
 table{border-collapse:collapse;width:auto;max-width:none;background:#1c1c22;border-radius:8px;overflow:hidden;table-layout:fixed}
 th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #2a2a33;vertical-align:top}
 th{background:#232329;color:#a0a0ac;font-weight:600;font-size:12px;text-transform:uppercase;white-space:nowrap}
 .st{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
 .on{background:#1f4d2b;color:#6ee7a0} .off{background:#4d1f1f;color:#ff9a9a} .pend{background:#4d421f;color:#ffd479} .none{background:#33333d;color:#9a9aa8}
 button{padding:3px 7px;border:1px solid #3a3a46;background:#2a2a33;color:#e6e6e6;border-radius:6px;cursor:pointer;margin:0 4px 4px 0;font-size:11.5px;white-space:nowrap}
 button:hover{background:#3a3a46} button.p{background:#1f4d2b;border-color:#2d6b3f} button.w{background:#4d421f;border-color:#6b5f2d}
 select{padding:3px 5px;border-radius:6px;border:1px solid #3a3a46;background:#2a2a33;color:#e6e6e6;font-size:12px;max-width:130px}
 code{background:#14141a;padding:2px 6px;border-radius:4px;font-size:12px;word-break:normal;white-space:nowrap}
 .path{max-width:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px}
 .burn{color:#ff9a9a;font-size:11px;white-space:nowrap}
 .pts{white-space:nowrap;font-size:12px}
 .act{white-space:nowrap}
 .act button{margin:0 6px 0 0}
 .box{max-width:1200px;margin-top:16px;padding:14px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;display:none}
 .box.show{display:block}
 .jrn{max-width:1200px;margin-top:16px;padding:12px 14px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;font-size:12.5px;color:#c9c9d2}
 .jrn div{padding:2px 0;border-bottom:1px solid #232329}
 .muted{color:#8b8b96}
 .row{display:flex;align-items:center;gap:12px;margin:10px 0}
 .modal{position:fixed;inset:0;background:rgba(0,0,0,.65);display:none;align-items:center;justify-content:center;z-index:60}
 .modal.show{display:flex}
 .mbox{background:#2a1214;border:1px solid #ff9a9a;border-radius:10px;padding:22px 26px;max-width:560px;color:#ffd7d7;box-shadow:0 10px 40px rgba(0,0,0,.5)}
 .mbox h2{margin:0 0 10px;font-size:17px;color:#ff9a9a}
 .mbox .muted{color:#e7b9b9}
 .sw{display:inline-block;width:42px;height:22px;border-radius:11px;background:#3a3a46;border:1px solid #4c4c5a;position:relative;cursor:pointer;vertical-align:middle;transition:background .15s,border-color .15s}
 .sw.on{background:#1f4d2b;border-color:#2d6b3f}
 .sw:after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#9a9aa8;transition:left .15s,background .15s}
 .sw.on:after{left:22px;background:#6ee7a0}
 .sw:hover{filter:brightness(1.25)}
 .netbox{max-width:1200px;margin-top:16px;padding:14px;background:#1c1c22;border:1px solid #2a2a33;border-radius:8px;font-size:13px}
</style></head><body>
<h1>МиниГен</h1>
<div class="sub">Области проектов: каждая — отдельная копия приложения со своим проектом, своим входом и своими сессиями. Статус берётся живым запросом к панели MiniMax.</div>
<div class="row">
  <div><b>Лечится само</b> <span class="muted">— при отказе по очкам МиниГен берёт аккаунт у Кармана, активирует бонус, меняет вход в области и шлёт «Продолжай»</span></div>
  <button onclick="reloadPanel()">Перезагрузить панель</button>
  <a class="muted" style="font-size:12px" href="http://127.0.0.1:8098" target="_blank">страница управления</a>
</div>
<table>
<colgroup><col style="width:168px"><col style="width:96px"><col style="width:104px"><col style="width:424px"><col style="width:172px"><col style="width:136px"><col style="width:258px"></colgroup>
<thead><tr>
<th>Проект</th><th>Запущена</th><th>Авто/Ручной</th><th>Действия</th><th>Аккаунт</th><th>Очки</th><th>Область</th>
</tr></thead><tbody id="tb"></tbody></table>
<div class="netbox" id="netbox">
  <div class="row">
    <b>Сеть и сессии</b>
    <button class="p" onclick="checkNet()">Проверить сеть</button>
    <button onclick="checkSessions()">Проверить сессии</button>
    <span class="muted" id="netstamp"></span>
  </div>
  <div id="netbody" class="muted">загрузка…</div>
</div>
<div class="modal" id="alarm"><div class="mbox">
  <h2>Аларм МиниГена</h2>
  <div id="alarmText"></div>
  <div class="muted" style="font-size:12px;margin:10px 0 14px">Процесс активации остановлен автоматически — нужно разобраться, что происходит. Подробности — в журнале.</div>
  <button onclick="ackAlarm()">Понятно, разбираюсь</button>
</div></div>
<div class="box" id="box"></div>
<div class="jrn" id="jrn"></div>
<script>
const tb=document.getElementById('tb'), box=document.getElementById('box');
let MODES={};
function st(a){
  const s=a.state||'?';
  if(s==='жив') return ['жив','on'];
  if(s==='вход в процессе'||s==='код входа протух') return [s,'pend'];
  if(s==='вышел из аккаунта'||s==='токен не отвечает') return [s,'off'];
  return [s,'none'];
}
let ALARM=null;
function showAlarm(a){
  if(!a||!a.text) return;
  if(localStorage.getItem('minigen_alarm_'+a.at)) return;
  ALARM=a;
  document.getElementById('alarmText').textContent=a.text;
  document.getElementById('alarm').classList.add('show');
}
function ackAlarm(){
  if(ALARM) localStorage.setItem('minigen_alarm_'+ALARM.at,'1');
  document.getElementById('alarm').classList.remove('show');
}
async function load(){
  const d=await (await fetch('/api/state')).json();
  MODES={}; for(const a of d.areas) MODES[a.slug]=!!a.autoheal;
  showAlarm(d.alarm);
  tb.innerHTML='';
  // Порядок строк (заказ владельца 17.11): самые активные рабочие области — наверху,
  // незапущенные — внизу. Внутри группы: с живым входом выше, дальше по расходу
  // за сегодня; при равенстве порядок исходный (sort стабилен).
  const rows=d.areas.slice().sort(function(x,y){
    if(!!x.running!==!!y.running) return x.running?-1:1;
    if(!!x.live!==!!y.live) return x.live?-1:1;
    return (y.pulls_today||0)-(x.pulls_today||0);
  });
  for(const a of rows){
    const [label,cls]=st(a);
    const tr=document.createElement('tr');
    const burn=(a.pulls_today?'<div class="burn">сегодня '+a.pulls_today+' акк.</div>':'')+
      (a.backoff_until>Date.now()?'<div class="muted" style="font-size:11px">пауза до '+
        new Date(a.backoff_until).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})+'</div>':'');
    tr.innerHTML='<td style="white-space:nowrap">'+a.title+'</td>'+
      '<td>'+(a.running?'<span class="st on">да</span>':'<span class="st none">нет</span>')+'</td>'+
      '<td><span class="sw'+(a.autoheal?' on':'')+'" data-slug="'+a.slug+'" onclick="setMode(this.dataset.slug)" title="'+(a.autoheal?'АВТОМАТ — область лечится сама':'РУЧНОЙ — вход меняете вы')+'"></span></td>'+
      '<td class="act">'+
        '<button class="p" data-slug="'+a.slug+'" onclick="newAcc(this.dataset.slug)">Новый вход (токен)</button>'+
        '<button data-slug="'+a.slug+'" data-op="'+(a.running?'stop':'start')+'" onclick="toggleArea(this.dataset.slug,this.dataset.op)">'+(a.running?'Остановить десктоп':'Запустить десктоп')+'</button>'+
      '</td>'+
      '<td class="muted" style="font-size:11.5px">'+(a.email||'—')+'</td>'+
      '<td class="pts">'+(a.live?('<b>'+(a.points_today||'-')+'</b> очков<div class="muted">кл. '+a.day_no+' · '+(a.claimed_today?'забрано':'след. '+(a.next_points||'-'))+'</div>'):'<span class="muted">—</span>')+'</td>'+
      '<td style="white-space:nowrap"><code>'+a.area+'</code><div class="path muted" title="'+(a.folder||'')+'">'+(a.folder||'')+'</div>'+burn+'</td>';
    tb.appendChild(tr);
  }
  const j=document.getElementById('jrn');
  j.innerHTML='<b>Журнал</b><br>'+(d.journal&&d.journal.length?d.journal.slice(0,14).map(x=>'<div>'+x+'</div>').join(''):'<div class="muted">пусто</div>');
  renderNet(d);
}
function renderNet(d){
  const net=d.net||{}, sess=d.sessions||{};
  const nl=net.last_run||{}, sl=sess.last_run||{};
  let h='<b>Сеть:</b> ';
  if(!nl.at){
    h+='<span class="muted">сетевой сторож ещё не отработал</span>';
  }else{
    for(const p of (nl.probes||[])){
      h+='<span class="st '+(p.alive?'on':'off')+'">'+(p.name||'?')+'</span> '+
         ((p.country?('страна '+p.country+' · '):'')+(p.ip||''))+
         ' <span class="muted">'+(p.note||'')+'</span><br>';
    }
    h+='<span class="muted">действие: '+(nl.action||'-')+' · устойчивый доступ: '+(nl.stable?'да':'нет')+'</span>';
  }
  const areas=sl.areas||[];
  let s='<br><b>Сессии:</b> ';
  let any=false;
  for(const a of areas){
    const vs=(a.verdicts||[]).filter(function(v){return v.verdict!=='готова'&&v.verdict!=='проба';});
    if(!vs.length) continue;
    any=true;
    s+='<br><span class="muted">'+a.slug+':</span> '+vs.map(function(v){
      return v.session_id.slice(0,12)+' — '+v.verdict+(v.fill?(' ('+v.fill+'%)'):'');
    }).join('; ');
  }
  if(!any) s+='<span class="muted">незакрытых дел нет</span>';
  document.getElementById('netbody').innerHTML=h+s;
  document.getElementById('netstamp').textContent='проход сети: '+(nl.at?new Date(nl.at).toLocaleTimeString('ru-RU'):'—')+' · сессий: '+(sl.at?new Date(sl.at).toLocaleTimeString('ru-RU'):'—');
}
async function checkNet(){
  box.classList.add('show');
  box.innerHTML='<b>Сеть:</b> проверяю пути и вендора…';
  const r=await (await fetch('/api/net-check')).json();
  box.innerHTML='<b>Сеть:</b> '+(r.ok?'проверка прошла.':'не вышло: '+(r.error||''))+
    '<br><span class="muted">'+(r.out||'').replace(/\\n/g,'<br>').slice(-1200)+'</span>';
  load();
}
async function checkSessions(){
  box.classList.add('show');
  box.innerHTML='<b>Сессии:</b> смотрю, что с ними (сухой прогон — ничего не трогаю)…';
  const r=await (await fetch('/api/sessions-check')).json();
  box.innerHTML='<b>Сессии:</b> '+(r.ok?'разбор сделан.':'не вышло: '+(r.error||''))+
    '<br><span class="muted">'+(r.out||'').replace(/\\n/g,'<br>').slice(-1200)+'</span>';
  load();
}
async function newAcc(slug){
  box.classList.add('show');
  box.innerHTML='<b>'+slug+':</b> беру аккаунт у КАРМАНА, сажаю его без перезапуска десктопа и отправляю «Продолжай» (сначала оборванным субагентам, потом в основной чат)…';
  const r=await (await fetch('/api/repair?area='+slug)).json();
  box.innerHTML='<b>'+slug+':</b> '+(r.ok?'готово — вход обновлён без перезапуска, «Продолжай» отправлен.':'не вышло: '+(r.error||''))+
    '<br><span class="muted">'+(r.out||'').replace(/\\n/g,'<br>')+'</span>';
  load();
}
async function reloadPanel(){
  box.classList.add('show');
  box.innerHTML='<b>Панель:</b> сбрасываю состояние и пересканирую десктопы…';
  const r=await (await fetch('/api/reload')).json();
  box.innerHTML='<b>Панель перезагружена.</b> '+(r.ok?('Областей пересканировано: '+r.areas+'.'):('не вышло: '+(r.error||'')));
  load();
}
async function setMode(slug){
  const want=MODES[slug]?'manual':'auto';
  box.classList.add('show');
  box.innerHTML='<b>'+slug+':</b> переключаю режим…';
  const r=await (await fetch('/api/mode?area='+slug+'&mode='+want)).json();
  if(r.ok){
    MODES[slug]=!!r.autoheal;
    const on=MODES[slug];
    document.querySelectorAll('.sw[data-slug="'+slug+'"]').forEach(function(el){
      el.classList.toggle('on', on);
      el.title=on?'АВТОМАТ — область лечится сама':'РУЧНОЙ — вход меняете вы';
    });
  }
  box.innerHTML='<b>'+slug+':</b> '+(r.ok?(r.autoheal?'режим — АВТОМАТ (лечится сама).':'режим — ВРУЧНОЙ (вход меняете вы).'):'не вышло: '+(r.error||''))+
    '<br><span class="muted">Лечащий перечитывает режим на каждом проходе (раз в 3 мин).</span>';
  load();
}
// Запуск и остановка десктопа области из панели.
// ⚠️ 18.11: эта функция в панели ОТСУТСТВОВАЛА — кнопка звала
// несуществующую toggleArea, и клик молча ничего не делал.
async function toggleArea(slug, op){
  box.classList.add('show');
  box.innerHTML='<b>'+slug+':</b> '+(op==='stop'?'останавливаю десктоп…':'запускаю десктоп…');
  try{
    const r=await (await fetch('/api/'+op+'?area='+slug)).json();
    box.innerHTML='<b>'+slug+':</b> '+(op==='stop'
      ? ('десктоп остановлен, осталось процессов: '+((r.left===undefined)?'?':r.left))
      : ('десктоп запущен: '+(r.out||'').trim().split(/\\n/).slice(-1)[0]));
  }catch(e){
    box.innerHTML='<b>'+slug+':</b> не получилось: '+e;
  }
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
    return json(200, { areas: list, accounts: acc, journal: readJournal(60), alarm: (healState().alarm || null),
      net: readJson(NET_STATE_FILE), sessions: readJson(SESSION_STATE_FILE) });
  }

  // Ручная проверка сети и сессий — зовём те же демоны, что и под супервизором
  // (одна реализация, никаких копий механики в панели).
  if (p === '/api/net-check') {
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
    const r = await run('python', [NETWATCH, '--once'], { env, timeout: 300000 });
    const last = (r.out || '').trim().split('\n').slice(-1)[0] || '';
    log('ручная проверка сети: ' + last);
    return json(200, { ok: r.code === 0, out: r.out });
  }

  if (p === '/api/sessions-check') {
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
    const r = await run('python', [SESSIONWATCH, '--once', '--dry-run'], { env, timeout: 300000 });
    const last = (r.out || '').trim().split('\n').slice(-1)[0] || '';
    log('ручная проверка сессий (сухой прогон): ' + last);
    return json(200, { ok: r.code === 0, out: r.out });
  }

  // Новый вход — только по одной области и только по команде владельца.
  if (p === '/api/repair') {
    const slug = q.get('area');
    if (!slug) return json(400, { error: 'лечение теперь ручное: укажи область' });
    const r = await newAccount(slug);
    return json(200, { ok: r.ok, out: r.out });
  }

  if (p === '/api/start' || p === '/api/stop') {
    const slug = q.get('area');
    if (!slug) return json(400, { error: 'нужен area' });
    if (p === '/api/start') {
      const script = path.join(BIN, 'mcode-project-start.ps1');
      const r = await runPS(script, ['-Project', slug]);
      log(slug + ': запуск десктопа — ' + (r.out || '').trim().split('\n').slice(-1)[0]);
      return json(200, { ok: true, out: r.out });
    }
    // Остановка: штатный стоп, потом ДОБИВАЕМ всё, что осталось — иначе
    // зависшее окно продолжает кушать память (жалоба владельца 17.11).
    const script = path.join(BIN, 'mcode-project-stop.ps1');
    const r = await runPS(script, ['-Project', slug]);
    const left = await killLeftovers(slug);
    log(slug + ': десктоп остановлен, осталось процессов: ' + left);
    return json(200, { ok: true, left: left, out: r.out });
  }

  // Режим обслуживания области: Автомат / Вручную.
  if (p === '/api/mode') {
    const slug = q.get('area');
    const mode = q.get('mode');
    if (!slug) return json(400, { error: 'нужен area' });
    const r = setMode(slug, mode);
    if (r.ok) {
      // Кэш состояния живёт 45 с — без сброса переключатель откатывался бы
      // на старый режим («ничего не меняется»). Обновляем кэш и заставляем
      // следующий опрос взять свежие данные.
      if (stateCache.data) {
        for (const a of stateCache.data) if (a.slug === slug) a.autoheal = !!r.autoheal;
      }
      stateCache.ts = 0;
    }
    return json(r.ok ? 200 : 400, r);
  }

  // Перезагрузка панели: сбросить состояние и пересканировать десктопы.
  if (p === '/api/reload') {
    stateCache.ts = 0;
    stateCache.data = null;
    let list = [];
    try { list = await liveState(true); } catch (e) { return json(200, { ok: false, error: String(e && e.message || e) }); }
    log('панель перезагружена — областей пересканировано: ' + list.length);
    return json(200, { ok: true, areas: list.length });
  }

  // Эндпоинт /api/login (ручной вход в браузере) удалён 18.11 по решению
  // владельца: кнопка «Оживить вход (браузер)» убрана из панели вместе с ним.

  res.writeHead(404);
  res.end('not found');
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('МиниГен (области проектов): http://127.0.0.1:' + PORT);
});
