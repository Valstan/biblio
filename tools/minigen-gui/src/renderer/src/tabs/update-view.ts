import type { ProgramVersion, UpdateState } from '@shared/types'

/** Что окно может сказать владельцу про обновление. */
export interface UpdateVerdict {
  tone: 'ok' | 'warn' | 'bad'
  text: string
  /** Окно работает на старом коде — нужен перезапуск. */
  needRestart: boolean
}

/**
 * Время установки программы — для строки «Установлено на месте».
 *
 * ⚠️ `installed_at` приходит в **секундах** (так пишет раскатка: `int(time.time())`),
 * а `new Date(...)` ждёт миллисекунды ⇒ без поправки на экране было
 * «21.01.1970, 21:45:10» (живой случай 24.11). Значения меньше 1e12 считаем
 * секундами, большие — миллисекундами (на всякий случай).
 */
export function formatInstalledAt(seconds: number | null | undefined): string {
  if (!seconds) return ''
  const ms = seconds < 1e12 ? seconds * 1000 : seconds
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('ru-RU')
}

/**
 * Главный вопрос про обновление: «у меня новая версия или нет?»
 *
 * ⚠️ Раскатка меняет файлы **на диске**, а уже запущенное окно продолжает
 * работать на том коде, с которого стартовало. Поэтому сравниваем метку
 * сборки окна (коммит, вшитый в бандл при сборке) с установленной версией
 * (`version.json` рядом с программой):
 *
 * - метки совпали ⇒ окно и файлы одной версии;
 * - разошлись ⇒ **на диске новее, чем в окне** ⇒ нужен перезапуск.
 *
 * Без этого сравнения владелец видит «новую» версию в настройках и не
 * понимает, почему поведение старое (живой случай 24.11: окно, открытое
 * до раскатки, продолжало работать на старом коде).
 */
export function describeUpdate(
  installed: ProgramVersion | null,
  buildCommit: string,
  state: UpdateState | null
): UpdateVerdict {
  if (state?.running) {
    return { tone: 'warn', text: 'Идёт обновление — шаги ниже.', needRestart: false }
  }
  const installedCommit = installed?.commit || ''
  if (!installedCommit) {
    return { tone: 'bad', text: 'Версию программы определить не удалось.', needRestart: false }
  }
  if (buildCommit && buildCommit !== installedCommit) {
    return {
      tone: 'warn',
      text: `На диске версия ${installedCommit}, а окно собрано из ${buildCommit} — перезапусти программу, иначе она продолжит работать на старом коде.`,
      needRestart: true
    }
  }
  const behind = state?.available?.behind ?? 0
  if (behind > 0) {
    return {
      tone: 'warn',
      text: `Доступна новая версия ${state?.available?.commit || '—'} (${behind} комм. новых) — обнови программу.`,
      needRestart: false
    }
  }
  return { tone: 'ok', text: `Программа актуальна (${installedCommit}).`, needRestart: false }
}
