# ReleasePilot

For deployment configuration, Jira/GitHub setup, security controls, and the official workflow, see [Configuration and setup](docs/CONFIGURATION_AND_SETUP.md).

ReleasePilot creates a reviewable release runbook from a declared release scope. It is deliberately read-only: it never deploys, restarts, scales, or changes a workload.

## Current capabilities

- Collect production release title, components, versions, owners, and declared dependencies.
- Identify missing declared dependencies in the submitted scope.
- Classify planning risk as standard, medium, or high.
- Create structured prepare, release, validation, and rollback guidance.
- Keep the generated plan explicit about its evidence source.

## Automatic release runbooks from GitHub Actions

The included GitHub Actions workflow is the recommended production path. It needs no ReleasePilot UI, URL, token, or running service: it compares the branch selected in GitHub Actions with the production baseline (default: `prd`), generates a Markdown runbook, and commits it back to that selected branch. Running it again with the same release number updates the same file instead of creating another runbook.

The generated runbook uses the following safeguards:

- Jira issue types, downtime markers, and migration markers influence release risk.
- Helm image, replica, resource, configuration-reference, and secret-reference changes are made visible.
- Secret values are never accepted, stored, or rendered; only a changed reference is recorded.
- Helm components without a matching Jira component are explicitly flagged for human review.
- The result records repository/ref provenance and the generated release evidence is retained as a workflow artifact.

Copy `.github/workflows/releasepilot-runbook.example.yml` and the `releasepilot/scripts/` directory into the deployment repository. In GitHub Actions, select **Run workflow**, choose the branch under **Use workflow from**, enter the release number and the current production baseline ref, and optionally add release notes. The workflow creates or updates `runbooks/<release>-RUNBOOK.md` on that branch. It requires repository write permission so it can commit the runbook.

### Weekly UAT → Production release process

For a weekly release such as `26.09.25`, start the workflow from the UAT → Prod branch and enter that release number. The production baseline defaults to `prd`; change it only when the currently deployed production reference is different. The workflow scans every changed component file matching `app_manifest/<component>/prd/values_prd.yaml`. The folder name is used as the component identity; for example, `app_manifest/gssnav-coi-ui/prd/values_prd.yaml` becomes `gssnav-coi-ui`.

The included `scripts/compare_prod_values.py` understands the component values structure shown in your example, including `container.imageName` and `container.imageVersion`, and produces a redacted manifest of:

- Changed production image tags
- Replica changes
- CPU/memory resource changes
- Configuration-reference changes
- Secret-reference changes, without including secret values

The target environment is supplied by the approved UAT → Prod workflow, not inferred from a field such as `environment.level` in the values file. Your example notes that deployment values can be overridden by the build/deploy/publish workflow, so the workflow target is the reliable release record.

The future approved Jira step can append issues tagged with Fix Version `26.09.25`. Until that integration is added, the runbook remains grounded in the actual Helm production promotion changes and any additional notes entered in the workflow.

### Official-environment operating model

After the one-time setup below, the normal release action is only: select the UAT → PROD branch in GitHub Actions and enter the weekly release number. ReleasePilot automatically detects the changed component values files, target image tags, previous production image tags, and configuration-related release risk. It then updates one `runbooks/<release>-RUNBOOK.md` file.

One-time setup:

1. Copy `releasepilot/.github/workflows/releasepilot-runbook.example.yml` to the deployment repository as `.github/workflows/releasepilot-runbook.yml`.
2. Copy `releasepilot/scripts/compare_prod_values.py` and `releasepilot/scripts/generate_runbook.py` to the same deployment repository.
3. Confirm the values file location is `app_manifest/<component>/prd/values_prd.yaml` and the production branch is `prd`; update the workflow only if your repository differs.
4. Allow the workflow token to write runbook commits, or use an approved bot token if branch rules require one.
5. Later, configure the Jira URL, project, and token as GitHub secrets. The workflow can then query the release Fix Version and add the exact Jira scope, labels, migration, downtime, ownership, and change-ticket evidence automatically.

