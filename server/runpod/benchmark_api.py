"""Concurrent /v1/chat/completions load against the RunPod proxy (benchmark only)."""
import argparse
import asyncio
import os
import statistics
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "scripts/runs/task_pipeline.py").is_file())
DEFAULT_BASE = "https://t4r30ysyfmt164-8000.proxy.runpod.net/v1"


async def one(client: httpx.AsyncClient, sem: asyncio.Semaphore, payload: dict) -> float:
    async with sem:
        start = time.perf_counter()
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        return time.perf_counter() - start


async def sweep(base: str, key: str | None, concurrencies: list[int], requests_each: int) -> None:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = {
        "model": os.getenv("BENCHMARK_MODEL", "gemma4"),
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 16,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=300.0) as client:
        for concurrency in concurrencies:
            sem = asyncio.Semaphore(concurrency)
            start = time.perf_counter()
            latencies = await asyncio.gather(
                *[one(client, sem, payload) for _ in range(requests_each)]
            )
            elapsed = time.perf_counter() - start
            print(
                f"concurrency={concurrency} requests={requests_each} "
                f"wall_s={elapsed:.1f} rps={requests_each / elapsed:.2f} "
                f"lat_p50={statistics.median(latencies):.2f}s "
                f"lat_p95={sorted(latencies)[int(0.95 * len(latencies)) - 1]:.2f}s",
                flush=True,
            )


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=os.getenv("RUNPOD_API_BASE", DEFAULT_BASE))
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8, 16, 32])
    parser.add_argument("--requests", type=int, default=8, help="requests per concurrency level")
    args = parser.parse_args()
    key = os.getenv("VLLM_API_KEY") or None
    asyncio.run(sweep(args.api_base.rstrip("/"), key, args.concurrency, args.requests))


if __name__ == "__main__":
    main()
