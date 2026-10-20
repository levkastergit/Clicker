"""
Обновление программы из GitHub-репозитория.

Поддерживаются три режима:
1. Скрипт в git-клоне (есть папка .git и установлен git) — выполняется `git pull`.
2. Скрипт без git — скачивается zip-архив ветки с GitHub и файлы заменяются.
3. Собранный exe (PyInstaller) — скачивается exe из последнего релиза GitHub
   и подменяется через вспомогательный .bat после выхода из программы.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_OWNER = "levkastergit"
REPO_NAME = "Clicker"
BRANCH = "main"

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{BRANCH}.zip"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"

# Файл, в котором хранится SHA коммита, из которого скачана текущая версия
# (используется только в режиме без git).
SHA_FILE = ".update_sha"

# Файлы и папки, которые никогда не затираются при обновлении из zip.
PROTECTED = {".git", "build", "dist", "__pycache__", SHA_FILE}

TIMEOUT = 20  # секунд на сетевые запросы


class UpdateError(Exception):
    """Ошибка обновления с текстом для пользователя."""


@dataclass
class UpdateResult:
    updated: bool
    message: str
    restart_cmd: list[str] | None = None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Папка, где лежит программа (скрипт или exe)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"{REPO_NAME}-updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Сервер GitHub ответил ошибкой {exc.code} для {url}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Нет связи с GitHub: {exc.reason}") from exc


def _git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(args: list[str], cwd: Path) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        timeout=120,
    )
    if proc.returncode != 0:
        details = proc.stderr.strip() or proc.stdout.strip()
        raise UpdateError(f"git {' '.join(args)} завершился с ошибкой:\n{details}")
    return proc.stdout.strip()


def _restart_cmd_script() -> list[str]:
    """Команда перезапуска текущего скрипта тем же интерпретатором."""
    return [sys.executable, *sys.argv]


# --------------------------------------------------------------------------
# Режим 1: git pull
# --------------------------------------------------------------------------
def _update_via_git(root: Path) -> UpdateResult:
    before = _run_git(["rev-parse", "HEAD"], root)
    _run_git(["fetch", "--quiet", "origin", BRANCH], root)
    remote = _run_git(["rev-parse", f"origin/{BRANCH}"], root)
    if before == remote:
        return UpdateResult(False, f"Уже установлена последняя версия ({before[:7]}).")

    dirty = _run_git(["status", "--porcelain", "--untracked-files=no"], root)
    if dirty:
        raise UpdateError(
            "В папке программы есть локальные изменения, git pull не выполнен:\n"
            f"{dirty}\n\nСохраните или отмените их и повторите."
        )

    _run_git(["pull", "--ff-only", "origin", BRANCH], root)
    after = _run_git(["rev-parse", "HEAD"], root)
    return UpdateResult(
        True,
        f"Обновлено: {before[:7]} -> {after[:7]}.",
        restart_cmd=_restart_cmd_script(),
    )


# --------------------------------------------------------------------------
# Режим 2: zip-архив ветки
# --------------------------------------------------------------------------
def _remote_head_sha() -> str:
    data = json.loads(_http_get(f"{API_BASE}/commits/{BRANCH}"))
    sha = data.get("sha")
    if not sha:
        raise UpdateError("GitHub не вернул SHA последнего коммита.")
    return sha


def _local_sha(root: Path) -> str | None:
    try:
        return (root / SHA_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _update_via_zip(root: Path) -> UpdateResult:
    remote_sha = _remote_head_sha()
    local_sha = _local_sha(root)
    if local_sha == remote_sha:
        return UpdateResult(False, f"Уже установлена последняя версия ({remote_sha[:7]}).")

    archive = _http_get(ZIP_URL)
    replaced: list[str] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = zf.namelist()
        if not names:
            raise UpdateError("Скачанный архив пуст.")
        # Первый компонент пути — папка вида "Clicker-main/"
        prefix = names[0].split("/", 1)[0] + "/"
        for member in zf.infolist():
            if member.is_dir() or not member.filename.startswith(prefix):
                continue
            rel = Path(member.filename[len(prefix):])
            if not rel.parts or rel.parts[0] in PROTECTED:
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            replaced.append(str(rel))

    (root / SHA_FILE).write_text(remote_sha, encoding="utf-8")
    old = local_sha[:7] if local_sha else "неизвестно"
    return UpdateResult(
        True,
        f"Обновлено: {old} -> {remote_sha[:7]}. Заменено файлов: {len(replaced)}.",
        restart_cmd=_restart_cmd_script(),
    )


# --------------------------------------------------------------------------
# Режим 3: exe из GitHub Releases
# --------------------------------------------------------------------------
def _update_frozen(current_version: str) -> UpdateResult:
    try:
        data = json.loads(_http_get(f"{API_BASE}/releases/latest"))
    except UpdateError as exc:
        if "404" in str(exc):
            raise UpdateError(
                "В репозитории пока нет релизов с готовым exe.\n"
                f"Скачайте новую версию вручную: {REPO_URL}"
            ) from exc
        raise

    tag = str(data.get("tag_name", "")).lstrip("vV")
    if tag and tag == current_version.lstrip("vV"):
        return UpdateResult(False, f"Уже установлена последняя версия ({tag}).")

    asset_url = None
    asset_name = None
    for asset in data.get("assets", []):
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe"):
            asset_url = asset.get("browser_download_url")
            asset_name = name
            break
    if not asset_url:
        raise UpdateError(
            f"В релизе {data.get('tag_name')} нет exe-файла.\n"
            f"Скачайте вручную: {REPO_URL}/releases"
        )

    exe_path = Path(sys.executable).resolve()
    tmp_dir = Path(tempfile.mkdtemp(prefix="clicker_update_"))
    new_exe = tmp_dir / (asset_name or exe_path.name)
    new_exe.write_bytes(_http_get(asset_url))

    pid = os.getpid()
    # .bat ждёт завершения текущего процесса, подменяет exe и запускает его.
    bat = tmp_dir / "apply_update.bat"
    bat_lines = [
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
        "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)",
        f'copy /Y "{new_exe}" "{exe_path}" >nul',
        f'start "" "{exe_path}"',
        f'del "{new_exe}"',
        '(goto) 2>nul & del "%~f0"',
    ]
    bat.write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")
    return UpdateResult(
        True,
        f"Скачана версия {data.get('tag_name')}. Программа перезапустится для установки.",
        restart_cmd=["cmd", "/c", str(bat)],
    )


# --------------------------------------------------------------------------
def check_and_update(current_version: str) -> UpdateResult:
    """Проверяет обновления и устанавливает их. Бросает UpdateError при сбое."""
    if is_frozen():
        return _update_frozen(current_version)
    root = app_dir()
    if (root / ".git").exists() and _git_available():
        return _update_via_git(root)
    return _update_via_zip(root)
