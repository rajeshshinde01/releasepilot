from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import hmac
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request as UrlRequest, urlopen
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RELEASEPILOT_DATA_DIR", ROOT / "data"))
SETTINGS_FILE = DATA_DIR / "integration-settings.json"
RUNBOOKS_FILE = DATA_DIR / "runbooks.json"
RUNBOOK_HISTORY_FILE = DATA_DIR / "runbook-history.json"
# Markdown is project evidence, not transient application data. Keep it in
# the repository by default so it can be reviewed and committed with the code.
RUNBOOK_MARKDOWN_DIR = Path(os.getenv("RELEASEPILOT_RUNBOOK_DIR", ROOT / "runbooks"))
app = FastAPI(title="ReleasePilot", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def prevent_local_ui_cache(request: Request, call_next):
    """Always serve the current local UI while ReleasePilot is under active development."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class Component(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=160)
    kind: Literal["service", "worker", "database", "job", "frontend"] = "service"
    depends_on: list[str] = Field(default_factory=list)
    owner: str | None = Field(default=None, max_length=120)
    previous_version: str | None = Field(default=None, max_length=160)
    additional_instructions: str = Field(default="", max_length=2000)


class ReleaseRequest(BaseModel):
    release_number: str = Field(min_length=2, max_length=80)
    title: str = Field(default="", max_length=160)
    environment: Literal["production"] = "production"
    change_ticket: str = Field(default="", max_length=120)
    release_date: str = Field(default="", max_length=40)
    release_engineer: str = Field(default="", max_length=120)
    application_owner: str = Field(default="", max_length=120)
    components: list[Component] = Field(default_factory=list, max_length=60)
    additional_information: str = Field(default="", max_length=2000)


class ApprovalChecklist(BaseModel):
    release_engineer_confirmed: bool = False
    application_owner_confirmed: bool = False
    change_ticket_confirmed: bool = False
    rollback_confirmed: bool = False
    validation_signoff_confirmed: bool = False
    approved_by: str = Field(default="", max_length=120)
    approved_at: str = Field(default="", max_length=80)


class ReleaseApprovalRequest(BaseModel):
    release_engineer_confirmed: bool = False
    application_owner_confirmed: bool = False
    change_ticket_confirmed: bool = False
    rollback_confirmed: bool = False
    validation_signoff_confirmed: bool = False
    approved_by: str = Field(default="", max_length=120)


class ConfluencePublishRequest(BaseModel):
    approved_for_publishing: bool = False
    actor: str = Field(default="", max_length=120)


class Runbook(BaseModel):
    id: str
    created_at: str
    updated_at: str
    revision: int = 1
    filename: str
    source: str
    release: ReleaseRequest
    risk: str
    steps: list[dict]
    checks_required: list[str]
    evidence: dict = Field(default_factory=dict)
    approvals: ApprovalChecklist = Field(default_factory=ApprovalChecklist)
    publication: dict = Field(default_factory=dict)


RUNBOOKS: dict[str, Runbook] = {}
RUNBOOK_INDEX: dict[str, str] = {}
RUNBOOK_HISTORY: dict[str, list[Runbook]] = {}


class JiraSettings(BaseModel):
    base_url: str = Field(default="", max_length=500)
    project_key: str = Field(default="", max_length=80)
    fix_version_field: str = Field(default="fixVersion", max_length=120)
    component_field: str = Field(default="components", max_length=160)
    downtime_field: str = Field(default="", max_length=160)
    migration_field: str = Field(default="", max_length=160)
    auth_mode: Literal["bearer", "basic"] = "bearer"
    token_environment_variable: str = Field(default="RELEASEPILOT_JIRA_TOKEN", max_length=120)
    email_environment_variable: str = Field(default="RELEASEPILOT_JIRA_EMAIL", max_length=120)


class HelmSettings(BaseModel):
    values_file_pattern: str = "app_manifest/*/prd/values_prd.yaml"
    component_path_segment: int = 2
    image_name_path: str = "container.imageName"
    image_version_path: str = "container.imageVersion"


class GitHubSettings(BaseModel):
    workflow_endpoint: str = "/api/workflows/release-runbook"
    release_version_source: str = "GitHub workflow input"
    baseline_ref_source: str = "Currently deployed production Git ref"
    repository: str = Field(default="", max_length=240)
    production_branch: str = Field(default="prd", max_length=160)
    app_id_environment_variable: str = Field(default="GITHUB_APP_ID", max_length=120)
    installation_id_environment_variable: str = Field(default="GITHUB_APP_INSTALLATION_ID", max_length=120)
    private_key_environment_variable: str = Field(default="GITHUB_APP_PRIVATE_KEY", max_length=120)
    workflow_file: str = Field(default="releasepilot-runbook.yml", max_length=200)
    release_branch_required: bool = True


class ReleaseCalendarSettings(BaseModel):
    """A release-team maintained schedule. ReleasePilot only reads it."""
    source_type: Literal["manual-import", "approved-api"] = "manual-import"
    source_url: str = Field(default="", max_length=500)
    timezone: str = Field(default="Europe/Berlin", max_length=80)
    release_number_field: str = Field(default="release_number", max_length=120)
    release_date_field: str = Field(default="release_date", max_length=120)
    owner_field: str = Field(default="release_owner", max_length=120)


class ArgoCDSettings(BaseModel):
    base_url: str = Field(default="", max_length=500)
    application_name_pattern: str = Field(default="{component}", max_length=240)
    token_environment_variable: str = Field(default="RELEASEPILOT_ARGOCD_TOKEN", max_length=120)
    verify_healthy_synced: bool = True


class KubernetesSettings(BaseModel):
    enabled: bool = False
    namespace: str = Field(default="gss", max_length=120)
    kubeconfig_environment_variable: str = Field(default="KUBECONFIG", max_length=120)
    workload_label_selector: str = Field(default="", max_length=240)
    read_only: bool = True


class PrometheusSettings(BaseModel):
    base_url: str = Field(default="", max_length=500)
    token_environment_variable: str = Field(default="RELEASEPILOT_PROMETHEUS_TOKEN", max_length=120)
    error_rate_query: str = Field(default="", max_length=1000)
    latency_query: str = Field(default="", max_length=1000)
    availability_query: str = Field(default="", max_length=1000)
    query_timeout_seconds: int = Field(default=15, ge=2, le=60)


class ConfluenceSettings(BaseModel):
    base_url: str = Field(default="", max_length=500)
    space_key: str = Field(default="", max_length=80)
    parent_page_id: str = Field(default="", max_length=120)
    page_title_template: str = Field(default="{release_number} GDC Release Runbook", max_length=160)
    token_environment_variable: str = Field(default="RELEASEPILOT_CONFLUENCE_TOKEN", max_length=120)
    publish_mode: Literal["manual-approval", "workflow-approved"] = "manual-approval"


class IntegrationSettings(BaseModel):
    jira: JiraSettings = Field(default_factory=JiraSettings)
    helm: HelmSettings = Field(default_factory=HelmSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    release_calendar: ReleaseCalendarSettings = Field(default_factory=ReleaseCalendarSettings)
    argocd: ArgoCDSettings = Field(default_factory=ArgoCDSettings)
    confluence: ConfluenceSettings = Field(default_factory=ConfluenceSettings)
    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)
    prometheus: PrometheusSettings = Field(default_factory=PrometheusSettings)


def load_integration_settings() -> IntegrationSettings:
    if not SETTINGS_FILE.exists():
        return IntegrationSettings()
    return IntegrationSettings.model_validate_json(SETTINGS_FILE.read_text(encoding="utf-8"))


def save_integration_settings(settings: IntegrationSettings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_runbooks() -> None:
    if not RUNBOOKS_FILE.exists():
        return
    for item in json.loads(RUNBOOKS_FILE.read_text(encoding="utf-8")):
        runbook = Runbook.model_validate(item)
        RUNBOOKS[runbook.id] = runbook
        release_number = runbook.evidence.get("release_number")
        if release_number:
            RUNBOOK_INDEX[f"{release_number.lower()}::{runbook.release.environment}"] = runbook.id


def save_runbooks() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records = sorted(RUNBOOKS.values(), key=lambda item: item.updated_at, reverse=True)
    RUNBOOKS_FILE.write_text(json.dumps([record.model_dump(mode="json") for record in records], indent=2) + "\n", encoding="utf-8")


def load_runbook_history() -> None:
    if not RUNBOOK_HISTORY_FILE.exists():
        return
    for item in json.loads(RUNBOOK_HISTORY_FILE.read_text(encoding="utf-8")):
        runbook = Runbook.model_validate(item)
        RUNBOOK_HISTORY.setdefault(runbook.id, []).append(runbook)


def save_runbook_history() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records = [item.model_dump(mode="json") for items in RUNBOOK_HISTORY.values() for item in items]
    records.sort(key=lambda item: (item["created_at"], item["revision"]))
    RUNBOOK_HISTORY_FILE.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def safe_runbook_filename(release_number: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", release_number).strip(".-")
    prefix = safe_name or "RELEASE"
    return f"{prefix}-RUNBOOK.md"


def write_runbook_markdown(runbook: Runbook) -> None:
    """Keep one clear current Markdown file per release for users."""
    RUNBOOK_MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    rendered = render_markdown(runbook)
    (RUNBOOK_MARKDOWN_DIR / runbook.filename).write_text(rendered, encoding="utf-8")


def store_runbook(runbook: Runbook, release_number: str | None = None) -> Runbook:
    """Store a release runbook once; repeat workflow runs revise the same record."""
    now = datetime.now(timezone.utc).isoformat()
    if release_number:
        key = f"{release_number.lower()}::{runbook.release.environment}"
        existing_id = RUNBOOK_INDEX.get(key)
        if existing_id and (existing := RUNBOOKS.get(existing_id)):
            runbook.id = existing.id
            runbook.created_at = existing.created_at
            runbook.revision = existing.revision + 1
        RUNBOOK_INDEX[key] = runbook.id
        runbook.filename = safe_runbook_filename(release_number)
    runbook.updated_at = now
    RUNBOOKS[runbook.id] = runbook
    save_runbooks()
    RUNBOOK_HISTORY.setdefault(runbook.id, []).append(runbook.model_copy(deep=True))
    save_runbook_history()
    write_runbook_markdown(runbook)
    return runbook


def require_admin_token(token: str | None) -> None:
    expected = os.getenv("RELEASEPILOT_ADMIN_TOKEN")
    if expected and not hmac.compare_digest(token or "", expected):
        raise HTTPException(status_code=401, detail="Administrator authentication failed.")


def configured_url(value: str) -> bool:
    return bool(value and value.startswith("https://"))


def external_json(url: str, *, token: str = "", method: str = "GET", payload: dict | None = None, timeout: int = 20) -> dict:
    """Minimal, redacted HTTP client used only by configured read/publish connectors."""
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = UrlRequest(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: destination is administrator-configured HTTPS.
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as error:
        raise HTTPException(status_code=502, detail=f"External service returned HTTP {error.code}.") from error
    except (URLError, TimeoutError) as error:
        raise HTTPException(status_code=502, detail="Could not reach the configured external service.") from error


def connector_state(settings: IntegrationSettings) -> dict[str, str]:
    return {
        "jira": "ready" if configured_url(settings.jira.base_url) and bool(os.getenv(settings.jira.token_environment_variable)) else "not configured",
        "github": "ready" if settings.github.repository and bool(os.getenv(settings.github.app_id_environment_variable)) else "not configured",
        "argocd": "ready" if configured_url(settings.argocd.base_url) and bool(os.getenv(settings.argocd.token_environment_variable)) else "not configured",
        "kubernetes": "ready" if settings.kubernetes.enabled else "not configured",
        "prometheus": "ready" if configured_url(settings.prometheus.base_url) else "not configured",
        "confluence": "ready" if configured_url(settings.confluence.base_url) and settings.confluence.space_key and settings.confluence.parent_page_id and bool(os.getenv(settings.confluence.token_environment_variable)) else "not configured",
        "release_calendar": "ready" if configured_url(settings.release_calendar.source_url) else "not configured",
    }


def runbook_open_actions(runbook: Runbook) -> list[str]:
    actions: list[str] = []
    release = runbook.release
    if not release.components:
        actions.append("Discover the production release scope before approval.")
    if not release.change_ticket.strip():
        actions.append("Add the approved change ticket.")
    if not release.release_engineer.strip():
        actions.append("Confirm the release engineer.")
    if not release.application_owner.strip():
        actions.append("Confirm the application IT owner.")
    for component in release.components:
        if not component.owner:
            actions.append(f"Confirm the owner for {component.name}.")
        if not component.previous_version:
            actions.append(f"Obtain the previous successful image for {component.name} from ArgoCD.")
    approval = runbook.approvals
    labels = {
        "release_engineer_confirmed": "release engineer confirmation",
        "application_owner_confirmed": "application owner confirmation",
        "change_ticket_confirmed": "change-ticket confirmation",
        "rollback_confirmed": "rollback confirmation",
        "validation_signoff_confirmed": "validation sign-off",
    }
    for name, label in labels.items():
        if not getattr(approval, name):
            actions.append(f"Record {label}.")
    return actions


def jira_release_scope(release_number: str, settings: IntegrationSettings) -> list[dict]:
    if not configured_url(settings.jira.base_url):
        raise HTTPException(status_code=409, detail="Jira is not configured. Add the HTTPS URL and deploy the Jira token secret first.")
    token = os.getenv(settings.jira.token_environment_variable, "")
    if not token:
        raise HTTPException(status_code=409, detail="Jira is configured but its runtime token secret is unavailable.")
    jql = f'project = "{settings.jira.project_key}" AND fixVersion = "{release_number}" ORDER BY key'
    query = urlencode({"jql": jql, "fields": f"summary,issuetype,{settings.jira.component_field},{settings.jira.downtime_field},{settings.jira.migration_field}"})
    response = external_json(f"{settings.jira.base_url.rstrip('/')}/rest/api/2/search?{query}", token=token)
    issues = []
    for item in response.get("issues", []):
        fields = item.get("fields", {})
        components = [entry.get("name", "") for entry in fields.get(settings.jira.component_field, []) if entry.get("name")]
        issue_type = str(fields.get("issuetype", {}).get("name", "other")).lower()
        issues.append({"key": item.get("key", ""), "summary": fields.get("summary", ""), "issue_type": issue_type, "components": components})
    return issues


class JiraIssue(BaseModel):
    key: str = Field(min_length=2, max_length=60)
    summary: str = Field(min_length=2, max_length=500)
    issue_type: Literal["feature", "bug", "migration", "maintenance", "other"] = "other"
    components: list[str] = Field(default_factory=list)
    requires_downtime: bool = False
    requires_data_migration: bool = False


class HelmChange(BaseModel):
    component: str = Field(min_length=2, max_length=120)
    chart: str | None = Field(default=None, max_length=160)
    current_image: str | None = Field(default=None, max_length=240)
    target_image: str | None = Field(default=None, max_length=240)
    replica_change: str | None = Field(default=None, max_length=80)
    resource_change: bool = False
    config_change: bool = False
    secret_reference_changed: bool = False


class AutomatedReleaseRequest(BaseModel):
    release_number: str = Field(min_length=2, max_length=80)
    environment: Literal["production"] = "production"
    repository: str = Field(min_length=3, max_length=240)
    git_ref: str = Field(min_length=2, max_length=240)
    helm_paths: list[str] = Field(min_length=1, max_length=80)
    jira_issues: list[JiraIssue] = Field(default_factory=list, max_length=500)
    helm_changes: list[HelmChange] = Field(min_length=1, max_length=200)


def verified_workflow_request(provided_token: str | None) -> None:
    expected_token = os.getenv("RELEASEPILOT_WORKFLOW_TOKEN")
    if expected_token and not hmac.compare_digest(provided_token or "", expected_token):
        raise HTTPException(status_code=401, detail="Workflow authentication failed.")


def build_runbook(request: ReleaseRequest) -> Runbook:
    release_title = request.title.strip() or f"{request.release_number} GDC Release Runbook"
    request = request.model_copy(update={"title": release_title})
    names = {item.name.lower() for item in request.components}
    component_steps = []
    risk_points = 0
    for item in request.components:
        missing_dependencies = [dep for dep in item.depends_on if dep.lower() not in names]
        if item.kind in {"database", "job"}:
            risk_points += 2
        if missing_dependencies:
            risk_points += 1
        component_steps.append({
            "component": item.name,
            "version": item.version,
            "owner": item.owner or "Unassigned — confirm before release",
            "previous_version": item.previous_version or "Confirm in ArgoCD History before rollout",
            "kind": item.kind,
            "dependencies": item.depends_on or ["No dependency declared"],
            "missing_dependencies": missing_dependencies,
        })

    component_instructions = [item for item in request.components if item.additional_instructions.strip()]
    risk = "high" if risk_points >= 4 else "medium" if risk_points >= 1 or len(request.components) > 3 else "standard"
    now = datetime.now(timezone.utc).isoformat()
    runbook = Runbook(
        id=str(uuid4()),
        created_at=now,
        updated_at=now,
        filename="MANUAL-RUNBOOK.md",
        source="Release input only — no live integrations connected",
        release=request,
        risk=risk,
        checks_required=[
            "Confirm release window, change approval, and component owners.",
            "Verify current workload health, alert state, and dependency availability.",
            "Capture the running image/version and baseline error and restart rates.",
            *(["Review the component-specific instructions recorded in this release scope."] if component_instructions else []),
            "Roll out one component at a time; pause if readiness, errors, or latency regress.",
            "Validate user-critical paths and monitor for the agreed observation period.",
            "Use the approved rollback procedure if a release guardrail fails.",
        ],
        evidence={
            "release_number": request.release_number,
            "additional_information": request.additional_information,
        },
        steps=[
            {"phase": "Prepare", "items": [
                "Review the change summary and confirm the scope is complete.",
                "Confirm backup or recovery requirements for stateful components.",
                "Record the currently running version for every component.",
            ]},
            {"phase": "Release", "items": [
                "Deploy in the approved order, respecting declared dependencies.",
                "Wait for readiness and available replicas before continuing.",
                "Stop and investigate if warning events, restart growth, or new error signatures appear.",
            ]},
            {"phase": "Validate", "items": [
                "Compare post-release health and error signals against the pre-release baseline.",
                "Confirm each dependency is reachable and expected user paths work.",
                "Record the outcome, final versions, and any follow-up work.",
            ]},
            {"phase": "Rollback", "items": [
                "Use the last known-good version recorded before the release.",
                "Restore state only through the approved backup/recovery runbook.",
                "Notify the release owner and retain evidence for incident review.",
            ]},
        ],
    )
    return runbook


def automated_runbook(request: AutomatedReleaseRequest) -> Runbook:
    jira_components = {component.lower(): [] for issue in request.jira_issues for component in issue.components}
    for issue in request.jira_issues:
        for component in issue.components:
            jira_components.setdefault(component.lower(), []).append(issue.key)

    component_specs: list[Component] = []
    unmapped_helm: list[str] = []
    change_flags: list[str] = []
    risk_points = 0
    for change in request.helm_changes:
        tickets = jira_components.get(change.component.lower(), [])
        if not tickets:
            unmapped_helm.append(change.component)
            risk_points += 1
        if change.current_image != change.target_image:
            change_flags.append(f"{change.component}: image {change.current_image or 'unknown'} → {change.target_image or 'unknown'}")
        if change.replica_change:
            change_flags.append(f"{change.component}: replicas {change.replica_change}")
            risk_points += 1
        if change.resource_change:
            change_flags.append(f"{change.component}: CPU or memory settings changed")
            risk_points += 1
        if change.config_change:
            change_flags.append(f"{change.component}: configuration reference changed")
            risk_points += 1
        if change.secret_reference_changed:
            change_flags.append(f"{change.component}: secret reference changed (value excluded)")
            risk_points += 1
        component_specs.append(Component(
            name=change.component,
            version=change.target_image or "Version not found in Helm comparison",
            previous_version=change.current_image,
            kind="database" if any(issue.requires_data_migration and change.component.lower() in {c.lower() for c in issue.components} for issue in request.jira_issues) else "service",
            owner=None,
        ))

    migration_issues = [issue.key for issue in request.jira_issues if issue.requires_data_migration or issue.issue_type == "migration"]
    downtime_issues = [issue.key for issue in request.jira_issues if issue.requires_downtime]
    helm_components = {change.component.lower() for change in request.helm_changes}
    jira_only_components = sorted(component for component in jira_components if component not in helm_components)
    risk_points += len(jira_only_components)
    risk_points += (2 * len(migration_issues)) + (2 * len(downtime_issues))
    risk = "high" if risk_points >= 4 else "medium" if risk_points else "standard"
    release = ReleaseRequest(
        release_number=request.release_number,
        title=f"{request.release_number} · {request.environment} release",
        environment=request.environment,
        components=component_specs,
        additional_information=f"Generated from {len(request.jira_issues)} Jira issue(s) and {len(request.helm_changes)} Helm component change(s).",
    )
    runbook = build_runbook(release)
    runbook.risk = risk
    runbook.source = "GitHub workflow manifest + Jira scope + Helm comparison (secret values excluded)"
    runbook.evidence = {
        "release_number": request.release_number,
        "repository": request.repository,
        "git_ref": request.git_ref,
        "helm_paths": request.helm_paths,
        "jira_issue_keys": [issue.key for issue in request.jira_issues],
        "change_flags": change_flags,
        "unmapped_helm_components": unmapped_helm,
        "jira_only_components": jira_only_components,
        "migration_issues": migration_issues,
        "downtime_issues": downtime_issues,
    }
    prepare = runbook.steps[0]["items"]
    if unmapped_helm:
        prepare.insert(0, f"Pause planning until Helm components are mapped to release scope: {', '.join(unmapped_helm)}.")
    if jira_only_components:
        prepare.insert(0, f"Confirm whether Jira-only component(s) have a deployable production change: {', '.join(jira_only_components)}.")
    if migration_issues:
        prepare.insert(0, f"Confirm a tested backup and approved migration plan for Jira: {', '.join(migration_issues)}.")
    if downtime_issues:
        prepare.insert(0, f"Confirm the approved maintenance window and customer communication for Jira: {', '.join(downtime_issues)}.")
    return runbook


def render_markdown(runbook: Runbook) -> str:
    evidence = runbook.evidence
    release = runbook.release
    def cell(value: str | None, fallback: str = "Confirm before release") -> str:
        return (value or fallback).replace("|", "\\|").replace("\n", " ")

    lines = [
        f"# {runbook.filename.removesuffix('.md')} — {release.title}",
        "",
        "## 1. Release information",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Release version | {cell(evidence.get('release_number'))} |",
        "| Environment | Production |",
        f"| Change ticket | {cell(release.change_ticket)} |",
        f"| Release date | {cell(release.release_date)} |",
        f"| Release engineer | {cell(release.release_engineer)} |",
        f"| Application IT owner | {cell(release.application_owner)} |",
        "| Deployment method | GitOps (GitHub + ArgoCD) |",
        "| Rollback method | ArgoCD revision rollback |",
        f"| Risk | {runbook.risk.title()} |",
        f"| Runbook revision | {runbook.revision} |",
        f"| Last updated | {runbook.updated_at} |",
    ]
    if evidence:
        lines.extend(["", "### Release evidence", f"- **Source:** {runbook.source}", f"- **Repository/ref:** {evidence.get('repository', 'Not supplied')} @ {evidence.get('git_ref', 'Not supplied')}"])
        for flag in evidence.get("change_flags", []):
            lines.append(f"- {flag}")
        if evidence.get("additional_information"):
            lines.extend(["", "### Additional information", str(evidence["additional_information"])])
    lines.extend(["", "## 2. Release scope", "", "### 2.1 Components included", "", "| Component | Target image/version | Previous stable version | Owner |", "| --- | --- | --- | --- |"])
    for component in release.components:
        lines.append(f"| {cell(component.name)} | {cell(component.version)} | {cell(component.previous_version, 'Confirm in ArgoCD History')} | {cell(component.owner, 'Confirm before release')} |")
    if not release.components:
        lines.append("| No components declared | Confirm release scope before continuing | — | — |")
    instructions = [component for component in release.components if component.additional_instructions.strip()]
    previous_version_rows = [f"| {cell(component.name)} | {cell(component.previous_version, 'Confirm in ArgoCD History')} |" for component in release.components] or ["| Release scope not yet discovered | Confirm in ArgoCD History |"]
    if instructions:
        lines.extend(["", "### 2.2 Component-specific instructions"])
        for component in instructions:
            lines.extend(["", f"#### {component.name}", component.additional_instructions.strip()])
    lines.extend(["", "## 3. Release readiness checks"] + [f"- [ ] {check}" for check in runbook.checks_required])
    phase_names = {"Prepare": "4.1 Pre-deployment", "Release": "4.2 Promote and deploy", "Validate": "4.4 Deployment verification", "Rollback": "5. Rollback plan"}
    for phase in runbook.steps:
        heading = phase_names.get(phase["phase"], phase["phase"])
        lines.extend(["", f"## {heading}"])
        if phase["phase"] != "Rollback":
            lines.extend([f"- [ ] {item}" for item in phase["items"]])
        if phase["phase"] == "Release":
            lines.extend(["", "### 4.3 ArgoCD synchronization", "- [ ] Sync only the applications listed in the release scope.", "- [ ] Confirm each application is **Healthy** and **Synced** before proceeding."])
        if phase["phase"] == "Validate":
            lines.extend(["", "- [ ] Confirm pods are running with no CrashLoopBackOff or failed containers.", "- [ ] Confirm the deployed image matches the target version recorded above."])
        if phase["phase"] == "Rollback":
            lines.extend(["", "### 5.1 Rollback criteria", "- Critical functionality is unavailable, the application is unhealthy, errors grow materially, or the business owner requests rollback.", "", "### 5.2 Rollback procedure", "- [ ] In ArgoCD, select the previous stable revision from **History and Rollback**.", "- [ ] Redeploy and synchronize the selected revision.", "- [ ] Confirm every affected application is **Healthy** and **Synced**.", *[f"- [ ] {item}" for item in phase["items"]], "", "### 5.3 Previous successful deployed versions", "", "| Component | Previous stable version |", "| --- | --- |", *previous_version_rows])
    lines.extend(["", "_This runbook is advisory and read-only. Deployment and rollback actions remain in approved GitHub workflows._"])
    return "\n".join(lines)


def render_html(runbook: Runbook) -> str:
    """Present the Markdown source as a readable release document in the browser."""
    release = runbook.release
    evidence = runbook.evidence

    def text(value: object | None, fallback: str = "Confirm before release") -> str:
        return escape(str(value or fallback))

    information = [
        ("Release version", evidence.get("release_number")),
        ("Environment", "Production"),
        ("Change ticket", release.change_ticket),
        ("Release date", release.release_date),
        ("Release engineer", release.release_engineer),
        ("Application IT owner", release.application_owner),
        ("Deployment method", "GitOps (GitHub + ArgoCD)"),
        ("Rollback method", "ArgoCD revision rollback"),
        ("Risk", runbook.risk.title()),
        ("Runbook revision", str(runbook.revision)),
        ("Last updated", runbook.updated_at),
    ]
    information_rows = "".join(f"<tr><th>{text(label)}</th><td>{text(value)}</td></tr>" for label, value in information)
    component_rows = "".join(
        f"<tr><td><strong>{text(component.name)}</strong></td><td><code>{text(component.version)}</code></td><td><code>{text(component.previous_version, 'Confirm in ArgoCD History')}</code></td><td>{text(component.owner)}</td></tr>"
        for component in release.components
    ) or "<tr><td colspan=\"4\">Release scope has not been discovered yet. Run the approved GitHub workflow to compare the release branch with <strong>prd</strong>.</td></tr>"
    instructions = "".join(
        f"<article class=\"instruction\"><h4>{text(component.name)}</h4><p>{text(component.additional_instructions)}</p></article>"
        for component in release.components if component.additional_instructions.strip()
    )
    checks = "".join(f"<li>{text(item)}</li>" for item in runbook.checks_required)
    phase_html = "".join(
        f"<section><h3>{text({'Prepare': '4.1 Pre-deployment', 'Release': '4.2 Promote and deploy', 'Validate': '4.4 Deployment verification'}.get(phase['phase'], phase['phase']))}</h3><ul>{''.join(f'<li>{text(item)}</li>' for item in phase['items'])}</ul></section>"
        for phase in runbook.steps if phase["phase"] != "Rollback"
    )
    previous_rows = "".join(
        f"<tr><td>{text(component.name)}</td><td><code>{text(component.previous_version, 'Confirm in ArgoCD History')}</code></td></tr>"
        for component in release.components
    ) or "<tr><td>Release scope not yet discovered</td><td>Confirm in ArgoCD History</td></tr>"
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{text(runbook.filename)} · ReleasePilot</title>
<style>
body{{margin:0;background:#f4f7f5;color:#182c26;font:15px/1.55 Arial,sans-serif}}main{{max-width:960px;margin:42px auto;padding:42px;background:white;box-shadow:0 4px 22px #17322918}}h1{{font-size:30px;margin:0 0 5px}}h2{{font-size:23px;margin:40px 0 14px;padding-bottom:9px;border-bottom:2px solid #276653}}h3{{font-size:18px;margin:29px 0 9px}}h4{{margin:0 0 5px}}p{{margin:7px 0}}.meta{{color:#597066;font-size:13px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #cddbd4;padding:10px 12px;text-align:left;vertical-align:top}}thead th{{background:#eaf3ee}}tbody th{{width:32%;background:#f7faf8}}code{{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:#174936;overflow-wrap:anywhere}}ul{{padding-left:22px}}li{{margin:6px 0}}.callout{{padding:14px 16px;background:#f5f9f6;border-left:4px solid #276653}}.instruction{{border:1px solid #cddbd4;border-radius:5px;padding:14px;margin:10px 0}}.actions{{margin:26px 0 0;display:flex;gap:10px;flex-wrap:wrap}}a{{color:#135a43;font-weight:bold}}@media(max-width:700px){{main{{margin:0;padding:22px}}h1{{font-size:25px}}table{{font-size:13px}}th,td{{padding:8px}}}}
</style></head><body><main>
<p class=\"meta\">RELEASEPILOT · PRODUCTION RUNBOOK</p><h1>{text(release.title)}</h1><p class=\"meta\">{text(runbook.filename)} · revision {runbook.revision}</p>
<section class=\"actions\"><a href=\"/api/runbooks/{escape(runbook.id)}/markdown\">Download Markdown source</a></section>
<h2>1. Release information</h2><table><tbody>{information_rows}</tbody></table>
<h2>2. Release scope</h2><h3>2.1 Components included</h3><table><thead><tr><th>Component</th><th>Target image tag</th><th>Previous successful image tag</th><th>Owner</th></tr></thead><tbody>{component_rows}</tbody></table>
{f'<h3>2.2 Component instructions</h3>{instructions}' if instructions else ''}
<h2>3. Release readiness checks</h2><ul>{checks}</ul>
<h2>4. Deployment procedure</h2><section><h3>4.1 Change ticket update</h3><ul><li>Update the approved change record with the release version, scope, approvals, and evidence links.</li></ul></section>{phase_html}<section><h3>4.3 ArgoCD synchronization</h3><ul><li>Sync only applications listed in the release scope.</li><li>Confirm every application is <strong>Healthy</strong> and <strong>Synced</strong>.</li></ul></section>
<h2>5. Rollback plan</h2><h3>5.1 Rollback criteria</h3><p>Roll back if critical functionality is unavailable, the application is unhealthy, errors grow materially, or the business owner requests it.</p><h3>5.2 Rollback procedure</h3><ul><li>In ArgoCD, select the previous stable revision from <strong>History and Rollback</strong>.</li><li>Redeploy and synchronize the selected revision.</li><li>Confirm every affected application is <strong>Healthy</strong> and <strong>Synced</strong>.</li></ul><h3>5.3 Previous successful deployed versions</h3><table><thead><tr><th>Component</th><th>Previous stable version</th></tr></thead><tbody>{previous_rows}</tbody></table>
<p class=\"callout\"><strong>Read-only advisory:</strong> ReleasePilot does not deploy or roll back workloads. All actions remain within approved GitHub and ArgoCD controls.</p>
</main></body></html>"""


def render_confluence_storage(runbook: Runbook) -> str:
    """Native Confluence storage format; secrets and raw log data are intentionally excluded."""
    release = runbook.release
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(str(value or 'Confirm before release'))}</td></tr>"
        for label, value in [
            ("Release version", release.release_number), ("Environment", "Production"),
            ("Change ticket", release.change_ticket), ("Release date", release.release_date),
            ("Release engineer", release.release_engineer), ("Application IT owner", release.application_owner),
            ("Deployment method", "GitOps (GitHub + ArgoCD)"), ("Rollback method", "ArgoCD revision rollback"),
            ("Risk", runbook.risk.title()),
        ]
    )
    components = "".join(
        f"<tr><td>{escape(component.name)}</td><td><code>{escape(component.version)}</code></td><td><code>{escape(component.previous_version or 'Confirm in ArgoCD History')}</code></td><td>{escape(component.owner or 'Confirm before release')}</td></tr>"
        for component in release.components
    ) or "<tr><td colspan='4'>Release scope is not discovered yet.</td></tr>"
    checks = "".join(f"<li>{escape(check)}</li>" for check in runbook.checks_required)
    prior = "".join(
        f"<tr><td>{escape(component.name)}</td><td><code>{escape(component.previous_version or 'Confirm in ArgoCD History')}</code></td></tr>"
        for component in release.components
    ) or "<tr><td>Release scope not discovered</td><td>Confirm in ArgoCD History</td></tr>"
    return f"""<h1>{escape(release.title)}</h1>
<h2>1. Release information</h2><table><tbody>{rows}</tbody></table>
<h2>2. Release scope</h2><h3>2.1 Components included</h3><table><tbody><tr><th>Component</th><th>Target image tag</th><th>Previous successful image tag</th><th>Owner</th></tr>{components}</tbody></table>
<h2>3. Release readiness checks</h2><ul>{checks}</ul>
<h2>4. Deployment procedure</h2><h3>4.1 Change ticket update</h3><p>Update the approved change record with the release version, scope, approvals, and evidence links.</p><h3>4.2 Promote UAT to PROD</h3><p>Use the approved GitHub pull request and deployment workflow only.</p><h3>4.3 ArgoCD synchronization</h3><p>Sync only applications in the release scope and confirm Healthy and Synced status.</p><h3>4.4 Deployment verification</h3><p>Verify target images, workload readiness, warning events, and agreed service checks.</p>
<h2>5. Rollback plan</h2><h3>5.1 Rollback criteria</h3><p>Critical functionality unavailable, unhealthy application, material error growth, or business owner request.</p><h3>5.2 Rollback procedure</h3><p>Use ArgoCD History and Rollback to select the approved previous stable revision, then confirm Healthy and Synced status.</p><h3>5.3 Previous successful deployed versions</h3><table><tbody><tr><th>Component</th><th>Previous stable version</th></tr>{prior}</tbody></table>
<p><em>Generated by ReleasePilot. Deployment and rollback remain approved GitHub and ArgoCD actions.</em></p>"""


