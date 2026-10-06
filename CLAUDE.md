# Claude repository bootstrap

Before substantial work, read `AGENTS.md` in the repository root. It is the canonical instruction entry point.

Then read:
- `docs/AGENT_OPERATING_SYSTEM.md` for all-work/history recovery and source-authority rules;
- `docs/AGENT_NAVIGATION.md` for this repository's architecture map.

Do not infer that work is absent from `main` alone. When the task concerns prior, abandoned or forgotten work, use `tools/agent_history.py` to search branch tips and full reachable Git history. Use `tools/build_agent_index.py --ref "<branch-or-SHA>"` for exact branch-local line/symbol navigation.

Task-specific constraints in `AGENTS.md` override generic archaeology/search guidance.
