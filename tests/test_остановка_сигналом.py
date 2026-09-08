# -*- coding: utf-8 -*-
"""Регресс на дефект остановки демона: `_Остановка` съедалась `socketserver`.

Дефект был такой: `_Остановка` наследовала `Exception`, а
`socketserver._handle_request_noblock` оборачивает `process_request` в
`except Exception: handle_error(...)` — независимо от того, что делает наш
собственный `try/except Exception` вокруг такта `BrowserWorker`, эта обёртка
чужая, из стандартной библиотеки, и стоит НИЖЕ, между `select()` и вызовом
обработчика запроса. Сигнал (`SIGTERM`), доставленный обработчиком сигнала
ИМЕННО в это окно, проглатывался: в лог падал трейсбек, `serve_forever`
крутил цикл дальше как ни в чём не бывало, и до `finally` в `main()`
(закрытие браузера, снятие `daemon_port`) дело не доходило вовсе — снаружи
это выглядело как «демон завис», и `garant stop` ждал полный таймаут.

Свойство, которое обязан ловить тест, — не форма (`_Остановка` наследует
`BaseException`), а поведение: исключение, поднятое ВНУТРИ `process_request`
настоящего `socketserver`, обязано выйти из `serve_forever()` и оборвать
цикл, а не быть проглоченным той же обвязкой, что ловит `Exception`.
Проверка формы (`issubclass(daemon._Остановка, BaseException)`) пережила бы
любую новую обёртку `except Exception`, добавленную на пути между сигналом
и обработчиком, — сторож на форму такой регресс не поймает, а этот тест
ловит.

Стенд — настоящий `socketserver.TCPServer` с настоящим сокетом на
127.0.0.1: без живого установленного соединения `select()` внутри
`serve_forever` простаивает бесконечно и окно `process_request` не
наступает никогда (именно так промахнулась прошлая проба — решение 28,
дополнение про пробы). Подменяется только `process_request`: он не
исполняет настоящий протокол HTTP, а сразу поднимает заданное исключение —
это ровно то место, где несовместимость `Exception`/`BaseException`
проявляется, и подменять здесь что-то другое незачем.
"""

from __future__ import annotations

import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import garant_mcp.daemon as daemon  # noqa: E402


class _ПустойОбработчик(socketserver.BaseRequestHandler):
    """Настоящий `process_request` этой заглушки никогда не зовёт: полностью
    подменяется ниже. Класс нужен только потому, что конструктор
    `TCPServer` требует `RequestHandlerClass`."""

    def handle(self):
        pass


def _сделать_сервер(поднимать_на_первом=None):
    """`TCPServer` на свободном порту 127.0.0.1. `process_request`
    подменён: считает вызовы и на ПЕРВОМ подключении поднимает заданное
    исключение (если оно задано), минуя настоящую обработку запроса."""
    сервер = socketserver.TCPServer(("127.0.0.1", 0), _ПустойОбработчик)
    сервер.вызовов = []

    def process_request(request, client_address):
        сервер.вызовов.append(1)
        if len(сервер.вызовов) == 1 and поднимать_на_первом is not None:
            # Сокет не закрываем сами: и `except Exception`, и bare
            # `except:` в socketserver._handle_request_noblock вызывают
            # shutdown_request(request) сами, до/после проглатывания или
            # переброса — двойное закрытие тут не нужно и не требуется.
            raise поднимать_на_первом
        request.close()

    сервер.process_request = process_request
    return сервер


def _запустить_в_потоке(сервер):
    """serve_forever в фоновом потоке; что из него вылетело — в `raised`."""
    raised: list = []
    finished = threading.Event()

    def цель():
        try:
            сервер.serve_forever(poll_interval=0.05)
        except BaseException as e:  # именно BaseException — нас интересует ЛЮБОЙ выход
            raised.append(e)
        finally:
            finished.set()

    поток = threading.Thread(target=цель, daemon=True)
    поток.start()
    return поток, raised, finished