@app.get("/health")
def health():
    return {"status": "ok", "service": "releasepilot"}


@app.on_event("startup")
def restore_runbook_history() -> None:
    load_runbooks()
    load_runbook_history()
    # Keep the project Markdown files consistent when the runbook template evolves.
    for runbook in RUNBOOKS.values():
        write_runbook_markdown(runbook)


@app.get("/api/status")
def status():
    settings = load_integration_settings()
    states = connector_state(settings)
    return {
        "service": "ReleasePilot",
        "mode": "planning",
        "integrations": {
            "release_calendar": states["release_calendar"] if states["release_calendar"] == "ready" else "not configured — release team calendar remains the authoritative source",
            "github": states["github"] if states["github"] == "ready" else "workflow template available — repository or GitHub App not configured",
            "kubernetes": states["kubernetes"],
            "ci_cd": "GitHub workflow manifest supported",
            "prometheus": states["prometheus"],
            "clustermind": "not configured",
            "jira": states["jira"],
            "argocd": states["argocd"],
            "confluence": states["confluence"],
        },
        "message": "Runbooks are generated from release input until live sources are configured.",
    }


@app.get("/api/dashboard")
def dashboard():
    settings = load_integration_settings()
    records = sorted(RUNBOOKS.values(), key=lambda item: item.updated_at, reverse=True)
    return {
        "connector_state": connector_state(settings),
        "runbooks": [
            {
                "id": item.id,
                "release_number": item.release.release_number,
                "title": item.release.title,
                "release_date": item.release.release_date,
                "risk": item.risk,
                "revision": item.revision,
                "published": bool(item.publication.get("url")),
                "open_actions": runbook_open_actions(item),
            }
            for item in records
        ],
        "message": "The release calendar remains read-only and authoritative. Deployment systems are never changed by this dashboard.",
    }


