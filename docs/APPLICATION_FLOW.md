# ReleasePilot application flow

ReleasePilot is a read-only release-planning application. It gathers approved release evidence, creates one production runbook for each release number, and helps people review and publish it. It never deploys an application, synchronizes ArgoCD, or performs a rollback.

## Normal operating flow

This is the standard flow once ReleasePilot has been deployed and configured. It is the flow used for each weekly production release.

1. **Deploy ReleasePilot once to Kubernetes.** The Helm deployment keeps the UI, history, and connector configuration available between releases.
2. **Configure the GitHub workflow callback once.** Store the ReleasePilot URL and shared workflow token in GitHub Actions secrets, and store the matching token in the ReleasePilot Kubernetes Secret.
3. **For each release, run the production GitHub Actions workflow** and provide the weekly release number, such as `26.10.09`.
4. **The workflow automatically:**
   - compares the changed production Helm values with the current `prd` baseline;
   - optionally reads the Jira Fix Version scope;
   - creates or updates `runbooks/<release-number>-RUNBOOK.md` in Git;
   - updates the matching ReleasePilot UI runbook after the Git commit succeeds, when the API callback is configured.
5. **In ReleasePilot, review the evidence and complete the human approval checklist.**
6. **Publish or update the single official Confluence page** for that same release number.

The ReleasePilot UI does not need manual runbook creation in the normal flow. Manual UI creation is only an exception path when the approved workflow is temporarily unavailable.

Rebuild and redeploy ReleasePilot only when its application code, Docker image, Helm chart, or deployment configuration changes. You do **not** rebuild or redeploy it for each weekly release.

## At a glance

```text
Developer prepares UAT changes
          │
          ▼
UAT validation workflow
  └─ compares UAT values with SIT
  └─ optional Jira Fix Version scope
  └─ stores a temporary evidence artifact
          │
          ▼
Approved UAT → Production promotion branch
          │
          ▼
Production runbook workflow
  └─ compares production values with prd
  └─ identifies components, image tags, and safe change signals
  └─ creates or updates one Markdown runbook in Git
  └─ optionally updates the matching ReleasePilot record
          │
          ▼
ReleasePilot review
  └─ review scope, evidence, risk, and open actions
  └─ complete human approval checklist
  └─ read ArgoCD evidence when configured
          │
          ▼
Publish / update Confluence
  └─ creates one official page for a new release number
  └─ updates the same page for later revisions
  └─ Confluence retains its page history
```

## 1. UAT validation flow

The release engineer starts **Validate UAT release scope** in GitHub Actions from the intended UAT branch.

The workflow receives:

- release number, for example `26.10.09`
- baseline reference, normally `sit`
- UAT Helm values-file pattern, normally `app_manifest/*/uat/values_uat.yaml`

It compares the selected UAT branch with the baseline and identifies changed components, target images, replica changes, resource changes, configuration references, and secret-reference changes. Secret values are never included.

If Jira is configured, the workflow also reads the Jira issues associated with the matching Fix Version. It produces a GitHub Actions artifact containing the redacted evidence.

The UAT flow deliberately does not:

- create a production runbook
- update ReleasePilot
- publish to Confluence
- commit generated files
- deploy or alter any environment

Its purpose is to let the team confirm scope before a production promotion is prepared.

## 2. Production discovery and runbook flow

After UAT approval, the release engineer starts **Create production release runbook** from the approved UAT-to-production promotion branch.

The workflow receives:

- production release number, for example `26.10.09`
- current production baseline, normally `prd`
- optional title and reviewer notes

It compares only changed production values files:

```text
app_manifest/<component>/prd/values_prd.yaml
```

For each changed component, it records:

- component name
- target image name and tag
- previous production image information from the baseline
- replica, resource, configuration-reference, and secret-reference changes
- source repository, branch, commit, and values-file path

The workflow then creates or updates this one stable file in the deployment repository:

```text
runbooks/<release-number>-RUNBOOK.md
```

For example, all runs for release `26.10.09` update:

```text
runbooks/26.10.09-RUNBOOK.md
```

It does not create `revision-2`, `final`, or duplicate files.

## 3. ReleasePilot UI flow

When the workflow-to-ReleasePilot API connection is configured, the production workflow sends its prepared, redacted evidence to ReleasePilot after the Markdown file has been committed successfully.

ReleasePilot uses the release number as the stable record identity:

- a new release number creates a new UI record
- the same release number updates the existing UI record
- each update increases the internal revision and preserves revision history

The **Release overview** shows each release, its revision state, the next required action, and the number of open actions. Select **Review open actions** to see exactly what is missing and return to the editor if a correction is needed.

Manual UI creation is available only as an exception path—for example, when an approved workflow is temporarily unavailable. The normal production path is the GitHub workflow.

## 4. Evidence and connector flow

ReleasePilot treats each system as a separate evidence source. It does not guess missing information.

| Source | Evidence used | When it is available |
| --- | --- | --- |
| GitHub / Helm | Changed components, image tags, values-file changes, repository and commit provenance | Production workflow run |
| Jira | Fix Version issue scope and component context | Jira URL, project key, and read-only token configured |
| ArgoCD | Application health, sync state, deployed revision, and rollback target | ArgoCD safe metadata and read-only token configured |
| Prometheus | Approved error-rate, latency, and availability queries | Prometheus URL, query definitions, and access configured |
| Confluence | Official release page and page history | Confluence safe metadata and publishing token configured |

If a connector is not configured, ReleasePilot shows **not configured** or identifies the evidence source as workflow input. It never replaces unavailable evidence with sample data.

## 5. Approval and publishing flow

Before Confluence publishing, the release reviewer completes the approval checklist in ReleasePilot:

1. Release engineer confirmed
2. Application owner confirmed
3. Change ticket approved
4. Rollback plan confirmed
5. Validation sign-off confirmed

Once the release is ready and Confluence is configured, select **Publish / update Confluence**.

ReleasePilot searches Confluence using the stable page title for the release number:

```text
<release-number> GDC Release Runbook
```

- If the page does not exist, ReleasePilot creates it beneath the configured parent page.
- If the page already exists, ReleasePilot updates that same page.
- Confluence maintains its normal page-version history.

Publishing is explicit: ReleasePilot does not publish automatically merely because a workflow completed.

## 6. Repeat-run behavior

Teams commonly rerun a release process as scope changes. ReleasePilot is designed for this.

| User action | Result |
| --- | --- |
| Run UAT validation again | Creates a new temporary Actions artifact only. |
| Run production workflow again with the same release number | Updates the same Git Markdown file and ReleasePilot record. |
| Publish the same release again | Updates the same Confluence page. |
| Run production workflow with a new release number | Creates one new Markdown file, UI record, and later Confluence page. |

The official runbook remains easy to find because its release number never changes.

## 7. Where history lives

| History | System of record |
| --- | --- |
| Generated Markdown changes | Git commit history in the deployment repository |
| Workflow evidence | GitHub Actions artifact retention policy |
| UI revisions | ReleasePilot persistent data volume; future external database when deployed at scale |
| Official release documentation | Confluence page history |

The ReleasePilot Helm chart enables persistent storage by default so UI history survives Pod restarts and redeployments. For high availability and long-term audit retention, configure the planned external database before using multiple replicas.

## 8. Safety boundary

ReleasePilot helps people make informed release decisions. The approved action systems remain separate:

```text
ReleasePilot: evidence, runbooks, review, approvals, Confluence publishing
GitHub Actions: controlled repository automation
ArgoCD: approved synchronization and rollback
Kubernetes: workload execution
```

This separation prevents a planning or documentation tool from silently changing production workloads.
