# PR coding-standards review

This directory runs the coding-standards review for pull requests and turns
the result into inline GitHub review comments.

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
2. Reads the rulebook from the **base commit**, not the PR head, so a PR
   cannot weaken the standards it is reviewed against.
3. Fetches the PR diff and checks out the PR head.
4. Runs opencode with the `pr-reviewer` agent in `opencode.json`. The agent
   can inspect the worktree (callers, types, tests) but cannot edit files,
   run tests, or reach the network.
5. Validates the agent's JSON findings and anchors each one to a line that is
   actually in the diff.
6. Posts one `COMMENT` review with inline comments. If the finding is outside
   the diff, it goes into the review body under "Findings outside the diff".
7. Posts nothing when there are no findings (silent when clean).

The agent is read-only. GitHub writes happen in `review.py` after the agent
exits, using the workflow's `GITHUB_TOKEN`.

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

Validate the examples:

```bash
python3 .github/pr-review/pr_evals.py validate \
  --examples-dir .github/pr-review/examples
```

## Examples

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
