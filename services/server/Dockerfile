FROM python:3.11-slim

WORKDIR /app

# System deps: git for cloning repos, build tools for tree-sitter bindings
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

# Base install (always)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -e .

# Optional: install sentence-transformers only when EMBEDDINGS_PROVIDER=local
# (avoids pulling PyTorch ~2GB in the default OpenAI mode)
ARG EMBEDDINGS_PROVIDER=openai
RUN if [ "$EMBEDDINGS_PROVIDER" = "local" ]; then \
      pip install --no-cache-dir "sentence-transformers>=3.0.0"; \
    fi

COPY src/ ./src/

# Create data directory for repo cache
RUN mkdir -p /data/repos-cache

CMD ["python", "-m", "src.main"]
