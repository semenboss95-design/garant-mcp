"""Пустое тело ответа доезжает предупреждением, а не выглядит честным нулём
(.claude/rules/provenance.md, «Пустое тело»).

До 06.09.2026 сбой «Гаранта» и честный нулевой результат («ничего не
найдено») доходили до читателя неразличимыми. Признак `_пусто`, который
`_fetch` ставит на пустое тело, обязан дойти до пользовательского ответа
как явная оговорка — а не потеряться где-то на пути через `_run_steps`.
"""
from __future__ import annotations

import json

from _фейк_транспорт import ФейкТранспорт

from garant_mcp import client


def test_пустое_тело_на_list_page_даёт_оговорку_а_не_нулевую_выдачу_молча():
    """/list/page ответил пустой строкой — это может быть и честный ноль,
    и сбой «Гаранта». `search()` обязан сказать об этом словами, а не
    просто вернуть `items: []`, неотличимо от «ничего не нашлось»."""
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/search/base/run" in url:
            return 200, json.dumps({"sid": "sid-1"})
        if "/search/poll" in url:
            return 200, json.dumps({"kindTree": {"doclistId": "d1", "total": 0}})
        if "/list/page" in url:
            return 200, ""    # ПУСТОЕ ТЕЛО — не {"items": []}, а буквально ничего
        raise AssertionError(url)

    к = client.GarantClient(transport=ФейкТранспорт(маршрут))
    ответ = к.search("запрос", limit=5)

    assert ответ["items"] == []
    assert "_предупреждение" in ответ, (
        "пустое тело ответа обязано дать явную оговорку — иначе оно "
        "неотличимо от честного «ничего не найдено»"
    )
    assert "ПУСТОЕ" in ответ["_предупреждение"]


def test_непустой_json_не_несёт_оговорку_о_пустом_теле():
    """Обратная сторона: НЕПУСТОЕ тело с честным `items: []` не должно
    подхватывать чужую оговорку — иначе она перестанет что-то отличать."""
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/search/base/run" in url:
            return 200, json.dumps({"sid": "sid-1"})
        if "/search/poll" in url:
            return 200, json.dumps({"kindTree": {"doclistId": "d1", "total": 0}})
        if "/list/page" in url:
            return 200, json.dumps({"items": []})   # честный пустой JSON, не пустое тело
        raise AssertionError(url)

    к = client.GarantClient(transport=ФейкТранспорт(маршрут))
    ответ = к.search("запрос", limit=5)

    assert ответ["items"] == []
    assert "_предупреждение" not in ответ, (
        "честный пустой JSON — это не пустое ТЕЛО, оговорка о нём звучать не должна"
    )
