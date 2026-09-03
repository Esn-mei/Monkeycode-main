from __future__ import annotations

import functools
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from monkeycode.skills.catalog import Catalog
from monkeycode.skills.install import install_from_source, install_from_url


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args) -> None:
        return None


def serve(directory):
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def write_zip(path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def test_install_from_url_happy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    zip_path = tmp_path / "skill.zip"
    write_zip(
        zip_path,
        {
            "myskill/SKILL.md": "---\nname: myskill\ndescription: My skill\n---\nBody\n",
        },
    )
    server = serve(tmp_path)
    try:
        catalog = Catalog()
        name = install_from_url(
            f"http://127.0.0.1:{server.server_port}/skill.zip",
            catalog,
            tmp_path / "work",
        )
    finally:
        server.shutdown()

    assert name == "myskill"
    assert (
        tmp_path / "home" / ".monkeycode" / "skills" / "myskill" / "SKILL.md"
    ).exists()
    assert catalog.get("myskill") is not None


def test_install_from_url_rejects_zip_slip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    zip_path = tmp_path / "bad.zip"
    write_zip(
        zip_path,
        {
            "bad/SKILL.md": "---\nname: bad\ndescription: Bad\n---\nBody\n",
            "bad/../../escape.txt": "nope",
        },
    )
    server = serve(tmp_path)
    try:
        with pytest.raises(ValueError, match="unsafe path in zip"):
            install_from_url(
                f"http://127.0.0.1:{server.server_port}/bad.zip",
                Catalog(),
                tmp_path / "work",
            )
    finally:
        server.shutdown()

    assert not (tmp_path / "home" / ".monkeycode" / "skills" / "bad").exists()


def test_install_from_local_zip_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    zip_path = tmp_path / "local.zip"
    write_zip(
        zip_path,
        {
            "localskill/SKILL.md": "---\nname: localskill\ndescription: Local skill\n---\nBody\n",
        },
    )
    catalog = Catalog()

    name = install_from_source(str(zip_path), catalog, tmp_path / "work")

    assert name == "localskill"
    assert (
        tmp_path / "home" / ".monkeycode" / "skills" / "localskill" / "SKILL.md"
    ).exists()
    assert catalog.get("localskill") is not None


def test_install_from_local_directory_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source_dir = tmp_path / "dirskill"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(
        "---\nname: dirskill\ndescription: Directory skill\n---\nBody\n",
        encoding="utf-8",
    )
    (source_dir / "notes.txt").write_text("extra", encoding="utf-8")
    catalog = Catalog()

    name = install_from_source(str(source_dir), catalog, tmp_path / "work")

    assert name == "dirskill"
    target = tmp_path / "home" / ".monkeycode" / "skills" / "dirskill"
    assert (target / "SKILL.md").exists()
    assert (target / "notes.txt").read_text(encoding="utf-8") == "extra"
    assert catalog.get("dirskill") is not None


def test_install_from_env_var_local_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    source_dir = tmp_path / "envskill"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(
        "---\nname: envskill\ndescription: Env skill\n---\nBody\n",
        encoding="utf-8",
    )

    name = install_from_source("%USERPROFILE%\\envskill", Catalog(), tmp_path / "work")

    assert name == "envskill"
    assert (
        tmp_path / "home" / ".monkeycode" / "skills" / "envskill" / "SKILL.md"
    ).exists()
