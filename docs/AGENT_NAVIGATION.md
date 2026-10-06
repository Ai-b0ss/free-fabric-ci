# Free Fabric CI — agent navigation map

## Code map
| Area | Start here | Follow next |
|---|---|---|
| Reusable CI | `.github/workflows/ci.yml` | tests invoked by workflow |
| Live fabric proof workflow | `.github/workflows/live-fabric-proof.yml` | harness scripts and transport tests |
| Protocol/migration harness | `harness/common.mjs` | forged/migration worker + verifier fixtures |
| Desktop release | `desktop/build_release.py` | combined acceptance and local HTTP helper |
| Acceptance orchestration | `desktop/combined_acceptance.py` | endpoint/transport/release tests |
| URL/loopback guard | `desktop/local_http.py` | `tests/test_local_http_url_guard.py` |
| Adversarial contracts | `tests/test_f52_adversarial.py` | harness fixtures it exercises |
| Release verification | `tests/test_release_verifier.py` | release builder/checks |

## Reading protocol
Treat harness files as executable protocol fixtures. When editing one side of a contract, locate the corresponding forged/migration worker/verifier and tests before changing semantics. Do not simplify adversarial fixtures merely to make tests green.

## Branches
All branch heads are in `.agent/branch-index.json`. Temporary branches are historical unless explicitly selected by a task. `main` remains the public integration baseline.

## Line-level navigation
Run `python tools/build_agent_index.py --ref "<branch-or-SHA>"`; use generated symbol lines as exact jump points for the checked-out branch. Regenerate after every branch switch.