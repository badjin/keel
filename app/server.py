"""Thin localhost HTTP layer over kit/. See DOCS/plans/2026-09-23-starter-kit-plan.md Phase 5."""
from __future__ import annotations
import argparse
import json
import mimetypes
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "app" / "static"

sys.path.insert(0, str(REPO_ROOT))

from app.jobs import JobRunner  # noqa: E402
from kit import github, history, install_hooks, llm_pass, local_index, obsidian, render_repo, skills_install, wiki_init  # noqa: E402
from kit.errors import KitRuntimeError, KitValueError, message as err_message, progress as kit_progress  # noqa: E402

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

DAYS_MIN = 30
DAYS_MAX = 180


class KitState:
    def __init__(self, home: Path, port: int, token: str):
        self.home = Path(home)
        self.port = port
        self.token = token
        self.github_token = None
        self.github_repos_cache = {}
        self.jobs = JobRunner()

    def redact(self, text: str) -> str:
        if self.github_token:
            return github._redact(text, self.github_token)
        return text

    def config(self):
        config_path = self.home / ".keel" / "config.json"
        if not config_path.exists():
            return None
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or not data.get("wiki_path"):
            # A config file can now exist with only `ui_language` recorded
            # (hooks installed before the wiki was created) — that is not
            # "the wiki is ready" for every other caller here, which reads
            # config()["wiki_path"] unconditionally once config() is truthy.
            return None
        return data


def _read_config_raw(home: Path) -> dict:
    """The raw config.json dict, or `{}` if missing/corrupt. Shared by every
    read-modify-write writer below — deliberately does not go through
    KitState.config(), which now treats a wiki_path-less config as "no
    wiki yet" (a writer may need to record a choice before the wiki
    exists, e.g. installing hooks before creating the wiki)."""
    config_path = home / ".keel" / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _merge_config(home: Path, updates: dict) -> None:
    """Read-merge-write of `updates` into config.json, creating it if
    needed, keeping every other key untouched. The one merge path every
    kit-owned config writer shares (ui_language, watch_repos, ...) instead
    of each hand-rolling its own read/write."""
    config_path = home / ".keel" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config_raw(home)
    data.update(updates)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_ui_language(home: Path, ui_language: str) -> None:
    _merge_config(home, {"ui_language": ui_language if ui_language in ("en", "ko") else "en"})


def _record_watch_repo(home: Path, repo: str, branch: str, watch: bool, days: int, language: str, llm) -> None:
    """Adds/replaces or removes `{repo, branch, days, language, llm}` in
    config.json's `watch_repos` list, keyed by `repo` alone — pages are
    rendered per repo (render_repo._resolve_slug), never per branch, so at
    most one watch entry per repo can ever mean anything. Recording the
    same repo again (even on a different branch) replaces its entry."""
    data = _read_config_raw(home)
    existing = data.get("watch_repos")
    watch_repos = [w for w in existing if isinstance(w, dict)] if isinstance(existing, list) else []
    watch_repos = [w for w in watch_repos if w.get("repo") != repo]
    if watch:
        watch_repos.append({"repo": repo, "branch": branch, "days": days, "language": language, "llm": llm})
    _merge_config(home, {"watch_repos": watch_repos})


def _read_json_body(handler: "Handler") -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _clamp_days(days) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        n = DAYS_MIN
    return max(DAYS_MIN, min(DAYS_MAX, n))


def _expand_under_home(home: Path, raw: str) -> Path:
    """Expands a leading `~` against the server's --home, never the process HOME."""
    if raw == "~":
        return Path(home)
    if raw.startswith("~/"):
        return Path(home) / raw[2:]
    return Path(raw)


def _find_git_repos(state: KitState, folders: list) -> list:
    """Absolute paths of git repos among `folders`: each folder itself if it
    is a repo, plus its immediate subfolders that are repos."""
    repos = []
    for folder in folders:
        folder = local_index.safe_under(state.home, folder)
        if (folder / ".git").exists():
            repos.append(str(folder))
        for child in local_index.list_dirs(state.home, folder):
            if child.get("is_git"):
                repos.append(child["path"])
    return repos


