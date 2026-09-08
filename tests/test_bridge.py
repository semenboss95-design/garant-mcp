# -*- coding: utf-8 -*-
"""Мост создаётся только по флагу (этап 01, шаг 4 → перенос на пакет, этап 02).

Пункт 4 критерия приёмки: без `GARANT_BRIDGE` каталог `bridge/` не
появляется. Проверяется цепочка целиком — `config.BRIDGE` → `daemon.МОСТ`
→ `paths.ensure_state(bridge=…)`, — а не одно её звено: разойтись эти
три могут только молча.

Единственное звено, которого тест не касается, — то, что `daemon.main()`
передаёт в `ensure_state` именно `МОСТ`. Вызвать `main()` здесь нельзя:
она занимает порт и поднимает браузер. Зато эта строка стоит в первых
строках `main()` и видна глазом, а всё, что ниже неё, тестом закрыто.

Каждый запуск — отдельным процессом: `config` читает окружение на импорте,
и в одном процессе второе значение флага уже не получить.

Тест ходит напрямую в `src/garant_mcp` (не в копию-песочницу): здесь не
меряется появление каталогов рядом с КОДОМ (это забота test_paths.py),
а только появление `bridge/` внутри отдельного `GARANT_HOME` на каждый
запуск — трогать оригинал безопасно.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_paths import ENV_КОНТУРА  # единственный источник — не дублируем

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

КОД = """
import sys, json
sys.path.insert(0, r'{src}')
import garant_mcp.config as config
import garant_mcp.daemon as daemon
import garant_mcp.paths as paths
paths.ensure_state(bridge=daemon.МОСТ)
print(json.dumps({{
    'config': bool(config.BRIDGE),
    'daemon': bool(daemon.МОСТ),
    'каталог': paths.bridge_dir.exists(),
    'state': str(paths.STATE),
}}))
"""


def _запуск(состояние, доп=None):
    e = dict(os.environ)
    for k in ENV_КОНТУРА:
        e.pop(k, None)
    e["GARANT_HOME"] = str(состояние)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    if доп:
        e.update(доп)
    r = subprocess.run(
        [sys.executable, "-c", КОД.format(src=SRC)],
        cwd=str(состояние.parent), env=e, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        pytest.fail("garant_mcp.daemon не импортировался:\n" + r.stderr.strip())
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_без_переменной_моста_нет(tmp_path):
    состояние = tmp_path / "состояние"
    r = _запуск(состояние)
    assert r["config"] is False, "умолчание bridge в config.py перестало быть False"
    assert r["daemon"] is False, (
        "daemon.МОСТ разошёлся с config.BRIDGE — флаг читается, но не "
        "переключает")
    assert r["каталог"] is False, (
        "каталог моста создан без GARANT_BRIDGE. Мост — второй, безтокенный "
        "вход в демона; пустые requests/responses у того, кто мостом не "
        "пользуется, это приглашение положить туда файл.")
    assert (состояние / "logs").is_dir(), (
        "выключенный мост не должен мешать созданию остального состояния")


def test_с_переменной_мост_поднимается(tmp_path):
    """Выключается поведение, а не возможность: код моста цел и включаем."""
    состояние = tmp_path / "состояние"
    r = _запуск(состояние, {"GARANT_BRIDGE": "1"})
    assert r["daemon"] is True
    assert r["каталог"] is True, "GARANT_BRIDGE=1 не поднимает мост"
    assert (состояние / "bridge" / "requests").is_dir()
    assert (состояние / "bridge" / "responses").is_dir()


def test_ложные_значения_не_включают_мост(tmp_path):
    """`GARANT_BRIDGE=false` не должна означать «включено»."""
    for значение in ("0", "false", "no", "off", "нет"):
        r = _запуск(tmp_path / ("состояние_" + значение),
                    {"GARANT_BRIDGE": значение})
        assert r["каталог"] is False, (
            "GARANT_BRIDGE={} включила мост".format(значение))
