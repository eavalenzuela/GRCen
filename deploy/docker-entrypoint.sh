#!/bin/sh
set -e

BIND="0.0.0.0:8000"
SSL_ARGS=""

if [ -n "$SSL_CERTFILE" ] && [ -n "$SSL_KEYFILE" ]; then
    BIND="0.0.0.0:8443"
    SSL_ARGS="--certfile $SSL_CERTFILE --keyfile $SSL_KEYFILE"
fi

exec gunicorn grcen.main:app \
    -k uvicorn.workers.UvicornWorker \
    -b "$BIND" \
    --workers "${GUNICORN_WORKERS:-2}" \
    $SSL_ARGS
