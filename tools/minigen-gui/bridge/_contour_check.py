"""Живая проверка: перезапуск контура через программу «МиниГен».

Запускает НАСТОЯЩИЙ restart_contour() из боевого файла сетевого сторожа
(после раскатки) и смотрит, дошёл ли он до программы (API :8097) и
перезапустил ли лечащего и сторожа сессий.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "nw_live", r"C:\Users\valstan\bin\minimax-netwatch.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["x"]
spec.loader.exec_module(mod)
print("адресаты:", [url for url, _ in mod.contour_targets()])
print("restart_contour ->", mod.restart_contour())
