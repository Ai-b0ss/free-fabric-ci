from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA = "ff-notion-windows-release-v1"
FIXED_DT = (1980, 1, 1, 0, 0, 0)
ROOT_FILES = ("ff.cmd",)
DESKTOP_FILES = (
    "desktop/README.md",
    "desktop/bootstrap.py",
    "desktop/cleanup.py",
    "desktop/combined_acceptance.py",
    "desktop/ff_desktop.py",
    "desktop/local_http.py",
    "desktop/requirements-desktop.txt",
)
MAX_ARCHIVE_MEMBERS = 2048
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_archive_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe archive path: {name}")
    if any(":" in part for part in parts):
        raise ValueError(f"unsafe archive path: {name}")
    normalized = path.as_posix()
    if normalized != name:
        raise ValueError(f"non-canonical archive path: {name}")
    return normalized


def collect_files(root: Path) -> list[str]:
    files = list(ROOT_FILES) + list(DESKTOP_FILES)
    payload = sorted(p.relative_to(root).as_posix() for p in (root / "payload").glob("*.py") if p.is_file())
    files.extend(payload)
    missing = [name for name in files if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError("required release files missing: " + ", ".join(missing))
    result = sorted(dict.fromkeys(files))
    for name in result:
        _safe_archive_name(name)
    return result


def manifest_for(root: Path, files: list[str]) -> dict[str, object]:
    entries = []
    for name in files:
        data = (root / name).read_bytes()
        entries.append({"path": name, "size": len(data), "sha256": sha256_bytes(data)})
    return {"schema": SCHEMA, "files": entries}


def write_entry(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    _safe_archive_name(name)
    if len(data) > MAX_MEMBER_BYTES:
        raise ValueError(f"release member too large: {name}")
    info = zipfile.ZipInfo(name, FIXED_DT)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    zf.writestr(info, data)


def build(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    files = collect_files(root)
    manifest = manifest_for(root, files)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in files:
                write_entry(zf, name, (root / name).read_bytes())
            write_entry(zf, "desktop-release-manifest.json", manifest_bytes)
        os.replace(tmp, output)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return {"schema": SCHEMA, "output": str(output), "sha256": sha256_bytes(output.read_bytes()), "file_count": len(files) + 1}


def verify(archive: Path) -> dict[str, object]:
    with zipfile.ZipFile(archive, "r") as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("archive has too many members")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive paths")
        for info in infos:
            _safe_archive_name(info.filename)
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"archive member too large: {info.filename}")
        try:
            manifest_info = zf.getinfo("desktop-release-manifest.json")
        except KeyError as exc:
            raise ValueError("release manifest missing") from exc
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("release manifest too large")
        raw = zf.read(manifest_info)
        manifest = json.loads(raw.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        if manifest.get("schema") != SCHEMA:
            raise ValueError("manifest schema mismatch")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("manifest files must be a list")
        expected: dict[str, dict[str, object]] = {}
        for entry in files:
            if not isinstance(entry, dict):
                raise ValueError("manifest file entry must be an object")
            name = entry.get("path")
            if not isinstance(name, str) or not name:
                raise ValueError("manifest file path must be a non-empty string")
            _safe_archive_name(name)
            if name in expected:
                raise ValueError(f"duplicate manifest path: {name}")
            size = entry.get("size")
            digest = entry.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_MEMBER_BYTES:
                raise ValueError(f"invalid manifest size: {name}")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"invalid manifest sha256: {name}")
            expected[name] = entry
        actual_payload_names = set(names) - {"desktop-release-manifest.json"}
        if set(expected) != actual_payload_names:
            raise ValueError("archive file set differs from manifest")
        for name, entry in expected.items():
            data = zf.read(name)
            if len(data) != entry["size"] or sha256_bytes(data) != entry["sha256"]:
                raise ValueError(f"integrity mismatch: {name}")
    return {"schema": SCHEMA, "archive": str(archive), "sha256": sha256_bytes(archive.read_bytes()), "verified": True}


def _write_negative_archive(path: Path, member_name: str) -> None:
    payload = b"bad\n"
    manifest = {"schema": SCHEMA, "files": [{"path": member_name, "size": len(payload), "sha256": sha256_bytes(payload)}]}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, payload)
        zf.writestr("desktop-release-manifest.json", json.dumps(manifest).encode("utf-8"))


def selftest() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "src"
        (root / "desktop").mkdir(parents=True)
        (root / "payload").mkdir()
        for name in ROOT_FILES + DESKTOP_FILES:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(name + "\n", "utf-8")
        (root / "payload" / "server.py").write_text("app = object()\n", "utf-8")
        first = Path(td) / "a.zip"
        second = Path(td) / "b.zip"
        build(root, first)
        build(root, second)
        if first.read_bytes() != second.read_bytes():
            raise AssertionError("release build is not reproducible")
        verify(first)
        rejected = 0
        for index, unsafe in enumerate(("../escape.txt", "/absolute.txt", "C:/drive.txt", "nested\\windows.txt")):
            bad = Path(td) / f"bad-{index}.zip"
            _write_negative_archive(bad, unsafe)
            try:
                verify(bad)
            except ValueError:
                rejected += 1
            else:
                raise AssertionError(f"unsafe archive path accepted: {unsafe}")
        return {"schema": SCHEMA, "selftest": True, "sha256": sha256_bytes(first.read_bytes()), "unsafe_paths_rejected": rejected}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify the reproducible Windows desktop release archive")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    b.add_argument("--output", default="dist/free-fabric-notion-windows.zip")
    v = sub.add_parser("verify")
    v.add_argument("archive")
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "build":
        result = build(Path(args.root), Path(args.output))
    elif args.command == "verify":
        result = verify(Path(args.archive))
    else:
        result = selftest()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
