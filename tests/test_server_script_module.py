"""server.py run as a script is one module: a lazy `import server` must not run the file a second time."""

import asyncio
from collections import Counter

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from tests.pal_server_process import server_params


@pytest.mark.asyncio
async def test_lazy_import_of_server_reuses_the_running_script(tmp_path):
    # version imports "server" lazily. No real key is needed; the proxy stops its GitHub version check at once.
    params = server_params(
        tmp_path,
        OPENROUTER_API_KEY="test-key-not-real",
        https_proxy="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9",
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await asyncio.wait_for(session.call_tool("version", {}), timeout=60)
            await asyncio.wait_for(session.call_tool("version", {}), timeout=60)

    lines = (tmp_path / "mcp_activity.log").read_text().splitlines()
    assert sum("TOOL_CALL: version" in line for line in lines) == 2
    assert [line for line, count in Counter(lines).items() if count > 1] == []
