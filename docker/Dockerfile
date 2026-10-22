# syntax=docker/dockerfile:1
# One image for both services: `mtt api` and `mtt runner` (deploy/compose.yaml).

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# aiomoqt pinned by commit until 0.13.1 is on PyPI.
ARG AIOMOQT="aiomoqt @ git+https://github.com/gmarzot/aiomoqt@f6a2763f3a0e91739bca7321994ccc40f9efde29"
RUN pip wheel --no-cache-dir --wheel-dir /wheels . "${AIOMOQT}"

FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /var/lib/mtt --shell /usr/sbin/nologin mtt \
    && mkdir -p /var/lib/mtt && chown mtt:mtt /var/lib/mtt
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels
COPY config /opt/mtt/config
COPY adapters /opt/mtt/adapters
ENV MTT_ROOT=/opt/mtt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER mtt
WORKDIR /var/lib/mtt
ENTRYPOINT ["tini", "--", "mtt"]
