/**
 * Логика вкладки «Агенты» — чистая, без React и без сети.
 *
 * Здесь то, что показывает окно: строки агентов, состояние канала
 * КАРМАНА и проверки нового агента. Всё это проверяется тестом
 * (`tests/agents-view.test.ts`) — правило владельца: приёмка правилом, а
 * не «на глаз».
 */

import type { AgentRecord, AgentsSnapshot, Area, KarmanChannel } from '@shared/types'

/** Строка агента для таблицы. */
export interface AgentRow {
  slug: string
  title: string
  role: string
  repo: string
  area: string
  skills: string[]
  requests: number
  /** Чего не хватает в манифесте (пусто — манифест полный). */
  missing: string[]
}

/**
 * Строка объединённого списка: **агент и его область — одно и то же**
 * (заказ владельца 28.11: «Области» и «Агенты» — одно целое, это «Агенты»).
 *
 * ⚠️ Ключ строки — **имя области**. Агент без области — тоже строка (видно,
 * что область не заведена), и область без агента — тоже строка (обычная
 * рабочая область).
 */
export interface MergedRow {
  /** Имя области — ключ строки. */
  area: string
  /** Агент из реестра (если есть). */
  agent: AgentRow | null
  /** Живая область из снимка состояния (если есть). */
  live: Area | null
  title: string
  role: string
  repo: string
  skills: string[]
  requests: number
  missing: string[]
  /** Агент есть, а области в списке — нет: область не заведена. */
  areaMissing: boolean
}

export type Tone = 'ok' | 'warn' | 'bad'

/** Состояние канала — словами и цветом. */
export interface ChannelView {
  tone: Tone
  label: string
  detail: string
  sections: string[]
}

/** Черновик нового агента (форма в окне). */
export interface AgentDraft {
  slug: string
  title: string
  role: string
}

const DASH = '—'

/**
 * Имя области агента: **без** префикса `p-`.
 *
 * ⚠️ Каталог данных области называется `p-<имя>`, а в списке — имя. Мост
 * уже отдаёт имя (живой случай 28.11 — манифест агента «Идеи» лежал с
 * `area: p-ideas`, и окно искало область `p-ideas`, которой нет), но
 * старый манифест может ещё встретиться — поэтому префикс срезается и
 * здесь.
 */
export function areaNameOf(agent: AgentRecord): string {
  const raw = String(agent?.area ?? '').trim()
  return raw.startsWith('p-') ? raw.slice(2) : raw
}

/** Строки агентов: манифест + число заявок. */
export function agentRows(snapshot: AgentsSnapshot | null): AgentRow[] {
  if (!snapshot) return []
  const agents = snapshot.agents ?? []
  return agents.map((agent: AgentRecord) => {
    const slug = (agent.slug || '').trim()
    const requests = agent.requests?.length ?? 0
    const missing: string[] = []
    if (!agent.title) missing.push('название')
    if (!agent.role) missing.push('роль')
    if (!areaNameOf(agent)) missing.push('область')
    return {
      slug,
      title: (agent.title || '').trim() || slug || DASH,
      role: (agent.role || '').trim() || DASH,
      repo: (agent.repo || '').trim() || DASH,
      area: areaNameOf(agent) || slug,
      skills: agent.skills ?? [],
      requests,
      missing
    }
  })
}

/**
 * Объединённый список: агенты реестра и живые области — **одна таблица**.
 *
 * ⚠️ Живой случай 28.11: в окне было две вкладки — «Области» и «Агенты» —
 * и они показывали разное. Агент, созданный в «Агентах», не появлялся в
 * «Рабочих областях» (дефект записи конфига), и владелец решал, что
 * программа не работает. Теперь список один: строка — это область, а
 * агент (если он есть) — её содержание.
 *
 * Порядок: сначала живые области (в том порядке, который дал вызывающий —
 * он сортирует), потом агенты без области (их области не заведены).
 */
export function mergedRows(areas: Area[], snapshot: AgentsSnapshot | null): MergedRow[] {
  const agents = agentRows(snapshot)
  const byArea = new Map<string, AgentRow>()
  for (const agent of agents) {
    const key = (agent.area || '').trim()
    if (key && !byArea.has(key)) byArea.set(key, agent)
  }
  const rows: MergedRow[] = []
  const seen = new Set<string>()
  for (const area of areas ?? []) {
    const slug = (area.slug || '').trim()
    if (!slug) continue
    seen.add(slug)
    const agent = byArea.get(slug) ?? null
    rows.push({
      area: slug,
      agent,
      live: area,
      title: agent?.title || area.title || slug,
      role: agent?.role ?? '',
      repo: agent?.repo ?? '',
      skills: agent?.skills ?? [],
      requests: agent?.requests ?? 0,
      missing: agent?.missing ?? [],
      areaMissing: false
    })
  }
  for (const agent of agents) {
    const key = (agent.area || '').trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    rows.push({
      area: key,
      agent,
      live: null,
      title: agent.title,
      role: agent.role,
      repo: agent.repo,
      skills: agent.skills,
      requests: agent.requests,
      missing: agent.missing,
      areaMissing: true
    })
  }
  return rows
}

