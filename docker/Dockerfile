# syntax=docker/dockerfile:1
# One image for both services: `mtt api` and `mtt runner` (deploy/compose.yaml).

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# aiomoqt pinned by commit until 0.15.0 is on PyPI.
ARG AIOMOQT="aiomoqt @ git+https://github.com/gmarzot/aiomoqt@7dee6b1b648402dd8930bca286089834382a7b33"
ARG AIOPQUIC="aiopquic==0.7.0"
RUN pip wheel --no-cache-dir --wheel-dir /wheels . "${AIOMOQT}" "${AIOPQUIC}"

# moq2ts-cli: the release AppImage, extracted, with the libraries it expects from its
# build platform (Ubuntu 24.04, glibc 2.39) beside it, so it runs on bookworm.
FROM ubuntu:24.04 AS moq2ts
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libasound2t64 libegl1 libfontconfig1 libfreetype6 libfribidi0 libgl1 libglx0 \
       libharfbuzz0b libice6 libjack-jackd2-0 libopengl0 libsm6 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:7e48a49f5c256318ab7c3330705c05ea57cf1152769b6df55248f0da7e3884c4 \
    https://github.com/openmoq/moq2ts/releases/download/v1.3.0/moq2ts-cli-linux-x86_64.AppImage \
    /tmp/moq2ts.AppImage
WORKDIR /opt
RUN chmod +x /tmp/moq2ts.AppImage \
    && /tmp/moq2ts.AppImage --appimage-extract >/dev/null \
    && mv squashfs-root moq2ts && rm -rf moq2ts/usr/plugins && mkdir moq2ts/sys \
    && LD_LIBRARY_PATH=/opt/moq2ts/usr/lib ldd moq2ts/usr/bin/moq2ts-cli > /tmp/ldd \
    && ! grep "not found" /tmp/ldd \
    && awk '$3 ~ /^\// && $3 !~ /^\/opt\// {print $3} $1 ~ /ld-linux/ {print $1}' /tmp/ldd \
       | sort -u | xargs -I{} cp -L {} moq2ts/sys/ \
    && chmod -R a+rX moq2ts

FROM python:3.12-slim-bookworm
ADD --checksum=sha256:551d93bae82fed0ac37e91d8f47553428731ce2b7bc16d9299109b5b8e36904b \
    https://github.com/tsduck/tsduck/releases/download/v3.39-3956/tsduck_3.39-3956.debian12_amd64.deb \
    /tmp/tsduck.deb
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tini /tmp/tsduck.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/tsduck.deb \
    && useradd --system --uid 10001 --home-dir /var/lib/mtt --shell /usr/sbin/nologin mtt \
    && mkdir -p /var/lib/mtt && chown mtt:mtt /var/lib/mtt
COPY --from=moq2ts /opt/moq2ts /opt/moq2ts
COPY docker/moq2ts-cli /usr/local/bin/moq2ts-cli
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels
COPY config /opt/mtt/config
COPY adapters /opt/mtt/adapters
COPY media/sources.yaml /opt/mtt/media/sources.yaml
ENV MTT_ROOT=/opt/mtt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TSDUCK_NO_VERSION_CHECK=1
USER mtt
WORKDIR /var/lib/mtt
ENTRYPOINT ["tini", "--", "mtt"]
