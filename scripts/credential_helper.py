#!/usr/bin/env python3
"""
Credential helper for multi-agent workflows.

Resolves secrets (like WARP_API_KEY) from a pluggable backend so agents never
see the secret inline. Default backend is macOS Keychain via the `security`
CLI. Scaffolding is provided for 1Password CLI (`op`), HashiCorp Vault, and
AWS Secrets Manager — stubs raise NotImplementedError until wired up.

Usage:
    # Resolve a secret
    python3 credential_helper.py get WARP_API_KEY

    # Store a secret
    python3 credential_helper.py set WARP_API_KEY

    # Emit `export KEY=...` lines (for `eval $(cred-helper export ...)`)
    python3 credential_helper.py export WARP_API_KEY GITHUB_TOKEN

    # Specify backend
    python3 credential_helper.py --backend keychain get WARP_API_KEY
    python3 credential_helper.py --backend 1password get WARP_API_KEY

Programmatic:
    from scripts.credential_helper import resolve_secret
    api_key = resolve_secret("WARP_API_KEY")

Design:
    - Backends are selected via --backend or MAW_CRED_BACKEND env var.
    - Default backend (auto): env var > keychain > error.
    - Service name defaults to 'multi-agent-workflows' but is configurable
      via --service / MAW_CRED_SERVICE so users can namespace secrets.
"""
from __future__ import annotations

import argparse
import getpass
import os
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Optional


DEFAULT_SERVICE = os.environ.get("MAW_CRED_SERVICE", "multi-agent-workflows")


