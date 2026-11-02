#!/usr/bin/env python3
"""
Base Test Class for Communication Simulator Tests

Provides common functionality and utilities for all simulator tests.
"""

import contextlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from typing import Optional

from .log_utils import LogUtils


class BaseSimulatorTest:
    """Base class for all communication simulator tests"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.test_files = {}
        self.test_dir = None
        self._session = None  # the server process that mcp_session() keeps open

        # Configure logging first
        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")
        self.logger = logging.getLogger(self.__class__.__name__)

        self.python_path = self._get_python_path()

    def _get_python_path(self) -> str:
        """Get the Python path for the virtual environment"""
        current_dir = os.getcwd()

        # Try .venv first (modern convention)
        venv_python = os.path.join(current_dir, ".venv", "bin", "python")
        if os.path.exists(venv_python):
            return venv_python

        # Try venv as fallback
        venv_python = os.path.join(current_dir, "venv", "bin", "python")
        if os.path.exists(venv_python):
            return venv_python

        # Try .pal_venv as fallback
        pal_venv_python = os.path.join(current_dir, ".pal_venv", "bin", "python")
        if os.path.exists(pal_venv_python):
            return pal_venv_python

        # Fallback to system python if venv doesn't exist
        self.logger.warning("Virtual environment not found, using system python")
        return "python"

    def setup_test_files(self):
        """Create test files for the simulation"""
        # Test Python file
        python_content = '''"""