def _подключиться(порт: int) -> socket.socket:
    return socket.create_connection(("127.0.0.1", порт), timeout=5)


# --------------------------------------------------------------------------
#  1. свойство: _Остановка обязана прервать serve_forever изнутри process_request
# --------------------------------------------------------------------------

def test_остановка_прерывает_serve_forever_из_process_request():
    сервер = _сделать_сервер(поднимать_на_первом=daemon._Остановка(15))
    поток, raised, finished = _запустить_в_потоке(сервер)
    порт = сервер.server_address[1]

    клиент = _подключиться(порт)
    try:
        assert finished.wait(5), (
            "serve_forever не завершился за 5 с после того, как "
            "process_request поднял daemon._Остановка — исключение было "
            "проглочено обвязкой socketserver вместо того, чтобы прервать "
            "цикл (ровно дефект, из-за которого «garant stop» видел "
            "зависший демон)."
        )
        assert raised and isinstance(raised[0], daemon._Остановка), (
            "из serve_forever вышло не то исключение, что было поднято: "
            "{}".format(raised)
        )
        assert len(сервер.вызовов) == 1

        # Цикл не сделал следующего оборота: поток serve_forever уже мёртв,
        # и даже настоящее новое TCP-подключение к слушающему сокету не
        # может быть принято — accept() больше никто не зовёт.
        второй = _подключиться(порт)
        try:
            time.sleep(0.3)
            assert len(сервер.вызовов) == 1, (
                "process_request был вызван повторно — serve_forever "
                "продолжил цикл ПОСЛЕ того, как из process_request вышла "
                "daemon._Остановка."
            )
        finally:
            второй.close()
    finally:
        клиент.close()
        сервер.server_close()
        поток.join(timeout=5)


# --------------------------------------------------------------------------
#  2. контроль: обычный Exception в том же месте ОБЯЗАН быть проглочен
# --------------------------------------------------------------------------

def test_контроль_обычное_исключение_в_process_request_проглатывается():
    """Без этого контроля тест выше был бы зелёным и на дефекте: если бы
    кто-то заново сделал `_Остановка` наследником `Exception`, поднятое
    исключение в первом тесте перестало бы прерывать цикл, и тест 1 упал
    бы сам по себе. Но проверить, что тест 1 ловит РОВНО класс дефекта
    (Exception против BaseException), можно только явным контролем: тот же
    стенд, тот же способ доставки, локальный класс на основе `Exception` —
    и утверждение прямо противоположное первому тесту."""

    class _КонтрольноеИсключение(Exception):
        pass

    сервер = _сделать_сервер(
        поднимать_на_первом=_КонтрольноеИсключение("бум (тест)"))
    поток, raised, finished = _запустить_в_потоке(сервер)
    порт = сервер.server_address[1]

    клиент = _подключиться(порт)
    try:
        time.sleep(0.3)
        assert len(сервер.вызовов) == 1
        assert not finished.is_set(), (
            "поток serve_forever завершился — обычный Exception, "
            "поднятый в process_request, не имеет права прервать цикл: "
            "именно эта проглатывающая обвязка и была причиной дефекта, "
            "будь на его месте daemon._Остановка."
        )
        assert not raised

        # Цикл жив и обслуживает дальше: второе подключение обрабатывается
        # обычным (не поднимающим) process_request.
        второй = _подключиться(порт)
        try:
            предел = time.time() + 5
            while time.time() < предел and len(сервер.вызовов) < 2:
                time.sleep(0.05)
            assert len(сервер.вызовов) == 2, (
                "serve_forever не обработал второе подключение — цикл "
                "не продолжился после того, как Exception был проглочен."
            )
        finally:
            второй.close()
    finally:
        клиент.close()
        сервер.shutdown()
        поток.join(timeout=5)
        сервер.server_close()
