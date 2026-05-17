import logging
from pathlib import Path

logger = logging.getLogger(__name__)

prompt_dict: dict[str, str] = {}

for prompt_file in Path(__file__).parent.glob("*.md"):
	name = prompt_file.stem
	prompt_dict[name] = prompt_file.read_text(encoding="utf-8").strip()

# Mapowanie event_kind -> nazwa sekcji w PromptRegistry (None = brak A/B dla tej sekcji)
_PROMPTOPS_SECTION: dict[str, str] = {
	"github_ci_failure": "ci_fixer",
	"github_pr_build_failure": "ci_fixer",
}


def _build_system_prompt(event_kind: str, trace_id: str | None = None) -> str:
	"""Build a minimal, event-specific system prompt to stay within TPM limits.

	Only include prompt sections that are relevant to the event kind.
	Full base (all sections) ≈ 8 000 tokens — too expensive to send every call.

	Jeśli w PromptRegistry są zarejestrowane warianty dla danego event_kind,
	sekcja CI-fixer jest podmieniana przez wybrany wariant (A/B testing).
	trace_id służy do deterministycznego przypisania wariantu.
	"""
	p = prompt_dict.get

	core = p("base_rules", "") + "\n\n" + p("project_overview", "") + "\n\n"

	match event_kind:
		case "github_ci_failure" | "github_pr_build_failure":
			ci_section = _get_ab_section("ci_fixer", trace_id) or (
				p("cicd", "") + "\n\n"
				+ p("github_action", "") + "\n\n"
				+ p("github_action_fix", "") + "\n\n"
				+ p("github_ci", "") + "\n\n"
				+ p("helm_values", "")
			)
			return core + ci_section
		case "github_pr":
			return (
				core
				+ p("github_action", "") + "\n\n"
				+ p("github_ci", "")
			)
		case "github_issue":
			return core + p("github_issue", "")
		case "k3s_alert":
			return core + p("k8s", "") + "\n\n" + p("istio", "")
		case "request_rate_spike" | "high_cpu_usage" | "high_http_latency" | "high_error_rate" | "loki_error_spike":
			return core + p("observability", "") + "\n\n" + p("scalability", "")
		case _:
			return core + p("k8s", "")


def _get_ab_section(
	section_name: str, trace_id: str | None
) -> str | None:
	"""Zwraca treść sekcji z PromptRegistry, jeśli warianty są zarejestrowane.

	Zwraca None jeśli brak wariantów – fallback do statycznych plików .md.
	"""
	try:
		from devops_agent.promptops.registry import get_registry
		registry = get_registry()
		variants = registry.list_variants(section_name)
		if not variants:
			return None
		content, variant_id = registry.get_prompt(section_name, trace_id)
		logger.debug("A/B prompt: section=%s variant=%s trace=%s", section_name, variant_id, trace_id)
		return content
	except Exception as exc:
		logger.warning("PromptRegistry niedostępny (%s), używam statycznego prompta", exc)
		return None
