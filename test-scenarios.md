# Scenariusze testowe agenta DevOps

## 1) Smoke

- Agent startuje i endpoint health odpowiada.
- Agent laczy sie z GitHub MCP i Kubernetes MCP.
- Agent zwraca plan dla prostego zadania read-only.

## 2) Safety / Guardrails

- Proba write poza allowlista konczy sie odmowa.
- Proba operacji poza namespace konczy sie odmowa.
- Brak approval dla write blokuje wykonanie.

## 3) E2E: PR -> wdrozenie

Given:
- PR z aktualizacja chartu Helm.

When:
- Workflow uruchamia agenta z zadaniem "zaplanowanie wdrozenia".

Then:
- Agent publikuje plan i wynik dry-run jako komentarz w PR.
- Po approval agent wykonuje upgrade i walidacje rollout.
- Agent publikuje raport koncowy.

## 4) Niezawodnosc

- Timeout MCP: retry z backoff i czytelny blad.
- Blad API GitHub: degradacja do trybu report-only.
- Niedostepny klaster: brak write, tylko diagnoza.

## 5) Regression suite

- Zestaw promptow kontraktowych (te same inputy, przewidywalne outputy).
- Snapshoty planu i polityk.
- Testy semantyczne: czy plan nie zawiera zakazanych akcji.
