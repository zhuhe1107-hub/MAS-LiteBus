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
_CODE_FENCE = re.compile(r"```(?:python|py)?\s*([\s\S]*?)```", re.MULTILINE)
_CODE_DELIMITER = re.compile(r"^[ \t]*---+(?:\s*CODE\s*---+)?[ \t]*$", re.MULTILINE)


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


def _find_first_balanced_object(text: str) -> tuple[int, int] | None:
    """Return (start, end_exclusive) of the first top-level {...} block.

    Respects nested braces and string literals so the JSON header is split
    cleanly from any Python code that follows.
    """
    depth = 0
    in_string = False
    escape = False
    start = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                return start, i + 1
    return None


def extract_json_then_code(text: str) -> tuple[dict[str, object], str]:
    """Parse Executor responses shaped as `{...header...}\\n---CODE---\\n<python>`.

    Tolerant of variants seen with open-weight models: missing/different
    delimiters, markdown code fences around the Python body, JSON wrapped
    in ```json fences. Returns (header_dict, raw_python_code). Raises
    ValueError if neither a JSON header nor recognisable code can be found.
    """
    if not text:
        raise ValueError("empty LLM response")

    body = text.strip()
    # If the model wrapped the whole reply in a fence, peel it once.
    outer_fence = _FENCE.search(body)
    if outer_fence and outer_fence.start() == 0:
        body = outer_fence.group(1).strip()

    span = _find_first_balanced_object(body)
    if span is None:
        # Fallback: treat the entire reply as code, infer artifact_kind later.
        code_fence = _CODE_FENCE.search(body)
        code = code_fence.group(1).strip() if code_fence else body.strip()
        if not code:
            raise ValueError(f"no JSON header and no code body in: {text[:200]!r}")
        return {"artifact_kind": "generic_checklist"}, code

    header_text = body[span[0] : span[1]]
    try:
        header = json.loads(header_text)
    except json.JSONDecodeError:
        try:
            header = json.loads(re.sub(r",(\s*[}\]])", r"\1", header_text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"bad JSON header: {header_text[:200]!r}") from exc

    tail = body[span[1] :].lstrip()
    # Drop a leading delimiter line if present.
    tail = _CODE_DELIMITER.sub("", tail, count=1).lstrip()
    # Drop wrapping markdown code fences around the code itself.
    code_fence = _CODE_FENCE.search(tail)
    if code_fence:
        code = code_fence.group(1).strip()
    else:
        code = tail.strip()

    if not isinstance(header, dict):
        raise ValueError(f"JSON header is not an object: {header!r}")
    return header, code
