# Scenariusz 03 — DDoS: request rate spike → agent proposes scalability fix

**Typ:** Prometheus alert (watchdog detects `request_rate_spike` event)  
**Cel:** Sprawdzenie, czy agent wykryje nienormalnie wysoki ruch na `iot-agent-gateway-api`,
potwierdzi go przez Prometheus, sprawdzi aktualną liczbę replik i zaproponuje
`replicaCount` bump lub konfigurację HPA w `values-dev.yaml`.

## Opis

Skrypt generuje 50 równoległych żądań HTTP co 50ms (≈ 1000 req/s) przez 60 sekund
do endpointu health `iot-agent-gateway-api`. Prometheus zbiera metryki `http_server_requests_seconds_count`.
Kiedy watchdog w następnym ticku (co 30s) odpyta Prometheus, `PrometheusMetricsMonitor` emituje
event `request_rate_spike`. Agent:

1. Potwierdza spike przez Prometheus query (`rate(...[1m]) by (pod)`)
2. Sprawdza `get_rollout_status` i `get_pods` — ile replik jest aktualnie UP
3. Odpytuje Loki o błędy (czy spike powoduje OOM/5xx)
4. Proponuje fix: `replicaCount: 3` w `Helm/values-dev.yaml` lub konfigurację `autoscaling.enabled: true`
5. Waliduje przez Helm dry-run na `iotag-sbx`
6. Otwiera PR z fixem

## Kroki

1. Upewnij się, że agent jest uruchomiony (`monitor_prometheus: true` w watchdog config)
2. Ustaw `TARGET_URL` w `.env` (lub zostaw default)
3. Uruchom: `bash scenario_03.sh`
4. Poczekaj ~30-60s (1 watchdog tick)
5. Sprawdź: `GET /alerts` na agent API — powinien pojawić się `request_rate_spike` event
6. Sprawdź: PR otwarty na repozytorium `iot-agent-gateway-api`

## Oczekiwana reakcja agenta

- Event: `request_rate_spike` na pod `iot-agent-gateway-api-*`
- Plan: Prometheus query → K8s replicas → Loki errors → get Helm values
- Fix: `replicaCount` bump lub HPA w `values-dev.yaml`
- Walidacja: Helm template render + kubectl apply --dry-run=server na `iotag-sbx`
- Wynik: PR otwarty do `main` z fixem

## Pliki

- `scenario_03.sh` — curl loop (50 workers × 60s)
- `cleanup_03.sh` — zatrzymuje background curl workers
- `.env` — zmienne środowiskowe (`TARGET_URL`, `CONCURRENCY`, `DURATION_S`)
