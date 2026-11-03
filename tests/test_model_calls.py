"""Worker pool, queue, cancellation and request IDs of utils.model_calls."""

import asyncio
import logging
import os
import subprocess
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


# A launcher that starts the real work in a child process with the same pipes, as an npm-installed CLI does.
# The grandchild writes its PID to the file named by the first argument.
LAUNCHER = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    "'import os, sys, time; open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(30)', sys.argv[1]]); "
    "time.sleep(30)"
)


def process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_cancel_kills_the_subprocess_and_its_children(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    outcome = []

    def codex_like_call():
        start = time.monotonic()
        try:
            run_cancellable([sys.executable, "-c", LAUNCHER, str(pid_file)], timeout=60)
        except ModelCallCancelled:
            outcome.append(time.monotonic() - start)

    task = asyncio.create_task(run_model_call(codex_like_call))
    await wait_until(lambda: pid_file.exists() and pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await wait_until(lambda: outcome)
    assert outcome[0] < 5, "the call ran on after the cancel"
    await wait_until(lambda: not process_exists(int(pid_file.read_text())))


def test_timeout_kills_the_subprocess_and_its_children(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_cancellable([sys.executable, "-c", LAUNCHER, str(pid_file)], timeout=1)

    assert time.monotonic() - start < 5, "the timeout waited for the child process"
    deadline = time.monotonic() + 5
    while process_exists(int(pid_file.read_text())):
        assert time.monotonic() < deadline, "the child process ran on after the timeout"
        time.sleep(0.05)


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
