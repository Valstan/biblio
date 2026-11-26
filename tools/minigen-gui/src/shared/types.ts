/** Формы данных «МиниГена» — общий контракт окна и моста. */

/**
 * Режим копии (заказ владельца 25.11, промт #131):
 * - `own` — свой репозиторий на GitHub, PR — в общий репозиторий программы;
 * - `shared` — папка в общем репозитории программы (дефолт после согласия);
 * - `local` — только локально, без сети: правки в локальном git-репозитории,
 *   PR не открывается, обмен с другими копиями недоступен.
 */
export type CopyMode = 'own' | 'shared' | 'local'

/** Режим копии — подпись и что режим значит (для окна). */
export interface CopyModeOption {
  id: CopyMode
  label: string
  hint: string
}

/** Обмен наработками: доступен ли и почему — если нет. */
export interface CopyExchange {
  available: boolean
  transport: string
  pool: string
  pool_exists?: boolean
  why: string
}

/** Локальный git-репозиторий правок (режим «только локально»). */
export interface CopyLocalRepo {
  path: string
  exists: boolean
  commit?: string
  branch?: string
  dirty?: boolean
  remotes?: string
}

/** Ответ на вопрос первого запуска (хранится в родословной копии). */
export interface CopyFirstRun {
  asked: boolean
  agreed: boolean
  mode: string
  answered_at: number
  instance: string
}

/** Текст вопроса первого запуска: репозиторий, папка, что даёт, отказ. */
export interface CopyOffer {
  title: string
  repository: string
  folder: string
  gives: string[]
  if_refuse: string[]
  who: string
  modes: CopyModeOption[]
}

/** Снимок первого запуска и режима копии (ответ моста `copy.first_run`). */
export interface CopyFirstRunSnapshot {
  first_run: CopyFirstRun
  offer: CopyOffer
  /** '' — режим не определён (мост не ответил или состояние неизвестно) */
  mode: CopyMode | ''
  mode_label: string
  exchange: CopyExchange
}

/** Состояние клона копии (ответ моста `clone.status`). */
export interface CloneStatus {
  instance_id?: string
  family?: string
  generation?: number
  clone_dir: string
  clone_exists: boolean
  pool_dir: string
  pool_exists: boolean
  copy_repo: string
  upstream: string
  branch?: string
  dirty?: boolean
  remotes?: Record<string, string>
  /** '' — режим не определён (мост не ответил или состояние неизвестно) */
  mode: CopyMode | ''
  mode_label: string
  exchange: CopyExchange
  /** локальный репозиторий — есть не всегда (мост отвечает не всегда) */
  local_repo?: CopyLocalRepo
  /** ответ на вопрос первого запуска — есть не всегда */
  first_run?: CopyFirstRun
}

/** Ген пула наработок. */
export interface GeneRecord {
  id: string
  title: string
  summary: string
  author: string
  created_at: number
  files: string[]
  base_commit?: string
  path?: string
}

/** Чужая наработка, которую стоит посмотреть (ответ моста `genes.suggest`). */
export interface GeneSuggestion extends GeneRecord {
  score: number
  why: string
  fits: boolean
}

/** Подсказки наработок. */
export interface GeneSuggestions {
  suggestions: GeneSuggestion[]
  fits: number
  my_files: string[]
  transport: string
  available: boolean
  why: string
}

/** Ответ моста на действие с генами (публикация, внедрение, обмен). */
export interface GeneActionResult {
  ok?: boolean
  gene?: string
  pr?: string
  branch?: string
  local_only?: boolean
  path?: string
  files?: number
  added?: string[]
  skipped?: string[]
  why?: string
  error?: string
}

/**
 * Токен копии (заказ владельца 25.11, промт #130): подтверждает право
 * копии работать **со своей папкой**. Значения секрета в ответе нет —
 * только путь, отпечаток и признак привязки.
 */
export interface CopyTokenStatus {
  has_token: boolean
  /** отпечаток токена (идентификатор) — секрета не раскрывает */
  token_id: string
  /** маска значения (видны только последние четыре знака) */
  masked: string
  /** где лежит токен: стор на машине, вне репозитория */
  path: string
  source?: string
  instance_id: string
  /** папка копии в общем репозитории: copies/<идентификатор копии>/ */
  folder: string
  issued_at: number
  /** токен подтверждён под свою папку */
  bound: boolean
  reason: string
}

