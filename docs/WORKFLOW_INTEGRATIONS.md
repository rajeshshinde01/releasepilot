# ReleasePilot workflow and integration setup

For the normal operating flow—deploy once, configure once, then run a workflow per release—plus the complete application journey and repeat-run behavior, see [Application flow](APPLICATION_FLOW.md).

This guide configures the two ReleasePilot workflows and their approved connections. It does not grant ReleasePilot permission to deploy, sync, scale, restart, or roll back workloads.

## 1. The two workflows

Copy the example workflows to the repository that contains the application Helm values files:

| Purpose | Copy from ReleasePilot | Active file in deployment repository | What it does |
| --- | --- | --- | --- |
| UAT validation | `.github/workflows/releasepilot-uat-validation.example.yml` | `.github/workflows/releasepilot-uat-validation.yml` | Read-only comparison of UAT changes with the SIT baseline; keeps evidence as an Actions artifact. |
| Production runbook | `.github/workflows/releasepilot-runbook.example.yml` | `.github/workflows/releasepilot-production-runbook.yml` | Compares production Helm values with `prd`, creates or updates one runbook, and can update the ReleasePilot UI. |

Also copy these helpers into the same deployment repository:

```text
releasepilot/scripts/compare_prod_values.py
releasepilot/scripts/generate_runbook.py
```

The workflow templates expect the helpers at `releasepilot/scripts/`. Keep that directory layout, or update the helper paths in both workflow files.

## 2. Configure GitHub Actions

In the deployment repository, open:

```text
Settings → Secrets and variables → Actions
```

### GitHub Actions secrets

Add only the secrets needed for the integrations you enable.

| Name | Required for | Notes |
| --- | --- | --- |
| `JIRA_BASE_URL` | Jira release scope | Internal Jira base address; store as a secret to avoid exposing internal endpoints. |
| `RELEASEPILOT_JIRA_TOKEN` | Jira release scope | Dedicated, read-only Jira service token. |
| `RELEASEPILOT_API_URL` | Production workflow → ReleasePilot UI | HTTPS URL of the deployed ReleasePilot service, for example `https://releasepilot.example.internal`. |
| `RELEASEPILOT_WORKFLOW_TOKEN` | Production workflow → ReleasePilot UI | Long random shared token. It must match the token in the Kubernetes Secret. |

Do not add Confluence, ArgoCD, Kubernetes, database, or GitHub App private keys to the workflow unless there is a separately approved need. The standard workflow does not deploy anything.

### GitHub Actions variable

Add this as a **variable**, not a secret:

| Name | Example | Purpose |
| --- | --- | --- |
| `JIRA_PROJECT_KEY` | `GSS` | Limits the Jira Fix Version search to the approved project. |

### Workflow permissions

- The UAT workflow uses `contents: read` only.
- The production workflow uses `contents: write` because it commits `runbooks/<release>-RUNBOOK.md`.
- If direct writes to your production branch are blocked, change the production workflow to create an approved pull request instead of pushing directly.

## 3. Configure ReleasePilot after Kubernetes deployment

Open the ReleasePilot **Connections** screen. Enter only safe, non-secret information:

| Connection | Safe details in ReleasePilot UI |
| --- | --- |
| Jira | Base URL and project key |
| GitHub | Deployment repository, production branch, and production workflow filename |
| ArgoCD | Base URL and application-name pattern |
| Confluence | Base URL, space key, parent page ID, and page-title template |

The UI must never contain API tokens, passwords, private keys, or client secrets.

## 4. Create the Kubernetes Secret

Create the Secret through your approved secret-management process. This example is only a key list; replace the placeholders through your organisation’s secure process.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: releasepilot-integrations
  namespace: releasepilot
type: Opaque
stringData:
  RELEASEPILOT_ADMIN_TOKEN: "<admin-token>"
  RELEASEPILOT_WORKFLOW_TOKEN: "<same-value-as-github-actions-secret>"
  RELEASEPILOT_JIRA_TOKEN: "<read-only-jira-token>"
  RELEASEPILOT_ARGOCD_TOKEN: "<read-only-argocd-token>"
  RELEASEPILOT_CONFLUENCE_TOKEN: "<confluence-publish-token>"
  GITHUB_APP_ID: "<github-app-id>"
  GITHUB_APP_INSTALLATION_ID: "<github-installation-id>"
  GITHUB_APP_PRIVATE_KEY: "<github-app-private-key>"
```

Reference it in the Helm installation values:

```yaml
secrets:
  existingSecret: releasepilot-integrations
```

The Helm chart maps only these keys into the ReleasePilot container. It does not create Secret resources or retain their values in Git.

## 5. First UAT test

1. Commit the UAT workflow to the deployment repository.
2. In **Actions**, run **Validate UAT release scope** from the intended UAT branch.
3. Enter the release number, such as `26.10.09`.
4. Use `sit` as the baseline unless your team uses a different approved baseline.
5. Download the `releasepilot-uat-validation-<release-number>` artifact.
6. Confirm it contains the expected changed components, image tags, and redacted Jira evidence.

UAT generates validation evidence only. It cannot create a production runbook, update Confluence, or invoke the ReleasePilot production API.

## 6. First production run

Before production, confirm that the UAT evidence is approved and the UAT-to-production promotion branch is ready.

1. Run **Create production release runbook** from the UAT-to-production branch.
2. Enter the weekly release number, for example `26.10.09`.
3. Keep `prd` as the baseline unless the currently deployed production reference differs.
4. Review the generated `runbooks/26.10.09-RUNBOOK.md` commit and evidence artifact.
5. If `RELEASEPILOT_API_URL` and `RELEASEPILOT_WORKFLOW_TOKEN` are configured, verify the same release appears in the ReleasePilot UI.
6. In ReleasePilot, complete the human approval checklist.
7. Use **Publish / update Confluence** to create or update the one official Confluence page for that release number.

Running production again with the same release number updates the same Markdown runbook, the same ReleasePilot record, and the same Confluence page. It does not create duplicate release documents.

## 7. Connection boundaries

| System | What ReleasePilot/workflow may do | What it never does |
| --- | --- | --- |
| GitHub | Read Helm values; production workflow writes the generated Markdown runbook | Deploy applications or alter application code |
| Jira | Read Fix Version release scope | Create, update, transition, or comment on issues |
| ArgoCD | Read health, sync, revision, and image evidence | Synchronize, rollback, or modify applications |
| Confluence | Create or update the matching official release page after explicit approval | Create duplicate pages for the same release number |
| Kubernetes | Host ReleasePilot | Grant ReleasePilot permissions to modify workloads |

## 8. Troubleshooting checklist

| Symptom | First check |
| --- | --- |
| Jira scope is empty | Fix Version spelling, `JIRA_PROJECT_KEY`, token permissions, and Jira endpoint. |
| UAT finds no files | Confirm the UAT values pattern and baseline reference. |
| Production finds no files | Confirm `app_manifest/*/prd/values_prd.yaml` and the `prd` baseline. |
| UI is not updated after production workflow | Confirm `RELEASEPILOT_API_URL`, matching `RELEASEPILOT_WORKFLOW_TOKEN`, ingress reachability, and workflow logs. |
| Confluence publish is unavailable | Confirm Confluence safe metadata in the UI, the Kubernetes token Secret, outbound HTTPS policy, and approvals. |
| Duplicate runbook concern | Use the exact same release number; ReleasePilot updates by release number. |
