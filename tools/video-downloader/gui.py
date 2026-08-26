# -*- coding: utf-8 -*-
"""video-downloader GUI (Tkinter, stdlib). Тяжёлая работа — в фоновом потоке."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from download import default_download_dir, download_urls


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Скачать видео со страницы")
        root.geometry("640x480")
        root.minsize(520, 380)

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Ссылки на страницы (по одной в строке):").pack(anchor="w")
        self.urls = tk.Text(frm, height=5, wrap="none")
        self.urls.pack(fill="x", pady=(2, 8))

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Папка:").pack(side="left")
        self.out_var = tk.StringVar(value=str(default_download_dir()))
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Обзор…", command=self.pick_dir).pack(side="left")

        row2 = ttk.Frame(frm)
        row2.pack(fill="x", pady=(0, 8))
        self.btn = ttk.Button(row2, text="⬇ Скачать все видео", command=self.start)
        self.btn.pack(side="left")
        self.audio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="только аудио", variable=self.audio_var).pack(side="left", padx=10)
        self.pbar = ttk.Progressbar(row2, mode="determinate", maximum=1.0)
        self.pbar.pack(side="left", fill="x", expand=True, padx=6)

        self.log = tk.Text(frm, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

        self.q: queue.Queue = queue.Queue()
        self.root.after(100, self.drain)

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    def start(self):
        urls = [u for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            self.q.put(("log", "Вставьте хотя бы одну ссылку."))
            return
        self.btn.config(state="disabled")
        threading.Thread(target=self.work, args=(urls, self.out_var.get(),
                         self.audio_var.get()), daemon=True).start()

    def work(self, urls, out_dir, audio):
        try:
            ok, err = download_urls(
                urls, out_dir,
                log=lambda s: self.q.put(("log", s)),
                progress=lambda f: self.q.put(("prog", f)),
                audio_only=audio,
            )
            self.q.put(("log", f"— Готово: скачано {ok}, ошибок {err}. Папка: {out_dir}"))
        except Exception as e:
            self.q.put(("log", f"✖ Сбой: {e}"))
        self.q.put(("done", None))

    def drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.config(state="normal")
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    self.log.config(state="disabled")
                elif kind == "prog":
                    self.pbar["value"] = val
                elif kind == "done":
                    self.btn.config(state="normal")
                    self.pbar["value"] = 0
        except queue.Empty:
            pass
        self.root.after(100, self.drain)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
