# Workflow files staged outside `.github/workflows`

The files in this directory are the complete GitHub Actions workflow set imported
from NousResearch/hermes-agent commit
`9c87a7c79e9b14366f5dd9aa5b46cebde868cfd4`.

They are stored here because the Arena GitHub App has code write permission but
GitHub rejects pushes that create files under `.github/workflows` without the
separate workflow permission. No upstream workflow file content was changed.

When an authorized maintainer wants to activate them, move every upstream YAML
file from this directory to `.github/workflows/` and remove this notice.
