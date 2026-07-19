# Scenariusz 09 — Orphaned K8s resources → agent proponuje cleanup

**Typ:** GitHub Issue + K8s check  
**Repo:** `AGH-iot-agent/iot-agent-authentication`  
**Cel:** Sprawdzenie, czy agent wykryje issue o osieroconych zasobach (ConfigMap/Secret),
sprawdzi K8s i zaproponuje komendy do ich usunięcia.

## Opis

Skrypt tworzy testowe, niereferencjonowane ConfigMap i Secret w namespace `iotag-dev`
(suf. timestamp — unikalne przy każdym uruchomieniu), następnie otwiera GitHub Issue
na repozytorium `iot-agent-authentication` opisujące problem. Agent’s `GitHubIssueMonitor`
wykrywa nowe issue z etykietą `iot-devops-agent`, analizuje je i generuje odpowiedź.

## Oczekiwana reakcja agenta

1. Wykrywa issue o osieroconych zasobach
2. Wykonuje `kubectl get configmap/secret -n iotag-dev` i filtruje po labelach
3. Weryfikuje, że żaden deploy nie referencjonuje tych zasobów
4. Komentuje issue z propozycją `kubectl delete` lub otwiera PR z cleanup jobem

## Kroki

1. Ustaw `GH_TOKEN` w `.env`
2. Uruchom: `bash scenario_09.sh`
3. Poczekaj ≈ 30s (1 watchdog tick)
4. Sprawdź: komentarz agenta w utworzonym issue na GitHubie
5. Wyczyść: `bash cleanup_09.sh`

## Pliki

- `scenario_09.sh` — tworzy K8s zasoby + otwiera GitHub Issue
- `cleanup_09.sh` — usuwa testowe ConfigMap/Secret z `iotag-dev`
- `.env` — `GH_TOKEN`, `NAMESPACE`

