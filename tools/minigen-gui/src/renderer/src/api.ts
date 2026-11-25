import type {
  Area,
  AgentsSnapshot,
  ChatReply,
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyMode,
  DaemonStatus,
  GeneActionResult,
  GeneMap,
  GeneRecord,
  GeneSuggestions,
  JournalLine,
  KarmanChannel,
  LineageInfo,
  ModelCatalog,
  NetworkState,
  ProgramVersion,
  PromptsSnapshot,
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
      saveText(name: string, content: string): Promise<{ ok: boolean; path: string; error?: string }>
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
  setAutoheal: (slug: string, autoheal: boolean) =>
    call<{ ok: boolean; error?: string }>('areas.set_autoheal', { slug, autoheal }),
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
  chatSend: (text: string) => call<ChatReply>('chat.send', { text }),
  chatConfirm: (approved: boolean) => call<ChatReply>('chat.confirm', { approved }),
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
  promptsList: (project?: string) =>
    call<PromptsSnapshot>('prompts.list', project ? { project } : {}),
  promptsSave: (payload: { project: string; title: string; body: string }) =>
    call<{ ok: boolean; error?: string; id?: string }>('prompts.save', payload),
  promptsArchive: (payload: { id: string; project?: string }) =>
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
  genesAdopt: (gene: string, title?: string) =>
    call<GeneActionResult>('genes.adopt', title ? { gene, title } : { gene }),
  genesSuggest: () => call<GeneSuggestions>('genes.suggest'),
  genesExport: (path?: string) =>
    call<GeneActionResult>('genes.export', path ? { path } : {}),
  genesImport: (path: string) => call<GeneActionResult>('genes.import', { path }),
  copyFirstRun: () => call<CopyFirstRunSnapshot>('copy.first_run'),
  copyAnswer: (agree: boolean, mode?: CopyMode) =>
    call<GeneActionResult>('copy.answer', mode ? { agree, mode } : { agree }),
  copyMode: (mode: CopyMode) => call<GeneActionResult>('copy.mode', { mode })
}

/** Действия окна (не моста): перезапуск, метка сборки, состояние моста. */
export const desktop = {
  buildInfo: () => window.minigen.buildInfo(),
  relaunch: () => window.minigen.relaunch(),
  bridgeStatus: () => window.minigen.bridgeStatus()
}
