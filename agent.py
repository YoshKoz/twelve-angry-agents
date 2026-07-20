"""Stateless wrapper around Ollama API on the Windows desktop.

One call = one agent turn.

Uses the Windows desktop's Ollama instance over the LAN.

Override with environment variables:
    OLLAMA_HOST=http://192.168.178.98:11434
    OLLAMA_MODEL=qwen3:latest
"""

import json
import os

import httpx

# Windows desktop Ollama endpoint
OLLAMA_BASE = os.environ.get(
    "OLLAMA_HOST",
    "http://192.168.178.98:11434",
)

# qwen3:latest (thinking-capable) on desktop Ollama — this Ollama instance
# serializes requests (no real concurrency), so a fast reasoning model keeps
# per-call latency low (~5s warm). A full 12-juror ballot runs 12 calls
# back-to-back, so slower models (14B/35B at 20-25s each) push a single
# ballot past the 90s cap and stall the vote. Keeps judgment quality while
# letting ballots actually converge.
OLLAMA_MODEL = os.environ.get(
    "OLLAMA_MODEL",
    "qwen3:latest",
)

# Shared HTTP client with connection pooling for concurrent jurors
_CLIENT = httpx.Client(
    timeout=httpx.Timeout(180.0, connect=10.0),
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
    ),
)


class AgentError(Exception):
    """Ollama call failed after retries."""


class MalformedReply(AgentError):
    """Agent kept returning invalid JSON."""


def ask(system_prompt, user_prompt, timeout=120):
    """Call Ollama API and return message content."""

    last_err = None

    for attempt in range(2):
        try:
            resp = _CLIENT.post(
                f"{OLLAMA_BASE}/api/chat",
                json={
                    "model":
                    OLLAMA_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": user_prompt,
                        },
                    ],
                    "format":
                    "json",
                    # qwen3 reasons through a hidden <think> trace before
                    # replying — slower (~10x) but noticeably better
                    # judgment than a bare JSON answer.
                    "think":
                    True,
                    "options": {
                        "temperature": 0.7,
                        "num_ctx": 8192,
                        "num_batch": 512,
                        "num_gpu": -1,
                    },
                    "stream":
                    False,
                },
                timeout=timeout,
            )

            resp.raise_for_status()

            data = resp.json()
            return data["message"]["content"].strip()

        except httpx.TimeoutException:
            last_err = (f"Ollama timed out after {timeout}s "
                        f"(attempt {attempt + 1}/2)")

        except httpx.HTTPError as e:
            last_err = f"Ollama request failed: {e}"

        except (KeyError, json.JSONDecodeError) as e:
            last_err = f"Ollama bad response: {e}"

    raise AgentError(last_err)


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


def ask_json(
    system_prompt,
    user_prompt,
    required_keys,
    timeout=120,
    retries=3,
):
    """Call agent and validate JSON response."""

    schema_hint = json.dumps({key: "..." for key in required_keys})

    prompt = user_prompt

    for _ in range(retries):
        text = ask(
            system_prompt,
            prompt,
            timeout=timeout,
        )

        obj = _extract_json(text)

        if obj is not None and all(key in obj for key in required_keys):
            return obj

        prompt = (user_prompt +
                  "\n\nReturn ONLY valid JSON matching this schema." +
                  " No prose: " + schema_hint)

    raise MalformedReply(f"no valid JSON with keys {required_keys} "
                         f"after {retries} attempts")


def ask_json_detailed(
    system_prompt,
    user_prompt,
    required_keys,
    timeout=120,
    retries=3,
):
    """
    Like ask_json, but returns:

    (
        parsed_json,
        raw_text,
        system_prompt,
        user_prompt_used
    )
    """

    schema_hint = json.dumps({key: "..." for key in required_keys})

    prompt = user_prompt

    for _ in range(retries):
        text = ask(
            system_prompt,
            prompt,
            timeout=timeout,
        )

        obj = _extract_json(text)

        if obj is not None and all(key in obj for key in required_keys):
            return (
                obj,
                text,
                system_prompt,
                prompt,
            )

        prompt = (user_prompt +
                  "\n\nReturn ONLY valid JSON matching this schema." +
                  " No prose: " + schema_hint)

    raise MalformedReply(f"no valid JSON with keys {required_keys} "
                         f"after {retries} attempts")
