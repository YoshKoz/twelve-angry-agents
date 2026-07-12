"""Stateless wrapper around `claude -p`. One call = one agent turn.

NEVER use the Anthropic SDK or an API key here — user is on Claude Max,
all LLM calls go through the claude CLI subprocess.
"""

import json
import subprocess


class AgentError(Exception):
    """claude -p failed after retries."""


class MalformedReply(AgentError):
    """Agent kept returning invalid JSON."""


def ask(system_prompt, user_prompt, timeout=120):
    """Spawn `claude -p`, return stripped stdout. Retries once on failure."""
    cmd = ["claude", "-p", user_prompt, "--system-prompt", system_prompt]
    last_err = None
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            last_err = f"claude -p timed out after {timeout}s"
            continue
        if proc.returncode == 0:
            return proc.stdout.strip()
        last_err = f"claude -p exit {proc.returncode}: {proc.stderr.strip()}"
    raise AgentError(last_err)


def _extract_json(text):
    """Best-effort: parse the outermost {...} block. Returns dict or None."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def ask_json(system_prompt, user_prompt, required_keys, timeout=120, retries=3):
    """ask(), then parse + validate JSON. Retries with a schema reminder."""
    schema_hint = json.dumps({k: "..." for k in required_keys})
    prompt = user_prompt
    for _ in range(retries):
        text = ask(system_prompt, prompt, timeout=timeout)
        obj = _extract_json(text)
        if obj is not None and all(k in obj for k in required_keys):
            return obj
        prompt = (user_prompt
                  + "\n\nReturn ONLY valid JSON matching this schema, "
                  + "no prose: " + schema_hint)
    raise MalformedReply(
        f"no valid JSON with keys {required_keys} after {retries} attempts")
