"""The default model completion timeout is conservative but overridable.

A node that runs a container build (podman) inside its own session can exceed
the usual authoring-mode ceiling; such a workload raises the limit via
``REFACTORY_MODEL_TIMEOUT_S`` rather than every consumer inheriting a high
default."""

import importlib
import os

from refactory_dsh import model


def test_default_timeout_is_conservative_by_default():
    assert model.DEFAULT_TIMEOUT_S == 1800


def test_default_timeout_honors_env_override(monkeypatch):
    monkeypatch.setenv("REFACTORY_MODEL_TIMEOUT_S", "7200")
    reloaded = importlib.reload(model)
    try:
        assert reloaded.DEFAULT_TIMEOUT_S == 7200
    finally:
        # Restore the module to its env-free state so later tests see the default.
        monkeypatch.delenv("REFACTORY_MODEL_TIMEOUT_S", raising=False)
        importlib.reload(model)
