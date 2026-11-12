FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.11.15 AS uv
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex@0.167.1 @anthropic-ai/claude-code@2.1.153 \
    && npm cache clean --force
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" HOME=/home/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown -R app:app /data /home/app
COPY app ./app
COPY ui ./ui
COPY scripts ./scripts
COPY examples ./examples
COPY skills ./skills
COPY .streamlit ./.streamlit
USER app
EXPOSE 8000 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
