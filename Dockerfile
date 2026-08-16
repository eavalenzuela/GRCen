FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libxmlsec1-dev libxmlsec1-openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
# Upgrade pip before installing: the base image ships 25.0.1, which carries six
# advisories (PYSEC-2026-196, -1795, -1796, -2875, -2876). They are build-time
# issues, but the final stage copies this site-packages, so the old pip would
# also ship in the runtime image and show up in any scan of it.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libxmlsec1 libxmlsec1-openssl \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 \
    libglib2.0-0 libgdk-pixbuf-2.0-0 libffi8 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ src/

# Drop pip from the runtime image. Nothing here installs packages -- the deps
# are already baked in -- and the COPY above overlays the builder's
# site-packages onto the base image's, which leaves the base image's stale
# pip-<old>.dist-info sitting next to the builder's. The code that runs is the
# newer pip, but scanners read the stale metadata and report the old version's
# advisories. Removing pip entirely is both smaller and permanently accurate.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.12/site-packages/pkg_resources \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

EXPOSE 8000 8443

COPY deploy/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
