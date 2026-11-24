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
  /** Что не прочиталось (снимок собран не целиком). Нет поля — всё прочиталось. */
  error?: string
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
  /** Состояние демонов не прочиталось (нет поля — читалось). */
  error?: string
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
