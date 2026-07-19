# Scenariusz 11 — High HTTP latency spike → agent proponuje skalowanie

**Typ:** Prometheus alert (`high_http_latency`)  
**Cel:** Sprawdzenie, czy agent wykryje wysokie opóźnienie HTTP na `iot-agent-alert-api`,
potwierdzi p99 przez Prometheus, sprawdzi repliki i zaproponuje
`replicaCount` bump lub HPA w `values-dev.yaml`.

## Opis

Skrypt generuje 30 równoległych żądań HTTP (timeout 5s, opóźnienie 100ms/worker)
przez 90 sekund do `iot-agent-alert-api`. Prometheus zbiera `http_server_requests_seconds`.
Kiedy watchdog następnym tickiem (co 30s) wykryje p99 > progu, emituje `high_http_latency`.

## Różnica względem scenario_03

- Scenariusz 03: **rate spike** (dużo req/s, max_time=2s) → `request_rate_spike`
- Scenariusz 11: **latency spike** (mniej req/s, timeout=5s, concurrent in-flight) → `high_http_latency`
- Docelowa usługa różna: 03=gateway, 11=alert-api

## Oczekiwana reakcja agenta

1. Event `high_http_latency` na pod `iot-agent-alert-api-*`
2. Plan: Prometheus query p99 → K8s replicas → Loki errors → get Helm values
3. Fix: `replicaCount` bump lub `autoscaling.enabled: true` w `values-dev.yaml`
4. Walidacja Helm dry-run na `iotag-sbx`
5. PR otwarty do `main`

## Kroki

1. Ustaw `TARGET_URL` i `GH_TOKEN` w `.env`
2. Uruchom: `bash scenario_11.sh`
3. Poczekaj ≈ 30-60s (1-2 watchdog ticki)
4. Sprawdź: `/alerts` na agent API — `high_http_latency` event
5. Wyczyść: `bash cleanup_11.sh`

## Pliki

- `scenario_11.sh` — curl loop (30 workers × 90s, timeout 5s)
- `cleanup_11.sh` — pkill curl workers
- `.env` — `TARGET_URL`, `CONCURRENCY`, `DURATION_S`, `REQUEST_DELAY`

