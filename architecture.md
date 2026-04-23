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
