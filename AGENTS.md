# AGENTS.md — Free Fabric CI

Public reusable CI, acceptance, packaging and release-verification tooling for the Free Fabric project family. See `README.md` for the overview.

`main` is the public source of truth. Other branches (worker, audit, probe, temp) are development or evidence lines; do not infer promotion from a branch name.

## Running tests
Python 3.13, standard library only. Same command as CI (`.github/workflows/ci.yml`):

    python -m unittest discover -s tests -p 'test_*.py' -v

`.github/workflows/live-fabric-proof.yml` needs live network and secrets; do not run it locally.

## Code map
| Area | Start here | Then look at |
|---|---|---|
| Protocol/migration harness | `harness/common.mjs` | forged/migration worker and verifier fixtures |
| Desktop release build | `desktop/build_release.py` | combined acceptance, local HTTP helper |
| Acceptance orchestration | `desktop/combined_acceptance.py` | endpoint, transport and release tests |
| URL/loopback guard | `desktop/local_http.py` | `tests/test_local_http_url_guard.py` |
| Adversarial contracts | `tests/test_f52_adversarial.py` | harness fixtures it exercises |
| Release verification | `tests/test_release_verifier.py` | release builder and checks |

Harness files are executable protocol fixtures. When changing one side of a contract, find the matching worker, verifier and tests first. Do not simplify adversarial fixtures just to make tests pass.

## Repository boundary
This repository must stay safe as public software. Do not add private product code, sessions, runtime state, credentials or private end-to-end evidence. Public harness behavior must be reproducible from the code stored here.

## Change discipline
- Protocol or harness changes need tests for both accept and reject paths.
- Release verification changes need deterministic artifact checks.
- Network helpers must keep the loopback/URL guard assumptions that the tests verify.
- Before changing a contract, read both its implementation and its adversarial, transport and release tests.
