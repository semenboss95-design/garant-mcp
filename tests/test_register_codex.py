# -*- coding: utf-8 -*-
"""`register.py`: цель Codex (TOML, раздел `mcp_servers`) — тот же смысл слияния,

что и у JSON-целей `test_register.py`, но записанный текстом, а не через
`json.dumps`: `tomllib` в стандартной библиотеке только читает, писателя TOML
там нет, а прогон чужого файла через любой сериализатор стёр бы комментарии
и форму чужих записей. Здесь проверяется ровно то, ради чего это сделано
текстом: чужое (комментарии, другие таблицы, CRLF) остаётся байт-в-байт,
битый файл не трогается никогда, а запись в форме, которую текстовое слияние
резать не умеет (inline-таблица, точечные ключи), — честный отказ, а не
попытка угадать.

Прямой импорт `garant_mcp.register` безопасен без песочницы (см. докстринг
`test_register.py`): побочных эффектов на импорте нет, файловые операции
идут по путям внутри `tmp_path`. `register.цель_codex()` в пунктах 1-6, 8
не вызывается вовсе — используется `register.Цель(..., формат=register.TOML)`
с путём внутри `tmp_path`, ровно как `тестовый клиент` в `test_register.py`.
Пункт 7 проверяет именно `цель_codex()`, и там `CODEX_HOME` подменена
`monkeypatch.setenv` на каталог внутри `tmp_path` — настоящий `~/.codex`
этой машины не читается и не пишется ни разу.

Пункты 9-10 (doctor, unregister/uninstall без ключей) идут через подпроцесс
`garant_mcp.cli`, как в `tests/test_doctor_починка.py` и
`tests/test_cli_необратимое.py`, — оттуда же переиспользованы песочница
(`_песочница`, `_окружение`, `чужой_каталог`, `_свободный_порт`) и обвязка
uninstall (`_garant`, `_чужой_дом`, `_окружение_с_чужим_домом`,
`_поставить_заглушку_autostart`): второй процесс, трогающий планировщик
или реестр демона этой машины, здесь так же недопустим, как и там.

Все проверки здесь доказаны красными на коде HEAD без Codex (`register.py`
не знает `TOML`/`цель_codex`/`формат=`, `cli.py` не печатает и не снимает
ничего под именем «Codex») — прогоном `verify_redness.py` по копии
`register.py`/`cli.py` из `git show HEAD:...` (сам скрипт вне репозитория,
как и полагается разовому доказательству, а не постоянному тесту).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import garant_mcp.register as register  # noqa: E402

from test_paths import _песочница, _окружение, чужой_каталог  # noqa: E402,F401
from test_port import _свободный_порт  # noqa: E402,F401
from test_cli_необратимое import (  # noqa: E402
    _garant, _чужой_дом, _окружение_с_чужим_домом, _поставить_заглушку_autostart,
)


def _цель(путь: Path, требует_каталог: bool = True) -> register.Цель:
    return register.Цель("тестовый Codex", путь, требует_каталог=требует_каталог,
                          формат=register.TOML)


def _не_создан_бак(путь: Path) -> bool:
    return not путь.with_name(путь.name + ".bak").exists()


def _подготовить_команду(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """`shutil.which("garant-mcp")` находит команду по ПУТИ — детерминированная
    замена настоящей установки, которой на машине теста нет вовсе (см.
    `register._путь_команды`: без which и без соседа `garant` регистрация
    честно отказывает, а не пишет имя «на всякий случай», решение записано
    в докстроке `_строки_codex`).

    Файл на диске — не декорация: `_путь_команды` берёт результат `which`
    как есть, но происхождение пути (PATH или сосед пульта) для теста
    неважно, важен только сам путь, поэтому подменяется `shutil.which`
    напрямую, а не `sys.argv`."""
    команда = tmp_path / "bin" / "garant-mcp.exe"
    команда.parent.mkdir(parents=True, exist_ok=True)
    команда.write_bytes(b"")
    monkeypatch.setattr(register.shutil, "which", lambda имя: str(команда))
    return str(команда)


def _запись_codex(команда: str) -> dict:
    """Ожидаемое содержимое нашей таблицы `[mcp_servers.garant]` — форма,
    которую строит `_строки_codex`, разобранная обратно `tomllib`: путь,
    потолок старта, кодировка вывода. Раньше здесь стояла константа
    `ЗАПИСЬ_CODEX` с именем `"garant-mcp"` — с решения «ПУТЬ, А НЕ ИМЯ»
    (докстрока `_строки_codex`, живая приёмка 09.09.2026) команда всегда
    путь конкретной установки, и общей константы для него больше нет."""
    return {"command": команда, "startup_timeout_sec": register.ТАЙМАУТ_СТАРТА_CODEX,
            "env": {"PYTHONIOENCODING": "utf-8"}}


# ==========================================================================
#  1. чужой config.toml с комментариями и таблицами — цел байт-в-байт
# ==========================================================================

def test_чужой_toml_с_комментариями_и_таблицами_остаётся_цел(tmp_path, monkeypatch):
    команда = _подготовить_команду(monkeypatch, tmp_path)
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = (
        "# конфигурация Codex CLI\n"
        "# правь руками, инструмент трогает только свой блок\n"
        "\n"
        "[model]\n"
        'name = "gpt-5"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "foo"\n'
        'args = ["--bar", "baz"]\n'
        'env = { FOO = "1" }\n'
    )
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    данные_до, состояние = register.прочитать(цель)
    assert состояние == register.ЕСТЬ

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст

    итог_текст = путь.read_text(encoding="utf-8")
    итог = tomllib.loads(итог_текст)
    assert итог["mcp_servers"]["garant"] == _запись_codex(команда)
    assert итог["mcp_servers"]["other"] == данные_до["mcp_servers"]["other"], (
        "чужая таблица mcp_servers.other обязана остаться нетронутой")
    assert итог["model"] == данные_до["model"], (
        "посторонняя таблица [model] обязана остаться нетронутой")
    assert исходный_текст in итог_текст, (
        "чужой текст (комментарии, таблицы) обязан остаться байт-в-байт — "
        "запись дописывается, а не пересобирает файл")

    бак = путь.with_name(путь.name + ".bak")
    assert бак.exists(), "перед первой записью обязана остаться копия .bak"
    assert бак.read_bytes() == исходный_текст.encode("utf-8"), (
        ".bak обязан содержать ИСХОДНОЕ содержимое")


# ==========================================================================
#  2. повторная регистрация не трогает файл
# ==========================================================================

def test_повторная_регистрация_codex_не_трогает_файл(tmp_path, monkeypatch):
    _подготовить_команду(monkeypatch, tmp_path)
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    цель = _цель(путь)

    ок1, текст1 = register.зарегистрировать(цель)
    assert ок1 is True, текст1
    содержимое_после_первого = путь.read_bytes()
    время_первого = путь.stat().st_mtime_ns

    ок2, текст2 = register.зарегистрировать(цель)
    assert ок2 is True
    assert "уже зарегистрирован" in текст2
    assert путь.read_bytes() == содержимое_после_первого, (
        "повторная регистрация не должна переписывать файл вовсе")
    assert путь.stat().st_mtime_ns == время_первого, (
        "повторная регистрация тронула mtime — значит, записала файл заново")


# ==========================================================================
#  3. наш блок с другим содержимым — заменяется, чужое цело
# ==========================================================================

def test_наш_блок_с_другим_содержимым_заменяется(tmp_path, monkeypatch):
    команда = _подготовить_команду(monkeypatch, tmp_path)
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = (
        "# конфигурация Codex CLI\n"
        "[mcp_servers.garant]\n"
        'command = "old-command"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "foo"\n'
    )
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст
    assert "обновлена" in текст

    итог = tomllib.loads(путь.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"] == _запись_codex(команда), (
        "старое содержимое нашего блока обязано быть заменено правильным")
    assert итог["mcp_servers"]["other"] == {"command": "foo"}, (
        "чужая таблица рядом с нашим блоком обязана остаться нетронутой")
    assert "# конфигурация Codex CLI" in путь.read_text(encoding="utf-8"), (
        "комментарий перед нашим блоком обязан остаться на месте")


# ==========================================================================
#  4. unregister убирает ТОЛЬКО нашу запись; повтор ничего не пишет
# ==========================================================================

def test_unregister_codex_убирает_только_нашу_запись(tmp_path):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    строки = [
        "# codex config",
        "[model]",
        'name = "gpt-5"',
        "",
        "[mcp_servers.garant]",
        'command = "garant-mcp"',
        'env = { PYTHONIOENCODING = "utf-8" }',
        "",
        "[mcp_servers.other]",
        'command = "foo"',
    ]
    исходный_текст = "\n".join(строки) + "\n"
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.снять(цель)
    assert ок is True
    assert "убрана" in текст

    итог_текст = путь.read_text(encoding="utf-8")
    итог = tomllib.loads(итог_текст)
    assert "garant" not in итог.get("mcp_servers", {}), (
        "наша запись обязана исчезнуть")
    assert итог["mcp_servers"]["other"] == {"command": "foo"}, (
        "чужая таблица обязана остаться на месте")
    assert итог["model"] == {"name": "gpt-5"}, (
        "посторонняя таблица [model] обязана остаться на месте")
    assert "# codex config" in итог_текст, "чужой комментарий обязан остаться"
    # Строки САМОГО ЭТОГО setup'а (не общей `_строки_codex` — тут нет
    # `startup_timeout_sec` вовсе, это блок ДО его появления в записи),
    # они же индексы 4:7 в `строки` выше ("[mcp_servers.garant]", "command
    # = ...", "env = ..."): каждая обязана исчезнуть целиком при снятии.
    for строка_блока in строки[4:7]:
        assert строка_блока not in итог_текст, (
            "строка нашего блока {!r} обязана исчезнуть целиком".format(строка_блока))

    бак = путь.with_name(путь.name + ".bak")
    assert бак.read_bytes() == исходный_текст.encode("utf-8")

    # повторное снятие — файл не трогаем вовсе
    содержимое_после_первого = путь.read_bytes()
    время_первого = путь.stat().st_mtime_ns
    бак_после_первого = бак.read_bytes()

    ок2, текст2 = register.снять(цель)
    assert ок2 is True
    assert "нечего" in текст2
    assert путь.read_bytes() == содержимое_после_первого
    assert путь.stat().st_mtime_ns == время_первого
    assert бак.read_bytes() == бак_после_первого, (
        "повторное снятие не должно трогать даже .bak — писать было нечего")


# ==========================================================================
#  5. битый TOML — не трогаем НИКОГДА, ни register, ни unregister
# ==========================================================================

def test_битый_toml_не_переписывается_никогда(tmp_path):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = '[bad\nkey = "unterminated string\n'
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    данные, состояние = register.прочитать(цель)
    assert состояние == register.БИТЫЙ
    assert register.битый(цель) == register.БИТЫЙ_TOML

    ок, текст = register.зарегистрировать(цель)
    assert ок is False, "регистрация на битом TOML обязана отказать"
    assert "TOML" in текст
    assert путь.read_bytes() == исходный_текст.encode("utf-8")
    assert _не_создан_бак(путь)

    ок2, текст2 = register.снять(цель)
    assert ок2 is False
    assert путь.read_bytes() == исходный_текст.encode("utf-8")
    assert _не_создан_бак(путь)


# ==========================================================================
#  6. запись в другой форме (inline-таблица, точечные ключи) — честный отказ
# ==========================================================================

ИНАЯ_ФОРМА = [
    pytest.param(
        '[mcp_servers]\ngarant = { command = "x" }\nother = { command = "y" }\n',
        id="inline-таблица"),
    pytest.param(
        'mcp_servers.garant.command = "x"\nmcp_servers.other.command = "y"\n',
        id="точечные-ключи"),
]


@pytest.mark.parametrize("исходный_текст", ИНАЯ_ФОРМА)
def test_иная_форма_register_отказывает(tmp_path, исходный_текст):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    данные, состояние = register.прочитать(цель)
    assert состояние == register.ЕСТЬ, "форма сама по себе валидный TOML"

    ок, текст = register.зарегистрировать(цель)
    assert ок is False
    assert "форме" in текст, (
        "отказ обязан назвать причину прямым текстом:\n" + текст)
    assert путь.read_bytes() == исходный_текст.encode("utf-8")
    assert _не_создан_бак(путь)


@pytest.mark.parametrize("исходный_текст", ИНАЯ_ФОРМА)
def test_иная_форма_снять_отказывает(tmp_path, исходный_текст):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.снять(цель)
    assert ок is False
    assert "форме" in текст
    assert путь.read_bytes() == исходный_текст.encode("utf-8")
    assert _не_создан_бак(путь)


# ==========================================================================
#  7. цель_codex(): нет каталога -> не установлен; каталог есть, файла нет
#     -> создаём с нашей записью
# ==========================================================================

def test_цель_codex_без_каталога_ничего_не_создаёт(tmp_path, monkeypatch):
    """`CODEX_HOME` указывает на каталог, которого нет вовсе."""
    домашний = tmp_path / "нет_такого_codex"
    monkeypatch.setenv("CODEX_HOME", str(домашний))

    цель = register.цель_codex()
    assert цель.путь == домашний / "config.toml"
    assert цель.формат == register.TOML

    данные, состояние = register.прочитать(цель)
    assert состояние == register.НЕТ_КАТАЛОГА

    ок, текст = register.зарегистрировать(цель)
    assert ок is False
    assert not домашний.exists(), (
        "каталог Codex, которого нет, создавать нельзя — это объявило бы "
        "установленным клиента, которого не ставили")
    assert "не установлен" in текст


def test_цель_codex_каталог_есть_файла_нет_создаёт_с_нашей_записью(tmp_path, monkeypatch):
    команда = _подготовить_команду(monkeypatch, tmp_path)
    домашний = tmp_path / "codex_домашний"
    домашний.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(домашний))

    цель = register.цель_codex()
    данные, состояние = register.прочитать(цель)
    assert состояние == register.НЕТ_ФАЙЛА

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст
    assert "добавлена" in текст

    файл = домашний / "config.toml"
    assert файл.exists()
    итог = tomllib.loads(файл.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"] == _запись_codex(команда)


# ==========================================================================
#  8. CRLF сохраняется
# ==========================================================================

def test_crlf_сохраняется(tmp_path, monkeypatch):
    команда = _подготовить_команду(monkeypatch, tmp_path)
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = ("# comment\r\n"
                      "[mcp_servers.other]\r\n"
                      'command = "foo"\r\n')
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст

    итог_текст = путь.read_bytes().decode("utf-8")
    assert исходный_текст in итог_текст, (
        "исходные CRLF-строки обязаны остаться байт-в-байт")
    assert итог_текст.count("\n") == итог_текст.count("\r\n"), (
        "файл жил с CRLF — ни одного «голого» \\n добавляться не должно:\n"
        + repr(итог_текст))
    for строка_блока in register._строки_codex(команда):
        assert строка_блока in итог_текст


# ==========================================================================
#  8b. `_путь_команды` и `_строка_toml`: покрытие, добавленное вторым
#      заходом (после правки python-dev A) — раньше константа `СТРОКИ_CODEX`
#      скрывала эти два места целиком, тестов на них не было вовсе.
# ==========================================================================

def test_регистрация_без_which_и_без_соседа_отказывает(tmp_path, monkeypatch):
    """Ни `which`, ни файл рядом с пультом не находят команду — регистрация
    обязана честно отказать, а не записать имя «на всякий случай»: запись,
    по которой Codex ничего не поднимет, выглядит сделанной работой, и
    человек идёт искать поломку в сервере, а не в PATH."""
    monkeypatch.setattr(register.shutil, "which", lambda имя: None)
    monkeypatch.setattr(sys, "argv", ["pytest"])   # argv[0].stem != "garant"

    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = "# ничего своего\n"
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is False
    assert "uv tool update-shell" in текст, (
        "отказ обязан назвать команду, которой чинится PATH:\n" + текст)
    assert путь.read_bytes() == исходный_текст.encode("utf-8"), (
        "файл не тронут — отказ ДО единой записи")
    assert _не_создан_бак(путь)


def test_регистрация_повторно_с_новым_путём_заменяет_блок(tmp_path, monkeypatch):
    """Переустановка в другой каталог: `which` теперь находит команду по
    ДРУГОМУ пути. Повторная регистрация обязана заменить блок на новый
    путь, а не посчитать старую запись «уже зарегистрированной» — иначе
    Codex продолжит запускать файл, которого больше нет."""
    старый = tmp_path / "old" / "garant-mcp.exe"
    старый.parent.mkdir(parents=True)
    старый.write_bytes(b"")
    monkeypatch.setattr(register.shutil, "which", lambda имя: str(старый))

    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    цель = _цель(путь)
    ок1, текст1 = register.зарегистрировать(цель)
    assert ок1 is True, текст1
    assert "добавлена" in текст1

    новый = tmp_path / "new" / "garant-mcp.exe"
    новый.parent.mkdir(parents=True)
    новый.write_bytes(b"")
    monkeypatch.setattr(register.shutil, "which", lambda имя: str(новый))

    ок2, текст2 = register.зарегистрировать(цель)
    assert ок2 is True, текст2
    assert "обновлена" in текст2, (
        "смена пути обязана распознаться как ОБНОВЛЕНИЕ блока, а не "
        "«уже зарегистрирован»:\n" + текст2)

    итог = tomllib.loads(путь.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"]["command"] == str(новый)


def test_путь_с_обратными_слэшами_литеральная_строка_toml(tmp_path, monkeypatch):
    """Путь Windows — литеральной строкой TOML (`'…'`): экранирования там
    нет вовсе, и обратные слэши не удваиваются. `tomllib` обязан прочитать
    РОВНО тот путь, который отдал `which`, посимвольно."""
    путь_команды = tmp_path / "Program Files" / "garant-mcp.exe"
    путь_команды.parent.mkdir(parents=True)
    путь_команды.write_bytes(b"")
    monkeypatch.setattr(register.shutil, "which", lambda имя: str(путь_команды))

    цель_путь = tmp_path / "codex" / "config.toml"
    цель_путь.parent.mkdir()
    цель = _цель(цель_путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст

    сырой = цель_путь.read_text(encoding="utf-8")
    assert "'" + str(путь_команды) + "'" in сырой, (
        "путь без одинарной кавычки обязан лечь литеральной строкой TOML, "
        "без экранирования обратных слэшей:\n" + сырой)
    итог = tomllib.loads(сырой)
    assert итог["mcp_servers"]["garant"]["command"] == str(путь_команды)


def test_путь_с_одинарной_кавычкой_basic_строка_toml(tmp_path, monkeypatch):
    """Путь с одинарной кавычкой (редкий, но легальный каталог на диске) —
    литеральная строка TOML такую кавычку выразить не может, и `_строка_toml`
    берёт обычную, с экранированием обратных слэшей. Круг «путь → строка
    TOML → `tomllib.loads`» обязан вернуть тот же путь, каким он был."""
    путь_команды = tmp_path / "o'brien" / "garant-mcp.exe"
    путь_команды.parent.mkdir(parents=True)
    путь_команды.write_bytes(b"")
    monkeypatch.setattr(register.shutil, "which", lambda имя: str(путь_команды))

    цель_путь = tmp_path / "codex" / "config.toml"
    цель_путь.parent.mkdir()
    цель = _цель(цель_путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is True, текст

    итог = tomllib.loads(цель_путь.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"]["command"] == str(путь_команды), (
        "путь обязан дойти дословно даже с кавычкой внутри")


def test_снятие_блока_с_чужим_содержимым_по_заголовку(tmp_path):
    """Заголовок `[mcp_servers.garant]` найден и снимается ПО ЗАГОЛОВКУ —
    не важно, совпадает ли содержимое таблицы с тем, что мы бы записали
    сейчас: человек мог поправить строку руками, а снятие обязано убрать
    ВСЮ нашу таблицу целиком, а не отказаться из-за несовпадения содержимого
    (в отличие от `_наш_блок`, который несовпадение читает — но только при
    РЕГИСТРАЦИИ, чтобы решить «обновлена» или «уже зарегистрирован»; у
    `снять` такого вопроса нет вовсе)."""
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = (
        "[mcp_servers.garant]\n"
        'command = "/совсем/другой/путь/garant-mcp"\n'
        "# правил руками, добавил свой комментарий\n"
        "\n"
        "[mcp_servers.other]\n"
        'command = "foo"\n'
    )
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.снять(цель)
    assert ок is True, текст
    assert "убрана" in текст

    итог_текст = путь.read_text(encoding="utf-8")
    итог = tomllib.loads(итог_текст)
    assert "garant" not in итог.get("mcp_servers", {})
    assert итог["mcp_servers"]["other"] == {"command": "foo"}
    assert "совсем/другой/путь" not in итог_текст, (
        "чужое (для сегодняшней записи) содержимое НАШЕЙ таблицы обязано "
        "уйти целиком — по заголовку, а не по совпадению содержимого")


# ==========================================================================
#  8c. `статус_codex`: юнит на все состояния, отдельно от подпроцесса doctor
# ==========================================================================

def test_статус_codex_файл_есть_зарегистрирован(tmp_path):
    """Базовый случай: запись наша, путь в `command` ведёт в существующий
    файл — тот же ответ, что у обычного `статус`."""
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    команда = tmp_path / "install" / "garant-mcp.exe"
    команда.parent.mkdir()
    команда.write_bytes(b"")
    путь.write_text("\n".join(register._строки_codex(str(команда))) + "\n",
                    encoding="utf-8")
    цель = _цель(путь)

    assert register.статус(цель) == "зарегистрирован"
    assert register.статус_codex(цель) == "зарегистрирован"


def test_статус_codex_файла_нет_запись_без_файла(tmp_path):
    """Запись наша, но по пути `command` ничего нет — ровно то, что
    оставляет переустановка пакета в другой каталог."""
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    команда = tmp_path / "install" / "давно_нет" / "garant-mcp.exe"
    путь.write_text("\n".join(register._строки_codex(str(команда))) + "\n",
                    encoding="utf-8")
    цель = _цель(путь)

    assert register.статус_codex(цель) == register.ЗАПИСЬ_БЕЗ_ФАЙЛА


def test_статус_codex_путь_ведёт_в_каталог_запись_без_файла(tmp_path):
    """Путь в `command` существует, но это каталог, а не файл — Codex его
    не запустит так же, как пустоту, и `статус_codex` обязан отличить
    каталог от файла, а не спрашивать одно лишь `Path.exists()`."""
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    # Имя каталога обязано быть «garant-mcp» — иначе `зарегистрирован()`
    # не узнает запись СВОЕЙ по имени файла в `command`, и тест проверил бы
    # не «каталог вместо файла», а «незарегистрирован».
    каталог_вместо_файла = tmp_path / "install" / "garant-mcp"
    каталог_вместо_файла.mkdir(parents=True)
    путь.write_text(
        "\n".join(register._строки_codex(str(каталог_вместо_файла))) + "\n",
        encoding="utf-8")
    цель = _цель(путь)

    assert register.статус(цель) == "зарегистрирован", (
        "предпосылка теста: имя каталога совпадает с именем команды")
    assert register.статус_codex(цель) == register.ЗАПИСЬ_БЕЗ_ФАЙЛА


@pytest.mark.parametrize("состояние_файла", ["нет_каталога", "нет_файла",
                                              "пустой", "не_зарегистрирован",
                                              "битый"])
def test_статус_codex_без_нашей_записи_не_смотрит_на_файл(tmp_path, состояние_файла):
    """Во всех состояниях, где `статус` не отвечает «зарегистрирован»,
    `статус_codex` обязан вернуть РОВНО тот же текст, что и `статус` — про
    файл по пути `command` там и спрашивать нечего, вопроса о его
    существовании ещё не встало."""
    путь = tmp_path / "codex" / "config.toml"
    if состояние_файла == "нет_каталога":
        pass   # ни каталога, ни файла
    elif состояние_файла == "нет_файла":
        путь.parent.mkdir()
    elif состояние_файла == "пустой":
        путь.parent.mkdir()
        путь.write_bytes(b"")
    elif состояние_файла == "не_зарегистрирован":
        путь.parent.mkdir()
        путь.write_text('[mcp_servers.other]\ncommand = "foo"\n', encoding="utf-8")
    else:   # битый — тот же текст, что в тесте раздела 5 (заведомо не TOML)
        путь.parent.mkdir()
        путь.write_bytes('[bad\nkey = "unterminated string\n'.encode("utf-8"))
    цель = _цель(путь)

    ожидание = register.статус(цель)
    assert ожидание != "зарегистрирован", (
        "проверка теста устарела: этот setup стал читаться как «зарегистрирован»")
    assert register.статус_codex(цель) == ожидание


# ==========================================================================
#  8d. расхождение `статус` / `статус_codex` на протухшей записи — свойство,
#      на которое ОПИРАЕТСЯ cli.py: `_цели()` (строка ~989) и `uninstall()`
#      (строка ~1309) решают «снимать ли Codex без явного ключа» ПО
#      `register.статус`, а не по `статус_codex`, — иначе протухшая запись
#      (файл давно удалён переустановкой) перестала бы быть «нашей» для
#      unregister/uninstall, и `garant uninstall` без ключей продолжал бы
#      оставлять клиенту ссылку на несуществующий сервер. Реального вызова
#      cli.py здесь нет — это опора на факт, что `статус` и `статус_codex`
#      расходятся ровно в вопросе существования файла, и e2e того же
#      сценария (запись с несуществующим `command`) уже прогоняется через
#      подпроцесс в разделе 10 (`test_unregister_без_ключей_снимает_codex_
#      если_есть`, `test_uninstall_снимает_codex_если_есть`).
# ==========================================================================

def test_статус_и_статус_codex_расходятся_на_протухшей_записи(tmp_path):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    команда = tmp_path / "install" / "давно_удалённый" / "garant-mcp"
    путь.write_text("\n".join(register._строки_codex(str(команда))) + "\n",
                    encoding="utf-8")
    цель = _цель(путь)

    assert register.статус(цель) == "зарегистрирован", (
        "протухшая запись обязана оставаться «нашей» для статус — иначе "
        "unregister/uninstall без ключей перестанут её видеть и снимать")
    assert register.статус_codex(цель) == register.ЗАПИСЬ_БЕЗ_ФАЙЛА, (
        "для doctor та же запись обязана читаться как ведущая в никуда")


# ==========================================================================
#  9. doctor: три состояния Codex
# ==========================================================================

_ЗНАЧКИ_OK = {"✓", "[ok]"}
_ЗНАЧКИ_НЕЙТРАЛЬНО = {"·", "[-]"}
_ЗНАЧКИ_ПЛОХО = {"✗", "[X]"}


def _прогнать_doctor_codex(пакет: Path, cwd: Path, доп: dict):
    порт = _свободный_порт()
    базовые = {"PYTHONPATH": str(пакет.parent),
               "GARANT_HOME": str(cwd / "состояние"),
               "GARANT_PORT": str(порт)}
    базовые.update(доп)
    return subprocess.run(
        [sys.executable, "-m", "garant_mcp.cli", "doctor"],
        cwd=str(cwd), env=_окружение(базовые),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)


def _строка_codex(вывод: str) -> tuple[str, str]:
    """(значок, текст) строки диагностики «Codex», либо падает с ассертом."""
    for строка in вывод.splitlines():
        части = строка.split(None, 1)
        if len(части) == 2 and части[1].startswith("Codex:"):
            return части[0], части[1][len("Codex:"):].strip()
    raise AssertionError("строка диагностики «Codex» не найдена в выводе:\n" + вывод)


def _красных_пунктов(вывод: str) -> int:
    if "красных пунктов нет." in вывод:
        return 0
    совпадение = re.search(r"красных пунктов:\s*(\d+)", вывод)
    if совпадение:
        return int(совпадение.group(1))
    raise AssertionError("итоговая строка о красных пунктах не найдена:\n" + вывод)


def test_doctor_codex_зарегистрирован_зелёный(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    codex_home = tmp_path / "codex_ок"
    codex_home.mkdir()
    # `doctor` спрашивает `register.статус_codex`, а не `статус` (решение 40,
    # правка python-dev E): помимо «наша ли это запись» он проверяет, что
    # ФАЙЛ по пути `command` существует — иначе зелёного не будет (см. тест
    # ниже, «файл_переустановлен_красный»). Раньше здесь годилась любая
    # строка без файла на диске; теперь путь обязан вести в реальный файл.
    команда = tmp_path / "install" / "bin" / "garant-mcp.exe"
    команда.parent.mkdir(parents=True)
    команда.write_bytes(b"")
    (codex_home / "config.toml").write_text(
        "\n".join(register._строки_codex(str(команда))) + "\n", encoding="utf-8")

    r = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_home)})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    значок, текст = _строка_codex(r.stdout)
    assert значок in _ЗНАЧКИ_OK, (
        "зарегистрированный Codex с живым файлом по пути `command` обязан "
        "быть зелёным пунктом:\n" + r.stdout)
    assert текст == "зарегистрирован"


def test_doctor_codex_файл_переустановлен_красный(tmp_path, чужой_каталог):
    """Запись есть (Codex когда-то зарегистрирован), но файла по пути
    `command` больше нет — ровно то, что оставляет переустановка пакета
    в другой каталог (решение 40, `register.ЗАПИСЬ_БЕЗ_ФАЙЛА`). Выбор Codex
    уже сделан, поэтому это поломка, а не необязательность: значок обязан
    стать красным, текст — назвать причину и переустановку, а под ним —
    команда починки `garant register --codex`."""
    пакет = _песочница(tmp_path)
    codex_home = tmp_path / "codex_протух"
    codex_home.mkdir()
    команда = tmp_path / "install_старый" / "garant-mcp.exe"   # не создаём
    (codex_home / "config.toml").write_text(
        "\n".join(register._строки_codex(str(команда))) + "\n", encoding="utf-8")

    r = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_home)})
    строки = r.stdout.splitlines()
    значок = текст = None
    следующая = ""
    for i, строка in enumerate(строки):
        части = строка.split(None, 1)
        if len(части) == 2 and части[1].startswith("Codex:"):
            значок, текст = части[0], части[1][len("Codex:"):].strip()
            следующая = строки[i + 1] if i + 1 < len(строки) else ""
            break
    assert значок is not None, "строка «Codex» не найдена:\n" + r.stdout
    assert значок in _ЗНАЧКИ_ПЛОХО, (
        "запись, ведущая в исчезнувший файл, обязана быть красным пунктом, "
        "а не нейтральным «Codex необязателен»:\n" + r.stdout)
    assert значок not in _ЗНАЧКИ_OK
    assert текст == register.ЗАПИСЬ_БЕЗ_ФАЙЛА + " — пакет переустановлен в другой каталог?", (
        "текст обязан называть ровно причину `ЗАПИСЬ_БЕЗ_ФАЙЛА`:\n" + текст)
    assert "починить:" in следующая and "garant register --codex" in следующая, (
        "красный пункт обязан нести исполнимую команду починки под собой:\n"
        + следующая)


def test_doctor_счётчик_красных_растёт_на_один_от_протухшей_записи_codex(tmp_path, чужой_каталог):
    """Как и у битого TOML (тест выше по файлу): «файл переустановлен»
    обязан добавлять РОВНО один красный пункт относительно нормального
    зелёного случая — не два и не ноль, то есть счётчик действительно
    считает именно эту строку, а не что-то попутное."""
    пакет = _песочница(tmp_path)

    codex_ок = tmp_path / "codex_ок2"
    codex_ок.mkdir()
    команда_ок = tmp_path / "install_ок" / "garant-mcp.exe"
    команда_ок.parent.mkdir(parents=True)
    команда_ок.write_bytes(b"")
    (codex_ок / "config.toml").write_text(
        "\n".join(register._строки_codex(str(команда_ок))) + "\n", encoding="utf-8")
    r_ок = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_ок)})
    значок_ок, _ = _строка_codex(r_ок.stdout)
    assert значок_ок in _ЗНАЧКИ_OK
    красных_ок = _красных_пунктов(r_ок.stdout)

    codex_протух = tmp_path / "codex_протух2"
    codex_протух.mkdir()
    команда_протух = tmp_path / "install_протух" / "garant-mcp.exe"   # не создаём
    (codex_протух / "config.toml").write_text(
        "\n".join(register._строки_codex(str(команда_протух))) + "\n", encoding="utf-8")
    r_протух = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_протух)})
    значок_протух, _ = _строка_codex(r_протух.stdout)
    assert значок_протух in _ЗНАЧКИ_ПЛОХО
    красных_протух = _красных_пунктов(r_протух.stdout)

    assert красных_протух == красных_ок + 1, (
        "протухшая запись обязана добавлять РОВНО один красный пункт "
        "относительно зелёного случая: зелёный={}, протух={}\n"
        "--- зелёный ---\n{}\n--- протух ---\n{}".format(
            красных_ок, красных_протух, r_ок.stdout, r_протух.stdout))


def test_doctor_codex_не_зарегистрирован_не_красный(tmp_path, чужой_каталог):
    """Codex необязателен: подписки ChatGPT нет не у всех — «записи нет» не
    поломка. Значок обязан быть нейтральным, а не красным, и нести команду.
    """
    пакет = _песочница(tmp_path)
    codex_home = tmp_path / "codex_нет"   # каталог намеренно не создаём

    r = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_home)})
    значок, текст = _строка_codex(r.stdout)
    assert значок in _ЗНАЧКИ_НЕЙТРАЛЬНО, (
        "не зарегистрированный (и потому не установленный) Codex не должен "
        "быть красным:\n" + r.stdout)
    assert значок not in _ЗНАЧКИ_ПЛОХО
    assert "необязательно" in текст
    assert "garant register --codex" in текст


def test_doctor_codex_битый_как_битый_json(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    codex_home = tmp_path / "codex_битый"
    codex_home.mkdir()
    (codex_home / "config.toml").write_bytes(b"not valid [[[ toml")

    r = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_home)})
    строки = r.stdout.splitlines()
    значок = текст = None
    следующая = ""
    for i, строка in enumerate(строки):
        части = строка.split(None, 1)
        if len(части) == 2 and части[1].startswith("Codex:"):
            значок, текст = части[0], части[1][len("Codex:"):].strip()
            следующая = строки[i + 1] if i + 1 < len(строки) else ""
            break
    assert значок is not None, "строка «Codex» не найдена:\n" + r.stdout
    assert значок in _ЗНАЧКИ_ПЛОХО, (
        "битый TOML обязан быть красным пунктом, как битый JSON у остальных "
        "клиентов:\n" + r.stdout)
    assert "TOML" in текст
    assert "починить:" in следующая and "garant register --codex" in следующая, (
        "красный пункт обязан нести исполнимую команду починки под собой:\n"
        + следующая)


def test_doctor_счётчик_красных_не_растёт_от_незарегистрированного_codex(tmp_path, чужой_каталог):
    """Сравнение в лоб: «не зарегистрирован» и «битый» отличаются РОВНО на
    один красный пункт — тот, что добавляет битый файл. Если бы «не
    зарегистрирован» тоже считался красным, разница была бы иной.
    """
    пакет = _песочница(tmp_path)

    codex_нет = tmp_path / "codex_нет"
    r_нет = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_нет)})
    значок_нет, _ = _строка_codex(r_нет.stdout)
    assert значок_нет in _ЗНАЧКИ_НЕЙТРАЛЬНО
    красных_нет = _красных_пунктов(r_нет.stdout)

    codex_битый = tmp_path / "codex_битый2"
    codex_битый.mkdir()
    (codex_битый / "config.toml").write_bytes(b"not valid [[[ toml")
    r_битый = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_битый)})
    значок_битый, _ = _строка_codex(r_битый.stdout)
    assert значок_битый in _ЗНАЧКИ_ПЛОХО
    красных_битый = _красных_пунктов(r_битый.stdout)

    assert красных_битый == красных_нет + 1, (
        "битый Codex обязан добавлять РОВНО один красный пункт относительно "
        "не зарегистрированного (который красных не добавляет вовсе): "
        "не зарегистрирован={}, битый={}\n--- не зарегистрирован ---\n{}\n"
        "--- битый ---\n{}".format(красных_нет, красных_битый,
                                   r_нет.stdout, r_битый.stdout))


# ==========================================================================
#  10. unregister/uninstall без ключей снимают Codex, если он есть
# ==========================================================================

def test_unregister_без_ключей_снимает_codex_если_есть(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    codex_cfg = codex_home / "config.toml"
    # `unregister` снимает по ЗАГОЛОВКУ (`register._снять_toml`), путь
    # в записи существующим быть не обязан — важно только имя `garant-mcp`
    # на конце, как и в доктор-тесте выше.
    команда = str(tmp_path / "install" / "garant-mcp")
    исходный_текст = ('[model]\nname = "gpt-5"\n\n'
                      + "\n".join(register._строки_codex(команда)) + "\n")
    codex_cfg.write_text(исходный_текст, encoding="utf-8")

    r = _garant(пакет, чужой_каталог, ["unregister"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     "CODEX_HOME": str(codex_home)}, дом))

    assert r.returncode == 0, r.stdout + r.stderr
    assert "Codex" in r.stdout, (
        "unregister без ключей обязан был заметить существующую запись "
        "Codex и снять её:\n" + r.stdout)

    итог = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert "garant" not in итог.get("mcp_servers", {}), (
        "запись Codex обязана быть снята")
    assert итог["model"] == {"name": "gpt-5"}, "чужая таблица обязана уцелеть"


def test_uninstall_снимает_codex_если_есть(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    codex_cfg = codex_home / "config.toml"
    команда = str(tmp_path / "install" / "garant-mcp")
    исходный_текст = "\n".join(register._строки_codex(команда)) + "\n"
    codex_cfg.write_text(исходный_текст, encoding="utf-8")

    r = _garant(пакет, чужой_каталог, ["uninstall", "--yes"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     "CODEX_HOME": str(codex_home),
                     "GARANT_PORT": str(_свободный_порт())}, дом))

    assert r.returncode == 0, r.stdout + r.stderr
    assert "Codex" in r.stdout, (
        "uninstall обязан был назвать Codex среди снимаемых регистраций:\n"
        + r.stdout)

    итог_текст = codex_cfg.read_text(encoding="utf-8")
    итог = tomllib.loads(итог_текст) if итог_текст.strip() else {}
    assert "garant" not in итог.get("mcp_servers", {}), (
        "запись Codex обязана быть снята после uninstall")

    бак = codex_cfg.with_name(codex_cfg.name + ".bak")
    assert бак.read_text(encoding="utf-8") == исходный_текст


def test_uninstall_без_codex_не_поминает_его(tmp_path, чужой_каталог):
    """Codex не зарегистрирован (каталога ~/.codex нет вовсе) — uninstall не
    должен называть его среди снимаемых: клиента, которого нет, в списке
    снятия быть не должно (симметрично doctor: не поломка, а несостоявшийся
    выбор).
    """
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"
    codex_home = tmp_path / "codex_нет"   # намеренно не создаём

    r = _garant(пакет, чужой_каталог, ["uninstall", "--yes"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     "CODEX_HOME": str(codex_home),
                     "GARANT_PORT": str(_свободный_порт())}, дом))

    assert r.returncode == 0, r.stdout + r.stderr
    строка_снято = next((с for с in r.stdout.splitlines()
                        if с.startswith("Будет снято")), "")
    assert "Codex" not in строка_снято, (
        "клиента, которого нет, в списке «Будет снято» быть не должно:\n"
        + строка_снято)
    assert not codex_home.exists(), (
        "каталог Codex, которого не было, не должен появиться из ниоткуда")
