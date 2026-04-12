# Katalog MCP dla agenta DevOps

## MCP wymagane (MVP)

1. GitHub MCP
- Zastosowanie: PR, issues, komentarze, status checks.
- Zakres: repo-scoped, bez admin uprawnien.

2. Kubernetes MCP
- Zastosowanie: odczyt zasobow, rollout status, controlled apply.
- Zakres: namespace-scoped ServiceAccount.

3. Helm MCP
- Zastosowanie: template, lint, dry-run, upgrade, rollback.
- Zakres: release-scoped.

4. Filesystem MCP (read-only)
- Zastosowanie: analiza repo i manifestow.
- Zakres: tylko wybrane katalogi.

## MCP opcjonalne

1. Docker MCP
- Build/inspect obrazow i diagnostyka lokalna.

2. Terraform MCP
- Plan/apply z rygorystycznym approval gate.

3. Monitoring MCP (Prometheus/Grafana)
- Walidacja SLO po wdrozeniu.

4. Komunikacja MCP (Slack/Jira)
- Notyfikacje i automatyczne tickety incydentowe.

## Kolejnosc wdrazania

1. GitHub + Filesystem (read-only)
2. Kubernetes (read-only)
3. Helm dry-run
4. Write operations za approval gate
