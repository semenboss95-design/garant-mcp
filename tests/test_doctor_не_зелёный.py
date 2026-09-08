# -*- coding: utf-8 -*-
"""`doctor` не красит недоказанное зелёным (спецификация нового поведения,
наряд этапа 03, фаза B, часть 2): `keepalive` и «версия пакета» — пункты,
которые ничего не проверяют, они только СООБЩАЮТ, и зелёный им не положен
ни при каком исходе — в том числе тогда, когда исход последнего продления
выглядит успешным («HTTP 200»): именно на этой подмене исторически и
проезжало зелёное «keepalive: ok» вместо доказанной сессии.

`doctor()` в одном вызове читает состояние демона, автозапуск и
регистрацию у РЕАЛЬНЫХ конфигураций MCP-клиентов этой машины — наряд
прямо запрещает трогать регистрации и планировщик. Здесь всё это
подменено контролируемыми заглушками до вызова: `doctor()` исполняется
целиком (не пересказывается), но ни к одному внешнему состоянию машины
не обращается — только к тому, что подставил тест.
"""
from __future__ import annotations

from types import SimpleNamespace

from garant_mcp import cli
from garant_mcp import daemon_client as dc
from garant_mcp import register
from garant_mcp import autostart
from garant_mcp import paths


def _подставить_демона(monkeypatch, *, keepalive_исход: str):
    """Демон «жив», сессия «жива», keepalive — заданный исход. Каталог
    состояния назван ТЕМ ЖЕ, что у paths.STATE — иначе doctor() уйдёт в
    ветку «демон другой установки» и до пункта keepalive не дойдёт вовсе."""
    monkeypatch.setattr(dc, "адрес",
                        lambda: {"alive": True, "port": 12345, "pid": 999})
    monkeypatch.setattr(dc, "состояние", lambda: {
        "каталоги": {"состояние": str(paths.STATE)},
        "сессия": "жива",
        "сессия_жива_с": "2026-01-01T00:00:00",
        "последний_keepalive": "2026-01-01T00:05:00",
        "последний_keepalive_исход": keepalive_исход,
    })
    monkeypatch.setattr(dc, "сверить_версию", lambda: None)


def _подставить_безопасное_окружение(monkeypatch):
    """Части doctor(), трогающие РЕАЛЬНУЮ машину (клиенты MCP, планировщик,
    chromium, PATH), — на нейтральные заглушки. Наряд запрещает трогать
    регистрации и планировщик; без подмены `doctor()` читал бы их настоящее
    состояние и тест зависел бы от машины, где его запустили."""
    monkeypatch.setattr(cli, "_питон", lambda: (cli.ОК, "3.13", ""))
    monkeypatch.setattr(cli, "_исполняемые", lambda: (cli.ОК, "в PATH", ""))
    monkeypatch.setattr(cli, "_chromium", lambda: (cli.ВНИМАНИЕ, "не проверялось", ""))
    monkeypatch.setattr(cli, "_состояние_каталога", lambda: (cli.ОК, str(paths.STATE), ""))
    monkeypatch.setattr(register, "цель_desktop",
                        lambda: SimpleNamespace(имя="desktop", путь="—"))
    monkeypatch.setattr(register, "цель_code",
                        lambda: SimpleNamespace(имя="code", путь="—"))
    monkeypatch.setattr(register, "статус", lambda цель: "клиент не установлен")
    monkeypatch.setattr(autostart, "проверить",
                        lambda: (_ for _ in ()).throw(
                            autostart.НетПоддержки("подменено тестом")))


def test_keepalive_не_зелёный_даже_при_http_200(monkeypatch, capsys):
    _подставить_демона(monkeypatch, keepalive_исход="HTTP 200")
    _подставить_безопасное_окружение(monkeypatch)

    cli.doctor()

    строки = [s for s in capsys.readouterr().out.splitlines()
             if "keepalive" in s]
    assert строки, "пункт keepalive не напечатался вовсе"
    for строка in строки:
        assert not строка.startswith(cli.ОК), (
            "keepalive напечатан зелёным даже при «HTTP 200» — исход "
            "продления ПОДСТАВЛЯЕТСЯ демоном и не доказывает сессию:\n"
            + строка)


def test_версия_пакета_не_зелёная(monkeypatch, capsys):
    _подставить_демона(monkeypatch, keepalive_исход="HTTP 200")
    _подставить_безопасное_окружение(monkeypatch)

    cli.doctor()

    строки = [s for s in capsys.readouterr().out.splitlines()
             if "версия пакета" in s]
    assert строки, "пункт «версия пакета» не напечатался вовсе"
    for строка in строки:
        assert not строка.startswith(cli.ОК), (
            "«версия пакета» ничего не проверяет — зелёной быть не "
            "может:\n" + строка)
