"""Worker pool, queue, cancellation and request IDs of utils.model_calls."""

import asyncio
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from providers.base import ModelProvider
from server import LocalTimeFormatter
from utils import model_calls
from utils.model_calls import ModelCallCancelled, request_id_var, run_cancellable, run_model_call


async def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_cancel_kills_the_running_subprocess():
    outcome = []

    def codex_like_call():
        start = time.monotonic()
        try:
            run_cancellable([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60)
        except ModelCallCancelled:
            outcome.append(time.monotonic() - start)

    task = asyncio.create_task(run_model_call(codex_like_call))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await wait_until(lambda: outcome)
    assert outcome[0] < 5, "the subprocess ran on after the cancel"


@pytest.mark.asyncio
async def test_calls_queue_when_all_workers_are_busy_and_a_canceled_queued_call_never_starts(monkeypatch, caplog):
    monkeypatch.setattr(model_calls, "_executor", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(model_calls, "_max_parallel", 1)
    release = threading.Event()
    ran = []

    first = asyncio.create_task(run_model_call(lambda: release.wait(5) and ran.append("first")))
    await asyncio.sleep(0.1)
    with caplog.at_level(logging.INFO, logger="utils.model_calls"):
        queued = asyncio.create_task(run_model_call(lambda: ran.append("queued")))
        await asyncio.sleep(0.1)
    assert "waits in the queue" in caplog.text

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await first
    await asyncio.sleep(0.1)

    assert ran == ["first"]


@pytest.mark.asyncio
async def test_no_retry_starts_after_a_cancel():
    attempts = []
    started = threading.Event()
    finish = threading.Event()

    class Retrying:
        _is_error_retryable = staticmethod(lambda error: True)

    def operation():
        attempts.append(1)
        started.set()
        finish.wait(5)
        raise RuntimeError("429 rate limit")

    def provider_call():
        try:
            ModelProvider._run_with_retries(Retrying(), operation, max_attempts=4, delays=[0])
        except ModelCallCancelled:
            attempts.append("stopped")

    task = asyncio.create_task(run_model_call(provider_call))
    await wait_until(started.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish.set()

    await wait_until(lambda: "stopped" in attempts)
    assert attempts == [1, "stopped"]


@pytest.mark.asyncio
async def test_worker_threads_see_the_request_id_and_log_lines_show_it():
    request_id_var.set("3f9a1c2e")
    assert await run_model_call(request_id_var.get) == "3f9a1c2e"

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    assert LocalTimeFormatter("[%(request_id)s] %(message)s").format(record) == "[3f9a1c2e] hello"
