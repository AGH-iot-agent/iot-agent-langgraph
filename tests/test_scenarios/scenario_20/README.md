# Scenariusz 20 — Compound Helm ze sprzecznymi symptomami → wyższość multi-agent

**Typ:** GitHub PR z błędem CI (`github_pr_build_failure`)
**Repo:** `AGH-iot-agent/iot-agent-login-screen`
**Cel:** Trzy usterki z **różnych domen** (schema / OOM / CrashLoop), żeby
single-agent zmieszał historie albo naprawił tylko jedną.

## Wstrzyknięte usterki (max 3)

1. `replicaCount: "not-a-number"` — głośny błąd schematu w CI
2. `resources.limits.memory: 1Mi` — wygląda jak OOMKilled
3. `livenessProbe.port: 9999` — wygląda jak CrashLoopBackOff na złym porcie

`values-dev.yaml` jest poprawny.

## Asercje

Komentarz musi wymienić **wszystkie** trzy wartości: `replicaCount`, `1Mi` i
`9999`. Diagnoza „to OOM” albo „to probe” bez pozostałych = FAIL.
