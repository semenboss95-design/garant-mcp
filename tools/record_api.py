# -*- coding: utf-8 -*-
"""
record_api — автозапись API «Гаранта» (асинхронная версия).

Почему async: в синхронном API Playwright вызов resp.json() внутри обработчика
события падает, и все ответы молча теряются. В async-обработчике чтение тела
штатно, поэтому запись работает.

Пишет ВСЕ XHR/fetch, а не только JSON: если тело не разобралось как JSON,
сохраняется текстовый образец. Разбираться, что боевое, будет build_endpoints.py.

Запуск:  python record_api.py
         python record_api.py --phase document     переснять одну фазу
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).parent
REC_DIR = HERE / "recordings"
STATE = HERE / "storage_state.json"

BASE = "https://internet.garant.ru"

PHASES = [
    ("search", "ПОИСК",
     "Введите в строку поиска: статья 97 УПК РФ  →  нажмите «Найти».\n"
     "   Дождитесь, пока список результатов полностью отрисуется."),
    ("document", "ДОКУМЕНТ",
     "Откройте из результатов ст. 97 УПК РФ (полный текст документа)."),
    ("article", "СТАТЬЯ / ПУНКТ",
     "ВНУТРИ уже открытого документа перейдите к конкретной статье\n"
     "   через оглавление или ссылку. Новый поиск не делать."),
    ("revisions", "РЕДАКЦИИ",
     "Откройте карточку документа / «Редакции» / «Изменения документа»\n"
     "   — список версий акта с изменяющими актами."),
    ("practice", "ПРАКТИКА",
     "Поиск судебной практики: «продление домашнего ареста»\n"
     "   ОБЯЗАТЕЛЬНО с фильтром по суду (кассационный) и по периоду."),
]

NOISE = re.compile(
    r"(google|yandex|metrika|analytics|counter|doubleclick|\.png|\.jpg|\.gif|\.svg"
    r"|\.css|\.js\b|\.woff|favicon|/ping|/heartbeat|sentry)", re.I)


def truncate(obj, depth=0):
    if depth > 6:
        return "...(глубже не пишем)"
    if isinstance(obj, str):
        return obj if len(obj) <= 1500 else obj[:1500] + f"...(всего {len(obj)} символов)"
    if isinstance(obj, list):
        return [truncate(x, depth + 1) for x in obj[:5]] + (
            [f"...(всего {len(obj)} элементов)"] if len(obj) > 5 else [])
    if isinstance(obj, dict):
        return {k: truncate(v, depth + 1) for k, v in obj.items()}
    return obj


class Recorder:
    def __init__(self):
        self.buf: list[dict] = []
        self.armed = True

    async def on_response(self, resp):
        if not self.armed:
            return
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            if NOISE.search(req.url):
                return

            ctype = (await resp.header_value("content-type")) or ""
            body, is_json = None, False
            try:
                raw = await resp.text()
            except Exception:
                raw = ""
            if raw:
                try:
                    body = json.loads(raw)
                    is_json = True
                except Exception:
                    body = raw[:2000]

            headers = {k: v for k, v in (await req.all_headers()).items()
                       if k.lower() not in {
                           "cookie", "user-agent", "referer", "origin", "host",
                           "accept-encoding", "content-length", "connection",
                       } and not k.lower().startswith("sec-")}

            post = req.post_data
            if post:
                try:
                    post = json.loads(post)
                except Exception:
                    pass

            self.buf.append({
                "url": req.url,
                "path": req.url.replace(BASE, ""),
                "method": req.method,
                "headers": headers,
                "body": post,
                "status": resp.status,
                "content_type": ctype,
                "is_json": is_json,
                "response_sample": truncate(body),
            })
            print(f"      · поймано: {req.method} {req.url[:78]}", flush=True)
        except Exception as e:
            print(f"      ! пропущен ответ ({type(e).__name__})", flush=True)


async def ask(prompt: str):
    """input() без блокировки цикла событий — иначе перехват встанет."""
    return await asyncio.get_running_loop().run_in_executor(None, input, prompt)


async def main(only_phase: str | None = None):
    REC_DIR.mkdir(exist_ok=True)
    rec = Recorder()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(
            storage_state=str(STATE) if STATE.exists() else None)
        ctx.on("response", lambda r: asyncio.create_task(rec.on_response(r)))

        page = await ctx.new_page()
        await page.goto(BASE, wait_until="domcontentloaded")

        if not STATE.exists():
            await ask("\nВойдите в Гарант в открывшемся окне, затем нажмите Enter здесь → ")
            await ctx.storage_state(path=str(STATE))
            print(f"Сессия сохранена: {STATE.name}")

        phases = [p for p in PHASES if not only_phase or p[0] == only_phase]
        print("\n" + "=" * 68)
        print("ЗАПИСЬ API. Делайте действие в браузере — строки «поймано» должны")
        print("появляться прямо во время действия. Затем Enter здесь.")
        print("=" * 68)

        for key, title, hint in phases:
            rec.buf.clear()
            print(f"\n▶ ФАЗА: {title}")
            print(f"   {hint}")
            await ask("   Сделали? Enter → ")

            recs = list(rec.buf)
            (REC_DIR / f"{key}.json").write_text(
                json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
            n_json = sum(1 for r in recs if r["is_json"])
            print(f"   Записано запросов: {len(recs)} (из них с JSON: {n_json})")
            if not recs:
                print("   ⚠ ПУСТО. Действие не дало ни одного XHR-запроса.")
                print("     Либо страница отдала всё из кэша, либо действие не выполнено.")
                print(f"     Переснять только эту фазу: python record_api.py --phase {key}")
            elif not n_json:
                print("   ⚠ Запросы есть, но ни один не вернул JSON — проверим на шаге 3.")

        await ctx.storage_state(path=str(STATE))
        rec.armed = False
        await browser.close()

    print(f"\nГотово. Записи в {REC_DIR}")
    print("Дальше:  python build_endpoints.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=[p[0] for p in PHASES])
    a = ap.parse_args()
    asyncio.run(main(a.phase))
