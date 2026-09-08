# -*- coding: utf-8 -*-
"""Ключи установщиков и печать сводки при обрыве держатся на одном прогоне
руками (наряд этапа 03, фаза B, часть 2) — здесь это автоматизировано.

Три свойства, каждое со своей ценой:

1. `install.sh --help` называет все пять ключей (`--no-login`,
   `--no-register`, `--no-autostart`, `--source`, `--help`) и выходит
   кодом 0 — без этого посторонний проверяющий (§ install.sh, «ТРИ КЛЮЧА
   ИЗОЛЯЦИИ») не узнает, чем ограничить установку, не имея под рукой
   ни подписки, ни готовности трогать конфигурации своей машины.
2. То же для `install.ps1 -Help`: русские имена параметров И их английские
   псевдонимы — оба набора, потому что «ключ, который нельзя набрать без
   переключения раскладки, в чужой командной строке не набирают вовсе»
   (комментарий самого файла).
3. Сводка обязана печататься даже при аварийном обрыве — `trap on_exit
   EXIT` в install.sh. Проверяется РЕАЛЬНЫМ кодом файла, а не пересказом:
   в тесте вырезается кусок install.sh ОТ НАЧАЛА ДО `trap on_exit EXIT`
   включительно (там же — все функции, которые вызывает `on_exit`) и
   исполняется с принудительным `exit` сразу после установки ловушки —
   ровно та точка, где реальный скрипт мог бы упасть на необработанном
   сигнале или ошибке, не дойдя до шага 4.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"

_КЛЮЧИ_SH = ("--no-login", "--no-register", "--no-autostart",
            "--source", "--help")
_РУССКИЕ_PS1 = ("-Источник", "-БезВхода", "-БезРегистрации",
               "-БезАвтозапуска", "-Справка")
_АНГЛИЙСКИЕ_PS1 = ("-Source", "-NoLogin", "-NoRegister",
                   "-NoAutostart", "-Help")


@pytest.mark.skipif(shutil.which("sh") is None,
                    reason="нет sh в PATH — install.sh им и исполняется")
def test_install_sh_help_называет_все_пять_ключей():
    r = subprocess.run(["sh", str(INSTALL_SH), "--help"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    assert r.returncode == 0, (
        "install.sh --help обязан выходить кодом 0:\n" + r.stdout + r.stderr)
    отсутствуют = [к for к in _КЛЮЧИ_SH if к not in r.stdout]
    assert not отсутствуют, (
        "install.sh --help не называет ключи {}:\n{}".format(
            отсутствуют, r.stdout))


def _powershell() -> tuple[str, str] | None:
    """(путь, кодировка вывода). pwsh (7+) пишет консоль в UTF-8; Windows
    PowerShell 5.1 при перенаправлении stdout использует OEM-кодовую
    страницу консоли (cp866 на этой машине, не cp1251 и не UTF-8) —
    без этого различия кириллица в выводе `Write-Host` превращается
    в мусор, и сравнение строк ничего не находит НЕ ПОТОМУ, что ключей
    нет, а потому что тест сам не смог прочитать вывод."""
    pwsh = shutil.which("pwsh")
    if pwsh:
        return pwsh, "utf-8"
    powershell = shutil.which("powershell")
    if powershell:
        return powershell, "cp866"
    return None


@pytest.mark.skipif(_powershell() is None,
                    reason="ни pwsh, ни powershell не найдены в PATH")
def test_install_ps1_help_называет_все_ключи_и_псевдонимы():
    exe, кодировка = _powershell()
    r = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(INSTALL_PS1), "-Справка"],
        capture_output=True, timeout=30)
    вывод_байты = r.stdout + r.stderr
    assert r.returncode == 0, (
        "install.ps1 -Справка обязан выходить кодом 0:\n"
        + вывод_байты.decode(кодировка, errors="replace"))
    вывод = r.stdout.decode(кодировка, errors="replace")
    отсутствуют_рус = [к for к in _РУССКИЕ_PS1 if к not in вывод]
    отсутствуют_англ = [к for к in _АНГЛИЙСКИЕ_PS1 if к not in вывод]
    assert not отсутствуют_рус, (
        "install.ps1 -Справка не называет русские имена {}:\n{}".format(
            отсутствуют_рус, вывод))
    assert not отсутствуют_англ, (
        "install.ps1 -Справка не называет английские псевдонимы {}:\n{}"
        .format(отсутствуют_англ, вывод))


@pytest.mark.skipif(shutil.which("sh") is None,
                    reason="нет sh в PATH — install.sh им и исполняется")
def test_install_ps1_принимает_английские_псевдонимы(tmp_path):
    """Второй прогон тем же файлом с ДРУГИМ набором ключей — иначе первый
    тест доказывает только то, что PowerShell понимает СВОИ РОДНЫЕ имена
    параметров (это умеет любой cmdlet), а не то, что англ. псевдонимы
    в файле объявлены."""
    пара = _powershell()
    if пара is None:
        pytest.skip("ни pwsh, ни powershell не найдены в PATH")
    exe, кодировка = пара
    r = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(INSTALL_PS1), "-Help"],
        capture_output=True, timeout=30)
    вывод = r.stdout.decode(кодировка, errors="replace")
    assert r.returncode == 0, (
        "install.ps1 -Help (английский псевдоним) обязан сработать так "
        "же, как -Справка:\n" + вывод + r.stderr.decode(кодировка, errors="replace"))
    assert "-БезВхода" in вывод, (
        "-Help не дошёл до того же текста справки, что и -Справка:\n" + вывод)


# --------------------------------------------------------------------------
#  сводка при обрыве
# --------------------------------------------------------------------------

def _вырезка_до_ловушки() -> str:
    """Всё install.sh от начала ДО `trap on_exit EXIT` включительно —
    реальный код (функции mark_*, step, summary, on_exit, сама установка
    ловушки), не пересказ его логики."""
    текст = INSTALL_SH.read_text(encoding="utf-8")
    маркер = "trap on_exit EXIT"
    позиция = текст.index(маркер)
    конец_строки = текст.index("\n", позиция)
    return текст[:конец_строки + 1]


@pytest.mark.skipif(shutil.which("sh") is None,
                    reason="нет sh в PATH — install.sh им и исполняется")
def test_сводка_печатается_даже_при_аварийном_обрыве(tmp_path):
    """Обрыв ПОСЛЕ установки ловушки (`trap on_exit EXIT`), ДО шага 4 —
    ровно то, для чего ловушка существует по собственному комментарию
    файла: «нераскрытая переменная, сигнал, ошибка в неучтённом месте».
    Здесь — принудительный `exit 7` сразу за реальным кодом install.sh,
    вырезанным до места установки ловушки."""
    обрубок = tmp_path / "install_обрубок.sh"
    обрубок.write_text(
        _вырезка_до_ловушки() + "\nexit 7\n", encoding="utf-8")

    r = subprocess.run(["sh", str(обрубок)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)

    assert r.returncode == 7, (
        "trap не должен подменять код завершения — ожидался 7, "
        "получено {}:\n{}".format(r.returncode, r.stdout + r.stderr))
    assert "оборвался, не дойдя до сводки" in r.stderr, (
        "on_exit обязан назвать сам факт обрыва до штатной сводки:\n"
        + r.stderr)
    assert "сделано:" in (r.stdout + r.stderr), (
        "сводка (со счётчиками сделано/пропущено/не удалось) не "
        "напечаталась при обрыве:\n" + r.stdout + r.stderr)
