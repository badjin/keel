"""Minimal GitHub REST client and a token-safe bare-clone fetcher for history."""
from __future__ import annotations
import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from kit.errors import KitRuntimeError

API_ROOT = "https://api.github.com"
USER_AGENT = "keel"

_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _default_opener(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class GitHub:
    def __init__(self, token: str, opener=None):
        self.token = token
        self._opener = opener or _default_opener

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        }

    def _get_all_pages(self, url: str) -> list:
        items = []
        next_url = url
        while next_url:
            status, headers, body = self._opener(next_url, self._headers())
            if status >= 400:
                raise KitRuntimeError("github_api_error", status=status, url=next_url)
            data = json.loads(body.decode("utf-8"))
            items.extend(data if isinstance(data, list) else [data])

            link = headers.get("Link") or headers.get("link")
            next_url = None
            if link:
                match = _LINK_NEXT_RE.search(link)
                if match:
                    next_url = match.group(1)
        return items

    def whoami(self) -> str:
        status, _headers, body = self._opener(f"{API_ROOT}/user", self._headers())
        if status >= 400:
            raise KitRuntimeError("github_api_error", status=status, url="/user")
        return json.loads(body.decode("utf-8"))["login"]

    def list_repos(self) -> list:
        url = f"{API_ROOT}/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member"
        repos = self._get_all_pages(url)
        return [
            {
                "full_name": r["full_name"],
                "default_branch": r.get("default_branch"),
                "private": r.get("private"),
                "pushed_at": r.get("pushed_at"),
            }
            for r in repos
        ]

    def list_branches(self, full_name: str) -> list:
        url = f"{API_ROOT}/repos/{full_name}/branches?per_page=100"
        return [b["name"] for b in self._get_all_pages(url)]

    def get_repo(self, full_name: str) -> dict:
        return self._get_all_pages(f"{API_ROOT}/repos/{full_name}")[0]


def gh_cli_token() -> Optional[str]:
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _token_b64(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")


def _redact(text: str, token: str) -> str:
    redacted = text.replace(token, "***")
    redacted = redacted.replace(_token_b64(token), "***")
    return redacted


def fetch_repo(full_name: str, branch: str, cache_dir: Path, token: str) -> Path:
    cache_dir = Path(cache_dir)
    owner, name = full_name.split("/", 1)
    dest = cache_dir / owner / f"{name}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {_token_b64(token)}"

    if dest.exists():
        args = [
            "git",
            "--git-dir",
            str(dest),
            "fetch",
            "--filter=blob:none",
        ]
        if (dest / "shallow").exists():
            args.append("--unshallow")
        args += ["origin", f"+{branch}:{branch}"]
    else:
        args = [
            "git",
            "clone",
            "--bare",
            "--filter=blob:none",
            "--single-branch",
            "--branch",
            branch,
            f"https://github.com/{full_name}.git",
            str(dest),
        ]

    result = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        stderr = _redact(result.stderr.strip(), token)
        raise KitRuntimeError("git_fetch_failed", rc=result.returncode, stderr=stderr)

    return dest
