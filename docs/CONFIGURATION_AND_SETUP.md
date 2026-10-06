# ReleasePilot configuration and setup

For a concise operational walkthrough of GitHub Actions secrets, Kubernetes Secrets, first UAT validation, first production run, and Confluence publishing, see [Workflow and integration setup](WORKFLOW_INTEGRATIONS.md).

ReleasePilot creates and maintains **production-only** release runbooks. It is read-only: it does not deploy, restart, scale, roll back, or modify workloads.

The intended official process is:

```text
Release number
  → Jira Fix Version scope
  → GitHub UAT-to-PROD comparison
  → changed Helm component and image evidence
  → one updated production runbook
```

For example, release `26.10.02` creates or updates one file named `26.10.02-RUNBOOK.md`.

## 1. Local setup

Use local mode to review the interface and manually create an exceptional runbook. It does not automatically connect to Jira or GitHub.

```sh
cd releasepilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8095
```

Open `http://localhost:8095`.

Generated Markdown is stored at:

```text
releasepilot/runbooks/<release-number>-RUNBOOK.md
```

Submitting the same production release number updates the same current Markdown file. ReleasePilot retains structured revision history in `releasepilot/data/`.

## 2. What needs to be configured

Keep non-sensitive settings separate from credentials.

| Setting | Purpose | Where to configure it |
| --- | --- | --- |
| Jira base URL | Jira server address | Helm values / ConfigMap; optionally Settings page metadata |
| Jira project key | Jira project used for release tickets | Helm values / ConfigMap; optionally Settings page metadata |
| Production branch | Currently deployed baseline, normally `prd` | GitHub workflow input default or Helm values |
| Helm values-file pattern | Component production values location | Helm values / ConfigMap; normally `app_manifest/*/prd/values_prd.yaml` |
| Release calendar source | Annual release-team calendar; read-only | Helm values / ConfigMap |
| ArgoCD URL and application pattern | Health, sync, and last successful deployed image evidence | Helm values / ConfigMap |
| Prometheus URL and approved queries | Pre/post release error rate, latency, and availability evidence | Helm values / ConfigMap |
| Confluence base URL, space, parent page | Official runbook publishing location | Helm values / ConfigMap |
| Jira token | Read-only Jira API authentication | Kubernetes Secret and GitHub Actions secret |
| GitHub App credentials | Read access to repository/PR content; write only if committing runbooks | Kubernetes Secret |
| Workflow token | Authenticates GitHub Actions to ReleasePilot if the API endpoint is used | Kubernetes Secret and matching GitHub Actions secret |

Do not put tokens, passwords, private keys, or secret values in the Settings page, Helm values file, Markdown runbook, Git repository, or workflow logs.

## 3. Kubernetes / Helm deployment configuration

Your Helm chart should create a ConfigMap for public settings and a Secret for credentials. The application receives both as environment variables.

### Public configuration

Example Helm values structure:

```yaml
releasepilot:
  config:
    jiraBaseUrl: "https://jira.example.internal"
    jiraProjectKey: "GSS"
    githubRepository: "organisation/gssnav-app-deployment"
    productionBranch: "prd"
    helmValuesPattern: "app_manifest/*/prd/values_prd.yaml"
```

### Credentials

Create credentials as a Kubernetes Secret. The exact secret-creation method should follow your organisation’s approved secret-management process.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: releasepilot-integrations
type: Opaque
stringData:
  RELEASEPILOT_JIRA_TOKEN: "replace-through-approved-secret-management"
  GITHUB_APP_ID: "replace-with-app-id"
  GITHUB_APP_INSTALLATION_ID: "replace-with-installation-id"
  GITHUB_APP_PRIVATE_KEY: "replace-through-approved-secret-management"
  RELEASEPILOT_WORKFLOW_TOKEN: "replace-with-a-long-random-shared-token"
  RELEASEPILOT_ARGOCD_TOKEN: "replace-through-approved-secret-management"
  RELEASEPILOT_PROMETHEUS_TOKEN: "replace-through-approved-secret-management"
  RELEASEPILOT_CONFLUENCE_TOKEN: "replace-through-approved-secret-management"
