from pathlib import Path

prompt_dict: dict[str, str] = {}

for prompt_file in Path(__file__).parent.glob("*.md"):
	name = prompt_file.stem
	prompt_dict[name] = prompt_file.read_text(encoding="utf-8").strip()

def _build_system_prompt(event_kind: str) -> str:

	base_rules = ""

	base_rules += prompt_dict.get("base_rules", "") 	  + "\n\n"
	base_rules += prompt_dict.get("project_overview", "") + "\n\n"
	base_rules += prompt_dict.get("istio", "") 		      + "\n\n"
	base_rules += prompt_dict.get("k8s", "") 	  		  + "\n\n"

	match event_kind:
		case "github_issue":
			return base_rules + "\n\n" + prompt_dict.get("github_issue", "")
		case "github_pr":
			return base_rules + "\n\n" + prompt_dict.get("github_ci", "")
		case "github_ci_failure" | "github_pr_build_failure":
			return base_rules + "\n\n" + prompt_dict.get("github_ci", "")
		case "k3s_alert":
			return base_rules + "\n\n" + prompt_dict.get("github_ci", "")
		case _:
			return base_rules
