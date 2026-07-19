# Scenariusz 06 — CI failure: GitHub Actions — `env` context w bloku `with` reusable workflow

**Typ:** GitHub Issue  
**Repo:** `AGH-iot-agent/iot-agent-logs`  
**Cel:** Sprawdzenie, czy agent wykryje błąd w workflow CI spowodowany użyciem kontekstu `env` w bloku `with` reusable workflow i zaproponuje poprawkę.

## Opis

Workflow CI dla `iot-agent-logs` kończy się błędem po refaktoryzacji wprowadzającej reusable workflow. Błąd `Unrecognized named-value: 'env'` pojawia się, bo kontekst `env` nie jest dostępny w bloku `with:` przy wywoływaniu reusable workflow. Wartości muszą być przekazywane przez `needs.<job>.outputs.<key>` lub inline conditional expression. Scenariusz tworzy issue — agent powinien wskazać błędną linię w workflow YAML i zaproponować korektę.

## Kroki

1. Uzupełnij `GH_TOKEN` w pliku `.env`
2. Uruchom: `bash scenario_06.sh`
3. Oczekiwane: agent identyfikuje błędne użycie `${{ env.NAMESPACE }}` w `with:` i proponuje użycie `needs.set-env.outputs.namespace`

## Pliki

- `scenario_06.sh` — tworzy GitHub Issue z opisem błędu workflow
- `issue_body.md` — treść Issue z fragmentem błędnego YAML workflow
- `cleanup_06.sh` — zamyka wszystkie otwarte issues z etykietą `iot-devops-agent`
- `.env` — zmienne środowiskowe (`GH_TOKEN`)
