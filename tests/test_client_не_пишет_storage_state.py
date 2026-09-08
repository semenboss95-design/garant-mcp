# -*- coding: utf-8 -*-
"""`client.py` больше не пишет `storage_state.json` (спецификация нового
поведения, наряд этапа 03, фаза B, часть 2).

Единственное хранилище сессии — профиль браузера целиком (ограничение 1
CLAUDE.md, решение о снимке cookie на диск отменено): `chrome_profile`
сохраняет себя сам, а отдельный файл с cookie не читал никто и
`garant uninstall --purge` его не удалял, оставляя на диске файл с живыми
cookie подписки без всякой пользы (комментарий в `client.close()`).

Свойство, которое здесь охраняется, — ОТСУТСТВИЕ вызова Playwright
`storage_state(...)` (запись cookie на диск отдельным файлом) в коде,
владеющем браузерным контекстом. Живого прогона это не требует и не
может: браузер в этой среде не поднимается (нет подписки, нет профиля).
Статическая проверка — единственная, которая здесь достижима, и она же
достаточна как регрессионный сторож: код, вернувший запись
`storage_state.json`, снова содержал бы литерал вызова.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_SRC = ROOT / "src" / "garant_mcp"

# `.storage_state(` — вызов Playwright, которым контекст СОХРАНЯЕТ cookie
# в файл. `storage_state.json` — само имя прежнего файла-снимка. Любое
# из двух — признак того, что снимок вернулся.
_ПРИЗНАК = re.compile(r"\.storage_state\(|storage_state\.json")


def test_ни_один_модуль_пакета_не_пишет_storage_state():
    находки = []
    for f in sorted(PKG_SRC.glob("*.py")):
        текст = f.read_text(encoding="utf-8")
        for n, строка in enumerate(текст.splitlines(), 1):
            if _ПРИЗНАК.search(строка):
                находки.append("{}:{}: {}".format(f.name, n, строка.strip()))
    assert not находки, (
        "снимок cookie storage_state.json вернулся — единственное "
        "хранилище сессии обязано быть профилем браузера целиком "
        "(ограничение 1 CLAUDE.md):\n" + "\n".join(находки))
