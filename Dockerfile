FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# isync (mbsync) performs the actual IMAP sync this service orchestrates;
# ca-certificates is needed for mbsync's own TLS verification of IMAP servers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    isync ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies before copying source for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mail_archive_server/ ./mail_archive_server/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 4103

CMD ["mail-archive-server"]
