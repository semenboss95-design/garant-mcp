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


ЗАПИСЬ_CODEX = {"command": "garant-mcp", "env": {"PYTHONIOENCODING": "utf-8"}}


# ==========================================================================
#  1. чужой config.toml с комментариями и таблицами — цел байт-в-байт
# ==========================================================================

def test_чужой_toml_с_комментариями_и_таблицами_остаётся_цел(tmp_path):
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
    assert ок is True

    итог_текст = путь.read_text(encoding="utf-8")
    итог = tomllib.loads(итог_текст)
    assert итог["mcp_servers"]["garant"] == ЗАПИСЬ_CODEX
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

def test_повторная_регистрация_codex_не_трогает_файл(tmp_path):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    цель = _цель(путь)

    ок1, _ = register.зарегистрировать(цель)
    assert ок1 is True
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

def test_наш_блок_с_другим_содержимым_заменяется(tmp_path):
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
    assert ок is True
    assert "обновлена" in текст

    итог = tomllib.loads(путь.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"] == ЗАПИСЬ_CODEX, (
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
    for строка_блока in register.СТРОКИ_CODEX:
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
    домашний = tmp_path / "codex_домашний"
    домашний.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(домашний))

    цель = register.цель_codex()
    данные, состояние = register.прочитать(цель)
    assert состояние == register.НЕТ_ФАЙЛА

    ок, текст = register.зарегистрировать(цель)
    assert ок is True
    assert "добавлена" in текст

    файл = домашний / "config.toml"
    assert файл.exists()
    итог = tomllib.loads(файл.read_text(encoding="utf-8"))
    assert итог["mcp_servers"]["garant"] == ЗАПИСЬ_CODEX


# ==========================================================================
#  8. CRLF сохраняется
# ==========================================================================

def test_crlf_сохраняется(tmp_path):
    путь = tmp_path / "codex" / "config.toml"
    путь.parent.mkdir()
    исходный_текст = ("# comment\r\n"
                      "[mcp_servers.other]\r\n"
                      'command = "foo"\r\n')
    путь.write_bytes(исходный_текст.encode("utf-8"))
    цель = _цель(путь)

    ок, текст = register.зарегистрировать(цель)
    assert ок is True

    итог_текст = путь.read_bytes().decode("utf-8")
    assert исходный_текст in итог_текст, (
        "исходные CRLF-строки обязаны остаться байт-в-байт")
    assert итог_текст.count("\n") == итог_текст.count("\r\n"), (
        "файл жил с CRLF — ни одного «голого» \\n добавляться не должно:\n"
        + repr(итог_текст))
    for строка_блока in register.СТРОКИ_CODEX:
        assert строка_блока in итог_текст


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
    (codex_home / "config.toml").write_text(
        "\n".join(register.СТРОКИ_CODEX) + "\n", encoding="utf-8")

    r = _прогнать_doctor_codex(пакет, чужой_каталог, {"CODEX_HOME": str(codex_home)})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    значок, текст = _строка_codex(r.stdout)
    assert значок in _ЗНАЧКИ_OK, (
        "зарегистрированный Codex обязан быть зелёным пунктом:\n" + r.stdout)
    assert текст == "зарегистрирован"


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
    исходный_текст = ('[model]\nname = "gpt-5"\n\n'
                      + "\n".join(register.СТРОКИ_CODEX) + "\n")
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
    исходный_текст = "\n".join(register.СТРОКИ_CODEX) + "\n"
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
