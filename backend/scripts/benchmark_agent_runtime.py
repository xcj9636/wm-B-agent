"""Run a fixed synthetic Agent Runtime workload and emit comparable JSON.

This benchmark measures framework/runtime overhead without contacting a real
provider. Production provider SLOs remain the responsibility of deployment
load tests; this script gives CI a deterministic regression gate.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import time
from typing import Dict, List
from uuid import UUID

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.knowledge import KnowledgeRetrievalService, RawKnowledgeCandidate
from app.services.knowledge_cache import KnowledgeCacheScope
from app.services.llm.contracts import LLMRequest, LLMStreamChunk, LLMUseCase
from app.services.llm.service import LLMService


WORKLOAD = "synthetic-fixed-v1"
ORG_ID = UUID("ba6e0000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("ba6e0000-0000-0000-0000-000000000002")


def _percentile(sorted_values: List[float], percentile: int) -> float:
    rank = max(1, math.ceil((percentile / 100) * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize_samples(samples: List[float]) -> Dict[str, float | int]:
    if not samples:
        raise ValueError("benchmark samples cannot be empty")
    values = sorted(samples)

    def rounded(value: float) -> float:
        return round(value, 3)

    return {
        "samples": len(values),
        "min_ms": rounded(values[0]),
        "p50_ms": rounded(_percentile(values, 50)),
        "p95_ms": rounded(_percentile(values, 95)),
        "p99_ms": rounded(_percentile(values, 99)),
        "max_ms": rounded(values[-1]),
    }


class _FixedStreamingBackend:
    supports_stream = True

    async def stream(self, request: LLMRequest):
        await asyncio.sleep(0.001)
        yield LLMStreamChunk(request_id=request.request_id, delta="Verified ")
        await asyncio.sleep(0.001)
        yield LLMStreamChunk(request_id=request.request_id, delta="response")


async def _fast_chat_once() -> tuple[float, float]:
    service = LLMService(_FixedStreamingBackend())
    started = time.perf_counter()
    ttft_ms = None
    async for chunk in service.stream(
        LLMUseCase.LIVE_REPLY,
        [{"role": "user", "content": "Give a concise product answer"}],
    ):
        if chunk.delta and ttft_ms is None:
            ttft_ms = (time.perf_counter() - started) * 1000
    e2e_ms = (time.perf_counter() - started) * 1000
    return ttft_ms or e2e_ms, e2e_ms


class _BenchmarkCache:
    def __init__(self) -> None:
        self.values = {}

    async def get(self, key):
        return self.values.get(key.digest())

    async def set(self, key, candidates):
        self.values[key.digest()] = candidates


class _BenchmarkKnowledgeBackend:
    async def cache_scope(self, *, org_id):
        return KnowledgeCacheScope(
            acl_policy_version="benchmark-acl-v1",
            index_version="benchmark-index-v1",
        )

    async def search(self, *, namespace, query, filters, limit):
        await asyncio.sleep(0.001)
        return [
            RawKnowledgeCandidate(
                candidate_id="benchmark-chunk-1",
                org_id=ORG_ID,
                document_id=DOCUMENT_ID,
                document_version=1,
                acl_policy_version="benchmark-document-acl-v1",
                index_version="benchmark-document-index-v1",
                chunk_id="chunk-1",
                content="Verified MOQ is 500 units.",
                source_ref="benchmark-catalog#chunk=1",
                authority="approved_document",
                sensitivity=Sensitivity.INTERNAL,
                valid_at=datetime.now(timezone.utc),
                score=1.0,
            )
        ]

    async def validate_cached_candidate(self, candidate):
        return all(
            (
                candidate.org_id == ORG_ID,
                candidate.document_id == DOCUMENT_ID,
                candidate.content == "Verified MOQ is 500 units.",
                candidate.source_ref == "benchmark-catalog#chunk=1",
                candidate.authority == "approved_document",
                candidate.sensitivity == Sensitivity.INTERNAL,
            )
        )


class _BenchmarkACL:
    def authorize(self, **kwargs):
        return kwargs["document_id"] == DOCUMENT_ID


def _benchmark_principal() -> ExecutionPrincipal:
    return ExecutionPrincipal(
        org_id=ORG_ID,
        user_id=1,
        roles={"sales"},
        entitlements_hash="a" * 64,
        authn_context="benchmark",
    )


async def _rag_cache_once() -> tuple[float, float]:
    service = KnowledgeRetrievalService(
        _BenchmarkKnowledgeBackend(),
        _BenchmarkACL(),
        cache=_BenchmarkCache(),
    )
    started = time.perf_counter()
    await service.retrieve(
        principal=_benchmark_principal(),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    await service.retrieve(
        principal=_benchmark_principal(),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    warm_ms = (time.perf_counter() - started) * 1000
    return cold_ms, warm_ms


async def _concurrent_chat_once(concurrency: int = 8) -> float:
    started = time.perf_counter()
    await asyncio.gather(*(_fast_chat_once() for _ in range(concurrency)))
    return (time.perf_counter() - started) * 1000


async def run_benchmarks(scenario: str, *, samples: int) -> Dict[str, object]:
    if scenario not in {"all", "fast_chat", "rag_cache", "concurrent_chat"}:
        raise ValueError("unknown benchmark scenario")
    if not 1 <= samples <= 10000:
        raise ValueError("samples must be between 1 and 10000")

    scenarios: Dict[str, object] = {}
    if scenario in {"all", "fast_chat"}:
        ttft_samples = []
        e2e_samples = []
        for _ in range(samples):
            ttft_ms, e2e_ms = await _fast_chat_once()
            ttft_samples.append(ttft_ms)
            e2e_samples.append(e2e_ms)
        scenarios["fast_chat"] = {
            "ttft_ms": summarize_samples(ttft_samples),
            "e2e_ms": summarize_samples(e2e_samples),
        }
    if scenario in {"all", "rag_cache"}:
        cold_samples = []
        warm_samples = []
        for _ in range(samples):
            cold_ms, warm_ms = await _rag_cache_once()
            cold_samples.append(cold_ms)
            warm_samples.append(warm_ms)
        scenarios["rag_cache"] = {
            "cold_ms": summarize_samples(cold_samples),
            "warm_ms": summarize_samples(warm_samples),
        }
    if scenario in {"all", "concurrent_chat"}:
        concurrent_samples = [
            await _concurrent_chat_once() for _ in range(samples)
        ]
        scenarios["concurrent_chat"] = {
            "e2e_ms": summarize_samples(concurrent_samples),
            "concurrency": 8,
        }

    return {
        "schema_version": 1,
        "workload": WORKLOAD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "scenarios": scenarios,
    }


def compare_benchmarks(
    baseline: Dict[str, object],
    candidate: Dict[str, object],
    *,
    max_regression_percent: float,
) -> List[Dict[str, object]]:
    if max_regression_percent < 0:
        raise ValueError("max regression percent cannot be negative")
    regressions = []
    baseline_scenarios = baseline.get("scenarios", {})
    candidate_scenarios = candidate.get("scenarios", {})
    for scenario, baseline_metrics in baseline_scenarios.items():
        candidate_metrics = candidate_scenarios.get(scenario, {})
        for metric, baseline_summary in baseline_metrics.items():
            if not isinstance(baseline_summary, dict):
                continue
            candidate_summary = candidate_metrics.get(metric)
            if not isinstance(candidate_summary, dict):
                continue
            baseline_p95 = float(baseline_summary.get("p95_ms", 0))
            candidate_p95 = float(candidate_summary.get("p95_ms", 0))
            if baseline_p95 <= 0:
                continue
            regression = ((candidate_p95 - baseline_p95) / baseline_p95) * 100
            if regression > max_regression_percent:
                regressions.append(
                    {
                        "scenario": scenario,
                        "metric": metric,
                        "baseline_p95_ms": baseline_p95,
                        "candidate_p95_ms": candidate_p95,
                        "regression_percent": round(regression, 3),
                    }
                )
    return regressions


def write_report(path: Path, report: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=("all", "fast_chat", "rag_cache", "concurrent_chat"),
        default="all",
    )
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--max-regression-percent", type=float, default=20.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(
        run_benchmarks(args.scenario, samples=args.samples)
    )
    write_report(args.output, report)
    if args.baseline is None:
        return 0
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    regressions = compare_benchmarks(
        baseline,
        report,
        max_regression_percent=args.max_regression_percent,
    )
    if regressions:
        print(json.dumps({"regressions": regressions}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
