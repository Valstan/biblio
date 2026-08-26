# biblio

Склад тестовых альфа- и бета-разработок владельца (~2019–2022) **и дом desktop-утилит
экосистемы** ([D-048](https://github.com/Valstan/brain_matrica), 2026-08-26).

Правила работы агентов — [AGENTS.md](AGENTS.md). Каталог ниже собран аудитом
2026-08-26; статус проставлен по коду, а не по памяти.

## Живое: desktop-утилиты — [`tools/`](tools/)

Единственная часть репо, которая развивается. Каркас «CLI-ядро + GUI + install.ps1 +
`Запустить.cmd`», тяжёлое (venv, модели) — gitignored. Подробности — [tools/README.md](tools/README.md).

| Утилита | Что делает |
|---|---|
| [audio-to-text](tools/audio-to-text/) | Русская речь → текст локально на CPU (GigaAM v2 + Silero), оффлайн |
| [video-downloader](tools/video-downloader/) | Скачать все видео со страницы (yt-dlp + ffmpeg из pip) |
| [net-monitor](tools/net-monitor/) | Трей Windows: жив ли интернет (IP и DNS раздельно), статус VPN, смена DNS |
| [kaspersky-matrica](tools/kaspersky-matrica/) | Помощник по исключениям Kaspersky для клиента Матрицы |

## Музей: старые наработки

⚠️ Ниже — **не рабочий код**, а источник идей. Почти всё завязано на отсутствующие в
репо `config.py` / `bases/logpass.py` (там жили секреты) и на API 2019–2021 годов:
парольная авторизация VK закрыта, `instabot` заблокирован Instagram, `Image.ANTIALIAS`
удалён в Pillow 10. Не чинить попутно — доделка это отдельная задача.

### Полка идей (что реально стоит переиспользовать)

| Идея | Где смотреть |
|---|---|
| **Конвейер фильтров контента**: композиция независимых `sort_*` над потоком постов + дедуп + ранжирование по просмотрам | [moduls/parser.py](moduls/parser.py) — лучшее в наборе |
| **Дедуп картинок по хешу гистограммы** — дёшево ловит копии с разными URL (база 50 МБ → 500 КБ) | [moduls/sort/sort_po_foto.py](moduls/sort/sort_po_foto.py), [history_postopus/version.txt](history_postopus/version.txt) |
| **OCR текста на картинке + чёрный список** — отсев рекламных баннеров (`--oem 3 --psm 6`, `lang='rus'`) | [moduls/utils/tesseract.py](moduls/utils/tesseract.py) |
| **Дедуп постов по тексту И вложениям** — ловит репосты с изменённой подписью | [moduls/sort/sort_double.py](moduls/sort/sort_double.py) |
| **Кольцевой буфер публикаций в JSON** — защита от повторов без БД | [moduls/main_program.py](moduls/main_program.py), [moduls/aprel.py](moduls/aprel.py) |
| **Нормализация репостов VK** до оригинала с сохранением метрик обёртки | [moduls/utils/clear_copy_history.py](moduls/utils/clear_copy_history.py) |
| **Letterboxing под соцсети**: вписать + отцентрировать на холсте + водяной знак | [moduls/utils/resize_img.py](moduls/utils/resize_img.py), [white_board.py](moduls/utils/white_board.py), [draw_text.py](moduls/utils/draw_text.py) |
| **Регистронезависимое вырезание подстрок** с сохранением регистра остального текста (параллельная lower-копия) | [moduls/utils/correct_txt.py](moduls/utils/correct_txt.py) |
| **Парсер cron-подобных диапазонов + адаптивный sleep** — лёгкий планировщик без cron | [history_postopus/old_schedule.py](history_postopus/old_schedule.py) |
| **`secrets.compare_digest`** вместо `==` при сравнении хэшей паролей (timing-атаки) | [instructions/hash_parol_python.txt](instructions/hash_parol_python.txt) |
| **Рецепт Flask за gunicorn за nginx** через unix-сокет + systemd + certbot | `instructions/install_new_server.txt` (не в git, см. ниже) |
| **Агрегация мелких объявлений в один сводный пост** вместо публикации каждого | [moduls/read_write/post_bezfoto.py](moduls/read_write/post_bezfoto.py) |

### Что где лежит

| Каталог / файл | Содержимое |
|---|---|
| [`moduls/`](moduls/) | Ядро VK-автопостера «Малмыж Инфо»: диспетчер [ruletka.py](moduls/ruletka.py), конвейер [parser.py](moduls/parser.py), публикация [main_program.py](moduls/main_program.py), репостеры (aprel, krugozor, reklama, repost_me), кросспост в Instagram |
| [`moduls/read_write/`](moduls/read_write/) | Обёртки VK API: сессия, чтение стены, `wall.post`, конвертация attachments, JSON-хранилище |
| [`moduls/sort/`](moduls/sort/) | Фильтры конвейера: свежесть, чёрный список, дубли, фото/без фото, дедуп по картинке |
| [`moduls/utils/`](moduls/utils/) | Картинки (resize, холст, водяной знак, OCR), чистка текста, атрибуция источника |
| [`history_postopus/`](history_postopus/) | Предыдущее поколение того же бота + [version.txt](history_postopus/version.txt) — **чейнджлог 1.0→4.4 с историей решений** (2019–2020) |
| [`instructions/`](instructions/) | Шпаргалки по разворачиванию Debian-сервера: пользователь, локаль, ufw+nginx, python, certbot |
| [`sumatra/`](sumatra/) | Учебный эксперимент: импортированный dict как разделяемое состояние между модулями |
| Корень: [pribil.py](pribil.py) | Парсер выгрузки сделок крипто-бота → график дневной прибыли (pandas + matplotlib) |
| Корень: [rating_posts_1000.py](rating_posts_1000.py), [stat_group_vk.py](stat_group_vk.py) | Аналитика VK: рейтинг постов по 4 метрикам, аудит аудитории («мёртвые души», география) |
| Корень: [shelling.py](shelling.py) | Модель сегрегации Шеллинга на tkinter (есть баг с перепутанными осями) |
| Корень: [clear_simbol.py](clear_simbol.py), [cortext.py](cortext.py) | Два подхода к чистке текста объявлений: словарь подстрок и regex |
| Корень: [instagram.py](instagram.py), [whatsapp_push.py](whatsapp_push.py) | Автопостинг в Instagram (instabot мёртв), пуш в WhatsApp через pywhatkit |

## Данные вне git

Часть файлов лежит на диске, но **не трекается** — там были секреты и личные данные
(аудит 2026-08-26): `config/` (сессия instabot), `pribil.txt` (история крипто-сделок),
`pywhatkit_dbs.txt`, `history_postopus/config.json`, а также `instructions/`-файлы с
SSH-координатами серверов. Репозиторий публичный — см. раздел про recon-поверхность
в [AGENTS.md](AGENTS.md).
