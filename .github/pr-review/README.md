# PR coding-standards review

This directory runs **Code Oracle**, the coding-standards review bot for pull
requests, and turns the result into inline GitHub review comments.

It is the CI version of the eval that produced
`chainlink-coding-standards-ai-automation-poc.xlsx`:

- Producer: `intern4all/pr-eval`
  (`run.sh` -> `entrypoint.sh` -> `make-csv.py` -> `out/pr_evals.csv`)
- Model: Fireworks `accounts/fireworks/models/glm-5p3-flash` (GLM 5.3 Flash)
- Rulebook: `.github/copilot-instructions.md`, copied to `/prs/standards.md`
  by the eval's `Dockerfile`
- Sheet: all 22 rows in the `pr_evals` tab match the eval's
  `out/pr_evals.csv` byte-for-byte

## How the workflow works

`.github/workflows/pr-coding-standards-review.yaml`:

1. Checks out the repository and resolves base/head SHAs for the PR.
2. Reads the rulebook from the **default branch**, not the PR head, so a PR
   cannot weaken the standards it is reviewed against. This matches the
   original eval's "current repo standard"; if the default branch fetch is
   unavailable it falls back to the base then the head commit.
3. Fetches the PR diff and checks out the PR head.
4. Runs opencode with the `pr-reviewer` agent in `opencode.review.json`. The agent
   can inspect the worktree (callers, types, tests) but cannot edit files,
   run tests, or reach the network.
5. Validates the agent's JSON findings and anchors each one to a line that is
   actually in the diff.
6. Posts one `COMMENT` review with inline comments. If the finding is outside
   the diff, it goes into the review body under "Findings outside the diff".
7. Posts nothing when there are no findings (silent when clean).

The agent is read-only. GitHub writes happen in `review.py` after the agent
exits, using the workflow's `GITHUB_TOKEN`.

### Who posts?

The workflow posts as `github-actions[bot]`. GitHub does not let a repository
rename that account, so the **Code Oracle** persona is in the review body and
the footer of every inline comment. The `GITHUB_TOKEN` cannot post as a custom
bot. A true custom author (`code-oracle[bot]` with its own avatar) needs a
GitHub App installed on the repository; that is a separate setup step.

The `replay` subcommand posts a saved eval example through the workflow token,
which is how the demo review on `007vasy/chainlink#3` was moved off a
developer's personal `gh` login and onto the Actions bot.

## Triggers

- `pull_request` `ready_for_review` and `reopened`: review automatically.
- `pull_request` `labeled`: review when the `ai-review` label is added.
- `pull_request` `synchronize`: re-review only when the `ai-review` label is
  present.
- `issue_comment` `/review` or `/ai-review`: re-review on request; the
  commenter must have write or admin permission.
- `workflow_dispatch`: review a specific PR. `dry_run` defaults to `true`.

Fork PRs are skipped because GitHub does not expose repository secrets to
them. Use a branch in the repository, or run the workflow in the fork.

## Required secret

Add `FIREWORKS_API_KEY` as a repository secret. The workflow fails with a
clear message if it is missing.

## Local use

`--checkout` runs `git checkout --detach <head>`, so use a clean checkout or a
separate worktree; do not run it in a tree with uncommitted work.

Dry-run a PR without posting:

```bash
FIREWORKS_API_KEY=... python3 .github/pr-review/review.py run \
  --repo smartcontractkit/chainlink \
  --pr 23837 \
  --workdir . \
  --out-dir /tmp/pr-review \
  --checkout \
  --dry-run
```

Outputs:

- `/tmp/pr-review/review.json` - the GitHub review payload
- `/tmp/pr-review/review.md` - a rendered preview
- `/tmp/pr-review/findings.json` - normalized agent findings
- `/tmp/pr-review/agent-output.txt` - raw agent output
- `/tmp/pr-review/opencode.log` - agent stderr

Validate that every inline comment anchors to a real diff line:

```bash
python3 .github/pr-review/review.py validate \
  --payload /tmp/pr-review/review.json \
  --diff /tmp/pr-review/diff.patch
```

## Replaying the original eval

`pr_evals.py` turns the old eval responses into the same review payloads used
by the workflow:

```bash
python3 .github/pr-review/pr_evals.py replay \
  --eval-dir ~/Projects/intern4all/pr-eval/out \
  --prs-json ~/Projects/intern4all/pr-eval/build/prs.json \
  --diff-dir ~/Projects/intern4all/pr-eval/build/prs \
  --out-dir .github/pr-review/examples \
  --prs 23837,23832,23878 \
  --with-jmank
```

It also has `collect` (find merged PRs reviewed by a user) and `run` (run the
live reviewer over a set in dry-run and write `pr_evals.jsonl` /
`pr_evals.csv`).

To post a saved example through the workflow token:

```bash
python3 .github/pr-review/review.py replay \
  --repo 007vasy/chainlink \
  --pr 3 \
  --example .github/pr-review/examples/23837 \
  --post
```

Validate the examples:

```bash
python3 .github/pr-review/pr_evals.py validate \
  --examples-dir .github/pr-review/examples
```

## Examples

Live demo PRs: [#2](https://github.com/007vasy/chainlink/pull/2) (smoke test),
[#3](https://github.com/007vasy/chainlink/pull/3) (replay of #23837),
[#4](https://github.com/007vasy/chainlink/pull/4) (live #23832),
[#5](https://github.com/007vasy/chainlink/pull/5) (live #23878).

`examples/` contains three recent PRs reviewed by `jmank88`, each with:

- `jmank.md` - his actual inline comments, as the style reference
- `eval.md` - the original eval response
- `findings.json` - the findings normalized into the workflow schema
- `review.json` - the GitHub review payload with inline comments
- `review.md` - a rendered preview
- `meta.json` - PR URL, base/head SHA, title
- `diff.patch` - the exact diff used to validate anchors

See `examples/README.md` for the comparison and the live demo links.

## Tests

```bash
python3 -m unittest discover -s .github/pr-review/tests -v
```

The self-test workflow runs the unit tests and validates the examples against
the real PR diffs on every change to this directory.
