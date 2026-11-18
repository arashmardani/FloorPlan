FROM debian:bookworm-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /build
ARG LIBREDWG_VERSION=0.13.3
RUN curl -fsSL --retry 3 https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz -o src.tar.xz && tar -xf src.tar.xz --strip-components=1 && ./configure --disable-shared --enable-static && make -j2 && make install && /usr/local/bin/dwg2dxf --version
FROM python:3.12-slim-bookworm
COPY --from=builder /usr/local/bin/dwg2dxf /usr/local/bin/
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m cad && mkdir /data && chown cad:cad /data
COPY app.py .
COPY web ./web
ENV DATA_DIR=/data
USER cad
EXPOSE 8000
CMD ["gunicorn","-b","0.0.0.0:8000","-w","1","--timeout","90","app:app"]
