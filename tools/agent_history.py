#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


def git(root: Path, *args: str, text: bool = True, check: bool = True):
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc


def repo_root() -> Path:
    p = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError("Not inside a Git repository")
    return Path(p.stdout.strip()).resolve()


def canonical_ref_name(name: str) -> str:
    if name.startswith("refs/heads/"):
        return name[len("refs/heads/"):]
    if name.startswith("refs/remotes/origin/"):
        return name[len("refs/remotes/origin/"):]
    return name


def list_refs(root: Path) -> list[dict]:
    fmt = "%(refname)%00%(objectname)%00%(committerdate:iso-strict)%00%(subject)%00"
    raw = git(
        root,
        "for-each-ref",
        f"--format={fmt}",
        "refs/heads",
        "refs/remotes/origin",
    ).stdout
    parts = raw.split("\x00")
    rows = []
    for i in range(0, len(parts) - 3, 4):
        refname, sha, date, subject = parts[i:i + 4]
        refname = refname.strip()
        if not refname or refname.endswith("/HEAD"):
            continue
        rows.append({
            "ref": refname,
            "name": canonical_ref_name(refname),
            "head": sha.strip(),
            "date": date.strip(),
            "subject": subject.strip(),
        })

    by_name: dict[str, dict] = {}
    for row in rows:
        current = by_name.get(row["name"])
        if current is None or row["ref"].startswith("refs/heads/"):
            by_name[row["name"]] = row
    return sorted(by_name.values(), key=lambda row: row["name"])


def resolve(root: Path, ref: str) -> str:
    return git(root, "rev-parse", f"{ref}^{{commit}}").stdout.strip()


def main_ref(root: Path, preferred: str) -> str:
    for candidate in (preferred, f"origin/{preferred}"):
        p = git(
            root,
            "rev-parse",
            "--verify",
            f"{candidate}^{{commit}}",
            check=False,
        )
        if p.returncode == 0:
            return candidate
    return preferred


def branch_info(root: Path, ref: str, baseline: str) -> dict:
    resolved = resolve(root, ref)
    base_ref = main_ref(root, baseline)
    merge_base = git(root, "merge-base", base_ref, ref).stdout.strip()
    counts = git(
        root,
        "rev-list",
        "--left-right",
        "--count",
        f"{base_ref}...{ref}",
    ).stdout.strip().split()
    behind, ahead = (
        (int(counts[0]), int(counts[1])) if len(counts) == 2 else (None, None)
    )
    stat = git(
        root,
        "diff",
        "--name-status",
        f"{base_ref}...{ref}",
    ).stdout.splitlines()

    changed = []
    top = Counter()
    for line in stat:
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0]
        path = fields[-1]
        changed.append({"status": status, "path": path})
        top[path.split("/", 1)[0]] += 1

    meta = git(
        root,
        "show",
        "-s",
        "--format=%H%n%cI%n%s",
        ref,
    ).stdout.splitlines()

    return {
        "ref": ref,
        "head": resolved,
        "date": meta[1] if len(meta) > 1 else None,
        "subject": meta[2] if len(meta) > 2 else None,
        "baseline": base_ref,
        "merge_base": merge_base,
        "ahead": ahead,
        "behind": behind,
        "changed_count": len(changed),
        "top_level_changed": top.most_common(),
        "changed_files": changed,
    }


def filter_refs(refs: list[dict], glob_pattern: str | None) -> list[dict]:
    if not glob_pattern:
        return refs
    return [
        row for row in refs
        if fnmatch.fnmatch(row["name"], glob_pattern)
    ]


def search_text(
    root: Path,
    pattern: str,
    refs: list[dict],
    regex: bool,
    ignore_case: bool,
    max_matches: int,
) -> list[dict]:
    results = []
    base_args = ["grep", "-n", "-I"]
    if ignore_case:
        base_args.append("-i")
    if not regex:
        base_args.append("-F")

    for row in refs:
        proc = git(
            root,
            *base_args,
            pattern,
            row["ref"],
            check=False,
        )
        if proc.returncode not in (0, 1):
            continue
        for line in proc.stdout.splitlines():
            match = re.match(r"^[^:]+:(.*?):(\d+):(.*)$", line)
            if not match:
                continue
            results.append({
                "branch": row["name"],
                "head": row["head"],
                "path": match.group(1),
                "line": int(match.group(2)),
                "text": match.group(3),
            })
            if len(results) >= max_matches:
                return results
    return results


