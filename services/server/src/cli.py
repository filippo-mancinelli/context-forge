#!/usr/bin/env python3
"""context-forge CLI for agent authentication and configuration."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx

CONFIG_DIR = Path.home() / ".context-forge"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load configuration from file."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config: dict) -> None:
    """Save configuration to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def cmd_configure(args: argparse.Namespace) -> None:
    """Configure CLI with API key and server URL."""
    config = load_config()

    if args.api_key:
        # Validate the API key by testing it
        server_url = args.server_url or config.get("server_url") or os.getenv("CONTEXT_FORGE_URL", "http://localhost:4000")
        print(f"Validating API key against {server_url}...")

        try:
            response = httpx.post(
                f"{server_url}/api/mcp/keys/validate",
                headers={"X-API-Key": args.api_key},
                timeout=10.0,
            )
            if response.status_code == 200:
                key_info = response.json()["key"]
                print(f"✓ API key validated: {key_info['name']} (scope: {key_info['scope']})")
                config["api_key"] = args.api_key
                config["server_url"] = server_url
                save_config(config)
                print(f"✓ Configuration saved to {CONFIG_FILE}")
            else:
                print(f"✗ API key validation failed: {response.status_code}")
                sys.exit(1)
        except Exception as e:
            print(f"✗ Failed to validate API key: {e}")
            sys.exit(1)

    if args.server_url:
        config["server_url"] = args.server_url
        save_config(config)
        print(f"✓ Server URL set to: {args.server_url}")

    if not args.api_key and not args.server_url:
        # Show current configuration
        print("Current configuration:")
        print(f"  Server URL: {config.get('server_url', 'not set')}")
        print(f"  API Key: {'configured' if config.get('api_key') else 'not set'}")
        print(f"\nConfig file: {CONFIG_FILE}")


def cmd_test(args: argparse.Namespace) -> None:
    """Test connection to context-forge server."""
    config = load_config()
    server_url = args.server_url or config.get("server_url") or os.getenv("CONTEXT_FORGE_URL", "http://localhost:4000")
    api_key = args.api_key or config.get("api_key") or os.getenv("CONTEXT_FORGE_API_KEY")

    if not api_key:
        print("✗ No API key configured. Use: forge-cli configure --api-key YOUR_KEY")
        sys.exit(1)

    print(f"Testing connection to {server_url}...")

    try:
        response = httpx.post(
            f"{server_url}/api/mcp/keys/validate",
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )
        if response.status_code == 200:
            key_info = response.json()["key"]
            print(f"✓ Connection successful!")
            print(f"  Key: {key_info['name']}")
            print(f"  Scope: {key_info['scope']}")
        else:
            print(f"✗ Connection failed: {response.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        sys.exit(1)


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="context-forge CLI - Configure and test agent authentication",
        prog="forge-cli",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Configure command
    configure_parser = subparsers.add_parser("configure", help="Configure CLI settings")
    configure_parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Set MCP API key (format: forge_...)",
    )
    configure_parser.add_argument(
        "--server-url",
        metavar="URL",
        help="Set context-forge server URL (default: http://localhost:4000)",
    )
    configure_parser.set_defaults(func=cmd_configure)

    # Test command
    test_parser = subparsers.add_parser("test", help="Test connection to server")
    test_parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Override configured API key",
    )
    test_parser.add_argument(
        "--server-url",
        metavar="URL",
        help="Override configured server URL",
    )
    test_parser.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
