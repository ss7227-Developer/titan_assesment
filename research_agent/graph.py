"""Explicit LangGraph nodes around a model-directed, bounded research loop."""

import functools
import json
import re
import sys
from copy import deepcopy
from dataclasses import replace
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .contracts import Answer, ResearchAssessment, ScopeDecision, Source, ToolResult
from .assessment import assessment_errors, supported_ids
from .context import MAX_RECENT_SEARCHES, make_context
from .prompts import ANSWER_PROMPT, RESEARCH_PROMPT, SCOPE_PROMPT, SEARCH_START_PROMPT
from .runtime import BudgetExceeded, Runtime
from .tools.base import query_cache_key, validate_query
from .trace import Trace, redact


class AgentState(TypedDict, total=False):
    question: str
    scope: dict
    recent_searches: list[dict]
    pending: list[dict]
    evidence: dict[str, Source]
    cache: dict[str, ToolResult]
    draft: object
    validation_errors: list[dict]
    repairs: int
    status: str
    stop_reason: str
    result: str
    counters: dict
    research_limited: bool
    research_stop: str
    assessment: dict
    assessment_repairs: int


def validate_answer(draft: object, evidence: dict[str, Source], assessment: dict | None = None) -> list[dict]:
    try:
        answer = Answer.model_validate(draft)
    except ValidationError as exc:
        return [{"path": ".".join(str(p) for p in e["loc"]), "code": e["type"],
                 "message": e["msg"][:160]} for e in exc.errors(include_input=False)[:8]]
    known = {s.citation_id for s in evidence.values()}
    by_id = {s.citation_id: s for s in evidence.values()}
    errors = []
    assessed_ids = supported_ids(assessment) if assessment else None
    if not answer.items and (not assessment or any(assessed_ids.values())):
        errors.append({"path": "items", "code": "empty_answer", "message": "Answer the supported parts with citations."})
    for index, item in enumerate(answer.items):
        unknown = [c for c in item.citations if c not in known]
        if unknown:
            errors.append({"path": f"items.{index}.citations", "code": "unknown_citation",
                           "unknown_ids": unknown, "allowed_ids": sorted(known),
                           "message": "Only cite IDs in the retrieved evidence registry."})
        if assessed_ids is not None and (not item.citations or not set(item.citations) <= assessed_ids.get(item.part_index, set())):
            errors.append({"path": f"items.{index}.citations", "code": "unassessed_support",
                           "message": "Use only supporting IDs from this item's assessed part_index."})
        if item.provenance == "retrieved":
            # A reported numeric observation must occur in the cited content or
            # its paper metadata. This is an additional check, not entailment proof.
            def numbers(text):
                return {n.replace(",", "") for n in re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text)}
            source_text = " ".join(
                by_id[c].text + " " + by_id[c].published + " " + by_id[c].source_id
                for c in item.citations if c in by_id)
            missing_numbers = numbers(item.text) - numbers(source_text)
            if missing_numbers:
                errors.append({"path": f"items.{index}.text", "code": "unsupported_numeric_claim",
                               "message": "Reported numbers must occur in the cited evidence; omit unsupported measurements.",
                               "unsupported_numbers": sorted(missing_numbers)})
        if not item.text.strip():
            errors.append({"path": f"items.{index}.text", "code": "blank_text",
                           "message": "Answer text must contain non-whitespace characters."})
        if re.search(r"https?://|\[\d+\]", item.text):
            errors.append({"path": f"items.{index}.text", "code": "embedded_reference",
                           "message": "Do not embed URLs or inline citation markers in item text."})
    return errors