def search_path(
    root: Path,
    pattern: str,
    refs: list[dict],
    max_matches: int,
) -> list[dict]:
    results = []
    for row in refs:
        proc = git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            row["ref"],
            check=False,
        )
        if proc.returncode != 0:
            continue
        for path in proc.stdout.splitlines():
            if fnmatch.fnmatch(path, pattern):
                results.append({
                    "branch": row["name"],
                    "head": row["head"],
                    "path": path,
                })
                if len(results) >= max_matches:
                    return results
    return results


def dump(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_index(
    root: Path,
    refs: list[dict],
    baseline: str,
    output: str | None,
) -> None:
    base = main_ref(root, baseline)
    rows = []
    for row in refs:
        if row["name"] == baseline or row["ref"] == base:
            info = {
                **row,
                "ahead": 0,
                "behind": 0,
                "changed_count": 0,
                "top_level_changed": [],
            }
        else:
            try:
                diff = branch_info(root, row["ref"], baseline)
                info = {
                    **row,
                    "ahead": diff["ahead"],
                    "behind": diff["behind"],
                    "changed_count": diff["changed_count"],
                    "top_level_changed": diff["top_level_changed"],
                }
            except Exception as exc:
                info = {**row, "error": str(exc)}
        rows.append(info)

    payload = {
        "schema": "agent-history-index-v1",
        "baseline": base,
        "branches": rows,
    }
    if output:
        path = root / output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(root)}: {len(rows)} branches")
    else:
        dump(payload)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search and inspect all Git branches without checking them out."
    )
    parser.add_argument(
        "--baseline",
        default="main",
        help="Canonical integration branch (default: main)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "branches",
        help="List every local/origin branch with head/date/subject",
    )
    p.add_argument("--glob", dest="glob_pattern")

    p = sub.add_parser(
        "branch-info",
        help="Diff one branch against the canonical baseline",
    )
    p.add_argument("ref")

    p = sub.add_parser(
        "find-text",
        help="Search text across every branch without checkout",
    )
    p.add_argument("pattern")
    p.add_argument("--regex", action="store_true")
    p.add_argument("-i", "--ignore-case", action="store_true")
    p.add_argument("--glob", dest="glob_pattern")
    p.add_argument("--max", type=int, default=500)

    p = sub.add_parser(
        "find-path",
        help="Find file paths across every branch",
    )
    p.add_argument("pattern", help="Shell-style glob, e.g. '**/ROADMAP*.md'")
    p.add_argument("--glob", dest="glob_pattern")
    p.add_argument("--max", type=int, default=500)

    p = sub.add_parser(
        "index",
        help="Build branch metadata/diff summary index",
    )
    p.add_argument("--glob", dest="glob_pattern")
    p.add_argument("--out", default=".agent/history-index.json")

    args = parser.parse_args()
    root = repo_root()
    refs = filter_refs(
        list_refs(root),
        getattr(args, "glob_pattern", None),
    )

    if args.cmd == "branches":
        dump({"branches": refs})
    elif args.cmd == "branch-info":
        dump(branch_info(root, args.ref, args.baseline))
    elif args.cmd == "find-text":
        dump({
            "pattern": args.pattern,
            "matches": search_text(
                root,
                args.pattern,
                refs,
                args.regex,
                args.ignore_case,
                args.max,
            ),
        })
    elif args.cmd == "find-path":
        dump({
            "pattern": args.pattern,
            "matches": search_path(
                root,
                args.pattern,
                refs,
                args.max,
            ),
        })
    elif args.cmd == "index":
        build_index(root, refs, args.baseline, args.out)


if __name__ == "__main__":
    main()
