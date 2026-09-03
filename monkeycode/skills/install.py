from __future__ import annotations

import shutil
import stat
import tempfile
import zipfile
import os
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx

from monkeycode.skills.catalog import Catalog, _user_skills_dir
from monkeycode.skills.parser import parse_skill_dir, validate_skill_name
from monkeycode.skills.types import SkillSource

MAX_ZIP_BYTES = 50 * 1024 * 1024


def install_from_url(source: str, catalog: Catalog, work_dir: Path) -> str:
    return install_from_source(source, catalog, work_dir)


def install_from_source(source: str, catalog: Catalog, work_dir: Path) -> str:
    source_path, cleanup = _resolve_source(source)
    try:
        if source_path.is_dir():
            name = _install_from_directory(source_path)
        else:
            name = _install_from_zip(source_path)
        catalog.reload(work_dir)
        return name
    finally:
        if cleanup:
            try:
                source_path.unlink()
            except OSError:
                pass


def _install_from_zip(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as archive:
        top_dir = _validate_zip(archive)
        target = _user_skills_dir() / top_dir
        temp_target = target.with_name(f".{top_dir}.installing")
        if temp_target.exists():
            shutil.rmtree(temp_target)
        temp_target.mkdir(parents=True, exist_ok=True)
        _extract_checked(archive, temp_target)
        _replace_target(temp_target, target)
    return top_dir


def _install_from_directory(source_dir: Path) -> str:
    skill = parse_skill_dir(source_dir, SkillSource.USER)
    target = _user_skills_dir() / skill.meta.name
    temp_target = target.with_name(f".{skill.meta.name}.installing")
    if temp_target.exists():
        shutil.rmtree(temp_target)
    shutil.copytree(source_dir, temp_target, symlinks=False)
    _replace_target(temp_target, target)
    return skill.meta.name


def _replace_target(temp_target: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    temp_target.replace(target)


def _resolve_source(source: str) -> tuple[Path, bool]:
    normalized = source.strip().strip('"').strip("'")
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"}:
        return _download_zip(normalized), True
    if parsed.scheme == "file":
        return Path(url2pathname(unquote(parsed.path))).resolve(), False
    if parsed.scheme and len(parsed.scheme) != 1:
        raise ValueError(
            "source must be an HTTP(S) URL, file URL, local zip, or local skill directory"
        )

    expanded = os.path.expandvars(normalized)
    return Path(expanded).expanduser().resolve(), False


def _download_zip(source: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        prefix="monkeycode-skill-", suffix=".zip", delete=False
    )
    tmp_path = Path(tmp.name)
    total = 0
    try:
        with tmp:
            with httpx.stream(
                "GET", source, timeout=60.0, follow_redirects=True
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_ZIP_BYTES:
                        raise ValueError("zip too large")
                    tmp.write(chunk)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return tmp_path


def _validate_zip(archive: zipfile.ZipFile) -> str:
    infos = [info for info in archive.infolist() if info.filename]
    if not infos:
        raise ValueError("zip is empty")

    top_names: set[str] = set()
    has_skill = False
    for info in infos:
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("unsafe path in zip")
        if _is_symlink(info):
            raise ValueError("unsafe path in zip")
        parts = [part for part in pure.parts if part not in {"", "."}]
        if not parts:
            continue
        top_names.add(parts[0])
        if len(parts) == 2 and parts[1] == "SKILL.md":
            has_skill = True

    if len(top_names) != 1:
        raise ValueError("zip must contain exactly one top-level skill directory")
    top_dir = next(iter(top_names))
    validate_skill_name(top_dir)
    if not has_skill:
        raise ValueError("zip must contain SKILL.md")
    return top_dir


def _extract_checked(archive: zipfile.ZipFile, temp_target: Path) -> None:
    root = temp_target.resolve()
    for info in archive.infolist():
        if not info.filename or info.is_dir():
            continue
        relative = PurePosixPath(info.filename.replace("\\", "/"))
        parts = [part for part in relative.parts if part not in {"", "."}]
        destination = root.joinpath(*parts[1:]).resolve()
        if destination != root and root not in destination.parents:
            raise ValueError("unsafe path in zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_IFMT(mode) == stat.S_IFLNK
