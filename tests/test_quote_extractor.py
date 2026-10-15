import pytest

from scansci_html.qa.evidence_table import build_evidence_table
from scansci_html.qa.quote_extractor import (
    ExtractedQuote,
    extract_quotes,
    extract_quotes_with_llm,
    is_substantive_evidence_hit,
    validate_quotes,
)


def test_extract_quotes_selects_exact_evidence_text_from_ranked_hits():
    hits = [
        {
            "evidence_id": "doc1.s0001",
            "doc_id": "doc1",
            "title": "Model Paper",
            "doi": "10.1234/model",
            "section": "Results",
            "section_kind": "results",
            "text": "Model predictions explained cortical activity in language regions.",
            "score": 5.5,
            "matched_terms": ["model", "cortical", "activity", "language"],
        },
        {
            "evidence_id": "doc2.s0001",
            "doc_id": "doc2",
            "title": "Unrelated Paper",
            "doi": "10.1234/other",
            "section": "Methods",
            "section_kind": "methods",
            "text": "Samples were heated before sequencing.",
            "score": 0.2,
            "matched_terms": [],
        },
    ]

    quotes = extract_quotes(
        "What evidence links language models to cortical activity?",
        hits,
        max_quotes=2,
    )

    assert [quote.to_dict() for quote in quotes] == [
        {
            "quote_id": "q0001",
            "question": "What evidence links language models to cortical activity?",
            "evidence_ids": ["doc1.s0001"],
            "exact_quote": "Model predictions explained cortical activity in language regions.",
            "role": "supports",
            "claim_hint": "Model predictions explained cortical activity in language regions.",
            "confidence": 1.0,
        }
    ]


def test_extract_quotes_skips_bibliography_entries_and_title_fragments():
    hits = [
        {
            "evidence_id": "paper.s0001",
            "text": "[24] Mohammadreza, A., Marc, K., Pavel, B., 2022. Bifacial photovoltaic technology.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002",
            "text": "Ecohydrological effects of photovoltaic solar farms on soil microclimates: a modeling study.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002a",
            "text": "Singh, G.K., 2013.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002b",
            "text": "Energy 53, 1-13. https://doi.org/10.1016/j.energy.2013.02.057.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002c",
            "text": "A risk-based multi-criteria spatial decision analysis for solar power plant site selection.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002d",
            "text": "13, 9 (2022).",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002e",
            "text": "EPJ Photovolt.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002f",
            "text": "Energy 6, 742-754 (2021).",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002g",
            "text": "National growth dynamics of wind and solar power compared to climate targets.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0002h",
            "text": "Renewable and Sustainable Energy Reviews, 2013, 24: 544-556. ［24］Mardani A, et al.",
            "matched_terms": ["photovoltaic"],
        },
        {
            "evidence_id": "paper.s0003",
            "section": "Results",
            "text": "Photovoltaic arrays reduced daytime soil temperature variability in the observed plots.",
            "matched_terms": ["photovoltaic", "soil"],
            "score": 2.0,
        },
    ]

    quotes = extract_quotes("What changed under photovoltaic arrays?", hits, max_quotes=3)

    assert [quote.evidence_ids for quote in quotes] == [["paper.s0003"]]
    assert not is_substantive_evidence_hit(hits[0])
    assert not is_substantive_evidence_hit(hits[1])
    assert not is_substantive_evidence_hit(hits[2])
    assert not is_substantive_evidence_hit(hits[3])
    assert not is_substantive_evidence_hit(hits[4])
    assert not is_substantive_evidence_hit(hits[5])
    assert not is_substantive_evidence_hit(hits[6])
    assert not is_substantive_evidence_hit(hits[7])
    assert not is_substantive_evidence_hit(hits[8])
    assert not is_substantive_evidence_hit(hits[9])
    assert is_substantive_evidence_hit(hits[10])


def test_substantive_filter_keeps_concise_research_predicates():
    statements = [
        "BERT uses bidirectional self-attention to fuse left and right context.",
        "The result supports a cortical activity interpretation.",
        "The analysis links drought with lower biomass.",
        "A second enabling characteristic involves tumor-promoting inflammation.",
        "Mean soil temperature under panels fell by 2 degrees Celsius.",
        "Plant cover rose by 8 percent in the shaded plots.",
    ]

    assert all(is_substantive_evidence_hit({"section": "Results", "text": text}) for text in statements)


