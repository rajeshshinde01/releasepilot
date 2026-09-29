#!/usr/bin/env python3
"""Create a redacted ReleasePilot manifest from a UAT → Prod Helm values diff.

The script records only safe operational metadata: changed image tags, replica
settings, resources, and configuration/secret *references*. It never writes a
ConfigMap or Secret value to the output.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


def load_values(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object at its root")
    return data


def leaves(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, str(key)))
    elif isinstance(value, list):
        # Lists are represented as a single field because list item indexes are
        # unstable between values files and would make release evidence noisy.
        yield path, value
    else:
        yield path, value


def component_for(path: tuple[str, ...]) -> str:
    lower = [part.lower() for part in path]
    for marker in ("image", "replicacount", "resources", "config", "env", "secret"):
        if marker in lower:
            index = lower.index(marker)
            if index:
                return path[index - 1]
    return path[0] if path else "root"


def image_reference(values: dict[str, Any]) -> str | None:
    """Return the safe image identity used by the component deployment values."""
    container = values.get("container")
    if not isinstance(container, dict):
        return None
    name = container.get("imageName")
    version = container.get("imageVersion")
    if name and version:
        return f"{name}:{version}"
    return str(version or name) if version or name else None


def diff_values(current: dict[str, Any], candidate: dict[str, Any], component_name: str | None = None) -> list[dict[str, Any]]:
    before = dict(leaves(current))
    after = dict(leaves(candidate))
    changes: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "component": "",
        "current_image": None,
        "target_image": None,
        "replica_change": None,
        "resource_change": False,
        "config_change": False,
        "secret_reference_changed": False,
    })
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        lower_path = ".".join(part.lower() for part in path)
        component = component_name or component_for(path)
        item = changes[component]
        item["component"] = component
        if ".secret" in lower_path or "secret" in lower_path:
            item["secret_reference_changed"] = True
            continue
        if "replicacount" in lower_path or lower_path.endswith(".replicas"):
            item["replica_change"] = f"{old if old is not None else 'unset'} → {new if new is not None else 'unset'}"
        elif ".resources." in lower_path or lower_path.endswith(".resources"):
            item["resource_change"] = True
        elif lower_path.endswith(".imageversion") or lower_path.endswith(".imagename") or ".image.tag" in lower_path or lower_path.endswith(".image"):
            item["current_image"] = str(old) if old is not None else None
            item["target_image"] = str(new) if new is not None else None
        elif any(token in lower_path for token in ("configmap", ".config.", ".env.")):
            item["config_change"] = True
    result = [change for change in changes.values() if change["component"]]
    if component_name:
        # The values example uses container.imageName plus imageVersion. Keep
        # the complete image identity in the runbook, not only the tag.
        for change in result:
            if change["current_image"] is not None or change["target_image"] is not None:
                change["current_image"] = image_reference(current)
                change["target_image"] = image_reference(candidate)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True, type=Path, help="Currently deployed production values file")
    parser.add_argument("--candidate", required=True, type=Path, help="UAT → Prod PR values file")
    parser.add_argument("--release-number", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-ref", required=True)
    parser.add_argument("--helm-path", required=True)
    parser.add_argument("--component", help="Component name derived from the app_manifest folder")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = {
        "release_number": args.release_number,
        "environment": args.environment,
        "repository": args.repository,
        "git_ref": args.git_ref,
        "helm_paths": [args.helm_path],
        # Populate these using the approved Jira query step in GitHub Actions.
        "jira_issues": [],
        "helm_changes": diff_values(load_values(args.current), load_values(args.candidate), args.component),
    }
    if not result["helm_changes"]:
        raise SystemExit("No deployable change was found in the two production values files.")
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
