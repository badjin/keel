from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from pathlib import Path

from . import check, close, critic, docs, fsutil, ladder, runner, work


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keel_wf.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    new = commands.add_parser("new")
    new.add_argument("slug")

    intent = commands.add_parser("intent")
    intent_commands = intent.add_subparsers(dest="intent_command", required=True)
    submit = intent_commands.add_parser("submit")
    submit.add_argument("--work", required=True)

    review = commands.add_parser("critic")
    review.add_argument("stage", choices=("intent", "spec", "plan", "phase", "light"))
    review.add_argument("phase", nargs="?", type=int)
    review.add_argument("--work")
    review.add_argument("--repo")
    review.add_argument("--request", default="")
    review.add_argument("--base", default="")
    review.add_argument("--statements")
    review.add_argument("--extra-review", action="store_true")

    run = commands.add_parser("run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_start = run_commands.add_parser("start")
    run_start.add_argument("--work", required=True)
    run_start.add_argument("--repo", required=True)
    run_phase = run_commands.add_parser("phase")
    run_phase.add_argument("phase", type=int)
    run_phase.add_argument("--work", required=True)
    run_phase.add_argument("--extra-review", action="store_true")
    run_status = run_commands.add_parser("status")
    run_status.add_argument("--work", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--work", required=True)
    choice = verify.add_mutually_exclusive_group(required=True)
    choice.add_argument("--results")
    choice.add_argument("--exempt", action="store_true")
    for name in ("audit", "archive"):
        commands.add_parser(name).add_argument("--work", required=True)
    return parser


def _origin() -> str:
    cwd = Path.cwd().resolve()
    result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True)
    return str(Path(result.stdout.strip()).resolve()) if result.returncode == 0 else str(cwd)


def _print_next_step(keel_home: Path, folder: Path, n: int, verdict) -> None:
    if verdict == "ERROR":
        print(f"review errored — rerun run phase {n}; no fix needed")
        return
    if verdict != "FAIL":
        return
    config = ladder.load(keel_home)
    fails = work.load_state(folder).get("review_fails", {}).get(f"phase-{n}", 0)
    step = ladder.next_step(fails, config)
    if step is None:
        print("no further fix step — stop and ask the user")
        return
    index = min(fails, len(config.steps))
    codex_model = step.codex["model"] or "current model"
    print(f"next fix: step {index} of {len(config.steps)} — claude: "
          f"{step.claude['model']}/{step.claude['effort']} (agent keel-hotfix-{index}); "
          f"codex: {codex_model}/{step.codex['effort']}")


def main(argv=None, keel_home: Path = Path(__file__).resolve().parent.parent) -> int:
    args = _parser().parse_args(argv)
    keel_home = Path(keel_home).resolve()
    if args.command == "check":
        rows, exit_code = check.run_check(keel_home)
        columns = ("id", "title", "where", "status")
        widths = {key: max(len(key), *(len(str(row[key])) for row in rows)) for key in columns}
        print("  ".join(key.ljust(widths[key]) for key in columns))
        for row in rows:
            print("  ".join(str(row[key]).ljust(widths[key]) for key in columns))
        print(docs.text("check_footer", "en"))
        return exit_code
    config = fsutil.read_json(keel_home / "config.json", {})
    if not config.get("wiki_path"):
        print("wiki_path missing from config")
        return 2
    kb = Path(config["wiki_path"]).resolve()
    lang = config.get("workflow", {}).get("language", "en")

    try:
        if args.command == "new":
            folder = work.new_work(kb, keel_home, args.slug, lang, _origin(), date.today().isoformat())
            print(folder.resolve())
            print((folder / "intent.md").resolve())
            return 0

        if args.command == "intent":
            folder = work.resolve(kb, args.work)
            path = folder / "intent.md"
            original = path.read_text(encoding="utf-8")
            problems = docs.check_intent(original, docs.text("none_tokens", lang))
            if problems:
                for problem in problems:
                    print(problem)
                return 1
            review = work.load_state(folder).get("reviews", {}).get("intent", {})
            if (review.get("verdict") != "PASS" or
                    review.get("hashes", {}).get("intent") != docs.intent_hash(original)):
                print(f"run: keel_wf.py critic intent --work {args.work}")
                return 1
            updated = docs.set_status(original, "awaiting-approval")
            updated = docs.add_changelog(updated, f"{fsutil.now_iso()[:10]} submitted for approval")
            fsutil.atomic_write_text(path, updated)
            print(docs.text("submit_instruction", lang, path=str(path.resolve()),
                            slug=work.load_state(folder)["slug"]))
            return 0

        if args.command == "run":
            folder = work.resolve(kb, args.work)
            if args.run_command == "start":
                result = runner.start(keel_home, kb, folder, Path(args.repo))
                print(result["branch"])
                return 0
            if args.run_command == "status":
                print(runner.status(keel_home, kb, folder))
                return 0
            result = runner.run_phase(keel_home, kb, folder, args.phase,
                                      extra_review=args.extra_review)
            print(result["status"])
            print(result["tests_record"])
            if result["status"] == "review-failed":
                if result.get("review_record"):
                    print(result["review_record"])
                    record_text = Path(result["review_record"]).read_text(encoding="utf-8")
                    for line in record_text.splitlines():
                        if line.startswith("CHECK "):
                            print(line)
                _print_next_step(keel_home, folder, args.phase, result.get("verdict"))
            return 0 if result["status"] in ("done", "no-change") else 1

        if args.command in ("verify", "audit", "archive"):
            folder = work.resolve(kb, args.work)
            if args.command == "verify":
                results = json.loads(Path(args.results).read_text(encoding="utf-8")) if args.results else None
                print(close.verify(folder, results, args.exempt).resolve())
                return 0
            if args.command == "audit":
                passed, problems, record = close.audit(keel_home, folder)
                print(record.resolve())
                for problem in problems:
                    print(problem)
                return 0 if passed else 1
            result = close.archive(kb, folder)
            print(result if result == "already archived" else (kb / "wiki" / "work-archive.md").resolve())
            return 0

        if args.stage == "phase" and args.phase is None:
            print("phase number required")
            return 2
        if args.stage != "phase" and args.phase is not None:
            print("phase number only valid with phase review")
            return 2
        if args.stage != "light" and not args.work:
            print("--work required")
            return 2
        if args.stage in ("phase", "light") and not args.repo:
            print("--repo required")
            return 2
        folder = work.resolve(kb, args.work) if args.work else None
        limit = {}
        if args.stage == "phase":
            try:
                limit["phase_limit"] = ladder.load(keel_home).phase_review_limit
            except ladder.LadderError as error:
                raise critic.Refused(str(error)) from error
        verdict, record, checks, answer = critic.run_review(
            args.stage, keel_home=keel_home, kb=kb, folder=folder,
            repo=Path(args.repo).resolve() if args.repo else None,
            phase=args.phase, request=args.request, base=args.base,
            statements=Path(args.statements).resolve() if args.statements else None,
            extra_review=args.extra_review, **limit,
        )
        print(verdict)
        if record:
            print(record.resolve())
        for line in checks:
            print(line)
        if args.stage == "light" or verdict == "ERROR":
            print(answer)
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[verdict]
    except runner.RunError as error:
        print(error)
        return 3
    except critic.Refused as error:
        print(error)
        return 3
    except (OSError, LookupError, ValueError, critic.ReviewError, close.CloseError) as error:
        if isinstance(error, LookupError) and len(error.args) > 1:
            print(error.args[0])
            for candidate in error.args[1]:
                print(candidate)
        else:
            print(error)
        return 1 if args.command in ("verify", "audit", "archive") else 2
