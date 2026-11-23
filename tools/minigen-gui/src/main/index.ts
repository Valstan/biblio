import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { writeFileSync } from 'fs'
import { join } from 'path'
import { Bridge } from './bridge'

/**
 * «МиниГен» — одно окно вместо браузера и набора скриптов.
 *
 * Окно не делает работу само: оно спрашивает Python-мост и показывает ответ.
 * Мост, в свою очередь, зовёт боевые демоны — поэтому логика сторожей
 * остаётся в одном месте.
 *
 * Два режима одной программы:
 * - **окно** — владелец смотрит состояние и общается с нейросетью;
 * - **без окна** (`--daemon-mode`) — автозапуск при входе в Windows: окна
 *   нет, но демоны «МиниГена» подняты и следятся. Старая панель с её
 *   супервизором архивирована, и теперь эту роль держит программа.
 */

const daemonMode = process.argv.includes('--daemon-mode')

if (daemonMode) {
  // Отдельный профиль: иначе включится блокировка одного экземпляра и
  // окно владельца не откроется (или откроется вместо фонового режима).
  app.setPath('userData', join(app.getPath('appData'), 'minigen-gui-daemon'))
}

let mainWindow: BrowserWindow | null = null
let bridge: Bridge | null = null

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 680,
    title: 'МиниГен',
    backgroundColor: '#101418',
    show: false,
    skipTaskbar: daemonMode,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    // В режиме без окна (автозапуск при входе в Windows) окно невидимо.
    if (!daemonMode) mainWindow?.show()
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  bridge = new Bridge((kind, message, alive) => {
    mainWindow?.webContents.send('minigen:event', { kind, message, alive })
  })
  bridge.start()

  ipcMain.handle('minigen:call', async (_event, cmd: string, args: Record<string, unknown>) => {
    if (!bridge) throw new Error('мост не запущен')
    return bridge.call(cmd, args ?? {})
  })

  ipcMain.handle('minigen:open-external', async (_event, path: string) => {
    shell.openPath(path)
    return true
  })

  ipcMain.handle('minigen:save-text', async (_event, name: string, content: string) => {
    const result = await dialog.showSaveDialog({
      defaultPath: name,
      filters: [{ name: 'CSV', extensions: ['csv'] }]
    })
    if (result.canceled || !result.filePath) return { ok: false, path: '' }
    try {
      writeFileSync(result.filePath, content, 'utf8')
      return { ok: true, path: result.filePath }
    } catch (err) {
      return { ok: false, path: result.filePath, error: String((err as Error).message) }
    }
  })

  createWindow()

  // Дымовая проверка: окно поднялось → мост ответил → состояние пришло.
  // Включается переменной MINIGEN_SMOKE=1, окно закрывается само.
  if (process.env.MINIGEN_SMOKE) {
    mainWindow?.webContents.once('did-finish-load', () => {
      setTimeout(async () => {
        try {
          const data = (await bridge?.call('state.snapshot')) as Record<string, unknown>
          const areas = (data?.areas as unknown[]) ?? []
          console.log('SMOKE OK: областей в снимке =', areas.length)
          // Экспорт CSV: окно собирает CSV из ЖИВЫХ данных (тот же код, что
          // и кнопка «Экспорт CSV»), главный процесс пишет файл тем же
          // обработчиком, что и диалог сохранения. Проверяется всё, кроме
          // самого окна выбора пути (это делает Windows).
          const csvPath = process.env.MINIGEN_SMOKE_CSV
          if (csvPath) {
            const csv = (await mainWindow!.webContents.executeJavaScript(
              'window.__minigenSmokeCsv || ""')) as string
            if (csv) {
              writeFileSync(csvPath, csv, 'utf8')
              console.log('SMOKE CSV: записан', csvPath, csv.length, 'символов')
            } else {
              console.log('SMOKE CSV: окно не собрало CSV')
            }
          }
        } catch (err) {
          console.log('SMOKE FAIL:', String((err as Error).message || err))
        }
        app.exit(0)
      }, 4000)
    })
  }

  if (daemonMode) {
    // Режим без окна: программа живёт и держит демонов. Окно скрыто, но
    // существует — иначе Electron завершился бы (нет окон — нет процесса).
    console.log('МиниГен: режим без окна — демоны под наблюдением')
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  // В режиме без окна закрывать нечего: программа продолжает держать демонов.
  if (daemonMode) return
  bridge?.stop()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  bridge?.stop()
})
