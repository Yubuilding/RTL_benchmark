FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iverilog verilator yosys \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "rtl_benchmark.cli"]
