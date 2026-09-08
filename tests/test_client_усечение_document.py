"""`document()` — три причины неполноты, и советы у них разные
(.claude/rules/provenance.md, «Неполнота объявляется, а не подразумевается»).

Три ветки: упёрлись в `max_pages` (совет — повторить с бо́льшим значением),
«Гарант» не отдал пакет посреди документа (совет — этот НЕ поможет, смотреть
логи), недобор без обрыва и без лимита. Тест проверяет, что ветки не слиплись:
у каждой стоит СВОЙ текст, и советы не перепутаны местами.
"""
from __future__ import annotations

import json

from _фейк_транспорт import ФейкТранспорт

from garant_mcp import client


def _редакции_ответ():
    return json.dumps({"redactions": [
        {"text": "ред.", "status": "rs_Actual",
         "activity": [{"from": "01.01.2024", "to": ""}], "documentId": 1}]},
        ensure_ascii=False)


def _маршрутизатор(pageCount, страницы):
    """`страницы` — функция(список_номеров) -> список текстов ("" — нет текста)."""
    def маршрут(method, url, form, номер):
        if "/server/keepalive" in url:
            return 200, ""
        if "/document/info" in url:
            return 200, json.dumps({"pageCount": pageCount, "documentTitle": "Д"})
        if "/redactions/list" in url:
            return 200, _редакции_ответ()
        if "/document/pages" in url:
            # pages={n} или {n},{n+1} — восстанавливаем список номеров из URL.
            часть = [p for p in url.split("pages=")[1].split("&")[0].split(",")]
            номера = [int(x) for x in часть]
            тексты = страницы(номера)
            items = [{"number": n, "text": t} for n, t in zip(номера, тексты)]
            return 200, json.dumps({"items": items}, ensure_ascii=False)
        raise AssertionError(f"незнакомый URL: {method} {url}")
    return маршрут


def test_упёрлись_в_max_pages_совет_повторить():
    """Документ длиннее max_pages — совет исполним: увеличить параметр."""
    транспорт = ФейкТранспорт(_маршрутизатор(
        pageCount=100,
        страницы=lambda nums: [f"<p>Текст страницы {n}.</p>" for n in nums]))
    к = client.GarantClient(transport=транспорт)

    ответ = к.document(doc_id=1, max_pages=2)

    предупреждение = ответ["_предупреждение"]
    assert "длиннее max_pages" in предупреждение
    assert "Повторите вызов с бо́льшим max_pages" in предупреждение
    assert "ОБОРВАЛАСЬ" not in предупреждение
    assert ответ["страниц"] == 2
    assert ответ["страниц_в_документе"] == 100


def test_гарант_не_отдал_пакет_совет_повтор_не_поможет():
    """Пакет посреди документа пуст — max_pages тут ни при чём, совет другой."""
    def страницы(nums):
        # Первая пара (1,2) отдаётся, вторая (3,4) — Гарант не вернул текста.
        if 1 in nums:
            return [f"<p>Текст {n}.</p>" for n in nums]
        return ["" for _ in nums]

    транспорт = ФейкТранспорт(_маршрутизатор(pageCount=10, страницы=страницы))
    к = client.GarantClient(transport=транспорт)

    ответ = к.document(doc_id=1, max_pages=40)

    предупреждение = ответ["_предупреждение"]
    assert "ОБОРВАЛАСЬ" in предупреждение
    assert "НЕ ПОМОЖЕТ" in предупреждение
    # Совет "увеличьте max_pages" здесь бесполезен и не должен звучать как
    # рекомендация — в этой ветке фраза не про max_pages.
    assert "Повторите вызов с бо́льшим max_pages" not in предупреждение
    assert ответ["страниц"] == 2


def test_недобор_без_обрыва_и_без_лимита():
    """Пакет вернулся, но часть страниц внутри него — без текста."""
    def страницы(nums):
        if nums == [1, 2]:
            return ["<p>Текст 1.</p>", ""]     # вторая страница пары пуста
        if nums == [3]:
            return ["<p>Текст 3.</p>"]
        raise AssertionError(f"неожиданный запрос страниц: {nums}")

    транспорт = ФейкТранспорт(_маршрутизатор(pageCount=3, страницы=страницы))
    к = client.GarantClient(transport=транспорт)

    ответ = к.document(doc_id=1, max_pages=40)

    предупреждение = ответ["_предупреждение"]
    assert "Получено 2 страниц из 3" in предупреждение
    assert "ОБОРВАЛАСЬ" not in предупреждение
    assert "длиннее max_pages" not in предупреждение
    assert ответ["страниц"] == 2