```

The Deployment template should reference the keys, rather than placing their values in the Deployment:

```yaml
env:
  - name: RELEASEPILOT_JIRA_TOKEN
    valueFrom:
      secretKeyRef:
        name: releasepilot-integrations
        key: RELEASEPILOT_JIRA_TOKEN
  - name: GITHUB_APP_ID
    valueFrom:
      secretKeyRef:
        name: releasepilot-integrations
        key: GITHUB_APP_ID
  - name: GITHUB_APP_INSTALLATION_ID
    valueFrom:
      secretKeyRef:
        name: releasepilot-integrations
        key: GITHUB_APP_INSTALLATION_ID
  - name: GITHUB_APP_PRIVATE_KEY
    valueFrom:
      secretKeyRef:
        name: releasepilot-integrations
        key: GITHUB_APP_PRIVATE_KEY
  - name: RELEASEPILOT_WORKFLOW_TOKEN
    valueFrom:
      secretKeyRef:
        name: releasepilot-integrations
        key: RELEASEPILOT_WORKFLOW_TOKEN
```

Use a least-privilege service account and NetworkPolicy. ReleasePilot needs outbound HTTPS only to the approved Jira and GitHub endpoints. It does not need Kubernetes permissions to deploy workloads.

## 4. GitHub repository and workflow setup

Use two GitHub Actions workflows: an evidence-only UAT validation workflow and a production-only runbook workflow. Copy these files into the deployment repository:

```text
releasepilot/.github/workflows/releasepilot-runbook.example.yml
releasepilot/.github/workflows/releasepilot-uat-validation.example.yml
releasepilot/scripts/compare_prod_values.py
releasepilot/scripts/generate_runbook.py
```

Place the workflow at:

```text
.github/workflows/releasepilot-uat-validation.yml
.github/workflows/releasepilot-production-runbook.yml
```

The production workflow:

1. Runs from the selected UAT-to-PROD release branch.
2. Uses `prd` as the default production baseline.
3. Finds changed `app_manifest/<component>/prd/values_prd.yaml` files.
4. Compares the target and previous production image values.
5. Detects replica, resource, configuration-reference, and secret-reference changes without exposing secret values.
6. Optionally queries Jira for the matching Fix Version and adds only the approved release-scope fields to the evidence.
7. Generates or updates `runbooks/<release-number>-RUNBOOK.md`.
8. Optionally sends the redacted evidence to ReleasePilot. The same release number updates one runbook in the UI.
9. Commits the updated runbook to the selected branch.

Grant the workflow only the permissions it needs. If it commits the generated Markdown file, it needs `contents: write`. If branch protection blocks direct writes, let the workflow create an approved pull request instead.

The UAT workflow has `contents: read` only. It compares the UAT values path (default `app_manifest/*/uat/values_uat.yaml`) against the approved SIT baseline, retains a redacted validation artifact, and may read Jira scope. It never creates a release runbook, calls the production ReleasePilot API, commits files, or publishes to Confluence.

### GitHub Actions secrets

Configure these as repository or organisation secrets only when the corresponding integration is enabled:

| Secret | Used by |
| --- | --- |
| `RELEASEPILOT_WORKFLOW_TOKEN` | GitHub Actions calling the optional ReleasePilot API |
| `RELEASEPILOT_JIRA_TOKEN` | GitHub Actions querying Jira directly, if that design is chosen |
| `JIRA_BASE_URL` | GitHub Actions Jira address (secret, because it can be an internal hostname) |
| `RELEASEPILOT_API_URL` | Publicly reachable ReleasePilot API address for workflow-to-UI sync |
| GitHub App private key | Preferably held by ReleasePilot; only add to Actions when the workflow itself must call GitHub as the App |

Configure `JIRA_PROJECT_KEY` as a GitHub Actions **variable** (not a secret). The workflow deliberately continues without Jira or ReleasePilot API configuration, so teams can introduce one connector at a time.

The workflow token must exactly match the `RELEASEPILOT_WORKFLOW_TOKEN` value in the Kubernetes Secret.

## 5. Jira setup

Use a dedicated Jira service account with read-only access to the intended project and release fields.

Configure:

```text
JIRA_BASE_URL=https://your-jira-server
JIRA_PROJECT_KEY=GSS
RELEASEPILOT_JIRA_TOKEN=<secret>
```

When the Jira connector is enabled, ReleasePilot will query by the entered weekly release number as Jira Fix Version, for example `26.10.02`. The connector should retrieve only the fields required for a runbook:

- issue key and summary
- components and labels
- issue type
- change ticket reference
- migration and downtime indicators
- owner or team, when available

The current ReleasePilot Settings metadata supports Jira URL, project key, Fix Version field, and component field. It intentionally does not store tokens.

## 6. GitHub App setup

Prefer a GitHub App over a personal access token. Install it only on the deployment repository and grant the smallest necessary permission set:

- **Contents: Read** to inspect branches, files, and image-tag changes.
- **Pull requests: Read** to identify the UAT-to-PROD promotion.
- **Contents: Write** only if ReleasePilot itself commits the generated runbook.

Store the App ID, installation ID, and private key only in the Kubernetes Secret. Rotate the private key according to your organisation’s credential policy.

## 7. Recommended production flow

After the one-time configuration, a release engineer should only need to:

1. Open GitHub Actions from the UAT-to-PROD release branch.
2. Enter the release number, such as `26.10.02`.
3. Start the approved workflow.
4. The workflow compares only changed `app_manifest/<component>/prd/values_prd.yaml` files with the production baseline and, when configured, obtains the Jira Fix Version scope.
5. Review the single generated runbook in GitHub or ReleasePilot. Complete the approval checklist.
6. Select **Publish / update Confluence** in ReleasePilot. The page title is stable for the release number, so Confluence updates the existing official page and retains its own page history.

ReleasePilot will discover component changes from the Helm comparison. After Jira is enabled, it also adds the verified Jira release scope. Manual component entry in the local UI remains only for exceptional cases.

### Release calendar

The release team owns the annual release calendar. ReleasePilot must treat it as a read-only source and never create, reschedule, or alter release entries. Configure the approved calendar API or export URL when it becomes available. A calendar record should provide at least:

- release number, for example `26.10.02`
- approved production release date/window
- release owner
- optional change-ticket reference

ReleasePilot can then validate that a requested production runbook belongs to the official calendar before publishing it.

### ArgoCD and Confluence

When configured, ArgoCD provides the last revision that was both **Healthy** and **Synced**. That revision is the authoritative source for a “previous successful deployed image tag”; the `prd` Git value is only a fallback previous production value.

Confluence is the official runbook repository. Configure the existing space and parent page where the release team already keeps runbooks. ReleasePilot will create one page per release number and update the same page on later runs, preserving Confluence page history. Both supported modes require an explicit approval: `manual-approval` is for a reviewer using the UI; `workflow-approved` is for an approved automation identity.

## 8. End-to-end flow and ownership

```text
GitHub Actions input: release number
  → optional Jira Fix Version lookup
  → Git/Helm production-values comparison
  → generated Markdown committed to the deployment repository
  → optional secure API callback updates the one ReleasePilot runbook
  → reviewer completes the approval checklist
  → explicit Confluence publish updates the matching official page
```

The integration boundaries are intentionally small:

- **GitHub Actions** discovers the repository changes and generates the auditable file.
- **Jira** provides the planned scope; it is never treated as proof of deployment.
- **ReleasePilot** presents the evidence, checklist, revision history, and publishing control.
- **ArgoCD** is read-only evidence for deployed revision, sync state, health, and rollback target.
- **Confluence** is the long-term official reference and owns page history.

If a connector is unavailable, ReleasePilot labels the evidence source accordingly instead of inventing data. It never deploys, synchronizes, or rolls back an application.

## 9. Security checklist

- [ ] All secrets come from approved secret management and are mounted as Kubernetes Secrets.
- [ ] No secret value is rendered in generated evidence or Markdown.
- [ ] Jira service account is read-only.
- [ ] GitHub App is installed only on the required repository.
- [ ] GitHub Actions has least-privilege permissions.
- [ ] Production branch and workflow protections require the usual approvals.
- [ ] ReleasePilot remains read-only and does not have deployment, rollback, or workload-write permissions.
- [ ] Audit GitHub workflow runs, generated runbook commits, and deployment approvals.

## 9. Connector implementation status

The application includes the connector code and API contracts for Jira Fix Version discovery, GitHub/Helm workflow evidence, ArgoCD evidence, Prometheus checks, release readiness, human approvals, Confluence preview/publishing, and release dashboard status.

No connector is active merely because this code is deployed. It becomes active only after its URL, public metadata, runtime secret, outbound network rule, and least-privilege service account are supplied. Test each connector against the approved non-production endpoint before enabling it for production.

The direct GitHub App API is intentionally not used for deployment actions. The approved GitHub Actions workflow remains the source of Git/Helm discovery and the only place that writes the generated Markdown file. This separation keeps ReleasePilot read-only toward deployment systems.
