# 빌드 컨텍스트는 레포 루트다.
FROM ghcr.io/astral-sh/uv:0.6.14-python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY apps/api/src ./src
RUN uv sync --locked --no-dev

# 빈 host는 IPv4와 IPv6를 모두 받는다. Railway 내부 네트워크는 IPv6를 쓴다("::"는 IPv6만 받음).
CMD ["/bin/sh", "-c", "exec .venv/bin/uvicorn api.main:app --host '' --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
