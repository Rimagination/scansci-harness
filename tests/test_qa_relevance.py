from scansci_html.qa.agent import (
    _apply_topical_relevance_gate,
    assess_facet_coverage,
    _filter_answer_to_requested_source_language,
)


def test_chinese_topic_gate_keeps_relevant_short_question_sufficient():
    question = "基于当前选择的知识库，找出与光伏电站和植物多样性直接相关的三条证据。"
    evidence = [
        {
            "quote_id": "q0001",
            "exact_quote": "光伏电站对板下植物生长具有明显影响。",
        }
    ]
    result = _apply_topical_relevance_gate(
        question,
        evidence,
        {"is_sufficient": True, "profile": "manual"},
    )

    assert result["is_sufficient"] is True


def test_chinese_topic_gate_still_rejects_unrelated_quotes():
    result = _apply_topical_relevance_gate(
        "光伏电站和植物多样性有哪些证据？",
        [{"quote_id": "q0001", "exact_quote": "量子材料的电荷迁移率在低温下提高。"}],
        {"is_sufficient": True, "profile": "manual"},
    )

    assert result["is_sufficient"] is False


def test_explicit_chinese_source_request_drops_english_claims():
    answer = {
        "question": "只返回中文原文摘要和引用",
        "answer": [
            {"claim_id": "c1", "text": "光伏板下植物多样性提高。", "quote_ids": ["q1"], "support_status": "supported"},
            {"claim_id": "c2", "text": "Solar farms alter vegetation.", "quote_ids": ["q2"], "support_status": "supported"},
        ],
        "limitations": [],
    }
    filtered = _filter_answer_to_requested_source_language(
        answer,
        [
            {"quote_id": "q1", "exact_quote": "光伏板下植物多样性提高。"},
            {"quote_id": "q2", "exact_quote": "Solar farms alter vegetation."},
        ],
        "只返回中文原文摘要和引用",
    )

    assert [item["claim_id"] for item in filtered["answer"]] == ["c1"]
    assert filtered["source_language_filter"] == "zh_source_excerpt"


def test_topical_gate_marks_borderline_overlap_for_review_instead_of_abstaining():
    result = _apply_topical_relevance_gate(
        "What evidence links tidal power to coral reef bleaching?",
        [{"quote_id": "q0001", "exact_quote": "Tidal currents influence coastal ecosystems."}],
        {"is_sufficient": True, "profile": "manual"},
        hits=[{"siliconflow_score": 0.72}],
    )

    assert result["is_sufficient"] is False
    assert result["answerability"] == "needs_review"
    assert result["retryable"] is True
    assert result["topical_relevance"]["status"] == "review"


def test_facet_coverage_accepts_scientific_phrase_variants():
    coverage = assess_facet_coverage(
        {
            "required_facets": [
                {"id": "soil carbon storage", "terms": ["soil carbon storage"]},
            ]
        },
        [{"quote_id": "q0001", "exact_quote": "The study measured soil organic carbon stocks."}],
    )

    assert coverage["status"] == "complete"
    assert coverage["covered_facets"] == ["soil carbon storage"]
    assert coverage["facet_scores"]["soil carbon storage"] >= 0.6


def test_mixed_language_question_with_matching_latin_terms_is_review_not_rejection():
    # Regression: “Attention 机制……” against an English library used to be
    # rejected as “generic Chinese overlap” even though the question's own
    # technical term (“attention”) matched the evidence, withholding a
    # verified answer the library fully supported.
    result = _apply_topical_relevance_gate(
        "Attention 机制的核心思想是什么？请依据资料回答。",
        [{"quote_id": "q0001", "exact_quote": "Scaled Dot-Product Attention computes compatibility between positions."}],
        {"is_sufficient": True, "profile": "manual"},
    )

    assert result["answerability"] == "needs_review"
    assert result["topical_relevance"]["status"] == "review"
    assert "attention" in result["topical_relevance"]["matched_terms"]


def test_chinese_question_without_any_latin_match_is_still_rejected():
    result = _apply_topical_relevance_gate(
        "植被群落和土壤碳储量如何响应放牧强度？",
        [{"quote_id": "q0001", "exact_quote": "Scaled Dot-Product Attention computes compatibility between positions."}],
        {"is_sufficient": True, "profile": "manual"},
    )

    assert result["is_sufficient"] is False
    assert result["answerability"] == "not_enough_information"
