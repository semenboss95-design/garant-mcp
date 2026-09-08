"""Общий диспетчер для тестов `article()` — оглавление, locate, страницы.

Не тест сам по себе (без `test_` в имени, pytest его не соберёт), а
конструктор фейкового транспорта, разделяемый между test_client_вырезка_статьи.py
и test_client_граница_пункта.py: у обоих одна и та же цепочка вызовов
(`/document/headline` → `/document/locate` → `/document/info` →
`/document/pages` → возможный второй `/document/locate` — зонд решения 21).
"""
from __future__ import annotations

import json


def маршрутизатор_статьи(*, children: list[dict], page_count: int,
                          locate_by_id: dict, pages_text: dict):
    """`children` — сырое дерево, как отдаёт /document/headline (без обёртки).
    `locate_by_id` — element_id -> номер страницы (int) либо None (отказ
    зонда — /document/locate отвечает так, будто не нашёл элемент).
    `pages_text` — номер страницы (int) -> HTML-текст этой страницы.
    """
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/document/headline" in url:
            return 200, json.dumps({"children": children}, ensure_ascii=False)
        if "/document/locate" in url:
            # elements={id}&type={type} — id нужен, чтобы понять, ЧЕЙ это locate.
            элемент = url.split("elements=")[1].split("&")[0]
            стр = locate_by_id.get(элемент)
            if стр is None:
                return 200, json.dumps({})   # «Гарант» не подтвердил страницу
            return 200, json.dumps({"page": стр})
        if "/document/info" in url:
            return 200, json.dumps({"pageCount": page_count, "documentTitle": "Д"})
        if "/document/pages" in url:
            номера = [int(x) for x in url.split("pages=")[1].split("&")[0].split(",")]
            items = [{"number": n, "text": pages_text.get(n, "")} for n in номера]
            return 200, json.dumps({"items": items}, ensure_ascii=False)
        if "/redactions/list" in url:
            # article() всегда собирает provenance по действующей редакции —
            # тестам вырезки это не интересно, поэтому одна нейтральная запись.
            return 200, json.dumps({"redactions": [
                {"text": "ред.", "status": "rs_Actual",
                 "activity": [{"from": "01.01.2024", "to": ""}], "documentId": 1}]})
        raise AssertionError(f"незнакомый URL в тесте article(): {method} {url}")
    return маршрут
