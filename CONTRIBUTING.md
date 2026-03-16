# Contributing to RTL Benchmark

Thanks for contributing to this project.

The goal of this repository is not only to host code, but to maintain a benchmark that other engineers and model teams can trust. Changes should therefore optimize for reproducibility, clear provenance, and benchmark integrity.

## What To Contribute

Contributions are welcome in four areas:

1. Benchmark infrastructure
2. Problem-bank content
3. Web and leaderboard UX
4. Documentation and best practices for Verilog / SystemVerilog agents

## Ground Rules

Before opening a pull request:

1. Keep changes focused. Split unrelated code, benchmark content, and UI work into separate pull requests when practical.
2. Add or update tests when behavior changes.
3. Document any new config, CLI behavior, or UI workflow.
4. Do not submit proprietary RTL, confidential verification collateral, or material you do not have the right to share.
5. Do not paste benchmark content copied from sites, books, or courses unless the source license clearly allows redistribution and you document the provenance.

## Problem-Bank Contributions

Problem contributions are especially valuable, but they need extra care.

When adding or editing benchmark cases:

1. Keep the task statement precise and executable.
2. Include enough metadata for filtering and analysis, such as source, suite, track, and difficulty when applicable.
3. Make the expected behavior verifiable through the included testbench and reference RTL.
4. Prefer original tasks or tasks derived from sources with clear redistribution rights.
5. If a task is adapted from an external source, state the provenance and license in the pull request description.

Good benchmark content should be hard to game, easy to reproduce, and unambiguous to grade.

## Code Contributions

For code changes:

1. Preserve deterministic behavior where possible.
2. Prefer small, reviewable patches over large rewrites.
3. Keep provider integrations and UI changes observable through tests or clear manual verification notes.
4. Avoid adding network dependencies, remote services, or heavyweight infrastructure unless there is a strong project need.

## Commit Sign-Off

This project uses the Developer Certificate of Origin (`DCO`) instead of a Contributor License Agreement.

Each commit in a contribution must be signed off by its author:

```bash
git commit -s -m "your message"
```

The sign-off certifies that you have the right to submit the work under this repository's license. See [DCO.md](DCO.md) for the full text.

## License Of Contributions

By contributing to this repository, you agree that your contribution is provided under the same license as the repository unless explicitly stated otherwise in the tree.

Current repository license:

- `AGPL-3.0-only` for code in this repository

If the project later adds separate licenses for documentation, data, or retained benchmark assets, those files will say so explicitly.

## Pull Request Checklist

Before requesting review, confirm:

1. The change is scoped and explained clearly.
2. Tests were added or updated when needed.
3. Benchmark content provenance is documented.
4. Every commit is signed off.
5. New user-facing behavior is reflected in `README.md` or another appropriate doc.
