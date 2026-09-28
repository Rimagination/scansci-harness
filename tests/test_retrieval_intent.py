from scansci_html.retrieval_intent import compile_retrieval_intent


def test_shared_intent_extracts_labelled_subject_without_forwarding_ui_instruction():
    intent = compile_retrieval_intent(
        "请检索与以下主题最相关的论文，并整理作者与 DOI。\n\n研究主题：植物功能性状的研究",
        kind="academic",
    )

    assert intent["subject"] == "植物功能性状的研究"
    assert intent["normalized_subject"] == "植物功能性状"
    assert intent["query_variants"] == ["植物功能性状的研究", "植物功能性状"]
    assert intent["quality_policy"] == "topic_relevance"


def test_shared_intent_normalizes_common_search_phrases_for_non_academic_tools():
    intent = compile_retrieval_intent("请搜索植物功能性状相关论文", kind="web")

    assert intent["subject"] == "植物功能性状"
    assert intent["normalized_subject"] == "植物功能性状"
    assert intent["kind"] == "web"


def test_shared_intent_preserves_doi_issn_and_arxiv_as_exact_lookup_requests():
    doi = compile_retrieval_intent("请查询 DOI: 10.1000/Example.42", kind="paper_atlas")
    issn = compile_retrieval_intent("期刊：1234-567X", kind="journal")

    assert doi["intent_type"] == "exact_identifier"
    assert doi["subject"] == "10.1000/Example.42"
    assert doi["quality_policy"] == "identifier_exact"
    assert issn["intent_type"] == "exact_identifier"
    assert issn["subject"] == "1234-567X"


def test_paper_download_intent_extracts_chinese_author_and_citation_ranking():
    intent = compile_retrieval_intent(
        "帮我下载中科院于贵瑞院士的TOP10被引论文",
        kind="paper_download",
    )

    assert intent["author"] == "于贵瑞"
    assert intent["author_only"] is True
    assert intent["subject"] == "于贵瑞"
    assert intent["sort"] == "cited_by_count"
    assert intent["limit"] == 10
    assert intent["intent_type"] == "author"