def test_substantive_filter_rejects_rose_author_fragments():
    for text in (
        "Rose, Smith, Brown, Solar park vegetation diversity, 2024.",
        "Rose et al. Solar park vegetation diversity, 2024.",
    ):
        assert not is_substantive_evidence_hit({"section": "Results", "text": text})


def test_substantive_filter_rejects_reference_section_metadata_even_with_factual_words():
    assert not is_substantive_evidence_hit(
        {
            "section_kind": "references",
            "section": "References",
            "text": "Photovoltaic arrays increased vegetation cover in arid regions.",
        }
    )


def test_extract_quotes_prefers_span_text_when_hit_contains_parent_context():
    hits = [
        {
            "evidence_id": "doc1.s0002",
            "doc_id": "doc1",
            "title": "Context Paper",
            "doi": "10.1234/context",
            "section": "Results",
            "section_kind": "results",
            "text": (
                "The cohort included 120 participants. "
                "Treatment increased cortical activity in language regions."
            ),
            "span_text": "Treatment increased cortical activity in language regions.",
            "score": 5.5,
            "matched_terms": ["cortical", "activity", "language"],
        }
    ]

    quotes = extract_quotes("What changed?", hits, max_quotes=1)

    assert quotes[0].exact_quote == "Treatment increased cortical activity in language regions."
    assert quotes[0].claim_hint == "Treatment increased cortical activity in language regions."


def test_validate_quotes_rejects_unknown_evidence_id_and_non_exact_quote():
    evidence = {
        "doc1.s0001": {
            "evidence_id": "doc1.s0001",
            "text": "The treatment increased biomass by 18%.",
        }
    }

    with pytest.raises(ValueError, match="unknown evidence_id"):
        validate_quotes(
            [
                ExtractedQuote(
                    quote_id="q0001",
                    question="question",
                    evidence_ids=["missing.s0001"],
                    exact_quote="The treatment increased biomass by 18%.",
                    role="supports",
                    claim_hint="The treatment increased biomass.",
                    confidence=0.8,
                )
            ],
            evidence,
        )

    with pytest.raises(ValueError, match="not an exact substring"):
        validate_quotes(
            [
                ExtractedQuote(
                    quote_id="q0002",
                    question="question",
                    evidence_ids=["doc1.s0001"],
                    exact_quote="The treatment increased yield by 18%.",
                    role="supports",
                    claim_hint="The treatment increased yield.",
                    confidence=0.8,
                )
            ],
            evidence,
        )


def test_extract_quotes_with_llm_uses_structured_output_and_validates_quotes():
    calls = []

    class FakeChatClient:
        def complete_json(self, messages, *, schema_name):
            calls.append((messages, schema_name))
            return [
                {
                    "quote_id": "q9000",
                    "evidence_ids": ["doc1.s0001"],
                    "exact_quote": "The treatment increased biomass by 18%.",
                    "role": "supports",
                    "claim_hint": "The treatment increased biomass.",
                    "confidence": 0.87,
                }
            ]

    hits = [
        {
            "evidence_id": "doc1.s0001",
            "text": "The treatment increased biomass by 18%.",
        }
    ]

    quotes = extract_quotes_with_llm("What happened?", hits, chat_client=FakeChatClient())

    assert quotes == [
        ExtractedQuote(
            quote_id="q9000",
            question="What happened?",
            evidence_ids=["doc1.s0001"],
            exact_quote="The treatment increased biomass by 18%.",
            role="supports",
            claim_hint="The treatment increased biomass.",
            confidence=0.87,
        )
    ]
    assert calls[0][1] == "extracted_quotes"
    assert "doc1.s0001" in calls[0][0][1]["content"]


