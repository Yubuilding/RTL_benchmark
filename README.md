# RTL LLM Benchmark Pipeline

This project scaffolds a practical RTL benchmark system for evaluating large language models on:

- RTL generation correctness (Verilog/SystemVerilog)
- Testbench generation quality (mutation-based scoring)
- Toolchain-level robustness (lint, simulation, synthesis)
- Continuous leaderboard updates for configured or newly discovered models

## What is implemented in this initial version

- Model discovery framework:
  - `file_feed` source (working now)
  - `huggingface` source (optional, API-based discovery + inference)
  - `openrouter` source (best used with explicit `models`)
  - `openai` source (best used with explicit `models`)
  - `anthropic` source (best used with explicit `models`)
- Model selection filters:
  - `include_any` / `exclude_any` keyword filters
  - provider filters (`openrouter`, `huggingface`, `mock`, etc.)
  - global cap with `max_models`
- Dynamic problem loading from `benchmarks/**/*.json`
- Agent-style iterative repair loop (up to `max_iterations`)
- Manual grading mode (`grade`) for pasted code / local files
- Problem listing mode (`problems`) for quick lookup of benchmark ids
- Sandbox-style evaluator stages:
  - Verilator lint (`--lint-only`)
  - Icarus Verilog compile/sim (`iverilog` + `vvp`)
  - Yosys synthesis check
- Testbench benchmark mode with mutant kill-rate scoring
- Run artifact persistence:
  - per-case logs in `results/runs/...`
  - raw run data in `results/raw/*.json`
  - rolling leaderboard in `results/leaderboard.json`
- GitHub Actions workflow for scheduled and manual runs
- Dockerfile for reproducible toolchain container

## Quick start

```bash
cd /Users/gary/RTL_benchmark
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m rtl_benchmark.cli run --config configs/pipeline.json
python -m rtl_benchmark.cli rank
```

If EDA tools are missing locally, checks are marked `skipped` and still logged.

## Project structure

```text
src/rtl_benchmark/
  cli.py
  pipeline.py
  problem_bank.py
  model_sources.py
  model_runner.py
  evaluator.py
  leaderboard.py
  types.py
  utils.py
benchmarks/
  rtl/*.json
  testbench/*.json
configs/pipeline.json
data/model_feeds/open_models.json
.github/workflows/benchmark.yml
Dockerfile
```

## Configuration

Main config: `configs/pipeline.json`
Real-provider template: `configs/pipeline.realtime.json`

Key fields:

- `problem_glob`: benchmark case pattern
- `sources`: model discovery sources
- `selection`: filtering policy for discovered models
- `generation`: inference params (`temperature`, `max_tokens`, timeout)
- `execution`: local vs Docker evaluator backend
- `max_iterations`: retry loop for model self-repair
- `run_root`, `raw_results_dir`, `leaderboard_path`: output paths

Docker mode keys:

- `mode`: `local` or `docker`
- `docker_image`: local image tag used for lint/sim/synth
- `docker_binary`: Docker CLI path, usually `docker`
- `docker_build_context`: build context for `build-image`
- `dockerfile`: Dockerfile path for `build-image`
- `container_workdir`: mount point used inside the container
- `timeout_seconds`: per-stage timeout
- `docker_network`: defaults to `none`
- `docker_read_only_rootfs`: enables `--read-only`
- `docker_tmpfs_mounts`: writable tmpfs paths inside the container
- `docker_security_opts`: extra `--security-opt` flags
- `docker_cap_drop`: Linux capabilities to drop
- `docker_pids_limit`, `docker_memory`, `docker_cpus`: resource limits

## Optional online model discovery

Set environment variables if you want live API discovery:

- `HF_TOKEN` for Hugging Face discovery + inference API
- `OPENROUTER_API_KEY` for OpenRouter discovery + chat completion API
- `OPENAI_API_KEY` for OpenAI `/v1` APIs
- `ANTHROPIC_API_KEY` for Anthropic `/v1` APIs
- `OPENAI_COMPATIBLE_API_KEY` for OpenAI-compatible third-party gateways (optional)