/** Заявка копии на токен — что пришло владельцу в «МиниГене». */
export interface CopyTokenRequest {
  id: string
  instance_id: string
  name: string
  folder: string
  family: string
  generation: number
  fork_commit: string
  /** отпечаток подписи копии */
  key_id: string
  signed_at: number
  signature: string
  /** «своя копия» / «чужая копия» — по родословной и подписи */
  relation: string
  signature_state: string
  created: string
  archived: boolean
}

/** Результат выпуска токена: значение показывается один раз (владельцу). */
export interface CopyTokenIssue {
  ok?: boolean
  token?: string
  token_id?: string
  proof?: string
  instance_id?: string
  folder?: string
  issued_at?: number
  path?: string
  why?: string
  error?: string
}

/** Проверка доступа копии (ответ моста `copy.token_check`). */
export interface CopyTokenCheck {
  ok: boolean
  folder: string
  token_id: string
  reason: string
}


export interface AccountState {
  email?: string
  expires_at?: number | null
  app_status?: string
  has_record?: boolean
  has_refresh?: boolean
  state?: string
}

export interface SessionVerdict {
  session_id: string
  title: string
  status: string
  verdict: string
  reason: string
  fill?: number | null
  peak_fill?: number
  turn_status?: string
  updated_at_ms: number
  broken_kids?: number
  last_text?: string
}

export interface Area {
  slug: string
  title: string
  folder: string
  karman: string
  autoheal: boolean
  dir: string
  running: boolean
  exists: boolean
  account: AccountState
  sessions: SessionVerdict[]
  points?: AccountState & { points_today?: number; next_points?: number; state?: string }
}

export interface NetworkPath {
  name: string
  proxy: string
  role: string
  alive: boolean
  code: number
  country: string
  ip: string
  note: string
}

export interface NetworkState {
  paths: NetworkPath[]
  vendor: Record<string, unknown>
  proxy: Record<string, unknown>
  stable: boolean
  last_run: Record<string, unknown>
  probed: boolean
}

export interface StatsArea {
  slug: string
  total: number
  reasons: Record<string, number>
  last: string
  running: boolean
}

export interface StatsEvent {
  ts: string
  slug: string
  kind: string
  reason: string
  text: string
}

export interface Stats {
  at: number
  days: number
  restarts: number
  network_events: number
  by_reason: Record<string, number>
  by_kind: Record<string, number>
  by_area: StatsArea[]
  events: StatsEvent[]
  areas_total: number
  areas_running: number
}

export interface JournalLine {
  ts: string
  text: string
}

export interface Settings {
  refreshMs: number
  journalTail: number
  manageDaemons: boolean
  modelArea: string
  model: string
  /** окно контекста модели (0 = взять родное значение области/модели) */
  modelContext: number
  /** уровень эффорта модели ('' = взять родное значение области/модели) */
  modelEffort: string
  /** разложить выбранные настройки по рабочим областям */
  modelApplyToAreas: boolean
  confirmDangerous: boolean
  statsWindow: number
  /** где исходники программы (для правок и PR) */
  codeRepo: string
  /** реестр агентов «МиниГена» (репозиторий на GitHub) */
  agentsRegistry: string
  /** где живут рабочие репозитории агентов */
  agentsWorkspace: string
  /** где лежат промты: в базе «МиниГена» или на внешнем ресурсе */
  promptStore: 'local' | 'karman'
  /** раздавать ли промты рабочим областям без живого оператора */
  promptAutoDispatch: boolean
  /** совет специалистов: обсуждать ли новое задание перед работой */
  councilEnabled: boolean
  /** сколько ролей совета реально отвечают на задачу (совет дорог) */
  councilMaxRoles: number
  /** сколько раундов задачи может занять совет специалистов */
  councilRounds: number
  /** гарантированный запас шагов для работы после обсуждения совета */
  councilWorkFloor: number
  /** режим копии: '' (не выбран), 'own', 'shared', 'local' */
  copyMode: CopyMode | ''
  /** репозиторий копии (в режиме «свой репозиторий») */
  copyRepo: string
  /** где лежит токен копии (в сторе на машине, вне репозитория) */
  copyTokenPath: string
  /** отпечаток токена (секрета не раскрывает) */
  copyTokenId: string
  /** папка копии в общем репозитории: copies/<идентификатор копии>/ */
  copyTokenFolder: string
  /** раздел КАРМАНА, куда уходят заявки на токен */
  copyRequestsSection: string
}

