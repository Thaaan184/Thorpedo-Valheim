FROM ghcr.io/reloadlife/valheim-server:latest

USER root

# Install Python 3, venv, and process inspection tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    procps \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for the management panel
RUN python3 -m venv /opt/panel_venv && \
    /opt/panel_venv/bin/pip install --no-cache-dir \
    fastapi \
    uvicorn \
    jinja2 \
    psutil \
    requests \
    python-dotenv \
    python-multipart

# Copy panel application
COPY panel /panel
RUN chmod -R 755 /panel

# Copy custom all-in-one entrypoint
COPY entrypoint_allinone.sh /usr/local/bin/entrypoint_allinone.sh
RUN chmod +x /usr/local/bin/entrypoint_allinone.sh

# Expose ports
EXPOSE 28456
EXPOSE 2456/udp
EXPOSE 2457/udp

# Entrypoint using tini
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint_allinone.sh"]
