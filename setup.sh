#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# context-forge setup wizard
# ─────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

header() { echo -e "\n${BLUE}▶ $1${NC}"; }
ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
warn()   { echo -e "  ${YELLOW}!${NC} $1"; }
err()    { echo -e "  ${RED}✗${NC} $1"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       context-forge setup            ║"
echo "╚══════════════════════════════════════╝"

# ─── Prerequisites ────────────────────────────────────────
header "Checking prerequisites"

if ! command -v docker &>/dev/null; then
    err "Docker not found. Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

if ! docker compose version &>/dev/null; then
    err "Docker Compose v2 not found. Update Docker or install the plugin."
    exit 1
fi
ok "Docker Compose $(docker compose version --short)"

# ─── .env ─────────────────────────────────────────────────
header "Environment configuration"

if [ -f .env ]; then
    warn ".env already exists, skipping creation"
else
    cp .env.example .env
    ok "Created .env from .env.example"

    # Generate a random Postgres password
    if command -v openssl &>/dev/null; then
        PG_PASS=$(openssl rand -base64 24 | tr -d '/+=')
        sed -i.bak "s/changeme_strong_password/$PG_PASS/" .env && rm -f .env.bak
        ok "Generated random Postgres password"
    fi

    echo ""
    echo "  Please edit .env and set your API keys:"
    echo -e "  ${YELLOW}OPENAI_API_KEY${NC}   (required for OpenAI embeddings)"
    echo -e "  ${YELLOW}GITHUB_TOKEN${NC}     (optional, for private GitHub repos)"
    echo -e "  ${YELLOW}GITLAB_TOKEN${NC}     (optional, for GitLab repos)"
    echo ""
    read -p "  Press Enter to open .env in your editor (or Ctrl+C to skip)..."
    ${EDITOR:-nano} .env
fi

# ─── context-forge.yml ────────────────────────────────────
header "Repository configuration"

if [ -f context-forge.yml ]; then
    warn "context-forge.yml already exists, skipping creation"
else
    cp context-forge.yml.example context-forge.yml
    ok "Created context-forge.yml from example"
    echo ""
    warn "Edit context-forge.yml to add your repositories"
    echo "  See context-forge.yml.example for examples"
fi

# ─── docker-compose.override.yml ──────────────────────────
header "Volume mounts for local repos"

if [ -f docker-compose.override.yml ]; then
    warn "docker-compose.override.yml already exists"
else
    cp docker-compose.override.yml.example docker-compose.override.yml
    ok "Created docker-compose.override.yml"
    warn "Edit docker-compose.override.yml to mount your local repo paths"
fi

# ─── Start services ───────────────────────────────────────
header "Starting context-forge"

echo ""
read -p "  Start services now? [Y/n] " START_NOW
START_NOW=${START_NOW:-Y}

if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
    docker compose build --quiet
    docker compose up -d
    echo ""
    ok "Services started!"

    echo ""
    echo "  Waiting for services to be ready..."
    sleep 5

    if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
        ok "API is healthy"
    else
        warn "API not yet ready — may still be initializing"
    fi

    echo ""
    echo -e "  ${GREEN}context-forge is running!${NC}"
    echo ""
    echo "  ┌─ Endpoints ──────────────────────────────────┐"
    echo "  │  MCP server:  http://localhost:4000/mcp      │"
    echo "  │  REST API:    http://localhost:8000/api       │"
    echo "  │  Web UI:      http://localhost:3000           │"
    echo "  └──────────────────────────────────────────────┘"
else
    echo ""
    echo "  Run manually with: docker compose up -d"
fi

# ─── Agent connection ─────────────────────────────────────
echo ""
header "Connect your AI agent"
echo ""
echo "  Claude Code:"
echo -e "  ${YELLOW}claude mcp add --transport http context-forge http://localhost:4000/mcp${NC}"
echo ""
echo "  Cursor — add to .cursor/mcp.json:"
echo -e '  ${YELLOW}{"mcpServers": {"context-forge": {"url": "http://localhost:4000/mcp"}}}${NC}'
echo ""
echo "  Copy system prompt to your project:"
echo -e "  ${YELLOW}cp templates/CLAUDE.md /path/to/your/project/CLAUDE.md${NC}"
echo ""
echo "  Full docs: docs/claude-code.md | docs/cursor.md | docs/codex.md"
echo ""
