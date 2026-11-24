import type { LineageInfo } from '@shared/types'

/**
 * Родословная копии — **по версии программы видно, кто это и от кого форк**
 * (заказ владельца 24.11: «версию программы бы нормальную сделать, по которой
 * бы визуально можно было определить родословную, по генам, чья это родня, у
 * каких соседей он брал гены»).
 *
 * Данные — из `lineage.show` моста: локальная родословная копии
 * (`instance.json`): идентификатор, семейство, поколение, от какой версии
 * форк, кто родитель по гену, какие гены приняты.
 *
 * Функция чистая — её можно проверить тестом.
 */
export function lineageRows(lineage: LineageInfo | null): { label: string; value: string }[] {
  if (!lineage) return []
  const instance = lineage.instance || {}
  const parent = instance.parent || {}
  const genes = instance.genes || []
  const rows: { label: string; value: string }[] = []

  rows.push({ label: 'Копия', value: instance.instance_id || '—' })
  rows.push({ label: 'Семейство', value: instance.family || 'minigen' })
  rows.push({ label: 'Поколение', value: String(instance.generation || 1) })

  const fork = instance.fork_of || {}
  if (fork.commit) {
    rows.push({
      label: 'Форк от версии',
      value: `${fork.commit}${fork.installed_at ? ` · установлено ${new Date(
        fork.installed_at * 1000
      ).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })}` : ''}`
    })
  }
  if (parent.gene) {
    const from = parent.instance ? ` (от ${parent.instance})` : ''
    const commit = parent.commit ? ` @ ${parent.commit}` : ''
    rows.push({ label: 'Родитель по гену', value: `${parent.gene}${from}${commit}` })
  }
  rows.push({ label: 'Генов принято', value: String(genes.length) })
  if (instance.copy_repo) {
    rows.push({ label: 'Репозиторий копии', value: instance.copy_repo })
  }
  if (instance.upstream) {
    rows.push({ label: 'Источник (upstream)', value: instance.upstream })
  }
  return rows
}

/** Короткая строка для версии: «mg-693f8fdd · поколение 2». */
export function lineageShort(lineage: LineageInfo | null): string {
  if (!lineage) return ''
  const instance = lineage.instance || {}
  if (!instance.instance_id) return ''
  return `${instance.instance_id} · поколение ${instance.generation || 1}`
}
