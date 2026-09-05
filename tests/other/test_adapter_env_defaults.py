from __future__ import annotations

import importlib


def test_adapter_base_url_safe_when_env_unset(monkeypatch) -> None:
    for key in ("PROMETHEUS_URL", "GRAFANA_URL", "LOKI_URL"):
        monkeypatch.delenv(key, raising=False)

    prom_mod = importlib.import_module("devops_agent.adapters.prometheus_adapter")
    grafana_mod = importlib.import_module("devops_agent.adapters.grafana_adapter")
    loki_mod = importlib.import_module("devops_agent.adapters.loki_adapter")

    prom_mod = importlib.reload(prom_mod)
    grafana_mod = importlib.reload(grafana_mod)
    loki_mod = importlib.reload(loki_mod)

    assert prom_mod.PrometheusAdapter().base_url == "http://localhost:9090"
    assert grafana_mod.GrafanaAdapter().base_url == ""
    assert loki_mod.LokiAdapter().base_url == ""


def test_adapter_base_url_adds_http_scheme_when_missing(monkeypatch) -> None:
    monkeypatch.setenv("LOKI_URL", "localhost:3100")
    monkeypatch.setenv("PROMETHEUS_URL", "prometheus:9090")
    monkeypatch.setenv("GRAFANA_URL", "grafana:3000")

    prom_mod = importlib.import_module("devops_agent.adapters.prometheus_adapter")
    grafana_mod = importlib.import_module("devops_agent.adapters.grafana_adapter")
    loki_mod = importlib.import_module("devops_agent.adapters.loki_adapter")

    prom_mod = importlib.reload(prom_mod)
    grafana_mod = importlib.reload(grafana_mod)
    loki_mod = importlib.reload(loki_mod)

    assert prom_mod.PrometheusAdapter().base_url == "http://prometheus:9090"
    assert grafana_mod.GrafanaAdapter().base_url == "http://grafana:3000"
    assert loki_mod.LokiAdapter().base_url == "http://localhost:3100"
