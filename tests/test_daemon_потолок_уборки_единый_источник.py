# -*- coding: utf-8 -*-
"""`daemon.ПОТОЛОК_УБОРКИ_СЕК` — не собственный литерал, а чтение из
`protocol.py` (решение 33, `docs/DECISIONS.md`, запись 33).

До решения 33 число (10) было зашито дважды: в `daemon.py` и, отдельно,
в комментарии-обещании «пульт ждёт дольше». Совпадение двух литералов
держалось на внимательности и один раз уже не удержалось бы молча —
поэтому единственный источник теперь один: `daemon.py` читает
`protocol.ПОТОЛОК_УБОРКИ_СЕК`, не объявляя своего числа.

Проверка НЕ сравнивает два числа на равенство (`daemon.X ==
protocol.X`) — это ничего не доказывает: `daemon.py` мог бы независимо
зашить то же самое число 10, и такое сравнение было бы зелёным и на
регрессе. Проверяется ПОВЕДЕНИЕ единого источника: `protocol.ПОТОЛОК_
УБОРКИ_СЕК` подменяется на заведомо неправдоподобное число ДО импорта
`daemon.py` — если `daemon.py` при импорте и правда читает его оттуда
(`ПОТОЛОК_УБОРКИ_СЕК = protocol.ПОТОЛОК_УБОРКИ_СЕК`), подменённое число
доедет и до `daemon.ПОТОЛОК_УБОРКИ_СЕК`. Если у `daemon.py` завёлся
собственный литерал, подмена никак на него не повлияет, и тест это
поймает.

Процесс свой, как в `test_демон_честность_сессии.py`: импорт `daemon.py`
на уровне модуля открывает лог-хендлер (`delay=True`) в каталог
состояния, который обязан существовать, — поэтому демон импортируется
в подпроцессе с отдельным `GARANT_HOME`, а не в процессе pytest, где это
загрязнило бы настоящий каталог состояния разработчика.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

# Заведомо неправдоподобное число: ни один настоящий потолок остановки
# (десятки секунд) не выведет из вычитания такое значение — совпадение
# с ним по случайности исключено.
СЕНТИНЕЛ = 999123

_СКРИПТ = r'''
import sys, json
sys.path.insert(0, r"{site}")
import garant_mcp.paths as paths
paths.ensure_state()
import garant_mcp.protocol as protocol
protocol.ПОТОЛОК_УБОРКИ_СЕК = {сентинел}
import garant_mcp.daemon as daemon
print(json.dumps({{"daemon": daemon.ПОТОЛОК_УБОРКИ_СЕК,
                   "protocol": protocol.ПОТОЛОК_УБОРКИ_СЕК}}))
'''


def _выполнить(состояние: Path) -> dict:
    import os

    r = subprocess.run(
        [sys.executable, "-c",
         _СКРИПТ.format(site=str(SRC), сентинел=СЕНТИНЕЛ)],
        cwd=str(состояние.parent),
        env={"GARANT_HOME": str(состояние), "PATH": os.environ.get("PATH", ""),
             "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert r.returncode == 0, (
        "сценарий импорта daemon.py упал:\n" + r.stdout + r.stderr)
    строки = [s for s in r.stdout.strip().splitlines() if s.startswith("{")]
    assert строки, "не удалось прочитать результат:\n" + r.stdout + r.stderr
    return json.loads(строки[-1])


def test_daemon_читает_потолок_уборки_из_protocol_а_не_свой_литерал(tmp_path):
    состояние = tmp_path / "состояние"
    р = _выполнить(состояние)

    assert р["daemon"] == СЕНТИНЕЛ, (
        "daemon.ПОТОЛОК_УБОРКИ_СЕК={}, а не подставленный в protocol.py "
        "СЕНТИНЕЛ={}: daemon.py при импорте не прочитал текущее значение "
        "protocol.ПОТОЛОК_УБОРКИ_СЕК, а держит собственное число — "
        "единственный источник (решение 33) снова раздвоился.".format(
            р["daemon"], СЕНТИНЕЛ)
    )
    assert р["protocol"] == СЕНТИНЕЛ, "подмена в самом protocol.py не удалась"
