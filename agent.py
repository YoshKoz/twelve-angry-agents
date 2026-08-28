"""Stateless LLM wrapper. One call = one agent turn.

Two interchangeable backends, selected with AGENT_BACKEND:

    claude_cli  (default) — headless `claude -p` subprocesses. Every agent in
                 the room is a real Claude process with its own system prompt,
                 fully isolated from this repo's CLAUDE.md, hooks, output
                 style and tools, so nothing leaks into a juror's voice.
    ollama      — a LAN Ollama instance (OLLAMA_HOST / OLLAMA_MODEL).

Both expose the same `ask` / `ask_json` / `ask_json_detailed` surface, so the
rest of the system never learns which one is running.
"""

import json
import os
import subprocess
import tempfile

import httpx

BACKEND = os.environ.get("AGENT_BACKEND", "claude_cli")

# --- claude_cli backend ----------------------------------------------------

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

# Per-role models. Jurors are called far more often than anyone else, so they
# default to the cheap fast model; the foreman, bailiff and judge make the
# structural calls that shape the run and get a stronger one.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")
CLAUDE_MODEL_FOREMAN = os.environ.get("CLAUDE_MODEL_FOREMAN", CLAUDE_MODEL)

# `claude -p` inherits context from its working directory: CLAUDE.md files
# found by walking up from cwd, plus settings-driven hooks and output styles.
# A juror must be nothing but its persona, so every call runs from an empty
# scratch dir outside the home tree with settings neutralized.
_WORKDIR = tempfile.mkdtemp(prefix="juryroom-")

_ISOLATION_ARGS = [
    # replaces the Claude Code system prompt outright — the juror persona is
    # the entire system prompt, no assistant identity underneath it
    "--exclude-dynamic-system-prompt-sections",
    "--output-format", "json",
    "--allowed-tools", "",          # a juror deliberates, it does not act
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--settings", '{"outputStyle":"default","hooks":{}}',
]


class AgentError(Exception):
    """LLM call failed after retries."""


class MalformedReply(AgentError):
    """Agent kept returning invalid JSON."""


def _claude_result_text(stdout):
    """Pull the final assistant text out of `claude -p --output-format json`.

    The payload is either a result object or a list of stream events whose
    last entry is that object.
    """
    data = json.loads(stdout)
    if isinstance(data, list):
        if not data:
            raise AgentError("claude returned an empty event list")
        data = data[-1]
    if not isinstance(data, dict):
        raise AgentError(f"claude returned unexpected payload: {type(data)}")
    if data.get("is_error"):
        raise AgentError(f"claude reported an error: {data.get('result')!r}")
    text = data.get("result")
    if not isinstance(text, str):
        raise AgentError(f"claude returned no result text: {data!r}")
    return text.strip()


def _ask_claude_cli(system_prompt, user_prompt, timeout, model):
    cmd = [
        CLAUDE_BIN, "-p", user_prompt,
        "--system-prompt", system_prompt,
        "--model", model or CLAUDE_MODEL,
        *_ISOLATION_ARGS,
    ]
    try:
        proc = subprocess.run(cmd, cwd=_WORKDIR, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AgentError(f"claude timed out after {timeout}s")
    except FileNotFoundError:
        raise AgentError(f"claude binary not found: {CLAUDE_BIN}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise AgentError(f"claude exited {proc.returncode}: {detail}")
    try:
        return _claude_result_text(proc.stdout)
    except json.JSONDecodeError:
        # a login prompt or rate-limit notice comes back as bare text
        raise AgentError(f"claude returned non-JSON: {proc.stdout.strip()[:300]}")


# --- ollama backend --------------------------------------------------------

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://192.168.178.98:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:latest")

_CLIENT = httpx.Client(
    timeout=httpx.Timeout(180.0, connect=10.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


def _ask_ollama(system_prompt, user_prompt, timeout, model):
    last_err = None
    for attempt in range(2):
        try:
            resp = _CLIENT.post(
                f"{OLLAMA_BASE}/api/chat",
                json={
                    "model": model or OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": "json",
                    "think": True,
                    "options": {"temperature": 0.7, "num_ctx": 8192,
                                "num_batch": 512, "num_gpu": -1},
                    "stream": False,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except httpx.TimeoutException:
            last_err = (f"Ollama timed out after {timeout}s "
                        f"(attempt {attempt + 1}/2)")
        except httpx.HTTPError as e:
            last_err = f"Ollama request failed: {e}"
        except (KeyError, json.JSONDecodeError) as e:
            last_err = f"Ollama bad response: {e}"
    raise AgentError(last_err)


_BACKENDS = {"claude_cli": _ask_claude_cli, "ollama": _ask_ollama}


def ask(system_prompt, user_prompt, timeout=120, model=None):
    """Run one agent turn on the configured backend, return raw text."""
    try:
        backend = _BACKENDS[BACKEND]
    except KeyError:
        raise AgentError(f"unknown AGENT_BACKEND {BACKEND!r}; "
                         f"expected one of {sorted(_BACKENDS)}")
    return backend(system_prompt, user_prompt, timeout, model)


def model_for(role):
    """Resolve the model a role should run on, for the active backend.

    Returns None when the backend has a single configured model, which every
    caller treats as "use the default".
    """
    if BACKEND != "claude_cli":
        return None
    return (CLAUDE_MODEL_FOREMAN if role in ("foreman", "judge", "bailiff")
            else CLAUDE_MODEL)


def _extract_json(text):
    """Extract the outermost JSON object from model output."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def ask_json_detailed(system_prompt, user_prompt, required_keys,
                      timeout=120, retries=3, model=None):
    """Like ask_json, but also returns the raw text and the prompts used.

    Returns (parsed_json, raw_text, system_prompt, user_prompt_used).
    """
    schema_hint = json.dumps({key: "..." for key in required_keys})
    prompt = user_prompt
    for _ in range(retries):
        text = ask(system_prompt, prompt, timeout=timeout, model=model)
        obj = _extract_json(text)
        if obj is not None and all(key in obj for key in required_keys):
            return obj, text, system_prompt, prompt
        prompt = (user_prompt +
                  "\n\nReturn ONLY valid JSON matching this schema." +
                  " No prose: " + schema_hint)
    raise MalformedReply(f"no valid JSON with keys {required_keys} "
                         f"after {retries} attempts")


def ask_json(system_prompt, user_prompt, required_keys,
             timeout=120, retries=3, model=None):
    """Call an agent and validate its JSON response."""
    obj, _, _, _ = ask_json_detailed(system_prompt, user_prompt, required_keys,
                                     timeout=timeout, retries=retries,
                                     model=model)
    return obj
