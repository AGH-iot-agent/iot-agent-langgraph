# Scenariusz 07 — PR: OOMKilled po zbyt niskich limitach pamięci w Helm values

**Typ:** GitHub Pull Request  
**Repo:** `AGH-iot-agent/iot-agent-login-screen`  
**Cel:** Sprawdzenie, czy agent wykryje OOMKilled spowodowany zbyt niskim limitem pamięci (`32Mi`) w Helm values i zaproponuje podniesienie limitu.

## Opis

PR wprowadza `values-dev.yaml` z ustawionym limitem pamięci `32Mi` dla kontenera. Z limitem `32Mi` pod powinien zostać zabity przez OOMKiller (exit code 137). Agent powinien przeanalizować logi deploymentu i/lub opis poda, wykryć przyczynę OOMKilled i zaproponować podniesienie limitu pamięci do bezpiecznej wartości.

## Kroki

1. Uzupełnij `GH_TOKEN` w pliku `.env`
2. Uruchom: `bash scenario_07.sh`
3. Oczekiwane: agent wykrywa OOMKilled, proponuje korektę `resources.limits.memory` do odpowiedniej wartości (np. `512Mi`)

## Pliki

- `scenario_07.sh` — klonuje repo, zastępuje values, pushuje branch i otwiera PR
- `values-dev.yaml` — Helm values z BŁĘDNYM limitem pamięci (`memory: 32Mi`)
- `values-sbx.yaml` — poprawna wersja Helm values dla środowiska sbx (bez limitów)
- `cleanup_07.sh` — zamyka otwarte PRy scenariusza i usuwa branche testowe
- `.env` — zmienne środowiskowe (`GH_TOKEN`)
