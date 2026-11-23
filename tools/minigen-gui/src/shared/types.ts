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

export interface DaemonStatus {
  supervisor: boolean
  procs: Record<string, boolean>
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
}

export interface BridgeEvent {
  kind: 'bridge-status' | 'bridge-error'
  message: string
  alive: boolean
}
