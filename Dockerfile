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
ENV OC_DIAG_ADVANCED=""

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${OC_DIAG_PORT:-9090}/api/mode')" || exit 1

ENTRYPOINT ["python3", "openclaw-dashboard.py", "--no-browser"]
CMD []
