from agent_cli.core.runtime.agents.resource_tracker import ResourceTracker


def test_resource_tracker_summary_without_budget() -> None:
    tracker = ResourceTracker(context_limit=1000)
    assert tracker.summary() == ""

    tracker.update(input_tokens=200, output_tokens=100, cost=0.0)
    summary = tracker.summary()
    assert "Turn 1" in summary
    assert "context ~30% used" in summary
    assert "cost $" not in summary


def test_resource_tracker_summary_with_budget() -> None:
    tracker = ResourceTracker(context_limit=1000, cost_budget=1.50)
    tracker.update(input_tokens=100, output_tokens=100, cost=0.125)
    summary = tracker.summary()
    assert "context ~20% used" in summary
    assert "cost $0.1250/$1.50" in summary
