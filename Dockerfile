FROM python:3.12-slim

LABEL maintainer="wujiaming88"
LABEL description="OpenClaw Diagnostic Dashboard"

WORKDIR /app

# Copy application files
COPY openclaw-dashboard.py .
COPY static/ static/

# Default environment variables
ENV OC_DIAG_PORT=9090
ENV OC_DIAG_HOST=0.0.0.0
ENV OC_DIAG_API_KEY=""

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://localhost:'+os.environ.get('OC_DIAG_PORT','9090')+'/api/mode')" || exit 1

ENTRYPOINT ["/bin/sh", "-c", \
    "exec python3 openclaw-dashboard.py --no-browser \
     --port ${OC_DIAG_PORT:-9090} \
     --host ${OC_DIAG_HOST:-0.0.0.0} \
     ${OC_DIAG_API_KEY:+--api-key $OC_DIAG_API_KEY}"]
