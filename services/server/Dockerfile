FROM python:3.11-slim

WORKDIR /app

# System deps: git for cloning repos, build tools for tree-sitter bindings
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

RUN pip install --no-cache-dir -e .

COPY src/ ./src/

# Create data directory for repo cache
RUN mkdir -p /data/repos-cache

CMD ["python", "-m", "src.main"]
