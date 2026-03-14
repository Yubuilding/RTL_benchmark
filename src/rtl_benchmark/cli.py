from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from rtl_benchmark.evaluator import Evaluator
from rtl_benchmark.importers import import_rtllm_repo
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.pipeline import BenchmarkPipeline
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.utils import ensure_dir, load_json, save_json, utc_run_id
from rtl_benchmark.webapp import serve_webapp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RTL LLM benchmark pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run full benchmark")
    run.add_argument("--config", default="configs/pipeline.json")
    run.add_argument("--include-known", action="store_true", help="benchmark known models too")

    discover = sub.add_parser("discover", help="discover models only")
    discover.add_argument("--config", default="configs/pipeline.json")
    discover.add_argument("--include-known", action="store_true")

    rank = sub.add_parser("rank", help="print leaderboard")
    rank.add_argument("--leaderboard", default="results/leaderboard.json")

    problems = sub.add_parser("problems", help="list benchmark problems")
    problems.add_argument("--config", default="configs/pipeline.json")

    doctor = sub.add_parser("doctor", help="check problems and execution backend readiness")
    doctor.add_argument("--config", default="configs/pipeline.json")

    build_image = sub.add_parser("build-image", help="build the Docker evaluator image from config")
    build_image.add_argument("--config", default="configs/pipeline.json")

    serve = sub.add_parser("serve", help="run the local web control panel")
    serve.add_argument("--config", default="configs/pipeline.realtime.json")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--ui-config", default="")

    grade = sub.add_parser("grade", help="evaluate pasted/local code for one problem")
    grade.add_argument("--config", default="configs/pipeline.json")
    grade.add_argument("--problem-id", required=True)
    grade.add_argument("--model-id", default="manual/paste")
    grade.add_argument("--code", default="")
    grade.add_argument("--code-file", default="")
    grade.add_argument("--stdin", action="store_true", help="read candidate code from stdin")
    grade.add_argument("--interactive", action="store_true", help="paste multiline code in the terminal")
    grade.add_argument(
        "--end-marker",
        default="EOF",
        help="line used to terminate interactive paste mode",
    )

    import_rtllm = sub.add_parser("import-rtllm", help="convert a local RTLLM repo snapshot into benchmark JSON files")
    import_rtllm.add_argument("--src", required=True, help="path to the local RTLLM repository snapshot")
    import_rtllm.add_argument("--dest", default="benchmarks/rtllm", help="output directory for converted JSON files")
    import_rtllm.add_argument("--overwrite", action="store_true", help="overwrite existing generated files")

    return parser


def cmd_run(config_path: str, include_known: bool) -> int:
    pipe = BenchmarkPipeline(config_path)
    out = pipe.run(include_known=include_known)

    print(f"run_id: {out['run_id']}")
    print(f"models tested: {len(out['models'])}")
    print(f"cases evaluated: {len(out['cases'])}")

    if out["summary"]:
        print("\nSummary:")
        for idx, row in enumerate(out["summary"], start=1):
            print(
                f"{idx:>2}. {row['model_id']} "
                f"score={row['score']:.2f} pass_rate={row['pass_rate']:.2f} "
                f"sim={fmt(row['sim_pass_rate'])} synth={fmt(row['synth_pass_rate'])}"
            )
    else:
        print("No models to evaluate after applying config selection and known-model state.")

    return 0


def cmd_discover(config_path: str, include_known: bool) -> int:
    cfg = load_json(config_path)
    models = discover_models(
        cfg.get("sources", []),
        cfg["state_path"],
        include_known=include_known,
        selection=cfg.get("selection", {}),
        update_state=False,
    )

    print(f"discovered models: {len(models)}")
    for m in models:
        print(f"- {m.id} [{m.provider}] cap={m.capability}")

    return 0


def cmd_problems(config_path: str) -> int:
    cfg = load_json(config_path)
    problems = load_problems(cfg["problem_glob"], cfg.get("problem_filters", {}))
    print(f"problems: {len(problems)}")
    for problem in problems:
        print(
            f"- {problem.id} [{problem.task_type}] "
            f"source={problem.source} suite={problem.suite} track={problem.track} "
            f"difficulty={problem.difficulty} top={problem.top_module} lang={problem.language}"
        )
    return 0


def cmd_doctor(config_path: str) -> int:
    cfg = load_json(config_path)
    problems = load_problems(cfg["problem_glob"], cfg.get("problem_filters", {}))
    evaluator = Evaluator(str(ensure_dir(cfg["run_root"]) / "doctor"), cfg.get("execution", {}))
    backend = evaluator.check_execution_backend()

    print(f"config: {config_path}")
    print(f"problem_count: {len(problems)}")
    print(f"execution_mode: {cfg.get('execution', {}).get('mode', 'local')}")
    print(f"backend_status: {backend.status}")
    print(f"backend_reason: {backend.reason}")
    return 0 if backend.status == "pass" else 1


def cmd_build_image(config_path: str) -> int:
    cfg = load_json(config_path)
    execution = cfg.get("execution", {})
    docker_binary = str(execution.get("docker_binary", "docker"))
    docker_image = str(execution.get("docker_image", "rtl-benchmark-tools:latest"))
    docker_build_context = str(execution.get("docker_build_context", "."))
    dockerfile = str(execution.get("dockerfile", "Dockerfile"))

    repo_root = Path(config_path).resolve().parent.parent
    context_dir = (repo_root / docker_build_context).resolve()
    dockerfile_path = (repo_root / dockerfile).resolve()

    cmd = [
        docker_binary,
        "build",
        "-t",
        docker_image,
        "-f",
        str(dockerfile_path),
        str(context_dir),
    ]
    try:
        proc = subprocess.run(cmd, text=True)
        return proc.returncode
    except FileNotFoundError:
        print(f"{docker_binary} not found")
        return 1


