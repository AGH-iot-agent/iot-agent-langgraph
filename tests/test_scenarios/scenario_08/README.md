# Scenariusz 08 — Błędna konfiguracja datasource (JDBC localhost)

**Cel:** Sprawdzenie, czy agent wykryje błędny `SPRING_DATASOURCE_URL` wskazujący na `localhost`
zamiast wewnętrznej nazwy DNS serwisu PostgreSQL w K8s, i zaproponuje poprawny adres.

**Rodzaj eventu:** `github_issue`  
**Repo:** `AGH-iot-agent/iot-agent-authentication`

## Scenariusz

Pod `iot-agent-authentication` nie może się uruchomić — `HikariPool` nie nawiąże połączenia
z PostgreSQL, bo `SPRING_DATASOURCE_URL` jest ustawiony na `localhost:5432` zamiast
`postgres.iotag-dev.svc.cluster.local:5432`. Aplikacja od razu wchodzi w `CrashLoopBackOff`.

## Oczekiwane działanie agenta
1. Przeczytać issue z opisem błędu HikariPool
2. Zidentyfikować w logach `Connection to localhost:5432 refused`
3. Sprawdzić `Helm/values-dev.yaml` w repozytorium `iot-agent-authentication`
4. Odnaleźć niepoprawne `SPRING_DATASOURCE_URL: jdbc:postgresql://localhost:5432/iotdb`
5. Zaproponować poprawny adres: `jdbc:postgresql://postgres.iotag-dev.svc.cluster.local:5432/iotdb`
6. Wpisać komentarz do issue z Root Cause + Proposed Fix (nie tworzyć PR — to issue event)

## Pliki
- `scenario_08.sh` — tworzy GitHub Issue z opisem problemu
- `cleanup_08.sh` — zamyka otwarte issue w repo
- `issue_body.md` — treść issue
- `.env` — `GH_TOKEN`
