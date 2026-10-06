#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

TEXT_EXTS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".jsonc",
    ".md", ".rst", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sql", ".html",
    ".css", ".scss", ".ps1", ".sh", ".bash", ".cmd", ".bat", ".cs", ".java",
    ".go", ".rs", ".rb", ".php", ".vue",
}
SPECIAL_TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile"}
SYMBOL_PATTERNS = [
    ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("def", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")),
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")),
    ("arrow", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>")),
    ("shell_function", re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")),
    ("powershell_function", re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_-]*)\b", re.I)),
]

def run_git(root: Path, *args: str, binary: bool = False):
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        stderr=subprocess.PIPE,
        text=not binary,
    )

def repo_root() -> Path:
    raw = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True)
    return Path(raw.strip()).resolve()

def list_blobs(root: Path, ref: str) -> list[dict]:
    raw = run_git(root, "ls-tree", "-r", "-z", "--long", ref, binary=True)
    rows = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path_raw = record.split(b"\t", 1)
        parts = meta.decode("ascii").split()
        if len(parts) < 4 or parts[1] != "blob":
            continue
        mode, _, blob, size_raw = parts[:4]
        path = path_raw.decode("utf-8", "surrogateescape")
        rows.append({
            "path": path,
            "blob": blob,
            "mode": mode,
            "bytes": None if size_raw == "-" else int(size_raw),
        })
    return rows

def analyze(root: Path, row: dict) -> dict:
    path = row["path"]
    data = run_git(root, "cat-file", "-p", row["blob"], binary=True)
    result = {
        "path": path,
        "blob": row["blob"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    suffix = Path(path).suffix.lower()
    if b"\0" in data[:8192] or (
        suffix not in TEXT_EXTS
        and Path(path).name not in SPECIAL_TEXT_NAMES
    ):
        return {**result, "kind": "binary_or_unindexed"}

    lines = data.decode("utf-8", "replace").splitlines()
    symbols = []
    for line_no, line in enumerate(lines, 1):
        for kind, pattern in SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append(
                    {"name": match.group(1), "kind": kind, "line": line_no}
                )
                break
    return {
        **result,
        "kind": "text",
        "lines": len(lines),
        "symbols": symbols,
    }

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an exact branch/ref code index without checking it out."
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="Git branch, tag or commit to index (default: HEAD)",
    )
    parser.add_argument(
        "--out",
        default=".agent/local-code-index.json",
        help="Output path relative to repo root",
    )
    args = parser.parse_args()

    root = repo_root()
    commit = run_git(root, "rev-parse", f"{args.ref}^{{commit}}").strip()
    rows = list_blobs(root, args.ref)
    files = [analyze(root, row) for row in rows]
    files.sort(key=lambda item: item["path"])

    payload = {
        "schema": "agent-ref-code-index-v2",
        "requested_ref": args.ref,
        "resolved_commit": commit,
        "tracked_files": len(files),
        "text_files": sum(item["kind"] == "text" for item in files),
        "files": files,
    }
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {out.relative_to(root)} for {args.ref} @ {commit}: {len(files)} files"
    )

if __name__ == "__main__":
    main()