def _github_default_branch(state: KitState, full_name: str) -> str:
    branch = state.github_repos_cache.get(full_name)
    if branch:
        return branch
    gh = github.GitHub(state.github_token)
    repo = gh.get_repo(full_name)
    branch = repo.get("default_branch")
    state.github_repos_cache[full_name] = branch
    if not branch:
        raise KitRuntimeError("default_branch_unknown", full_name=full_name)
    return branch


def _run_history_item(state: KitState, item: dict, days: int, language: str, llm: str, log, ui_lang: str = "en") -> list:
    kind = item.get("kind")
    branch = item.get("branch") or None
    repo_or_path = item.get("repo_or_path", "")
    label = repo_or_path

    if kind == "github":
        if not state.github_token:
            raise KitRuntimeError("gh_token_not_connected")
        branch = branch or _github_default_branch(state, repo_or_path)
        log(kit_progress("fetching", ui_lang, label=label))
        cache_dir = state.home / ".keel" / "cache"
        git_dir = github.fetch_repo(repo_or_path, branch, cache_dir, state.github_token)
        changes = history.collect_changes(git_dir, branch, days)
        source = f"https://github.com/{repo_or_path}"
        repo_name = repo_or_path
    elif kind == "local":
        path = local_index.safe_under(state.home, Path(repo_or_path))
        branch = branch or local_index.git_branches(path)["default"]
        log(kit_progress("fetching", ui_lang, label=label))
        changes = history.collect_changes(path, branch, days)
        source = str(path)
        repo_name = path.name
    else:
        raise KitValueError("unknown_kind", kind=kind)

    log(kit_progress("changes", ui_lang, label=label, n=len(changes)))

    summaries = None
    if llm:
        prompts = llm_pass.build_prompts(repo_name, changes, language)
        summaries = llm_pass.summarize(
            llm, prompts,
            progress=lambda scope: log(kit_progress("summarizing", ui_lang, label=label, scope=scope)),
        )

    config = state.config()
    if not config:
        raise KitValueError("wiki_not_ready")
    wiki_path = Path(config["wiki_path"])
    written = render_repo.render_repo(wiki_path, repo_name, branch, days, changes, language, summaries, source)
    log(kit_progress("pages_written", ui_lang, label=label, n=len(written)))

    if kind == "github" and "watch" in item:
        _record_watch_repo(state.home, repo_or_path, branch, bool(item.get("watch")), days, language, llm)

    return written


