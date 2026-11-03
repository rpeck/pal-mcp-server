"""Run blocking model calls in worker threads, so that tool calls from one client overlap.

A call waits in a queue when all workers are busy. When the client cancels a tool call, a queued model
call never starts, a running subprocess (for example the Codex CLI) is killed, and no retry starts.
"""

import asyncio
import contextvars
import logging
import os
import signal
import subprocess
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

from utils.env import get_env

logger = logging.getLogger(__name__)

DEFAULT_MAX_PARALLEL_MODEL_CALLS = 16

# The ID of the tool call that the current code serves. Log lines and generated files carry it.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("pal_request_id", default="-")


class ModelCallCancelled(Exception):
    """The client canceled the tool call that started this model call."""


def kill_process_tree(process: Any) -> None:
    """Kill a process started with ``start_new_session=True`` and every process in its group.

    A CLI often runs its real work in a child process (for example an npm launcher), so killing only the direct
    child would leave that work running. Works for ``subprocess.Popen`` and ``asyncio.subprocess.Process``.
    """
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass  # The process ended already.


class CancelToken:
    """Kills the subprocesses of one model call when the client cancels the call. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen] = []
        self.cancelled = False

    def register(self, process: subprocess.Popen) -> None:
        with self._lock:
            if not self.cancelled:
                self._processes.append(process)
                return
        kill_process_tree(process)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            processes, self._processes = self._processes, []
        for process in processes:
            kill_process_tree(process)


_cancel_token: contextvars.ContextVar[Optional[CancelToken]] = contextvars.ContextVar("pal_cancel_token", default=None)


def raise_if_cancelled() -> None:
    """Raise ModelCallCancelled when the client canceled the model call that runs this code."""
    token = _cancel_token.get()
    if token is not None and token.cancelled:
        raise ModelCallCancelled("the client canceled this call")


def _max_workers() -> int:
    raw = get_env("PAL_MAX_PARALLEL_MODEL_CALLS", str(DEFAULT_MAX_PARALLEL_MODEL_CALLS))
    try:
        value = int(raw or "")
    except ValueError:
        value = 0
    if value >= 1:
        return value
    logger.warning(
        "PAL_MAX_PARALLEL_MODEL_CALLS=%r is not a positive integer; using %d", raw, DEFAULT_MAX_PARALLEL_MODEL_CALLS
    )
    return DEFAULT_MAX_PARALLEL_MODEL_CALLS


_max_parallel = _max_workers()
_executor = ThreadPoolExecutor(max_workers=_max_parallel, thread_name_prefix="pal-model-call")
_busy_lock = threading.Lock()
_busy = 0


def _release_worker(_future: Future) -> None:
    global _busy
    with _busy_lock:
        _busy -= 1


async def run_model_call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)`` in a worker thread and return its result.

    The thread sees the caller's context variables (the request ID for logging, and the cancel token).
    """
    global _busy
    token = CancelToken()
    context = contextvars.copy_context()
    context.run(_cancel_token.set, token)

    with _busy_lock:
        queued = _busy >= _max_parallel
        _busy += 1
    if queued:
        logger.info("All %d model-call workers are busy; this call waits in the queue", _max_parallel)

    future = _executor.submit(context.run, fn, *args, **kwargs)
    future.add_done_callback(_release_worker)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        # A queued call never starts (wrap_future cancels it); a running one loses its subprocesses and retries.
        token.cancel()
        logger.info("The client canceled the call; stopped its model call")
        raise


def run_cancellable(
    command: list[str],
    *,
    input: Optional[bytes] = None,
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    """Run a command like ``subprocess.run(capture_output=True)``, but a client cancel kills it.

    Raises:
        subprocess.TimeoutExpired: the command did not end within ``timeout`` seconds (it is killed).
        ModelCallCancelled: the client canceled the call while the command ran.
    """
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,  # its own process group, so a kill reaches its children too
    ) as process:
        token = _cancel_token.get()
        if token is not None:
            token.register(process)
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            process.wait()
            raise
        except BaseException:
            kill_process_tree(process)
            raise
    raise_if_cancelled()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
