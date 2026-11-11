"""Serial live CLI evaluation. Captures artifacts; does NOT auto-grade factual quality.

Run: .venv/bin/python evaluation/run_questions.py
Requires local Ollama + network. This is separate from the default offline pytest run.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="+", type=int, help="Run only these case IDs")
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = json.loads((Path(__file__).parent / "questions.json").read_text())
    if args.ids:
        unknown = set(args.ids) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"Unknown case IDs: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.ids]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = (args.output or ROOT / ".research-agent" / "evaluations" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    for path in [ROOT / "agent.py", *sorted((ROOT / "research_agent").rglob("*.py"))]:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    results = {"started_at": datetime.now(timezone.utc).isoformat(),
               "model": args.model, "app_fingerprint": digest.hexdigest(),
               "grading": "Manual review required: process success is not answer correctness.",
               "cases": []}
    for case in cases:
        print(f"Running {case['id']}: {case['type']}", flush=True)
        started = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, "agent.py", case["question"], "--model", args.model],
                                  cwd=ROOT, capture_output=True, text=True, timeout=65)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode() if isinstance(stderr, bytes) else stderr
            code = 124
        entry = {**case, "wall_seconds": round(time.monotonic() - started, 3),
                 "exit_code": code, "stdout": stdout, "stderr": stderr,
                 "review_status": "not_reviewed"}
        match = re.search(r"^Trace: (.+)$", stderr, re.MULTILINE)
        if match:
            trace_path = Path(match[1])
            trace_path = trace_path if trace_path.is_absolute() else ROOT / trace_path
            if trace_path.is_file():
                saved_trace = output / f"case-{case['id']}-trace.jsonl"
                shutil.copyfile(trace_path, saved_trace)
                events = [json.loads(line) for line in saved_trace.read_text().splitlines()]
                entry["trace_file"] = saved_trace.name
                terminal = next((e for e in reversed(events) if e["event"] == "run_end"), {})
                entry["termination_reason"] = terminal.get("termination_reason", "missing")
                entry["counters"] = terminal.get("counters", {})
                entry["tools"] = [e["tool"] for e in events if e["event"] == "tool_end"]
        (output / f"case-{case['id']}-answer.txt").write_text(stdout, encoding="utf-8")
        results["cases"].append(entry)
        (output / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {entry.get('termination_reason', 'no trace')}; {entry['wall_seconds']}s; exit {code}", flush=True)
    print(f"Artifacts: {output}", flush=True)


if __name__ == "__main__":
    main()
