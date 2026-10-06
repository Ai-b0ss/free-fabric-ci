# Agent Operating System — free-fabric-ci

> Mandatory repository-orientation contract for autonomous coding/research agents.
>
> Purpose: prevent useful work from becoming effectively lost because it lives on another branch, an older commit, an evidence line, a deleted file, or a document the current agent did not know existed.

## 1. Repository identity

Public reusable Free Fabric CI, acceptance, release and adversarial harness repository.

The repository is not equivalent to the current working tree and is not equivalent to `main`.
Treat it as five overlapping knowledge layers:

1. **Current integration state** — normally `main`.
2. **Current branch tips** — every named branch and its exact head SHA.
3. **Reachable Git history** — older commits, including files/code later deleted from branch tips.
4. **Documentation/evidence** — decisions, experiments, audits, proofs, handoffs and abandoned designs.
5. **Local working state** — uncommitted files on the machine, if present. This layer is not represented by GitHub and must be inspected separately when a live checkout is available.

Do not declare an idea, implementation or decision nonexistent until the relevant layers have been searched.

## 2. Mandatory bootstrap for every substantial task

Before making a repository-level conclusion or non-trivial edit:

1. Read root `AGENTS.md`.
2. Read `docs/AGENT_NAVIGATION.md`.
3. Read this file.
4. Identify the exact working ref and commit:
   `git branch --show-current`
   `git rev-parse HEAD`
   `git status --short`
5. Inspect `.agent/branch-index.json` for the known branch surface.
6. Inspect `.agent/file-index.json` for the canonical `main` file surface.
7. Read task-specific state, manifests, tests and runbooks named by `AGENTS.md` / `AGENT_NAVIGATION.md`.
8. If the task refers to an old feature, abandoned approach, previous bug fix, forgotten name, "we did this before", or anything that may have moved/disappeared: search **all branch tips and full Git history** before designing a replacement.

A fast task may skip irrelevant deep reading, but it must not skip branch/ref identity or the possibility that prior work already exists.

## 3. Canonical access tools

### Exact code map for any branch/commit

```bash
python tools/build_agent_index.py --ref "<branch-or-SHA>"
```

This writes `.agent/local-code-index.json` containing, for that exact ref:

- resolved commit SHA;
- every tracked file;
- blob SHA;
- byte size and SHA-256;
- text line count;
- detected functions/classes/arrow functions and their start lines.

Use it as a jump table, not as a substitute for reading callers, callees and tests.

### List every current branch tip

```bash
python tools/agent_history.py branches
```

Filter a family:

```bash
python tools/agent_history.py branches --glob "worker/*"
```

### Understand one branch relative to main

```bash
python tools/agent_history.py branch-info "<branch>"
```

Returns exact head, date, subject, merge-base, ahead/behind counts and changed files. Use this before calling a branch "old", "merged", "abandoned", "final" or "newer".

### Search text in current branch tips

```bash
python tools/agent_history.py find-text "term"
python tools/agent_history.py find-text "term" -i
python tools/agent_history.py find-text "regex" --regex
```

Use when the work probably still exists at some branch head.

### Search text through full reachable history

```bash
python tools/agent_history.py find-history-text "term"
```

This is the critical recovery path. It can find code/text that existed in an older commit and was later deleted even from the same branch.

For very large repositories, narrow the first pass:

```bash
python tools/agent_history.py find-history-text "term" --max-commits 500 --max 100
```

Then broaden if necessary.

### Find files by path across branch tips

```bash
python tools/agent_history.py find-path "**/ROADMAP*.md"
```

### Find files that existed only in older commits

```bash
python tools/agent_history.py find-history-path "**/ROADMAP*.md"
```

### Build a richer branch metadata index

```bash
python tools/agent_history.py index
```

This generates `.agent/history-index.json` from the local clone with dates, subjects, ahead/behind counts, changed-file counts and top-level change areas.

## 4. Lost-work recovery protocol

Use this whenever current code seems incomplete, a user remembers prior work, a branch name hints at a previous solution, or a replacement would duplicate old effort.

### Phase A — formulate search vocabulary

Search not only the current feature name. Include:

- old names and abbreviations;
- user-facing labels;
- class/function names;
- error strings;
- endpoint/action names;
- filenames;
- provider/product names;
- commit-message terms;
- synonyms in Russian/English if the project has mixed-language history.

### Phase B — search branch tips

Run `find-text` and `find-path`.
Inspect candidate branches with `branch-info`.

### Phase C — search deleted history

