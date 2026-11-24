import { execSync } from 'child_process'
import { mkdirSync, writeFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'

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

/** Ветка, из которой собрано окно (для честного отчёта). */
function buildBranch(): string {
  try {
    return execSync('git rev-parse --abbrev-ref HEAD', {
      cwd: __dirname,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore']
    }).trim()
  } catch {
    return ''
  }
}

/**
 * Метка сборки **рядом с бандлом** (`out/build-info.json`).
 *
 * ⚠️ Зачем (живой случай 24.11): окно и «версия на месте» должны описывать
 * одно и то же. Раньше раскатка писала в `version.json` коммит рабочего
 * дерева, а окно несло метку той сборки, что лежит на диске. Стоило
 * закатить только документы (без пересборки) — и «версия на месте» уезжала
 * вперёд, а окно оставалось на старом коде: владелец вечно видел «на диске
 * новее, окно старое», а перезапуск не помогал (перезапускался тот же
 * бандл). Теперь версия ставится **из самого бандла** — расхождение невозможно
 * по построению.
 */
function buildInfoPlugin(): Plugin {
  return {
    name: 'minigen-build-info',
    closeBundle() {
      try {
        const file = resolve(__dirname, 'out/build-info.json')
        mkdirSync(dirname(file), { recursive: true })
        writeFileSync(
          file,
          JSON.stringify(
            {
              commit: buildCommit(),
              branch: buildBranch(),
              built_at: new Date().toISOString()
            },
            null,
            2
          ),
          'utf-8'
        )
      } catch {
        /* метка не записалась — раскатка возьмёт коммит рабочего дерева */
      }
    }
  }
}

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin(), buildInfoPlugin()],
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
