#!/usr/bin/env python3
"""Setup OAuth client for context-forge MCP server."""
import asyncio
import sys
from pathlib import Path

# Add server src to path
server_src = Path(__file__).parent.parent / "services" / "server" / "src"
sys.path.insert(0, str(server_src))

from api.security import create_oauth_client


async def main():
    """Create the OAuth client for Claude Code."""
    print("Creating OAuth client for Claude Code...")

    try:
        await create_oauth_client(
            client_id="claude-code",
            name="Claude Code CLI",
            redirect_uris=[
                "http://localhost:5173/oauth/callback",
                "http://localhost:3000/oauth/callback",
                "http://127.0.0.1:5173/oauth/callback",
                "http://127.0.0.1:3000/oauth/callback",
                "http://localhost:8080/oauth/callback",
                "http://127.0.0.1:8080/oauth/callback",
            ],
            scopes="read,write",
        )
        print("✓ OAuth client created successfully!")
        print(f"  Client ID: claude-code")
        print(f"  Name: Claude Code CLI")
        print(f"  Redirect URIs: 6 URLs configured")
        print(f"  Scopes: read,write")
    except Exception as e:
        if "unique" in str(e).lower():
            print("ℹ OAuth client already exists")
        else:
            print(f"✗ Error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
