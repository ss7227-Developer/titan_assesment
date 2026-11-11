from copy import deepcopy

import pytest

from research_agent.assessment import assessment_errors
from research_agent.contracts import ResearchAssessment, Source


def decision(*, action="finish", kind="fact", missing="", supports=None, cutoff=""):
    return {"action": action, "parts": [{"question_part": "Research question part", "kind": kind,
        "supports": supports if supports is not None else [{"evidence_id": 1,
        "observed_at": ""}],
        "missing": missing, "published_after": cutoff}], "searches": [], "reason": "Evidence assessment summary."}


def sources(provider="wikipedia", published=""):
    s = Source(provider, "id", "A source", "https://example.org", "A supported observation was recorded in March 2024.",
               "2026-11-11", published=published, citation_id=1)
    return {s.identity: s}


def errors(data, evidence=None, needs=None):
    return assessment_errors(ResearchAssessment.model_validate(data), evidence if evidence is not None else sources(), needs or [])


def test_valid_support_and_explicit_completion():
    assert errors(decision()) == []


@pytest.mark.parametrize("change,expected", [
    ("id", "not retrieved"),
    ("missing", "Cannot finish"), ("no_support", "no evidence"),
])
def test_finish_requires_usable_support(change, expected):
    data = decision()
    if change == "id":
        data["parts"][0]["supports"][0]["evidence_id"] = 99
    elif change == "missing":
        data["parts"][0]["missing"] = "An important question part is unanswered."
    else:
        data["parts"][0]["supports"] = []
    assert expected in " ".join(errors(data))


def test_paper_request_cannot_be_satisfied_by_background_article():
    assert "background articles" in " ".join(errors(decision(kind="papers"), needs=["papers"]))
    assert not errors(decision(kind="papers"), sources("arxiv", "2025-01-01"), ["papers"])


def test_declared_paper_requirement_cannot_be_dropped():
    assert "requested papers" in " ".join(errors(decision(), needs=["papers"]))


def test_recency_uses_publication_date_not_retrieval_time():
    data = decision(kind="papers", cutoff="2023-01-01")
    assert "predates" in " ".join(errors(data, sources("arxiv", "2017-01-01")))
    assert not errors(data, sources("arxiv", "2025-01-01"))


def test_comparison_requires_separate_parts_and_each_side_supported():
    data = decision(kind="comparison")
    assert "at least two" in " ".join(errors(data, needs=["comparison"]))
    other = deepcopy(data["parts"][0])
    other.update(question_part="Other subject", supports=[], missing="No evidence for the other subject.")
    data["parts"].append(other)
    assert "Cannot finish" in " ".join(errors(data, needs=["comparison"]))
    data["action"] = "limited"
    assert not errors(data, needs=["comparison"])


def test_dated_measurement_requires_observation_period_in_source():
    data = decision(kind="dated_measurement")
    assert "observation period" in " ".join(errors(data, needs=["dated_measurement"]))
    data["parts"][0]["supports"][0]["observed_at"] = "2026-11-11"
    assert "observation period" in " ".join(errors(data))
    data["parts"][0]["supports"][0]["observed_at"] = "March 2024"
    assert not errors(data)


def test_search_and_limited_completion_are_valid_alternatives_to_false_completeness():
    data = decision(action="limited", supports=[], missing="No reliable observations were returned.")
    assert not errors(data)
    data.update(action="search", searches=[{"tool": "search_arxiv", "query": "focused topic", "max_results": 2}])
    assert not errors(data)


def test_finish_cannot_smuggle_an_additional_search():
    data = decision()
    data["searches"] = [{"tool": "search_wikipedia", "query": "another topic", "max_results": 1}]
    assert "must not schedule" in " ".join(errors(data))
