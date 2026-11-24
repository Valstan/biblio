import { execSync } from 'child_process'
import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

/**
 * Коммит, из которого собрано окно, — вшивается в бандл.
 *
 * ⚠️ Зачем (заказ владельца 24.11): раскатка меняет файлы программы **на
 * диске**, а уже открытое окно продолжает работать на старом коде. Владелец
 * видел «новую» версию в настройках и не понимал, почему поведение старое.
 * С меткой сборки сравнение честное: окно собрано из `<коммит>`, на диске
 * стоит `<коммит>` — расхождение видно сразу, и окно предлагает перезапуск.
 */
function buildCommit(): string {
  try {
    return execSync('git rev-parse --short HEAD', {
      cwd: __dirname,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore']
    }).trim()
  } catch {
    return ''
  }
}

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    define: {
      'process.env.MINIGEN_COMMIT': JSON.stringify(buildCommit())
    },
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/main/index.ts') }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/preload/index.ts') }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    resolve: {
      alias: {
        '@shared': resolve(__dirname, 'src/shared'),
        '@': resolve(__dirname, 'src/renderer/src')
      }
    },
    plugins: [react()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/renderer/index.html') }
      }
    }
  }
})
