import type {
  Area,
  AgentsSnapshot,
  ChatAttachment,
  ChatMessage,
  ChatReply,
  PendingAction,
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyMode,
  CopyTokenCheck,
  CopyTokenIssue,
  CopyTokenRequest,
  CopyTokenStatus,
  DaemonStatus,
  DbExportResult,
  DbImportPlan,
  DbImportReport,
  DbStatus,
  GeneActionResult,
  GeneMap,
  GeneRecord,
  GeneSuggestions,
  InstallGuide,
  JournalLine,
  KarmanChannel,
  LineageInfo,
  ModelCatalog,
  NetworkState,
  ProgramVersion,
  PromptsSnapshot,
  PromptsView,
  Settings,
  Snapshot,
  Stats,
  UpdateAvailable,
  UpdateState
} from '@shared/types'

declare global {
  interface Window {
    minigen: {
      call(cmd: string, args?: Record<string, unknown>): Promise<unknown>
      openExternal(path: string): Promise<boolean>
      copyText(text: string): Promise<boolean>
      saveText(name: string, content: string): Promise<{ ok: boolean; path: string; error?: string }>
      /** выбрать файлы для чата (диалог в главном процессе) */
      pickFiles(): Promise<{ path: string; name: string; size: number }[]>
      /** настоящий путь перетащенного файла (пусто — не удалось) */
      getPathForFile(file: File): string
      relaunch(): Promise<boolean>
      buildInfo(): Promise<{ commit: string }>
      bridgeStatus(): Promise<{ alive: boolean; message: string }>
      smoke: { csv: string }
      onEvent(handler: (event: { kind: string; message: string; alive: boolean }) => void): () => void
    }
  }
}

/** Тонкая обёртка над мостом: типизированные вызовы и понятные ошибки. */
async function call<T>(cmd: string, args: Record<string, unknown> = {}): Promise<T> {
  const data = await window.minigen.call(cmd, args)
  return data as T
}

