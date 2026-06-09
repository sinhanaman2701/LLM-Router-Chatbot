from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Iterable


def _labels_key(label_names: tuple[str, ...], labels: dict[str, str]) -> tuple[str, ...]:
    missing = [name for name in label_names if name not in labels]
    if missing:
        raise KeyError(f"Missing metric labels: {', '.join(missing)}")
    return tuple(str(labels[name]) for name in label_names)


def _format_labels(label_names: tuple[str, ...], label_values: tuple[str, ...]) -> str:
    if not label_names:
        return ""
    parts = [
        f'{name}="{value.replace("\\", "\\\\").replace(chr(10), "\\n").replace("\"", "\\\"")}"'
        for name, value in zip(label_names, label_values, strict=True)
    ]
    return "{" + ",".join(parts) + "}"


class CounterMetric:
    def __init__(self, name: str, description: str, label_names: Iterable[str]) -> None:
        self.name = name
        self.description = description
        self.label_names = tuple(label_names)
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(self.label_names, labels)
        with self._lock:
            self._values[key] += amount

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            items = sorted(self._values.items())
        for label_values, value in items:
            lines.append(f"{self.name}{_format_labels(self.label_names, label_values)} {value}")
        return "\n".join(lines)


class GaugeMetric:
    def __init__(self, name: str, description: str, label_names: Iterable[str]) -> None:
        self.name = name
        self.description = description
        self.label_names = tuple(label_names)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        key = _labels_key(self.label_names, labels)
        with self._lock:
            self._values[key] = value

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            items = sorted(self._values.items())
        for label_values, value in items:
            lines.append(f"{self.name}{_format_labels(self.label_names, label_values)} {value}")
        return "\n".join(lines)


