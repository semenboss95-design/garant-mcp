# -*- coding: utf-8 -*-
"""Д-14 аудита: ветка «число страниц НЕИЗВЕСТНО» — в `document()`
(client.py:1068-1084 по нумерации аудита) и парная в `article()`
(client.py:1127-1140). Ни одна не была покрыта тестом.

Сценарий у обеих один и тот же реальный случай: `/document/info` не
отвечает (клиент не знает `total`), а `/document/pages` на пакет из ДВУХ
страниц отвечает пустым списком — то, что «Гарант» делает, когда вторая
страница пакета не существует (комментарий в client.py: «на пакет с
несуществующей страницей Гарант отвечает CanNotFindData НА ВЕСЬ пакет»).
Без явного предупреждения read одностраничного акта в этом случае молча
выглядит как «текст такой и есть» — пустой либо начатый и оборванный.

`/document/info`, отвечающий кодом 500, — не изображение конкретной формы
ответа «Гаранта» (её здесь нет, endpoints.json карту `document_info` не
даёт синтетике формы отказа), а самый прямой способ добиться того, что
`page_count()` ловит и превращает в `None`: `except GarantError: return
None`. Разбор этого пути уже проверяется другими тестами; здесь используется
как заданное условие, а не как предмет проверки.
"""
from __future__ import annotations

import json

from _фейк_транспорт import ФейкТранспорт

from garant_mcp import client, protocol


def _маршрутизатор_document():
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/document/info" in url:
            return 500, "внутренняя ошибка (синтетика теста)"
        if "/document/pages" in url:
            # Пустой список — то же самое, чем «Гарант» отвечает на пакет
            # с несуществующей страницей: НЕ ошибка транспорта, а пустая
            # выдача (CanNotFindData НА ВЕСЬ пакет).
            return 200, json.dumps({"items": []})
        if "/redactions/list" in url:
            return 200, json.dumps({"redactions": [
                {"text": "ред.", "status": "rs_Actual",
                 "activity": [{"from": "01.01.2024", "to": ""}],
                 "documentId": 1}]})
        raise AssertionError(f"незнакомый URL: {method} {url}")
    return маршрут


def test_document_предупреждает_о_неизвестном_числе_страниц():
    транспорт = ФейкТранспорт(_маршрутизатор_document())
    к = client.GarantClient(transport=транспорт)

    ответ = к.document(doc_id=1)

    assert ответ["страниц_в_документе"] is None, (
        "/document/info отказал — «страниц_в_документе» обязано быть None, "
        "а не 0 (0 читается как «страниц нет», а не «неизвестно»)")
    assert "_предупреждение" in ответ, (
        "число страниц неизвестно, а страница не загрузилась вовсе — "
        "молчание здесь выглядит как «документ пуст», а не как отказ")
    assert "НЕИЗВЕСТНО" in ответ["_предупреждение"], (
        "нет предупреждения о том, что число страниц не установлено:\n"
        + ответ["_предупреждение"])
    assert "Загружено 0 страниц" in ответ["_предупреждение"], (
        "ноль загруженных страниц при неизвестном total — самый частый "
        "практический случай (одностраничный акт), и предупреждение обязано "
        "назвать его прямо, а не только теоретическую возможность:\n"
        + ответ["_предупреждение"])


def _маршрутизатор_article():
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/document/headline" in url:
            return 200, json.dumps({"children": [
                {"text": "Статья 5",
                 "data": {"element": {"id": "e5", "type": "article"}}}]})
        if "/document/locate" in url:
            return 200, json.dumps({"page": 1})
        if "/document/info" in url:
            return 500, "внутренняя ошибка (синтетика теста)"
        if "/document/pages" in url:
            return 200, json.dumps({"items": []})
        raise AssertionError(f"незнакомый URL: {method} {url}")
    return маршрут


def test_article_поднимает_ошибку_о_неизвестном_числе_страниц():
    транспорт = ФейкТранспорт(_маршрутизатор_article())
    к = client.GarantClient(transport=транспорт)

    try:
        к.article(doc_id=1, article="5")
        raise AssertionError("article() обязан был отказать: страниц нет, "
                             "и число страниц неизвестно")
    except client.GarantError as e:
        assert "неизвестно" in str(e).lower(), (
            "текст ошибки не называет главную причину отказа "
            "(число страниц неизвестно): " + str(e))
        assert "garant_document" in str(e), (
            "ошибка обязана предложить конкретный обходной путь — взять "
            "документ целиком: " + str(e))
        assert e.тип == protocol.ТИП_НЕ_НАЙДЕНО, (
            "тип ошибки — not_found (спросили то, чего не оказалось), "
            "получено: " + e.тип)