def render_answer(draft: dict, evidence: dict[str, Source]) -> str:
    answer = Answer.model_validate(draft)
    lines, used = [], set()
    for item in answer.items:
        label = "Retrieved evidence" if item.provenance == "retrieved" else "Model inference"
        ids = list(dict.fromkeys(item.citations))
        lines.append(f"{label}: {redact(item.text.strip())} " + " ".join(f"[{i}]" for i in ids))
        used.update(ids)
    lines.append("\nSources:")
    for source in sorted(evidence.values(), key=lambda s: s.citation_id):
        if source.citation_id not in used:
            continue
        if source.provider == "wikipedia":
            detail = f"Wikipedia article extract; retrieved {source.retrieved_at[:10]}"
            if source.revision:
                detail += f"; revision {source.revision}"
        else:
            authors = ", ".join(source.authors[:3])
            if len(source.authors) > 3:
                authors += ", et al."
            detail = f"arXiv paper abstract; ID {source.source_id}; {source.published[:4]}"
            if authors:
                detail += f"; {authors}"
        lines.append(f"[{source.citation_id}] {source.title} — {detail}\n    {source.url}")
    return "\n\n".join(lines)


class ResearchGraph:
    def __init__(self, model, tools, runtime: Runtime, trace: Trace, debug: bool = False):
        self.model = model
        self.tool_registry = {tool.name: tool for tool in tools}
        self.rt, self.trace, self.debug = runtime, trace, debug
        builder = StateGraph(AgentState)
        for name in ("scope", "research", "tools", "synthesize", "validate", "render"):
            builder.add_node(name, self.observed(name, getattr(self, name)))
        builder.add_edge(START, "scope")
        builder.add_conditional_edges("scope", self.route_scope, ["research", "render"])
        builder.add_conditional_edges("research", self.route_research, ["tools", "synthesize", "render"])
        builder.add_conditional_edges("tools", self.route_tools, ["research", "synthesize", "render"])
        builder.add_edge("synthesize", "validate")
        builder.add_conditional_edges("validate", self.route_validate, ["synthesize", "render"])
        builder.add_edge("render", END)
        self.graph = builder.compile()

    def observed(self, name, function):
        @functools.wraps(function)
        async def node(state):
            self.trace.event("node_start", name, status=state.get("status", "running"))
            if self.debug:
                print(f"[{name}] {self.rt.elapsed:.2f}s", file=sys.stderr)
            result = await function(state)
            result["counters"] = self.rt.counters()
            self.trace.event("node_end", name, status=result.get("status", "running"))
            return result
        return node

    def transition(self, origin, target, reason):
        self.trace.event("transition", origin, to=target, decision_summary=reason)
        return target

    async def scope(self, state):
        messages = [{"role": "system", "content": SCOPE_PROMPT},
                    {"role": "user", "content": json.dumps({"question": state["question"]})}]
        for attempt in range(2):
            response = await self.model.chat(messages, node="scope",
                                             schema=ScopeDecision.model_json_schema(), research=True)
            try:
                decision = ScopeDecision.model_validate_json(response.get("content", ""))
                if decision.decision == "in_scope" and not decision.research_question.strip():
                    raise ValueError("Missing supported research question")
                if decision.decision == "needs_clarification" and not decision.message.strip():
                    raise ValueError("Missing clarification question")
                data = redact(decision.model_dump())
                # A generic caveat is not an unsupported task. Require an anchored
                # span from the original request before adding a mixed-scope notice.
                unsupported = " ".join(data["unsupported"].casefold().split())
                original = " ".join(state["question"].casefold().split())
                supported = " ".join(data["research_question"].casefold().split())
                if unsupported and (unsupported not in original or unsupported == original
                                    or unsupported in supported):
                    data["unsupported"] = ""
                    self.trace.event("scope_notice_discarded", "scope",
                                     reason="unsupported span is ungrounded or overlaps the supported question")
                # Enforce the agreed mixed-request rule when the model identifies
                # distinct supported/unsupported spans but contradicts its own routing.
                if (data["decision"] == "out_of_scope" and data["unsupported"]
                        and supported and supported in original):
                    data["decision"] = "in_scope"
                    self.trace.event("scope_decision_normalized", "scope",
                                     reason="answer the identified supported portion of a mixed request")
                self.trace.event("decision", "scope", action=data["decision"],
                                 decision_summary=data["interpretation"] or data["decision"],
                                 supported_question=data["research_question"],
                                 unsupported=data["unsupported"])
                return {"scope": data}
            except (ValidationError, ValueError):
                if attempt or not self.rt.can_research():
                    return {"status": "invalid_output", "result": "I could not classify this question reliably. Please rephrase it."}
                messages.append({"role": "user", "content": "Return valid scope JSON with the required nonempty question or clarification/refusal."})
        raise AssertionError("unreachable")

    def route_scope(self, state):
        accepted = state.get("scope", {}).get("decision") == "in_scope" and not state.get("result")
        return self.transition("scope", "research" if accepted else "render", "scope decision")

    async def research(self, state):
        errors, rejected = [], None
        repairs = state.get("assessment_repairs", 0)
        initial_response = None
        if not state.get("recent_searches") and not state.get("assessment") and self.rt.can_research():
            try:
                initial_response = await self.model.chat([
                    {"role": "system", "content": SEARCH_START_PROMPT},
                    {"role": "user", "content": make_context(state)}], node="research",
                    tools=[t.schema for t in self.tool_registry.values()], research=True)
            except (TimeoutError, BudgetExceeded):
                return self.limited_assessment(state, "The initial search decision reached its budget.", repairs)
            if initial_response.get("tool_calls"):
                self.trace.event("decision", "research", action="search",
                                 decision_summary="initial model-selected search; completion assessment will follow the results")
                return {"pending": initial_response["tool_calls"]}
        while True:
            # Reserve one model call for synthesis. Budget stops still get an
            # assessment if possible, using the existing synthesis-time reserve.
            if self.rt.model_calls >= self.rt.max_models - 1 or self.rt.remaining("model") <= 0.1:
                return self.limited_assessment(state, "The remaining budget did not allow a fresh evidence assessment.", repairs)
            forced = not self.rt.can_research()
            if forced and not state.get("evidence"):
                return self.limited_assessment(state, "Searches stopped without usable evidence.", repairs)
            context = {**state, "counters": {**self.rt.counters(),
                       "research_seconds_remaining": round(self.rt.remaining("research"), 1)}}
            payload = json.loads(make_context(context))
            payload["available_tools"] = [t.schema for t in self.tool_registry.values()]
            payload["search_allowed"] = not forced
            payload["assessment_errors"] = errors
            if rejected is not None:
                from .context import bounded_draft
                payload["rejected_assessment"] = bounded_draft(rejected)
            assessment = None
            try:
                if initial_response is not None:
                    response, initial_response = initial_response, None
                else:
                    response = await self.model.chat([
                        {"role": "system", "content": RESEARCH_PROMPT},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                        node="research", schema=ResearchAssessment.model_json_schema(),
                        research=not forced, max_tokens=1400)
                rejected = response.get("content", "")
                assessment = ResearchAssessment.model_validate_json(rejected)
                previous = state.get("assessment", {}).get("parts", [])
                errors = assessment_errors(assessment, state.get("evidence", {}),
                                           list({p["kind"] for p in previous}))
                if previous and [(p.question_part, p.kind) for p in assessment.parts] != [
                        (p["question_part"], p["kind"]) for p in previous]:
                    errors.append("Preserve the existing question parts and kinds; update their evidence/gaps, not the requirements.")
                if not errors:
                    data = assessment.model_dump()
                    if data["action"] == "search" and (forced or not self.rt.can_research()):
                        data.update(action="limited", searches=[], reason="Research budget/no-progress limit reached before the proposed follow-up.")
                    self.trace.event("completion_assessment", "research", assessment=data,
                                     accepted=True, forced_stop=forced)
                    calls = [{"function": {"name": s["tool"], "arguments": {
                        "query": s["query"], "max_results": s["max_results"]}}}
                        for s in data["searches"]]
                    return {"assessment": data, "assessment_repairs": repairs, "pending": calls,
                            "research_limited": forced,
                            "research_stop": (self.rt.research_stop_reason() or data["reason"]) if not calls else ""}
            except ValidationError as exc:
                errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False)[:8]]
            except (TimeoutError, BudgetExceeded):
                return self.limited_assessment(state, "The evidence assessment reached its time/call budget.", repairs)
            from .context import bounded_draft
            self.trace.event("assessment_rejected", "research", errors=errors,
                             rejected_assessment=bounded_draft(rejected))
            if repairs >= 1:
                return self.limited_assessment(state, "The completion assessment remained inconsistent after one repair; only mechanically valid support is used.",
                                               repairs, candidate=assessment)
            repairs += 1

    def limited_assessment(self, state, reason, repairs, candidate=None):
        # Preserve only a previous accepted assessment. Newly retrieved material
        # that was never assessed is not automatically considered supported.
        parts = deepcopy(state.get("assessment", {}).get("parts", []))
        if candidate is not None:
            # An inconsistent stop decision need not discard every valid source
            # mapping. Keep only support that independently passes the general
            # ID/provider/date checks; never promote an invalid/unknown reference.
            recovered = []
            for part in candidate.parts:
                valid = []
                for support in part.supports:
                    probe = part.model_copy(update={"supports": [support], "missing": "Completeness is not verified."})
                    check = ResearchAssessment(parts=[probe], action="limited", searches=[], reason=reason)
                    if not assessment_errors(check, state.get("evidence", {}), []):
                        valid.append(support)
                recovered.append(part.model_copy(update={"supports": valid,
                    "missing": part.missing or "Completeness could not be verified from the assessment."}).model_dump())
            # Do not erase previously identified requirements during recovery.
            names = {p["question_part"] for p in recovered}
            parts = (recovered + [p for p in parts if p["question_part"] not in names])[:5]
        if not parts:
            parts = [{"question_part": state["scope"]["research_question"][:120],
                      "kind": "fact", "supports": [], "missing": "Evidence has not been assessed reliably.",
                      "published_after": ""}]
        data = {"action": "limited", "parts": parts, "searches": [], "reason": reason}
        self.trace.event("completion_assessment", "research", assessment=data,
                         accepted=True, forced_stop=True, origin="application_fallback")
        return {"assessment": data, "pending": [], "assessment_repairs": repairs,
                "research_limited": True, "research_stop": reason}

    def route_research(self, state):
        if state.get("pending"):
            target = "tools"  # Node reports skipped responses if budget expired after the model call.
        else:
            target = "synthesize" if any(supported_ids(state.get("assessment", {})).values()) else "render"
        return self.transition("research", target, "tool request or research completion")

    async def tools(self, state):
        registry = dict(state.get("evidence", {}))
        cache = dict(state.get("cache", {}))
        recent = list(state.get("recent_searches", []))
        limited = state.get("research_limited", False)
        for index, call in enumerate(state.get("pending", [])):
            function = call.get("function", {})
            name, arguments = function.get("name", ""), function.get("arguments", {})
            call_id = str(call.get("id") or f"{self.rt.model_calls}:{index}")
            started = self.rt.elapsed
            self.trace.event("tool_start", "tools", tool=name, arguments=arguments, call_id=call_id)
            result = ToolResult("error", error="Invalid tool or arguments")
            cache_hit, new_ids = False, []
            effective_arguments = arguments
            try:
                if name not in self.tool_registry or not isinstance(arguments, dict):
                    raise ValueError("Unknown tool or malformed arguments")
                if set(arguments) - {"query", "max_results"}:
                    raise ValueError("Unexpected tool arguments")
                query, count = arguments.get("query"), arguments.get("max_results", 3)
                requested_count = count
                if type(count) is int and count > 3:
                    count = 3
                    effective_arguments = {**arguments, "max_results": count}
                    self.trace.event("tool_arguments_normalized", "tools", tool=name, call_id=call_id,
                                     requested_arguments=arguments, effective_arguments=effective_arguments)
                validate_query(query, count)
                tool = self.tool_registry[name]
                key = name + ":" + getattr(tool, "cache_key", query_cache_key)(query, count)
                if not self.rt.can_research():
                    limited = True
                    result = ToolResult("skipped", error="Research budget reached; synthesize from existing evidence.")
                else:
                    if key in cache:
                        cache_hit = True
                        self.rt.cache_hits += 1
                        result = deepcopy(cache[key])
                    else:
                        self.rt.tool_calls += 1
                        timeout = min(8, self.rt.remaining("research"))
                        result = await self.rt.bounded(tool.search(query, count, timeout), timeout)
                        # Cache successful and empty results for this invocation, not transient errors.
                        if result.status in {"ok", "empty"}:
                            # A larger result set also satisfies smaller result-limit requests.
                            for size in range(1, count + 1):
                                subset_key = name + ":" + getattr(tool, "cache_key", query_cache_key)(query, size)
                                cache[subset_key] = deepcopy(replace(result, sources=result.sources[:size]))
                        if result.status == "ok" and result.sources:
                            self.rt.successful_searches += 1
                    normalized = []
                    for source in result.sources[:count]:
                        if source.identity in registry:
                            normalized.append(registry[source.identity])
                            continue
                        room = 18000 - self.rt.evidence_chars
                        if room <= 0:
                            limited = True
                            break
                        text = source.text[:min(3000, room)]
                        source = replace(source, text=text, citation_id=len(registry) + 1,
                                         truncated=source.truncated or len(text) < len(source.text))
                        registry[source.identity] = source
                        new_ids.append(source.citation_id)
                        self.rt.evidence_chars += len(text)
                        normalized.append(source)
                    result = replace(result, sources=normalized,
                                     status="ok" if normalized else result.status)
            except ValueError as exc:
                result = ToolResult("error", error=str(exc))
            except (TimeoutError, BudgetExceeded):
                result = ToolResult("error", error="Tool execution reached its timeout or budget", retryable=True)
            self.rt.no_progress = 0 if new_ids else self.rt.no_progress + 1
            data = redact(result.as_dict())
            self.trace.event("tool_end", "tools", tool=name, arguments=arguments,
                             effective_arguments=effective_arguments,
                             call_id=call_id, result=data, status=data["status"], cache_hit=cache_hit,
                             new_source_ids=new_ids,
                             duration_ms=round((self.rt.elapsed - started) * 1000, 2))
            recent.append({"tool": name[:80], "query": str(arguments.get("query", ""))[:200]
                           if isinstance(arguments, dict) else "", "status": result.status,
                           "citation_ids": [s.citation_id for s in result.sources],
                           "new_source_ids": new_ids, "cache_hit": cache_hit,
                           "error": result.error[:200]})
        stop = self.rt.research_stop_reason()
        if stop:
            self.trace.event("research_stop", "tools", reason=stop)
        return {"recent_searches": recent[-MAX_RECENT_SEARCHES:], "pending": [], "evidence": registry,
                "cache": cache, "research_stop": stop,
                "research_limited": limited or stop == "research_budget"}

    def route_tools(self, state):
        return self.transition("tools", "research", "assess the returned evidence before continuing or stopping")

    async def synthesize(self, state):
        support = supported_ids(state.get("assessment", {}))
        allowed = set().union(*support.values()) if support else set()
        # Synthesis sees only assessed sources, not every broadly related hit.
        synthesis_state = {**state, "recent_searches": [], "evidence": {
            key: source for key, source in state.get("evidence", {}).items()
            if source.citation_id in allowed}}
        context = make_context(synthesis_state, repair=bool(state.get("validation_errors")))
        response = await self.model.chat([
            {"role": "system", "content": ANSWER_PROMPT},
            {"role": "user", "content": context}],
            node="synthesize", schema=Answer.model_json_schema())
        try:
            draft = json.loads(response.get("content", ""))
        except (ValueError, TypeError):
            # Preserve the actual rejected output for the repair prompt as well.
            draft = response.get("content", "")
        if isinstance(draft, dict) and isinstance(draft.get("items"), list):
            for item in draft["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("citations"), list):
                    continue
                ids = item["citations"]
                if not ids or any(type(i) is not int for i in ids):
                    continue
                candidates = [index for index, covered in support.items() if set(ids) <= covered]
                if len(candidates) == 1 and item.get("part_index", 0) != candidates[0]:
                    item["part_index"] = candidates[0]
                    self.trace.event("answer_part_index_normalized", "synthesize", part_index=candidates[0])
        return {"draft": draft, "repairs": state.get("repairs", 0) + bool(state.get("validation_errors"))}

    async def validate(self, state):
        errors = validate_answer(state.get("draft", {}), state.get("evidence", {}), state.get("assessment"))
        self.trace.event("validation", "validate", valid=not errors, errors=errors)
        return {"validation_errors": errors}

    def route_validate(self, state):
        repair = (state.get("validation_errors") and state.get("repairs", 0) < 1
                  and self.rt.remaining("model") > 1 and self.rt.model_calls < self.rt.max_models)
        return self.transition("validate", "synthesize" if repair else "render",
                               "repair invalid answer" if repair else "validation complete")

    async def render(self, state):
        scope = state.get("scope", {})
        status, result = state.get("status", ""), state.get("result", "")
        if result:
            pass
        elif scope.get("decision") != "in_scope":
            status = "out_of_scope" if scope.get("decision") == "out_of_scope" else "clarification_needed"
            result = ("I can help with factual research, but this request is outside that scope."
                      if status == "out_of_scope" else
                      "Clarification needed: " + scope.get("message", "Please clarify your research question."))
        elif not state.get("evidence") or not any(supported_ids(state.get("assessment", {})).values()):
            status = "insufficient_evidence"
            result = "I could not retrieve enough evidence to answer this question. No model-memory answer was substituted."
        elif state.get("validation_errors") or not state.get("draft"):
            status = "invalid_output"
            result = "I retrieved sources but could not produce a valid cited answer within the available attempts."
        else:
            result = render_answer(state["draft"], state["evidence"])
            status = "answered"
        if scope.get("unsupported"):
            # Never append unchecked model prose to an otherwise validated answer.
            result += "\n\nScope limitation: Only the factual research portion is supported; other requested assistance is outside this agent's scope."
            if status == "answered":
                status = "partial_answer"
        assessment = state.get("assessment", {})
        gaps = [p["question_part"] + ": " + p["missing"] for p in assessment.get("parts", [])
                if p["missing"]]
        if gaps:
            result += "\n\nNot established from retrieved evidence:\n" + "\n".join("- " + gap for gap in gaps)
        if assessment.get("action") == "limited":
            result += "\n\nResearch limitation: " + assessment["reason"]
            if status == "answered":
                status = "partial_answer"
        if state.get("research_limited") and assessment.get("action") != "limited" and status in {"answered", "partial_answer"}:
            result += "\n\nResearch limitation: search time or call limits were reached; this answer uses the evidence retrieved so far."
            status = "partial_answer"
        self.trace.event("transition", "render", to="END", decision_summary=status)
        return {"result": redact(result), "status": status, "stop_reason": status}

    async def run(self, question: str) -> AgentState:
        self.trace.event("question", original_question=question)
        self.trace.event("transition", "START", to="scope", decision_summary="begin scope check")
        return await self.graph.ainvoke({"question": question, "recent_searches": [], "evidence": {},
                                        "cache": {}, "repairs": 0, "research_limited": False},
                                       config={"recursion_limit": 40})