@app.get("/api/discovery/jira/{release_number}")
def discover_jira_release(release_number: str):
    """Read the configured Fix Version scope. It does not create or change a Jira issue."""
    settings = load_integration_settings()
    issues = jira_release_scope(release_number, settings)
    return {"release_number": release_number, "source": "Jira Fix Version", "issues": issues}


@app.get("/api/runbooks/{runbook_id}/readiness")
def runbook_readiness(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    settings = load_integration_settings()
    actions = runbook_open_actions(runbook)
    return {
        "runbook_id": runbook.id,
        "ready_to_publish": not actions,
        "risk": runbook.risk,
        "open_actions": actions,
        "connectors": connector_state(settings),
        "checks": {
            "pre_release": ["readiness and rollout state", "pod/container restart count", "warning events", "Prometheus error rate, latency, availability"],
            "post_release": ["target image verification", "Healthy and Synced ArgoCD status", "baseline comparison", "business-path validation"],
        },
    }


@app.put("/api/runbooks/{runbook_id}/approvals", response_model=Runbook)
def update_runbook_approvals(runbook_id: str, request: ReleaseApprovalRequest):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    approval = ApprovalChecklist(**request.model_dump())
    if all([
        approval.release_engineer_confirmed, approval.application_owner_confirmed,
        approval.change_ticket_confirmed, approval.rollback_confirmed, approval.validation_signoff_confirmed,
    ]):
        approval.approved_at = datetime.now(timezone.utc).isoformat()
    runbook.approvals = approval
    return store_runbook(runbook, runbook.release.release_number)


@app.get("/api/runbooks/{runbook_id}/argocd")
def argocd_evidence(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    settings = load_integration_settings()
    if not configured_url(settings.argocd.base_url):
        raise HTTPException(status_code=409, detail="ArgoCD is not configured. Add its HTTPS URL and runtime token secret during deployment.")
    token = os.getenv(settings.argocd.token_environment_variable, "")
    if not token:
        raise HTTPException(status_code=409, detail="ArgoCD is configured but its runtime token secret is unavailable.")
    applications = []
    for component in runbook.release.components:
        application = settings.argocd.application_name_pattern.format(component=component.name)
        body = external_json(f"{settings.argocd.base_url.rstrip('/')}/api/v1/applications/{quote(application, safe='')}", token=token)
        status = body.get("status", {})
        applications.append({
            "component": component.name,
            "application": application,
            "sync": status.get("sync", {}).get("status", "Unknown"),
            "health": status.get("health", {}).get("status", "Unknown"),
            "revision": status.get("sync", {}).get("revision", "Unknown"),
            "images": status.get("summary", {}).get("images", []),
        })
    return {"source": "ArgoCD read-only application API", "applications": applications}


@app.get("/api/runbooks/{runbook_id}/metrics")
def prometheus_evidence(runbook_id: str):
    if runbook_id not in RUNBOOKS:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    settings = load_integration_settings()
    if not configured_url(settings.prometheus.base_url):
        raise HTTPException(status_code=409, detail="Prometheus is not configured. Add its HTTPS URL and approved queries during deployment.")
    token = os.getenv(settings.prometheus.token_environment_variable, "")
    queries = {"error_rate": settings.prometheus.error_rate_query, "latency": settings.prometheus.latency_query, "availability": settings.prometheus.availability_query}
    results = {}
    for name, query in queries.items():
        if query:
            results[name] = external_json(f"{settings.prometheus.base_url.rstrip('/')}/api/v1/query?{urlencode({'query': query})}", token=token, timeout=settings.prometheus.query_timeout_seconds)
    return {"source": "Prometheus read-only query API", "metrics": results, "missing_queries": [name for name, query in queries.items() if not query]}


@app.get("/api/admin/integrations", response_model=IntegrationSettings)
def get_integration_settings(x_releasepilot_admin_token: str | None = Header(default=None)):
    require_admin_token(x_releasepilot_admin_token)
    return load_integration_settings()


@app.put("/api/admin/integrations", response_model=IntegrationSettings)
def update_integration_settings(
    settings: IntegrationSettings,
    x_releasepilot_admin_token: str | None = Header(default=None),
):
    require_admin_token(x_releasepilot_admin_token)
    if settings.jira.base_url and not settings.jira.base_url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Jira URL must use HTTPS.")
    save_integration_settings(settings)
    return settings


@app.post("/api/runbooks", response_model=Runbook)
def create_runbook(request: ReleaseRequest):
    if not request.release_date.strip():
        raise HTTPException(status_code=422, detail="Release date is required for every production runbook.")
    duplicate_names = [item.name for item in request.components if sum(x.name.lower() == item.name.lower() for x in request.components) > 1]
    if duplicate_names:
        raise HTTPException(status_code=422, detail=f"Each component may appear once: {', '.join(sorted(set(duplicate_names)))}")
    runbook = build_runbook(request)
    return store_runbook(runbook, request.release_number)


@app.post("/api/workflows/release-runbook", response_model=Runbook)
def create_automated_runbook(
    request: AutomatedReleaseRequest,
    x_releasepilot_token: str | None = Header(default=None),
):
    """Accept a signed, pre-redacted manifest generated by an approved GitHub workflow."""
    verified_workflow_request(x_releasepilot_token)
    runbook = automated_runbook(request)
    return store_runbook(runbook, request.release_number)


@app.get("/api/runbooks/{runbook_id}/markdown", response_class=PlainTextResponse)
def runbook_markdown(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    return PlainTextResponse(
        render_markdown(runbook),
        headers={"Content-Disposition": f'attachment; filename="{runbook.filename}"'},
    )


@app.get("/api/runbooks/{runbook_id}/view", response_class=HTMLResponse)
def runbook_view(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    return HTMLResponse(render_html(runbook))


@app.get("/api/runbooks/{runbook_id}/document")
def runbook_document(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    return {"filename": runbook.filename, "content": render_markdown(runbook)}


@app.get("/api/runbooks/{runbook_id}/confluence-preview")
def confluence_preview(runbook_id: str):
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    return {"title": runbook.release.title, "storage_format": render_confluence_storage(runbook)}


@app.post("/api/runbooks/{runbook_id}/publish/confluence")
def publish_to_confluence(runbook_id: str, request: ConfluencePublishRequest):
    """Create or update the one official Confluence page for this production release."""
    runbook = RUNBOOKS.get(runbook_id)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    if not request.approved_for_publishing:
        raise HTTPException(status_code=422, detail="Explicit approval is required before publishing to Confluence.")
    settings = load_integration_settings()
    if connector_state(settings)["confluence"] != "ready":
        raise HTTPException(status_code=409, detail="Confluence is not configured. Add the base URL, space, parent page ID, and runtime token secret during deployment.")
    if settings.confluence.publish_mode != "workflow-approved":
        raise HTTPException(status_code=409, detail="Confluence publishing is configured for manual approval. Set the approved workflow mode only after governance review.")
    token = os.getenv(settings.confluence.token_environment_variable, "")
    title = settings.confluence.page_title_template.format(release_number=runbook.release.release_number)
    root = settings.confluence.base_url.rstrip("/")
    lookup = external_json(f"{root}/rest/api/content?{urlencode({'spaceKey': settings.confluence.space_key, 'title': title, 'expand': 'version'})}", token=token)
    storage = render_confluence_storage(runbook)
    results = lookup.get("results", [])
    if results:
        page = results[0]
        page_id = page["id"]
        payload = {"id": page_id, "type": "page", "title": title, "space": {"key": settings.confluence.space_key}, "body": {"storage": {"value": storage, "representation": "storage"}}, "version": {"number": int(page.get("version", {}).get("number", 0)) + 1}}
        published = external_json(f"{root}/rest/api/content/{page_id}", token=token, method="PUT", payload=payload)
    else:
        payload = {"type": "page", "title": title, "space": {"key": settings.confluence.space_key}, "ancestors": [{"id": settings.confluence.parent_page_id}], "body": {"storage": {"value": storage, "representation": "storage"}}}
        published = external_json(f"{root}/rest/api/content", token=token, method="POST", payload=payload)
    links = published.get("_links", {})
    runbook.publication = {"provider": "Confluence", "page_id": published.get("id", ""), "url": f"{root}{links.get('webui', '')}", "published_at": datetime.now(timezone.utc).isoformat(), "published_by": request.actor or "approved workflow"}
    store_runbook(runbook, runbook.release.release_number)
    return {"published": True, **runbook.publication}


@app.get("/api/runbooks/{runbook_id}/history")
def runbook_history(runbook_id: str):
    if runbook_id not in RUNBOOKS:
        raise HTTPException(status_code=404, detail="Runbook not found.")
    return sorted(RUNBOOK_HISTORY.get(runbook_id, []), key=lambda item: item.revision, reverse=True)


@app.get("/api/runbooks/{runbook_id}/history/{revision}/markdown", response_class=PlainTextResponse)
def runbook_history_markdown(runbook_id: str, revision: int):
    record = next((item for item in RUNBOOK_HISTORY.get(runbook_id, []) if item.revision == revision), None)
    if not record:
        raise HTTPException(status_code=404, detail="Runbook revision not found.")
    historical_name = f"{Path(record.filename).stem}-revision-{record.revision:03d}.md"
    return PlainTextResponse(
        render_markdown(record),
        headers={"Content-Disposition": f'attachment; filename="{historical_name}"'},
    )


@app.get("/api/runbooks")
def list_runbooks():
    return list(RUNBOOKS.values())


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")
