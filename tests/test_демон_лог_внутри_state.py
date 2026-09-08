# -*- coding: utf-8 -*-
"""Сторож на класс дефекта Д-В1 (изоляция тестов от состояния владельца,
08.09.2026): `daemon.py:129` — `log = setup_log()` — вешает
`logging.handlers.RotatingFileHandler(LOG_DIR / "daemon.log", delay=True)`
на путь, вычисленный `paths.log_dir` в момент ИМПОРТА `garant_mcp.daemon`.

Найденный дефект был не в `daemon.py`, а в тестах: `tests/test_probe_
тип_сессии.py` импортировал `garant_mcp.daemon` прямо в процессе pytest,
без `GARANT_HOME`, — `paths.log_dir` в этом процессе указывал на настоящий
каталог состояния ОС, и `_probe()` (через `_set_session`) дописывал туда
`log.warning("СЕССИЯ: недействительна…")` на каждый прогон всего набора.
Тест исправлен переносом в подпроцесс (`tests/test_probe_тип_сессии.py`,
по образцу `tests/test_демон_честность_сессии.py`).

Этот файл не повторяет ту правку — он проверяет свойство КОДА, которое и
делает такую изоляцию осмысленной: если `GARANT_HOME` выставлена ДО
импорта, `daemon.LOG_DIR` обязан лежать ВНУТРИ него, а не рядом с кодом
и не в каталоге данных ОС. Не будь этого свойства верным, никакая
дисциплина «импортируй демона только в подпроцессе со своим GARANT_HOME»
тестам бы не помогла: подпроцесс писал бы владельцу ровно так же.

`delay=True` прячет открытие файла до первой записи (это отдельно и
намеренно, см. `daemon.py:114-117` и `tests/test_paths.py::
test_импорт_не_создаёт_каталогов` — импорт САМ ПО СЕБЕ каталогов не
создаёт). Поэтому здесь после импорта делается настоящая запись —
`daemon.log.warning(...)` — и проверяется, где в итоге лёг файл: только
запись обнажает адрес, куда указывает делегированный, но ещё не открытый
хендлер.

Подпроцесс, как во всех проверках побочных эффектов импорта
(`tests/test_paths.py`, докстринг «Каждый импорт исполняется в отдельном
процессе: импорт кэшируется, и второй раз побочных эффектов уже не
будет»).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

_СКРИПТ = r'''
import sys, json
from pathlib import Path
sys.path.insert(0, r"{site}")
import garant_mcp.paths as paths
paths.ensure_state()
import garant_mcp.daemon as daemon

# Настоящая запись, не просто импорт: delay=True открывает файл только на
# первом log.<уровень>() — ровно тем вызовом, что и уличил владельческий
# лог в дефекте Д-В1 (log.warning из _set_session).
daemon.log.warning("сторож Д-В1: тестовая запись")

log_dir = Path(daemon.LOG_DIR)
лог_файл = log_dir / "daemon.log"
state = Path(paths.STATE)

# Сырые пути, БЕЗ сравнения друг с другом здесь: log_dir и state — оба
# вычислены в этом же (потенциально испорченном) процессе из одной и той
# же paths.STATE, и сравнение log_dir с state доказало бы только то, что
# daemon.py не разошёлся сам с собой. Если _определить_state() проигнорирует
# GARANT_HOME и вернёт каталог данных ОС, log_dir всё равно окажется
# «внутри» такого state — сторож обязан сверяться с ЗАДАННЫМ GARANT_HOME,
# который знает вызывающий тест, а не с тем, что о себе думает подпроцесс.
print(json.dumps({{
    "log_dir": str(log_dir),
    "state": str(state),
    "файл_создан": лог_файл.is_file(),
    "файл_путь": str(лог_файл) if лог_файл.is_file() else None,
}}))
'''


def _выполнить(состояние: Path) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", _СКРИПТ.format(site=str(SRC))],
        cwd=str(состояние.parent),
        env={"GARANT_HOME": str(состояние), "PATH": os.environ.get("PATH", ""),
             "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert r.returncode == 0, (
        "импорт daemon.py и запись в лог упали:\n" + r.stdout + r.stderr)
    строки = [s for s in r.stdout.strip().splitlines() if s.startswith("{")]
    assert строки, "не удалось прочитать результат:\n" + r.stdout + r.stderr
    return json.loads(строки[-1])


def test_лог_демона_создаётся_внутри_garant_home(tmp_path):
    """Пре-условие для всей дисциплины «демон — только в подпроцессе со
    своим GARANT_HOME»: подпроцесс сам по себе не спасает, если
    `daemon.LOG_DIR` игнорирует GARANT_HOME и всё равно указывает наружу.

    Сравнение — с GARANT_HOME, который здесь и задан (`состояние`), а НЕ
    с `paths.STATE`, о котором отчитывается сам подпроцесс: `daemon.LOG_DIR`
    вычисляется как `paths.STATE / "logs"` в том же процессе, и если бы
    `_определить_state()` проигнорировала GARANT_HOME и подставила каталог
    данных ОС, `LOG_DIR` всё равно оказался бы «внутри» такого — уже
    неверного — `STATE`. Только сверка с известным заранее значением
    ловит эту подмену.
    """
    состояние = tmp_path / "состояние"   # не создаём заранее — ensure_state() обязана сама
    р = _выполнить(состояние)

    ожидаемое = состояние.resolve()
    log_dir = Path(р["log_dir"]).resolve()
    state = Path(р["state"]).resolve()

    assert state == ожидаемое, (
        "paths.STATE внутри подпроцесса ({}) разошёлся с заданным GARANT_HOME "
        "({}) — вычисление каталога состояния проигнорировало переменную "
        "окружения".format(р["state"], состояние))
    assert log_dir.is_relative_to(ожидаемое), (
        "daemon.LOG_DIR ({}) не лежит внутри заданного GARANT_HOME ({}) — "
        "запись лога ушла бы мимо изолированного каталога состояния, ровно "
        "как это случилось с владельческим логом при дефекте Д-В1".format(
            р["log_dir"], состояние))
    assert р["файл_создан"] is True, (
        "лог-файл не появился вовсе — сценарий записи не сработал, "
        "проверка ничего не доказывает")
    файл = Path(р["файл_путь"]).resolve() if р["файл_путь"] else None
    assert файл is not None and файл.is_relative_to(ожидаемое), (
        "daemon.log создан ВНЕ заданного GARANT_HOME ({}) — то же "
        "наблюдаемое следствие, что нашла независимая проверка чистоты "
        "машины 08.09.2026".format(состояние))


def test_импорт_без_записи_не_создаёт_лог_файл(tmp_path):
    """Контраст: САМ импорт (без явного log.*) файла не создаёт — delay=True
    действительно откладывает открытие до первой записи. Если бы это было
    не так, любой in-process импорт `daemon.py` в тестах уже писал бы
    владельцу на голом `import`, а не только там, где код реально логирует
    (как это было с `_probe()` → `_set_session` в исходном дефекте)."""
    состояние = tmp_path / "состояние"
    состояние.mkdir(parents=True)

    код = (
        "import sys, json\n"
        "sys.path.insert(0, r'{site}')\n"
        "import garant_mcp.paths as paths\n"
        "paths.ensure_state()\n"
        "import garant_mcp.daemon as daemon\n"
        "лог = daemon.LOG_DIR / 'daemon.log'\n"
        "print(json.dumps({{'файл_после_импорта': лог.is_file()}}))\n"
    ).format(site=str(SRC))
    r = subprocess.run(
        [sys.executable, "-c", код],
        cwd=str(состояние.parent),
        env={"GARANT_HOME": str(состояние), "PATH": os.environ.get("PATH", ""),
             "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert r.returncode == 0, "импорт daemon.py упал:\n" + r.stdout + r.stderr
    строки = [s for s in r.stdout.strip().splitlines() if s.startswith("{")]
    р = json.loads(строки[-1])

    assert р["файл_после_импорта"] is False, (
        "простой импорт daemon.py уже создал daemon.log — delay=True "
        "перестал прятать открытие файла до первой записи, значит, "
        "любой in-process импорт демона в тестах теперь пишет владельцу "
        "на голом import")
    # ensure_state() создаёт пустые logs/ и cache/ (это её задача и предмет
    # test_paths.py) — здесь важно только то, что среди появившегося нет
    # ни одного daemon.log, где бы он ни лёг.
    найденные_логи = list(состояние.rglob("daemon.log"))
    assert найденные_логи == [], (
        "простой импорт создал daemon.log без единой записи: " +
        str(найденные_логи))
