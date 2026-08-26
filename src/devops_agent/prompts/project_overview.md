# Architektura Systemu — iot-agent

## 1. Infrastruktura i Orkiestracja

- **Kubernetes (k3s):**
  - Lekka dystrybucja K8s, środowisko dev/test.
  - **Dwa namespace:**  
    - `iot-agent` (produkcyjny, główne serwisy)
    - `iotag-dev` (środowisko deweloperskie/testowe)
- **Helm:**  
  - Zarządzanie deploymentami, szablony dla wszystkich serwisów.
- **Istio:**  
  - Service mesh, routing, mTLS, monitoring ruchu, circuit breaking.
- **Ingress / API Gateway:**  
  - NGINX lub Istio Gateway, centralny punkt wejścia do API.
- **Persistent Volumes:**  
  - Przechowywanie danych (np. bazy, logi).

## 2. Mikroserwisy i API

- **iot-agent-authentication:**  
  - API do logowania, rejestracji, zarządzania użytkownikami.
- **iot-agent-alert-api:**  
  - Obsługa alertów, powiadomień, integracja z monitoringiem.
- **iot-agent-dashboard-api:**  
  - Backend do dashboardu, agregacja danych, statusy.
- **iot-agent-dashboard-ui:**  
  - Frontend (React/Vue), panel administracyjny.
- **iot-agent-device-api:**  
  - Zarządzanie urządzeniami IoT, rejestracja, statusy.
- **iot-agent-gateway-api:**  
  - Brama API, proxy do innych serwisów, translacja protokołów.
- **iot-agent-monitoring:**  
  - Prometheus, Grafana, alerting, metryki.
- **iot-agent-mqtt-broker:**  
  - Komunikacja MQTT, obsługa urządzeń IoT.
- **iot-agent-sim-devices:**  
  - Symulacja urządzeń, testy end-to-end.
- **iot-agent-stream-worker:**  
  - Przetwarzanie strumieni danych, ETL.
- **iot-agent-db-ops:**  
  - Operacje na bazie danych, migracje, backupy.
- **iot-agent-infra:**  
  - Skrypty, config-mapy, sekrety, runnerzy CI/CD.

## 3. API Gateway

- **NGINX lub Istio Gateway:**
  - SSL termination, routing do serwisów po ścieżkach.
  - Ochrona rate-limit, CORS, autoryzacja JWT.
  - Przekierowania do dashboardu, API, brokerów.

## 4. Monitoring, Logging, Alerting

- **Prometheus:**  
  - Zbieranie metryk z serwisów, K8s, Istio.
- **Grafana:**  
  - Dashboardy dla API i komponentów platformy, wizualizacja metryk.
  - Przykładowe pulpity: gateway-api, alert-api, login-screen, logs, stream-worker.
- **Loki:**  
  - Centralne logowanie, agregacja logów z podów.
- **Alertmanager:**  
  - Powiadomienia (Slack, email, PagerDuty).
- **Custom Alert API:**  
  - Integracja alertów z systemem zgłoszeń.

## 5. Procesy DevOps

- **CI/CD (GitHub Actions):**
  - Build, test, deploy dla każdego mikroserwisu.
  - Workflows: lint, testy jednostkowe, build obrazu, push do registry, deploy przez Helm.
- **Pull Request (PR):**
  - Każda zmiana przez PR, automatyczne testy, review, statusy.
- **Code Review (CR):**
  - Wymagany review, automatyczne komentarze z LangGraph.
- **Release Workflow:**
  - Tagowanie, automatyczny deploy na produkcję/dev.
- **Monitoring procesów:**
  - Watchdog monitoruje pipeline, deploymenty, crashloop, alerty.
- **Approval Flow:**
  - Dashboard do zatwierdzania akcji (np. restart deploymentu).

## 6. Automatyzacja i Agent

- **LangGraph DevOps Agent:**
  - Scheduler, event-driven, pluginy (monitory: GitHub, K8s, Prometheus, Loki).
  - MCPAdapter: warstwa narzędziowa (API do K8s, GitHub, Prometheus, Loki).
  - Dashboard (FastAPI): health, events, approvals.
  - Watchdog: uruchamia LangGraph, reaguje na alerty, publikuje komentarze, zarządza flow approval.

## 7. Bezpieczeństwo

- **Secrets Management:**  
  - K8s secrets, sealed-secrets, cert-manager.
- **Network Policies:**  
  - Ograniczenie ruchu między namespace’ami.

## 8. Przykładowy Przepływ

1. **PR do repo:**  
   - Automatyczne testy, review, statusy.
2. **Deploy na dev przez workflow:**  
   - Helm, rollout, health check.
3. **Watchdog monitoruje pod:**  
   - Wykrywa crashloop, pobiera logi, publikuje alert.
4. **Alert trafia do dashboardu:**  
   - Możliwa akcja naprawcza (np. restart deploymentu) — wymaga zatwierdzenia.
5. **Approval przez dashboard:**  
   - Watchdog ponawia akcję, restartuje deployment.
6. **Monitoring i alerting:**  
   - Prometheus/Loki/Grafana — pełna obserwowalność.
