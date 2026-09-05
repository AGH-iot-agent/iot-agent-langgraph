"""Cluster signals that Helm/CI logs routinely omit (OOMKilled, exit 137)."""

from __future__ import annotations

from typing import Any

OOM_REASONS = {"OOMKilled", "OOMKilling"}
_OOM_MESSAGE_HINTS = ("oomkilled", "oom killing", "out of memory", "memory cgroup")


def pod_is_oomkilled(pod: dict[str, Any]) -> bool:
    if pod.get("oomkilled"):
        return True
    if pod.get("last_terminated_reason") in OOM_REASONS or pod.get("last_exit_code") == 137:
        return True
    for container in pod.get("containers") or []:
        if not isinstance(container, dict):
            continue
        if container.get("oomkilled"):
            return True
        if container.get("reason") in OOM_REASONS or container.get("exit_code") == 137:
            return True
    return False


def event_is_oom(event: dict[str, Any]) -> bool:
    reason = str(event.get("reason") or "")
    if reason in OOM_REASONS:
        return True
    message = str(event.get("message") or "").lower()
    return any(hint in message for hint in _OOM_MESSAGE_HINTS)


def event_mentions(event: dict[str, Any], name: str) -> bool:
    if not name:
        return False
    obj = str(event.get("object") or "")
    message = str(event.get("message") or "")
    return name == obj or name in obj or name in message


def collect_oom_evidence(
    pods: list[dict[str, Any]],
    events: list[dict[str, Any]],
    service: str = "",
) -> list[dict[str, Any]]:
    """Compact OOM facts for CI context. Empty when the cluster has already rolled back."""
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pod in pods:
        name = str(pod.get("name") or "")
        if service and name and service not in name:
            continue
        if not pod_is_oomkilled(pod):
            continue
        key = f"pod:{name}"
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "source": "pod",
            "pod": name,
            "reason": pod.get("last_terminated_reason") or "OOMKilled",
            "exit_code": pod.get("last_exit_code"),
            "restarts": pod.get("restarts"),
            "memory_limits": pod.get("memory_limits") or [],
        })

    for event in events:
        if not event_is_oom(event):
            continue
        if service and not event_mentions(event, service):
            # Node-level OOMKilling often omits the workload name; keep it anyway.
            if str(event.get("object_kind") or "").lower() != "node" and str(event.get("reason") or "") not in OOM_REASONS:
                continue
        key = f"event:{event.get('object')}:{event.get('reason')}:{event.get('message')}"
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "source": "event",
            "object": event.get("object"),
            "object_kind": event.get("object_kind"),
            "reason": event.get("reason"),
            "message": event.get("message"),
        })

    return evidence


def snapshot_deploy_namespace(
    adapter: Any,
    namespace: str,
    service: str = "",
) -> dict[str, Any]:
    """Read pods/events immediately after a Helm CI failure (before --atomic rollback)."""
    snapshot: dict[str, Any] = {"namespace": namespace, "pods": [], "events": [], "oom_evidence": []}
    try:
        pods_resp = adapter.run("kubernetes", "get_pods", {"namespace": namespace}, dry_run=False)
        events_resp = adapter.run("kubernetes", "get_events", {"namespace": namespace}, dry_run=False)
    except Exception:
        return snapshot

    pods = list(pods_resp.get("pods") or []) if isinstance(pods_resp, dict) else []
    events = list(events_resp.get("events") or []) if isinstance(events_resp, dict) else []
    if service:
        pods = [p for p in pods if service in str(p.get("name") or "")] or pods
    snapshot["pods"] = pods
    snapshot["events"] = events
    snapshot["oom_evidence"] = collect_oom_evidence(pods, events, service=service)
    return snapshot
