"""Minimal in-process metrics with a Prometheus text endpoint (/metrics)."""
from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.sums: dict[tuple[str, str], float] = defaultdict(float)
        self.counters: dict[str, float] = defaultdict(float)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.requests[(method, route, status)] += 1
            buckets = self.hist[(method, route)]
            for i, edge in enumerate(BUCKETS):
                if seconds <= edge:
                    buckets[i] += 1
                    break
            else:
                buckets[-1] += 1
            self.sums[(method, route)] += seconds

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self.counters[name] += value

    def render(self, extra: dict[str, float] | None = None) -> str:
        lines = ["# TYPE vaani_http_requests_total counter"]
        with self._lock:
            for (method, route, status), count in sorted(self.requests.items()):
                lines.append(f'vaani_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
            lines.append("# TYPE vaani_http_request_seconds histogram")
            for (method, route), buckets in sorted(self.hist.items()):
                cumulative = 0
                for edge, count in zip((*BUCKETS, float("inf")), buckets):
                    cumulative += count
                    le = "+Inf" if edge == float("inf") else edge
                    lines.append(f'vaani_http_request_seconds_bucket{{method="{method}",route="{route}",le="{le}"}} {cumulative}')
                lines.append(f'vaani_http_request_seconds_sum{{method="{method}",route="{route}"}} {self.sums[(method, route)]:.4f}')
                lines.append(f'vaani_http_request_seconds_count{{method="{method}",route="{route}"}} {cumulative}')
            for name, value in sorted(self.counters.items()):
                lines.append(f"{name} {value}")
        for name, value in (extra or {}).items():
            lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
