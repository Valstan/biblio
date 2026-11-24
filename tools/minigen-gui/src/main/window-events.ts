/**
 * Безопасная отправка события в окно «МиниГена».
 *
 * ⚠️ Урок 24.11: главный процесс падал при закрытии окна —
 * «Uncaught Exception: TypeError: Object has been destroyed
 *  at Bridge.event (main/index.js:162)». Причина: `mainWindow?.webContents.send(...)`
 * проверяет только `null`, но НЕ проверяет, что окно уже уничтожено.
 * Закрытие окна ⇒ `window-all-closed` ⇒ `bridge.stop()` ⇒ дочерний Python
 * процесс умирает и шлёт событие «мост завершился» — в уже мёртвое окно.
 *
 * Здесь три проверки подряд: окно есть, окно живо, webContents жив. Плюс
 * `try/catch` на саму отправку: окно может умереть между проверкой и
 * вызовом. Отправка в мёртвое окно — не ошибка программы, а обычная
 * гонка при закрытии, и она не должна ронять главный процесс.
 */

export interface WindowLike {
  isDestroyed(): boolean
  webContents?: {
    isDestroyed(): boolean
    send(channel: string, payload: unknown): void
  }
}

/**
 * Отправить событие в окно, если оно ещё живо.
 *
 * @returns `true`, если событие ушло; `false` — окно закрыто (это не ошибка).
 */
export function sendToWindow(
  win: WindowLike | null | undefined,
  channel: string,
  payload: unknown
): boolean {
  if (!win) return false
  try {
    if (win.isDestroyed()) return false
    const contents = win.webContents
    if (!contents || contents.isDestroyed()) return false
    contents.send(channel, payload)
    return true
  } catch {
    // Окно умерло между проверкой и отправкой — молча пропускаем.
    return false
  }
}