If branch-tip search is empty or suspiciously incomplete, run `find-history-text` and `find-history-path`.
A zero result from current branches is **not** evidence that the work never existed.

### Phase D — reconstruct context

For every promising candidate:

1. record branch/commit SHA;
2. inspect commit subject/date;
3. build exact code index for that ref;
4. read the implementation;
5. read adjacent tests/docs/evidence;
6. compare against `main`;
7. inspect later commits that removed/replaced it when relevant.

### Phase E — classify instead of guessing

Use one of these labels in notes/reports:

- **ACTIVE** — current authoritative implementation.
- **CANDIDATE** — plausible unmerged work worth evaluating.
- **EVIDENCE** — proof/diagnostic artifact, not product source.
- **EXPERIMENT** — deliberately exploratory.
- **SUPERSEDED** — replaced by a known later implementation/decision.
- **ABANDONED** — intentionally stopped, with evidence for that conclusion.
- **UNKNOWN** — status cannot yet be proven.

Never classify from branch names alone. Words like `final`, `fixed`, `canonical`, `prod`, `release`, `old`, `tmp` or `backup` are hints, not proof.

### Phase F — reuse safely

Do not cherry-pick or copy old code merely because it exists.

Before revival, answer:

- What problem did it solve?
- Why was it not merged / why was it later removed?
- Which assumptions have changed?
- Which tests still apply?
- Does current architecture already solve the same problem differently?
- Is the old code evidence-only, unsafe, platform-specific or dependent on retired infrastructure?

Preserve useful ideas even when implementation should not be revived.

## 5. Source-of-truth hierarchy

For **runtime behavior**:
1. exact code on the selected ref;
2. tests/gates exercising that behavior;
3. current manifests/configuration/state files;
4. current architecture/runbook documents;
5. historical docs/evidence;
6. comments, branch names and commit titles.

For **product intent / owner requirements**:
1. explicit current owner requirements and current task;
2. current authoritative product/state documents named by `AGENTS.md`;
3. accepted design decisions;
4. implementation;
5. historical ideas.

For **historical claims**:
1. immutable commit/blob evidence;
2. branch/commit metadata;
3. contemporaneous reports/tests;
4. later summaries.

If sources conflict, state the conflict. Do not silently choose the most convenient one.

## 6. Line-level code reading rules

"Knowing the file" is not enough.

For a non-trivial behavior:

1. pin branch + commit;
2. use `build_agent_index.py` for symbol locations;
3. read the symbol body;
4. read callers;
5. read direct callees that control outcomes;
6. inspect error/timeout/retry/cancellation paths;
7. inspect state mutation and persistence;
8. inspect user-visible result path;
9. inspect nearest tests;
10. search other branches/history for alternate implementations if the area has a long experimental history.

Never carry line numbers from one branch/commit into another without regenerating the index.

## 7. Documentation rules for future agents

When adding or changing substantial behavior:

- update the relevant authoritative map/state document;
- name exact code entry points;
- name tests/gates that prove the behavior;
- distinguish current truth from historical context;
- preserve links/SHAs to superseded work when it may explain future decisions;
- record why an approach was abandoned, not only that it was abandoned;
- avoid creating another free-floating "FINAL_v7_really_final" document when an authoritative file can be amended.

A future agent should be able to answer "why is this like this?" without reverse-engineering the entire commit history, while still being able to reach that history when necessary.

## 8. Branch preservation policy

Historical/evidence branches are normally read-only.

Do **not** update every old branch with new navigation files: that destroys immutable evidence and changes head SHAs.
Navigation belongs in the canonical branch and must be able to inspect old refs externally.

When a task explicitly revives an old branch, create a new working branch from the chosen commit unless preserving the original branch identity is required.

## 9. Limits of "all work"

These tools can recover work that is still reachable from Git refs/history in the clone/GitHub repository.

They cannot guarantee recovery of:

- work that was never committed;
- local files on a machine that are not available;
- commits reachable only from deleted refs after server garbage collection;
- external artifacts that were never stored or linked.

If a user says work existed but Git search finds nothing, inspect connected/local machines, CI artifacts, issues/PRs, releases, external storage and prior handoffs before concluding it is gone.

## 10. Completion standard for an agent

Before claiming a repository-level task is complete, report:

- exact branch and final commit;
- main files/symbols changed;
- tests/checks run;
- historical branches/commits consulted when relevant;
- whether prior work was reused, superseded or deliberately rejected;
- remaining UNKNOWNs;
- whether any user action is genuinely required.

The goal is not maximum documentation volume. The goal is **lossless orientation**: small mandatory entry points that lead deterministically to every level of detail when needed.