export const api = {
  snapshot: () => call<Snapshot>('state.snapshot'),
  areas: () => call<{ areas: Area[] }>('areas.list', { sessions: true }),
  areaState: (slug: string) => call<Area>('areas.state', { slug }),
  points: (slugs?: string[]) => call<{ points: Record<string, unknown>; pending: string[] }>(
    'areas.points',
    slugs ? { slugs } : {}
  ),
  // ⚠️ Промт КАРМАНА #182: выключить автомат аккаунтов **нечем** — ручного
  // режима в «МиниГене» больше нет, колонка «Режим» убрана из окна. Поэтому
  // в окне и нет вызова, который переключал бы режим обслуживания области.
  // Переключатель «водитель проекта»: кто водит сессиями области
  // («Управляющий» / «автомат» / «никто» / «по правилу») — промт #174.
  setDriver: (slug: string, driver: string) =>
    call<{ ok: boolean; error?: string; driver?: string; title?: string }>(
      'areas.set_driver',
      { slug, driver }
    ),
  start: (slug: string) => call<{ ok: boolean; error?: string }>('areas.start', { slug }),
  stop: (slug: string) => call<{ ok: boolean; error?: string }>('areas.stop', { slug }),
  restart: (slug: string) => call<{ ok: boolean; error?: string }>('areas.restart', { slug }),
  relogin: (slug: string) => call<{ ok: boolean; error?: string }>('areas.relogin', { slug }),
  createArea: (payload: Record<string, unknown>) =>
    call<{ ok: boolean; error?: string; slug?: string }>('areas.create', payload),
  sessions: (slug: string) => call<{ sessions: unknown[] }>('sessions.list', { slug }),
  networkCheck: () => call<NetworkState>('network.check'),
  journal: (params: { limit?: number; contains?: string; tag?: string }) =>
    call<{ lines: JournalLine[]; path: string }>('journal.read', params),
  stats: (days: number) => call<Stats>('stats.summary', { days }),
  settings: () => call<Settings>('settings.get'),
  saveSettings: (patch: Partial<Settings>) => call<Settings>('settings.set', patch),
  chatSend: (text: string, attachments?: ChatAttachment[]) =>
    call<ChatReply>('chat.send', { text, attachments }),
  chatConfirm: (approved: boolean) => call<ChatReply>('chat.confirm', { approved }),
  // История чата с диска машины: переписка не теряется при перезапуске окна.
  chatHistory: () => call<{ messages: ChatMessage[]; path?: string }>('chat.history'),
  // Ожидающее подтверждение опасного действия: переживает перезапуск окна
  // (промт #129) — иначе вопрос владельцу исчезает молча.
  chatPending: () =>
    call<{ pending?: PendingAction; restored?: boolean }>('chat.pending'),
  chatReset: () => call<{ ok: boolean }>('chat.reset'),
  daemonsStatus: () => call<DaemonStatus>('daemons.status'),
  programVersion: () => call<ProgramVersion>('program.version'),
  lineageShow: () => call<{ lineage: LineageInfo }>('lineage.show'),
  mapShow: () => call<GeneMap>('map.show'),
  modelCatalog: (area?: string) => call<ModelCatalog>('model.catalog', area ? { area } : {}),
  modelApply: (args: { model?: string; modelContext?: number; modelEffort?: string; areas?: string[] }) =>
    call<{ applied: unknown[]; failed: unknown[]; changed: number; error?: string }>(
      'model.apply',
      args
    ),
  daemonsStart: (name?: string) => call<unknown>('daemons.start', name ? { name } : {}),
  daemonsStop: (name?: string) => call<unknown>('daemons.stop', name ? { name } : {}),
  daemonsRestart: (name?: string) => call<unknown>('daemons.restart', name ? { name } : {}),
  updateStatus: () => call<UpdateState>('update.status'),
  updateCheck: () => call<UpdateAvailable>('update.check'),
  updateStart: () => call<{ ok: boolean; why?: string; error?: string }>('update.start'),
  openFolder: (path: string) => call<{ ok: boolean; error?: string }>('app.open_folder', { path }),
  // ── агенты «МиниГена» (вкладка «Агенты», 25.11) ─────────────────────────
  agentsList: () => call<AgentsSnapshot>('agents.list'),
  agentsApply: async (slug: string) => {
    const result = await call<{ ok: boolean; error?: string; area?: string; skills_installed?: string[] }>(
      'agents.apply',
      { slug }
    )
    if (result?.error) throw new Error(result.error)
    return result
  },
  agentsCreate: async (draft: { slug: string; title: string; role: string }) => {
    const result = await call<{ ok: boolean; error?: string; repo?: string; area?: string }>(
      'agents.create',
      { slug: draft.slug, title: draft.title, role: draft.role }
    )
    if (result?.error) throw new Error(result.error)
    return result
  },
  agentsRegistryRestore: async () => {
    const result = await call<{ ok: boolean; error?: string; before?: string[]; after?: string[] }>(
      'agents.registry_restore',
      {}
    )
    if (result?.error) throw new Error(result.error)
    return result
  },
  karmanStatus: () => call<KarmanChannel>('karman.status'),
  // ── «Хранилище промтов» (вкладка «Промты», 25.11) ────────────────────────
  promptsList: (project?: string, view?: PromptsView) =>
    call<PromptsSnapshot>('prompts.list', {
      ...(project ? { project } : {}),
      ...(view ? { view } : {})
    }),
  promptsSave: (payload: { project: string; title: string; body: string }) =>
    call<{ ok: boolean; error?: string; id?: string }>('prompts.save', payload),
  promptsArchive: (payload: { id: string; project?: string; archived?: boolean }) =>
    call<{ ok: boolean; error?: string }>('prompts.archive', payload),
  promptsMark: (payload: { id: string; project?: string; auto: boolean }) =>
    call<{ ok: boolean; error?: string }>('prompts.mark', payload),
  promptsDispatch: (payload: { id: string; project?: string; area: string }) =>
    call<{ ok: boolean; error?: string; why?: string }>('prompts.dispatch', payload),
  // ── копия: режимы, первый запуск, обмен наработками (25.11, #131) ────────
  cloneStatus: () => call<CloneStatus>('clone.status'),
  cloneSetup: (args: { mode?: CopyMode; upstream?: string; pool?: string; private?: boolean }) =>
    call<GeneActionResult & { steps?: unknown[] }>('clone.setup', args),
  cloneSync: () =>
    call<{ ok: boolean; why?: string; steps?: { what: string; ok: boolean; why?: string }[] }>(
      'clone.sync'
    ),
  genesList: () => call<{ genes: GeneRecord[] }>('genes.list'),
  genesRead: (gene: string) =>
    call<{ manifest?: Record<string, unknown>; notes?: string; patch?: string; error?: string }>(
      'genes.read',
      { gene }
    ),
  genesPublish: (payload: { title: string; summary?: string; notes?: string }) =>
    call<GeneActionResult>('genes.publish', payload),
  genesAdopt: (gene: string, title?: string, allowAppearance?: boolean) =>
    call<GeneActionResult>('genes.adopt', {
      gene,
      ...(title ? { title } : {}),
      ...(allowAppearance ? { allow_appearance: true } : {})
    }),
  genesSuggest: () => call<GeneSuggestions>('genes.suggest'),
  genesExport: (path?: string) =>
    call<GeneActionResult>('genes.export', path ? { path } : {}),
  genesImport: (path: string) => call<GeneActionResult>('genes.import', { path }),
  // ── откат, оценки и пометка «не ставить» (промт КАРМАНА #187) ──────────
  /** Откатить внедрённый ген: вернуть состояние «как было». */
  genesRollback: (gene: string) => call<GeneActionResult>('genes.rollback', { gene }),
  /** Оценка гену от 1 до 10 (звёздочки). */
  genesRate: (gene: string, score: number) =>
    call<GeneActionResult>('genes.rate', { gene, score }),
  /** Пометить ген «не ставить» (или снять пометку). */
  genesDismiss: (gene: string, on = true, reason?: string) =>
    call<GeneActionResult>('genes.dismiss', { gene, on, ...(reason ? { reason } : {}) }),
  copyFirstRun: () => call<CopyFirstRunSnapshot>('copy.first_run'),
  copyAnswer: (agree: boolean, mode?: CopyMode) =>
    call<GeneActionResult>('copy.answer', mode ? { agree, mode } : { agree }),
  copyMode: (mode: CopyMode) => call<GeneActionResult>('copy.mode', { mode }),
  // ── токен копии (25.11, #130) ────────────────────────────────────────────
  copyTokenStatus: () => call<CopyTokenStatus>('copy.token_status'),
  copyTokenRequest: (name?: string, section?: string) =>
    call<GeneActionResult & { id?: string; instance_id?: string }>(
      'copy.token_request',
      { name, section }
    ),
  copyTokenRequests: (section?: string) =>
    call<{ requests: CopyTokenRequest[] }>(
      'copy.token_requests',
      section ? { section } : {}
    ),
  copyTokenIssue: (args: { instance_id?: string; folder?: string; name?: string }) =>
    call<CopyTokenIssue>('copy.token_issue', args),
  copyTokenSet: (args: { token: string; instance_id?: string; folder?: string }) =>
    call<CopyTokenStatus & { bound?: boolean }>('copy.token_set', args),
  copyTokenCheck: (folder?: string) =>
    call<CopyTokenCheck>('copy.token_check', folder ? { folder } : {}),
  copyTokenTrust: (instance_id: string, key_id?: string) =>
    call<{ ok?: boolean; why?: string; error?: string }>('copy.token_trust', {
      instance_id,
      key_id
    }),
  // ── локальная база «МиниГена» (промт #92) ───────────────────────────────
  dbStatus: () => call<DbStatus>('db.status'),
  dbRecords: (kind?: string, limit?: number) =>
    call<{ ok: boolean; records: unknown[]; count: number }>('db.records', {
      kind,
      limit
    }),
  /** Выгрузка: путь выбирает оператор (диалог главного процесса). */
  dbExport: () => call<DbExportResult>('db.export'),
  /** Разбор файла выгрузки — ничего не меняет, только план и вопросы. */
  dbInspect: (path: string) => call<DbImportPlan>('db.inspect', { path }),
  /** Импорт: решения оператора по спорным записям («вид|ключ» → решение). */
  dbImport: (path: string, decisions?: Record<string, string>) =>
    call<DbImportReport>('db.import', { path, decisions }),
  /** Выбор файла выгрузки — диалог открывает главный процесс. */
  dbPickFile: () => call<{ ok: boolean; path?: string; canceled?: boolean }>('db.pick'),
  // ── «Черенки»: инструкция по установке копии другому (26.11) ───────────
  installGuide: () => call<InstallGuide>('install.guide')
}

/** Действия окна (не моста): перезапуск, метка сборки, состояние моста. */
export const desktop = {
  buildInfo: () => window.minigen.buildInfo(),
  relaunch: () => window.minigen.relaunch(),
  bridgeStatus: () => window.minigen.bridgeStatus()
}
