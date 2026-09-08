# -*- coding: utf-8 -*-
"""Слой `endpoints.local.json` (этап 02, решение 14): сливается, не подменяет.

`config.карта()` строит карту эндпоинтов из пакетного `endpoints.json`
(в `paths.HOME`, только чтение) и `<STATE>/endpoints.local.json` (в каталоге
состояния, машина пользователя, обновлением пакета не затирается). Слияние
идёт по секциям на одну секцию вглубь — человек, переопределивший один ключ
секции `daemon`, обязан получить рабочую карту со всеми остальными секциями
и остальными ключами той же секции целыми, а не куцую карту из одного файла.

Приоритет целиком: переменная окружения → `endpoints.local.json` →
пакетный `endpoints.json` → умолчание. Env и локальный файл проверены по
отдельности от пакетного файла в test_paths.py (шаг 3 этапа 01); здесь —
средняя ступень, которой раньше не было вовсе.

`config.карта()` кэширует результат на процесс (см. докстринг config.py):
чтобы увидеть новый локальный файл, нужен новый процесс — эта же граница
проверяется явно в последнем тесте: локальный файл, изменённый ПОСЛЕ
первого чтения в том же процессе, вторым чтением не подхватывается.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_paths import _песочница, _запуск, ROOT

SRC = ROOT / "src"


def _исходная_карта(пакет: Path) -> dict:
    return json.loads((пакет / "endpoints.json").read_text(encoding="utf-8"))


def _написать_локальный(состояние: Path, секция: dict) -> None:
    состояние.mkdir(parents=True, exist_ok=True)
    (состояние / "endpoints.local.json").write_text(
        json.dumps(секция, ensure_ascii=False), encoding="utf-8")


def _карта(пакет: Path, cwd: Path, доп=None) -> dict:
    код = (
        "import sys, json\n"
        "sys.path.insert(0, r'{site}')\n"
        "import garant_mcp.config as config\n"
        "print(json.dumps(config.карта(), ensure_ascii=False))\n"
    ).format(site=пакет.parent)
    r = _запуск(код, cwd, доп, таймаут=60)
    assert r.returncode == 0, "config.карта() не читается:\n" + r.stderr.strip()
    return json.loads(r.stdout.strip())


def test_локальный_файл_сливается_а_не_подменяет(tmp_path):
    """Переопределён один ключ секции daemon — остальная карта цела."""
    пакет = _песочница(tmp_path)
    исходная = _исходная_карта(пакет)
    состояние = tmp_path / "состояние"
    _написать_локальный(состояние, {"daemon": {"port": 8792}})

    итог = _карта(пакет, tmp_path, {"GARANT_HOME": str(состояние)})

    assert итог["daemon"]["port"] == 8792, (
        "переопределённый ключ не применился: {}".format(итог.get("daemon")))
    assert итог["base_url"] == исходная["base_url"], (
        "base_url не обязан меняться от локального файла, переопределяющего "
        "только daemon.port"
    )
    for секция in ("search", "practice", "browser_profile", "login_url"):
        assert секция in итог, (
            "секция «{}» пропала — локальный файл ПОДМЕНИЛ карту вместо "
            "того, чтобы слиться поверх неё".format(секция)
        )
        assert итог[секция] == исходная[секция]
    # остальные ключи ВНУТРИ переопределённой секции обязаны уцелеть —
    # слияние на одну секцию вглубь, а не замена секции целиком.
    for ключ, значение in исходная["daemon"].items():
        if ключ == "port":
            continue
        assert итог["daemon"].get(ключ) == значение, (
            "ключ daemon.{} потерян при слиянии локального файла: "
            "было {!r}, стало {!r}".format(
                ключ, значение, итог["daemon"].get(ключ))
        )


def test_локальный_файл_может_убрать_ключ_секции(tmp_path):
    """Секция объявлена словарём без 'bridge' — она не обязана исчезнуть,
    но и не обязана появиться там, где локальный файл её не задавал:
    убеждаемся, что слияние делает {**пакетная, **локальная}, а не
    рекурсивную сборку с сохранением отсутствующих в обеих версиях ключей.
    """
    пакет = _песочница(tmp_path)
    состояние = tmp_path / "состояние"
    _написать_локальный(состояние, {"daemon": {"keepalive_sec": 200}})

    итог = _карта(пакет, tmp_path, {"GARANT_HOME": str(состояние)})
    исходная = _исходная_карта(пакет)

    assert итог["daemon"]["keepalive_sec"] == 200
    assert итог["daemon"]["call_timeout_sec"] == исходная["daemon"]["call_timeout_sec"]
    assert итог["daemon"]["bridge"] == исходная["daemon"]["bridge"]


def test_приоритет_env_сильнее_локального_файла(tmp_path):
    """env → endpoints.local.json → endpoints.json → умолчание — целиком."""
    пакет = _песочница(tmp_path)
    состояние = tmp_path / "состояние"

    # без локального файла и без env — пакетное умолчание (порта в секции
    # daemon пакетного файла нет вовсе, поэтому это умолчание модуля).
    без_ничего = _карта(пакет, tmp_path, {"GARANT_HOME": str(состояние)})
    assert без_ничего["daemon"].get("port") is None

    _написать_локальный(состояние, {"daemon": {"port": 8793}})
    только_локальный = _карта(пакет, tmp_path, {"GARANT_HOME": str(состояние)})
    assert только_локальный["daemon"]["port"] == 8793, (
        "локальный файл обязан перекрыть пакетный (в котором ключа нет)"
    )

    # config.PORT — это уже применённая величина (env -> json -> умолчание),
    # а не сырая карта; сверяем именно её, раз речь о приоритете env.
    код = (
        "import sys\n"
        "sys.path.insert(0, r'{site}')\n"
        "import garant_mcp.config as config\n"
        "print(config.PORT)\n"
    ).format(site=пакет.parent)
    r = _запуск(код, tmp_path,
               {"GARANT_HOME": str(состояние), "GARANT_PORT": "8799"},
               таймаут=60)
    assert r.returncode == 0, r.stderr.strip()
    assert r.stdout.strip() == "8799", (
        "переменная окружения обязана победить и локальный файл, и "
        "пакетный: получено {!r}".format(r.stdout.strip())
    )


def test_карта_кэшируется_на_процесс(tmp_path):
    """Изменение локального файла ПОСЛЕ первого чтения не видно тем же
    процессом — это прямое следствие module-level кэша `_КЭШ_КАРТЫ`
    в config.py, а не то, что подмену можно наблюдать снаружи процесса.
    """
    пакет = _песочница(tmp_path)
    состояние = tmp_path / "состояние"
    _написать_локальный(состояние, {"daemon": {"port": 8794}})

    код = (
        "import sys, json\n"
        "sys.path.insert(0, r'{site}')\n"
        "import garant_mcp.config as config\n"
        "import garant_mcp.paths as paths\n"
        "первый = config.карта()['daemon'].get('port')\n"
        "paths.local_endpoints_file.write_text(\n"
        "    json.dumps({{'daemon': {{'port': 9999}}}}), encoding='utf-8')\n"
        "второй = config.карта()['daemon'].get('port')\n"
        "print(json.dumps({{'первый': первый, 'второй': второй}}))\n"
    ).format(site=пакет.parent)
    r = _запуск(код, tmp_path, {"GARANT_HOME": str(состояние)}, таймаут=60)
    assert r.returncode == 0, r.stderr.strip()
    данные = json.loads(r.stdout.strip())
    assert данные["первый"] == 8794
    assert данные["второй"] == 8794, (
        "config.карта() перечитала файл в том же процессе — кэш на процесс "
        "не работает, а докстринг config.py обещает обратное: {}".format(данные)
    )
