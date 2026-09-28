from __future__ import annotations

import json
import re
import unicodedata


SYNTHETIC_USER_PREFIXES = (
    "<environment_context>", "<user_instructions>", "<INSTRUCTIONS>",
    "# AGENTS.md instructions", "<command-name>", "<command-message>",
    "<command-args>", "<local-command-", "<system-reminder>",
    "<user-prompt-submit-hook>", "Caveat: The messages below were generated",
    "<task-notification>", "<hook_prompt", "Base directory for this skill:", "Stop hook feedback:",
)


def _user_text(content) -> str:
    if isinstance(content, str):
        return "" if content.strip().startswith(SYNTHETIC_USER_PREFIXES) else content
    if isinstance(content, list):
        return "".join(
            part["text"] for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text")
            and isinstance(part.get("text"), str)
            and not part["text"].strip().startswith(SYNTHETIC_USER_PREFIXES)
        )
    return ""


def user_messages(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as source:
            lines = source.readlines()
    except OSError:
        return []
    messages = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if (row.get("type") == "user" and not row.get("isCompactSummary")
                and not row.get("isMeta") and isinstance(row.get("message"), dict)):
            message = row["message"]
            if message.get("role", "user") == "user":
                content = message.get("content")
                text = _user_text(content)
                if text.strip():
                    messages.append(text)
        elif (row.get("type") == "queue-operation" and row.get("operation") == "remove"
              and row.get("reason") == "absorbed_mid_turn"):
            text = _user_text(row.get("content"))
            if text.strip():
                messages.append(text)
        elif row.get("type") == "response_item" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            if payload.get("type") == "message" and payload.get("role") == "user":
                text = _user_text(payload.get("content"))
                if text.strip():
                    messages.append(text)
    return messages


def _string_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _string_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _string_values(item)]
    return []


def tool_call_texts(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as source:
            lines = source.readlines()
    except (OSError, UnicodeError):
        return []
    texts = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "assistant" and isinstance(row.get("message"), dict):
            content = row["message"].get("content")
            for block in content if isinstance(content, list) else []:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") not in ("Write", "Edit", "MultiEdit", "NotebookEdit")):
                    texts.extend(_string_values(block.get("input")))
        elif row.get("type") == "response_item" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            if payload.get("type") == "function_call":
                if payload.get("name") == "apply_patch":
                    continue
                arguments = payload.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (ValueError, TypeError):
                        pass
                texts.extend(_string_values(arguments))
            elif payload.get("type") == "custom_tool_call":
                if payload.get("name") != "apply_patch":
                    texts.extend(_string_values(payload.get("input")))
            elif payload.get("type") == "local_shell_call":
                action = payload.get("action")
                if isinstance(action, dict) and isinstance(action.get("command"), list):
                    texts.append(" ".join(item for item in action["command"] if isinstance(item, str)))
    return texts


_ASSIGNMENT = re.compile(r"(?:^|[;\n]|&&|\|\||\||[({]|\s)\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)=(?:'([^']*)'|\"([^\"]*)\"|([^\s;&|()]+))")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))")


def expand_shell_variables(text: str, home: str) -> str:
    values = {"HOME": home}
    parts = []
    position = 0

    def expand(segment: str) -> str:
        return _VARIABLE.sub(lambda match: values.get(match.group(1) or match.group(2), match.group(0)), segment)

    for assignment in _ASSIGNMENT.finditer(text):
        parts.append(expand(text[position:assignment.start()]))
        value_group = next(group for group in (2, 3, 4) if assignment.group(group) is not None)
        value = assignment.group(value_group)
        if value_group != 2:
            value = expand(value)
        parts.extend((text[assignment.start():assignment.start(value_group)], value,
                      text[assignment.end(value_group):assignment.end()]))
        values[assignment.group(1)] = value
        position = assignment.end()
    parts.append(expand(text[position:]))
    return "".join(parts)


def path_variants(text: str, home: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", text)
    expanded = unicodedata.normalize("NFC", expand_shell_variables(normalized, home))
    return tuple(variant for base in (normalized, expanded)
                 for variant in (base, base.replace("'", "").replace('"', "").replace("\\ ", " ")))


def read_evidence(texts: list[str], abs_path: str, home: str) -> bool:
    page = unicodedata.normalize("NFC", str(abs_path))
    home_path = unicodedata.normalize("NFC", str(home)).rstrip("/")
    home_page = "~" + page[len(home_path):] if page.startswith(home_path + "/") else None
    for text in texts:
        if any(page in variant or (home_page is not None and home_page in variant)
               for variant in path_variants(text, home_path)):
            return True
    return False


def user_messages_with_context(path: str) -> list[tuple[str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as source:
            lines = source.readlines()
    except (OSError, UnicodeError):
        return []
    messages = []
    context = ""
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "assistant" and isinstance(row.get("message"), dict):
            content = row["message"].get("content")
            if isinstance(content, list):
                parts = [part["text"] for part in content if isinstance(part, dict)
                         and part.get("type") == "text" and isinstance(part.get("text"), str)]
                if parts:
                    context = "".join(parts)
        elif (row.get("type") == "user" and not row.get("isCompactSummary")
              and not row.get("isMeta") and isinstance(row.get("message"), dict)):
            message = row["message"]
            if message.get("role", "user") == "user":
                text = _user_text(message.get("content"))
                if text.strip():
                    messages.append((text, context))
                    context = ""
        elif (row.get("type") == "queue-operation" and row.get("operation") == "remove"
              and row.get("reason") == "absorbed_mid_turn"):
            text = _user_text(row.get("content"))
            if text.strip():
                messages.append((text, context))
                context = ""
        elif row.get("type") == "response_item" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                content = payload.get("content")
                if isinstance(content, list):
                    parts = [part["text"] for part in content if isinstance(part, dict)
                             and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
                    if parts:
                        context = "".join(parts)
            elif payload.get("type") == "message" and payload.get("role") == "user":
                text = _user_text(payload.get("content"))
                if text.strip():
                    messages.append((text, context))
                    context = ""
    return messages
