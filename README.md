# iot-agent-langgraph

Kod agenta DevOps w oparciu o LangGraph.

## Co jest zaimplementowane

- `planner`: buduje plan krokow na podstawie zadania.
- `critic`: sprawdza plan pod katem ryzyka i polityk.
- `executor`: wykonuje kroki w trybie `dry_run` przez adapter MCP.
- API HTTP (`FastAPI`) do uruchamiania workflow.

## Struktura

- `src/devops_agent/graph.py` - definicja i kompilacja grafu.
- `src/devops_agent/nodes/planner.py` - planner.
- `src/devops_agent/nodes/critic.py` - krytyk.
- `src/devops_agent/nodes/executor.py` - executor.
- `src/devops_agent/main.py` - endpointy `/health` i `/run`.

## Uruchomienie lokalne

```bash
cd iot-agent-langgraph
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn devops_agent.main:app --host 0.0.0.0 --port 8123
```

Przyklad requestu:

```bash
curl -X POST http://localhost:8123/run \
  -H 'Content-Type: application/json' \
  -d '{"request":"Sprawdz rollout deploymentu iot-agent-gateway-api", "dry_run": true}'
```

## GitHub Actions: odczyt jobow i podsumowanie awarii

Ustaw zmienne srodowiskowe:

```bash
export GITHUB_TOKEN=<token_z_uprawnieniami_repo_actions>
export GITHUB_REPOSITORY=<owner/repo>
# opcjonalnie dla GH Enterprise:
export GITHUB_API_URL=https://api.github.com
```

Zakres tokena dla klasycznego PAT: `repo` oraz `workflow`.
Dla fine-grained token: co najmniej read dla Actions i write dla Pull requests/Issues (jesli agent ma komentowac).

Przyklad analizy ostatniego failujacego runa i komentarza do PR/issue:

```bash
curl -X POST http://localhost:8123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "request": "Przeanalizuj GitHub Actions CI i podsumuj co sie wysypalo",
    "dry_run": false,
    "context": {
      "run_id": 123456789,
      "issue_number": 42
    }
  }'
```

Uwagi:
- Gdy `run_id` nie jest podane, agent probuje wybrac ostatni run z bledem.
- Wynik `final_summary` zawiera markdown z lista failujacych jobow, pierwszymi padnietymi krokami i sugerowanymi poprawkami.
- W trybie `dry_run=true` agent tylko symuluje kroki i nic nie wysyla do GitHuba.