/**
 * Состояние канала КАРМАНА.
 *
 * Три честных состояния: жив, настроен но не отвечает, не настроен
 * (нет секрета канала). Пустое состояние — «неизвестно», а не «жив».
 */
export function channelView(channel: KarmanChannel | null): ChannelView {
  if (!channel) {
    return {
      tone: 'bad',
      label: 'канал не спросили',
      detail: 'мост не ответил — состояние канала неизвестно',
      sections: []
    }
  }
  const sections = channel.sections ?? []
  if (channel.ok) {
    return {
      tone: 'ok',
      label: 'канал живой',
      detail: `разделов: ${sections.length} · секрет из ${channel.secret_source || DASH}`,
      sections
    }
  }
  return {
    tone: channel.has_secret ? 'warn' : 'bad',
    label: channel.has_secret ? 'канал не отвечает' : 'канал не настроен',
    detail: channel.reason || 'причина неизвестна',
    sections
  }
}

/** Сводка по реестру: где он и сколько агентов. */
export function registrySummary(snapshot: AgentsSnapshot | null): string {
  if (!snapshot) return 'реестр неизвестен'
  const name = snapshot.registry || 'реестр агентов'
  if (!snapshot.registry_cloned) {
    return `${name}: не склонирован на этой машине`
  }
  const count = snapshot.agents?.length ?? 0
  return `${name}: ${count} ${agentsWord(count)} · рабочее место ${snapshot.workspace || DASH}`
}

/** Русское склонение: 1 агент, 2 агента, 5 агентов. */
function agentsWord(count: number): string {
  if (count === 1) return 'агент'
  if (count >= 2 && count <= 4) return 'агента'
  return 'агентов'
}

/** Баннер о расхождении локальной копии реестра с GitHub. */
export function registryWarning(snapshot: AgentsSnapshot | null): string {
  const state = snapshot?.registry_state
  if (!state || !state.dirty) return ''
  return state.why || 'локальная копия реестра отличается от GitHub'
}

/** Проверка черновика нового агента: список претензий (пусто — можно). */
export function validateDraft(draft: AgentDraft): string[] {
  const problems: string[] = []
  const slug = (draft.slug || '').trim().toLowerCase()
  if (!slug) {
    problems.push('нужен slug — короткое имя латиницей')
  } else if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
    problems.push('slug: только латиница, цифры, дефис и подчёркивание')
  }
  if (!(draft.title || '').trim()) {
    problems.push('нужно название агента')
  }
  if (!(draft.role || '').trim()) {
    problems.push('нужна роль — чем агент занимается')
  }
  return problems
}

/**
 * Что скажет окно перед созданием агента.
 *
 * Создание агента **меняет внешний мир**: заводит репозиторий на GitHub,
 * область и запись в реестре — поэтому подтверждение обязательно, и в
 * нём должны быть названы последствия.
 */
export function createConfirmText(draft: AgentDraft): string {
  const slug = (draft.slug || '').trim().toLowerCase()
  const title = (draft.title || '').trim() || slug
  return (
    `Создать агента «${title}» (${slug})?\n\n` +
    `Это создаст на GitHub репозиторий Valstan/${slug}, заведёт область ` +
    `${slug} (каталог p-${slug}), поставит конституцию и скиллы и запишет ` +
    `агента в реестр.\n\n` +
    'Продолжить?'
  )
}

/**
 * Текст подтверждения восстановления агента.
 *
 * ⚠️ Называем **имя** области и её каталог: имя — ключ списка, а каталог
 * данных всегда `p-<имя>` (живой случай 28.11 — манифест агента «Идеи»
 * лежал с `area: p-ideas`, и области `p-ideas` не существовало).
 */
export function applyConfirmText(row: AgentRow): string {
  return (
    `Восстановить агента «${row.title}» на этой машине?\n\n` +
    `Программа склонирует репозиторий ${row.repo}, поставит конституцию и ` +
    `скиллы и заведёт область ${row.area} (каталог p-${row.area}), если её нет.\n\n` +
    'Продолжить?'
  )
}
