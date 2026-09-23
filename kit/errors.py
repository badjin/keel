"""Shared bilingual error codes for kit modules and the local server.

Each raise site across kit/*.py and app/server.py used to embed a Korean
message directly. Task 6.15 (bilingual UI) needs those messages to come
back in whichever language the page is using, so every deliberate error
now carries a `code` (plus format kwargs) and looks its text up here at
render time via `message(code, lang, **kwargs)`.
"""
from __future__ import annotations

MESSAGES = {
    "forbidden": {"en": "Not authorized", "ko": "권한이 없습니다"},
    "not_found": {"en": "Not found", "ko": "찾을 수 없습니다"},
    "bad_json": {"en": "Invalid JSON body", "ko": "잘못된 JSON 본문입니다"},
    "job_running": {"en": "A job is already running", "ko": "이미 실행 중인 작업이 있습니다"},
    "unknown_kind": {"en": "Unknown kind: {kind}", "ko": "알 수 없는 종류: {kind}"},
    "wiki_not_ready": {
        "en": "The wiki has not been created yet",
        "ko": "위키가 아직 만들어지지 않았습니다",
    },
    "path_required": {"en": "A path is required", "ko": "경로가 필요합니다"},
    "gh_cli_token_missing": {
        "en": "Could not find a gh CLI login token",
        "ko": "gh CLI 로그인 토큰을 찾을 수 없습니다",
    },
    "token_required": {"en": "A token is required", "ko": "토큰이 필요합니다"},
    "gh_not_connected": {
        "en": "GitHub is not connected",
        "ko": "GitHub 이 연결되어 있지 않습니다",
    },
    "gh_token_not_connected": {
        "en": "The GitHub token is not connected",
        "ko": "GitHub 토큰이 연결되어 있지 않습니다",
    },
    "default_branch_unknown": {
        "en": "Could not determine the default branch for {full_name}",
        "ko": "{full_name} 의 기본 브랜치를 확인할 수 없습니다",
    },
    "llm_invalid": {"en": "The llm value is not valid", "ko": "llm 값이 올바르지 않습니다"},
    "all_failed": {"en": "Every item failed", "ko": "모든 항목이 실패했습니다"},
    "unknown_hook_id": {
        "en": "Unknown hook id(s): {ids}",
        "ko": "알 수 없는 훅 id: {ids}",
    },
    "invalid_handoff_threshold": {
        "en": "Handoff threshold must be an integer from 40 to 80",
        "ko": "핸드오프 기준값은 40부터 80까지의 정수여야 합니다",
    },
    "path_unresolvable": {
        "en": "Could not resolve path: {p}",
        "ko": "경로를 확인할 수 없습니다: {p}",
    },
    "path_outside_home": {
        "en": "{p} is not under {home}",
        "ko": "{p} 는 {home} 아래에 있지 않습니다",
    },
    "not_git_repo": {
        "en": "{path} is not a git repository",
        "ko": "{path} 는 git 저장소가 아닙니다",
    },
    "branch_check_failed": {
        "en": "Could not determine branches",
        "ko": "브랜치를 확인할 수 없습니다",
    },
    "wiki_path_outside_home": {
        "en": "Wiki path {wiki_path} must be under home {home}",
        "ko": "위키 경로 {wiki_path} 는 홈 {home} 아래에 있어야 합니다",
    },
    "github_api_error": {
        "en": "GitHub API error {status}: {url}",
        "ko": "GitHub API 오류 {status}: {url}",
    },
    "git_fetch_failed": {
        "en": "git command failed (rc={rc}): {stderr}",
        "ko": "git 명령이 실패했습니다 (rc={rc}): {stderr}",
    },
}


PROGRESS = {
    "fetching": {"en": "{label}: fetching", "ko": "{label}: 가져오는 중"},
    "changes": {"en": "{label}: {n} changes", "ko": "{label}: 변경 {n}건"},
    "summarizing": {"en": "{label}: summarizing {scope}", "ko": "{label}: 요약 {scope}"},
    "pages_written": {"en": "{label}: {n} pages written", "ko": "{label}: 페이지 {n}개 작성"},
    "failed": {"en": "{label}: failed — {err}", "ko": "{label}: 실패 — {err}"},
}


def _lookup(table: dict, code: str, lang: str, **kwargs) -> str:
    lang = lang if lang in ("en", "ko") else "en"
    entry = table.get(code)
    if entry is None:
        return code
    text = entry.get(lang, entry["en"])
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def progress(code: str, lang: str = "en", **kwargs) -> str:
    return _lookup(PROGRESS, code, lang, **kwargs)


def message(code: str, lang: str = "en", **kwargs) -> str:
    return _lookup(MESSAGES, code, lang, **kwargs)


class KitValueError(ValueError):
    def __init__(self, code: str, **kwargs):
        self.code = code
        self.kwargs = kwargs
        super().__init__(message(code, "en", **kwargs))


class KitRuntimeError(RuntimeError):
    def __init__(self, code: str, **kwargs):
        self.code = code
        self.kwargs = kwargs
        super().__init__(message(code, "en", **kwargs))
