# Tencent's XP2P static libraries are GCC 9 "slim LTO" objects: they can only be
# linked by GCC 9.x, which is what Ubuntu 20.04 ships. Build stage stays on 20.04.
FROM ubuntu:20.04 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ make libc6-dev curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY bridge/ ./
# fetch_sdk.sh downloads Tencent's official SDK and verifies its sha256.
RUN sh fetch_sdk.sh vendor && make && strip xp2p_bridge

ARG MEDIAMTX_VERSION=v1.21.2
ARG MEDIAMTX_SHA256=121be6e00397e473a95d83c75f4fcd3819a6af3b0e1a50889cba43ab4fab6a27
RUN curl -fsSL -o /tmp/mediamtx.tgz \
        "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz" \
    && echo "${MEDIAMTX_SHA256}  /tmp/mediamtx.tgz" | sha256sum -c - \
    && tar -xzf /tmp/mediamtx.tgz -C /build mediamtx

FROM ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/xp2p_bridge /build/mediamtx /usr/local/bin/
WORKDIR /app
COPY server.py run.sh ./
RUN chmod +x run.sh
CMD ["/app/run.sh"]

LABEL io.hass.type="addon" io.hass.arch="amd64"
