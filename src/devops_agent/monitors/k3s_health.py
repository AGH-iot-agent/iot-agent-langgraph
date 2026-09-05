from __future__ import annotations
import logging
import subprocess
import json
import os
import time
from devops_agent.event import AgentEvent
from devops_agent.k8s_signals import event_is_oom, event_mentions, pod_is_oomkilled
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
        self._kubectl_timeout_s = max(1, int(os.getenv("K3S_HEALTH_KUBECTL_TIMEOUT_SEC", "15")))
        self._retry_max = max(1, int(os.getenv("K3S_HEALTH_KUBECTL_RETRY_MAX", "2")))
        self._error_log_cooldown_s = max(1, int(os.getenv("K3S_HEALTH_ERROR_COOLDOWN_SEC", "120")))
        self._last_error_log_at: dict[str, float] = {}

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
                        event_kind, event_title = self._classify_pod_event(pod, phase, restarts, k8s_events)
                        events.append(AgentEvent(
                            kind=event_kind,
                            title=event_title,
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

    def _classify_pod_event(
        self,
        pod: dict,
        phase: str,
        restarts: int,
        k8s_events: list[dict],
    ) -> tuple[str, str]:
        """Return (event_kind, title) for an unhealthy pod.

        Promotes to ``k8s_pod_oomkilled`` when:
        - The summarized pod already has lastState/exit 137 (preferred — no extra call), OR
        - A cluster event is OOMKilled/OOMKilling for this pod (including Node events
          whose message mentions the pod), OR
        - The full pod spec (fetched via get_pod) contains lastState.terminated.reason == OOMKilled.

        Falls back to generic ``k8s_pod_crash`` for all other failures.
        """
        pod_name = str(pod.get("name") or "")

        if pod_is_oomkilled(pod):
            return (
                "k8s_pod_oomkilled",
                f"Pod OOMKilled: {pod_name} (restarts={restarts})",
            )

        for evt in k8s_events:
            if event_is_oom(evt) and event_mentions(evt, pod_name):
                return (
                    "k8s_pod_oomkilled",
                    f"Pod OOMKilled: {pod_name} (restarts={restarts})",
                )

        try:
            pod_resp = self._adapter.run(
                "kubernetes", "get_pod",
                {"pod_name": pod_name, "namespace": self._namespace},
                dry_run=False,
            )
            if pod_resp.get("status") == "ok":
                container_statuses = (
                    pod_resp.get("pod", {})
                    .get("status", {})
                    .get("containerStatuses", [])
                )
                for cs in container_statuses:
                    last_terminated = cs.get("lastState", {}).get("terminated", {})
                    current_terminated = cs.get("state", {}).get("terminated", {})
                    if last_terminated.get("reason") == "OOMKilled" or current_terminated.get("reason") == "OOMKilled":
                        return (
                            "k8s_pod_oomkilled",
                            f"Pod OOMKilled: {pod_name} (restarts={restarts})",
                        )
                    if last_terminated.get("exitCode") == 137 or current_terminated.get("exitCode") == 137:
                        return (
                            "k8s_pod_oomkilled",
                            f"Pod OOMKilled: {pod_name} (restarts={restarts})",
                        )
        except Exception:
            logger.debug("[K3sHealthMonitor] could not fetch full pod for %s", pod_name)

        return (
            "k8s_pod_crash",
            f"Pod unhealthy: {pod_name} (phase={phase}, restarts={restarts})",
        )

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
        for attempt in range(1, self._retry_max + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=os.environ,
                    timeout=self._kubectl_timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt >= self._retry_max:
                    self._log_throttled(
                        f"fetch_timeout:{kind}",
                        logging.WARNING,
                        "[K3sHealthMonitor] timeout getting %s in %s after %d attempts (%s)",
                        kind,
                        self._namespace,
                        attempt,
                        exc,
                    )
                    return []
                sleep_s = 0.5 * attempt
                logger.warning(
                    "[K3sHealthMonitor] timeout getting %s in %s, retry %d/%d in %.1fs",
                    kind,
                    self._namespace,
                    attempt,
                    self._retry_max,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue

            if result.returncode == 0:
                try:
                    return json.loads(result.stdout).get("items", [])
                except json.JSONDecodeError as exc:
                    self._log_throttled(
                        f"fetch_bad_json:{kind}",
                        logging.WARNING,
                        "[K3sHealthMonitor] invalid JSON for %s in %s: %s",
                        kind,
                        self._namespace,
                        exc,
                    )
                    return []

            if attempt >= self._retry_max:
                self._log_throttled(
                    f"fetch_cmd_error:{kind}",
                    logging.WARNING,
                    "[K3sHealthMonitor] command failed for %s in %s: %s",
                    kind,
                    self._namespace,
                    (result.stderr or "").strip(),
                )
                return []

            sleep_s = 0.5 * attempt
            logger.warning(
                "[K3sHealthMonitor] command failed for %s in %s, retry %d/%d in %.1fs",
                kind,
                self._namespace,
                attempt,
                self._retry_max,
                sleep_s,
            )
            time.sleep(sleep_s)

        return []

    def _log_throttled(self, key: str, level: int, msg: str, *args: object) -> None:
        now = time.time()
        last = self._last_error_log_at.get(key, 0.0)
        if now - last < self._error_log_cooldown_s:
            return
        self._last_error_log_at[key] = now
        logger.log(level, msg, *args)

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
                self._log_throttled(
                    f"orphan_check_failed:{kind}",
                    logging.ERROR,
                    "[K3sHealthMonitor] orphan check failed for %s in %s",
                    kind,
                    self._namespace,
                )
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
            self._log_throttled(
                "istio_vs_check_failed",
                logging.ERROR,
                "[K3sHealthMonitor] Istio VS check failed for %s",
                ns,
            )
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

