"""Shared Claude call used by both agents."""

from __future__ import annotations

import re

import anthropic


def call_claude(client: anthropic.Anthropic, model: str, system: str, user_message: str) -> str:
    """One call to Claude. Returns the text of the reply (thinking blocks dropped)."""
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_message}],
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # if a safety classifier declines, retry on a fallback model
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off at max_tokens")
    return "".join(block.text for block in response.content if block.type == "text")


def strip_fences(text: str) -> str:
    """Tolerate a reply wrapped in ```json fences, even though we ask for none."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip()
