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
  - `gemini` source (Google Gemini API, best used with explicit `models`)
- Model selection filters:
  - `include_any` / `exclude_any` keyword filters
  - provider filters (`openrouter`, `huggingface`, `mock`, etc.)
  - global cap with `max_models`
- Dynamic problem loading from `benchmarks/**/*.json`
- Bundled starter problem sets for local smoke tests and HDLBits-style RTL exercises
- Structured problem-bank metadata for source/suite/track/difficulty-aware benchmark selection
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
  hdlbits/*/*.json
  industrial/*/*.json
  testbench/*.json
configs/pipeline.json
data/model_feeds/open_models.json
.github/workflows/benchmark.yml
Dockerfile
```

External benchmark source catalogs live under `data/problem_catalogs/`:

- `hdlbits_index.json`: link-only HDLBits index because the site license is not explicit
- `open_rtl_benchmarks.json`: open-source benchmark repos that are candidates for future import

## Configuration

Main config: `configs/pipeline.json`
Real-provider template: `configs/pipeline.realtime.json`

Key fields:

- `problem_glob`: benchmark case pattern
- `problem_filters`: optional metadata filters for dynamic problem-set assembly
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
- `GEMINI_API_KEY` for Google Gemini `/v1beta` APIs
- `OPENAI_COMPATIBLE_API_KEY` for OpenAI-compatible third-party gateways (optional)

Without these, the pipeline still works with local model feeds.

For Docker benchmarking of a fixed target set, prefer explicit `models` lists for `openai`, `anthropic`, `openrouter`, `gemini`, and `openai_compatible` sources. Those entries are treated as pinned benchmark targets:

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

For OpenAI, Anthropic, OpenRouter, Gemini, or OpenAI-compatible gateways, a static model list is usually more reliable than relying on provider `/models` timestamps. Example:

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

The same `models` field works for `anthropic`, `openrouter`, `gemini`, and `openai_compatible` sources.

## Google Gemini API

Native Gemini support uses the Google AI Studio Gemini API with `x-goog-api-key` authentication and the REST `generateContent` endpoint.

Example source block:

```json
{
  "type": "gemini",
  "enabled": true,
  "provider": "gemini",
  "base_url": "https://generativelanguage.googleapis.com/v1beta",
  "api_key_env": "GEMINI_API_KEY",
  "models": [
    {"id": "gemini-2.5-flash"}
  ]
}
```

If you omit `models`, the `gemini` source can also list available models from `/v1beta/models` and keeps only entries that support `generateContent`.

## List Problems

```bash
python -m rtl_benchmark.cli problems --config configs/pipeline.json
```

Problem rows now expose `source`, `suite`, `track`, and `difficulty` so you can verify the active benchmark mix before a run.

Example filter:

```json
{
  "problem_filters": {
    "sources": ["hdlbits"],
    "tracks": ["rtl_core"],
    "difficulties": ["easy", "medium"]
  }
}
```

Ready-to-use examples:

- `configs/pipeline.hdlbits.json`: teaching-oriented HDLBits sweep
- `configs/pipeline.industrial.json`: harder control/protocol subset
- `configs/pipeline.rtllm.json`: imported RTLLM subset once local conversion is done

External-source policy:

- mirror into `benchmarks/` only when the upstream license is explicit and compatible
- keep link-only indexes for public sites without clear redistribution terms
- review derived datasets separately if they incorporate HDLBits or other unclear sources

RTLLM local import:

```bash
PYTHONPATH=src python3 -m rtl_benchmark.cli import-rtllm \
  --src /path/to/RTLLM \
  --dest benchmarks/rtllm
PYTHONPATH=src python3 -m rtl_benchmark.cli problems --config configs/pipeline.rtllm.json
```

This importer expects the official RTLLM folder layout with `design_description.txt`, `testbench.v`, and either
`designer_RTL.v` or `verified_verilog.v` inside each design directory.

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

## Web Console

Run the lightweight local control panel:

```bash
python -m rtl_benchmark.cli serve --config configs/pipeline.realtime.json --host 127.0.0.1 --port 8787
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787).

The web console supports:

- editing local API keys and explicit model lists for OpenAI, Anthropic, Gemini, OpenRouter, Hugging Face, and OpenAI-compatible gateways
- choosing the full benchmark suite or a selected subset of built-in problems
- pasting a custom problem with structured fields
- viewing live run status, saved history, per-run result details, and the local leaderboard

Custom pasted problems can run in two modes:

- full evaluation when you provide the required verification fields such as `testbench` and `reference_rtl`
- generation-only when those fields are missing; the UI records the generated HDL and marks evaluation as skipped

The console stores its local settings in `.state/webui_config.json`.

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

## License

This repository is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).

## Contributing

Community contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

This project uses the Developer Certificate of Origin instead of a CLA. Every commit in a pull request should be signed off:

```bash
git commit -s -m "your message"
```

See [DCO.md](DCO.md) for the exact certification text.
