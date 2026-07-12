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
