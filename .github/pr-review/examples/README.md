# Examples: inline feedback on PRs reviewed by jmank88

These examples come from the original eval run that produced
`chainlink-coding-standards-ai-automation-poc.xlsx`. The source is
`intern4all/pr-eval`; its `Dockerfile` copies
`.github/copilot-instructions.md` to `/prs/standards.md`, and `entrypoint.sh`
asks GLM 5.3 Flash to judge the PR against that file.

The three PRs below were reviewed by `jmank88` and are in the eval set. Each
directory contains:

| File | What it is |
| --- | --- |
| `jmank.md` | jmank88's actual inline comments, with file:line anchors. This is the style reference. |
| `eval.md` | The original eval response, as produced by `intern4all/pr-eval`. |
| `findings.json` | The eval findings normalized into the workflow schema. |
| `review.json` | The GitHub review payload: one `COMMENT` review with inline comments. |
| `review.md` | A rendered preview of the review. |
| `meta.json` | PR URL, title, base SHA, head SHA. |
| `diff.patch` | The exact diff used to validate every anchor. |

## The three PRs

| PR | Title | jmank88 comments | Eval findings | Inline examples |
| --- | --- | ---: | ---: | ---: |
| [#23837](https://github.com/smartcontractkit/chainlink/pull/23837) | `[CRE-6745] wire noop coordinator + feature flag` | 3 | 5 | 5 |
| [#23832](https://github.com/smartcontractkit/chainlink/pull/23832) | `[CRE] Prune stale remote capability shims in the launcher` | 3 | 3 | 3 |
| [#23878](https://github.com/smartcontractkit/chainlink/pull/23878) | `[CRE-6746] trigger coordinator implementation` | 5 | 1 | 1 |

All nine eval findings anchor to added lines in the diff, so the workflow can
post every one as a true inline comment.

## What the comparison shows

These are complementary reviews, not a like-for-like match:

- **#23837:** jmank88 asked for package defaults, questioned a bool where a
  timestamp range would preserve the start time, and suggested extracting a
  duplicated cleanup block. The eval instead found a leaked gate limiter, a
  hardcoded DON config version, a noop that violates its interface contract,
  and missing tests for the noop. The duplication theme overlaps; the
  correctness findings are deeper.
- **#23832:** jmank88 asked about double-close semantics, `shimID` naming, and
  a `slices.Delete` simplification. The eval found that pruning a stale shim
  can remove the wrong registry entry, that a removed method can still
  silently return a dead callback, and that the executable prune paths were
  untested. Almost no overlap.
- **#23878:** jmank88 focused on concurrency and lifecycle shape (RWMutex,
  deferred cleanup, helper extraction, `sync.Map.Range` semantics). The eval
  found that a failed capability unregistration is never retried and its
  retry state is dropped.

The lesson is the same one the reviewer interviews reached: the bot is useful
for the behavioural defect a human reviewer may not have time to chase, and
the human is better at naming, shape, and future-fragility comments. The
workflow should stay silent on the latter when it is only style.

## Regenerate

```bash
python3 .github/pr-review/pr_evals.py replay \
  --eval-dir ~/Projects/intern4all/pr-eval/out \
  --prs-json ~/Projects/intern4all/pr-eval/build/prs.json \
  --diff-dir ~/Projects/intern4all/pr-eval/build/prs \
  --out-dir .github/pr-review/examples \
  --prs 23837,23832,23878 \
  --with-jmank
```

## Validate

```bash
python3 .github/pr-review/pr_evals.py validate \
  --examples-dir .github/pr-review/examples
```

The validator checks that every `review.json` comment names a file in the diff
and that its `line` and `side` are a real GitHub anchor. The self-test
workflow runs the same check, and can fetch the diffs from GitHub with
`--online` when a cached `diff.patch` is not present.

## Live demo on the personal fork

The workflow was exercised on `007vasy/chainlink` so it could post real
inline comments without touching merged upstream PRs. Each demo PR uses the
exact upstream head and a base branch at the original base commit, so the diff
is the reviewed change.

| Demo PR | Source | Review | Inline comments |
| --- | --- | --- | ---: |
| [#2](https://github.com/007vasy/chainlink/pull/2) | smoke test (go.mod downgrade + flaky test) | live agent | 2 |
| [#3](https://github.com/007vasy/chainlink/pull/3) | replay of [#23837](https://github.com/smartcontractkit/chainlink/pull/23837) | [eval replay](https://github.com/007vasy/chainlink/pull/3#pullrequestreview-5709418044) | 5 |
| [#4](https://github.com/007vasy/chainlink/pull/4) | live agent on [#23832](https://github.com/smartcontractkit/chainlink/pull/23832) | live agent | 2 |
| [#5](https://github.com/007vasy/chainlink/pull/5) | live agent on [#23878](https://github.com/smartcontractkit/chainlink/pull/23878) | live agent | 1 |
| [#6](https://github.com/007vasy/chainlink/pull/6) | synthetic race/leak test | `pull_request` opened | 3 |

The live runs on #4 and #5 reproduced the eval's two main #23832 findings and
its #23878 finding. The live run on #3 returned no findings on the last run;
the five comments there are a deterministic replay of the original eval
response. That variance is exactly why the eval harness judges pooled
findings rather than trusting one model run, and why the workflow is silent
when it is not confident.

The old jmank88 demo PRs use base branches at their original base commits,
which predate this workflow, so GitHub cannot run a `pull_request` workflow
from those base branches. They were run with `workflow_dispatch`. PR #6
targets the current `develop` and proves the automatic `pull_request` /
`opened` path. Commenting `/review` on #2 also proves the command path and the
path+line deduplication (a second run added no duplicate comments).

The smoke test PR #2 is useful in its own right: the agent caught the
`go.mod` Go-version downgrade (a repo-wide build break) and the fact that the
new test exercises no capabilities code. Both are real defects that a
test-only PR description hides.
