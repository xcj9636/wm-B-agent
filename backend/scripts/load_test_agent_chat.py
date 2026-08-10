"""Exercise the real detached Agent Chat HTTP path and enforce deployment SLOs.

The script intentionally uses a fixed, non-sensitive prompt. Authentication is
read from an environment variable so credentials never appear in command-line
arguments or reports.
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Dict, List, Optional
from uuid import uuid4

import httpx

from scripts.benchmark_agent_runtime import summarize_samples


WORKLOAD = "detached-agent-api-v1"
FIXED_SAFE_PROMPT = "Rewrite this greeting in concise professional English."


@dataclass(frozen=True)
class AgentChatLoadConfig:
    requests: int = 20
    concurrency: int = 4
    poll_interval_seconds: float = 0.1
    run_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not 1 <= self.requests <= 10000:
            raise ValueError("requests must be between 1 and 10000")
        if not 1 <= self.concurrency <= self.requests:
            raise ValueError("concurrency must be between 1 and requests")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll interval cannot be negative")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run timeout must be positive")


@dataclass(frozen=True)
class _LoadSample:
    success: bool
    ttft_ms: Optional[float] = None
    e2e_ms: Optional[float] = None
    route_path: Optional[str] = None
    error_code: Optional[str] = None


def _parse_sse(payload: str) -> List[Dict[str, object]]:
    events: List[Dict[str, object]] = []
    normalized = payload.replace("\r\n", "\n")
    for frame in normalized.split("\n\n"):
        if not frame.strip():
            continue
        event_type = "message"
        sequence = None
        data_lines = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("id:"):
                sequence = int(line[3:].strip())
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        raw_data = "\n".join(data_lines)
        try:
            data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            data = {}
        events.append(
            {
                "event": event_type,
                "id": sequence,
                "data": data if isinstance(data, dict) else {},
            }
        )
    return events


class AgentChatLoadRunner:
    """Run isolated chat turns concurrently through production API contracts."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: AgentChatLoadConfig,
    ) -> None:
        self._client = client
        self._config = config

    async def run(self) -> Dict[str, object]:
        semaphore = asyncio.Semaphore(self._config.concurrency)
        started = time.perf_counter()

        async def bounded(index: int) -> _LoadSample:
            async with semaphore:
                return await self._run_one(index)

        samples = await asyncio.gather(
            *(bounded(index) for index in range(self._config.requests))
        )
        duration_seconds = max(time.perf_counter() - started, 0.000001)
        successful = [sample for sample in samples if sample.success]
        failed = len(samples) - len(successful)
        ttft_values = [
            sample.ttft_ms
            for sample in successful
            if sample.ttft_ms is not None
        ]
        e2e_values = [
            sample.e2e_ms
            for sample in successful
            if sample.e2e_ms is not None
        ]
        route_paths = Counter(
            sample.route_path for sample in successful if sample.route_path
        )
        errors = Counter(
            sample.error_code or "unknown_error"
            for sample in samples
            if not sample.success
        )
        return {
            "schema_version": 1,
            "workload": WORKLOAD,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "requests": len(samples),
            "concurrency": self._config.concurrency,
            "completed": len(successful),
            "failed": failed,
            "error_rate": round(failed / len(samples), 6),
            "duration_seconds": round(duration_seconds, 3),
            "throughput_rps": round(len(successful) / duration_seconds, 3),
            "ttft_ms": summarize_samples(ttft_values) if ttft_values else None,
            "e2e_ms": summarize_samples(e2e_values) if e2e_values else None,
            "route_paths": dict(sorted(route_paths.items())),
            "errors": dict(sorted(errors.items())),
        }

    async def _run_one(self, index: int) -> _LoadSample:
        session_id: Optional[str] = None
        started = time.perf_counter()
        sample: _LoadSample
        try:
            session_response = await self._client.post(
                "/api/v1/ai/chat/sessions",
                json={"title": f"load-test-{index}"},
            )
            session_response.raise_for_status()
            session_id = str(session_response.json()["id"])
            run_response = await self._client.post(
                f"/api/v1/ai/chat/sessions/{session_id}/messages/runs",
                json={
                    "content": FIXED_SAFE_PROMPT,
                    "idempotency_key": f"load-test:{uuid4()}",
                },
            )
            run_response.raise_for_status()
            run_id = str(run_response.json()["run_id"])
            sample = await self._await_run(run_id, started=started)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            sample = _LoadSample(
                success=False,
                error_code=self._safe_error_code(exc),
            )
        if session_id is not None:
            try:
                cleanup = await self._client.delete(
                    f"/api/v1/ai/chat/sessions/{session_id}"
                )
                cleanup.raise_for_status()
            except httpx.HTTPError:
                return _LoadSample(
                    success=False,
                    route_path=sample.route_path,
                    error_code="session_cleanup_failed",
                )
        return sample

    async def _await_run(
        self,
        run_id: str,
        *,
        started: float,
    ) -> _LoadSample:
        deadline = time.perf_counter() + self._config.run_timeout_seconds
        last_event_id = 0
        ttft_ms: Optional[float] = None
        route_path: Optional[str] = None
        while time.perf_counter() < deadline:
            response = await self._client.get(
                f"/api/v1/agent/runs/{run_id}/events",
                headers={"Last-Event-ID": str(last_event_id)},
            )
            response.raise_for_status()
            for event in _parse_sse(response.text):
                sequence = event.get("id")
                if isinstance(sequence, int):
                    last_event_id = max(last_event_id, sequence)
                event_type = event["event"]
                data = event["data"]
                if event_type == "route.selected":
                    selected = data.get("path")
                    if selected in {"fast", "deep"}:
                        route_path = str(selected)
                elif event_type == "message.delta" and ttft_ms is None:
                    ttft_ms = (time.perf_counter() - started) * 1000
                elif event_type == "run.completed":
                    e2e_ms = (time.perf_counter() - started) * 1000
                    if route_path is None:
                        return _LoadSample(
                            success=False,
                            error_code="missing_route_event",
                        )
                    if ttft_ms is None:
                        return _LoadSample(
                            success=False,
                            route_path=route_path,
                            error_code="missing_first_token_event",
                        )
                    return _LoadSample(
                        success=True,
                        ttft_ms=ttft_ms,
                        e2e_ms=e2e_ms,
                        route_path=route_path,
                    )
                elif event_type in {"run.failed", "run.cancelled"}:
                    return _LoadSample(
                        success=False,
                        route_path=route_path,
                        error_code=event_type.replace(".", "_"),
                    )
            await asyncio.sleep(self._config.poll_interval_seconds)
        return _LoadSample(
            success=False,
            route_path=route_path,
            error_code="run_timeout",
        )

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"http_{exc.response.status_code}"
        if isinstance(exc, httpx.TimeoutException):
            return "http_timeout"
        if isinstance(exc, httpx.HTTPError):
            return "http_transport_error"
        return "invalid_api_response"


