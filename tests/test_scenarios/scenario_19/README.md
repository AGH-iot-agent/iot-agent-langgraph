# Scenariusz 19 — Compound Helm (3 usterki) → wyższość multi-agent

**Typ:** GitHub PR z błędem CI (`github_pr_build_failure`)
**Repo:** `AGH-iot-agent/iot-agent-login-screen`
**Cel:** Zestaw **trzech niezależnych** usterek w `Helm/values-sbx.yaml`, tak aby
single-agent „złapał” tylko głośny błąd z logów CI, a multi-agent (critic +
sandbox retry) musiał nazwać wszystkie trzy.

## Wstrzyknięte usterki (max 3)

1. `replicaCount: "not-a-number"` — błąd schematu Helm, widoczny w logach CI
2. `livenessProbe.path: /invalid-path-for-liveness-probe` — usterka health-check,
   niekoniecznie w pierwszym błędzie linta
3. VirtualService `hosts: wrong-host.iotag-dev.com` — cross-env (dev w sbx)

`values-dev.yaml` jest poprawny — problemy są tylko w sbx.

## Asercje

Komentarz bota musi wymienić **wszystkie** rodziny: `replicaCount`, `probe` i
`wrong-host`. Częściowa diagnoza = FAIL. Walidacja sandbox: `iotag-sbx`.