Without these, the pipeline still works with local model feeds.

For Docker benchmarking of a fixed target set, prefer explicit `models` lists for `openai`, `anthropic`, `openrouter`, and `openai_compatible` sources. Those entries are treated as pinned benchmark targets:

- they run on every `run`, even if they were already seen in `.state/known_models.json`
- `models: []` disables that provider cleanly instead of falling back to provider-side discovery
- `selection.max_models` and keyword filters are usually unnecessary when the config already names exact model ids

## Real-provider run example

1. Edit the explicit `models` lists in `/Users/gary/RTL_benchmark/configs/pipeline.realtime.json` to the exact model ids you want to benchmark.
2. Set the corresponding API key(s) in your environment.
3. Run:

```bash
docker build -t rtl-benchmark-tools:latest .
python -m rtl_benchmark.cli discover --config configs/pipeline.realtime.json
python -m rtl_benchmark.cli run --config configs/pipeline.realtime.json
```

`discover` is read-only now. It prints the pinned model set without consuming state, and `run` will still benchmark the same explicit models on later invocations.

The default `configs/pipeline.json` uses local execution. `configs/pipeline.realtime.json` uses Docker execution.

## OpenAI-Compatible API

In `configs/pipeline.realtime.json`, use a source block like:

```json
{
  "type": "openai",
  "enabled": true,
  "provider": "openai_compatible",
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
  "models": [
    {"id": "your-openai-compatible-model"}
  ]
}
```

For OpenAI, Anthropic, OpenRouter, or OpenAI-compatible gateways, a static model list is usually more reliable than relying on provider `/models` timestamps. Example:

```json
{
  "type": "openai",
  "enabled": true,
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "models": [
    {"id": "gpt-4.1"},
    {"id": "o3-mini"}
  ]
}
```

The same `models` field works for `anthropic`, `openrouter`, and `openai_compatible` sources.

## List Problems

```bash
python -m rtl_benchmark.cli problems --config configs/pipeline.json
```

## Check Environment

```bash
python -m rtl_benchmark.cli doctor --config configs/pipeline.realtime.json
```

This checks:

- benchmark problems can be loaded
- execution mode is configured
- Docker CLI is available when Docker mode is enabled
- the evaluator image exists locally

Build the evaluator image from config:

```bash
python -m rtl_benchmark.cli build-image --config configs/pipeline.realtime.json
```

## Paste Code And Grade

Grade from a file:

```bash
python -m rtl_benchmark.cli grade \
  --config configs/pipeline.json \
  --problem-id rtl_add8 \
  --model-id manual/my_try \
  --code-file /path/to/my_answer.sv
```

Grade by direct paste (stdin):

```bash
python -m rtl_benchmark.cli grade \
  --config configs/pipeline.json \
  --problem-id rtl_add8 \
  --model-id manual/paste \
  --stdin <<'EOF'
module add8(input [7:0] a, input [7:0] b, output [8:0] sum);
  assign sum = a + b;
endmodule
EOF
```

Grade in interactive terminal paste mode:

```bash
python -m rtl_benchmark.cli grade \
  --config configs/pipeline.json \
  --problem-id rtl_add8 \
  --model-id manual/interactive \
  --interactive
```

Then paste HDL and finish with a line containing only `EOF`.

If you run `grade` from a terminal without `--code`, `--code-file`, or `--stdin`, it now enters interactive paste mode automatically.

## Notes

- This is a practical baseline implementation to start the project quickly.
- Next phase can add:
  - real HDLBits/ChipDev ingestion
  - richer prompt templates and agent orchestration
  - PPA extraction and normalized scoring
  - web leaderboard (Streamlit/Gradio/GitHub Pages)