def evaluate_slos(
    report: Dict[str, object],
    *,
    max_error_rate: float,
    max_p95_ttft_ms: float,
    max_p95_e2e_ms: float,
) -> List[Dict[str, float | str]]:
    limits = (
        ("error_rate", float(report.get("error_rate", 1.0)), max_error_rate),
    )
    failures: List[Dict[str, float | str]] = []
    for metric, actual, limit in limits:
        if actual > limit:
            failures.append(
                {"metric": metric, "actual": actual, "limit": float(limit)}
            )
    for metric, report_key, limit in (
        ("ttft_ms.p95_ms", "ttft_ms", max_p95_ttft_ms),
        ("e2e_ms.p95_ms", "e2e_ms", max_p95_e2e_ms),
    ):
        summary = report.get(report_key)
        if not isinstance(summary, dict) or "p95_ms" not in summary:
            failures.append(
                {
                    "metric": f"{report_key}.samples",
                    "actual": 0.0,
                    "limit": 1.0,
                }
            )
            continue
        actual = float(summary.get("p95_ms", float("inf")))
        if actual > limit:
            failures.append(
                {"metric": metric, "actual": actual, "limit": float(limit)}
            )
    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token-env", default="B_AGENT_LOAD_TOKEN")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.1)
    parser.add_argument("--run-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ttft-ms", type=float, default=3000.0)
    parser.add_argument("--max-p95-e2e-ms", type=float, default=15000.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


async def _run_from_args(args: argparse.Namespace, token: str) -> Dict[str, object]:
    config = AgentChatLoadConfig(
        requests=args.requests,
        concurrency=args.concurrency,
        poll_interval_seconds=args.poll_interval_seconds,
        run_timeout_seconds=args.run_timeout_seconds,
    )
    timeout = httpx.Timeout(args.run_timeout_seconds + 10)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        return await AgentChatLoadRunner(client, config).run()


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"Missing authentication token in {args.token_env}")
    report = asyncio.run(_run_from_args(args, token))
    failures = evaluate_slos(
        report,
        max_error_rate=args.max_error_rate,
        max_p95_ttft_ms=args.max_p95_ttft_ms,
        max_p95_e2e_ms=args.max_p95_e2e_ms,
    )
    report["slo"] = {"passed": not failures, "failures": failures}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["slo"], ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
