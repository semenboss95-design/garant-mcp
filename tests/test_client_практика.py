"""Практика — серверными фильтрами, ограничение 9 CLAUDE.md.

`/list/kind` → `/list/filter` → `/list/page`, и КАЖДЫЙ шаг отдаёт НОВЫЙ
doclistId, а следующий шаг обязан унести его в форму/URL. Клиентский отбор
по заголовкам не годится — это ровно то, против чего написано ограничение 9.
Проверяется по журналу вызовов фейкового транспорта: тест не смотрит внутрь
`practice()`, он смотрит на то, что РЕАЛЬНО ушло наружу через границу
`Transport.fetch`.

Плюс: ответ несёт безусловное предупреждение о неполноте (решение,
закрытое 06.09.2026) — оно обязано стоять всегда, а не только при пустой
выдаче.

Синтетика — `tests/fixtures/поиск.json` и `tests/fixtures/практика.json`,
собраны по форме endpoints.json (секция practice: kind_step, date_step,
page_step). Тексты актов и номера дел выдуманы.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from _фейк_транспорт import ФейкТранспорт

from garant_mcp import client

_ПОИСК = json.loads(
    (Path(__file__).parent / "fixtures" / "поиск.json").read_text(encoding="utf-8"))
_ПРАКТИКА = json.loads(
    (Path(__file__).parent / "fixtures" / "практика.json").read_text(encoding="utf-8"))


def _id_из_url(url: str) -> str | None:
    """`id=...` — что ушло в URL/форму на этом шаге. Форма — уже закодированная
    строка (после `_form`), поэтому ищем и там, и в query-строке URL."""
    m = re.search(r"[?&]id=([^&]+)", url)
    return m.group(1) if m else None


def _маршрутизатор(kind_body=None, date_body=None, page_body=None):
    kind_body = kind_body if kind_body is not None else _ПРАКТИКА["kind"]
    date_body = date_body if date_body is not None else _ПРАКТИКА["date"]
    page_body = page_body if page_body is not None else _ПРАКТИКА["page"]

    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/search/base/run" in url:
            return 200, json.dumps(_ПОИСК["run"])
        if "/search/poll" in url:
            return 200, json.dumps(_ПОИСК["poll"])
        if "/list/kind" in url:
            return 200, json.dumps(kind_body, ensure_ascii=False)
        if "/list/filter" in url:
            return 200, json.dumps(date_body, ensure_ascii=False)
        if "/list/page" in url:
            return 200, json.dumps(page_body, ensure_ascii=False)
        raise AssertionError(f"незнакомый URL в тесте практики: {method} {url}")
    return маршрут


def test_каждый_шаг_несёт_новый_doclist_id():
    """doclistId идёт по цепочке: poll -> kind -> filter -> page, и на КАЖДОМ
    шаге в форму/URL уходит ИМЕННО значение, отданное ПРЕДЫДУЩИМ шагом —
    а не то, что было на шаг раньше и не какая-то заглушка."""
    транспорт = ФейкТранспорт(_маршрутизатор())
    к = client.GarantClient(transport=транспорт)

    к.practice("запрос", date_from="01.01.2020", date_to="01.01.2021")

    def найти(подстрока, последний=False):
        """`последний=True` — для /list/page: у него ДВЕ разные роли в одном
        вызове practice(). Первый раз /list/page дёргает СОБСТВЕННЫЙ, третий
        шаг базового поиска (spec search.steps) — это не финал практики,
        а внутренний шаг search_spec, выполняемый заодно с run/poll. Только
        ПОСЛЕДНИЙ вызов /list/page — тот, что явно собран в practice()
        (`spec["page_step"]`) поверх уже отфильтрованного списка."""
        совпадения = [(url, form) for method, url, form in транспорт.вызовы
                      if подстрока in url]
        if not совпадения:
            raise AssertionError(f"шаг {подстрока} не вызывался")
        url, form = совпадения[-1] if последний else совпадения[0]
        if form:
            m = re.search(r"(?:^|&)id=([^&]+)", form)
            if m:
                return m.group(1)
        return _id_из_url(url)

    id_поиска = _ПОИСК["poll"]["kindTree"]["doclistId"]
    id_kind = _ПРАКТИКА["kind"]["doclistId"]
    id_date = _ПРАКТИКА["date"]["doclistId"]

    assert найти("/list/kind") == id_поиска, (
        "/list/kind обязан получить doclistId БАЗОВОГО поиска"
    )
    assert найти("/list/filter") == id_kind, (
        "/list/filter обязан получить НОВЫЙ doclistId, отданный /list/kind, "
        "а не исходный из базового поиска"
    )
    assert найти("/list/page", последний=True) == id_date, (
        "финальный /list/page обязан получить doclistId, отданный /list/filter"
    )
    # Все три различны — ни один шаг не выполнялся с чужим или старым id.
    assert len({id_поиска, id_kind, id_date}) == 3


def test_без_периода_page_использует_id_от_kind():
    """Шаг /list/filter не вызывается, если period не задан — тогда
    /list/page обязан унести doclistId, отданный /list/kind, а не поиска."""
    транспорт = ФейкТранспорт(_маршрутизатор())
    к = client.GarantClient(transport=транспорт)

    к.practice("запрос")   # без date_from/date_to

    урлы = [url for (_, url, _) in транспорт.вызовы]
    assert not any("/list/filter" in u for u in урлы), (
        "период не задан — /list/filter не должен был вызываться вовсе"
    )
    # Последний /list/page — тот, что собирает practice() поверх уже
    # отфильтрованного по виду списка; первый принадлежит внутреннему шагу
    # базового поиска (см. комментарий в предыдущем тесте).
    page_url = [u for u in урлы if "/list/page" in u][-1]
    assert _id_из_url(page_url) == _ПРАКТИКА["kind"]["doclistId"]


def test_предупреждение_о_неполноте_безусловно():
    """Стоит ВСЕГДА, а не только при пустой выдаче — иначе «ничего не
    найдено» и «нашлось, но показана часть» неотличимы для читателя."""
    транспорт = ФейкТранспорт(_маршрутизатор())
    к = client.GarantClient(transport=транспорт)

    ответ = к.practice("запрос", date_from="01.01.2020", date_to="01.01.2021")

    assert ответ["items"], "в этом сценарии выдача непустая"
    assert "_предупреждение" in ответ
    assert "НЕПОЛНАЯ" in ответ["_предупреждение"]


def test_уточнение_по_суду_отбор_на_клиенте_назван_явно():
    транспорт = ФейкТранспорт(_маршрутизатор())
    к = client.GarantClient(transport=транспорт)

    ответ = к.practice("запрос", court="Верховного")

    assert ответ["уточнение_по_суду"] == "Верховного"
    assert all("верховного" in (i["title"] or "").lower() for i in ответ["items"])
    assert "ОТБОРОМ ПО ЗАГОЛОВКУ" in ответ["_предупреждение"]
