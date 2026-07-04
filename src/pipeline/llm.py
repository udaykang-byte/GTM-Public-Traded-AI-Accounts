"""LLM provider abstraction for scoring reasoning.

v1 (this version): scoring runs through Claude Code Haiku subagents via the
file-based packet handoff (see scoring.py + /score skill). Zero API cost.

Production (v2): OpenRouterProvider consumes the SAME packets via API —
`score --provider openrouter` — no other pipeline changes.
"""
from __future__ import annotations

import json
from typing import Protocol

import httpx

from pipeline.config import env
from pipeline.models import ScoreVerdict


class LLMProvider(Protocol):
    def score_packet(self, packet: dict) -> ScoreVerdict: ...


class ClaudeCodeProvider:
    """Placeholder provider: v1 scoring is orchestrated by the /score skill
    (Claude Code spawns Haiku subagents that write result files). Calling this
    directly just explains the flow."""

    def score_packet(self, packet: dict) -> ScoreVerdict:
        raise RuntimeError(
            "v1 scoring is file-based: run `score --prepare`, then use the /score "
            "skill in Claude Code (Haiku subagents write results), then `score --commit`."
        )


class OpenRouterProvider:
    """Production scoring via OpenRouter. Same packet in, same verdict out."""

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str | None = None):
        self.api_key = env("OPENROUTER_API_KEY")
        if not self.api_key:
            raise SystemExit("OPENROUTER_API_KEY not set — OpenRouter scoring is a v2/production path.")
        self.model = model or env("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    def score_packet(self, packet: dict) -> ScoreVerdict:
        prompt = (
            "You are a B2B account scorer for an AI-services company.\n\n"
            f"{packet['rubric']}\n\n"
            f"COMPANY: {json.dumps(packet['company'])}\n\n"
            f"SIGNALS: {json.dumps(packet['signals'], default=str)}\n\n"
            f"BASE SCORE (deterministic): {json.dumps(packet['base_score'])}\n\n"
            f"SERVICES CATALOG: {json.dumps(packet['services_catalog'])}\n\n"
            "Respond with ONLY a JSON object matching this schema (no markdown fences):\n"
            f"{json.dumps(packet['output_schema'])}"
        )
        resp = httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return ScoreVerdict.model_validate_json(content)


def get_provider(name: str = "claude-code") -> LLMProvider:
    if name == "openrouter":
        return OpenRouterProvider()
    return ClaudeCodeProvider()
