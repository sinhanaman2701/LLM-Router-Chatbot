from __future__ import annotations

from chatbot.observability.metrics import ChatbotMetrics


def test_metrics_render_prometheus_text():
    metrics = ChatbotMetrics(namespace="chatbot")
    metrics.observe_request(component="api", route="/health", method="GET", status=200, duration_ms=12.5)
    metrics.observe_llm(
        component="router",
        model="gemma4:31b-cloud",
        prompt_version="router_v1.0",
        outcome="success",
        duration_ms=55.0,
    )
    metrics.observe_tool_call(component="harness", tool_name="create_booking", outcome="confirm")
    metrics.observe_policy(tool_name="create_booking", action="REQUIRE_CONFIRMATION")
    metrics.set_circuit_breaker_state(tool_name="create_booking", state="HALF_OPEN")
    metrics.observe_router_confidence(intent_class="new_task", confidence=0.95, low_threshold=0.7, high_threshold=0.85)
    metrics.increment_audit_log_failure()

    rendered = metrics.render()

    assert "# HELP chatbot_request_duration_ms" in rendered
    assert 'chatbot_tool_calls_total{component="harness",tool_name="create_booking",outcome="confirm"} 1.0' in rendered
    assert 'chatbot_circuit_breaker_state{tool_name="create_booking"} 1.0' in rendered
    assert 'chatbot_router_confidence_total{intent_class="new_task",band="high"} 1.0' in rendered
