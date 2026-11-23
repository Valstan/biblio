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
 */

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
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  mainWindow.on('ready-to-show', () => mainWindow?.show())
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
        } catch (err) {
          console.log('SMOKE FAIL:', String((err as Error).message || err))
        }
        app.exit(0)
      }, 1500)
    })
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  bridge?.stop()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  bridge?.stop()
})
