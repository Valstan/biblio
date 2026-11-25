/**
 * Вкладки окна «МиниГена» — список в одном месте.
 *
 * Вынесено отдельным модулем, чтобы тест мог проверить, что вкладка
 * «Агенты» **есть** в окне (заявка владельца от 24.11: «вкладка
 * „Агенты“ в окне»).
 */

export type TabId =
  | 'areas'
  | 'stats'
  | 'network'
  | 'chat'
  | 'journal'
  | 'genes'
  | 'agents'
  | 'prompts'
  | 'update'
  | 'settings'

export interface TabDef {
  id: TabId
  label: string
}

export const TABS: TabDef[] = [
  { id: 'areas', label: 'Области' },
  { id: 'stats', label: 'Статистика' },
  { id: 'network', label: 'Сеть' },
  { id: 'chat', label: 'Управляющий' },
  { id: 'journal', label: 'Журнал' },
  { id: 'genes', label: 'Гены' },
  { id: 'agents', label: 'Агенты' },
  { id: 'prompts', label: 'Промты' },
  { id: 'update', label: 'Обновление' },
  { id: 'settings', label: 'Настройки' }
]