export interface ToolCall {
  tool: string
  args: Record<string, unknown>
  result: unknown
  approved?: boolean
}

export interface PendingAction {
  tool: string
  args: Record<string, unknown>
  reason: string
  call_id: string
}

export interface ChatReply {
  status: 'done' | 'confirm' | 'unavailable' | 'error'
  text: string
  error?: string
  calls: ToolCall[]
  pending?: PendingAction
}

/**
 * Файл, приложенный оператором к чату «Управляющий» (промт #90).
 *
 * Оператор прикладывает файлы кнопкой или перетаскиванием — содержимое уходит
 * модели в контекст. Окно **не читает диск**: файл читает мост, окно знает
 * только имя, вид и размер.
 */
export interface ChatAttachment {
  /** имя файла (как его видит оператор) */
  name: string
  /** вид: `text` | `image` | `document` | `unknown` */
  kind: string
  /** размер в байтах */
  size: number
  /** прочитан ли мостом (у файла в истории это уже известно) */
  ok?: boolean
  /** полный путь на машине — нужен мосту, окну только для отправки */
  path?: string
}

/**
 * Реплика чата «Управляющий» — то, что видит владелец.
 *
 * ⚠️ Это **видимая** история, а не техническая: она лежит на диске машины
 * (`%LOCALAPPDATA%\minigen-gui\chat-history.json`) и восстанавливается при
 * перезапуске окна. Вызовы инструментов лежат здесь же, но в окне показываются
 * свёрнутыми («Что сделано: N действий») — подробности открываются по клику.
 */
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  calls?: ToolCall[]
  /** файлы, приложенные оператором к этой реплике (промт #90) */
  attachments?: ChatAttachment[]
  /** когда реплика (мс) */
  at?: number
}

export interface Snapshot {
  at: number
  areas: Area[]
  network: NetworkState
  daemons: DaemonStatus
  stats: Stats
  config_error: string
  journal_path: string
  areas_root: string
  daemon_dir: string
}

export interface DaemonInfo {
  name: string
  role: string
  script: string
  exists: boolean
  alive: boolean
  pids: number[]
}

export interface SupervisorInfo {
  pid: number
  path: string
  since?: number
  alive: boolean
  /** Надзиратель — этот самый процесс (окно программы). */
  is_self?: boolean
}

export interface DaemonStatus {
  daemons: DaemonInfo[]
  alive_count: number
  total: number
  checked_at: number
  supervisor?: SupervisorInfo | null
}

export interface ProgramVersion {
  commit: string
  installed_at: number
  source: string
  installed: boolean
  running_from: string
}

/** Шаг обновления программы — одна строка журнала. */
export interface UpdateStep {
  ts: number
  step: string
  status: 'info' | 'ok' | 'error'
  text: string
}

/** Что есть в репозитории (по последней проверке). */
export interface UpdateAvailable {
  commit: string
  behind: number
  repo: string
  remote: string
  ok?: boolean
  why?: string
  up_to_date?: boolean
  installed?: ProgramVersion
}

/** Состояние обновления — окно спрашивает его раз в секунду. */
export interface UpdateState {
  running: boolean
  steps: UpdateStep[]
  installed: ProgramVersion
  available: UpdateAvailable
  last: { ok?: boolean; why?: string; at?: number; installed?: ProgramVersion }
  repo: string
}

/** Модель — с её **родными** настройками (как объявляет платформа). */
export interface ModelEntry {
  id: string
  name: string
  context_default: number
  context_options: number[]
  context_hints: Record<string, string>
  effort_options: string[]
  effort_default: string
  max_output: number
  variants: { id: string; thinking: Record<string, unknown> }[]
  reasoning: boolean
}

/** Каталог моделей провайдера области + что используется сейчас. */
export interface ModelCatalog {
  provider: string
  provider_name: string
  npm: string
  models: ModelEntry[]
  default_model: string
  area_context: number
  area_effort: string
  area_variant: string
  config_path: string
  area?: string
  effective?: {
    provider: string
    model: string
    context: number
    effort: string
    variant: string
    max_output: number
    effort_options: string[]
    context_options: number[]
  }
}

