"""Explicit LangGraph nodes around a model-directed, bounded research loop."""

import asyncio
import functools
import json
import operator
import re
import sys
from dataclasses import asdict, replace
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .contracts import Answer, ScopeDecision, Source, ToolResult
from .prompts import ANSWER_PROMPT, RESEARCH_PROMPT, SCOPE_PROMPT
from .runtime import BudgetExceeded, Runtime
from .tools.base import validate_query
from .trace import Trace, redact


class AgentState(TypedDict, total=False):
    question: str
    scope: dict
    messages: Annotated[list[dict], operator.add]
    pending: list[dict]
    evidence: dict[str, Source]
    seen: list[str]
    draft: dict
    validation_errors: list[str]
    repairs: int
    status: str
    stop_reason: str
    result: str
    counters: dict
    research_limited: bool


def validate_answer(draft: dict, evidence: dict[str, Source]) -> list[str]:
    try:
        answer = Answer.model_validate(draft)
    except ValidationError:
        return ["Use the required answer schema with nonempty items and citations."]
    known = {s.citation_id for s in evidence.values()}
    errors = []
    for item in answer.items:
        if any(c not in known for c in item.citations):
            errors.append("Only cite IDs in the retrieved evidence registry.")
        if re.search(r"https?://|\[\d+\]", item.text):
            errors.append("Do not embed URLs or inline citation markers in item text.")
    return list(dict.fromkeys(errors))


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
                if decision.decision != "in_scope" and not decision.message.strip():
                    raise ValueError("Missing clarification/refusal")
                data = redact(decision.model_dump())
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
        if not self.rt.can_research():
            return {"pending": [], "research_limited": True}
        additions = []
        if not state.get("messages"):
            additions = [{"role": "system", "content": RESEARCH_PROMPT},
                         {"role": "user", "content": json.dumps(state["scope"])}]
        budget_message = {"role": "user", "content": json.dumps({
            "application_budget": self.rt.counters(),
            "research_seconds_remaining": round(self.rt.remaining("research"), 1)})}
        try:
            response = await self.model.chat(state.get("messages", []) + additions + [budget_message],
                                             node="research", tools=[t.schema for t in self.tool_registry.values()],
                                             research=True)
        except TimeoutError:
            return {"pending": [], "research_limited": True}
        calls = response.get("tool_calls") or []
        # Operational summary comes from action metadata, not private reasoning text.
        self.trace.event("decision", "research", action="search" if calls else "finish",
                         decision_summary="model requested source searches" if calls else "model finished research",
                         tool_names=[c.get("function", {}).get("name", "") for c in calls])
        return {"messages": additions + [budget_message, response], "pending": calls}

    def route_research(self, state):
        if state.get("pending"):
            target = "tools"  # Node reports skipped responses if budget expired after the model call.
        else:
            target = "synthesize" if state.get("evidence") else "render"
        return self.transition("research", target, "tool request or research completion")

    async def tools(self, state):
        registry = dict(state.get("evidence", {}))
        seen, messages = list(state.get("seen", [])), []
        limited = state.get("research_limited", False)
        for index, call in enumerate(state.get("pending", [])):
            function = call.get("function", {})
            name, arguments = function.get("name", ""), function.get("arguments", {})
            call_id = str(call.get("id") or f"{self.rt.model_calls}:{index}")
            started = self.rt.elapsed
            self.trace.event("tool_start", "tools", tool=name, arguments=arguments, call_id=call_id)
            result = ToolResult("error", error="Invalid tool or arguments")
            try:
                if name not in self.tool_registry or not isinstance(arguments, dict):
                    raise ValueError("Unknown tool or malformed arguments")
                if set(arguments) - {"query", "max_results"}:
                    raise ValueError("Unexpected tool arguments")
                query, count = arguments.get("query"), arguments.get("max_results", 3)
                validate_query(query, count)
                key = name + ":" + " ".join(query.casefold().split())
                if key in seen:
                    result = ToolResult("skipped", error="Duplicate search. Use existing results or reformulate.")
                elif not self.rt.can_research():
                    limited = True
                    result = ToolResult("skipped", error="Research budget reached; synthesize from existing evidence.")
                else:
                    seen.append(key)
                    self.rt.tool_calls += 1
                    timeout = min(8, self.rt.remaining("research"))
                    result = await self.rt.bounded(self.tool_registry[name].search(query, count, timeout), timeout)
                    normalized = []
                    for source in result.sources:
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
                        self.rt.evidence_chars += len(text)
                        normalized.append(source)
                    result = replace(result, sources=normalized,
                                     status="ok" if normalized else result.status)
            except ValueError as exc:
                result = ToolResult("error", error=str(exc))
            except (TimeoutError, BudgetExceeded):
                result = ToolResult("error", error="Tool execution reached its timeout or budget", retryable=True)
            data = redact(result.as_dict())
            self.trace.event("tool_end", "tools", tool=name, arguments=arguments,
                             call_id=call_id, result=data, status=data["status"],
                             duration_ms=round((self.rt.elapsed - started) * 1000, 2))
            message = {"role": "tool", "tool_name": name, "content": json.dumps(data)}
            if call.get("id"):
                message["tool_call_id"] = call["id"]
            messages.append(message)
        return {"messages": messages, "pending": [], "evidence": registry,
                "seen": seen, "research_limited": limited or not self.rt.can_research()}

    def route_tools(self, state):
        if self.rt.can_research() and not state.get("research_limited"):
            target = "research"
        else:
            target = "synthesize" if state.get("evidence") else "render"
        return self.transition("tools", target, "continue research or enforce budget")

    async def synthesize(self, state):
        context = {"scope": state["scope"],
                   "evidence": [asdict(s) for s in state.get("evidence", {}).values()],
                   "repair_errors": state.get("validation_errors", [])}
        response = await self.model.chat([
            {"role": "system", "content": ANSWER_PROMPT},
            {"role": "user", "content": json.dumps(context)}],
            node="synthesize", schema=Answer.model_json_schema())
        try:
            draft = json.loads(response.get("content", ""))
        except (ValueError, TypeError):
            draft = {}
        return {"draft": draft, "repairs": state.get("repairs", 0) + bool(state.get("validation_errors"))}

    async def validate(self, state):
        errors = validate_answer(state.get("draft", {}), state.get("evidence", {}))
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
            result = scope.get("message", "Please clarify your research question.")
            status = "out_of_scope" if scope.get("decision") == "out_of_scope" else "clarification_needed"
        elif not state.get("evidence"):
            status = "insufficient_evidence"
            result = "I could not retrieve enough evidence to answer this question. No model-memory answer was substituted."
        elif state.get("validation_errors") or not state.get("draft"):
            status = "invalid_output"
            result = "I retrieved sources but could not produce a valid cited answer within the available attempts."
        else:
            result = render_answer(state["draft"], state["evidence"])
            status = "answered"
        if scope.get("interpretation"):
            result = "Interpretation: " + scope["interpretation"] + "\n\n" + result
        if scope.get("unsupported"):
            result += "\n\nScope limitation: " + scope["unsupported"]
            if status == "answered":
                status = "partial_answer"
        if state.get("research_limited") and status in {"answered", "partial_answer"}:
            result += "\n\nResearch limitation: search time or call limits were reached; this answer uses the evidence retrieved so far."
            status = "partial_answer"
        self.trace.event("transition", "render", to="END", decision_summary=status)
        return {"result": redact(result), "status": status, "stop_reason": status}

    async def run(self, question: str) -> AgentState:
        self.trace.event("transition", "START", to="scope", decision_summary="begin scope check")
        return await self.graph.ainvoke({"question": question, "messages": [], "evidence": {},
                                        "seen": [], "repairs": 0, "research_limited": False},
                                       config={"recursion_limit": 40})