class CredentialBackend(ABC):
    """Abstract credential backend. Subclasses implement get/set/delete."""

    name: str = "abstract"

    @abstractmethod
    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        """Fetch secret; return None if absent."""

    @abstractmethod
    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        """Store secret (overwrite if exists)."""

    def delete(self, key: str, service: str = DEFAULT_SERVICE) -> None:
        """Delete secret. Default: raise NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not implement delete()")


class EnvBackend(CredentialBackend):
    """Read secrets from environment variables (no writes)."""

    name = "env"

    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        return os.environ.get(key)

    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        raise NotImplementedError("EnvBackend is read-only. Use `export KEY=value` in your shell.")


class KeychainBackend(CredentialBackend):
    """macOS Keychain backend via the `security` CLI.

    Stores secrets as generic passwords keyed by (service, account=key).
    Suitable for dev machines. Does NOT propagate across hosts — agents must
    have their own keychain or inject via another backend.
    """

    name = "keychain"

    def _check_platform(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("KeychainBackend requires macOS. Use --backend env or another backend.")

    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        self._check_platform()
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", key, "-w"],
                capture_output=True,
                text=True,
                check=True,
            )
            return out.stdout.rstrip("\n")
        except subprocess.CalledProcessError:
            return None

    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        self._check_platform()
        # -U updates if exists; otherwise creates
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", key, "-w", value],
            check=True,
            capture_output=True,
        )

    def delete(self, key: str, service: str = DEFAULT_SERVICE) -> None:
        self._check_platform()
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", key],
            check=True,
            capture_output=True,
        )


class OnePasswordBackend(CredentialBackend):
    """1Password CLI (`op`) backend. Scaffolding — requires user to sign in.

    Expected secret path: op://<vault>/<service>/<key>
    Vault name comes from MAW_OP_VAULT (default: 'Private').
    """

    name = "1password"

    def _vault(self) -> str:
        return os.environ.get("MAW_OP_VAULT", "Private")

    def _ref(self, key: str, service: str) -> str:
        return f"op://{self._vault()}/{service}/{key}"

    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        try:
            out = subprocess.run(
                ["op", "read", self._ref(key, service)],
                capture_output=True,
                text=True,
                check=True,
            )
            return out.stdout.rstrip("\n")
        except FileNotFoundError:
            raise RuntimeError("`op` CLI not found. Install 1Password CLI: https://developer.1password.com/docs/cli/")
        except subprocess.CalledProcessError as e:
            if "isn't an item" in (e.stderr or "") or "not found" in (e.stderr or "").lower():
                return None
            raise

    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        raise NotImplementedError(
            "1Password write is not implemented. Create items manually or use `op item create`."
        )


class VaultBackend(CredentialBackend):
    """HashiCorp Vault backend. Scaffolding only — implement if needed.

    Expected: VAULT_ADDR + VAULT_TOKEN in env; path secret/<service>/<key>.
    """

    name = "vault"

    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        raise NotImplementedError(
            "VaultBackend is scaffolded but not implemented. "
            "Contribute: use `vault kv get -field=value secret/<service>/<key>`."
        )

    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        raise NotImplementedError("VaultBackend.set not implemented.")


class AWSSecretsBackend(CredentialBackend):
    """AWS Secrets Manager backend. Scaffolding only.

    Expected: IAM credentials in env; secret id `<service>/<key>`.
    """

    name = "aws"

    def get(self, key: str, service: str = DEFAULT_SERVICE) -> Optional[str]:
        raise NotImplementedError(
            "AWSSecretsBackend is scaffolded but not implemented. "
            "Contribute: use boto3.client('secretsmanager').get_secret_value(SecretId=...)"
        )

    def set(self, key: str, value: str, service: str = DEFAULT_SERVICE) -> None:
        raise NotImplementedError("AWSSecretsBackend.set not implemented.")


BACKENDS: dict[str, type[CredentialBackend]] = {
    "env": EnvBackend,
    "keychain": KeychainBackend,
    "1password": OnePasswordBackend,
    "vault": VaultBackend,
    "aws": AWSSecretsBackend,
}


def get_backend(name: Optional[str] = None) -> CredentialBackend:
    """Return a backend instance. If name is None, check env then default."""
    name = name or os.environ.get("MAW_CRED_BACKEND")
    if not name:
        # Auto-select: macOS → keychain, else env
        name = "keychain" if sys.platform == "darwin" else "env"
    if name not in BACKENDS:
        raise ValueError(f"Unknown backend: {name}. Available: {list(BACKENDS)}")
    return BACKENDS[name]()


def resolve_secret(
    key: str,
    service: str = DEFAULT_SERVICE,
    backend: Optional[str] = None,
    fallback_env: bool = True,
) -> Optional[str]:
    """Programmatic secret resolution with env fallback.

    Resolution order (if fallback_env=True):
    1. Environment variable `key`
    2. Configured backend (keychain by default on macOS)

    Returns None if the secret is unavailable.
    """
    if fallback_env:
        val = os.environ.get(key)
        if val:
            return val
    return get_backend(backend).get(key, service)


def cmd_get(args: argparse.Namespace) -> int:
    value = get_backend(args.backend).get(args.key, args.service)
    if value is None:
        print(f"Error: secret '{args.key}' not found in backend '{args.backend or 'auto'}'", file=sys.stderr)
        return 1
    print(value)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    value = args.value or getpass.getpass(f"Enter value for {args.key}: ")
    if not value:
        print("Error: empty value not allowed", file=sys.stderr)
        return 1
    get_backend(args.backend).set(args.key, value, args.service)
    print(f"Stored '{args.key}' in backend '{args.backend or 'auto'}' (service={args.service})", file=sys.stderr)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    get_backend(args.backend).delete(args.key, args.service)
    print(f"Deleted '{args.key}' from backend '{args.backend or 'auto'}'", file=sys.stderr)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Emit `export KEY='value'` lines for shell eval."""
    backend = get_backend(args.backend)
    missing = []
    for key in args.keys:
        value = backend.get(key, args.service)
        if value is None:
            missing.append(key)
            continue
        # shlex.quote protects against shell injection in values
        print(f"export {key}={shlex.quote(value)}")
    if missing:
        print(f"# Missing secrets: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Credential helper for multi-agent workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s get WARP_API_KEY
  %(prog)s --backend keychain set WARP_API_KEY
  eval $(%(prog)s export WARP_API_KEY GITHUB_TOKEN)
""",
    )
    parser.add_argument(
        "--backend",
        choices=list(BACKENDS),
        help="Credential backend (default: auto-detect, or $MAW_CRED_BACKEND)",
    )
    parser.add_argument(
        "--service",
        default=DEFAULT_SERVICE,
        help=f"Service namespace (default: {DEFAULT_SERVICE}, or $MAW_CRED_SERVICE)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="Fetch a secret to stdout")
    p_get.add_argument("key")
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set", help="Store a secret (prompts if value omitted)")
    p_set.add_argument("key")
    p_set.add_argument("value", nargs="?", help="Value (omit to prompt via getpass)")
    p_set.set_defaults(func=cmd_set)

    p_del = sub.add_parser("delete", help="Delete a secret")
    p_del.add_argument("key")
    p_del.set_defaults(func=cmd_delete)

    p_exp = sub.add_parser("export", help="Print `export KEY=value` for eval")
    p_exp.add_argument("keys", nargs="+")
    p_exp.set_defaults(func=cmd_export)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (RuntimeError, NotImplementedError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"Backend command failed: {e.stderr or e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
