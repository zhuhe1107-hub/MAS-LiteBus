"""Lenient JSON extraction from LLM output.

Open-weight models commonly wrap their JSON in markdown code fences, prepend
explanatory text, or emit a single trailing comma. This module tries the most
permissive interpretations first so the Agent can still proceed when the
model deviates from the strict prompt instruction.
"""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```", re.MULTILINE)
_FIRST_OBJ = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def extract_json(text: str) -> dict[str, Any]:
    """Try several strategies to recover a JSON object from raw LLM text.

    Raises ValueError if no usable JSON is found. Callers should treat the
    failure as a parse error and fall back to template behaviour while
    incrementing `metrics.llm_parse_failures`.
    """
    if not text:
        raise ValueError("empty LLM response")

    # 1) Direct: the whole thing is JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) Strip a markdown fence if present.
    fence_match = _FENCE.search(text)
    if fence_match:
        body = fence_match.group(1).strip()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            pass

    # 3) First {...} block in the document.
    obj_match = _FIRST_OBJ.search(text)
    if obj_match:
        candidate = obj_match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 4) Drop a trailing comma before } or ] (common Qwen tic).
            stripped = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

    raise ValueError(f"could not extract JSON from LLM text: {text[:200]!r}")