class HistogramMetric:
    def __init__(
        self,
        name: str,
        description: str,
        label_names: Iterable[str],
        buckets: Iterable[float],
    ) -> None:
        self.name = name
        self.description = description
        self.label_names = tuple(label_names)
        self.buckets = tuple(sorted(float(bucket) for bucket in buckets))
        self._bucket_counts: dict[tuple[str, ...], list[int]] = {}
        self._sum: dict[tuple[str, ...], float] = defaultdict(float)
        self._count: dict[tuple[str, ...], int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = _labels_key(self.label_names, labels)
        with self._lock:
            bucket_counts = self._bucket_counts.setdefault(key, [0] * len(self.buckets))
            for index, bucket in enumerate(self.buckets):
                if value <= bucket:
                    bucket_counts[index] += 1
            self._sum[key] += value
            self._count[key] += 1

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            bucket_items = sorted(self._bucket_counts.items())
            sum_items = dict(self._sum)
            count_items = dict(self._count)
        for label_values, bucket_counts in bucket_items:
            for bucket, count in zip(self.buckets, bucket_counts, strict=True):
                labels = dict(zip(self.label_names, label_values, strict=True))
                labels["le"] = str(int(bucket) if float(bucket).is_integer() else bucket)
                all_label_names = self.label_names + ("le",)
                lines.append(f"{self.name}_bucket{_format_labels(all_label_names, label_values + (labels['le'],))} {count}")
            inf_label_names = self.label_names + ("le",)
            lines.append(f"{self.name}_bucket{_format_labels(inf_label_names, label_values + ('+Inf',))} {count_items.get(label_values, 0)}")
            lines.append(f"{self.name}_sum{_format_labels(self.label_names, label_values)} {sum_items.get(label_values, 0.0)}")
            lines.append(f"{self.name}_count{_format_labels(self.label_names, label_values)} {count_items.get(label_values, 0)}")
        return "\n".join(lines)

class ChatbotMetrics:
    def __init__(self, namespace: str = "chatbot") -> None:
        prefix = f"{namespace}_"
        duration_buckets = (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
        self.request_duration = HistogramMetric(
            f"{prefix}request_duration_ms",
            "Request duration by component and route in milliseconds.",
            ("component", "route", "method", "status"),
            duration_buckets,
        )
        self.llm_requests_total = CounterMetric(
            f"{prefix}llm_requests_total",
            "Total LLM requests by component, model, and prompt version.",
            ("component", "model", "prompt_version", "outcome"),
        )
        self.llm_duration = HistogramMetric(
            f"{prefix}llm_duration_ms",
            "LLM request duration in milliseconds.",
            ("component", "model", "prompt_version", "outcome"),
            duration_buckets,
        )
        self.tool_calls_total = CounterMetric(
            f"{prefix}tool_calls_total",
            "Tool call outcomes by tool and component.",
            ("component", "tool_name", "outcome"),
        )
        self.policy_outcomes_total = CounterMetric(
            f"{prefix}harness_policy_outcomes_total",
            "Policy engine outcomes by tool and action.",
            ("tool_name", "action"),
        )
        self.circuit_breaker_state = GaugeMetric(
            f"{prefix}circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half-open, 2=open).",
            ("tool_name",),
        )
        self.session_writes_retried_total = CounterMetric(
            f"{prefix}session_writes_retried_total",
            "Redis optimistic write retries.",
            ("component",),
        )
        self.mock_server_auth_refresh_total = CounterMetric(
            f"{prefix}mock_server_auth_refresh_total",
            "Mock server auth cache misses and refreshes.",
            ("outcome",),
        )
        self.router_confidence_total = CounterMetric(
            f"{prefix}router_confidence_total",
            "Router confidence band distribution.",
            ("intent_class", "band"),
        )
        self.audit_log_failures_total = CounterMetric(
            f"{prefix}audit_log_failures_total",
            "Audit log insert failures.",
            ("component",),
        )
        self.dependency_duration = HistogramMetric(
            f"{prefix}dependency_duration_ms",
            "Dependency latency in milliseconds.",
            ("dependency", "outcome"),
            duration_buckets,
        )

    def render(self) -> str:
        metrics = [
            self.request_duration,
            self.llm_requests_total,
            self.llm_duration,
            self.tool_calls_total,
            self.policy_outcomes_total,
            self.circuit_breaker_state,
            self.session_writes_retried_total,
            self.mock_server_auth_refresh_total,
            self.router_confidence_total,
            self.audit_log_failures_total,
            self.dependency_duration,
        ]
        return "\n\n".join(metric.render() for metric in metrics) + "\n"

    def observe_request(self, *, component: str, route: str, method: str, status: int, duration_ms: float) -> None:
        self.request_duration.observe(
            duration_ms,
            component=component,
            route=route,
            method=method,
            status=str(status),
        )

    def observe_llm(self, *, component: str, model: str, prompt_version: str, outcome: str, duration_ms: float) -> None:
        self.llm_requests_total.inc(
            component=component,
            model=model,
            prompt_version=prompt_version,
            outcome=outcome,
        )
        self.llm_duration.observe(
            duration_ms,
            component=component,
            model=model,
            prompt_version=prompt_version,
            outcome=outcome,
        )

    def observe_tool_call(self, *, component: str, tool_name: str, outcome: str) -> None:
        self.tool_calls_total.inc(component=component, tool_name=tool_name, outcome=outcome)

    def observe_policy(self, *, tool_name: str, action: str) -> None:
        self.policy_outcomes_total.inc(tool_name=tool_name, action=action)

    def set_circuit_breaker_state(self, *, tool_name: str, state: str) -> None:
        value = {"CLOSED": 0.0, "HALF_OPEN": 1.0, "OPEN": 2.0}.get(state, math.nan)
        if not math.isnan(value):
            self.circuit_breaker_state.set(value, tool_name=tool_name)

    def increment_session_write_retry(self) -> None:
        self.session_writes_retried_total.inc(component="state_manager")

    def observe_mock_server_auth(self, *, outcome: str) -> None:
        self.mock_server_auth_refresh_total.inc(outcome=outcome)

    def observe_router_confidence(self, *, intent_class: str, confidence: float, low_threshold: float, high_threshold: float) -> None:
        if confidence < low_threshold:
            band = "low"
        elif confidence >= high_threshold:
            band = "high"
        else:
            band = "mid"
        self.router_confidence_total.inc(intent_class=intent_class, band=band)

    def increment_audit_log_failure(self) -> None:
        self.audit_log_failures_total.inc(component="audit_logger")

    def observe_dependency(self, *, dependency: str, outcome: str, duration_ms: float) -> None:
        self.dependency_duration.observe(duration_ms, dependency=dependency, outcome=outcome)
