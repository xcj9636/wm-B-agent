from collections import defaultdict
from uuid import uuid4

import httpx
import pytest

from scripts.load_test_agent_chat import (
    AgentChatLoadConfig,
    AgentChatLoadRunner,
    evaluate_slos,
)


@pytest.mark.asyncio
async def test_load_runner_exercises_detached_api_and_reports_route_distribution():
    calls = defaultdict(int)

    def handler(request: httpx.Request) -> httpx.Response:
        calls[(request.method, request.url.path)] += 1
        if request.method == "POST" and request.url.path.endswith(
            "/ai/chat/sessions"
        ):
            session_id = str(uuid4())
            return httpx.Response(
                201,
                json={
                    "id": session_id,
                    "title": "load-test",
                    "use_case": "live_reply",
                    "created_at": "2026-08-10T12:00:00Z",
                    "updated_at": "2026-08-10T12:00:00Z",
                    "messages": [],
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            "/messages/runs"
        ):
            session_id = request.url.path.split("/")[-3]
            return httpx.Response(
                202,
                json={
                    "run_id": str(uuid4()),
                    "turn_id": str(uuid4()),
                    "session_id": session_id,
                    "status": "queued",
                },
            )
        if request.method == "GET" and "/agent/runs/" in request.url.path:
            body = (
                'id: 1\nevent: route.selected\ndata: {"path":"fast"}\n\n'
                'id: 2\nevent: message.delta\ndata: {"delta":"ok"}\n\n'
                'id: 3\nevent: run.completed\ndata: {"content":"ok"}\n\n'
            )
            return httpx.Response(
                200,
                text=body,
                headers={"content-type": "text/event-stream"},
            )
        if request.method == "DELETE" and "/ai/chat/sessions/" in request.url.path:
            return httpx.Response(204)
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://agent.example/api/v1",
        headers={"Authorization": "Bearer test-only"},
    ) as client:
        report = await AgentChatLoadRunner(
            client,
            AgentChatLoadConfig(
                requests=4,
                concurrency=2,
                poll_interval_seconds=0,
                run_timeout_seconds=2,
            ),
        ).run()

    assert report["workload"] == "detached-agent-api-v1"
    assert report["requests"] == 4
    assert report["completed"] == 4
    assert report["failed"] == 0
    assert report["route_paths"] == {"fast": 4}
    assert report["ttft_ms"]["samples"] == 4
    assert report["e2e_ms"]["samples"] == 4
    assert sum(
        count
        for (method, path), count in calls.items()
        if method == "DELETE" and "/ai/chat/sessions/" in path
    ) == 4


def test_slo_gate_fails_on_latency_or_error_budget_breach():
    report = {
        "error_rate": 0.02,
        "ttft_ms": {"p95_ms": 3100.0},
        "e2e_ms": {"p95_ms": 12000.0},
    }

    failures = evaluate_slos(
        report,
        max_error_rate=0.01,
        max_p95_ttft_ms=3000,
        max_p95_e2e_ms=15000,
    )

    assert failures == [
        {
            "metric": "error_rate",
            "actual": 0.02,
            "limit": 0.01,
        },
        {
            "metric": "ttft_ms.p95_ms",
            "actual": 3100.0,
            "limit": 3000.0,
        },
    ]


def test_slo_gate_rejects_reports_without_successful_latency_samples():
    report = {
        "error_rate": 1.0,
        "ttft_ms": None,
        "e2e_ms": None,
    }

    failures = evaluate_slos(
        report,
        max_error_rate=1.0,
        max_p95_ttft_ms=3000,
        max_p95_e2e_ms=15000,
    )

    assert {failure["metric"] for failure in failures} == {
        "ttft_ms.samples",
        "e2e_ms.samples",
    }
