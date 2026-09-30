from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DEFAULTS = {
    "phase_review_limit": 3,
    "steps": [
        {"claude": {"model": "sonnet", "effort": "xhigh"},
         "codex": {"model": None, "effort": "high"}},
        {"claude": {"model": "opus", "effort": "xhigh"},
         "codex": {"model": None, "effort": "xhigh"}},
    ],
}


class LadderError(Exception):
    pass


@dataclass
class Step:
    claude: dict
    codex: dict


@dataclass
class Ladder:
    phase_review_limit: int
    steps: List[Step]


def _parse(data: dict, path: Path) -> Ladder:
    def bad(reason: str) -> LadderError:
        return LadderError(f"{path}: {reason}")

    if not isinstance(data, dict):
        raise bad("expected a JSON object")
    limit = data.get("phase_review_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise bad("phase_review_limit must be an integer >= 1")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise bad("steps must be a non-empty list")
    steps = []
    for index, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict):
            raise bad(f"step {index} must be an object")
        parts = {}
        for cli in ("claude", "codex"):
            part = raw.get(cli)
            if not isinstance(part, dict):
                raise bad(f"step {index} is missing {cli}")
            if not isinstance(part.get("effort"), str) or not part["effort"]:
                raise bad(f"step {index} {cli}.effort must be a string")
            model = part.get("model")
            if model is None and cli == "codex":
                pass
            elif not isinstance(model, str) or not model:
                raise bad(f"step {index} {cli}.model must be a string")
            parts[cli] = {"model": model, "effort": part["effort"]}
        steps.append(Step(claude=parts["claude"], codex=parts["codex"]))
    return Ladder(phase_review_limit=limit, steps=steps)


def load(keel_home) -> Ladder:
    path = Path(keel_home) / "ladder.json"
    if not path.exists():
        return _parse(DEFAULTS, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LadderError(f"{path}: cannot read ({error})")
    return _parse(data, path)


def next_step(fails: int, ladder: Ladder) -> Optional[Step]:
    if fails >= ladder.phase_review_limit:
        return None
    return ladder.steps[min(fails, len(ladder.steps)) - 1]
