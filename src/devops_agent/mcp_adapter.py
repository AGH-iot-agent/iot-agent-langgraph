from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request as urllib_request

from devops_agent.adapters.loki_adapter import LokiAdapter
from devops_agent.adapters.prometheus_adapter import PrometheusAdapter


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(token in text for token in needles)


def _job_level_hints(job_name: str) -> list[str]:
    hints: list[str] = []
    mapping = {
        ("test",): "Uruchom lokalnie testy z tego modulu i popraw pierwsza asercje, ktora pada.",
        ("lint", "format"): "Uruchom linter/formatter lokalnie i zatwierdz poprawki stylu.",
        ("build", "compile"): "Sprawdz zaleznosci i wersje JDK/Node oraz odtworz build lokalnie.",
        ("docker", "image"): "Zweryfikuj Dockerfile, kontekst buildu i dostepnosc bazowych obrazow.",
        ("deploy", "helm", "k8s"): "Sprawdz sekrety, uprawnienia klastra i wartosci Helm dla danego srodowiska.",
    }
    for tokens, message in mapping.items():
        if _contains_any(job_name, tokens):
            hints.append(message)
    return hints


def _step_level_hints(step_name: str) -> list[str]:
    hints: list[str] = []
    mapping = {
        ("test",): "Padl krok testowy: odtworz komende testowa i sprawdz ostatnie zmiany domenowe.",
        ("lint", "checkstyle"): "Padl krok lint/checkstyle: popraw naruszenia i odpal pipeline ponownie.",
        ("npm", "yarn", "pnpm"): "Sprawdz lockfile i zgodnosc wersji Node z pipeline.",
        ("maven", "gradle"): "Sprawdz cache zaleznosci i kompatybilnosc pluginow build toola.",
    }
    for tokens, message in mapping.items():
        if _contains_any(step_name, tokens):
            hints.append(message)
    return hints


def _infer_fix_hints(job: dict[str, Any]) -> list[str]:
    hints = _job_level_hints(str(job.get("name", "")).lower())
    job_name = str(job.get("name", "")).lower()
    steps = job.get("steps", []) or []

    for step in steps:
        step_name = str(step.get("name", "")).lower()
        hints.extend(_step_level_hints(step_name))

    if not hints and job_name:
        hints.append("Sprawdz log joba i napraw pierwszy krok z conclusion=failure, potem rerun workflow.")
    return _dedupe(hints)


def _select_run_from_args(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not runs:
        return None
    for run in runs:
        if str(run.get("conclusion", "")).lower() in {"failure", "cancelled", "timed_out", "action_required"}:
            return run
    return runs[0]


def _extract_failed_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        job
        for job in jobs
        if str(job.get("conclusion", "")).lower() in {"failure", "cancelled", "timed_out", "action_required"}
    ]


