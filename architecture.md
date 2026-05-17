# Architektura agenta DevOps (LangGraph + MCP)

## Cele

- Automatyzacja rutynowych operacji DevOps.
- Bezpieczne wykonywanie zmian z kontrola uprawnien.
- Pelny audyt decyzji i dzialan agenta.

## Warstwy logiczne

1. Wejscie:
- Trigger z GitHub Actions, API lub manualny prompt.

2. Orkiestracja (LangGraph):
- Planowanie krokow.
- Decyzja read-only vs write.
- Walidacja warunkow bezpieczenstwa.

3. Narzedzia (MCP):
- GitHub MCP: PR, komentarze, statusy.
- Kubernetes MCP: kubectl-style read/write.
- Helm MCP: dry-run, upgrade, rollback.

4. Policy engine:
- Allowlist narzedzi i argumentow.
- Ograniczenie namespace i typow zasobow.
- Blokada komend destrukcyjnych bez approval.

5. Obserwowalnosc:
- Log decyzji, argumentow i wynikow.
- Metryki: czas planu, sukces/blad, retry count.

## Stany workflow

- `ingest` -> `plan` -> `simulate` -> `approve` -> `apply` -> `verify` -> `report`

## Kontrakty krokow write

Dla kazdego kroku write zdefiniuj:
- precondition
- command preview
- dry-run output
- rollback strategy
- postcondition

## ADR-y, ktore warto miec na start

- ADR-001: model tozsamosci i sekrety (OIDC/GitHub App)
- ADR-002: granice uprawnien K8s
- ADR-003: poziom autonomii agenta (auto-apply vs human-in-the-loop)
- ADR-004: format audytu i retencja

## Drugi set architektury: Evaluation & Quality

Cel tej warstwy: mierzyc jakosc agenta i koszt rozwiazania problemu end-to-end,
zamiast oceniac tylko fakt publikacji komentarza/PR.

### Komponenty

1. `execution telemetry` (node executor):
- input_tokens, output_tokens
- liczba rund tool-calling
- liczba krokow planu

2. `dispatch telemetry` (watchdog):
- trace_id dla kazdego eventu
- czas startu i czas zamkniecia obslugi eventu
- MTTR na poziomie eventu

3. `quality outcomes`:
- validation_passed (czy sandbox walidacja przeszla)
- pr_created (czy utworzono fix PR)
- revision_count (ile razy krytyk odrzucil plan)

4. `api reporting`:
- endpoint listy eventow z metrykami
- endpoint agregatow (srednie i rate)

### Przeplyw metryk

`watchdog dispatch start` -> `planner/critic/executor` -> `ci_fixer/validator` -> `watchdog dispatch end` -> `metrics store`

### KPI do oceny agenta

- `MTTR` (Mean Time To Resolve):
	sredni czas od pojawienia sie eventu do final_summary/PR.

- `Total tokens per incident`:
	input_tokens + output_tokens dla pojedynczego eventu.

- `Steps to resolve`:
	liczba krokow planu + rund tool-calling.

- `Validation pass rate`:
	odsetek eventow, gdzie sandbox walidacja przeszla.

- `PR creation rate`:
	odsetek eventow konczacych sie utworzeniem fix PR.

- `Revision pressure`:
	srednia liczba poprawek planu (revision_count).

### SLO (proponowane)

- GitHub Issue events: MTTR < 90s, input_tokens < 8k
- GitHub PR/CI failures: MTTR < 120s, input_tokens < 12k
- Prometheus/Loki alerts: MTTR < 60s, input_tokens < 10k
