# .agent — machine-oriented repository navigation

This directory is intentionally small and deterministic.

## Files

- `branch-index.json` — known branch names and exact non-main head SHAs. `main` is resolved dynamically.
- `file-index.json` — exact tracked-file inventory for current `main`, excluding itself to avoid recursive staleness.
- `local-code-index.json` — generated on demand; not a canonical committed source. Build it with `tools/build_agent_index.py`.
- `history-index.json` — optional generated branch metadata/diff summary from `tools/agent_history.py index`.

## Mandatory documents

- `../AGENTS.md` — canonical agent entry point.
- `../docs/AGENT_OPERATING_SYSTEM.md` — how to recover all reachable work and resolve source authority.
- `../docs/AGENT_NAVIGATION.md` — repository-specific architecture.
- `../docs/REPOSITORY_INVENTORY.md` — structural snapshot and branch/file-family summary.

## Exact-ref code navigation

```bash
python tools/build_agent_index.py --ref "<branch-or-SHA>"
```

## Search current branch tips

```bash
python tools/agent_history.py find-text "term"
python tools/agent_history.py find-path "**/name*.md"
```

## Search deleted/older committed work

```bash
python tools/agent_history.py find-history-text "term"
python tools/agent_history.py find-history-path "**/name*.md"
```

## Understand a candidate branch

```bash
python tools/agent_history.py branch-info "<branch>"
```

Never infer absence from `main` alone. Never mutate old evidence branches merely to add navigation.
