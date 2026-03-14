# Benchmark Problem Bank

This repository now treats the benchmark set as a structured problem bank rather than a flat folder of JSON files.

## Schema goals

- Keep educational and industrial-style tasks in the same loader.
- Preserve benchmark provenance so public tasks can be separated from curated/private tasks.
- Make problem selection dynamic through config filters instead of hard-coded globs.
- Keep each problem self-contained so historical runs can store a full snapshot.

## Recommended fields

Required:

- `id`
- `task_type`
- `language`
- `prompt`
- `top_module`

Recommended:

- `suite`: logical bundle such as `starter`, `hdlbits`, `chipdev`
- `track`: capability bucket such as `rtl_core`, `protocol`, `verification`
- `difficulty`: `easy`, `medium`, `hard`, or a project-specific label
- `tags`: fine-grained searchable labels
- `exposure`: `public`, `curated`, or `private`

Inferred when omitted:

- `prompt_style`: `spec_to_rtl` or `spec_to_testbench`
- `harness_type`: `testbench_compare` or `mutation`
- `evaluation_targets`: RTL defaults to `syntax/functionality/synthesis`; testbench defaults to `syntax/functionality/mutation`

## Directory strategy

- `benchmarks/rtl`: small local smoke-test suite
- `benchmarks/testbench`: mutation-scored verification tasks
- `benchmarks/hdlbits/<category>`: public HDLBits-style practice tasks
- `benchmarks/industrial/<category>`: curated higher-difficulty control/protocol tasks
- `benchmarks/rtllm/<category>`: imported open-source RTLLM tasks converted into local JSON

The loader still infers `source` and `category` from the path, so `benchmarks/hdlbits/sequential/foo.json` becomes `source=hdlbits`, `category=sequential`.

For external sources that are not mirrored locally yet, see `data/problem_catalogs/`.

## Config-driven selection

Use `problem_filters` in config files to assemble runs without changing the filesystem layout.

Supported filters:

- `ids`
- `task_types`
- `sources`
- `suites`
- `tracks`
- `categories`
- `difficulties`
- `exposure`
- `tags_any`
- `tags_all`

Example:

```json
{
  "problem_glob": "benchmarks/**/*.json",
  "problem_filters": {
    "sources": ["hdlbits"],
    "tracks": ["rtl_core"],
    "difficulties": ["easy", "medium"]
  }
}
```
