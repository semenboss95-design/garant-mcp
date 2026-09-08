# -*- coding: utf-8 -*-
"""`update`/`uninstall`/`migrate` (`cli.py`, этап 02, шаг 4) — цена ошибки

необратима: повторный ручной вход в подписку, потерянная чужая запись
в конфигурации MCP-клиента, поломанный копированием на ходу профиль
Chromium. Здесь проверяется именно это, в порядке убывания цены:

  1. `uninstall` БЕЗ `--yes` не трогает ничего.
  2. `--purge` БЕЗ отдельного подтверждения профиль не удаляет.
  3. `uninstall` не разрушает чужие записи в конфигурации клиента.
  4. `migrate` не пишет в источник (сверка побайтово, а не по факту
     существования).
  5. `migrate` при занятом замке профиля отказывает.
  6. `migrate` не затирает непустую цель.
  7. `migrate` переносит отпечаток; отдельно — источник без отпечатка.
  8. `_чем_поставлено()` на трёх раскладках: uv tool, pipx, обычная.

Песочница и её доказательство
------------------------------
Два разных механизма защищают эту машину, и оба доказаны здесь эмпирически,
а не обещаны:

  · `register.цель_desktop()`/`цель_code()` читают `Path.home()` и
    `os.environ["APPDATA"]`. Переопределение `USERPROFILE`/`HOME`/`APPDATA`
    в окружении ПОДПРОЦЕССА — это не подмена по `PATH` (тот случай, что
    провалился 06.09.2026: разрешение имени исполняемых файлов у ОС своё
    и с ожиданиями не совпадает). Это переменные, которые сам `pathlib`
    и сам `register.py` читают ЯВНО в своём коде — подмена работает
    настолько же надёжно, насколько надёжен сам интерпретатор.
    Доказательство — `test_подмена_home_перехватывается`: `garant status`
    в песочнице с чужим `USERPROFILE` печатает путь ВНУТРИ песочницы,
    а не настоящий `Path.home()` этой машины.
  · `autostart.py` трогает планировщик задач/launchd/systemd НАСТОЯЩЕЙ
    машины — это прямо в списке «способно изменить машину», трогать нельзя
    ни разу. Подмена — не окружение, а функция в КОПИИ КОДА: файл
    `autostart.py` внутри песочницы `_песочница()` замещается двойником,
    который не запускает ни одного процесса и только возвращает
    заготовленный ответ. Доказательство — `test_подмена_autostart_перехватывается`:
    `garant autostart status` в песочнице с подменённым файлом печатает
    маркер двойника, а не результат настоящего `schtasks`/`launchctl`.

Обе подмены проходят НИЖЕ уровня `cli.py`: сам код пульта, который
проверяется, не тронут ни строкой.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from test_paths import _песочница, _окружение, чужой_каталог  # noqa: F401
from test_port import _свободный_порт  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --------------------------------------------------------------------------
#  общая обвязка
# --------------------------------------------------------------------------

# Маркер двойника autostart — по нему отличаем «сработал стенд» от
# «случайно вызвался настоящий модуль» в каждом тесте, который его ставит.
МАРКЕР_ДВОЙНИКА = "(тестовый двойник autostart, машина не тронута)"

_ЗАГЛУШКА_AUTOSTART = '''# -*- coding: utf-8 -*-
"""Двойник autostart.py для тестов uninstall — НЕ трогает планировщик/
launchd/systemd этой машины. Кладётся ПОВЕРХ копии в песочнице теста,
настоящий src/garant_mcp/autostart.py не тронут ничем.
"""
from __future__ import annotations

МАРКЕР = "{маркер}"
ВЫЗОВЫ = []


class НетПоддержки(RuntimeError):
    pass


def включить():
    ВЫЗОВЫ.append("включить")
    return True, МАРКЕР + ": включён"


def выключить():
    ВЫЗОВЫ.append("выключить")
    return True, МАРКЕР + ": выключен"


def состояние():
    return МАРКЕР


def проверить():
    return "нет", МАРКЕР
'''.format(маркер=МАРКЕР_ДВОЙНИКА)


def _поставить_заглушку_autostart(пакет: Path) -> None:
    (пакет / "autostart.py").write_text(_ЗАГЛУШКА_AUTOSTART, encoding="utf-8")


def _чужой_дом(tmp_path: Path) -> Path:
    """Каталог, изображающий Path.home() чужого пользователя — не настоящий."""
    дом = tmp_path / "чужой_дом"
    дом.mkdir()
    return дом


def _окружение_с_чужим_домом(доп: dict, дом: Path) -> dict:
    """USERPROFILE/HOME/APPDATA — на песочницу, а не на эту машину.

    `register.цель_desktop()` на Windows берёт `APPDATA`, `цель_code()` —
    `Path.home()` (который на Windows читает `USERPROFILE`). Оба
    переопределены, иначе `garant uninstall`/`setup` писали бы в НАСТОЯЩИЕ
    `%APPDATA%\\Claude\\claude_desktop_config.json` и `~/.claude.json`.
    """
    e = dict(доп)
    e["USERPROFILE"] = str(дом)
    e["HOME"] = str(дом)
    e["APPDATA"] = str(дом / "AppData" / "Roaming")
    return e


def _garant(пакет: Path, cwd: Path, аргументы: list[str],
           доп: dict | None = None, вход: str | None = None,
           timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "garant_mcp.cli", *аргументы],
        cwd=str(cwd),
        env=_окружение(доп or {}),
        input=вход,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def _хэши(корень: Path) -> dict:
    """sha256 каждого файла, относительным путём — для побайтовой сверки."""
    итог = {}
    for p in sorted(корень.rglob("*")):
        if p.is_file():
            итог[str(p.relative_to(корень))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return итог


def _старая_установка(корень: Path, с_отпечатком: bool = True) -> Path:
    """Каталог старой установки: `<корень>/chrome_profile` + fingerprint.json.

    Профиль опознаётся `_похоже_на_профиль()` по каталогу `Default` —
    кладём его и немного содержимого, чтобы сверка контрольных сумм после
    переноса проверяла настоящие байты, а не пустые каталоги.
    """
    профиль = корень / "chrome_profile"
    (профиль / "Default").mkdir(parents=True)
    (профиль / "Default" / "Cookies").write_bytes(os.urandom(97))
    (профиль / "Local State").write_text(
        json.dumps({"os_crypt": {"encrypted_key": "abc"}}), encoding="utf-8")
    (профиль / "лог_старой_установки.log").write_text(
        "строка из прошлой сессии\n" * 5, encoding="utf-8")
    if с_отпечатком:
        (корень / "fingerprint.json").write_text(
            json.dumps({"user_agent": "Mozilla/5.0 (старая установка)",
                       "timezone_id": "Pacific/Auckland"},
                       ensure_ascii=False),
            encoding="utf-8")
    return профиль


ЛОК_ДЕРЖАТЕЛЬ = '''
import msvcrt, sys, time
f = open(sys.argv[1], "a+b")
f.seek(0)
msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
print("locked", flush=True)
time.sleep(120)
'''


class _ДержательЗамка:
    """Настоящий процесс, держащий ТОТ ЖЕ замок, что и `daemon.занять_профиль()`.

    Не мок функции проверки: замок берётся ПОСТОРОННИМ процессом той же
    системной блокировкой (`msvcrt.locking`), какой пользуется настоящий
    демон, — `cli._профиль_занят()` в самом тесте не тронут ничем и должен
    обнаружить занятость по-настоящему.
    """

    def __init__(self, путь_лока: Path):
        путь_лока.parent.mkdir(parents=True, exist_ok=True)
        self.p = subprocess.Popen(
            [sys.executable, "-c", ЛОК_ДЕРЖАТЕЛЬ, str(путь_лока)],
            stdout=subprocess.PIPE, text=True)
        строка = self.p.stdout.readline()
        assert строка.strip() == "locked", (
            "держатель замка не подтвердил захват — тест ничего не проверяет: "
            + строка)

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


# ==========================================================================
#  ДОКАЗАТЕЛЬСТВО ПЕСОЧНИЦЫ (правило §4 «песочница доказывается»)
# ==========================================================================

def test_подмена_home_перехватывается(tmp_path, чужой_каталог):
    """Безобидный вызов ДО всякой разрушительной проверки: `garant status`
    не трогает демона (он не поднимается), но само чтение путей внутри
    процесса обязано показать ПЕСОЧНЫЙ дом, а не настоящий `Path.home()`
    этой машины.
    """
    пакет = _песочница(tmp_path)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"
    код = ("import sys; sys.path.insert(0, r'{site}');"
          "from pathlib import Path; print(Path.home())"
          ).format(site=пакет.parent)
    r = subprocess.run(
        [sys.executable, "-c", код], cwd=str(чужой_каталог),
        env=_окружение(_окружение_с_чужим_домом(
            {"GARANT_HOME": str(состояние)}, дом)),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30)
    напечатанный_дом = Path(r.stdout.strip())
    assert напечатанный_дом == дом, (
        "подмена USERPROFILE/HOME не перехватила Path.home(): получено {!r}, "
        "ожидалось {!r}. Без этого тесты uninstall писали бы в настоящую "
        "конфигурацию Claude этой машины.".format(напечатанный_дом, дом))
    assert напечатанный_дом != Path.home(), (
        "подмена вернула НАСТОЯЩИЙ Path.home() этой машины — песочницы нет")


def test_подмена_autostart_перехватывается(tmp_path, чужой_каталог):
    """Безобидный вызов: `autostart status` не пишет ничего, только читает —
    и именно поэтому годится доказать подмену перед тем, как тем же файлом
    воспользуется `uninstall` (который писать как раз может).
    """
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    состояние = tmp_path / "состояние"
    r = _garant(пакет, чужой_каталог, ["autostart", "status"],
                {"PYTHONPATH": str(пакет.parent),
                 "GARANT_HOME": str(состояние)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert МАРКЕР_ДВОЙНИКА in r.stdout, (
        "autostart status обязан был напечатать маркер ДВОЙНИКА, а напечатал "
        "{!r} — подмена файла в песочнице не сработала, дальше запускать "
        "uninstall НЕЛЬЗЯ: он тронул бы настоящий планировщик.".format(r.stdout))


# ==========================================================================
#  1. uninstall БЕЗ --yes не трогает ничего
# ==========================================================================

def test_uninstall_без_yes_не_трогает_ничего(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"

    # канарейки: конфигурация клиента с чужой записью и уже существующий
    # профиль — оба обязаны остаться байт-в-байт.
    desktop_cfg = дом / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    desktop_cfg.parent.mkdir(parents=True)
    desktop_cfg.write_text(json.dumps(
        {"mcpServers": {"чужой": {"command": "foo"}}}), encoding="utf-8")
    code_cfg = дом / ".claude.json"
    code_cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    профиль = состояние / "chrome_profile"
    профиль.mkdir(parents=True)
    (профиль / "живая_сессия.dat").write_bytes(os.urandom(50))

    до = {"desktop": desktop_cfg.read_bytes(), "code": code_cfg.read_bytes(),
          "профиль": _хэши(профиль)}

    r = _garant(пакет, чужой_каталог, ["uninstall"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние)}, дом))

    assert r.returncode == 1, (
        "без --yes и без интерактивного stdin uninstall обязан отказать:\n"
        + r.stdout + r.stderr)
    assert desktop_cfg.read_bytes() == до["desktop"], (
        "конфигурация Claude Desktop изменилась хотя подтверждения не было")
    assert code_cfg.read_bytes() == до["code"], (
        "конфигурация Claude Code изменилась хотя подтверждения не было")
    assert _хэши(профиль) == до["профиль"], (
        "профиль подписки тронут хотя подтверждения не было")
    assert профиль.exists(), "профиль исчез хотя подтверждения не было"


# ==========================================================================
#  2. --purge БЕЗ отдельного подтверждения профиль не удаляет
# ==========================================================================

def test_purge_без_подтверждения_профиль_остаётся(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"
    профиль = состояние / "chrome_profile"
    профиль.mkdir(parents=True)
    (профиль / "живая_сессия.dat").write_bytes(os.urandom(64))
    до = _хэши(профиль)

    # --yes снимает ТОЛЬКО общий вопрос, а не второе, более опасное
    # подтверждение на удаление профиля (docstring `uninstall`, `cli.py`);
    # stdin не терминал — значит второе подтверждение получить неоткуда,
    # и это ИМЕННО тот путь, который проверяется.
    r = _garant(пакет, чужой_каталог, ["uninstall", "--purge", "--yes"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     # Изолированный порт: без него `stop()` внутри uninstall
                     # мог бы наткнуться на НАСТОЯЩИЙ демон этой машины на
                     # порту по умолчанию (обнаружено эмпирически при первом
                     # прогоне — на машине разработки демон реально жив) и
                     # отказал бы по своей, посторонней причине раньше, чем
                     # дело дойдёт до проверяемого здесь свойства.
                     "GARANT_PORT": str(_свободный_порт())}, дом))

    assert профиль.exists(), (
        "--purge без отдельного подтверждения удалил профиль — "
        "недопустимо, цена ошибки: повторный ручной вход в подписку.\n"
        + r.stdout + r.stderr)
    assert _хэши(профиль) == до, "содержимое профиля изменилось"
    assert r.returncode != 0, (
        "не сделанное (профиль не удалён, хотя просили) обязано быть видно "
        "в коде возврата, а не спрятано за общим «успехом»")
    assert "НЕ удалён" in r.stdout or "не удал" in r.stdout.lower(), (
        "вывод обязан явно сказать, что профиль НЕ удалён:\n" + r.stdout)


# ==========================================================================
#  3. uninstall не разрушает чужие записи в конфигурации клиента
# ==========================================================================

def test_uninstall_не_разрушает_чужие_записи(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    _поставить_заглушку_autostart(пакет)
    дом = _чужой_дом(tmp_path)
    состояние = tmp_path / "состояние"

    desktop_cfg = дом / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    desktop_cfg.parent.mkdir(parents=True)
    чужой_сервер = {"command": "foo", "args": ["--bar"]}
    desktop_cfg.write_text(json.dumps({"mcpServers": {
        "чужой-сервер": чужой_сервер,
        "garant": {"command": "garant-mcp"},
    }, "прочееНеТронуто": 42}), encoding="utf-8")

    code_cfg = дом / ".claude.json"
    code_cfg.write_text(json.dumps({"mcpServers": {
        "другой-чужой": {"command": "bar"},
    }}), encoding="utf-8")

    r = _garant(пакет, чужой_каталог, ["uninstall", "--yes"],
                _окружение_с_чужим_домом(
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     # см. комментарий в test_purge_без_подтверждения…: без
                     # изолированного порта `stop()` натыкается на настоящий
                     # демон этой машины на порту по умолчанию.
                     "GARANT_PORT": str(_свободный_порт())}, дом))

    assert r.returncode == 0, r.stdout + r.stderr

    desktop_итог = json.loads(desktop_cfg.read_text(encoding="utf-8"))
    assert desktop_итог["mcpServers"]["чужой-сервер"] == чужой_сервер, (
        "чужая запись в Claude Desktop обязана пережить uninstall")
    assert "garant" not in desktop_итог["mcpServers"], (
        "своя запись обязана быть снята")
    assert desktop_итог["прочееНеТронуто"] == 42, (
        "посторонний ключ верхнего уровня потерян")

    code_итог = json.loads(code_cfg.read_text(encoding="utf-8"))
    assert code_итог["mcpServers"]["другой-чужой"] == {"command": "bar"}, (
        "чужая запись в Claude Code обязана пережить uninstall")


# ==========================================================================
#  4. migrate НЕ ПИШЕТ В ИСТОЧНИК — побайтовая сверка
# ==========================================================================

def test_migrate_не_пишет_в_источник(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    старая = tmp_path / "старая_установка"
    старая.mkdir()
    профиль_источника = _старая_установка(старая)
    состояние = tmp_path / "состояние"   # ещё не существует — создаст migrate

    до_профиль = _хэши(профиль_источника)
    до_отпечаток = (старая / "fingerprint.json").read_bytes()
    до_mtime = {p: p.stat().st_mtime_ns for p in профиль_источника.rglob("*")
               if p.is_file()}

    r = _garant(пакет, чужой_каталог, ["migrate", str(старая)],
                {"PYTHONPATH": str(пакет.parent),
                 "GARANT_HOME": str(состояние),
                 "GARANT_PORT": str(_свободный_порт())})

    assert r.returncode == 0, r.stdout + r.stderr
    assert _хэши(профиль_источника) == до_профиль, (
        "содержимое исходного профиля изменилось — migrate обязан быть "
        "чистым копированием: перенос профиля, затеянный ради сохранения "
        "входа, не должен ставить этот же вход под угрозу.\n"
        + r.stdout)
    assert (старая / "fingerprint.json").read_bytes() == до_отпечаток, (
        "исходный fingerprint.json изменился")
    новые_mtime = {p: p.stat().st_mtime_ns for p in профиль_источника.rglob("*")
                  if p.is_file()}
    assert новые_mtime == до_mtime, (
        "mtime файлов источника изменился — что-то писало в исходный каталог")


# ==========================================================================
#  5. migrate при занятом замке профиля отказывает
# ==========================================================================

def test_migrate_при_занятом_замке_отказывает(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    старая = tmp_path / "старая_установка"
    старая.mkdir()
    _старая_установка(старая)
    состояние = tmp_path / "состояние"
    состояние.mkdir()

    держатель = _ДержательЗамка(состояние / "profile.lock")
    try:
        r = _garant(пакет, чужой_каталог, ["migrate", str(старая)],
                    {"PYTHONPATH": str(пакет.parent),
                     "GARANT_HOME": str(состояние),
                     "GARANT_PORT": str(_свободный_порт())})
        assert r.returncode != 0, (
            "migrate при занятом замке профиля обязан отказать — перенос "
            "профиля Chromium на ходу его ломает (ограничение 2, "
            "решение 1):\n" + r.stdout + r.stderr)
        assert "владеет профилем" in r.stderr or "занят" in r.stderr.lower(), (
            "отказ обязан называть причину:\n" + r.stderr)
        # цель не создана вовсе — отказ произошёл раньше copytree.
        assert not (состояние / "chrome_profile").exists(), (
            "отказ обязан случиться ДО начала копирования")
    finally:
        держатель.close()


# ==========================================================================
#  6. migrate не затирает непустую цель
# ==========================================================================

def test_migrate_не_затирает_непустую_цель(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    старая = tmp_path / "старая_установка"
    старая.mkdir()
    _старая_установка(старая)
    состояние = tmp_path / "состояние"
    цель_профиль = состояние / "chrome_profile"
    цель_профиль.mkdir(parents=True)
    (цель_профиль / "уже_живая_сессия.dat").write_bytes(os.urandom(33))
    до_цель = _хэши(цель_профиль)
    до_источник = _хэши(старая / "chrome_profile")

    r = _garant(пакет, чужой_каталог, ["migrate", str(старая)],
                {"PYTHONPATH": str(пакет.parent),
                 "GARANT_HOME": str(состояние),
                 "GARANT_PORT": str(_свободный_порт())})

    assert r.returncode != 0, (
        "migrate обязан отказать, когда в целевом каталоге уже есть профиль:\n"
        + r.stdout + r.stderr)
    # Не любой ненулевой код: без ЭТОЙ проверки копирование всё равно
    # споткнётся позже об `os.replace` непустого каталога (та же защита
    # у ОС) — но тогда это необработанный traceback, а не понятный отказ
    # с названной причиной и советом. Проверяем, что сработала именно
    # проверка, а не случайный крах где-то ниже.
    assert "уже есть профиль" in r.stderr, (
        "отказ обязан называть причину прямым текстом («уже есть профиль»), "
        "а не быть побочным следствием краха копирования:\n" + r.stderr)
    assert "Traceback" not in r.stderr, (
        "непустая цель обязана быть отловлена ДО копирования, а не через "
        "необработанное исключение:\n" + r.stderr)
    assert _хэши(цель_профиль) == до_цель, (
        "существующий целевой профиль изменился — молчаливое затирание "
        "недопустимо, в нём мог быть живой вход")
    assert _хэши(старая / "chrome_profile") == до_источник, (
        "источник тоже не должен был измениться при отказе")


# ==========================================================================
#  7. migrate переносит отпечаток; источник без отпечатка — предупреждение
# ==========================================================================

def test_migrate_переносит_отпечаток(tmp_path, чужой_каталог):
    пакет = _песочница(tmp_path)
    старая = tmp_path / "старая_установка"
    старая.mkdir()
    _старая_установка(старая, с_отпечатком=True)
    состояние = tmp_path / "состояние"

    r = _garant(пакет, чужой_каталог, ["migrate", str(старая)],
                {"PYTHONPATH": str(пакет.parent),
                 "GARANT_HOME": str(состояние),
                 "GARANT_PORT": str(_свободный_порт())})

    assert r.returncode == 0, r.stdout + r.stderr
    целевой_отпечаток = состояние / "fingerprint.json"
    assert целевой_отпечаток.exists(), (
        "отпечаток обязан быть перенесён вместе с профилем (решение 20)")
    assert целевой_отпечаток.read_bytes() == (старая / "fingerprint.json").read_bytes(), (
        "перенесённый отпечаток обязан совпадать с отпечатком старой "
        "установки побайтово — сессия привязана именно к нему")
    assert "перенесён" in r.stdout, r.stdout


def test_migrate_без_отпечатка_у_источника_предупреждает(tmp_path, чужой_каталог):
    """У всех установок до решения 16 отпечатка нет — это не редкий, а
    ОСНОВНОЙ случай для машины владельца на момент переезда, и молчание
    здесь стоит дороже предупреждения (решение 20).
    """
    пакет = _песочница(tmp_path)
    старая = tmp_path / "старая_установка"
    старая.mkdir()
    _старая_установка(старая, с_отпечатком=False)
    состояние = tmp_path / "состояние"

    r = _garant(пакет, чужой_каталог, ["migrate", str(старая)],
                {"PYTHONPATH": str(пакет.parent),
                 "GARANT_HOME": str(состояние),
                 "GARANT_PORT": str(_свободный_порт())})

    assert r.returncode == 0, r.stdout + r.stderr
    assert not (состояние / "fingerprint.json").exists(), (
        "переносить нечего — файл не должен был появиться из ниоткуда")
    assert "отпечатка" in r.stdout and (
        "нет" in r.stdout or "нечего" in r.stdout), (
        "отсутствие отпечатка у источника обязано быть названо явным "
        "предупреждением, а не пройти молча:\n" + r.stdout)


# ==========================================================================
#  8. _чем_поставлено() на трёх раскладках
# ==========================================================================

def _уложить_код_в(корень_кода: Path) -> None:
    """Копирует файлы пакета прямо в `корень_кода` (тот и есть `paths.HOME`)."""
    корень_кода.mkdir(parents=True)
    PKG_SRC = SRC / "garant_mcp"
    for f in PKG_SRC.iterdir():
        if f.is_file() and f.suffix in (".py", ".json", ".txt"):
            shutil.copy2(f, корень_кода / f.name)


def _чем_поставлено_в(раскладка: Path, tmp_path: Path) -> tuple[str | None, str]:
    состояние = tmp_path / "состояние"
    код = (
        "import sys, json\n"
        "sys.path.insert(0, r'{site}')\n"
        "import garant_mcp.cli as cli\n"
        "чем, почему = cli._чем_поставлено()\n"
        "print(json.dumps([чем, почему], ensure_ascii=False))\n"
    ).format(site=раскладка.parent)
    r = subprocess.run(
        [sys.executable, "-c", код],
        env=_окружение({"GARANT_HOME": str(состояние)}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    чем, почему = json.loads(r.stdout.strip().splitlines()[-1])
    return чем, почему


def test_чем_поставлено_uv_tool(tmp_path):
    раскладка = (tmp_path / "домашняя" / "uv" / "tools" / "garant-mcp"
                / "lib" / "site-packages" / "garant_mcp")
    _уложить_код_в(раскладка)
    чем, почему = _чем_поставлено_в(раскладка, tmp_path)
    assert чем == "uv", (
        "код лежит в каталоге с 'uv' и 'tools' по пути — обязан быть "
        "опознан как установка uv tool, получено {!r} ({})".format(чем, почему))


def test_чем_поставлено_pipx(tmp_path):
    раскладка = (tmp_path / "домашняя" / "pipx" / "venvs" / "garant-mcp"
                / "lib" / "site-packages" / "garant_mcp")
    _уложить_код_в(раскладка)
    чем, почему = _чем_поставлено_в(раскладка, tmp_path)
    assert чем == "pipx", (
        "код лежит в каталоге pipx — обязан быть опознан как pipx, "
        "получено {!r} ({})".format(чем, почему))


def test_чем_поставлено_обычная_раскладка_честный_отказ(tmp_path):
    """Ни uv tool, ни pipx — например, `pip install` в системный/venv Python
    без их разметки каталогов. Обязан вернуться None, а не угаданная
    команда: выполнить не ту команду обновления хуже, чем не выполнить
    никакой (docstring `_чем_поставлено`).
    """
    раскладка = (tmp_path / "usr" / "local" / "lib" / "python3.12"
                / "site-packages" / "garant_mcp")
    _уложить_код_в(раскладка)
    чем, почему = _чем_поставлено_в(раскладка, tmp_path)
    assert чем is None, (
        "обычная раскладка без разметки uv tool/pipx обязана дать честный "
        "отказ (None), а не угаданную команду обновления: получено {!r} ({})"
        .format(чем, почему))
    assert "uv tool" not in почему.lower() or "не окружение" in почему, почему