Sample Python module for testing MCP conversation continuity
"""

def fibonacci(n):
    """Calculate fibonacci number recursively"""
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

def factorial(n):
    """Calculate factorial iteratively"""
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result

class Calculator:
    """Simple calculator class"""

    def __init__(self):
        self.history = []

    def add(self, a, b):
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        return result

    def multiply(self, a, b):
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        return result
'''

        # Test configuration file
        config_content = """{
  "database": {
    "host": "localhost",
    "port": 5432,
    "name": "testdb",
    "ssl": true
  },
  "cache": {
    "redis_url": "redis://localhost:6379",
    "ttl": 3600
  },
  "logging": {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  }
}"""

        # Create files in the current project directory
        current_dir = os.getcwd()
        self.test_dir = os.path.join(current_dir, "test_simulation_files")
        os.makedirs(self.test_dir, exist_ok=True)

        test_py = os.path.join(self.test_dir, "test_module.py")
        test_config = os.path.join(self.test_dir, "config.json")

        with open(test_py, "w") as f:
            f.write(python_content)
        with open(test_config, "w") as f:
            f.write(config_content)

        # Ensure absolute paths for MCP server compatibility
        self.test_files = {"python": os.path.abspath(test_py), "config": os.path.abspath(test_config)}
        self.logger.debug(f"Created test files with absolute paths: {list(self.test_files.values())}")

    @contextlib.contextmanager
    def mcp_session(self):
        """Send every call_mcp_tool inside the block to one server process.

        Conversation memory lives in the server process, so a continuation_id works only within one session.
        Outside the block, each call_mcp_tool starts its own server process.
        """
        session = _ServerSession(self.python_path)
        self._session = session
        try:
            yield
        finally:
            self._session = None
            returncode, stderr = session.close()
            if returncode not in (0, None):
                self.logger.error(f"Standalone server failed with return code {returncode}")
                self.logger.error(f"Stderr: {stderr}")

    def call_mcp_tool(self, tool_name: str, params: dict) -> tuple[Optional[str], Optional[str]]:
        """Call an MCP tool via standalone server"""
        try:
            if self._session is None:
                with self.mcp_session():
                    return self._call_in_session(tool_name, params)
            return self._call_in_session(tool_name, params)
        except subprocess.TimeoutExpired:
            self.logger.error(f"MCP tool call timed out after 1 hour: {tool_name}")
            return None, None
        except Exception as e:
            self.logger.error(f"MCP tool call failed: {e}")
            return None, None

    def _call_in_session(self, tool_name: str, params: dict) -> tuple[Optional[str], Optional[str]]:
        self.logger.debug(f"Calling MCP tool {tool_name}")
        request_id, stdout = self._session.request("tools/call", {"name": tool_name, "arguments": params})

        # Parse the response - look for the tool call response
        response_data = self._parse_mcp_response(stdout, expected_id=request_id)
        if not response_data:
            return None, None

        # Extract continuation_id if present
        continuation_id = self._extract_continuation_id(response_data)

        return response_data, continuation_id

    def _parse_mcp_response(self, stdout: str, expected_id: int = 2) -> Optional[str]:
        """Parse MCP JSON-RPC response from stdout"""
        try:
            lines = stdout.strip().split("\n")
            for line in lines:
                if line.strip() and line.startswith("{"):
                    response = json.loads(line)
                    # Look for the tool call response with the expected ID
                    if response.get("id") == expected_id and "result" in response:
                        # Extract the actual content from the response
                        result = response["result"]
                        # Handle new response format with 'content' array
                        if isinstance(result, dict) and "content" in result:
                            content_array = result["content"]
                            if isinstance(content_array, list) and len(content_array) > 0:
                                return content_array[0].get("text", "")
                        # Handle legacy format
                        elif isinstance(result, list) and len(result) > 0:
                            return result[0].get("text", "")
                    elif response.get("id") == expected_id and "error" in response:
                        self.logger.error(f"MCP error: {response['error']}")
                        return None

            # If we get here, log all responses for debugging
            self.logger.warning(f"No valid tool call response found for ID {expected_id}")
            self.logger.warning(f"Full stdout: {stdout}")
            self.logger.warning(f"Total stdout lines: {len(lines)}")
            for i, line in enumerate(lines[:10]):  # Log first 10 lines
                self.logger.warning(f"Line {i}: {line[:100]}...")
            return None

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse MCP response: {e}")
            self.logger.debug(f"Stdout that failed to parse: {stdout}")
            return None

    def _extract_continuation_id(self, response_text: str) -> Optional[str]:
        """Extract continuation_id from response metadata"""
        try:
            # Parse the response text as JSON to look for continuation metadata
            response_data = json.loads(response_text)

            # Look for continuation_id in various places
            if isinstance(response_data, dict):
                # Check for direct continuation_id field (new workflow tools)
                if "continuation_id" in response_data:
                    return response_data["continuation_id"]

                # Check metadata
                metadata = response_data.get("metadata", {})
                if "thread_id" in metadata:
                    return metadata["thread_id"]

                # Check follow_up_request
                follow_up = response_data.get("follow_up_request", {})
                if follow_up and "continuation_id" in follow_up:
                    return follow_up["continuation_id"]

                # Check continuation_offer
                continuation_offer = response_data.get("continuation_offer", {})
                if continuation_offer and "continuation_id" in continuation_offer:
                    return continuation_offer["continuation_id"]

            self.logger.debug(f"No continuation_id found in response: {response_data}")
            return None

        except json.JSONDecodeError as e:
            self.logger.debug(f"Failed to parse response for continuation_id: {e}")
            return None

    def run_command(self, cmd: list[str], check: bool = True, capture_output: bool = False, **kwargs):
        """Run a shell command with logging"""
        if self.verbose:
            self.logger.debug(f"Running: {' '.join(cmd)}")

        return subprocess.run(cmd, check=check, capture_output=capture_output, **kwargs)

    def create_additional_test_file(self, filename: str, content: str) -> str:
        """Create an additional test file for mixed scenario testing"""
        if not hasattr(self, "test_dir") or not self.test_dir:
            raise RuntimeError("Test directory not initialized. Call setup_test_files() first.")

        file_path = os.path.join(self.test_dir, filename)
        with open(file_path, "w") as f:
            f.write(content)
        # Return absolute path for MCP server compatibility
        return os.path.abspath(file_path)

    def cleanup_test_files(self):
        """Clean up test files"""
        if hasattr(self, "test_dir") and self.test_dir and os.path.exists(self.test_dir):
            import shutil

            shutil.rmtree(self.test_dir)
            self.logger.debug(f"Removed test files directory: {self.test_dir}")

    # ============================================================================
    # Log Utility Methods (delegate to LogUtils)
    # ============================================================================

    def get_server_logs_since(self, since_time: Optional[str] = None) -> str:
        """Get server logs from both main and activity log files."""
        return LogUtils.get_server_logs_since(since_time)

    def get_recent_server_logs(self, lines: int = 500) -> str:
        """Get recent server logs from the main log file."""
        return LogUtils.get_recent_server_logs(lines)

    def get_server_logs_subprocess(self, lines: int = 500) -> str:
        """Get server logs using subprocess (alternative method)."""
        return LogUtils.get_server_logs_subprocess(lines)

    def check_server_logs_for_errors(self, lines: int = 500) -> list[str]:
        """Check server logs for error messages."""
        return LogUtils.check_server_logs_for_errors(lines)

    def extract_conversation_usage_logs(self, logs: str) -> list[dict[str, int]]:
        """Extract token budget calculation information from logs."""
        return LogUtils.extract_conversation_usage_logs(logs)

    def extract_conversation_token_usage(self, logs: str) -> list[int]:
        """Extract conversation token usage values from logs."""
        return LogUtils.extract_conversation_token_usage(logs)

    def extract_thread_creation_logs(self, logs: str) -> list[dict[str, str]]:
        """Extract thread creation logs with parent relationships."""
        return LogUtils.extract_thread_creation_logs(logs)

    def extract_history_traversal_logs(self, logs: str) -> list[dict[str, any]]:
        """Extract conversation history traversal logs."""
        return LogUtils.extract_history_traversal_logs(logs)

    def validate_file_deduplication_in_logs(self, logs: str, tool_name: str, test_file: str) -> bool:
        """Validate that logs show file deduplication behavior."""
        return LogUtils.validate_file_deduplication_in_logs(logs, tool_name, test_file)

    def search_logs_for_pattern(
        self, pattern: str, logs: Optional[str] = None, case_sensitive: bool = False
    ) -> list[str]:
        """Search logs for a specific pattern."""
        return LogUtils.search_logs_for_pattern(pattern, logs, case_sensitive)

    def get_log_file_info(self) -> dict[str, dict[str, any]]:
        """Get information about log files."""
        return LogUtils.get_log_file_info()

    def run_test(self) -> bool:
        """Run the test - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement run_test()")

    @property
    def test_name(self) -> str:
        """Get the test name - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement test_name property")

    @property
    def test_description(self) -> str:
        """Get the test description - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement test_description property")


