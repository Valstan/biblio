import type {
  Area,
  ChatReply,
  JournalLine,
  NetworkState,
  Settings,
  Snapshot,
  Stats
} from '@shared/types'

declare global {
  interface Window {
    minigen: {
      call(cmd: string, args?: Record<string, unknown>): Promise<unknown>
      openExternal(path: string): Promise<boolean>
      saveText(name: string, content: string): Promise<{ ok: boolean; path: string; error?: string }>
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
  openFolder: (path: string) => call<{ ok: boolean; error?: string }>('app.open_folder', { path })
}
