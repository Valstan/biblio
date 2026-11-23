import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'
import { existsSync } from 'fs'
import { join, resolve } from 'path'
import { app } from 'electron'

/**
 * Мост «МиниГена»: дочерний процесс Python, который говорит с ядром.
 *
 * Протокол — JSON-lines (одна строка JSON на запрос и на ответ). Мост
 * живёт всё время жизни окна: окно — оболочка, ядро — Python.
 */
export class Bridge {
  private proc: ChildProcessWithoutNullStreams | null = null
  private pending = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()
  private counter = 0
  private buffer = ''
  private ready = false

  constructor(
    private readonly onEvent: (kind: 'bridge-status' | 'bridge-error', message: string, alive: boolean) => void
  ) {}

  /** Папка с Python-мостом: в сборке — resources, при разработке — репозиторий. */
  static bridgeDir(): string {
    if (app.isPackaged) return join(process.resourcesPath, 'bridge')
    return resolve(__dirname, '../../bridge')
  }

  /** Кандидаты интерпретатора Python — по порядку. */
  static pythonCandidates(): string[] {
    const fromEnv = process.env.MINIGEN_PYTHON
    const list = fromEnv ? [fromEnv] : []
    return [...list, 'python', 'py']
  }

  get isReady(): boolean {
    return this.ready
  }

  start(): void {
    const dir = Bridge.bridgeDir()
    const script = join(dir, 'minigen_bridge.py')
    if (!existsSync(script)) {
      this.onEvent('bridge-error', `мост не найден: ${script}`, false)
      return
    }
    for (const candidate of Bridge.pythonCandidates()) {
      const args = candidate === 'py' ? ['-3', script] : [script]
      try {
        this.proc = spawn(candidate, args, {
          cwd: dir,
          windowsHide: true,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
        }) as ChildProcessWithoutNullStreams
        this.attach(this.proc, candidate)
        return
      } catch {
        /* пробуем следующего кандидата */
      }
    }
    this.onEvent('bridge-error', 'не найден интерпретатор Python', false)
  }

  private attach(proc: ChildProcessWithoutNullStreams, candidate: string): void {
    this.ready = true
    this.onEvent('bridge-status', `мост запущен (${candidate})`, true)
    proc.stdout.setEncoding('utf-8')
    proc.stdout.on('data', (chunk: string) => this.onData(chunk))
    proc.stderr.setEncoding('utf-8')
    proc.stderr.on('data', (chunk: string) => {
      const text = chunk.trim()
      if (text) this.onEvent('bridge-error', text.slice(0, 400), this.ready)
    })
    proc.on('exit', (code) => {
      this.ready = false
      this.onEvent('bridge-status', `мост завершился (код ${code ?? '—'})`, false)
      this.rejectAll(new Error('мост остановлен'))
    })
    proc.on('error', (err) => {
      this.ready = false
      this.onEvent('bridge-error', String(err.message || err), false)
    })
  }

  private onData(chunk: string): void {
    this.buffer += chunk
    let index = this.buffer.indexOf('\n')
    while (index >= 0) {
      const line = this.buffer.slice(0, index).trim()
      this.buffer = this.buffer.slice(index + 1)
      if (line) this.handleLine(line)
      index = this.buffer.indexOf('\n')
    }
  }

  private handleLine(line: string): void {
    let msg: { id?: string; ok?: boolean; data?: unknown; error?: string }
    try {
      msg = JSON.parse(line)
    } catch {
      return
    }
    if (!msg.id) return
    const waiter = this.pending.get(msg.id)
    if (!waiter) return
    this.pending.delete(msg.id)
    if (msg.ok) waiter.resolve(msg.data)
    else waiter.reject(new Error(msg.error || 'ошибка моста'))
  }

  private rejectAll(err: Error): void {
    this.pending.forEach((waiter) => waiter.reject(err))
    this.pending.clear()
  }

  call(cmd: string, args: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.proc || !this.ready) {
      return Promise.reject(new Error('мост не запущен'))
    }
    const id = String(++this.counter)
    const payload = JSON.stringify({ id, cmd, args }) + '\n'
    return new Promise((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise })
      this.proc!.stdin.write(payload, (err) => {
        if (err) {
          this.pending.delete(id)
          rejectPromise(err)
        }
      })
    })
  }

  stop(): void {
    if (this.proc) {
      this.proc.stdin.end()
      this.proc.kill()
      this.proc = null
    }
    this.ready = false
  }
}