export interface BridgeEvent {
  kind: 'bridge-status' | 'bridge-error'
  message: string
  alive: boolean
}

/**
 * Родословная копии (ответ моста `lineage.show`).
 */
export interface LineageInfo {
  instance: {
    instance_id?: string
    family?: string
    generation?: number
    birth?: number
    fork_of?: { commit?: string; installed_at?: number }
    parent?: { instance?: string; gene?: string; commit?: string }
    genes?: { gene?: string; from?: string; commit?: string; ts?: number }[]
    copy_repo?: string
    upstream?: string
  }
  history?: { ts?: number; event?: string; [key: string]: unknown }[]
  instance_file?: string
  ledger_file?: string
}

/**
 * Карта родословной: паутина копий «МиниГена» и генов между ними
 * (заказ владельца 24.11). Собирается из пула наработок: каждая копия
 * дописывает в пул свои связи (рождение, публикация гена, внедрение чужого).
 */
export interface GeneMapCopy {
  instance: string
  generation: number
  born_at: number
  fork_commit: string
  parent: { instance?: string; gene?: string; commit?: string }
  genes_made: string[]
  genes_taken: string[]
  taken_by: string[]
  repo: string
  upstream: string
  is_self: boolean
}

export interface GeneMapGene {
  id: string
  author: string
  title: string
  summary: string
  base_commit: string
  created_at: number
  state: 'published' | 'adopted' | 'retracted' | string
  taken_by: string[]
  replaced_by: string
  why_retracted: string
}

export interface GeneMapEdge {
  from: string
  to: string
  kind: 'made' | 'took' | 'born' | 'forked' | 'replaced' | string
}

export interface GeneMap {
  self: {
    instance: string
    generation: number
    born_at: number
    fork_commit: string
    parent: { instance?: string; gene?: string; commit?: string }
    repo: string
    upstream: string
  }
  copies: GeneMapCopy[]
  genes: GeneMapGene[]
  edges: GeneMapEdge[]
  stats: {
    copies: number
    genes: number
    links: number
    events: number
    made: number
    took: number
  }
  pool: string
  pool_ready: boolean
  note: string
}

/**
 * Манифест агента (реестр `agents/<slug>/agent.yaml`).
 *
 * Поля могут отсутствовать: манифест — это yaml, и старый агент мог быть
 * заведён без роли или области. Тогда окно показывает «—», а не врёт.
 */
export interface AgentManifest {
  slug: string
  title?: string
  role?: string
  repo?: string
  area?: string
  template?: string
  model?: string
  constitution?: string
  skills?: string[]
}

/** Заявка агента, уже сохранённая в его репозитории. */
export interface AgentRequest {
  path: string
  project: string
  size: number
}

/** Агент реестра + его заявки (ответ моста `agents.list`). */
export interface AgentRecord extends AgentManifest {
  requests?: AgentRequest[]
}

/** Локальная копия реестра: совпадает ли с GitHub (источник правды). */
export interface RegistryState {
  registry: string
  path: string
  cloned: boolean
  dirty: boolean
  files: string[]
  why: string
}

/** Снимок реестра агентов (ответ моста `agents.list`). */
export interface AgentsSnapshot {
  registry: string
  registry_path: string
  registry_cloned: boolean
  registry_state?: RegistryState
  workspace: string
  agents: AgentRecord[]
  templates: string[]
  digest?: string
}

/**
 * Состояние канала промтов КАРМАНА (ответ моста `karman.status`).
 *
 * Значения секрета здесь нет — только источник (`file` / `env` / `vault`).
 */
export interface KarmanChannel {
  has_url: boolean
  has_secret: boolean
  secret_source: string
  url_source: string
  room_token_source?: string
  http: number
  ok: boolean
  sections: string[]
  reason: string
}

/**
 * «Хранилище промтов» (заказ владельца 24.11): где лежат промты и планы —
 * в базе «МиниГена» или на внешнем ресурсе (Карман со своим хранилищем).
 */
export type PromptStoreKind = 'local' | 'karman'

