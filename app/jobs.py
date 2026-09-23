"""Runs a background job on a thread and tracks its progress for polling."""
from __future__ import annotations
import threading
import uuid
from typing import Callable, Optional

# Bilingual by the request's language (see start()'s `lang` — X-Kit-Lang on
# the /api/history/run call that started this job); default English, same
# convention as kit/errors.py's message tables.
_DONE_TEXT = {"en": "Done", "ko": "완료"}
_ERROR_TEXT = {"en": "Error: {err}", "ko": "오류: {err}"}


def _job_text(table: dict, lang: str, **kwargs) -> str:
    text = table.get(lang, table["en"])
    return text.format(**kwargs)


class JobRunner:
    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def start(self, fn: Callable, exclusive: bool = False, lang: str = "en") -> Optional[str]:
        job_id = uuid.uuid4().hex
        job = {"state": "running", "log": [], "written": []}
        with self._lock:
            if exclusive and any(j["state"] == "running" for j in self._jobs.values()):
                return None
            self._jobs[job_id] = job

        def log(message: str) -> None:
            with self._lock:
                job["log"].append(message)

        def run() -> None:
            try:
                written = fn(log)
                with self._lock:
                    job["written"] = [str(p) for p in (written or [])]
                    job["log"].append(_job_text(_DONE_TEXT, lang))
                    job["state"] = "done"
            except Exception as exc:  # noqa: BLE001 - job errors surface via state/log
                with self._lock:
                    job["log"].append(_job_text(_ERROR_TEXT, lang, err=str(exc)))
                    job["state"] = "error"

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return job_id

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {"state": job["state"], "log": list(job["log"]), "written": list(job["written"])}
