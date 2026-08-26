#!/usr/bin/env python3
"""audio-to-text — графическая оболочка (Tkinter, stdlib): пакетная обработка.

Накидай несколько аудиофайлов (кнопкой или перетаскиванием) → они по очереди
распознаются в фоне → у каждого свой блок текста. Блоки можно пометить галочкой
и «Копировать выбранные» (объединить), либо копировать каждый блок отдельно.
Окно можно не закрывать — вернулся и забрал текст.

Кнопки действий пришпилены к низу окна (`side="bottom"`), поэтому видны при любом
размере/DPI-масштабе.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from transcribe import DEFAULT_MODEL, Transcriber, split_for_bot

MODELS = ["v2_rnnt", "v2_ctc", "v1_rnnt", "v1_ctc"]


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: "queue.Queue[tuple[int, str, str]]" = queue.Queue()
        self.jobs: "queue.Queue[tuple[int, str, str, bool]]" = queue.Queue()
        self.cards: dict[int, dict] = {}
        self._seq = 0
        self._tr: Transcriber | None = None
        self._tr_model: str | None = None

        root.title("Аудио → текст (локально, GigaAM) — пакетная обработка")
        root.geometry("820x640")
        root.minsize(600, 460)

        self._build_toolbar()
        self._build_bottom()   # ВАЖНО: низ пакуем раньше центра → всегда виден
        self._build_scroll()

        self._enable_dnd()
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._drain)

    # ---- разметка --------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="➕ Добавить файлы…", command=self.add_files).pack(side="left")
        ttk.Label(bar, text="Модель:").pack(side="left", padx=(12, 4))
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        ttk.Combobox(bar, textvariable=self.model_var, values=MODELS,
                     width=10, state="readonly").pack(side="left")
        self.punct_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Пунктуация и абзацы",
                        variable=self.punct_var).pack(side="left", padx=12)
        ttk.Button(bar, text="✂ Нарезать для бота SaluteSpeech…",
                   command=self.split_action).pack(side="right")
        self.status = ttk.Label(self.root, anchor="w", padding=(10, 0),
                                text="Перетащите аудио в окно или нажмите «Добавить файлы».")
        self.status.pack(side="top", fill="x")

    def _build_bottom(self) -> None:
        bottom = ttk.Frame(self.root, padding=(10, 8))
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="📋 Копировать выбранные",
                   command=self.copy_selected).pack(side="left")
        ttk.Button(bottom, text="💾 Сохранить выбранные…",
                   command=self.save_selected).pack(side="left", padx=6)
        ttk.Button(bottom, text="☑ Выбрать все",
                   command=lambda: self.select_all(True)).pack(side="left")
        ttk.Button(bottom, text="☐ Снять все",
                   command=lambda: self.select_all(False)).pack(side="left", padx=6)
        ttk.Button(bottom, text="🗑 Очистить",
                   command=self.clear_all).pack(side="right")

    def _build_scroll(self) -> None:
        wrap = ttk.Frame(self.root)
        wrap.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, padding=6)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    # ---- drag-and-drop (опционально) ------------------------------------
    def _enable_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore

            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event) -> None:
        paths = self.root.tk.splitlist(event.data)
        self.add_files([p for p in paths if os.path.isfile(p)])

    # ---- добавление файлов ----------------------------------------------
    def add_files(self, paths=None) -> None:
        if paths is None:
            paths = filedialog.askopenfilenames(
                title="Выберите аудиофайлы",
                filetypes=[("Аудио", "*.ogg *.mp3 *.wav *.m4a *.flac *.opus *.aac *.wma"),
                           ("Все файлы", "*.*")],
            )
        model = self.model_var.get()
        punct = self.punct_var.get()
        for path in paths:
            job_id = self._make_card(path)
            self.jobs.put((job_id, path, model, punct))
        if paths:
            self.status.config(
                text=f"В очереди: {self.jobs.qsize()} | всего блоков: {len(self.cards)}")

    def _make_card(self, path: str) -> int:
        self._seq += 1
        jid = self._seq
        card = ttk.Frame(self.inner, relief="groove", borderwidth=1, padding=6)
        card.pack(fill="x", pady=4, padx=2)

        head = ttk.Frame(card)
        head.pack(fill="x")
        sel = tk.BooleanVar(value=True)
        ttk.Checkbutton(head, variable=sel).pack(side="left")
        ttk.Label(head, text=os.path.basename(path), font=("Segoe UI", 10, "bold")
                  ).pack(side="left", padx=(2, 8))
        status = ttk.Label(head, text="в очереди", foreground="#888")
        status.pack(side="left")
        ttk.Button(head, text="✕", width=3,
                   command=lambda: self._remove(jid)).pack(side="right")
        ttk.Button(head, text="📋 копировать", command=lambda: self._copy_card(jid)
                   ).pack(side="right", padx=4)

        text = tk.Text(card, wrap="word", height=5, font=("Segoe UI", 11), undo=True)
        text.pack(fill="x", pady=(6, 0))

        self.cards[jid] = {"frame": card, "status": status, "text": text,
                           "sel": sel, "path": path}
        return jid

    # ---- фоновый воркер --------------------------------------------------
    def _worker(self) -> None:
        while True:
            jid, path, model, punct = self.jobs.get()
            if jid not in self.cards:        # удалили до обработки
                continue
            try:
                prog = lambda m, j=jid: self.events.put((j, "status", m))
                if self._tr is None or self._tr_model != model:
                    prog("Загрузка модели…")
                    self._tr = Transcriber(model, progress=prog)
                    self._tr_model = model
                text = self._tr.transcribe_file(path, punctuate=punct, progress=prog)
                self.events.put((jid, "done", text))
            except Exception as exc:  # noqa: BLE001
                self.events.put((jid, "error", str(exc)))

    def _drain(self) -> None:
        try:
            while True:
                jid, kind, payload = self.events.get_nowait()
                if jid == 0:                       # глобальные события (нарезка)
                    self._drain_global(kind, payload)
                    continue
                card = self.cards.get(jid)
                if card is None:
                    continue
                if kind == "status":
                    card["status"].config(text=payload, foreground="#0a6")
                elif kind == "done":
                    card["text"].delete("1.0", "end")
                    card["text"].insert("1.0", payload)
                    n = len(payload.split())
                    card["status"].config(text=f"✓ готово · {n} слов", foreground="#080")
                    self._refresh_status()
                elif kind == "error":
                    card["status"].config(text="✗ ошибка", foreground="#c00")
                    messagebox.showerror("Ошибка распознавания",
                                         f"{card['path']}\n\n{payload}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _refresh_status(self) -> None:
        done = sum(1 for c in self.cards.values()
                   if c["status"].cget("text").startswith("✓"))
        self.status.config(
            text=f"Готово {done}/{len(self.cards)} | в очереди: {self.jobs.qsize()}")

    # ---- нарезка для бота SaluteSpeech ----------------------------------
    def split_action(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Выберите аудио для нарезки под бота SaluteSpeech",
            filetypes=[("Аудио", "*.ogg *.mp3 *.wav *.m4a *.flac *.opus *.aac *.wma"),
                       ("Все файлы", "*.*")],
        )
        if paths:
            threading.Thread(target=self._do_split, args=(list(paths),),
                             daemon=True).start()

    def _do_split(self, paths: list[str]) -> None:
        total, last_dir = 0, None
        for p in paths:
            try:
                self.events.put((0, "gstatus", f"Нарезка: {os.path.basename(p)}…"))
                parts = split_for_bot(p, progress=lambda m: self.events.put((0, "gstatus", m)))
                total += len(parts)
                if parts:
                    last_dir = os.path.dirname(parts[0])
            except Exception as exc:  # noqa: BLE001
                self.events.put((0, "error", f"{os.path.basename(p)}: {exc}"))
        self.events.put((0, "split_done", f"{total}\n{last_dir or ''}"))

    def _drain_global(self, kind: str, payload: str) -> None:
        if kind == "gstatus":
            self.status.config(text=payload, foreground="#0a6")
        elif kind == "error":
            self.status.config(text="Ошибка нарезки.")
            messagebox.showerror("Ошибка нарезки", payload)
        elif kind == "split_done":
            total, _, folder = payload.partition("\n")
            self.status.config(text=f"Нарезано фрагментов: {total} → {folder}")
            if folder and messagebox.askyesno(
                    "Готово", f"Нарезано {total} фрагм. в:\n{folder}\n\nОткрыть папку?"):
                try:
                    os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606
                except Exception:
                    pass

    # ---- действия над блоками -------------------------------------------
    def _ordered(self) -> list[dict]:
        """Карточки в порядке отображения."""
        order = {str(w): i for i, w in enumerate(self.inner.winfo_children())}
        return sorted(self.cards.values(), key=lambda c: order.get(str(c["frame"]), 1e9))

    def _selected_texts(self) -> list[str]:
        out = []
        for c in self._ordered():
            if c["sel"].get():
                t = c["text"].get("1.0", "end").strip()
                if t:
                    out.append(t)
        return out

    def copy_selected(self) -> None:
        texts = self._selected_texts()
        if not texts:
            self.status.config(text="Нечего копировать — отметь блоки галочками.")
            return
        self._to_clipboard("\n\n".join(texts))
        self.status.config(text=f"Скопировано блоков: {len(texts)} (объединены).")

    def _copy_card(self, jid: int) -> None:
        c = self.cards.get(jid)
        if not c:
            return
        t = c["text"].get("1.0", "end").strip()
        if t:
            self._to_clipboard(t)
            self.status.config(text=f"Скопирован блок: {os.path.basename(c['path'])}")

    def _to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def save_selected(self) -> None:
        texts = self._selected_texts()
        if not texts:
            self.status.config(text="Нечего сохранять — отметь блоки галочками.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Текст", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(texts))
            self.status.config(text=f"Сохранено: {path}")

    def select_all(self, value: bool) -> None:
        for c in self.cards.values():
            c["sel"].set(value)

    def _remove(self, jid: int) -> None:
        c = self.cards.pop(jid, None)
        if c:
            c["frame"].destroy()
        self._refresh_status()

    def clear_all(self) -> None:
        if self.cards and not messagebox.askyesno("Очистить", "Удалить все блоки?"):
            return
        for c in list(self.cards.values()):
            c["frame"].destroy()
        self.cards.clear()
        self.status.config(text="Очищено.")


def main() -> None:
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore

        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
