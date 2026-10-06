# Free Fabric CI

> **Agent navigation:** start with [`AGENTS.md`](AGENTS.md), then [`docs/AGENT_NAVIGATION.md`](docs/AGENT_NAVIGATION.md). Exact branch heads are indexed in [`.agent/branch-index.json`](.agent/branch-index.json); main-branch files/blobs are in [`.agent/file-index.json`](.agent/file-index.json). For any branch/SHA, run `python tools/build_agent_index.py --ref "<branch-or-SHA>"` to generate exact line counts and symbol locations.

Public reusable CI, acceptance, packaging and deployment tooling for the Free Fabric project family.

This repository intentionally contains only components that are safe and useful as public software: protocol-contract acceptance harnesses, loopback-only transport guards, release/archive verification, cross-platform regressions, and deployment/packaging tooling.

## CI model

- Public reusable components are tested here on GitHub-hosted `ubuntu-latest` and `windows-latest`.
- Private product implementations, sessions, runtime state and secrets are **not** copied into this repository.
- Product-native private end-to-end evidence remains on private execution surfaces such as CircleCI.
- This repository is not an opaque proxy for private CI; its workflows test the code that lives in this public project.

## Release discipline

The parent autopilot uses BUILD -> STABILIZE -> RC. Once an RC SHA is frozen, only reproducible release-blocking/high defects justify changing release code. Non-blocking hardening goes to the next-version backlog.