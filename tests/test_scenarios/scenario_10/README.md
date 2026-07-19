# Scenariusz 10 — YAML merge conflict w values-dev.yaml → agent naprawia przez PR

**Typ:** GitHub PR z błędem (CI `helm lint` failure)  
**Repo:** `AGH-iot-agent/iot-agent-logs`  
**Cel:** Sprawdzenie, czy agent wykryje w CI konflikt merge w `Helm/values-dev.yaml`,
identyfikuje błąd, wybierze poprawną wartość `replicaCount` i otworzy PR naprawczy.

## Opis

Skrypt klonuje repo `iot-agent-logs`, tworzy branż `test/scenario_10_{ts}`,
wstrzykuje markery konfliktu merge do `Helm/values-dev.yaml` w miejscu `replicaCount`
i otwiera PR do `main`. Pipeline `helm lint` w CI nieodwracalnie padnie —
`GitHubPRBuildMonitor` emituje `github_pr_build_failure`. Agent:

1. Pobiera log CI (lint error: invalid YAML — merge conflict markers)
2. Identyfikuje linię z `<<<<<<<`
3. Wybiera poprawny `replicaCount` (wyższą wartość lub `HEAD` side)
4. Tworzy branż `fix/PR-{nr}-{ts}`, commituje fix, otwiera PR do branży PR-a

## Wymagania

- `gh` CLI (GitHub CLI) zalogowany jako `GH_TOKEN`
- `git` + `python3` (wbudowane w Linux)

## Kroki

1. Ustaw `GH_TOKEN` w `.env`
2. Uruchom: `bash scenario_10.sh`
3. Poczekaj na CI + ≈ 30s watchdog tick
4. Sprawdź: PR fix otwarty na `$REPO` z branży `fix/PR-*`
5. Wyczyść: `bash cleanup_10.sh` (zamyka PR i usuwa branż)

## Pliki

- `scenario_10.sh` — klonuje repo, wstrzykuje konflikt, pushuje PR
- `cleanup_10.sh` — zamyka otwarte PR test/scenario_10_* i usuwa branżę
- `.env` — `GH_TOKEN`, `OWNER`, `REPO`

