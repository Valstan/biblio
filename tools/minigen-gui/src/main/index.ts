import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { statSync, writeFileSync } from 'fs'
import { basename, join } from 'path'
import { Bridge } from './bridge'
import { sendToWindow } from './window-events'
import { clickTabScript, mapGeometryScript, parseSmokeTabs, shotName } from './smoke'

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
} else if (process.env.MINIGEN_SMOKE) {
  // Дымовая приёмка окна — тоже отдельный профиль: проверка идёт вторым
  // экземпляром, пока окно владельца живо, а профиль держит блокировку
  // одного экземпляра (Chromium). Без этого проверка не запустилась бы.
  app.setPath('userData', join(app.getPath('appData'), 'minigen-gui-smoke'))
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
    if (!daemonMode && mainWindow && !mainWindow.isDestroyed()) mainWindow.show()
  })
  // Окно закрыто — ссылка на него больше не годится: события моста нельзя
  // слать в уничтоженное окно (главный процесс падал именно на этом).
  mainWindow.on('closed', () => {
    mainWindow = null
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
    // ⚠️ Не `mainWindow?.webContents.send(...)`: окно может быть уже
    // уничтожено (закрытие окна ⇒ мост умирает ⇒ шлёт последнее событие),
    // и тогда главный процесс падал с «Object has been destroyed».
    sendToWindow(mainWindow, 'minigen:event', { kind, message, alive })
  })
  bridge.start()

  ipcMain.handle('minigen:call', async (_event, cmd: string, args: Record<string, unknown>) => {
    if (!bridge) throw new Error('мост не запущен')
    return bridge.call(cmd, args ?? {})
  })

  ipcMain.handle('minigen:bridge-status', async () =>
    bridge?.status() ?? { alive: false, message: 'мост не запущен' })

  ipcMain.handle('minigen:open-external', async (_event, path: string) => {
    shell.openPath(path)
    return true
  })

  // Выбор файлов для чата (промт #90): диалог открывает главный процесс,
  // окно получает настоящие пути и на диск само не ходит.
  ipcMain.handle('minigen:pick-files', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections']
    })
    if (result.canceled) return []
    return result.filePaths.map((path) => {
      let size = 0
      try {
        size = statSync(path).size
      } catch {
        size = 0
      }
      return { path, name: basename(path), size }
    })
  })

  // Перезапуск окна: раскатка меняет файлы на диске, а запущенное окно
  // продолжает работать на старом коде. Поэтому перезапуск — отдельное
  // действие, его видно владельцу (вкладка «Обновление»).
  ipcMain.handle('minigen:relaunch', async () => {
    app.relaunch()
    app.quit()
    return true
  })

  // Метка сборки: коммит, вшитый в окно при сборке. По ней видно, что окно
  // запущено из старой сборки, а на диске уже новая.
  ipcMain.handle('minigen:build-info', async () => ({
    commit: process.env.MINIGEN_COMMIT || ''
  }))

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
          // ── снимки вкладок: видимый текст и скриншот ───────────────────
          // «Приёмка окна глазами» (24.11): окно само показывает, что на
          // вкладке есть — приёмка читает это и проверяет маркеры.
          const labels = parseSmokeTabs(process.env.MINIGEN_SMOKE_TABS)
          if (labels.length && mainWindow && !mainWindow.isDestroyed()) {
            const textPath = process.env.MINIGEN_SMOKE_TEXT
            const shotsDir = process.env.MINIGEN_SMOKE_SHOTS
            const texts: Record<string, { text: string; map: Record<string, unknown> | null }> = {}
            for (const [index, label] of labels.entries()) {
              const clicked = (await mainWindow.webContents.executeJavaScript(
                clickTabScript(label))) as string
              if (!String(clicked).startsWith('ок')) {
                console.log('SMOKE TAB: вкладка не открылась —', label, '—', clicked)
                continue
              }
              // вкладка тянет свои данные с моста — дать им прийти
              // ⚠️ 4 с, а не 1,5: снимок состояния идёт ~1,5 с (три вызова
              // PowerShell на демонов), и данные вкладки встают в очередь за
              // ним ⇒ на 1,5 с приёмка снимала пустые поля (живой случай
              // 24.11: «Версия: неизвестна», «Родословная не прочитана»).
              await new Promise((resolve) => setTimeout(resolve, 4000))
              const text = (await mainWindow.webContents.executeJavaScript(
                'document.body.innerText')) as string
              const geometry = (await mainWindow.webContents.executeJavaScript(
                mapGeometryScript())) as Record<string, unknown> | null
              texts[label] = { text: String(text || ''), map: geometry }
              if (geometry) {
                console.log('SMOKE MAP:', label, 'обрезана:', geometry.ok ? 'нет' : 'ДА',
                  JSON.stringify(geometry))
              }
              if (shotsDir) {
                const image = await mainWindow.webContents.capturePage()
                const file = join(shotsDir, shotName(label, index))
                writeFileSync(file, image.toPNG())
                console.log('SMOKE SHOT:', file)
              }
            }
            if (textPath) {
              writeFileSync(textPath, JSON.stringify(texts, null, 2), 'utf8')
              console.log('SMOKE TEXT: записан', textPath, 'вкладок:', Object.keys(texts).length)
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
