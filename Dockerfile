FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc=4:14.2.0-1 \
        python3-dev=3.13.5-1 \
        libffi-dev=3.4.8-2 \
        libgmp-dev=2:6.3.0+dfsg-3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py genesis.json ./
COPY minichain/ ./minichain/

RUN useradd --create-home --uid 1000 minichain \
    && chown -R minichain:minichain /app
USER minichain

EXPOSE 9000 8545

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8545), timeout=3)"

ENTRYPOINT ["python", "main.py"]
CMD ["--host", "0.0.0.0", "--rpc-host", "0.0.0.0", "--port", "9000"]
