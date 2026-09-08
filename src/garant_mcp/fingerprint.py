# -*- coding: utf-8 -*-
"""Отпечаток браузера, снятый с ЭТОЙ машины (решение 16, ограничение 3).

Ограничение 3 требует, чтобы отпечаток окна входа и рабочего контекста
СОВПАДАЛ: «Гарант» привязывает сессию к отпечатку, и cookie, выданные окну
с одним UA, в контексте с другим отвергаются с Unauthorized — а выглядит
это как «сессия истекла». Ограничение не требует, чтобы отпечаток был
одинаков у всех установок мира, а именно так и было: значения лежали
в публичном `endpoints.json`, снятые с машины автора, и уезжали в каждый
клон вместе с его часовым поясом.

Здесь значения берутся у машины и записываются в файл. Пока файл есть,
они не меняются — даже если ОС сменила часовой пояс. Иначе поездка или
перевод часов давали бы `Unauthorized` на живой сессии и повторный ручной
вход. Расхождение с ОС — предупреждение (`расхождение()`), а не ошибка.

НИ ОДНОГО mkdir И НИ ОДНОГО ЗАПУСКА БРАУЗЕРА НА ИМПОРТЕ. Модуль только
объявляет функции; снимает отпечаток `отпечаток()`, и зовут её явно.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from . import paths

# Окно — часть отпечатка, а не настройка установки: менять его пользователю
# незачем, а разойдясь между входом и работой, оно ломает ровно то, что
# ограничение 3 и защищает. Поэтому здесь, а не в config.py.
ОКНО = {"width": 1440, "height": 900}

# Чем headless выдаёт себя серверу. Замена — не маскировка ради обхода
# защиты: вход человек выполняет в ВИДИМОМ окне того же профиля, и там
# chromium представляется без этого слова. Оставить как есть значит
# предъявить «Гаранту» два разных браузера на одну сессию.
_HEADLESS_В_UA = "HeadlessChrome"


def _локаль_ос() -> str | None:
    """Локаль пользователя в форме BCP-47 («ru-RU»), как её видит ОС."""
    if os.name == "nt":
        try:
            import ctypes

            буфер = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(буфер, 85):
                return буфер.value or None
        except Exception:
            return None
        return None
    сырое = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
             or os.environ.get("LANG") or "")
    сырое = сырое.split(".")[0].split("@")[0].strip()
    if not re.fullmatch(r"[a-zA-Z]{2,3}([_-][A-Za-z0-9]{2,8})?", сырое or ""):
        return None
    return сырое.replace("_", "-")


def снять() -> dict:
    """Снять отпечаток с машины. Поднимает временный chromium.

    UA и часовой пояс спрашиваются у самого chromium: он и будет ходить
    к «Гаранту», и его собственный ответ — единственное значение, которое
    заведомо не разойдётся с тем, что увидит сервер. Профиль при этом
    не открывается: браузер запускается на пустом временном каталоге,
    поэтому монопольность профиля (ограничение 2) не нарушается.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        браузер = pw.chromium.launch(headless=True)
        try:
            страница = браузер.new_context().new_page()
            ua = страница.evaluate("navigator.userAgent")
            пояс = страница.evaluate(
                "Intl.DateTimeFormat().resolvedOptions().timeZone")
            язык = страница.evaluate("navigator.language")
        finally:
            браузер.close()

    return {
        "user_agent": (ua or "").replace(_HEADLESS_В_UA, "Chrome"),
        # Локаль у ОС, а не у chromium: navigator.language отдаёт «ru»,
        # без региона, а «Гарант» — сервис одной страны, и региональная
        # форма ближе к тому, что предъявляет обычный браузер.
        "locale": _локаль_ос() or язык or "ru-RU",
        "timezone_id": пояс,
        "viewport": dict(ОКНО),
        "снят": datetime.now().isoformat(timespec="seconds"),
    }


def прочитать() -> dict | None:
    """Отпечаток из файла. Битый или отсутствующий файл — None, не исключение."""
    try:
        данные = json.loads(paths.fingerprint_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return данные if isinstance(данные, dict) and данные.get("user_agent") else None


def записать(значения: dict) -> dict:
    """Записать отпечаток. Каталог создаётся здесь — внутри вызова, не на импорте."""
    paths.fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
    # Временный файл и замена: читателя, заставшего запись на середине,
    # ждал бы обрезанный JSON, а прочитать() истолковала бы это как
    # «отпечатка нет» — и следующий запуск снял бы ДРУГОЙ отпечаток.
    врем = paths.fingerprint_file.with_name(paths.fingerprint_file.name + ".tmp")
    врем.write_text(json.dumps(значения, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.replace(врем, paths.fingerprint_file)
    return значения


def отпечаток() -> dict:
    """Действующий отпечаток: из файла, а если файла нет — снять и записать.

    Одна функция на оба случая (вход и работа) намеренно: два разных
    источника значений и есть тот способ разойтись, который ограничение 3
    запрещает. Кто пришёл первым, тот и снял; второй читает файл.
    """
    готовое = прочитать()
    if готовое:
        return готовое
    return записать(снять())


def параметры_контекста() -> dict:
    """Аргументы браузерного контекста Playwright из действующего отпечатка."""
    ф = отпечаток()
    out = {}
    for ключ in ("user_agent", "locale", "timezone_id", "viewport"):
        if ф.get(ключ):
            out[ключ] = ф[ключ]
    return out


def _смещение_пояса(имя: str | None) -> int | None:
    """Смещение зоны от UTC в минутах. None — если зону негде взять."""
    if not имя:
        return None
    try:
        from zoneinfo import ZoneInfo

        сдвиг = datetime.now(ZoneInfo(имя)).utcoffset()
    except Exception:
        # На Windows базы зон в стандартной библиотеке нет (пакет tzdata
        # в зависимостях не значится). Сверить нечем — молчим, а не
        # выдумываем расхождение.
        return None
    return None if сдвиг is None else int(сдвиг.total_seconds() // 60)


def расхождение() -> list[str]:
    """Чем записанный отпечаток разошёлся с текущей ОС. Пусто — не разошёлся.

    Для `garant doctor`: это ПРЕДУПРЕЖДЕНИЕ, а не ошибка. Расхождение
    означает, что ОС сменила пояс или локаль после входа; отпечаток при
    этом менять нельзя — сессия привязана к прежнему.
    """
    ф = прочитать()
    if not ф:
        return []
    вышло = []
    локаль = _локаль_ос()
    if локаль and ф.get("locale") and локаль != ф["locale"]:
        вышло.append("локаль ОС {} против {} в отпечатке".format(
            локаль, ф["locale"]))
    записанное = _смещение_пояса(ф.get("timezone_id"))
    сейчас = datetime.now().astimezone().utcoffset()
    if записанное is not None and сейчас is not None:
        текущее = int(сейчас.total_seconds() // 60)
        if текущее != записанное:
            вышло.append("часовой пояс ОС сместился на {:+d} мин против {} "
                         "в отпечатке".format(текущее - записанное,
                                              ф["timezone_id"]))
    return вышло


def описание() -> str:
    """Одна строка об отпечатке. Для логов и диагностики."""
    ф = прочитать()
    if not ф:
        return "отпечаток: не снят (снимется при первом входе)"
    окно = ф.get("viewport") or {}
    строка = "отпечаток: {} · {} · {}×{} · снят {}".format(
        ф.get("locale"), ф.get("timezone_id"),
        окно.get("width"), окно.get("height"), ф.get("снят"))
    разница = расхождение()
    return строка + ("; расхождение с ОС: " + "; ".join(разница) if разница else "")
