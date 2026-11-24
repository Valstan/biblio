import { contextBridge, ipcRenderer } from 'electron'
import type { BridgeEvent } from '../shared/types'

/**
 * Мост окна и главного процесса. Окно не имеет прямого достапа к Node —
 * только эти три функции.
 */
const api = {
  /** Вызвать команду программы (тот же реестр, что у встроенной нейросети). */
  call(cmd: string, args: Record<string, unknown> = {}): Promise<unknown> {
    return ipcRenderer.invoke('minigen:call', cmd, args)
  },
  /** Открыть папку в проводнике. */
  openExternal(path: string): Promise<boolean> {
    return ipcRenderer.invoke('minigen:open-external', path)
  },
  /** Сохранить текст в файл (диалог выбора пути — в главном процессе). */
  saveText(name: string, content: string): Promise<{ ok: boolean; path: string; error?: string }> {
    return ipcRenderer.invoke('minigen:save-text', name, content)
  },
  /** Флаги проверки (дымовая проверка экспорта CSV). */
  smoke: {
    csv: process.env.MINIGEN_SMOKE_CSV || ''
  },
  /** Перезапустить окно (после обновления программы). */
  relaunch(): Promise<boolean> {
    return ipcRenderer.invoke('minigen:relaunch')
  },
  /** Из какой сборки запущено окно (коммит, вшитый при сборке). */
  buildInfo(): Promise<{ commit: string }> {
    return ipcRenderer.invoke('minigen:build-info')
  },
  /** Состояние моста прямо сейчас (событие могло прийти до подписки окна). */
  bridgeStatus(): Promise<{ alive: boolean; message: string }> {
    return ipcRenderer.invoke('minigen:bridge-status')
  },
  /** События моста: он упал / поднялся. */
  onEvent(handler: (event: BridgeEvent) => void): () => void {
    const listener = (_event: unknown, payload: BridgeEvent) => handler(payload)
    ipcRenderer.on('minigen:event', listener)
    return () => ipcRenderer.removeListener('minigen:event', listener)
  }
}

contextBridge.exposeInMainWorld('minigen', api)

export type MinigenApi = typeof api
