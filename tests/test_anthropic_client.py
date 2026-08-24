"""The Anthropic client's constructor contract, with the SDK faked out.

This module never imports the real ``anthropic`` package and never touches the network: a
fake module is injected into ``sys.modules`` before construction, so these tests run
identically whether or not the SDK is installed and whether or not a key is exported.

The transport options (``timeout``, ``max_retries``) exist for P4(c), which owns a bounded
retry policy of its own and therefore needs the SDK to add no invisible attempts beneath it.
The load-bearing guarantee for every OTHER caller is that omitting both reproduces the
previous construction EXACTLY — asserted here by dict equality, so a silently injected
default would fail.
"""

from __future__ import annotations

import sys
import types

import pytest

from harness.llm.anthropic_client import DEFAULT_MODEL, AnthropicClient


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a fake ``anthropic`` module that records the kwargs the client passes."""
    recorded: dict[str, dict] = {}

    class _FakeSDKClient:
        pass

    def _Anthropic(**kwargs):
        recorded["kwargs"] = kwargs
        return _FakeSDKClient()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_Anthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    return recorded


# ========================================================================================
# Backward compatibility: omitting both options must reproduce the old construction
# ========================================================================================
def test_omitting_transport_options_constructs_exactly_as_before(fake_anthropic):
    AnthropicClient()
    assert fake_anthropic["kwargs"] == {"api_key": "test-key"}


def test_timeout_alone_is_forwarded_and_nothing_else_is_injected(fake_anthropic):
    AnthropicClient(timeout=60.0)
    assert fake_anthropic["kwargs"] == {"api_key": "test-key", "timeout": 60.0}


def test_max_retries_alone_is_forwarded_and_nothing_else_is_injected(fake_anthropic):
    AnthropicClient(max_retries=3)
    assert fake_anthropic["kwargs"] == {"api_key": "test-key", "max_retries": 3}


def test_both_transport_options_are_forwarded(fake_anthropic):
    AnthropicClient(timeout=60.0, max_retries=0)
    assert fake_anthropic["kwargs"] == {
        "api_key": "test-key",
        "timeout": 60.0,
        "max_retries": 0,
    }


def test_zero_max_retries_is_forwarded_not_swallowed_as_falsy(fake_anthropic):
    """The P4(c) contract depends on this exact value reaching the SDK: `max_retries=0` is
    what makes the wrapper the only retry layer."""
    AnthropicClient(max_retries=0)
    assert fake_anthropic["kwargs"]["max_retries"] == 0


def test_zero_timeout_is_forwarded_not_swallowed_as_falsy(fake_anthropic):
    AnthropicClient(timeout=0.0)
    assert fake_anthropic["kwargs"]["timeout"] == 0.0


def test_transport_options_are_keyword_only(fake_anthropic):
    with pytest.raises(TypeError):
        AnthropicClient("test-key", None, 0.0, 1024, 60.0)  # type: ignore[misc]


# ========================================================================================
# Pre-existing behavior, unchanged
# ========================================================================================
def test_missing_api_key_raises_before_importing_the_sdk(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicClient()


def test_explicit_api_key_is_used_and_forwarded(fake_anthropic):
    AnthropicClient(api_key="explicit-key")
    assert fake_anthropic["kwargs"] == {"api_key": "explicit-key"}


def test_model_defaults_and_env_override(fake_anthropic, monkeypatch):
    assert AnthropicClient()._model == DEFAULT_MODEL
    assert AnthropicClient(model="pinned-model")._model == "pinned-model"
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    assert AnthropicClient()._model == "env-model"
    assert AnthropicClient(model="explicit-wins")._model == "explicit-wins"


def test_temperature_and_max_tokens_defaults_are_unchanged(fake_anthropic):
    client = AnthropicClient()
    assert client._temperature == 0.0
    assert client._max_tokens == 1024
    pinned = AnthropicClient(temperature=0.0, max_tokens=2048)
    assert pinned._max_tokens == 2048
