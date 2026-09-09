# -*- coding: utf-8 -*-
"""Шаг 1 README для Windows исполняется по-настоящему (release-gate,
фаза E2, 09.09.2026): дефект жил в том, что ни одна проверка ни разу
не гоняла ФОРМУ ЗАПУСКА, которую README реально даёт пользователю для
копирования, — обе проверки в `test_установщики_ключи.py` зовут файл
формой `-File`.

Разница не косметическая. Шаг 1 превращает ТЕКСТ install.ps1 в
`[scriptblock]::Create(...)` и вызывает его через `&`. Внутри install.ps1
копилки сводки — `$Сделано`, `$Пропущено`, `$НеУдалось` — читаются и
меняются функциями `Отметить-*`; до починки они менялись через
`$script:Сделано += …`, а под ЭТОЙ формой запуска (в отличие от `-File`)
`$script:` внутри функции, определённой в scriptblock'е, указывает не на
ту переменную, которую читает итоговая сводка (докстринг самого
install.ps1, строки ~137-145: «счётчики оставались нулевыми, ветка "не
удалось" становилась недостижимой, и после настоящего отказа установщик
рапортовал "Код возврата 0"»). Форма `-File` этой ловушки не видит вовсе —
там сводка верна ВСЕГДА, и она ничего не защищает от этого дефекта.

Сети здесь нет, а шаг 1 качает install.ps1 через `irm <адрес>`. Тест
исполняет ЕЁ МЕХАНИЗМ, не сетевой вызов: ровно фрагмент `irm <URL>`
заменяется на локальное чтение ТЕМ ЖЕ СПОСОБОМ, каким текст приходит по
HTTP, — `[System.Text.Encoding]::UTF8.GetString(ReadAllBytes(...))`, а НЕ
`[IO.File]::ReadAllText(...)`. Разница проверена эмпирически на этой
машине (09.09.2026): `ReadAllText` — что с явной `[Text.Encoding]::UTF8`,
что без — сам режет метку порядка байтов через внутренний `StreamReader`
(первый символ результата НЕ `U+FEFF`), а `Encoding.UTF8.GetString(bytes)`
эту метку оставляет символом `U+FEFF`, ровно как это делает и
`Invoke-RestMethod` (комментарий install.ps1, строки ~40-47: «irm отдаёт
текст ВМЕСТЕ с меткой порядка байтов»). С `ReadAllText` `.TrimStart(
[char]0xFEFF)` внутри самой команды оказался бы мёртвым кодом, и тест
проверял бы не тот путь, каким реально идёт README.

`uv` подставлен заглушкой на `PATH` (`_fake_uv`): без неё тест либо requires
живой `uv` на машине, где его гоняют, либо тянет настоящий `uv` с
astral.sh по сети при первом запуске — ни то, ни другое не годится для
теста, обязанного быть сетенезависимым. Заглушке достаточно НАЙТИСЬ:
шаг 1 install.ps1 только спрашивает `Get-Command uv`, не вызывает её саму
на пути, которым идёт этот тест (плохой `-Источник` обрывает установку
раньше, чем дошло бы до `& uv tool install`).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_paths import _окружение  # noqa: F401
from test_установщики_ключи import _powershell, INSTALL_PS1

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _windows_step1_command() -> str:
    """Команда шага 1 для Windows — взята ИЗ README.md, не переписана руками."""
    текст = README.read_text(encoding="utf-8")
    # Форма README — «**Windows** — …»: маркер без точки, чтобы тест не
    # диктовал пунктуацию, а брал первый жирный «Windows» и блок за ним.
    маркер = "**Windows**"
    поз = текст.index(маркер)
    начало_ограды = текст.index("```", поз)
    начало_блока = текст.index("\n", начало_ограды) + 1
    конец_блока = текст.index("```", начало_блока)
    return текст[начало_блока:конец_блока].strip()


def test_readme_шаг1_windows_называет_scriptblock_trimstart_и_адрес():
    """Статическая проверка текста README — без исполнения."""
    команда = _windows_step1_command()
    assert "[scriptblock]::Create" in команда, (
        "шаг 1 обязан оборачивать текст install.ps1 в scriptblock — иначе "
        "это форма «irm | iex», а она с этим файлом не работает вовсе "
        "(комментарий install.ps1, строки ~40-44):\n" + команда)
    assert "TrimStart([char]0xFEFF)" in команда, (
        "шаг 1 обязан срезать метку порядка байтов, которую irm сама не "
        "отбрасывает:\n" + команда)
    assert ("raw.githubusercontent.com/semenboss95-design/garant-mcp/"
            "main/install.ps1") in команда, (
        "шаг 1 обязан качать install.ps1 именно из этого репозитория:\n"
        + команда)


def _windows_step1_script_без_сети(install_ps1: Path) -> str:
    """Внутренность `-Command` из шага 1: `irm <URL>` заменён на честное
    чтение локального файла тем же механизмом (BOM символом, не байтами
    без него), которым данные реально приходят по HTTP.
    """
    команда = _windows_step1_command()
    кв1 = команда.index('"')
    кв2 = команда.rindex('"')
    внутр = команда[кв1 + 1:кв2]

    идх = внутр.index("irm ")
    конец_url = внутр.index(")", идх)
    фрагмент = внутр[идх:конец_url]

    путь = str(install_ps1.resolve()).replace("'", "''")
    чтение = ("[System.Text.Encoding]::UTF8.GetString("
              "[System.IO.File]::ReadAllBytes('{}'))").format(путь)
    return внутр.replace(фрагмент, чтение)


def _fake_uv(sandbox: Path) -> Path:
    """`uv` на PATH — тест не должен зависеть ни от живого uv на машине,
    ни от сети к astral.sh (шаг 1 install.ps1 только ищет команду).
    """
    бин = sandbox / "fakebin"
    бин.mkdir(exist_ok=True)
    (бин / "uv.cmd").write_bytes(b"@echo off\r\nexit /b 0\r\n")
    return бин


def _окружение_для_шага1(sandbox: Path) -> dict:
    домашний = sandbox / "домашний"
    домашний.mkdir(exist_ok=True)
    fakebin = _fake_uv(sandbox)
    текущий_path = os.environ.get("PATH", "")
    return _окружение({
        "PATH": str(fakebin) + os.pathsep + текущий_path,
        "UV_TOOL_DIR": str(sandbox / "uv_tool_dir"),
        "UV_TOOL_BIN_DIR": str(sandbox / "uv_tool_bin"),
        "UV_CACHE_DIR": str(sandbox / "uv_cache"),
        "GARANT_HOME": str(sandbox / "garant_home"),
        "PLAYWRIGHT_BROWSERS_PATH": str(sandbox / "pw_browsers"),
        "USERPROFILE": str(домашний),
        "HOME": str(домашний),
        "APPDATA": str(домашний / "AppData" / "Roaming"),
    })


def test_readme_шаг1_windows_форма_даёт_честный_отказ_на_плохом_источнике(tmp_path):
    """A/B этим и нашли: `-Источник` на каталог без `pyproject.toml`.

    Под формой `-File` (см. `test_установщики_ключи.py`, косвенно) это
    даёт «не удалось: 1» и код 1. Под ЭТОЙ формой, до починки, сводка была
    подставной: «сделано: 0 / пропущено: 0 / не удалось: 0», код 0 —
    установка, которая ничего не сделала, рапортовала об успехе.

    Вне Windows пропускается ДО проверки на pwsh/powershell: на
    ubuntu-latest в GitHub Actions `pwsh` есть, и без этого пропуска тест
    там реально исполнялся бы. `_fake_uv` кладёт `uv.cmd` — расширение,
    которое `Get-Command` резолвит через `PATHEXT` только на Windows; на
    Linux `pwsh` ищет файл `uv` побуквенно, заглушки не находит, и
    install.ps1 (шаг «1/4 uv», строки ~343-355) на СВОЁМ пути, где
    `Есть-Команда 'uv'` возвращает `$false`, реально идёт в сеть за
    `https://astral.sh/uv/install.ps1` и исполняет его `Invoke-Expression`
    — ровно то, чего в этой среде не бывает и не должно требовать тест.
    """
    if os.name != "nt":
        pytest.skip("форма шага 1 README для Windows; вне Windows не исполняется")
    пара = _powershell()
    if пара is None:
        pytest.skip("ни pwsh, ни powershell не найдены в PATH")
    exe, кодировка = пара

    источник = tmp_path / "источник без pyproject"
    источник.mkdir()

    внутр = _windows_step1_script_без_сети(INSTALL_PS1)
    внутр += ' -Источник "{}" -БезВхода -БезРегистрации -БезАвтозапуска'.format(
        str(источник))

    r = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", внутр],
        cwd=str(tmp_path), env=_окружение_для_шага1(tmp_path),
        capture_output=True, timeout=90)
    вывод = (r.stdout + r.stderr).decode(кодировка, errors="replace")

    assert r.returncode != 0, (
        "форма запуска шага 1 README (scriptblock + TrimStart) с заведомо "
        "плохим -Источник обязана закончиться НЕНУЛЕВЫМ кодом — установка "
        "не сделала ничего, а сводка не вправе сказать «код 0»:\n" + вывод)
    assert "не удалось: 1" in вывод, (
        "сводка обязана честно показать один отказ. Подставная сводка "
        "«сделано: 0 / пропущено: 0 / не удалось: 0» под этой формой — "
        "ровно тот дефект, который здесь проверяется ($script:Сделано "
        "внутри функции, вызванной через & по scriptblock'у из "
        "[scriptblock]::Create, — другая переменная, чем читает сводка):\n"
        + вывод)


def test_readme_шаг1_windows_форма_справка(tmp_path):
    """`-Справка` через эту же форму обязана сработать как через `-File`:
    печатает синопсис и выходит кодом 0, не тронув ни одного шага.

    Вне Windows пропускается той же причиной, что и соседний тест этого
    файла: форма из README — команда для Windows PowerShell, вне Windows
    её никто не запускает, а прогонять её на чужой ОС в CI незачем.
    """
    if os.name != "nt":
        pytest.skip("форма шага 1 README для Windows; вне Windows не исполняется")
    пара = _powershell()
    if пара is None:
        pytest.skip("ни pwsh, ни powershell не найдены в PATH")
    exe, кодировка = пара

    внутр = _windows_step1_script_без_сети(INSTALL_PS1) + " -Справка"

    r = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", внутр],
        cwd=str(tmp_path), env=_окружение_для_шага1(tmp_path),
        capture_output=True, timeout=60)
    вывод = (r.stdout + r.stderr).decode(кодировка, errors="replace")

    assert r.returncode == 0, (
        "-Справка через эту форму запуска обязана выйти кодом 0:\n" + вывод)
    assert "-Источник" in вывод and "-БезВхода" in вывод, (
        "-Справка через форму README обязана напечатать синопсис ключей, "
        "как и через -File:\n" + вывод)
