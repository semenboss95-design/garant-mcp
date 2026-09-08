# -*- coding: utf-8 -*-
"""Уборка браузера — в потоке-владельце, а не в главном (этап 03).

До правки `client.close()` вызывался из `finally` в `main()` — то есть из
главного потока, ПОСЛЕ того как рабочий поток уже мог быть остановлен.
Объекты Playwright привязаны к создавшему их потоку: закрытие «не своим»
потоком — не гарантия, а надежда. Теперь `client.close()` переехал в
`finally` метода `BrowserWorker.run()` и исполняется САМИМ потоком-
владельцем (`garant-browser`), а `main()` лишь ждёт его через `join()`.

Проверяется здесь настоящий `BrowserWorker` с подставным клиентом —
подмена коллаборатора демона (`self.client`), а не подмена того, что
разбирает ответ «Гаранта» (граница подмены для клиента — `Transport.fetch`,
решение 17, и этой проверки она не касается: это про демон, а не про
`client.py`). Реальный код очереди задач, потока и его `finally` исполняется
по-настоящему.

Процесс свой (как в `test_демон_честность_сессии.py`): импорт `daemon.py`
на уровне модуля открывает лог-хендлер в каталог состояния (`delay=True`,
но каталог обязан существовать при первой записи), поэтому демон
импортируется в подпроцессе с отдельным `GARANT_HOME`, а не в процессе
pytest, где это загрязнило бы настоящий каталог состояния разработчика.

Два свойства:
1. `close()` подставного клиента вызван из потока с именем
   `"garant-browser"`, а не из главного потока подпроцесса.
2. Тот же `close()`, поднимающий исключение, не мешает потоку завершиться,
   и обязан быть пойман ИМЕННО там, где стоит: если внутренний
   `try/except` вокруг `self.client.close()` в `run()` пропадёт, брошенное
   исключение оборвёт `finally` ДО строки `self.client = None` — и клиент,
   переживший неудачное закрытие, останется висеть на `worker.client`,
   вместо того чтобы быть сброшенным. Это наблюдаемая, а не косвенная
   разница: одного «поток мёртв» здесь недостаточно — поток threading
   считается завершённым, даже когда `run()` вышел необработанным
   исключением, поэтому регресс ловит именно `client is None`, а не
   `is_alive()` сам по себе.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

_СКРИПТ = r'''
import sys, json, threading
sys.path.insert(0, r"{site}")
import garant_mcp.paths as paths
paths.ensure_state()
import garant_mcp.daemon as daemon

РЕЗУЛЬТАТ = {{}}


class _ПодставнойКлиент:
    """Стоит на месте GarantClient. close() фиксирует имя потока-вызывающего
    и, если попросили, бросает исключение — вместо реального закрытия
    Playwright-контекста."""

    def __init__(self, кидать=False):
        self.поток_закрытия = None
        self.кидать = кидать

    def _keepalive(self, force=True):
        # Такт демона на _last_ka == 0.0 вызывает keepalive уже на ПЕРВОМ
        # обороте цикла — подставной клиент обязан на это отвечать, иначе
        # такт упадёт до того, как мы вообще дойдём до остановки.
        return "HTTP 200 (тест)"

    def close(self):
        self.поток_закрытия = threading.current_thread().name
        if self.кидать:
            raise RuntimeError("закрытие сломано (тест)")


def сценарий(кидать):
    w = daemon.BrowserWorker(keepalive_sec=10**9)
    клиент = _ПодставнойКлиент(кидать=кидать)
    w.client = клиент
    w.start()
    w._остановлен.set()
    w.join(timeout=5)
    return {{
        "поток_жив": w.is_alive(),
        "поток_закрытия": клиент.поток_закрытия,
        "client_сброшен": w.client is None,
    }}


РЕЗУЛЬТАТ["обычное_закрытие"] = сценарий(кидать=False)
РЕЗУЛЬТАТ["закрытие_бросает"] = сценарий(кидать=True)

print(json.dumps(РЕЗУЛЬТАТ, ensure_ascii=False))
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
        "сценарий BrowserWorker.close() упал:\n" + r.stdout + r.stderr)
    строки = [s for s in r.stdout.strip().splitlines() if s.startswith("{")]
    assert строки, "не удалось прочитать результат:\n" + r.stdout + r.stderr
    return json.loads(строки[-1])


@pytest.fixture(scope="module")
def р(tmp_path_factory) -> dict:
    состояние = tmp_path_factory.mktemp("состояние")
    return _выполнить(состояние)


def test_close_вызывается_из_потока_владельца_а_не_из_главного(р):
    assert р["обычное_закрытие"]["поток_закрытия"] == "garant-browser", (
        "client.close() был вызван не из потока garant-browser — уборка "
        "браузера уехала из потока-владельца, а Playwright-объекты "
        "привязаны к своему потоку."
    )
    assert р["обычное_закрытие"]["поток_жив"] is False
    assert р["обычное_закрытие"]["client_сброшен"] is True


def test_close_бросающий_исключение_не_мешает_потоку_завершиться_и_client_сбрасывается(р):
    исход = р["закрытие_бросает"]
    assert исход["поток_закрытия"] == "garant-browser", (
        "даже в сценарии со сломанным close() вызов обязан был произойти "
        "из потока-владельца."
    )
    assert исход["поток_жив"] is False, (
        "поток BrowserWorker не завершился за 5 с после того, как "
        "client.close() бросил исключение — run() обязан пережить это "
        "исключение и дойти до конца finally."
    )
    assert исход["client_сброшен"] is True, (
        "self.client не сброшен в None после того, как close() бросил "
        "исключение — значит, внутренний try/except вокруг client.close() "
        "в BrowserWorker.run() пропал: необработанное исключение обрывает "
        "finally ДО строки `self.client = None`, и сломанный клиент "
        "остаётся висеть на worker.client."
    )
