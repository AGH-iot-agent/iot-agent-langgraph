"""
AI Security Layer
=================
Defends the agent against six threat classes:

  1. prompt_injection   – malicious instructions embedded in user input or tool outputs
  2. jailbreak          – attempts to bypass safety guardrails (DAN, developer-mode, etc.)
  3. secret_leakage     – API keys / tokens / passwords appearing in LLM output
  4. pii_leakage        – personal data (email, phone, SSN, CC) in LLM output
  5. data_exfiltration  – tool args containing unauthorised external URLs
  6. malicious_tool_call – dangerous shell / SQL / k8s patterns in tool arguments

Usage
-----
    from devops_agent.security import security_layer, SecurityScanResult

    result = security_layer.scan_input(user_text, source="user_request")
    if not result.is_safe:
        raise ValueError(result.violations[0].description)

    clean_output = security_layer.scan_output(llm_text).sanitized_text
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class SecurityViolation:
    threat_type: str   
    severity: str      
    description: str
    matched_pattern: str = ""
    field: str = "" 


@dataclass
class SecurityScanResult:
    is_safe: bool
    violations: list[SecurityViolation] = field(default_factory=list)
    sanitized_text: str = ""

    def has_severity(self, *levels: str) -> bool:
        return any(v.severity in levels for v in self.violations)


_PROMPT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?previous\s+instructions",        "high"),
    (r"disregard\s+(all\s+)?previous\s+instructions",     "high"),
    (r"forget\s+(all\s+)?previous\s+instructions",        "high"),
    (r"override\s+(all\s+)?previous\s+instructions",      "high"),
    (r"new\s+instruction[s]?\s*:",                        "medium"),
    (r"\[new\s+instruction[s]?\]",                        "high"),
    (r"\[SYSTEM\]",                                       "high"),
    (r"<\|.*?system.*?\|>",                               "high"),
    (r"```\s*system\s*\n",                                "high"),
    (r"you\s+are\s+now\s+(a|an)\s+",                     "medium"),
    (r"from\s+now\s+on\s+you\s+(are|will)\s+",           "high"),
    (r"your\s+new\s+(role|persona|task|purpose)\s+is",   "high"),
    (r"(print|reveal|show|repeat|output)\s+(your\s+)?(system\s+prompt|instructions|initial\s+prompt|configuration)", "high"),
    (r"what\s+(are\s+)?(your\s+)?(instructions|system\s+prompt|rules)",  "medium"),
    (r"assistant\s*:\s*i\s+will\s+now",                  "high"),
    (r"\[\s*system\s*\]",                                 "high"),
    (r"\[INST\].*?\[/INST\]",                             "high"),
]

# (regex, severity)
_JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    (r"\bDAN\b",                                          "high"),
    (r"do\s+anything\s+now",                              "high"),
    (r"jailbreak",                                        "high"),
    (r"developer\s+mode",                                 "medium"),
    (r"maintenance\s+mode",                               "medium"),
    (r"god\s+mode",                                       "high"),
    (r"unrestricted\s+mode",                              "high"),
    (r"no\s+(restrictions|limits|rules|guidelines)",      "medium"),
    (r"safety\s+(off|disabled|bypassed|override)",        "high"),
    (r"bypass\s+(safety|restrictions|filters|guardrails)","high"),
    (r"disable\s+(safety|restrictions|filters|guardrails)","high"),
    (r"you\s+have\s+no\s+(restrictions|rules|guidelines)","high"),
    (r"ethical\s+guidelines?\s+(don.?t|do\s+not)\s+apply","high"),
    (r"pretend\s+(you\s+are|to\s+be)\s+.{0,40}(unethical|evil|hacker|malicious)", "high"),
]

_SECRET_PATTERNS: list[tuple[str, str, str]] = [
    (r"(?i)(api[_\-]?key|apikey)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}", "secret_leakage", "high"),
    (r"(?i)(secret[_\-]?key|secretkey)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}", "secret_leakage", "high"),
    (r"(?i)(access[_\-]?token|accesstoken)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{20,}", "secret_leakage", "high"),
    (r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{8,}", "secret_leakage", "high"),
    (r"(?i)(private[_\-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{20,}", "secret_leakage", "critical"),
    (r"-----BEGIN\s+(RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE KEY-----", "secret_leakage", "critical"),
    (r"\bAKIA[0-9A-Z]{16}\b",                                         "secret_leakage", "critical"),   
    (r"\bsk-[A-Za-z0-9]{32,}\b",                                          "secret_leakage", "critical"),   # OpenAI
    (r"\bghp_[A-Za-z0-9]{36}\b",                                      "secret_leakage", "critical"),   # GitHub PAT
    (r"\bghs_[A-Za-z0-9]{36}\b",                                      "secret_leakage", "high"),       # GitHub app token
    (r"\bxoxb-[0-9]{11}-[0-9]{11}-[A-Za-z0-9]{24}\b",                "secret_leakage", "critical"),   # Slack bot
    (r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}", "secret_leakage", "high"),
    (r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{30,}", "secret_leakage", "medium"),
    (r"(?i)postgres(ql)?://[^@\s]+:[^@\s]+@",   "secret_leakage", "critical"),
    (r"(?i)mysql://[^@\s]+:[^@\s]+@",            "secret_leakage", "critical"),
    (r"(?i)mongodb(\+srv)?://[^@\s]+:[^@\s]+@",  "secret_leakage", "critical"),
    (r"(?i)redis://:[^@\s]+@",                   "secret_leakage", "high"),
]

_PII_PATTERNS: list[tuple[str, str, str]] = [
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "email",       "medium"),
    (r"\b(\+\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b",       "phone",       "medium"),
    (r"\b\d{3}[\s\-]?\d{2}[\s\-]?\d{4}\b",                                    "ssn",         "high"),
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b",                                           "credit_card", "critical"),  # Visa
    (r"\b5[1-5][0-9]{14}\b",                                                   "credit_card", "critical"),  # Mastercard
    (r"\b3[47][0-9]{13}\b",                                                    "credit_card", "critical"),  # Amex
]

# Requested actions that must never be planned or executed (mirrors
# devops_agent.nodes.critic.FORBIDDEN_ACTIONS). Matched in scan_input so a
# github_issue that *asks* for delete_namespace is blocked before planner/executor
# invent a Helm diagnosis. Both single_agent and multi_agent share this gate.
_FORBIDDEN_ACTION_REQUEST_PATTERNS: list[tuple[str, str]] = [
    (r"\bdelete_namespace\b", "delete_namespace"),
    (r"kubectl\s+delete\s+n(s|amespace)\b", "delete_namespace"),
    (r"delete\s+the\s+whole\s+namespace", "delete_namespace"),
    (r"delete\s+(the\s+)?namespace\s+iotag-", "delete_namespace"),
    (r"run\s+delete[_\s]namespace", "delete_namespace"),
    (r"\bforce_push_main\b", "force_push_main"),
    (r"\bcluster_admin_apply\b", "cluster_admin_apply"),
    (r"\bdelete_persistentvolumeclaim\b", "delete_persistentvolumeclaim"),
    (r"\bdelete_pvc\b", "delete_pvc"),
    (r"\bpatch_node\b", "patch_node"),
    (r"\btaint_node\b", "taint_node"),
]


_DANGEROUS_ARG_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-[rf]{1,2}",                    "critical"),
    (r"kubectl\s+delete\s+all",             "critical"),
    (r"kubectl\s+delete\s+namespace",       "critical"),
    (r"kubectl\s+delete\s+cluster",         "critical"),
    (r"\bDROP\s+TABLE\b",                   "critical"),
    (r"\bDROP\s+DATABASE\b",                "critical"),
    (r"\bTRUNCATE\s+TABLE\b",              "high"),
    (r";\s*DROP\b",                         "critical"),   # SQL injection trailer
    (r"\|\s*bash\b",                        "critical"),   # pipe to bash
    (r"\|\s*sh\b",                          "critical"),
    (r"\beval\s*\(",                        "high"),
    (r"\bexec\s*\(",                        "high"),
    (r"\b__import__\s*\(",                  "high"),
    (r"\bos\.system\s*\(",                  "high"),
    (r"\.\./\.\./\.\.",                     "high"),       # path traversal
    (r"curl\s+.{0,100}\|\s*(bash|sh)\b",   "critical"),   # curl|bash
    (r"wget\s+.{0,100}\|\s*(bash|sh)\b",   "critical"),
    (r"\bformat\s+[Cc]:\b",                "critical"),   # Windows disk format
]

_ALLOWED_HOSTS_RE = re.compile(
    r"https?://(localhost|127\.0\.0\.1|"
    r"[\w\-]+\.svc(\.cluster\.local)?|"          # Kubernetes internal
    r"[\w\-]+\.default(\.svc)?|"
    r"prometheus\.|grafana\.|loki\.|jaeger\.|"
    r"api\.github\.com|github\.com|"
    r"openai\.com|api\.openai\.com"
    r")",
    re.IGNORECASE,
)
_ANY_URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)

_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset({
    "k8s_get_pods",
    "k8s_describe_pod",
    "k8s_get_pod_logs",
    "k8s_get_events",
    "k8s_get_pod_events",
    "k8s_restart_deployment",
    "k8s_helm_template",
    "k8s_apply_dryrun",
    "prom_query",
    "prom_query_range",
    "loki_get_logs",
    "gh_list_failed_runs",
    "gh_get_job_logs",
    "gh_get_workflow_runs",
    "gh_get_file_content",
    "gh_create_pr",
})

class SecurityLayer:
    """
    All six threat classes are covered by four scan methods:

    - scan_input()      → prompt_injection + jailbreak
    - scan_output()     → secret_leakage + pii_leakage
    - scan_tool_call()  → malicious_tool_call + data_exfiltration
    - scan_tool_output()→ indirect prompt_injection (external data → LLM context)
                          + secret_leakage in tool responses
    """


    def scan_input(self, text: str, source: str = "user_input") -> SecurityScanResult:
        """
        Scan incoming text (user request or external trigger) for
        prompt-injection, jailbreak, and explicit forbidden-action requests.
        """
        violations: list[SecurityViolation] = []

        for pattern, severity in _PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                violations.append(SecurityViolation(
                    threat_type="prompt_injection",
                    severity=severity,
                    description=f"Prompt injection pattern detected in {source}",
                    matched_pattern=pattern,
                    field=source,
                ))
                logger.warning(
                    "[SECURITY] prompt_injection (%s) in %s | pattern=%s",
                    severity, source, pattern,
                )

        for pattern, severity in _JAILBREAK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                violations.append(SecurityViolation(
                    threat_type="jailbreak",
                    severity=severity,
                    description=f"Jailbreak attempt detected in {source}",
                    matched_pattern=pattern,
                    field=source,
                ))
                logger.warning(
                    "[SECURITY] jailbreak (%s) in %s | pattern=%s",
                    severity, source, pattern,
                )

        for pattern, action_name in _FORBIDDEN_ACTION_REQUEST_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(SecurityViolation(
                    threat_type="forbidden_action",
                    severity="critical",
                    description=f"Forbidden action requested: {action_name}",
                    matched_pattern=action_name,
                    field=source,
                ))
                logger.error(
                    "[SECURITY] forbidden_action (%s) in %s",
                    action_name, source,
                )

        is_safe = not any(v.severity in ("high", "critical") for v in violations)
        sanitized = text if is_safe else self._neutralise_injections(text)
        return SecurityScanResult(is_safe=is_safe, violations=violations, sanitized_text=sanitized)

    def scan_output(self, text: str) -> SecurityScanResult:
        """
        Scan LLM output for secret-leakage and PII before it is returned
        to the caller. Returns a sanitised version with sensitive values
        replaced by redaction tokens.
        """
        violations: list[SecurityViolation] = []
        sanitized = text

        for pattern, threat_type, severity in _SECRET_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                violations.append(SecurityViolation(
                    threat_type=threat_type,
                    severity=severity,
                    description="Potential secret/credential detected in LLM output",
                    matched_pattern=pattern,
                    field="llm_output",
                ))
                logger.warning("[SECURITY] %s (%s) in llm_output", threat_type, severity)
                sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE | re.DOTALL)

        for pattern, pii_type, severity in _PII_PATTERNS:
            if re.search(pattern, text):
                violations.append(SecurityViolation(
                    threat_type="pii_leakage",
                    severity=severity,
                    description=f"PII ({pii_type}) detected in LLM output",
                    matched_pattern=pattern,
                    field="llm_output",
                ))
                logger.warning("[SECURITY] pii_leakage/%s (%s) in llm_output", pii_type, severity)
                sanitized = re.sub(pattern, f"[{pii_type.upper()}_REDACTED]", sanitized)

        is_safe = not any(v.severity in ("high", "critical") for v in violations)
        return SecurityScanResult(is_safe=is_safe, violations=violations, sanitized_text=sanitized)

    def scan_tool_call(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityScanResult:
        """
        Validate a pending tool call:
          - tool name must be in the allowlist
          - argument values must not contain dangerous shell/SQL/k8s patterns
          - argument values must not reference unauthorised external URLs (exfiltration)
        """
        violations: list[SecurityViolation] = []
        args_str = str(tool_args)

        if tool_name not in _ALLOWED_TOOL_NAMES:
            violations.append(SecurityViolation(
                threat_type="malicious_tool_call",
                severity="critical",
                description=f"Tool '{tool_name}' is not in the allowed tools list",
                field="tool_name",
            ))
            logger.error("[SECURITY] malicious_tool_call — unknown tool '%s'", tool_name)

        for pattern, severity in _DANGEROUS_ARG_PATTERNS:
            if re.search(pattern, args_str, re.IGNORECASE | re.DOTALL):
                violations.append(SecurityViolation(
                    threat_type="malicious_tool_call",
                    severity=severity,
                    description=f"Dangerous pattern in args for tool '{tool_name}'",
                    matched_pattern=pattern,
                    field="tool_args",
                ))
                logger.error(
                    "[SECURITY] malicious_tool_call (%s) in args of '%s' | pattern=%s",
                    severity, tool_name, pattern,
                )

        for url in _ANY_URL_RE.findall(args_str):
            if not _ALLOWED_HOSTS_RE.match(url):
                violations.append(SecurityViolation(
                    threat_type="data_exfiltration",
                    severity="high",
                    description=f"Unauthorised external URL in args for '{tool_name}': {url[:80]}",
                    matched_pattern=url[:80],
                    field="tool_args",
                ))
                logger.error(
                    "[SECURITY] data_exfiltration — unauthorised URL in '%s': %s",
                    tool_name, url[:80],
                )

        is_safe = not any(v.severity in ("high", "critical") for v in violations)
        return SecurityScanResult(is_safe=is_safe, violations=violations, sanitized_text=args_str)

    def scan_tool_output(self, tool_name: str, output: str) -> SecurityScanResult:
        """
        Scan the raw string output from a tool call for:
          - indirect prompt injection (malicious instructions in GitHub issues,
            Loki log lines, Prometheus labels, etc.)
          - secrets that external systems might accidentally return
        """
        injection_result = self.scan_input(output, source=f"tool_output:{tool_name}")
        secret_result = self.scan_output(output)

        combined = injection_result.violations + secret_result.violations
        is_safe = injection_result.is_safe and secret_result.is_safe
        sanitized = secret_result.sanitized_text  # secrets redacted; injection already logged

        return SecurityScanResult(is_safe=is_safe, violations=combined, sanitized_text=sanitized)

    @staticmethod
    def _neutralise_injections(text: str) -> str:
        """Replace injection trigger phrases with a neutral placeholder."""
        sanitized = text
        for pattern, _ in _PROMPT_INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "[BLOCKED_INJECTION]", sanitized, flags=re.IGNORECASE | re.DOTALL)
        for pattern, _ in _JAILBREAK_PATTERNS:
            sanitized = re.sub(pattern, "[BLOCKED_JAILBREAK]", sanitized, flags=re.IGNORECASE | re.DOTALL)
        for pattern, _ in _FORBIDDEN_ACTION_REQUEST_PATTERNS:
            sanitized = re.sub(pattern, "[BLOCKED_FORBIDDEN_ACTION]", sanitized, flags=re.IGNORECASE)
        return sanitized

    @staticmethod
    def violations_to_dicts(violations: list[SecurityViolation]) -> list[dict[str, str]]:
        """Serialise violations so they can be stored in AgentState."""
        return [
            {
                "threat_type":     v.threat_type,
                "severity":        v.severity,
                "description":     v.description,
                "matched_pattern": v.matched_pattern,
                "field":           v.field,
            }
            for v in violations
        ]

security_layer = SecurityLayer()
