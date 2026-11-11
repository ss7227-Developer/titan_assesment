"""Mechanical evidence requirements, not a semantic truth oracle or question lookup."""

from datetime import date

from .contracts import ResearchAssessment, Source


def normalize(text: str) -> str:
    return " ".join(text.split())


def assessment_errors(assessment: ResearchAssessment, evidence: dict[str, Source],
                      needs: list[str]) -> list[str]:
    by_id = {s.citation_id: s for s in evidence.values()}
    errors = []
    kinds = [part.kind for part in assessment.parts]
    for need in needs if assessment.action == "finish" else []:
        if need not in kinds and not (need == "comparison" and kinds.count("dated_measurement") >= 2):
            errors.append(f"Include the requested {need} evidence requirement as an assessment part.")
    if assessment.action == "finish" and ("comparison" in needs or "comparison" in kinds) and len({p.question_part for p in assessment.parts}) < 2:
        errors.append("Assess each compared subject/period separately; at least two comparison parts are needed.")
    for index, part in enumerate(assessment.parts):
        prefix = f"parts.{index}"
        if not part.supports and not part.missing.strip():
            errors.append(f"{prefix}: no evidence supports this part; identify what is missing.")
        cutoff = None
        if part.published_after:
            try:
                cutoff = date.fromisoformat(part.published_after)
            except ValueError:
                errors.append(f"{prefix}: published_after must be an ISO date.")
        for support in part.supports:
            source = by_id.get(support.evidence_id)
            if not source:
                errors.append(f"{prefix}: evidence ID {support.evidence_id} was not retrieved.")
                continue
            if part.kind == "papers":
                if source.provider != "arxiv":
                    errors.append(f"{prefix}: background articles cannot establish a paper request.")
                if cutoff:
                    try:
                        if date.fromisoformat(source.published[:10]) < cutoff:
                            errors.append(f"{prefix}: paper {support.evidence_id} predates the stated recency window.")
                    except ValueError:
                        errors.append(f"{prefix}: paper {support.evidence_id} has no usable publication date.")
            if part.kind == "dated_measurement":
                if not support.observed_at.strip() or normalize(support.observed_at) not in normalize(source.text):
                    errors.append(f"{prefix}: a measurement needs an observation period in its evidence text; retrieval date is insufficient.")
    missing = any(p.missing.strip() or not p.supports for p in assessment.parts)
    if assessment.action == "finish" and missing:
        errors.append("Cannot finish as complete while evidence is missing; choose a focused search or limited completion.")
    if assessment.action == "search" and not assessment.searches:
        errors.append("A search action needs a focused planned search.")
    if assessment.action != "search" and assessment.searches:
        errors.append("Finish/limited actions must not schedule searches.")
    if assessment.action == "limited" and not missing:
        errors.append("Use finish when every part is established; limited completion must identify a gap.")
    return errors[:8]


def supported_ids(assessment: dict) -> dict[int, set[int]]:
    return {index: {s["evidence_id"] for s in part["supports"]}
            for index, part in enumerate(assessment.get("parts", []))}
