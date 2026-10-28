# syntax=docker/dockerfile:1
# One image for both services: `mtt api` and `mtt runner` (deploy/compose.yaml).

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# aiomoqt gmarzot-0.14.0 development branch.
ARG AIOMOQT="aiomoqt @ git+https://github.com/gmarzot/aiomoqt@1b6ff16"
ARG AIOPQUIC="aiopquic==0.7.0rc2"
RUN pip wheel --no-cache-dir --wheel-dir /wheels . "${AIOMOQT}" "${AIOPQUIC}"

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
COPY media/sources.yaml /opt/mtt/media/sources.yaml
ENV MTT_ROOT=/opt/mtt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER mtt
WORKDIR /var/lib/mtt
ENTRYPOINT ["tini", "--", "mtt"]
