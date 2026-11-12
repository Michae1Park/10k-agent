# Web app image: the API (era serve) plus the built UI on :8000.
# The model server (vLLM) runs outside the container; see docs/SERVING.md.

FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra web --extra local --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --extra web --extra local
COPY config.yaml companies.yaml research.yaml ./
COPY experiments/v1/config.yaml experiments/v1/config.yaml
COPY --from=ui /ui/dist ui/dist
EXPOSE 8000
CMD ["era", "serve", "--host", "0.0.0.0"]
