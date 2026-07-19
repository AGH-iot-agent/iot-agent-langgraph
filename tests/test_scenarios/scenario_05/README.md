# Scenariusz 05 — PR: CrashLoopBackOff po niepoprawnym porcie liveness probe

**Typ:** GitHub Pull Request  
**Repo:** `AGH-iot-agent/iot-agent-alert-api`  
**Cel:** Sprawdzenie, czy agent wykryje CrashLoopBackOff spowodowany błędnym numerem portu w konfiguracji liveness probe i zaproponuje poprawkę w PR.

## Opis

PR wprowadza zmodyfikowany plik `values-dev.yaml`, w którym liveness probe używa nieistniejącego portu `9999` zamiast poprawnej nazwy portu `default-service`. Po deploymencie pod przechodzi do CrashLoopBackOff ponieważ kubelet nie może połączyć się z nieistniejącym portem. Agent powinien przeanalizować logi CI/deployment, wykryć błędną konfigurację proby i zaproponować zmianę `port: 9999` → `port: default-service`.

## Kroki

1. Uzupełnij `GH_TOKEN` w pliku `.env`
2. Uruchom: `bash scenario_05.sh`
3. Oczekiwane: agent wykrywa błąd w konfiguracji liveness probe i proponuje korektę portu

## Pliki

- `scenario_05.sh` — klonuje repo, zastępuje values, pushuje branch i otwiera PR
- `values-dev.yaml` — Helm values z BŁĘDNYM portem liveness probe (`port: 9999`)
- `values-sbx.yaml` — poprawna wersja Helm values dla środowiska sbx
- `cleanup_05.sh` — zamyka otwarte PRy scenariusza i usuwa branche testowe
- `.env` — zmienne środowiskowe (`GH_TOKEN`)