def test_extract_quotes_with_llm_accepts_quotes_object_payload():
    class FakeChatClient:
        def complete_json(self, messages, *, schema_name):
            return {
                "quotes": [
                    {
                        "quote_id": "q9000",
                        "evidence_ids": ["doc1.s0001"],
                        "exact_quote": "The treatment increased biomass by 18%.",
                        "role": "supports",
                        "claim_hint": "The treatment increased biomass.",
                        "confidence": 0.87,
                    }
                ]
            }

    hits = [
        {
            "evidence_id": "doc1.s0001",
            "text": "The treatment increased biomass by 18%.",
        }
    ]

    quotes = extract_quotes_with_llm("What happened?", hits, chat_client=FakeChatClient())

    assert quotes[0].quote_id == "q9000"
    assert quotes[0].exact_quote == "The treatment increased biomass by 18%."


def test_build_evidence_table_keeps_source_metadata_for_human_review():
    quotes = [
        ExtractedQuote(
            quote_id="q0001",
            question="question",
            evidence_ids=["doc1.s0001"],
            exact_quote="The treatment increased biomass by 18%.",
            role="supports",
            claim_hint="The treatment increased biomass.",
            confidence=0.82,
        )
    ]
    evidence = {
        "doc1.s0001": {
            "evidence_id": "doc1.s0001",
            "title": "Biomass Paper",
            "doi": "10.1234/biomass",
            "section": "Results",
            "section_kind": "results",
            "source_url": "https://publisher.example/biomass",
            "html_path": "paper.evidence.html",
            "html_anchor": "results-p1-s0001",
            "text": "The treatment increased biomass by 18%.",
        }
    }

    table = build_evidence_table(quotes, evidence)

    assert table == [
        {
            "quote_id": "q0001",
            "claim_target": "The treatment increased biomass.",
            "stance": "supports",
            "exact_quote": "The treatment increased biomass by 18%.",
            "paper": "Biomass Paper",
            "section": "Results",
            "section_kind": "results",
            "doi": "10.1234/biomass",
            "source_url": "https://publisher.example/biomass",
                "evidence_id": "doc1.s0001",
                "html_path": "paper.evidence.html",
                "html_anchor": "results-p1-s0001",
                "matched_text": "The treatment increased biomass by 18%.",
                "context_before": "",
                "context_before_evidence_id": "",
                "context_after": "",
                "context_after_evidence_id": "",
                "context_text": "",
            "parent_block_id": "",
            "parent_evidence_ids": [],
            "confidence": 0.82,
        }
    ]


def test_build_evidence_table_keeps_parent_context_when_available():
    quotes = [
        ExtractedQuote(
            quote_id="q0001",
            question="question",
            evidence_ids=["doc1.s0002"],
            exact_quote="Treatment increased cortical activity in language regions.",
            role="supports",
            claim_hint="Treatment increased cortical activity.",
            confidence=0.82,
        )
    ]
    evidence = {
        "doc1.s0002": {
            "evidence_id": "doc1.s0002",
            "title": "Context Paper",
            "doi": "10.1234/context",
            "section": "Results",
            "section_kind": "results",
            "html_path": "paper.evidence.html",
            "html_anchor": "results-p1-s0002",
            "matched_text": "Treatment increased cortical activity in language regions.",
            "context_before": "The cohort included 120 participants.",
            "context_before_evidence_id": "doc1.s0001",
            "context_after": "The follow-up assessment found the effect persisted.",
            "context_after_evidence_id": "doc1.s0003",
            "text": (
                "The cohort included 120 participants. "
                "Treatment increased cortical activity in language regions."
            ),
            "span_text": "Treatment increased cortical activity in language regions.",
            "parent_text": (
                "The cohort included 120 participants. "
                "Treatment increased cortical activity in language regions."
            ),
            "parent_block_id": "doc1:results-p1",
            "parent_evidence_ids": ["doc1.s0001", "doc1.s0002"],
        }
    }

    table = build_evidence_table(quotes, evidence)

    assert table[0]["context_text"] == (
        "The cohort included 120 participants. "
        "Treatment increased cortical activity in language regions."
    )
    assert table[0]["parent_block_id"] == "doc1:results-p1"
    assert table[0]["parent_evidence_ids"] == ["doc1.s0001", "doc1.s0002"]
    assert table[0]["context_before"] == "The cohort included 120 participants."
    assert table[0]["context_after"] == "The follow-up assessment found the effect persisted."