def cmd_serve(config_path: str, host: str, port: int, ui_config_path: str) -> int:
    serve_webapp(base_config_path=config_path, host=host, port=port, ui_config_path=ui_config_path)
    return 0


def cmd_rank(leaderboard_path: str) -> int:
    board = load_json(leaderboard_path, default={"models": []})
    rows = board.get("models", [])
    if not rows:
        print("Leaderboard is empty.")
        return 0

    print(f"leaderboard updated_at: {board.get('updated_at', 'n/a')}")
    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx:>2}. {row['model_id']} [{row.get('provider', 'unknown')}] "
            f"score={row.get('score', 0):.2f} pass_rate={row.get('pass_rate', 0):.2f} "
            f"runs={row.get('runs', 0)}"
        )
    return 0


def cmd_grade(
    config_path: str,
    problem_id: str,
    model_id: str,
    code: str,
    code_file: str,
    read_stdin: bool,
    interactive: bool,
    end_marker: str,
) -> int:
    cfg = load_json(config_path)
    problems = {p.id: p for p in load_problems(cfg["problem_glob"], cfg.get("problem_filters", {}))}
    problem = problems.get(problem_id)
    if problem is None:
        print(f"Unknown problem_id: {problem_id}")
        print("Available problem ids:")
        for pid in sorted(problems.keys()):
            print(f"- {pid}")
        return 2

    candidate = _resolve_candidate_code(
        code=code,
        code_file=code_file,
        read_stdin=read_stdin,
        interactive=interactive,
        end_marker=end_marker,
    )
    if not candidate.strip():
        print("No candidate code provided. Use --code, --code-file, --stdin, or --interactive.")
        return 2

    run_id = utc_run_id()
    run_root = ensure_dir(cfg["run_root"]) / f"manual_{run_id}"
    evaluator = Evaluator(str(run_root), cfg.get("execution", {}))
    result = evaluator.evaluate(model_id=model_id, problem=problem, candidate_code=candidate, attempt=1)

    print(f"manual_run_id: manual_{run_id}")
    print(f"problem_id: {problem_id}")
    print(f"model_id: {model_id}")
    print(f"passed: {result.passed}")
    print(
        f"stages: lint={result.lint.status}, sim={result.simulation.status}, "
        f"synth={result.synthesis.status}"
    )
    if result.mutation_kill_rate is not None:
        print(f"mutation_kill_rate: {result.mutation_kill_rate:.2f}")
    print(f"feedback: {result.feedback}")

    raw_dir = ensure_dir(cfg["raw_results_dir"])
    out_path = raw_dir / f"manual_{run_id}_{problem_id.replace('/', '__')}.json"
    save_json(out_path, {"run_id": f"manual_{run_id}", "case": asdict(result)})
    print(f"raw_result: {out_path}")
    return 0


def cmd_import_rtllm(src_root: str, dest_root: str, overwrite: bool) -> int:
    outputs = import_rtllm_repo(src_root=src_root, dest_root=dest_root, overwrite=overwrite)
    print(f"imported problems: {len(outputs)}")
    for path in outputs:
        print(f"- {path}")
    return 0


def _resolve_candidate_code(
    code: str,
    code_file: str,
    read_stdin: bool,
    interactive: bool,
    end_marker: str,
) -> str:
    if code:
        return code
    if code_file:
        with open(code_file, "r", encoding="utf-8") as f:
            return f.read()
    if interactive:
        return _read_interactive_code(end_marker)
    if read_stdin:
        return sys.stdin.read()
    if sys.stdin.isatty():
        return _read_interactive_code(end_marker)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _read_interactive_code(end_marker: str) -> str:
    marker = end_marker or "EOF"
    print(f"Paste code below. End input with a line containing only {marker}.")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == marker:
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "run":
        raise SystemExit(cmd_run(args.config, args.include_known))
    if args.cmd == "discover":
        raise SystemExit(cmd_discover(args.config, args.include_known))
    if args.cmd == "rank":
        raise SystemExit(cmd_rank(args.leaderboard))
    if args.cmd == "problems":
        raise SystemExit(cmd_problems(args.config))
    if args.cmd == "doctor":
        raise SystemExit(cmd_doctor(args.config))
    if args.cmd == "build-image":
        raise SystemExit(cmd_build_image(args.config))
    if args.cmd == "serve":
        raise SystemExit(cmd_serve(args.config, args.host, args.port, args.ui_config))
    if args.cmd == "grade":
        raise SystemExit(
            cmd_grade(
                config_path=args.config,
                problem_id=args.problem_id,
                model_id=args.model_id,
                code=args.code,
                code_file=args.code_file,
                read_stdin=args.stdin,
                interactive=args.interactive,
                end_marker=args.end_marker,
            )
        )
    if args.cmd == "import-rtllm":
        raise SystemExit(cmd_import_rtllm(args.src, args.dest, args.overwrite))

    raise SystemExit(2)


if __name__ == "__main__":
    main()
