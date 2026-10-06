# AGENTS.md — Free Fabric CI

## Mandatory orientation
1. Pin branch/head in `.agent/branch-index.json`.
2. Read `README.md` and `docs/AGENT_NAVIGATION.md`.
3. Use `.agent/file-index.json` to choose harness, desktop/release, tests or workflow code.
4. Run `python tools/build_agent_index.py --ref "<branch-or-SHA>"` on a checkout for exact line counts and symbol start lines.
5. Before changing a contract, read both the implementation and its adversarial/transport/release tests.

`main` is the reusable public source of truth. Worker/audit/probe/temp branches are development or evidence lines; do not infer promotion from branch names.

## Repository boundary
This repository must remain safe as public software. Do not copy private product code, sessions, runtime state, credentials or private end-to-end evidence into it. Keep public harness behavior reproducible from the code stored here.

## Change discipline
Protocol/harness changes require tests for accept and reject paths. Release verification changes require deterministic artifact checks. Network helpers must preserve loopback/URL guard assumptions verified by tests.