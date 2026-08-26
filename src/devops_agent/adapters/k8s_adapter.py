from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import subprocess
import json
import logging
import shutil
import tempfile
import tarfile
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

KUBECONFIG_DEFAULT  = os.getenv("KUBECONFIG")
_LOG_RUNNING_CMD    = "Running command: %s"
_LOG_CMD_FAILED     = "Command failed with error: %s"
_YAML_SUFFIX        = ".yaml"

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
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())

            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
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
        
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        kubeconfig = self.kubeconfig or env.get("KUBECONFIG")
        if kubeconfig is not None:
            env["KUBECONFIG"] = kubeconfig
        else:
            env.pop("KUBECONFIG", None)
        return env

    def get_pod(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            pod_json = json.loads(result.stdout)
            logger.debug("Successfully retrieved pod %s in namespace %s", pod_name, namespace)
            return {"status": "ok", "pod": pod_json}
        except Exception as e:
            logger.exception("Exception occurred while getting pod %s in namespace %s", pod_name, namespace)
            return {"status": "error", "message": str(e)}

    def describe_pod(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "describe", "pod", pod_name, "-n", namespace]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
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
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
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
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            logger.debug("Successfully described event %s in namespace %s", event_name, namespace)
            return {"status": "ok", "description": result.stdout}
        except Exception as e:
            logger.exception("Exception occurred while describing event %s in namespace %s", event_name, namespace)
            return {"status": "error", "message": str(e)}


    def get_pod_events(self, pod_name: str, namespace: str = "default") -> dict[str, Any]:
        cmd = ["kubectl", "get", "events", "-n", namespace, "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
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
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
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


    def apply_manifest_dryrun(self, manifest_yaml: str, namespace: str = "iotag-sbx") -> dict[str, Any]:
        """Server-side dry-run of a manifest — validates without applying."""
        import tempfile, os as _os
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=_YAML_SUFFIX, delete=False) as f:
                f.write(manifest_yaml)
                tmp_path = f.name
            cmd = ["kubectl", "apply", "--dry-run=server", "-n", namespace, "-f", tmp_path]
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            _os.unlink(tmp_path)
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            return {"status": "ok", "output": result.stdout}
        except Exception as exc:
            logger.exception("Exception in apply_manifest_dryrun")
            return {"status": "error", "message": str(exc)}


    @staticmethod
    def _materialize_template_entry(entry, orig_templates) -> None:
        import shutil

        if entry.is_symlink():
            orig_entry = orig_templates / entry.name
            real = orig_entry.resolve()
            if real.is_file():
                entry.unlink()
                shutil.copy2(real, entry)
                logger.debug("[HELM] Materialized symlink: %s -> %s", entry.name, real)
            return

        if not entry.is_file():
            return

        raw = entry.read_text(encoding="utf-8", errors="replace").strip().rstrip("\r\n")
        if raw.startswith("../") and (raw.endswith(_YAML_SUFFIX) or raw.endswith(".tpl")):
            resolved = (orig_templates / raw).resolve()
            if resolved.is_file():
                shutil.copy2(resolved, entry)
                logger.debug("[HELM] Materialized pointer file: %s -> %s", entry.name, resolved)
            else:
                logger.warning("[HELM] Pointer target not found: %s -> %s", entry.name, resolved)

    @staticmethod
    def _materialize_chart(chart: str) -> tuple[str, str | None]:
        """Copy the chart to a temp directory and materialize all pointer files and symlinks.

        The local Universal-Kubernetes-Helm-Charts checkout contains template files whose
        content is only a relative path like ``../../../templates/service.yaml`` (pointer files)
        or filesystem symlinks.  ``helm template`` cannot process these — it sees them as
        literal YAML and either fails or renders nothing.

        This mirrors the "Materialize chart helper symlinks" step in
        ``common-pipeline/.github/workflows/helm.yml`` so the local render matches CI exactly.
        """
        import shutil, tempfile as _tf
        from pathlib import Path as _Path

        orig_templates = _Path(chart) / "templates"
        if not orig_templates.is_dir():
            return chart, None

        tmp_dir = _tf.mkdtemp(prefix="helm-chart-")
        tmp_chart = os.path.join(tmp_dir, "chart")
        shutil.copytree(chart, tmp_chart, symlinks=True)

        templates_dir = _Path(tmp_chart) / "templates"
        if not templates_dir.is_dir():
            return tmp_chart, tmp_dir

        for entry in templates_dir.iterdir():
            K8sAdapter._materialize_template_entry(entry, orig_templates)

        return tmp_chart, tmp_dir

    @staticmethod
    def _is_smb_uri(path: str) -> bool:
        return str(path).lower().startswith("smb://")

    @staticmethod
    def _download_smb_chart(uri: str) -> tuple[str, str]:
        tmp_dir = tempfile.mkdtemp(prefix="helm-chart-smb-")
        parsed = urlparse(uri)

        # smb://server/share/path/to/file.tar
        server = parsed.hostname
        share_and_path = parsed.path.lstrip("/")

        parts = share_and_path.split("/")
        share = parts[0]
        remote_path = "/".join(parts[1:]) if len(parts) > 1 else ""

        filename = os.path.basename(parsed.path) or "chart.tar"
        local_path = os.path.join(tmp_dir, filename)

        smb_username = os.environ.get("SMB_USERNAME", "")
        smb_password = os.environ.get("SMB_PASSWORD", "")
        smb_domain = os.environ.get("SMB_DOMAIN", "")

        user = smb_username
        if smb_domain and "\\" not in smb_username:
            user = f"{smb_domain}\\{smb_username}"

        cmd = [
            "smbclient",
            f"//{server}/{share}",
            "-c",
            f"get {remote_path} {local_path}" if remote_path else f"get {filename} {local_path}",
        ]

        if user and smb_password:
            cmd += ["-U", f"{user}%{smb_password}"]
        else:
            cmd += ["-N"]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(
                f"failed to fetch SMB chart source '{uri}': {result.stderr or result.stdout}"
            )

        return local_path, tmp_dir

    @staticmethod
    def _find_chart_dir(root_dir: str) -> str:
        for current_root, _, files in os.walk(root_dir):
            if "Chart.yaml" in files:
                return current_root
        raise RuntimeError(f"no Helm chart directory (Chart.yaml) found under extracted archive: {root_dir}")

    @staticmethod
    def _extract_chart_archive(archive_path: str) -> tuple[str, str]:
        extract_dir = tempfile.mkdtemp(prefix="helm-chart-extract-")
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(path=extract_dir)
            chart_dir = K8sAdapter._find_chart_dir(extract_dir)
            return chart_dir, extract_dir
        except Exception:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise

    def _resolve_chart_source(self, chart: str) -> tuple[str, list[str]]:
        """Resolve chart source into helm-usable path and cleanup dirs.

        Supported sources:
        - local chart directory
        - local chart archive (.tgz/.tar/.tar.gz)
        - smb:// URI to an archive (wnloaded via curl first)
        """
        source = (chart or "").strip()
        cleanup_dirs: list[str] = []

        if self._is_smb_uri(source):
            downloaded, tmp_dir = self._download_smb_chart(source)
            cleanup_dirs.append(tmp_dir)
            source = downloaded

        if os.path.isdir(source):
            materialized_chart, materialize_tmp = self._materialize_chart(source)
            if materialize_tmp:
                cleanup_dirs.append(materialize_tmp)
            return materialized_chart, cleanup_dirs

        if os.path.isfile(source):
            lower = source.lower()
            if lower.endswith(".tar") or lower.endswith(".tar.gz") or lower.endswith(".tgz"):
                chart_dir, extract_tmp = self._extract_chart_archive(source)
                cleanup_dirs.append(extract_tmp)
                materialized_chart, materialize_tmp = self._materialize_chart(chart_dir)
                if materialize_tmp:
                    cleanup_dirs.append(materialize_tmp)
                return materialized_chart, cleanup_dirs

        raise RuntimeError(f"unsupported or missing Helm chart source: {chart}")

    def get_resource_quota(self, namespace: str = "default") -> dict[str, Any]:
        """Return ResourceQuota usage and hard limits for the given namespace."""
        cmd = ["kubectl", "get", "resourcequota", "-n", namespace, "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            rq_json = json.loads(result.stdout)
            quotas = []
            for item in rq_json.get("items", []):
                hard = item.get("status", {}).get("hard", {})
                used = item.get("status", {}).get("used", {})
                resources: list[dict] = []
                for resource, hard_val in hard.items():
                    used_val = used.get(resource, "0")
                    resources.append({
                        "resource": resource,
                        "hard": hard_val,
                        "used": used_val,
                    })
                quotas.append({
                    "name": item["metadata"]["name"],
                    "namespace": namespace,
                    "resources": resources,
                })
            return {"status": "ok", "quotas": quotas, "namespace": namespace}
        except Exception as e:
            logger.exception("Exception in get_resource_quota for namespace %s", namespace)
            return {"status": "error", "message": str(e)}

    def get_pvc_usage(self, namespace: str = "default") -> dict[str, Any]:
        """Return PersistentVolumeClaim list with capacity and access modes for the namespace."""
        cmd = ["kubectl", "get", "pvc", "-n", namespace, "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            pvc_json = json.loads(result.stdout)
            pvcs = []
            for item in pvc_json.get("items", []):
                spec = item.get("spec", {})
                status = item.get("status", {})
                pvcs.append({
                    "name": item["metadata"]["name"],
                    "namespace": namespace,
                    "phase": status.get("phase", ""),
                    "storage_class": spec.get("storageClassName", ""),
                    "access_modes": spec.get("accessModes", []),
                    "capacity_requested": spec.get("resources", {}).get("requests", {}).get("storage", ""),
                    "capacity_actual": status.get("capacity", {}).get("storage", ""),
                    "volume_name": spec.get("volumeName", ""),
                })
            return {"status": "ok", "pvcs": pvcs, "namespace": namespace}
        except Exception as e:
            logger.exception("Exception in get_pvc_usage for namespace %s", namespace)
            return {"status": "error", "message": str(e)}

    def get_node_conditions(self) -> dict[str, Any]:
        """Return node conditions (DiskPressure, MemoryPressure, PIDPressure, Ready) for all nodes."""
        cmd = ["kubectl", "get", "nodes", "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            nodes_json = json.loads(result.stdout)
            nodes = []
            pressure_types = {"DiskPressure", "MemoryPressure", "PIDPressure", "Ready"}
            for node in nodes_json.get("items", []):
                conditions = {
                    c["type"]: c["status"]
                    for c in node.get("status", {}).get("conditions", [])
                    if c["type"] in pressure_types
                }
                allocatable = node.get("status", {}).get("allocatable", {})
                nodes.append({
                    "name": node["metadata"]["name"],
                    "conditions": conditions,
                    "allocatable_cpu": allocatable.get("cpu", ""),
                    "allocatable_memory": allocatable.get("memory", ""),
                    "allocatable_pods": allocatable.get("pods", ""),
                })
            return {"status": "ok", "nodes": nodes}
        except Exception as e:
            logger.exception("Exception in get_node_conditions")
            return {"status": "error", "message": str(e)}

    def helm_template_render(self, name: str, chart: str, values_override: str = "") -> dict[str, Any]:
        """Render a Helm chart with optional values override (YAML string).

        Pointer files and symlinks in the chart templates directory are materialized
        before rendering so the result matches what the CI pipeline produces.
        """
        import os as _os, shutil as _shutil
        cleanup_dirs: list[str] = []
        tmp_values: str | None = None
        try:
            resolved_chart, cleanup_dirs = self._resolve_chart_source(chart)

            cmd = ["helm", "template", name, resolved_chart]
            if values_override:
                with tempfile.NamedTemporaryFile(mode="w", prefix="values_fix_01-", suffix=_YAML_SUFFIX, delete=False) as f:
                    f.write(values_override)
                    tmp_values = f.name
                cmd += ["-f", tmp_values]

            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            return {"status": "ok", "manifests": result.stdout}
        except Exception as exc:
            logger.exception("Exception in helm_template_render")
            return {"status": "error", "message": str(exc)}
        finally:
            if tmp_values and os.path.exists(tmp_values):
                _os.unlink(tmp_values)
            for cleanup_dir in cleanup_dirs:
                if cleanup_dir and os.path.isdir(cleanup_dir):
                    _shutil.rmtree(cleanup_dir, ignore_errors=True)

    def _helm_uninstall(self, release_name: str, namespace: str, dry_run: bool = False) -> dict[str, Any]:
        cmd = ["helm", "uninstall", release_name, "--namespace", namespace]
        if dry_run:
            return {"status": "dry_run", "message": f"[DRY RUN] Would run: {' '.join(cmd)}"}
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            return {"status": "ok", "output": result.stdout}
        except Exception as exc:
            logger.exception("Exception in _helm_uninstall")
            return {"status": "error", "message": str(exc)}

    def _helm_upgrade_dry_run(
        self,
        *,
        release_name: str,
        namespace: str,
        chart: str,
        values_override: str,
        image_repository: str,
        image_tag: str,
    ) -> dict[str, Any]:
        import os as _os, shutil as _shutil

        cleanup_dirs: list[str] = []
        tmp_values: str | None = None
        try:
            resolved_chart, cleanup_dirs = self._resolve_chart_source(chart)
            with tempfile.NamedTemporaryFile(mode="w", prefix="values_fix_01-", suffix=_YAML_SUFFIX, delete=False) as f:
                f.write(values_override or "{}\n")
                tmp_values = f.name

            cmd = [
                "helm",
                "upgrade",
                "--install",
                release_name,
                resolved_chart,
                "--namespace",
                namespace,
                "--create-namespace",
                "-f",
                tmp_values,
                "--set",
                f"image.repository={image_repository}",
                "--set",
                f"image.tag={image_tag}",
                "--dry-run",
                "--debug",
                "--wait",
                "--timeout",
                "5m",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": result.stderr or result.stdout or "helm upgrade --dry-run failed",
                }

            return {
                "status": "ok",
                "output": result.stdout,
            }
        except Exception as exc:
            logger.exception("Exception in _helm_upgrade_dry_run")
            return {"status": "error", "message": str(exc)}
        finally:
            if tmp_values and _os.path.exists(tmp_values):
                _os.unlink(tmp_values)
            for cleanup_dir in cleanup_dirs:
                if cleanup_dir and _os.path.isdir(cleanup_dir):
                    _shutil.rmtree(cleanup_dir, ignore_errors=True)


    def helm_validate_deployability(
        self,
        release_name: str,
        service_name: str,
        namespace: str,
        chart: str,
        values_override: str = "",
        image_repository: str = "ghcr.io/placeholder/placeholder",
        image_tag: str = "ci",
    ) -> dict[str, Any]:
        """Validate Helm deployability in the target namespace without applying resources.

        Mirrors CI Helm inputs: release/service/namespace/chart/values/image fields.
        The chart is rendered with Helm and validated against the cluster API using
        ``kubectl apply --dry-run=server``.
        """
        preflight_error = self._validate_helm_environment(chart=chart, namespace=namespace)
        if preflight_error is not None:
            return preflight_error

        upgrade_dry_run = self._helm_upgrade_dry_run(
            release_name=release_name,
            namespace=namespace,
            chart=chart,
            values_override=values_override,
            image_repository=image_repository,
            image_tag=image_tag,
        )

        if upgrade_dry_run.get("status") != "ok":
            return {
                "status": "error",
                "message": f"helm upgrade --install --dry-run failed: {upgrade_dry_run.get('message', '')}",
            }

        render_result = self.helm_template_render(
            name=release_name,
            chart=chart,
            values_override=values_override,
        )
        if render_result.get("status") != "ok":
            return {
                "status": "error",
                "message": f"helm template failed: {render_result.get('message', '')}",
            }

        manifests = render_result.get("manifests", "")
        if not manifests:
            return {
                "status": "error",
                "message": "helm template returned empty manifests",
            }

        apply_result = self.apply_manifest_dryrun(manifests, namespace=namespace)
        if apply_result.get("status") != "ok":
            return {
                "status": "error",
                "message": f"kubectl apply --dry-run failed: {apply_result.get('message', '')}",
            }

        return {
            "status": "ok",
            "release_name": release_name,
            "service_name": service_name,
            "namespace": namespace,
            "chart": chart,
            "image_repository": image_repository,
            "image_tag": image_tag,
            "manifests": manifests,
            "helm_upgrade_dry_run_output": upgrade_dry_run.get("output", ""),
            "output": apply_result.get("output", ""),
        }

    def _probe_cluster_access(self) -> str | None:
        last_error: str = "cluster API unreachable"
        kubectl_probe_cmds = [
            ["kubectl", "version", "--short", "--request-timeout=8s"],
            ["kubectl", "version", "--request-timeout=8s"],
        ]

        for cmd in kubectl_probe_cmds:
            try:
                cluster_probe = subprocess.run(
                    cmd, capture_output=True, text=True, env=self._env(), timeout=12,
                )
            except Exception as exc:
                last_error = str(exc)
                continue

            if cluster_probe.returncode == 0:
                return None

            stderr = (cluster_probe.stderr or "").strip()
            stdout = (cluster_probe.stdout or "").strip()
            detail = stderr or stdout or "cluster API unreachable"
            last_error = detail

            # Older kubectl builds do not support --short, try fallback command.
            if "unknown flag: --short" in detail and "--short" in cmd:
                continue

            break

        return last_error

    def _validate_helm_environment(self, *, chart: str, namespace: str) -> dict[str, Any] | None:
        missing: list[str] = []
        if not shutil.which("helm"):
            missing.append("helm binary not found in PATH")
        if not shutil.which("kubectl"):
            missing.append("kubectl binary not found in PATH")
        if self._is_smb_uri(chart) and not shutil.which("smbclient"):
            missing.append("smbclient binary not found in PATH (required for smb:// chart source)")
        if missing:
            return {"status": "error", "message": "; ".join(missing)}

        if not chart:
            return {"status": "error", "message": "chart source is empty"}
        if not self._is_smb_uri(chart) and not os.path.exists(chart):
            return {"status": "error", "message": f"chart path does not exist: {chart}"}

        cluster_error = self._probe_cluster_access()
        if cluster_error:
            return {
                "status": "error",
                "message": f"cluster preflight failed for namespace {namespace}: {cluster_error}",
            }

        return None
    
    def list_secrets(self, namespace: str = "default") -> dict[str, Any]:
        kubectl_list_cmd = ["kubectl", "get", "secrets", "-n", namespace, "-o", "json"]
        logger.debug(_LOG_RUNNING_CMD, " ".join(kubectl_list_cmd))
        try:
            result = subprocess.run(kubectl_list_cmd, capture_output=True, text=True, env=self._env())
            if result.returncode != 0:
                logger.warning(_LOG_CMD_FAILED, result.stderr)
                return {"status": "error", "message": result.stderr}
            secrets_json = json.loads(result.stdout)
            secrets = []
            for secret in secrets_json.get("items", []):
                secrets.append({
                    "name": secret["metadata"]["name"],
                    "type": secret.get("type", ""),
                    "creation_timestamp": secret["metadata"].get("creationTimestamp", ""),
                })
            logger.debug("Retrieved %d secrets from namespace %s", len(secrets), namespace)
            return {"status": "ok", "secrets": secrets}
        except Exception as e:
            logger.exception("Exception occurred while listing secrets in namespace %s", namespace)
            return {"status": "error", "message": str(e)}  
        
    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"status": "ok", "dry_run": True}
        
        match action:
            case "get_pods":
                return self.get_pods(args["namespace"])
            case "get_pod":
                return self.get_pod(args["pod_name"], args["namespace"])
            case "describe_pod":
                return self.describe_pod(args["pod_name"], args["namespace"])
            case "get_events": 
                return self.get_events(args["namespace"])
            case "describe_event":
                return self.describe_event(args["event_name"], args["namespace"])
            case "get_pod_events":
                return self.get_pod_events(args["pod_name"], args["namespace"])
            case "get_rollout_status":
                return self.get_rollout_status(args["namespace"])
            case "get_pod_logs":
                return self.get_pod_logs(args["namespace"], args.get("pod") or args.get("pod_name", ""), args.get("container"), args.get("tail", 100))
            case "list_secrets":
                return self.list_secrets(args["namespace"])
            case "restart_deployment":
                return self.restart_deployment(args["namespace"], args["deployment"], dry_run=dry_run)
            case "apply_manifest_dryrun":
                return self.apply_manifest_dryrun(args["manifest_yaml"], args.get("namespace", "iotag-sbx"))
            case "helm_uninstall":
                return self._helm_uninstall(args["release_name"], args["namespace"], dry_run=dry_run)
            case "helm_template_render":
                return self.helm_template_render(args["name"], args["chart"], args.get("values_override", ""))
            case "helm_validate_deployability":
                return self.helm_validate_deployability(
                    release_name=args["release_name"],
                    service_name=args["service_name"],
                    namespace=args["namespace"],
                    chart=args["chart"],
                    values_override=args.get("values_override", ""),
                    image_repository=args.get("image_repository", "ghcr.io/placeholder/placeholder"),
                    image_tag=args.get("image_tag", "ci"),
                )
            case _:
                return {"status": "error", "message": f"Unknown action: {action}"}

