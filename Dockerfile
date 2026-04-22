FROM node:22-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    git \
    curl \
    jq \
    ripgrep \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js tools (Codex + repomix)
RUN npm install -g @openai/codex repomix

# Set up Python virtualenv
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Python dependencies
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir /app

# Copy application code
COPY src/ /app/src/
COPY commands/ /app/commands/
COPY templates/ /app/templates/

# Copy AGENTS.md if it exists (best-effort)
COPY AGENTS.md /app/AGENTS.md 2>/dev/null || true

# Copy entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Environment
ENV PYTHONPATH=/app/src
ENV APP_DIR=/app

WORKDIR /workspace
ENTRYPOINT ["/app/entrypoint.sh"]
