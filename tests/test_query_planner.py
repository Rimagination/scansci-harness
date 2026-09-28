from scansci_html.qa.query_planner import plan_query


def test_method_source_policy_does_not_turn_evidence_question_into_methods_only_search():
    question = (
        "在光伏生态文献中总结生态影响；只有论文明确讨论方法时才引用方法。"
    )

    assert plan_query(question)["filters"] == {}


def test_explicit_method_comparison_still_searches_methods():
    question = "比较不同研究方法的优缺点"

    assert plan_query(question)["filters"] == {"section_kinds": ["methods"]}


def test_chinese_factoid_question_without_dimension_cue_has_no_required_facets():
    # Regression: this question used to emit “机制的核心思想是什么” and
    # “请依据资料回答” as mandatory facets; the resulting facet-coverage
    # failure withheld a verified answer that the evidence fully supported.
    plan = plan_query("Attention 机制的核心思想是什么？请依据资料回答。")

    assert plan["required_facets"] == []


def test_chinese_facet_candidates_containing_scaffolding_are_dropped():
    # The dimension cue (“影响”) is present, but the trailing candidate run
    # still carries question scaffolding and must not become a facet.
    plan = plan_query("气候变化对植被覆盖的影响是什么？请依据资料回答。")

    assert plan["required_facets"] == []


def test_chinese_multi_dimension_question_keeps_explicit_facets():
    plan = plan_query("不同建设年份的光伏项目如何影响微气候、植被群落和土壤碳储量？")

    assert [facet["id"] for facet in plan["required_facets"]] == [
        "微气候",
        "植被群落",
        "土壤碳储量",
    ]
