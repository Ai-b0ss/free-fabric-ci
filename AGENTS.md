<!-- PUBLIC_ACCOUNT_CONTEXT_V1 -->
## Public repository account context — mandatory

Before substantial work, read:

1. `docs/ACCOUNT_CONTEXT.md` — privacy-safe account-level boundary.
2. `.agent/github-objects-index.json` — this public repository's PR/issue/workflow/tag archaeology.

If authenticated owner-authorized tooling exposes related private repositories and the task requires account-wide archaeology, search them too — but never copy private names, metadata, code or evidence into this public repository unless explicitly authorized for publication.

---

<!-- AGENT_BOOTSTRAP_V2 -->
# READ THIS FIRST — mandatory repository bootstrap

For every substantial task in this repository, **before designing or editing**:

1. Read [docs/AGENT_OPERATING_SYSTEM.md](docs/AGENT_OPERATING_SYSTEM.md) — this is the all-work recovery and source-authority contract.
2. Read [docs/AGENT_NAVIGATION.md](docs/AGENT_NAVIGATION.md) — repository-specific architecture map.
3. Pin the exact branch and commit; do not reason from a branch name alone.
4. If prior/abandoned/forgotten work may exist, search branch tips **and full reachable Git history** with `tools/agent_history.py` before implementing a replacement.
5. For exact code locations on any ref, run `python tools/build_agent_index.py --ref "<branch-or-SHA>"`.
6. Never conclude "this was never implemented" from `main` alone.

The navigation files in `main` are deliberately external to old evidence branches so those historical heads remain immutable.

---

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