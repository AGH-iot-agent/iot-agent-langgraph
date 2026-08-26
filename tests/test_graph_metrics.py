from __future__ import annotations

from devops_agent.graph import record_graph_run_metrics
from devops_agent.metrics import agent_metrics


def test_record_graph_run_metrics_records_event() -> None:
    before = len(agent_metrics.list_events(limit=1000))

    record_graph_run_metrics(
        {
            "trace_id": "trace-graph-metrics-1",
            "event_kind": "github_issue",
            "token_usage": {
                "input_tokens": 1200,
                "output_tokens": 400,
                "tool_call_rounds": 3,
            },
            "plan": ["step1", "step2"],
            "revision_count": 1,
            "validation_result": {"passed": True},
            "pr_url": "https://example.com/pr/1",
        },
        started_at=100.0,
        trace_id="trace-graph-metrics-1",
        event_kind="github_issue",
        repo="org/example-repo",
        title="Latency spike",
    )

    events = agent_metrics.list_events(limit=1000)
    assert len(events) == before + 1
    event = events[-1]
    assert event["event_kind"] == "github_issue"
    assert event["repo"] == "org/example-repo"
    assert event["title"] == "Latency spike"
    assert event["total_tokens"] == 1600
    assert event["validation_passed"] is True
    assert event["pr_created"] is True
