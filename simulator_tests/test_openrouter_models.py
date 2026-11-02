#!/usr/bin/env python3
"""
OpenRouter Model Tests

Tests that verify OpenRouter functionality including:
- Model alias resolution (flash, pro, o3, etc. map to OpenRouter equivalents)
- Multiple OpenRouter models work correctly
- Conversation continuity works with OpenRouter models
- Error handling when models are not available
"""

import json
import os

from .base_test import BaseSimulatorTest
from .log_utils import LogUtils


class OpenRouterModelsTest(BaseSimulatorTest):
    """Test OpenRouter model functionality and alias mapping"""

    @property
    def test_name(self) -> str:
        return "openrouter_models"

    @property
    def test_description(self) -> str:
        return "OpenRouter model functionality and alias mapping"

    def run_test(self) -> bool:
        """Test OpenRouter model functionality"""
        try:
            self.logger.info("Test: OpenRouter model functionality and alias mapping")

            # Check if OpenRouter API key is configured
            import os

            has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))

            if not has_openrouter:
                self.logger.info("  ⚠️  OpenRouter API key not configured - skipping test")
                self.logger.info("  ℹ️  This test requires OPENROUTER_API_KEY to be set in .env")
                return True  # Return True to indicate test is skipped, not failed

            # Setup test files for later use
            self.setup_test_files()
            log_start = self._log_size()

            # Test 1: Flash alias mapping to OpenRouter
            self.logger.info("  1: Testing the 'flash' alias through OpenRouter")

            response1, continuation_id = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Say 'Hello from Flash model!' and nothing else.",
                    "model": "flash",
                    "temperature": 0.1,
                    "working_directory_absolute_path": self.test_dir,
                },
            )

            if self._error_in(response1):
                self.logger.error(f"  ❌ Flash alias test failed: {self._error_in(response1)}")
                return False

            self.logger.info("  ✅ Flash alias call completed")
            if continuation_id:
                self.logger.info(f"  ✅ Got continuation_id: {continuation_id}")

            # Test 2: Pro alias mapping to OpenRouter
            self.logger.info("  2: Testing the 'pro' alias through OpenRouter")

            response2, _ = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Say 'Hello from Pro model!' and nothing else.",
                    "model": "pro",
                    "temperature": 0.1,
                    "working_directory_absolute_path": self.test_dir,
                },
            )

            if self._error_in(response2):
                self.logger.error(f"  ❌ Pro alias test failed: {self._error_in(response2)}")
                return False

            self.logger.info("  ✅ Pro alias call completed")

            # Test 3: O3 alias mapping to OpenRouter
            self.logger.info("  3: Testing the 'o3' alias through OpenRouter")

            response3, _ = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Say 'Hello from O3 model!' and nothing else.",
                    "model": "o3",
                    "temperature": 0.1,
                    "working_directory_absolute_path": self.test_dir,
                },
            )

            if self._error_in(response3):
                self.logger.error(f"  ❌ O3 alias test failed: {self._error_in(response3)}")
                return False

            self.logger.info("  ✅ O3 alias call completed")

            # Test 4: Direct OpenRouter model name
            self.logger.info("  4: Testing a direct OpenRouter model name (anthropic/claude-haiku-5.5)")

            response4, _ = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Say 'Hello from Claude Haiku!' and nothing else.",
                    "model": "anthropic/claude-haiku-5.5",
                    "temperature": 0.1,
                    "working_directory_absolute_path": self.test_dir,
                },
            )

            if self._error_in(response4):
                self.logger.error(f"  ❌ Direct OpenRouter model test failed: {self._error_in(response4)}")
                return False

            self.logger.info("  ✅ Direct OpenRouter model call completed")

            # Test 5: OpenRouter alias from config
            self.logger.info("  5: Testing the 'opus' alias from the OpenRouter catalog")

            response5, _ = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Say 'Hello from Opus!' and nothing else.",
                    "model": "opus",
                    "temperature": 0.1,
                    "working_directory_absolute_path": self.test_dir,
                },
            )

            if self._error_in(response5):
                self.logger.error(f"  ❌ OpenRouter alias test failed: {self._error_in(response5)}")
                return False

            self.logger.info("  ✅ OpenRouter alias call completed")

            # Test 6: Conversation continuity with OpenRouter models. Conversation memory lives in the server
            # process, so both calls go to one server session.
            self.logger.info("  6: Testing conversation continuity with OpenRouter")

            with self.mcp_session():
                response6, new_continuation_id = self.call_mcp_tool(
                    "chat",
                    {
                        "prompt": "Remember this number: 42. Reply with only the word OK.",
                        "model": "sonnet",  # Claude Sonnet via OpenRouter
                        "temperature": 0.1,
                        "working_directory_absolute_path": self.test_dir,
                    },
                )

                if self._error_in(response6) or not new_continuation_id:
                    self.logger.error(f"  ❌ Failed to start conversation: {self._error_in(response6)}")
                    return False

                # Continue the conversation
                response7, _ = self.call_mcp_tool(
                    "chat",
                    {
                        "prompt": "What was the number I told you earlier? Reply with only the number.",
                        "model": "sonnet",
                        "continuation_id": new_continuation_id,
                        "temperature": 0.1,
                        "working_directory_absolute_path": self.test_dir,
                    },
                )

            if self._error_in(response7):
                self.logger.error(f"  ❌ Failed to continue conversation: {self._error_in(response7)}")
                return False

            if "42" not in json.loads(response7).get("content", ""):
                self.logger.error("  ❌ The model did not remember the number from the earlier turn")
                return False
            self.logger.info("  ✅ Conversation continuity working with OpenRouter")

            # Test 7: Validate OpenRouter API usage from logs
            self.logger.info("  7: Validating OpenRouter API usage in logs")
            logs = self._logs_since(log_start)

            # Check for OpenRouter API calls
            openrouter_logs = [line for line in logs.split("\n") if "openrouter" in line.lower()]
            openrouter_api_logs = [line for line in logs.split("\n") if "openrouter.ai/api/v1" in line]

            # Check for specific model mappings
            flash_mapping_logs = [
                line for line in logs.split("\n") if "Resolved model alias 'flash' to 'google/" in line
            ]

            pro_mapping_logs = [line for line in logs.split("\n") if "Resolved model alias 'pro' to 'google/" in line]

            # Log findings
            self.logger.info(f"   OpenRouter-related logs: {len(openrouter_logs)}")
            self.logger.info(f"   OpenRouter API logs: {len(openrouter_api_logs)}")
            self.logger.info(f"   Flash mapping logs: {len(flash_mapping_logs)}")
            self.logger.info(f"   Pro mapping logs: {len(pro_mapping_logs)}")

            # Sample log output for debugging
            if self.verbose and openrouter_logs:
                self.logger.debug("  📋 Sample OpenRouter logs:")
                for log in openrouter_logs[:5]:
                    self.logger.debug(f"    {log}")

            # Success criteria
            openrouter_api_used = len(openrouter_api_logs) > 0
            models_mapped = len(flash_mapping_logs) > 0 or len(pro_mapping_logs) > 0

            success_criteria = [
                ("OpenRouter API calls made", openrouter_api_used),
                ("Model aliases mapped correctly", models_mapped),
                ("All model calls succeeded", True),  # We already checked this above
            ]

            passed_criteria = sum(1 for _, passed in success_criteria if passed)
            self.logger.info(f"   Success criteria met: {passed_criteria}/{len(success_criteria)}")

            for criterion, passed in success_criteria:
                status = "✅" if passed else "❌"
                self.logger.info(f"    {status} {criterion}")

            if passed_criteria >= 2:  # At least 2 out of 3 criteria
                self.logger.info("  ✅ OpenRouter model tests passed")
                return True
            else:
                self.logger.error("  ❌ OpenRouter model tests failed")
                return False

        except Exception as e:
            self.logger.error(f"OpenRouter model test failed: {e}")
            return False
        finally:
            self.cleanup_test_files()

    @staticmethod
    def _log_size() -> int:
        try:
            return os.path.getsize(LogUtils.MAIN_LOG_FILE)
        except OSError:
            return 0

    @staticmethod
    def _logs_since(offset: int) -> str:
        """The server log written after ``offset``; the whole file if it rotated in between."""
        try:
            with open(LogUtils.MAIN_LOG_FILE, encoding="utf-8", errors="replace") as log:
                if os.path.getsize(LogUtils.MAIN_LOG_FILE) >= offset:
                    log.seek(offset)
                return log.read()
        except OSError:
            return ""

    @staticmethod
    def _error_in(response):
        """The problem with a chat response, or None when it is a normal answer."""
        if not response:
            return "no response"
        try:
            data = json.loads(response)
        except ValueError:
            return response[:200]
        return data.get("content", "error")[:200] if data.get("status") == "error" else None


def main():
    """Run the OpenRouter model tests"""
    import sys

    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    test = OpenRouterModelsTest(verbose=verbose)

    success = test.run_test()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