class Handler(BaseHTTPRequestHandler):
    server: "ThreadingHTTPServer"

    def log_message(self, fmt, *args):  # noqa: A003 - silence default stderr logging
        pass

    @property
    def state(self) -> KitState:
        return self.server.state  # type: ignore[attr-defined]

    # ---- auth / dispatch ----

    def _authorized(self) -> bool:
        token = self.headers.get("X-Kit-Token") or ""
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.state.port}", f"localhost:{self.state.port}"}
        token_ok = secrets.compare_digest(token.encode("utf-8"), self.state.token.encode("utf-8"))
        return token_ok and host in allowed

    def _lang(self) -> str:
        lang = self.headers.get("X-Kit-Lang") or ""
        return lang if lang in ("en", "ko") else "en"

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, exc: Exception) -> None:
        lang = self._lang()
        if isinstance(exc, (KitValueError, KitRuntimeError)):
            text = err_message(exc.code, lang, **exc.kwargs)
        else:
            text = str(exc)
        self._send_json(status, {"error": self.state.redact(text)})

    def _forbidden(self) -> None:
        self._send_json(403, {"error": err_message("forbidden", self._lang())})

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/" or (not path.startswith("/api/") and not path.startswith("/static/")):
            self._serve_static("/index.html" if path == "/" else path)
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static"):])
            return
        if not self._authorized():
            self._forbidden()
            return
        self._route_get(path)

    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        if not self._authorized():
            self._forbidden()
            return
        self._route_post(path)

    # ---- static ----

    def _serve_static(self, rel_path: str) -> None:
        rel_path = rel_path.lstrip("/") or "index.html"
        candidate = (STATIC_DIR / rel_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_response(404)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if not candidate.is_file():
            self.send_response(404)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        content_type = CONTENT_TYPES.get(candidate.suffix)
        if content_type is None:
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- API routing ----

    def _route_get(self, path: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        try:
            if path == "/api/env":
                self._api_env()
            elif path == "/api/catalogue":
                self._api_catalogue()
            elif path == "/api/fs/list":
                self._api_fs_list(query)
            elif path == "/api/github/repos":
                self._api_github_repos()
            elif path == "/api/github/branches":
                self._api_github_branches(query)
            elif path == "/api/local/branches":
                self._api_local_branches(query)
            elif path.startswith("/api/jobs/"):
                self._api_jobs_get(path[len("/api/jobs/"):])
            elif path == "/api/hooks/codex-trust":
                self._api_hooks_codex_trust()
            else:
                self._send_json(404, {"error": err_message("not_found", self._lang())})
        except Exception as exc:  # noqa: BLE001 - ValueError and others both map to 400
            self._send_error(400, exc)

    def _route_post(self, path: str) -> None:
        try:
            body = _read_json_body(self)
        except Exception:
            self._send_json(400, {"error": err_message("bad_json", self._lang())})
            return
        try:
            if path == "/api/hooks/install":
                self._api_hooks_install(body)
            elif path == "/api/hooks/uninstall":
                self._api_hooks_uninstall(body)
            elif path == "/api/wiki/init":
                self._api_wiki_init(body)
            elif path == "/api/wiki/local":
                self._api_wiki_local(body)
            elif path == "/api/github/token":
                self._api_github_token(body)
            elif path == "/api/history/run":
                self._api_history_run(body)
            elif path == "/api/obsidian/open":
                self._api_obsidian_open()
            elif path == "/api/finish":
                self._api_finish()
            else:
                self._send_json(404, {"error": err_message("not_found", self._lang())})
        except Exception as exc:  # noqa: BLE001 - ValueError and others both map to 400
            self._send_error(400, exc)

    # ---- 5.1 endpoints ----

    def _api_env(self) -> None:
        state = self.state
        home = state.home
        version_path = REPO_ROOT / "VERSION"
        version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "dev"
        self._send_json(
            200,
            {
                "home": str(home),
                "default_wiki": str(home / "knowledge-base"),
                "clis": {
                    "claude": bool(shutil.which("claude")),
                    "codex": bool(shutil.which("codex")),
                    "grok": bool(shutil.which("grok")),
                },
                "gh_cli": bool(shutil.which("gh")),
                "obsidian": obsidian.find_obsidian(home),
                "obsidian_hint": obsidian.install_hint(),
                "config": state.config(),
                "version": version,
            },
        )

    def _api_catalogue(self) -> None:
        state = self.state
        catalogue = install_hooks.load_catalogue(REPO_ROOT)
        installed = install_hooks.installed_ids(state.home)
        config_path = state.home / ".keel" / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        self._send_json(200, {"catalogue": catalogue, "installed": installed,
                              "handoff_targets": config.get("handoff_targets", []),
                              "handoff_threshold": config.get("handoff_threshold", 60)})

    def _api_hooks_install(self, body: dict) -> None:
        state = self.state
        ids = body.get("ids", [])
        targets = body.get("targets", [])
        result = install_hooks.install(
            state.home, REPO_ROOT, ids, targets, python=sys.executable,
            handoff_threshold=body.get("handoff_threshold", 60),
            handoff_targets=body.get("handoff_targets", []),
            ui_language=self._lang(),
        )
        # Recorded here too (not only by wiki/init) so a user who installs
        # hooks before creating the wiki still gets bilingual hook text.
        _record_ui_language(state.home, self._lang())
        self._send_json(200, result)

    def _api_hooks_codex_trust(self) -> None:
        state = self.state
        result = install_hooks.codex_trust_status(state.home, REPO_ROOT)
        self._send_json(200, result)

    def _api_hooks_uninstall(self, body: dict) -> None:
        state = self.state
        targets = body.get("targets", [])
        remove_skills = bool(body.get("remove_skills", False))
        result = install_hooks.uninstall(state.home, targets, remove_skills=remove_skills)
        self._send_json(200, result)

    # ---- 5.2 endpoints ----

    def _api_wiki_init(self, body: dict) -> None:
        state = self.state
        raw_path = body.get("path")
        if not raw_path:
            raise KitValueError("path_required")
        wiki_path = _expand_under_home(state.home, raw_path)
        language = body.get("language") or self._lang()
        targets = body.get("targets", [])
        created = wiki_init.init_wiki(state.home, wiki_path, language, ui_language=self._lang())
        skills = skills_install.install_skills(state.home, wiki_path, targets)
        self._send_json(200, {"created": [str(p) for p in created], "skills": [str(p) for p in skills]})

    def _api_fs_list(self, query: dict) -> None:
        state = self.state
        raw_path = query.get("path", [str(state.home)])[0]
        base = local_index.safe_under(state.home, Path(raw_path))
        home_resolved = state.home.resolve()
        parent = None if base == home_resolved else str(base.parent)
        dirs = local_index.list_dirs(state.home, base)
        self._send_json(200, {"path": str(base), "parent": parent, "dirs": dirs})

    def _api_wiki_local(self, body: dict) -> None:
        state = self.state
        config = state.config()
        if not config:
            raise KitValueError("wiki_not_ready")
        wiki_path = Path(config["wiki_path"])
        language = config.get("language", "ko")
        folders = [_expand_under_home(state.home, f) for f in body.get("folders", [])]
        written = local_index.build_local_index(state.home, wiki_path, folders, language)
        git_repos = _find_git_repos(state, folders)
        self._send_json(200, {"written": [str(p) for p in written], "git_repos": git_repos})

    def _api_obsidian_open(self) -> None:
        state = self.state
        config = state.config()
        if not config:
            raise KitValueError("wiki_not_ready")
        wiki_path = Path(config["wiki_path"])
        obsidian.write_obsidian_config(wiki_path)
        url = obsidian.open_url(wiki_path)
        if sys.platform == "darwin":
            subprocess.run(["open", url], capture_output=True, check=False)
        self._send_json(200, {"url": url})

    def _api_finish(self) -> None:
        state = self.state
        config = state.config()
        if not config:
            raise KitValueError("wiki_not_ready")
        wiki_path = Path(config["wiki_path"])
        language = config.get("language", "ko")
        hooks = install_hooks.installed_ids(state.home)
        skills = sorted(
            name
            for name in skills_install.SKILL_NAMES
            for base in skills_install.TARGET_BASE.values()
            if (state.home / base / "skills" / name / "SKILL.md").exists()
        )
        skills = sorted(set(skills))
        repos_dir = wiki_path / "repos"
        repos = []
        if repos_dir.is_dir():
            repos = sorted(p.name for p in repos_dir.iterdir() if p.is_dir())

        result = {
            "finished_at": datetime.now().isoformat(),
            "wiki_path": str(wiki_path),
            "language": language,
            "hooks": hooks,
            "skills": skills,
            "repos": repos,
        }
        result_path = state.home / ".keel" / "install-result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._send_json(200, result)

    # ---- 5.3 endpoints ----

    def _api_github_token(self, body: dict) -> None:
        state = self.state
        if body.get("use_gh"):
            token = github.gh_cli_token()
            if not token:
                raise KitValueError("gh_cli_token_missing")
        else:
            token = (body.get("token") or "").strip()
            if not token:
                raise KitValueError("token_required")
        gh = github.GitHub(token)
        try:
            login = gh.whoami()
        except Exception as exc:  # noqa: BLE001 - candidate token isn't stored yet, redact it directly
            raise ValueError(github._redact(str(exc), token)) from exc
        state.github_token = token
        self._send_json(200, {"login": login})

    def _api_github_repos(self) -> None:
        state = self.state
        if not state.github_token:
            raise KitValueError("gh_not_connected")
        gh = github.GitHub(state.github_token)
        repos = gh.list_repos()
        state.github_repos_cache = {r["full_name"]: r.get("default_branch") for r in repos}
        self._send_json(200, repos)

    def _api_github_branches(self, query: dict) -> None:
        state = self.state
        if not state.github_token:
            raise KitValueError("gh_not_connected")
        repo = query.get("repo", [""])[0]
        gh = github.GitHub(state.github_token)
        self._send_json(200, gh.list_branches(repo))

    def _api_local_branches(self, query: dict) -> None:
        state = self.state
        raw_path = query.get("path", [""])[0]
        if not raw_path:
            raise KitValueError("path_required")
        target = local_index.safe_under(state.home, Path(raw_path))
        result = local_index.git_branches(target)
        self._send_json(200, result)

    def _api_history_run(self, body: dict) -> None:
        state = self.state
        if not state.config():
            raise KitValueError("wiki_not_ready")
        items = body.get("items", [])
        days = _clamp_days(body.get("days"))
        language = body.get("language", "ko")
        llm = body.get("llm") or None
        if llm not in (None, "claude", "codex"):
            raise KitValueError("llm_invalid")
        ui_lang = self._lang()  # captured now: progress lines outlive this request

        def run(log) -> list:
            all_written: list = []
            any_success = False
            for item in items:
                label = item.get("repo_or_path", "")
                try:
                    written = _run_history_item(state, item, days, language, llm, log, ui_lang)
                    all_written.extend(written)
                    any_success = True
                except Exception as exc:  # noqa: BLE001 - one item's failure must not stop the others
                    err_text = (
                        err_message(exc.code, ui_lang, **exc.kwargs)
                        if isinstance(exc, (KitValueError, KitRuntimeError))
                        else str(exc)
                    )
                    log(kit_progress("failed", ui_lang, label=label, err=state.redact(err_text)))
            if items and not any_success:
                raise KitRuntimeError("all_failed")
            return all_written

        job_id = state.jobs.start(run, exclusive=True, lang=ui_lang)
        if job_id is None:
            self._send_json(409, {"error": err_message("job_running", self._lang())})
            return
        self._send_json(200, {"job": job_id})

    def _api_jobs_get(self, job_id: str) -> None:
        state = self.state
        job = state.jobs.get(job_id)
        if job is None:
            self._send_json(404, {"error": err_message("not_found", self._lang())})
            return
        self._send_json(200, job)


def make_server(home, port: int = 0):
    home = Path(home)
    result_path = home / ".keel" / "install-result.json"
    if result_path.exists():
        prev_path = result_path.with_name("install-result.prev.json")
        result_path.replace(prev_path)
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    server.state = KitState(home, actual_port, token)  # type: ignore[attr-defined]
    return server, token


def _detach(args) -> int:
    child_argv = [a for a in sys.argv[1:] if a != "--detach"]
    log_path = Path(args.log)
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), *child_argv],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    Path(args.pid_file).write_text(str(proc.pid), encoding="utf-8")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if "http://127.0.0.1:" in line:
                print(line)
                return 0
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--log", default="/tmp/keel-install.log")
    parser.add_argument("--pid-file", default="/tmp/keel-server.pid")
    args = parser.parse_args()

    if args.detach:
        sys.exit(_detach(args))

    server, token = make_server(Path(args.home), args.port)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"Open / 열기: {url}")
    sys.stdout.flush()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
