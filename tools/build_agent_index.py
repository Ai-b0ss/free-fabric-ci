#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".agent" / "local-code-index.json"

TEXT_EXTS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".jsonc",
    ".md", ".rst", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sql", ".html",
    ".css", ".scss", ".ps1", ".sh", ".bash", ".cmd", ".bat", ".cs", ".java",
    ".go", ".rs", ".rb", ".php", ".vue",
}
SYMBOL_PATTERNS = [
    ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("def", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")),
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")),
    ("arrow", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>")),
    ("shell_function", re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")),
    ("powershell_function", re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_-]*)\b", re.I)),
]

def git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""

def tracked_files() -> list[Path]:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    result = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = ROOT / item.decode("utf-8", "surrogateescape")
        if path.is_file():
            result.append(path)
    return result

def analyze(path: Path) -> dict:
    rel = path.relative_to(ROOT).as_posix()
    data = path.read_bytes()
    base = {
        "path": rel,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if b"\0" in data[:8192] or (
        path.suffix.lower() not in TEXT_EXTS
        and path.name not in {"Dockerfile", "Makefile", "Procfile"}
    ):
        return {**base, "kind": "binary_or_unindexed"}

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
        **base,
        "kind": "text",
        "lines": len(lines),
        "symbols": symbols,
    }

def main() -> None:
    records = [analyze(path) for path in tracked_files()]
    records.sort(key=lambda row: row["path"])
    payload = {
        "schema": "agent-local-code-index-v1",
        "branch": git("branch", "--show-current") or None,
        "head": git("rev-parse", "HEAD") or None,
        "tracked_files": len(records),
        "text_files": sum(row["kind"] == "text" for row in records),
        "files": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT.relative_to(ROOT)}: {payload['tracked_files']} files")

if __name__ == "__main__":
    main()