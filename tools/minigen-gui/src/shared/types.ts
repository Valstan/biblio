/** Формы данных «МиниГена» — общий контракт окна и моста. */

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
  /** где лежат промты: в базе «МиниГена» или на внешнем ресурсе */
  promptStore: 'local' | 'karman'
  /** раздавать ли промты рабочим областям без живого оператора */
  promptAutoDispatch: boolean
  /** совет специалистов: обсуждать ли новое задание перед работой */
  councilEnabled: boolean
  /** сколько ролей совета реально отвечают на задачу (совет дорог) */
  councilMaxRoles: number
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
}
