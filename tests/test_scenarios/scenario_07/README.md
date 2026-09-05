# Scenariusz 07 — PR: OOMKilled po zbyt niskich limitach pamięci w Helm values

**Typ:** GitHub Pull Request  
**Repo:** `AGH-iot-agent/iot-agent-login-screen`  
**Cel:** Sprawdzenie, czy agent wykryje OOMKilled spowodowany zbyt niskim limitem pamięci (`1Mi`) w Helm values i zaproponuje podniesienie limitu.

## Opis

PR wprowadza `values-dev.yaml` i `values-sbx.yaml` z ustawionym limitem pamięci `1Mi` dla kontenera. Limit jest celowo zbyt niski i ma prowokować OOMKilled (exit code 137). Agent powinien przeanalizować logi deploymentu i/lub opis poda, wykryć przyczynę OOMKilled i zaproponować podniesienie limitu pamięci do bezpiecznej wartości.

## Kroki

1. Uzupełnij `GH_TOKEN` w pliku `.env`
2. Dla testu live ustaw też `OPENAI_API_KEY`
3. Uruchom cały scenariusz: `bash run_07.sh live-test`
4. Alternatywnie: `bash run_07.sh pr-only` (tylko utworzenie PR)
5. Sprzątanie: `bash run_07.sh cleanup`
6. Oczekiwane: agent wykrywa OOMKilled, proponuje korektę `resources.limits.memory` do odpowiedniej wartości (np. `512Mi`)

## Pliki

- `scenario_07.sh` — klonuje repo, zastępuje values, pushuje branch i otwiera PR
- `run_07.sh` — skrypt orkiestrujący (`live-test`, `pr-only`, `cleanup`)
- `values-dev.yaml` — Helm values z BŁĘDNYM limitem pamięci (`memory: 1Mi`)
- `values-sbx.yaml` — Helm values z BŁĘDNYM limitem pamięci (`memory: 1Mi`) dla środowiska sbx
- `cleanup_07.sh` — zamyka otwarte PRy scenariusza i usuwa branche testowe
- `.env` — zmienne środowiskowe (`GH_TOKEN`)
