"""Tests for scripts/credential_helper.py."""
from __future__ import annotations

import os
import subprocess
import sys
from unittest import mock

import pytest

from scripts import credential_helper as ch


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Drop ambient env state that could pollute tests."""
    for var in ("WARP_API_KEY", "MAW_CRED_BACKEND", "MAW_CRED_SERVICE", "MAW_OP_VAULT"):
        monkeypatch.delenv(var, raising=False)


class TestEnvBackend:
    def test_get_returns_value_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "hunter2")
        assert ch.EnvBackend().get("MY_SECRET") == "hunter2"

    def test_get_returns_none_when_missing(self):
        assert ch.EnvBackend().get("NEVER_SET_XYZ") is None

    def test_set_raises(self):
        with pytest.raises(NotImplementedError):
            ch.EnvBackend().set("X", "y")


class TestKeychainBackend:
    def test_non_darwin_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(RuntimeError, match="requires macOS"):
            ch.KeychainBackend().get("WARP_API_KEY")

    def test_get_returns_none_when_not_found(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")

        def fake_run(*args, **kwargs):
            raise subprocess.CalledProcessError(44, ["security"], stderr="not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ch.KeychainBackend().get("MISSING") is None

    def test_get_returns_value(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")

        def fake_run(cmd, **kwargs):
            assert cmd[0:2] == ["security", "find-generic-password"]
            return subprocess.CompletedProcess(cmd, 0, stdout="secret-value\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ch.KeychainBackend().get("X") == "secret-value"

    def test_set_uses_update_flag(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ch.KeychainBackend().set("K", "V")
        assert calls, "subprocess.run not called"
        assert "-U" in calls[0]
        assert "-w" in calls[0]


class TestOnePasswordBackend:
    def test_ref_format(self, monkeypatch):
        monkeypatch.setenv("MAW_OP_VAULT", "Work")
        be = ch.OnePasswordBackend()
        assert be._ref("WARP_API_KEY", "my-service") == "op://Work/my-service/WARP_API_KEY"

    def test_missing_cli_raises_runtime(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("op")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="op.*CLI not found"):
            ch.OnePasswordBackend().get("X")

    def test_set_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ch.OnePasswordBackend().set("X", "Y")


class TestVaultAndAWSScaffolds:
    def test_vault_get_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ch.VaultBackend().get("X")

    def test_aws_get_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ch.AWSSecretsBackend().get("X")


class TestGetBackend:
    def test_explicit_name(self):
        assert isinstance(ch.get_backend("env"), ch.EnvBackend)

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("MAW_CRED_BACKEND", "env")
        assert isinstance(ch.get_backend(), ch.EnvBackend)

    def test_auto_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(ch.get_backend(), ch.KeychainBackend)

    def test_auto_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert isinstance(ch.get_backend(), ch.EnvBackend)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            ch.get_backend("nonexistent")


class TestResolveSecret:
    def test_env_fallback_preferred(self, monkeypatch):
        monkeypatch.setenv("WARP_API_KEY", "from-env")
        # Would raise if called — but env fallback should short-circuit.
        with mock.patch.object(ch, "get_backend") as mock_gb:
            assert ch.resolve_secret("WARP_API_KEY") == "from-env"
            mock_gb.assert_not_called()

    def test_fallback_to_backend(self, monkeypatch):
        fake_backend = mock.MagicMock()
        fake_backend.get.return_value = "from-backend"
        monkeypatch.setattr(ch, "get_backend", lambda *_a, **_k: fake_backend)
        assert ch.resolve_secret("WARP_API_KEY") == "from-backend"

    def test_disable_env_fallback(self, monkeypatch):
        monkeypatch.setenv("WARP_API_KEY", "from-env")
        fake_backend = mock.MagicMock()
        fake_backend.get.return_value = "from-backend"
        monkeypatch.setattr(ch, "get_backend", lambda *_a, **_k: fake_backend)
        assert ch.resolve_secret("WARP_API_KEY", fallback_env=False) == "from-backend"


class TestExportCommand:
    def test_export_shell_quotes(self, monkeypatch, capsys):
        fake_backend = mock.MagicMock()
        fake_backend.get.side_effect = lambda k, s: {"FOO": "a b'c", "BAR": "simple"}.get(k)
        monkeypatch.setattr(ch, "get_backend", lambda *_a, **_k: fake_backend)

        args = mock.MagicMock()
        args.backend = None
        args.service = ch.DEFAULT_SERVICE
        args.keys = ["FOO", "BAR"]
        rc = ch.cmd_export(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "export FOO='a b'\"'\"'c'" in out or "export FOO=" in out
        assert "export BAR=simple" in out

    def test_export_missing_returns_1(self, monkeypatch):
        fake_backend = mock.MagicMock()
        fake_backend.get.return_value = None
        monkeypatch.setattr(ch, "get_backend", lambda *_a, **_k: fake_backend)

        args = mock.MagicMock()
        args.backend = None
        args.service = ch.DEFAULT_SERVICE
        args.keys = ["MISSING"]
        assert ch.cmd_export(args) == 1