def _extract_failing_steps(failed_jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for job in failed_jobs:
        for step in job.get("steps", []) or []:
            if str(step.get("conclusion", "")).lower() == "failure":
                steps.append(
                    {
                        "job": str(job.get("name", "unknown")),
                        "step": str(step.get("name", "unknown")),
                    }
                )
    return steps


def _build_summary_markdown(
    run_payload: dict[str, Any],
    failed_jobs: list[dict[str, Any]],
    failing_steps: list[dict[str, str]],
    hints: list[str],
) -> str:
    summary_lines = [
        "## GitHub Actions Failure Summary",
        f"- Run ID: {run_payload.get('id')}",
        f"- Workflow: {run_payload.get('name', 'unknown')}",
        f"- Status/Conclusion: {run_payload.get('status')}/{run_payload.get('conclusion')}",
        f"- URL: {run_payload.get('html_url')}",
        "",
        "### Failed jobs",
    ]

    if failed_jobs:
        summary_lines.extend(
            [
                f"- {job.get('name', 'unknown')} ({job.get('conclusion')}) -> {job.get('html_url')}"
                for job in failed_jobs
            ]
        )
    else:
        summary_lines.append("- No failed jobs found in selected run.")

    summary_lines.append("")
    summary_lines.append("### First failing steps")
    if failing_steps:
        summary_lines.extend([f"- {item['job']}: {item['step']}" for item in failing_steps[:10]])
    else:
        summary_lines.append("- No explicit failing step available.")

    summary_lines.append("")
    summary_lines.append("### Suggested fixes")
    summary_lines.extend([f"- {hint}" for hint in hints[:8]])
    return "\n".join(summary_lines)


@dataclass
class MCPAdapter:
    github_token: str | None = None
    github_repository: str | None = None
    github_org: str | None = None
    github_api_url: str = "https://api.github.com"
    _loki: LokiAdapter = field(default_factory=LokiAdapter, init=False, repr=False)
    _prometheus: PrometheusAdapter = field(default_factory=PrometheusAdapter, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.github_token is None:
            self.github_token = os.getenv("GITHUB_TOKEN")
        if self.github_repository is None:
            self.github_repository = os.getenv("GITHUB_REPOSITORY")
        if self.github_org is None:
            self.github_org = os.getenv("GITHUB_ORG", "AGH-iot-agent")
        api_url = os.getenv("GITHUB_API_URL")
        if api_url:
            self.github_api_url = api_url.rstrip("/")

    def _github_headers(self) -> dict[str, str]:
        if not self.github_token:
            raise ValueError("Missing GITHUB_TOKEN env var.")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "iot-agent-langgraph",
        }

    def _github_request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.github_repository:
            raise ValueError("Missing GITHUB_REPOSITORY env var (owner/repo).")

        full_path = f"/repos/{self.github_repository}{path}"
        if query:
            full_path = f"{full_path}?{parse.urlencode(query)}"
        url = f"{self.github_api_url}{full_path}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None

        req = urllib_request.Request(
            url=url,
            data=payload,
            headers=self._github_headers(),
            method=method,
        )
        with urllib_request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)

    def _github_list_workflow_runs(self, args: dict[str, Any]) -> dict[str, Any]:
        query: dict[str, Any] = {
            "per_page": int(args.get("per_page", 20)),
        }
        if args.get("branch"):
            query["branch"] = str(args["branch"])
        if args.get("status"):
            query["status"] = str(args["status"])

        data = self._github_request("GET", "/actions/runs", query=query)
        runs = data.get("workflow_runs", [])
        return {
            "status": "ok",
            "count": len(runs),
            "workflow_runs": runs,
        }

    def _github_list_jobs_for_run(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = args.get("run_id")
        if not run_id:
            return {
                "status": "error",
                "message": "run_id is required for list_jobs_for_run",
            }

        data = self._github_request("GET", f"/actions/runs/{run_id}/jobs", query={"per_page": 100})
        jobs = data.get("jobs", [])
        return {
            "status": "ok",
            "run_id": run_id,
            "count": len(jobs),
            "jobs": jobs,
        }

    def _github_get_run_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = args.get("run_id")

        if not run_id:
            runs_data = self._github_list_workflow_runs({"per_page": 20})
            runs = runs_data.get("workflow_runs", [])
            chosen = _select_run_from_args(runs)
            if not chosen:
                return {
                    "status": "error",
                    "message": "No workflow runs found.",
                }
            run_id = chosen.get("id")
            run_payload = chosen
        else:
            run_payload = self._github_request("GET", f"/actions/runs/{run_id}")

        jobs_data = self._github_list_jobs_for_run({"run_id": run_id})
        jobs = jobs_data.get("jobs", [])
        failed_jobs = _extract_failed_jobs(jobs)
        failing_steps = _extract_failing_steps(failed_jobs)

        hints: list[str] = []
        for job in failed_jobs:
            hints.extend(_infer_fix_hints(job))
        hints = _dedupe(hints)
        summary_markdown = _build_summary_markdown(run_payload, failed_jobs, failing_steps, hints)

        return {
            "status": "ok",
            "run_id": run_id,
            "workflow": run_payload.get("name"),
            "run_url": run_payload.get("html_url"),
            "failed_jobs_count": len(failed_jobs),
            "failed_jobs": [
                {
                    "name": job.get("name"),
                    "conclusion": job.get("conclusion"),
                    "url": job.get("html_url"),
                }
                for job in failed_jobs
            ],
            "failing_steps": failing_steps,
            "suggested_fixes": hints,
            "summary_markdown": summary_markdown,
        }

    def _github_comment_pr_or_issue(self, args: dict[str, Any]) -> dict[str, Any]:
        issue_number = args.get("issue_number") or args.get("pr_number")
        body = str(args.get("body", "")).strip()
        if not issue_number:
            return {
                "status": "error",
                "message": "issue_number or pr_number is required for comment_pr_or_issue",
            }
        if not body:
            return {
                "status": "error",
                "message": "body is required for comment_pr_or_issue",
            }

        payload = self._github_request(
            "POST",
            f"/issues/{issue_number}/comments",
            body={"body": body},
        )
        return {
            "status": "ok",
            "issue_number": issue_number,
            "comment_url": payload.get("html_url"),
        }

    def _github_org_request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.github_org:
            raise ValueError("Missing GITHUB_ORG env var.")
        full_path = f"/orgs/{self.github_org}{path}"
        if query:
            full_path = f"{full_path}?{parse.urlencode(query)}"
        url = f"{self.github_api_url}{full_path}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib_request.Request(
            url=url,
            data=payload,
            headers=self._github_headers(),
            method=method,
        )
        with urllib_request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)

    def _github_list_org_repos(self, args: dict[str, Any]) -> dict[str, Any]:
        query: dict[str, Any] = {
            "per_page": int(args.get("per_page", 100)),
            "type": str(args.get("type", "all")),
        }
        data = self._github_org_request("GET", "/repos", query=query)
        repos = data if isinstance(data, list) else data.get("repositories", [])
        return {
            "status": "ok",
            "org": self.github_org,
            "count": len(repos),
            "repos": [r.get("full_name") for r in repos],
        }

    def _github_list_org_workflow_runs(self, args: dict[str, Any]) -> dict[str, Any]:
        """List workflow runs across ALL repos in the org that match optional repo filter."""
        repos_data = self._github_list_org_repos({})
        repos = [r for r in repos_data.get("repos", []) if isinstance(r, str)]

        repo_filter = str(args.get("repo", "")).lower()
        if repo_filter:
            repos = [r for r in repos if repo_filter in r.lower()]

        all_runs: list[dict[str, Any]] = []
        original_repo = self.github_repository
        try:
            for repo_full in repos[:20]:  # cap at 20 repos to avoid flooding
                self.github_repository = repo_full
                runs_result = self._github_list_workflow_runs(
                    {"per_page": int(args.get("per_page", 5)), "status": args.get("status", "")}
                )
                all_runs.extend(runs_result.get("workflow_runs", []))
        finally:
            self.github_repository = original_repo

        # sort by created_at desc, newest first
        all_runs.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return {
            "status": "ok",
            "org": self.github_org,
            "count": len(all_runs),
            "workflow_runs": all_runs[: int(args.get("per_page", 20))],
        }

    # ------------------------------------------------------------------ #
    #  Kubernetes helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_k8s_client() -> Any:
        """Load kubernetes SDK and configure from KUBECONFIG / in-cluster."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config  # type: ignore[import]
        except ImportError:
            raise RuntimeError("kubernetes SDK not installed — run: pip install kubernetes")

        kubeconfig = os.getenv("KUBECONFIG")
        try:
            if kubeconfig:
                k8s_config.load_kube_config(config_file=kubeconfig)
            else:
                k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()  # fallback to default ~/.kube/config
        return k8s_client

    def _k8s_get_pods(self, args: dict[str, Any]) -> dict[str, Any]:
        k8s = self._load_k8s_client()
        namespace = str(args.get("namespace", "iot-agent"))
        label_selector = str(args.get("label_selector", ""))
        v1 = k8s.CoreV1Api()
        pod_list = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector or None)
        pods = [
            {
                "name": p.metadata.name,
                "phase": p.status.phase,
                "ready": all(
                    c.ready for c in (p.status.container_statuses or [])
                ),
                "restarts": sum(
                    c.restart_count for c in (p.status.container_statuses or [])
                ),
                "node": p.spec.node_name,
            }
            for p in pod_list.items
        ]
        return {"status": "ok", "namespace": namespace, "count": len(pods), "pods": pods}

    def _k8s_get_events(self, args: dict[str, Any]) -> dict[str, Any]:
        k8s = self._load_k8s_client()
        namespace = str(args.get("namespace", "iot-agent"))
        v1 = k8s.CoreV1Api()
        event_list = v1.list_namespaced_event(namespace=namespace)
        events = [
            {
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "object": e.involved_object.name if e.involved_object else None,
                "last_time": str(e.last_timestamp) if e.last_timestamp else None,
            }
            for e in sorted(event_list.items, key=lambda x: str(x.last_timestamp or ""), reverse=True)[:30]
        ]
        return {"status": "ok", "namespace": namespace, "count": len(events), "events": events}

    def _k8s_get_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        k8s = self._load_k8s_client()
        namespace = str(args.get("namespace", "iot-agent"))
        pod_name = str(args.get("pod"))
        if not pod_name:
            return {"status": "error", "message": "pod arg is required for get_logs"}
        container = args.get("container")
        tail = int(args.get("tail_lines", 100))
        v1 = k8s.CoreV1Api()
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container or None,
            tail_lines=tail,
        )
        return {
            "status": "ok",
            "namespace": namespace,
            "pod": pod_name,
            "container": container,
            "tail_lines": tail,
            "logs": logs,
        }

    def _k8s_get_rollout_status(self, args: dict[str, Any]) -> dict[str, Any]:
        k8s = self._load_k8s_client()
        namespace = str(args.get("namespace", "iot-agent"))
        name = args.get("name")
        apps_v1 = k8s.AppsV1Api()

        def _dep_status(dep: Any) -> dict[str, Any]:
            spec = dep.spec
            status = dep.status
            return {
                "name": dep.metadata.name,
                "desired": spec.replicas or 0,
                "ready": status.ready_replicas or 0,
                "available": status.available_replicas or 0,
                "up_to_date": status.updated_replicas or 0,
            }

        if name:
            dep = apps_v1.read_namespaced_deployment(name=str(name), namespace=namespace)
            return {"status": "ok", "namespace": namespace, "deployments": [_dep_status(dep)]}

        dep_list = apps_v1.list_namespaced_deployment(namespace=namespace)
        return {
            "status": "ok",
            "namespace": namespace,
            "count": len(dep_list.items),
            "deployments": [_dep_status(d) for d in dep_list.items],
        }

    # ------------------------------------------------------------------ #
    #  Run dispatcher                                                      #
    # ------------------------------------------------------------------ #

    def run(self, tool: str, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {
                "tool": tool,
                "action": action,
                "args": args,
                "status": "simulated",
                "dry_run": True,
                "message": "MCP adapter dry-run: no real external API call performed.",
            }

        try:
            if tool == "github":
                result = self._dispatch_github(action, args)
            elif tool == "kubernetes":
                result = self._dispatch_kubernetes(action, args)
            elif tool == "loki":
                return self._loki.run(action, args, dry_run=False)
            elif tool == "prometheus":
                return self._prometheus.run(action, args, dry_run=False)
            else:
                result = {
                    "status": "error",
                    "message": f"Unknown tool: {tool}. Available: github, kubernetes, loki, prometheus.",
                }
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        result.update({"tool": tool, "action": action, "args": args, "dry_run": False})
        return result

    def _dispatch_github(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if action == "list_workflow_runs":
                return self._github_list_workflow_runs(args)
            elif action == "list_org_workflow_runs":
                return self._github_list_org_workflow_runs(args)
            elif action == "list_org_repos":
                return self._github_list_org_repos(args)
            elif action == "list_jobs_for_run":
                return self._github_list_jobs_for_run(args)
            elif action == "get_run_summary":
                return self._github_get_run_summary(args)
            elif action == "comment_pr_or_issue":
                return self._github_comment_pr_or_issue(args)
            else:
                return {"status": "error", "message": f"Unsupported github action: {action}"}
        except (ValueError, error.HTTPError, error.URLError, TimeoutError) as exc:
            return {"status": "error", "message": str(exc)}

    def _dispatch_kubernetes(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if action == "get_pods":
                return self._k8s_get_pods(args)
            elif action == "get_events":
                return self._k8s_get_events(args)
            elif action == "get_logs":
                return self._k8s_get_logs(args)
            elif action == "get_rollout_status":
                return self._k8s_get_rollout_status(args)
            else:
                return {"status": "error", "message": f"Unsupported kubernetes action: {action}"}
        except RuntimeError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": f"Kubernetes error: {exc}"}
