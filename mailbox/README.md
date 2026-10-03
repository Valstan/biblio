# mailbox — исходящие письма biblio → brain

Кладём сюда `to-brain/YYYY-MM-DD-slug.md` с frontmatter: `from`, `to`, `date`, `kind`
(`idea` | `directive` | `question` | `feedback` | `report` — виды контура v3), опционально
`compliance`, `urgency` и `ref:` на full-slug письма, на которое отвечаем. Письмо остаётся
в PR и после слияния. Кодировка: UTF-8 без BOM.

Входящие для biblio у brain-репозитория **теперь есть** (открыто 2026-10-04, D-107):
читаем их из `../brain_matrica/mailboxes/biblio/from-brain/` (read-only, только на чтение).

Безопасность: в письмах не светим длинные числа токенов и хостнеймы-координаты (D-038).
