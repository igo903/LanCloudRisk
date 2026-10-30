# LanCloudRisk development rules

## Mandatory manual confirmation before every future submission

The user authorized the initial v0.4.2 import into igo903/LanCloudRisk.
For every subsequent batch of code changes:

1. Implement the requested changes locally and run the relevant checks.
2. Show the user a concise summary of the concrete changes, test results and material limitations.
3. Wait for explicit manual confirmation for that exact batch before creating a Git commit, pushing code, or uploading source changes through the GitHub API.
4. Only after confirmation, submit the approved changes to this repository and report the resulting commit link.

An instruction to edit code, a previous confirmation, an expired approval request, or silence is not confirmation for a new submission. Do not configure automatic commits, pushes or source synchronization. Keep unapproved work local and reviewable.

## Source of truth and checks

- Edit backend source in `local/server.py` and frontend source in `dist/`.
- Regenerate `run_risklens_local.py` using `python3 tools/build_local_bundle.py` after source changes. Preserve this downloadable single-file entry point.
- Use the relevant regression tests; do not report checks as passed unless they completed successfully.
- Keep UI/data provenance honest. Missing information remains missing. Intraday K-lines must remain provisional and excluded from confirmed-history performance.
- Preserve the documented accounting rules for fees, partial sales and weighted buy cost.
- Never commit credentials, tokens, account snapshots, holdings, runtime caches or user-specific environment files.
- Keep the repository private unless the user explicitly requests a visibility change.