class _ServerSession:
    """One PAL server process over stdio.

    Stdin stays open until close(), as a real MCP client keeps it: when stdin closes, the MCP SDK cancels every
    request that still runs. Stderr goes to a file, so a full pipe cannot block the server.
    """

    TIMEOUT_SECONDS = 3600  # for one request

    def __init__(self, python_path: str):
        self._stderr = tempfile.TemporaryFile(mode="w+")
        self.process = subprocess.Popen(
            [python_path, "server.py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, text=True
        )
        self._next_id = 1
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "communication-simulator", "version": "1.0.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> tuple[int, str]:
        """Send one request; return its ID and the stdout lines up to and including its response."""
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        timed_out = threading.Event()
        timer = threading.Timer(self.TIMEOUT_SECONDS, lambda: (timed_out.set(), self.process.kill()))
        timer.start()
        lines = []
        try:
            for line in self.process.stdout:
                lines.append(line)
                if _is_response_to(line, request_id):
                    break
        finally:
            timer.cancel()
        if timed_out.is_set():
            raise subprocess.TimeoutExpired(self.process.args, self.TIMEOUT_SECONDS)
        return request_id, "".join(lines)

    def close(self) -> tuple[Optional[int], str]:
        """Close stdin, let the server exit, and return its exit code and stderr."""
        try:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        self._stderr.seek(0)
        stderr = self._stderr.read()
        self._stderr.close()
        return self.process.returncode, stderr


def _is_response_to(line: str, expected_id: int) -> bool:
    """Whether one stdout line is the JSON-RPC response with the given ID."""
    try:
        message = json.loads(line)
    except ValueError:
        return False
    return isinstance(message, dict) and message.get("id") == expected_id
