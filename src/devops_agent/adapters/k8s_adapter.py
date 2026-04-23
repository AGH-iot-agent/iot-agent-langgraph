from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import subprocess
import json
import logging

logger = logging.getLogger(__name__)

KUBECONFIG_DEFAULT = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))

@dataclass
class K8sAdapter:
    kubeconfig: str = field(default_factory=lambda: KUBECONFIG_DEFAULT)

    ''' 
        Pod operations:
        - get_pods(namespace) -> list of pods with status
        - get_pod(pod_name, namespace) -> detailed pod debug
        - describe_pod(pod_name, namespace) -> kubectl describe output
        - get_events(namespace) -> recent events in the namespace
        - describe_event(event_name, namespace) -> kubectl describe output for event
    '''

    def get_pods(self, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)

            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}

            pods_json = json.loads(result.stdout)
            pod_list = []

            for pod in pods_json.get("items", []):
                statuses = pod.get("status", {}).get("containerStatuses", [])
                pod_list.append({
                    "name": pod["metadata"]["name"],
                    "phase": pod["status"].get("phase", ""),
                    "node": pod["spec"].get("nodeName", None),
                    "restarts": sum(cs.get("restartCount", 0) for cs in statuses),
                    "ready": all(cs.get("ready", False) for cs in statuses) if statuses else False,
                })
            logger.debug("Retrieved %d pods from namespace %s", len(pod_list), namespace)
            return {"status": "ok", "pods": pod_list}
        except Exception as e:
            logger.exception("Exception occurred while getting pods for namespace %s", namespace)
            return {"status": "error", "message": str(e)}


    def get_pod(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}
            pod_json = json.loads(result.stdout)
            logger.debug("Successfully retrieved pod %s in namespace %s", pod_name, namespace)
            return {"status": "ok", "pod": pod_json}
        except Exception as e:
            logger.exception("Exception occurred while getting pod %s in namespace %s", pod_name, namespace)
            return {"status": "error", "message": str(e)}


    def describe_pod(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "describe", "pod", pod_name, "-n", namespace]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}
            logger.debug("Successfully described pod %s in namespace %s", pod_name, namespace)
            return {"status": "ok", "description": result.stdout}
        except Exception as e:
            logger.exception("Exception occurred while describing pod %s in namespace %s", pod_name, namespace)
            return {"status": "error", "message": str(e)}

    ''' 
        Events:
        - get_events(namespace) -> list of recent events in the namespace
        - describe_event(event_name, namespace) -> kubectl describe output for the event
    '''

    def get_events(self, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "events", "-n", namespace, "-o", "json"]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}
            events_json = json.loads(result.stdout)
            events = []
            for event in events_json.get("items", []):
                events.append({
                    "reason": event.get("reason"),
                    "message": event.get("message"),
                    "type": event.get("type"),
                    "object": event.get("involvedObject", {}).get("name"),
                    "last_time": event.get("lastTimestamp"),
                })
            logger.debug("Retrieved %d events from namespace %s", len(events), namespace)
            return {"status": "ok", "events": events}
        except Exception as e:
            logger.exception("Exception occurred while getting events for namespace %s", namespace)
            return {"status": "error", "message": str(e)}


    def describe_event(self, event_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "describe", "event", event_name, "-n", namespace]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}
            logger.debug("Successfully described event %s in namespace %s", event_name, namespace)
            return {"status": "ok", "description": result.stdout}
        except Exception as e:
            logger.exception("Exception occurred while describing event %s in namespace %s", event_name, namespace)
            return {"status": "error", "message": str(e)}


    def get_pod_events(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "events", "-n", namespace, "-o", "json"]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}

            events_json = json.loads(result.stdout)

            events = []
            for event in events_json.get("items", []):
                involved = event.get("involvedObject", {})

                if involved.get("kind") == "Pod" and involved.get("name") == pod_name:
                    events.append({
                        "reason": event.get("reason"),
                        "message": event.get("message"),
                        "type": event.get("type"),
                        "last_time": event.get("lastTimestamp"),
                    })
            logger.debug("Retrieved %d events for pod %s in namespace %s", len(events), pod_name, namespace)
            return {"status": "ok", "events": events}

        except Exception as e:
            logger.exception("Exception occurred while getting events for pod %s in namespace %s", pod_name, namespace)
            return {"status": "error", "message": str(e)}

    '''
        Rollout status:
        - get_rollout_status(namespace) -> status of deployments in the namespace
    '''

    def get_rollout_status(self, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"]
        logger.debug("Running command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("Command failed with error: %s", result.stderr)
                return {"status": "error", "message": result.stderr}
            deployments_json = json.loads(result.stdout)
            deployments = []
            for dep in deployments_json.get("items", []):
                status = dep.get("status", {})
                deployments.append({
                    "name": dep["metadata"]["name"],
                    "desired": status.get("replicas", 0),
                    "ready": status.get("readyReplicas", 0),
                    "available": status.get("availableReplicas", 0),
                })
            logger.debug("Retrieved rollout status for %d deployments in namespace %s", len(deployments), namespace)
            return {"status": "ok", "deployments": deployments}
        except Exception as e:
            logger.exception("Exception occurred while getting rollout status for namespace %s", namespace)
            return {"status": "error", "message": str(e)}

    '''
        Get pod logs:
        - get_pod_logs(namespace, pod_name, container=None, tail=100) -> last lines of logs from the pod (optionally from specific container)
    '''
    def get_pod_logs(
        self,
        namespace: str,
        pod: str,
        container: str | None = None,
        tail: int = 100,
    ) -> dict:
        try:
            from kubernetes import client
            v1 = client.CoreV1Api()
            logs = v1.read_namespaced_pod_log(
                name=pod,
                namespace=namespace,
                container=container,
                tail_lines=tail,
            )
            return {
                "status": "ok",
                "pod": pod,
                "namespace": namespace,
                "lines": logs.splitlines(),
                "line_count": len(logs.splitlines()),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


    def restart_deployment(
        self,
        namespace: str,
        deployment: str,
        dry_run: bool = True,
    ) -> dict:
        if dry_run:
            return {
                "status":  "dry_run",
                "message": f"[DRY RUN] Would restart deployment/{deployment} in {namespace}",
                "deployment": deployment,
                "namespace":  namespace,
            }
        try:
            from kubernetes import client
            from datetime import datetime, timezone
            apps_v1 = client.AppsV1Api()
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": 
                                    datetime.now(timezone.utc).isoformat()
                            }
                        }
                    }
                }
            }
            apps_v1.patch_namespaced_deployment(
                name=deployment,
                namespace=namespace,
                body=patch,
            )
            return {
                "status":     "ok",
                "message":    f"Restarted deployment/{deployment} in {namespace}",
                "deployment": deployment,
                "namespace":  namespace,
            }
        except Exception as exc:
                logger.exception("Exception occurred while restarting deployment %s in namespace %s", deployment, namespace)
                return {"status": "error", "message": str(exc)}


    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"status": "ok", "dry_run": True}
        
        if action == "get_pods":
            return self.get_pods(args["namespace"])
        if action == "get_pod":
            return self.get_pod(args["pod_name"], args["namespace"])
        if action == "describe_pod":
            return self.describe_pod(args["pod_name"], args["namespace"])
        
        if action == "get_events":
            return self.get_events(args["namespace"])
        if action == "describe_event":
            return self.describe_event(args["event_name"], args["namespace"])
        if action == "get_pod_events":
            return self.get_pod_events(args["pod_name"], args["namespace"])
        
        if action == "get_rollout_status":
            return self.get_rollout_status(args["namespace"])
        if action == "get_pod_logs":
            return self.get_pod_logs(args["namespace"], args["pod_name"], args.get("container"), args.get("tail", 100))
        if action == "restart_deployment":
            return self.restart_deployment(args["namespace"], args["deployment"], dry_run=dry_run)