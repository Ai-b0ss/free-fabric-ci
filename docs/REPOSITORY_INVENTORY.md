# Repository inventory — free-fabric-ci

Public reusable Free Fabric CI, acceptance, release and adversarial harness repository.

This is a structural snapshot for orientation, not a substitute for exact-ref inspection. For current truth resolve `main` at read time; for another ref use the tools below.

## Canonical navigation

1. `AGENTS.md`
2. `docs/AGENT_OPERATING_SYSTEM.md`
3. `docs/AGENT_NAVIGATION.md`
4. `.agent/branch-index.json`
5. `.agent/file-index.json`

## Current main snapshot

Tracked blob files currently visible on `main`: **36**.
Known named branches in the committed branch catalog: **11**.

### Top-level areas by stored bytes

| Area | Files | Bytes |
|---|---:|---:|
| `harness` | 12 | 43,105 |
| `desktop` | 3 | 26,168 |
| `tests` | 6 | 21,974 |
| `tools` | 2 | 18,832 |
| `docs` | 2 | 11,943 |
| `.agent` | 3 | 9,770 |
| `.github` | 3 | 8,864 |
| `AGENTS.md` | 1 | 2,086 |
| `README.md` | 1 | 1,573 |
| `CLAUDE.md` | 1 | 703 |
| `.cursor` | 1 | 658 |
| `GEMINI.md` | 1 | 588 |

### File roles

| Role | Files |
|---|---:|
| `agent_tooling` | 12 |
| `acceptance_harness` | 12 |
| `test` | 6 |
| `desktop_release_runtime` | 3 |
| `ci` | 2 |
| `documentation` | 1 |

### Largest tracked files

Large files are not automatically bad; this table tells an agent where blind full-file reading is likely wasteful.

| File | Bytes | Role |
|---|---:|---|
| `desktop/combined_acceptance.py` | 15,096 | `desktop_release_runtime` |
| `tools/agent_history.py` | 14,293 | `agent_tooling` |
| `docs/AGENT_OPERATING_SYSTEM.md` | 10,435 | `agent_tooling` |
| `tests/test_public_harness.py` | 9,323 | `test` |
| `desktop/build_release.py` | 8,764 | `desktop_release_runtime` |
| `harness/migration-verifier.mjs` | 7,788 | `acceptance_harness` |
| `.github/workflows/live-fabric-proof.yml` | 7,751 | `ci` |
| `.agent/file-index.json` | 5,908 | `agent_tooling` |
| `harness/migration-worker-b.mjs` | 5,533 | `acceptance_harness` |
| `harness/common.mjs` | 5,316 | `acceptance_harness` |
| `tests/test_f52_adversarial.py` | 4,572 | `test` |
| `tools/build_agent_index.py` | 4,539 | `agent_tooling` |
| `harness/forged-verifier-accept.mjs` | 4,035 | `acceptance_harness` |
| `harness/migration-worker-a.mjs` | 3,880 | `acceptance_harness` |
| `harness/forged-malicious-worker.mjs` | 3,195 | `acceptance_harness` |

## Branch families

Branch prefixes are navigational hints, not authority. Exact names and non-main head SHAs are in `.agent/branch-index.json`.

| Family | Branches |
|---|---:|
| `audit` | 2 |
| `worker-a` | 2 |
| `autopilot-control-plane` | 1 |
| `main` | 1 |
| `probe` | 1 |
| `temp-misis-run-20260906` | 1 |
| `tmp-misis-schedule-20260913` | 1 |
| `worker-b` | 1 |
| `worker-d` | 1 |

## How to reach all work

Current branch tips:
```bash
python tools/agent_history.py branches
python tools/agent_history.py find-text "term"
python tools/agent_history.py find-path "**/name*"
```

Deleted/older committed work:
```bash
python tools/agent_history.py find-history-text "term"
python tools/agent_history.py find-history-path "**/name*"
```

Candidate branch context:
```bash
python tools/agent_history.py branch-info "<branch>"
```

Exact file/symbol/line index for any ref:
```bash
python tools/build_agent_index.py --ref "<branch-or-SHA>"
```

## Interpretation rule

Do not treat this inventory, a branch name, or a README statement as proof of runtime behavior. Trace the exact ref through code, tests, state/configuration and relevant evidence. When old work is found, determine why it diverged or disappeared before reusing it.
