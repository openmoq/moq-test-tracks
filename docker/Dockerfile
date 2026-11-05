# syntax=docker/dockerfile:1
# One image for both services: `mtt api` and `mtt runner` (deploy/compose.yaml).

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY docker/wheels ./prebuilt
# aiomoqt pinned by commit until 0.15.0 is on PyPI.
ARG AIOMOQT="aiomoqt @ git+https://github.com/gmarzot/aiomoqt@22fb07187fa8bcf5571d41936782bd3df0261bb3"
# aiopquic 5711a08 wheel from gmarzot/aiopquic CI run 42113838269, until 0.8.0 is on PyPI.
ARG AIOPQUIC="./prebuilt/aiopquic-0.8.0.dev28+g5711a08-cp312-cp312-manylinux_2_34_x86_64.whl"
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
