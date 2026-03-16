# ─────────────────────────────────────────────────────────
# context-forge setup wizard (Windows PowerShell)
# ─────────────────────────────────────────────────────────
param()

function Write-Header($msg) { Write-Host "`n▶ $msg" -ForegroundColor Blue }
function Write-Ok($msg)     { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg)   { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Write-Err($msg)    { Write-Host "  ✗ $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║       context-forge setup            ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan

# ─── Prerequisites ────────────────────────────────────────
Write-Header "Checking prerequisites"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "Docker not found. Install Docker Desktop: https://docs.docker.com/desktop/windows/"
    exit 1
}
Write-Ok "Docker found"

try {
    docker compose version | Out-Null
    Write-Ok "Docker Compose v2 found"
} catch {
    Write-Err "Docker Compose v2 not found. Update Docker Desktop."
    exit 1
}

# ─── .env ─────────────────────────────────────────────────
Write-Header "Environment configuration"

if (Test-Path ".env") {
    Write-Warn ".env already exists, skipping creation"
} else {
    Copy-Item ".env.example" ".env"
    Write-Ok "Created .env from .env.example"

    # Generate random Postgres password
    $pgPass = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes([System.Guid]::NewGuid().ToString("N"))).Substring(0, 24)
    (Get-Content ".env") -replace "changeme_strong_password", $pgPass | Set-Content ".env"
    Write-Ok "Generated random Postgres password"

    Write-Host ""
    Write-Host "  Please edit .env and set:" -ForegroundColor Yellow
    Write-Host "    OPENAI_API_KEY   (required for OpenAI embeddings)" -ForegroundColor Yellow
    Write-Host "    GITHUB_TOKEN     (optional, for private GitHub repos)" -ForegroundColor Yellow
    Write-Host "    GITLAB_TOKEN     (optional, for GitLab repos)" -ForegroundColor Yellow
    Write-Host ""
    $open = Read-Host "  Open .env in Notepad? [Y/n]"
    if ($open -eq "" -or $open -match "^[Yy]") {
        notepad .env
        Write-Host "  Press Enter when done editing..." -ForegroundColor Cyan
        Read-Host
    }
}

# ─── context-forge.yml ────────────────────────────────────
Write-Header "Repository configuration"

if (Test-Path "context-forge.yml") {
    Write-Warn "context-forge.yml already exists, skipping"
} else {
    Copy-Item "context-forge.yml.example" "context-forge.yml"
    Write-Ok "Created context-forge.yml from example"
    Write-Warn "Edit context-forge.yml to add your repositories"
}

# ─── docker-compose.override.yml ──────────────────────────
Write-Header "Volume mounts for local repos"

if (Test-Path "docker-compose.override.yml") {
    Write-Warn "docker-compose.override.yml already exists"
} else {
    Copy-Item "docker-compose.override.yml.example" "docker-compose.override.yml"
    Write-Ok "Created docker-compose.override.yml"
    Write-Warn "Edit docker-compose.override.yml to mount your local repo paths (use forward slashes: C:/Users/...)"
}

# ─── Start services ───────────────────────────────────────
Write-Header "Starting context-forge"

Write-Host ""
$startNow = Read-Host "  Start services now? [Y/n]"
if ($startNow -eq "" -or $startNow -match "^[Yy]") {
    docker compose build --quiet
    docker compose up -d

    Write-Host ""
    Write-Ok "Services started!"
    Write-Host ""
    Write-Host "  Waiting for services to be ready..." -ForegroundColor Gray
    Start-Sleep -Seconds 8

    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 5
        Write-Ok "API is healthy"
    } catch {
        Write-Warn "API not yet ready — may still be initializing (check: docker compose logs context-forge)"
    }

    Write-Host ""
    Write-Host "  context-forge is running!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ┌─ Endpoints ──────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "  │  MCP server:  http://localhost:4000/mcp      │" -ForegroundColor Cyan
    Write-Host "  │  REST API:    http://localhost:8000/api       │" -ForegroundColor Cyan
    Write-Host "  │  Web UI:      http://localhost:3000           │" -ForegroundColor Cyan
    Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "  Run manually with: docker compose up -d"
}

# ─── Agent connection ─────────────────────────────────────
Write-Host ""
Write-Header "Connect your AI agent"
Write-Host ""
Write-Host "  Claude Code:" -ForegroundColor White
Write-Host "  claude mcp add --transport http context-forge http://localhost:4000/mcp" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Cursor — add to .cursor\mcp.json:" -ForegroundColor White
Write-Host '  {"mcpServers": {"context-forge": {"url": "http://localhost:4000/mcp"}}}' -ForegroundColor Yellow
Write-Host ""
Write-Host "  Copy system prompt to your project:" -ForegroundColor White
Write-Host "  Copy-Item templates\CLAUDE.md C:\path\to\your\project\CLAUDE.md" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Full docs: docs\claude-code.md | docs\cursor.md | docs\codex.md" -ForegroundColor Gray
Write-Host ""
