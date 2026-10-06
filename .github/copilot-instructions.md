# Repository agent instructions

Always start from the root `AGENTS.md`.

Also read:
- `docs/AGENT_OPERATING_SYSTEM.md` — full-history recovery, authority and branch rules.
- `docs/AGENT_NAVIGATION.md` — repository-specific architecture.

When a change may duplicate forgotten work, search branch tips and full Git history with `tools/agent_history.py` before implementing it. Use `tools/build_agent_index.py --ref "<branch-or-SHA>"` for exact symbol/line navigation.

Do not treat branch names, stale docs or `main` alone as proof that an implementation never existed.
