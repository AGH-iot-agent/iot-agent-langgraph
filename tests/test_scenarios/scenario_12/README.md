# Scenariusz 12 — Wrong VirtualService host w values-sbx.yaml → agent naprawia przez PR

**Typ:** GitHub PR z błędem (CI deploy failure — `github_pr_build_failure`)  
**Repo:** `AGH-iot-agent/iot-agent-dashboard-api`  
**Cel:** Sprawdzenie, czy agent wykryje błędną konfigurację `VirtualService.host`
(domena `iotag-dev` zamiast `iotag-sbx`) i zaproponuje poprawkę przez `fix/PR-*` branch.

## Opis

Skrypt klonuje repo `iot-agent-dashboard-api`, tworzy branż `test/scenario_12_{ts}`,
kompromituje `Helm/values-sbx.yaml` — podmienia host VirtualService na
`wrong-host.iotag-dev.com` (cross-environment). Otwiera PR do `main`.
CI deploy step (helm template render + kubectl apply ——dry-run) się nie powiedzie.
`GitHubPRBuildMonitor` emituje `github_pr_build_failure`. Agent:

1. Pobiera log CI — błąd: `VirtualService host does not match namespace pattern`
2. Identyfikuje błędny host w `values-sbx.yaml`
3. Proponuje poprawną wartość: `iot-agent-dashboard-api-sbx.iotag-sbx.com`
4. Tworzy branż `fix/PR-{nr}-{ts}`, commituje, otwiera PR do branży PR-a

## Wymagania

- `gh` CLI (GitHub CLI) zalogowany jako `GH_TOKEN`
- `git` + `python3`

## Kroki

1. Ustaw `GH_TOKEN` w `.env`
2. Uruchom: `bash scenario_12.sh`
3. Poczekaj na CI + ≈ 30s watchdog tick
4. Sprawdź: PR fix na `$REPO` z branży `fix/PR-*`
5. Wyczyść: `bash cleanup_12.sh`

## Pliki

- `scenario_12.sh` — klonuje repo, wstrzykuje zły host VS, pushuje PR
- `cleanup_12.sh` — zamyka otwarte PR test/scenario_12_* i usuwa branżę
- `.env` — `GH_TOKEN`, `OWNER`, `REPO`

