from __future__ import annotations
import logging
import subprocess
import json
import os
from devops_agent.event import AgentEvent
from devops_agent.monitors.base import BaseMonitor
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)

class K3sHealthMonitor(BaseMonitor):
    def __init__(self, adapter: MCPAdapter, namespace: str = "iot-agent") -> None:
        self._adapter = adapter
        self._namespace = namespace
        self._alerted: set[str] = set()
        self._alerted_orphans: set[str] = set()
        self._alerted_vs: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events = []
        try:
            pods_resp = self._adapter.run("kubernetes", "get_pods", {"namespace": self._namespace}, dry_run=False)
            pods = pods_resp.get("pods", []) or []
            rollout_resp = self._adapter.run("kubernetes", "get_rollout_status", {"namespace": self._namespace}, dry_run=False)
            deployments = rollout_resp.get("deployments", []) or []
            events_resp = self._adapter.run("kubernetes", "get_events", {"namespace": self._namespace}, dry_run=False)
            k8s_events = events_resp.get("events", []) or []
            snapshot = {"pods": pods, "deployments": deployments, "events": k8s_events}

            healthy_now: set[str] = set()
            for pod in pods:
                name = pod.get("name", "")
                phase = str(pod.get("phase", "Unknown"))
                restarts = int(pod.get("restarts", 0))
                ready = bool(pod.get("ready", False))
                is_unhealthy = phase not in {"Running", "Succeeded"} or not ready or restarts > 3
                if is_unhealthy:
                    if name not in self._alerted:
                        events.append(AgentEvent(
                            kind="k8s_pod_crash",
                            title=f"Pod unhealthy: {name} (phase={phase}, restarts={restarts})",
                            context={
                                "namespace": self._namespace,
                                "pod": pod,
                                "cluster_snapshot": snapshot,
                            },
                        ))
                        self._alerted.add(name)
                else:
                    healthy_now.add(name)
            self._alerted -= healthy_now

            events.extend(self._check_orphaned_resources(pods, deployments))
            events.extend(self._check_istio_virtualservices())

        except Exception:
            logger.exception("[K3sHealthMonitor] poll failed")
        return events

    @staticmethod
    def _pod_references(pods: list[dict]) -> set[str]:
        refs: set[str] = set()
        for pod in pods:
            for r in pod.get("configmap_refs", []):
                refs.add(f"configmap:{r}")
            for r in pod.get("secret_refs", []):
                refs.add(f"secret:{r}")
        return refs

    @staticmethod
    def _is_system_resource(name: str) -> bool:
        return any(name.startswith(p) for p in ("kube-", "default-token", "sh.helm"))

    def _fetch_k8s_items(self, kind: str) -> list[dict]:
        cmd = ["kubectl", "get", kind, "-n", self._namespace, "-o", "json"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ, timeout=15)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout).get("items", [])

    def _check_orphaned_resources(
        self,
        pods: list[dict],
        deployments: list[dict],  # noqa: ARG002 — reserved for future owner-graph checks
    ) -> list[AgentEvent]:
        """Detect ConfigMaps and Secrets that have no ownerReferences and are not referenced
        by any running pod (via env, envFrom, or volume mounts)."""
        events: list[AgentEvent] = []
        referenced = self._pod_references(pods)
        for kind in ("configmaps", "secrets"):
            try:
                events.extend(self._scan_orphaned_kind(kind, referenced))
            except Exception:
                logger.exception("[K3sHealthMonitor] orphan check failed for %s in %s", kind, self._namespace)
        return events

    def _scan_orphaned_kind(self, kind: str, referenced: set[str]) -> list[AgentEvent]:
        ns = self._namespace
        events: list[AgentEvent] = []
        for item in self._fetch_k8s_items(kind):
            name = item.get("metadata", {}).get("name", "")
            owner_refs = item.get("metadata", {}).get("ownerReferences", [])
            if self._is_system_resource(name):
                continue
            ref_key = f"{kind[:-1]}:{name}"
            if owner_refs or ref_key in referenced:
                continue
            key = f"{kind}:{ns}:{name}"
            if key not in self._alerted_orphans:
                self._alerted_orphans.add(key)
                events.append(AgentEvent(
                    kind="k8s_orphaned_resource",
                    title=f"Orphaned {kind[:-1]} '{name}' in {ns} — no owner, no pod references",
                    context={"namespace": ns, "resource_kind": kind[:-1], "resource_name": name},
                ))
        return events


    def _check_istio_virtualservices(self) -> list[AgentEvent]:
        """Check Istio VirtualServices for hosts that don't match any K8s Service in the namespace."""
        events: list[AgentEvent] = []
        ns = self._namespace
        try:
            svc_names = {item.get("metadata", {}).get("name", "") for item in self._fetch_k8s_items("svc")}
            for item in self._fetch_k8s_items("virtualservice"):
                events.extend(self._validate_vs_hosts(item, svc_names, ns))
        except Exception:
            logger.exception("[K3sHealthMonitor] Istio VS check failed for %s", ns)
        return events

    def _validate_vs_hosts(
        self, item: dict, svc_names: set[str], ns: str
    ) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        vs_name = item.get("metadata", {}).get("name", "")
        for host in item.get("spec", {}).get("hosts", []):
            if host == "*" or "." in host:
                continue
            if host not in svc_names:
                key = f"istio_vs:{ns}:{vs_name}:{host}"
                if key not in self._alerted_vs:
                    self._alerted_vs.add(key)
                    events.append(AgentEvent(
                        kind="istio_vs_misconfiguration",
                        title=f"VirtualService '{vs_name}' in {ns} references unknown host '{host}'",
                        context={
                            "namespace": ns,
                            "vs_name": vs_name,
                            "unknown_host": host,
                            "known_services": sorted(svc_names),
                        },
                    ))
        return events

