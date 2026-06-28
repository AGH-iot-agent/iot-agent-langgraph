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

## ADR-001: Model tozsamosci i sekrety

Status: accepted

Kontekst:
Agent musi uwierzytelnic sie wobec GitHub API i Kubernetes API.
Sekrety nie moga byc hardcodowane w kodzie ani obrazach.

Decyzja:
- GitHub: PAT (Personal Access Token) przekazywany przez zmienna GITHUB_TOKEN
  (zakres: repo + workflow + read:org dla fine-grained: Actions read, Pull requests write).
- Kubernetes: ServiceAccount w namespace iotag-dev z RoleBinding ograniczonym
  do zasobow wymienionych w ADR-002.
- Sekrety montowane jako zmienne srodowiskowe przez K8s Secret / .env lokalnie.
- Rotacja: tokeny wazne max 90 dni, automatyczne alerty o wygasnieciu.

Konsekwencje:
- Brak OIDC/GitHub App w MVP (wymaga dodatkowej infrastruktury).
- Uproszczona konfiguracja lokalna przez .env.

## ADR-002: Granice uprawnien Kubernetes

Status: accepted

Kontekst:
Agent moze wykonywac operacje kubectl-style. Zbyt szeroki zakres uprawnien
stwarza ryzyko przypadkowego zniszczenia zasobow.

Decyzja:
Allowlist zasobow (read + write):
  Read (zawsze dozwolone):
  - pods, deployments, replicasets, services, configmaps, secrets (tylko listing)
  - events, nodes (read-only)
  - horizontalpodautoscalers

  Write (tylko za podmiotem dry_run=False i bez blokad krytyka):
  - deployments: patch (restart_deployment, scale)
  - horizontalpodautoscalers: patch (scale replicas)
  - configmaps: update (tylko w namespace iotag-dev)

  Bezwzglednie zakazane (niezaleznie od dry_run):
  - delete namespace
  - cluster-admin verby
  - patch node / taint node
  - delete PersistentVolumeClaim

Namespace scope: tylko iotag-dev (KUBERNETES_NAMESPACE env var).

Konsekwencje:
- Skalowanie i restart wdrozenia moze byc auto-apply (ADR-003).
- Zadne operacje destrukcyjne nie wymagaja dodatkowego zabezpieczenia –
  sa po prostu zablokowane na poziomie guardrails.

## ADR-003: Poziom autonomii agenta (auto-apply vs human-in-the-loop)

Status: accepted

Kontekst:
Agent moze dzialac w trybie dry_run (tylko symulacja) lub w trybie live
(faktyczne zmiany). Dla pracy magisterskiej kluczowe jest zmierzenie
efektywnosci obu trybów i okreslenie granicy bezpiecznej automatyzacji.

Decyzja (dwupoziomowy model):

Poziom 1 – Auto-apply (dry_run=False, bez zatwierdzenia):
Akcje bezpieczne, odwracalne, o niskim ryzyku w namespace iotag-dev:
  - restart_deployment (rollout restart)
  - scale_deployment (tylko zwiekszenie replik, max 2x obecna wartosc)
  - patch_hpa_min_replicas (tylko w gore, max +2)
  Warunek wstepny: krytyk musi przejsc (critique_passed=True) i akcja
  musi byc na allowliscie WRITE_SAFE_ACTIONS w guardrails.py.

Poziom 2 – Human-in-the-loop (zawsze dry_run=True + komentarz na PR/Issue):
Akcje z wyzszym ryzykiem lub trudno odwracalne:
  - zmiana Helm values (upgrade chartu)
  - zmiana ConfigMap / Secret
  - kazda operacja poza namespace iotag-dev
  - tworzenie lub usuwanie zasobow K8s
  Agent produkuje plan + dry-run output i publikuje jako komentarz.
  Czlowiek zatwierdza reczne zastosowanie lub merge PR.

Eksperymenty:
Kampania testowa uruchamia scenariusze w obu trybach i porownuje:
  - MTTR (auto-apply typowo krotszy)
  - ryzyko bledu (auto-apply moze propagowac bledna diagnoze)
  - pokrycie (co mozna autoaplikowac vs co wymaga ludzkiego oka)

Konsekwencje:
- Domyslny tryb agenta to dry_run=True (bezpieczny start).
- Aktywacja auto-apply wymaga jawnego AGENT_AUTOAPPLY=true w srodowisku
  + KUBERNETES_NAMESPACE ustawionego na iotag-dev (nie prod).
- Dla pracy magisterskiej: eksperymenty z auto-apply tylko w klastrze dev/sbx.

## ADR-004: Format audytu i retencja

Status: accepted

Kontekst:
Kazda decyzja agenta musi byc audytowalna – ktory model, jaki plan,
jakie narzedzia, jaki wynik. Niezbedne dla analizy w pracy magisterskiej.

Decyzja:
- EventMetrics zapisywane in-memory (AgentMetrics, max 1000 eventow).
- Kazdy event zawiera: trace_id, event_kind, repo, title, mttr_s,
  plan_step_count, tool_call_rounds, revision_count, input_tokens,
  output_tokens, total_tokens, validation_passed, pr_created.
- Eksport: GET /metrics/export?format=csv (pelna lista eventow).
- Retencja: in-memory w trakcie sesji; trwala retencja przez eksport CSV
  przed restartem lub przez zewnetrzny log (stdout JSON).
- Format logow: JSON structured (logger.info z parametrami).
- Dla pracy magisterskiej: po kazdym scenariuszu eksportuj CSV i dolacz
  do wynikow kampanii.

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
