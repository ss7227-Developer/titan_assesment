"""Bounded model input built from unique evidence, never full conversation replay."""

import json
from datetime import datetime, timezone

MAX_CONTEXT_CHARS = 16000
MAX_MODEL_INPUT_CHARS = 24000
MAX_RECENT_SEARCHES = 4


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded_draft(value):
    serialized = encode(value)
    if len(serialized) <= 4000:
        return value
    # Normal model drafts fit; adversarial/oversized outputs must not break the cap.
    return {"truncated": True, "serialized_prefix": serialized[:3500]}


def make_context(state: dict, *, repair: bool = False) -> str:
    scope = state["scope"]
    context = {"original_question": state["question"][:2000],
               "as_of_date": datetime.now(timezone.utc).date().isoformat(),
               "research_question": scope["research_question"][:2000],
               "evidence_needs": scope.get("evidence_needs", []),
               "has_unsupported_part": bool(scope.get("unsupported")),
               "recent_searches": state.get("recent_searches", [])[-MAX_RECENT_SEARCHES:],
               "budget": state.get("counters", {}), "evidence": []}
    if state.get("assessment"):
        context["completion_assessment"] = state["assessment"]
    if repair:
        context["rejected_answer"] = bounded_draft(state.get("draft"))
        context["validation_errors"] = state.get("validation_errors", [])[:8]
    sources = list(state.get("evidence", {}).values())
    full_texts = []
    for source in sources:
        # Reference rendering still uses the original metadata, not these input caps.
        item = {"citation_id": source.citation_id, "provider": source.provider,
                "source_id": source.source_id[:100], "title": source.title[:150],
                "published": source.published[:32], "text": "", "truncated": True}
        context["evidence"].append(item)
        full_texts.append(source.text)
    room = max(0, MAX_CONTEXT_CHARS - len(encode(context)))
    allowance = min(3000, room // max(1, len(full_texts)))
    while True:
        for item, text, source in zip(context["evidence"], full_texts, sources):
            item["text"] = text[:allowance]
            item["truncated"] = source.truncated or len(text) > allowance
        result = encode(context)
        if len(result) <= MAX_CONTEXT_CHARS:
            return result
        if allowance == 0:
            raise ValueError("Context metadata exceeds the configured input budget")
        allowance //= 2