Manual component entry remains available for an exceptional release, but it is not the intended official release process.

## Planned live evidence sources

1. Kubernetes: workloads, images, owners, readiness, rollout status, events, and dependencies.
2. CI/CD: approved release metadata, deployment windows, release history, and last-known-good version.
3. Prometheus: service health, SLO/error-rate, latency, saturation, and post-release guardrails.
4. ClusterMind: prepared, redacted operational evidence, open risks, and recent incident context.

The future release planner should generate evidence-backed steps, require human approval for any action, and provide links to approved deployment and rollback workflows rather than running them itself.

## Connector-ready release control plane

The application code now includes the production integration endpoints. They are safe to deploy before credentials exist: each endpoint returns a clear **not configured** response until its HTTPS URL and runtime secret are supplied.

- `GET /api/discovery/jira/{release_number}` — reads the configured Jira Fix Version; it never changes Jira.
- `GET /api/runbooks/{id}/argocd` — reads application sync, health, revision, and reported images from ArgoCD.
- `GET /api/runbooks/{id}/metrics` — executes only the approved Prometheus queries configured for error rate, latency, and availability.
- `GET /api/runbooks/{id}/readiness` — produces the pre-publish checklist, open actions, and required pre/post-release evidence.
- `PUT /api/runbooks/{id}/approvals` — records the five required human confirmations.
- `GET /api/runbooks/{id}/confluence-preview` — returns native Confluence storage content for review.
- `POST /api/runbooks/{id}/publish/confluence` — creates or updates one official release page only after explicit approval and workflow-approved publishing are configured.
- `GET /api/dashboard` — returns release status, risks, publishing state, open actions, and connector readiness for a future UI/dashboard.

The application never deploys, syncs, rolls back, scales, or changes Kubernetes/ArgoCD resources. GitHub Actions and ArgoCD remain the approved action planes.

## Integration configuration

Administrators can configure safe integration metadata through:

- `GET /api/admin/integrations`
- `PUT /api/admin/integrations`

The settings include Jira URL, project key, Fix Version field, component field, optional downtime/migration fields, and the component Helm values-file pattern. Credentials are **not** accepted by the settings API or stored in the project. Configure them only as runtime environment variables:

- `RELEASEPILOT_ADMIN_TOKEN` — protects administrator configuration endpoints.

`RELEASEPILOT_WORKFLOW_TOKEN` is only needed if you choose to use the optional API-based workflow endpoint. It is not needed by the self-contained GitHub workflow described above.

The Jira settings are intentionally configuration-only at this stage. The future Jira reader will use them to query a weekly Fix Version such as `26.09.25`, but no Jira endpoint or credential is required yet.

## Runbook naming and repeat workflow runs

ReleasePilot creates runbooks only for the production promotion. The GitHub workflow saves one stable filename, `26.09.18-RUNBOOK.md`, under `runbooks/`. A second or later production workflow run for the same release number updates that same file without creating a duplicate. The local UI does the same. SIT, test, and UAT evidence remain part of the promotion process, but ReleasePilot does not create separate runbooks for them.

For UI-created runbooks, the current Markdown file is saved in this project as `runbooks/<release>-RUNBOOK.md`. It is the only user-facing runbook file for that release and is updated in place. Structured audit history is retained separately in `data/runbooks.json` and `data/runbook-history.json`; when runbooks are committed through GitHub Actions, Git also records the file history. The generated result includes a **Download** link. The UI is intentionally a manual fallback: it shows the components and images entered by the user. Automatic discovery of all changed component images happens in the GitHub workflow by comparing the selected release branch against the production baseline.

## Run locally

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8095
```

Open `http://localhost:8095`.

## API

- `GET /health` — service health
- `GET /api/status` — integration state
- `POST /api/runbooks` — create a runbook from release input
- `GET /api/runbooks` — list runbooks for the running process
# releasepilot
