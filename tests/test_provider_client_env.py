"""Building an OpenAI-compatible client must not change os.environ: worker threads read it at the same time."""

import os

from providers.openai import OpenAIModelProvider


def test_client_ignores_proxy_variables_without_removing_them(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    removed = []
    environ_type = type(os.environ)
    original_delitem = environ_type.__delitem__

    def recording_delitem(self, key):
        removed.append(key)
        original_delitem(self, key)

    monkeypatch.setattr(environ_type, "__delitem__", recording_delitem)

    client = OpenAIModelProvider(api_key="test-key-not-real").client

    assert removed == []
    assert client._client._mounts == {}, "the client picked up the proxy from the environment"