/**
 * Что показывает вкладка: живые промты, архивные или и те и другие.
 *
 * ⚠️ Архив виден **отдельно**: в КАРМАНЕ архив = «сделано», и заданный по
 * ошибке промт должен быть виден и возвращаемым, а не исчезать из оборота.
 */
export type PromptsView = 'live' | 'archived' | 'all'

/** Запись хранилища: промт или план (план идёт рядом с промтом). */
export interface PromptRecord {
  id: string
  project: string
  title: string
  body: string
  kind: 'prompt' | 'plan'
  archived: boolean
  createdAt: string
  source: PromptStoreKind
  /** владелец пометил промт как «раздавать автоматически» */
  auto?: boolean
}

/** Место хранения — что окно показывает сверху вкладки. */
export interface PromptStoreInfo {
  kind: PromptStoreKind
  /** «в базе МиниГена» / «на внешнем ресурсе» */
  label: string
  /** адрес внешнего ресурса (пусто у локальной базы) */
  location: string
  ok: boolean
  reason: string
  sections: string[]
}

/** Область, которой можно отдать промт (раздел промта = проект области). */
export interface PromptTarget {
  slug: string
  title: string
  section: string
  /** в области уже есть живая сессия */
  busy: boolean
}

/** Снимок хранилища (ответ моста `prompts.list`). */
export interface PromptsSnapshot {
  store: PromptStoreInfo
  prompts: PromptRecord[]
  targets: PromptTarget[]
  /** автораздача без живого оператора включена ли */
  auto_dispatch: boolean
  /** какой вид показан (живые / архив / все) — ответ моста */
  view?: PromptsView
}

/**
 * Локальная база «МиниГена» (заказ владельца 24.11, промт #92).
 *
 * ⚠️ Схема базы **не привязана к версии программы**: данные лежат видами
 * (`kind`), а версия программы знает только, какие виды понимает. Чужой вид
 * не выбрасывается, а лежит в «неразобранном» — и уезжает в следующей
 * выгрузке.
 */
export interface DbStatus {
  ok: boolean
  /** где лежит файл базы */
  path: string
  /** версия схемы хранилища (не версия программы) */
  schema_version: number
  total: number
  counts_by_kind: Record<string, number>
  kinds_present: string[]
  /** виды, которые эта версия программы понимает */
  kinds_known: string[]
  /** как каждый известный вид называется для человека */
  kind_titles?: Record<string, string>
  /** виды, которых она не знает (лежат в неразобранном) */
  kinds_unknown: string[]
  unparsed: number
  format: string
  format_version: number
  error?: string
}

/** Запись базы — общий контейнер: вид, ключ, содержимое. */
export interface DbRecord {
  kind: string
  key: string
  payload: Record<string, unknown>
  version: number
  created_at: string
  updated_at: string
  deleted_at?: string | null
}

/** Выгрузка базы в один файл (ответ моста `db.export`). */
export interface DbExportResult {
  ok: boolean
  path: string
  total: number
  counts_by_kind: Record<string, number>
  checksum: string
  exported_at: string
  schema_version: number
  /** оператор отменил выбор файла */
  canceled?: boolean
  error?: string
}

/** Спорная запись при импорте — её решает оператор, а не программа. */
export interface DbConflict {
  /** «вид|ключ» — под этим идентификатором решение и принимается */
  id: string
  kind: string
  key: string
  title: string
  question: string
}

/** Разбор файла выгрузки (ответ моста `db.inspect`) — ничего не меняет. */
export interface DbImportPlan {
  ok: boolean
  path: string
  file?: {
    schema_version?: number
    total?: number
    counts_by_kind?: Record<string, number>
    exported_at?: string
  }
  version?: {
    ours?: number
    theirs?: number
    same?: boolean
    note?: string
  }
  counts?: {
    insert?: number
    same?: number
    conflict?: number
  }
  conflicts: DbConflict[]
  /** вопросы оператору: куда и как переносить спорные данные */
  questions: string[]
  unknown_kinds: string[]
  checksums_ok?: boolean
  error?: string
}

/** Отчёт об импорте (ответ моста `db.import`). */
export interface DbImportReport {
  ok: boolean
  path: string
  inserted: number
  same: number
  overwritten: number
  kept: number
  copied: number
  unparsed: number
  /** конфликты без решения оператора — их не тронули */
  unresolved: string[]
  questions: string[]
  why: string
  error?: string
}
