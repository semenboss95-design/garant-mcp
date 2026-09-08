# -*- coding: utf-8 -*-
"""`garant stop` не убивает процесс, чей PID не совпал с нашим демоном.

Свойство раньше проверялось на пульте PowerShell (`docs/TEAM.md` §7,
осиротевшая проверка «пульт не убивал процесс, чей PID не совпал
с `daemon_port`»), пульт удалён решением 18, а тест за ним не переехал.
Место сменилось на `cli.stop()` (`src/garant_mcp/cli.py`) — логика та же:
демон опознаётся тройной проверкой (порт отвечает НАШИМ признаком → PID
взят из `daemon_port` → имя процесса с этим PID «похоже на наше»), и
третье условие — единственное, что мешает убить чужую программу, если
`daemon_port` протух, а PID успел достаться кому-то ещё.

Воспроизводится ИМЕННО худший случай, а не облегчённый: на порту сидит
настоящий HTTP-слушатель, отвечающий `/health` нашим признаком
(`protocol.ПРИЗНАК_ДЕМОНА`) — то есть проверка «жив ли демон» проходит.
`daemon_port` при этом называет PID НАСТОЯЩЕГО постороннего процесса —
не мёртвого, не выдуманного числа: если бы `stop()` всё-таки убил его,
это было бы видно по-настоящему, а не по факту вызова функции.

Транспорт не подменяется: `garant stop` запускается настоящим
подпроцессом, `_имя_процесса()` внутри него зовёт настоящий `tasklist`/`ps`.
Подменяется только то, что физически недостижимо в песочнице теста —
сам процесс-самозванец на порту `daemon_port` и решение ОС завести
посторонний процесс с известным PID; собственно код `cli.py` не тронут
ничем.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_paths import _песочница, _окружение, чужой_каталог  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import garant_mcp.protocol as protocol  # noqa: E402

ТОКЕН_КАНАРЕЙКА = "canary-token-do-not-leak-stop-42"


class _Самозванец:
    """Настоящий HTTP-слушатель, отвечающий на `/health` нашим признаком.

    Это и есть худший случай из задания: «жив» — правда, а вот PID из
    `daemon_port` принадлежит не ему, а постороннему процессу.
    """

    def __init__(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ответ(self):
                тело = json.dumps({protocol.ПРИЗНАК_ДЕМОНА: "жив"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(тело)))
                self.end_headers()
                self.wfile.write(тело)

            do_GET = _ответ
            do_POST = _ответ

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.порт = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _посторонний_процесс() -> subprocess.Popen:
    """Настоящий живой процесс, чьё имя заведомо не «похоже на наше».

    `cli._наш_на_вид()` пропускает всё, что содержит `python` или `garant`
    в имени — родной интерпретатор теста прошёл бы эту проверку и не
    показал бы отказа. Нужен посторонний, долгоживущий и безобидный
    исполняемый файл, который есть на любой машине из коробки.
    """
    if os.name == "nt":
        # cmd.exe /c ping — PID достаётся именно cmd.exe (он ждёт ребёнка),
        # имя в tasklist — «cmd.exe», ни «python», ни «garant» в нём нет.
        return subprocess.Popen(
            ["cmd.exe", "/c", "ping", "-n", "60", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.Popen(
        ["sleep", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_stop_не_убивает_процесс_с_чужим_pid(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    состояние = tmp_path / "состояние"
    состояние.mkdir()

    самозванец = _Самозванец()
    посторонний = _посторонний_процесс()
    try:
        (состояние / "daemon_port").write_text(
            json.dumps({"порт": самозванец.порт, "pid": посторонний.pid}),
            encoding="utf-8")
        (состояние / "daemon_token").write_text(
            ТОКЕН_КАНАРЕЙКА, encoding="utf-8")

        r = subprocess.run(
            [sys.executable, "-m", "garant_mcp.cli", "stop"],
            cwd=str(чужой_каталог),
            env=_окружение({"PYTHONPATH": str(пакет.parent),
                            "GARANT_HOME": str(состояние)}),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )

        # Сначала — что посторонний процесс вообще жив к моменту запуска
        # `garant stop`. Без этой проверки тест зелен и в случае, если
        # процесс не поднялся вовсе, — тогда отказ по другой причине
        # выглядел бы как защита, а её нет.
        assert посторонний.poll() is None, (
            "посторонний процесс не пережил даже запуск теста — "
            "проверка ничего не проверяет")

        # Дать ОС время исполнить возможный taskkill/kill, прежде чем
        # опрашивать процесс: убийство не мгновенно.
        time.sleep(1.0)

        assert r.returncode != 0, (
            "`garant stop` обязан отказать, когда PID из daemon_port "
            "не похож на наш процесс, а не выйти кодом 0:\n"
            + r.stdout + r.stderr)
        assert "не похоже" in r.stderr or "PID" in r.stderr, (
            "отказ обязан называть причину — что PID не опознан как наш:\n"
            + r.stderr)

        assert посторонний.poll() is None, (
            "`garant stop` убил посторонний процесс (PID {}), чьё имя не "
            "похоже на наше — ровно тот случай, ради которого существует "
            "тройная проверка перед убийством.".format(посторонний.pid))
    finally:
        самозванец.close()
        if посторонний.poll() is None:
            посторонний.kill()
            посторонний.wait(timeout=10)
