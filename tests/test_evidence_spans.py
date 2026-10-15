import json
from pathlib import Path

from bs4 import BeautifulSoup

from scansci_html.evidence_spans import extract_evidence_spans, write_evidence_html


def test_extract_evidence_spans_creates_sentence_level_metadata_and_stable_ids():
    html = """
    <!doctype html>
    <html>
      <head><title>Fallback Title</title></head>
      <body>
        <article class="paper" data-doi="10.1234/example" data-source-url="https://publisher.example/article" data-publication-year="2024">
          <h1>Evidence First Article</h1>
          <section>
            <h2>Results</h2>
            <p id="result-p1">Treatment increased root biomass by 18%. Control plants did not change.</p>
          </section>
          <figure>
            <figcaption id="fig-1">Fig. 1. Biomass responses across treatments.</figcaption>
          </figure>
        </article>
      </body>
    </html>
    """

    spans = extract_evidence_spans(
        html,
        html_path=Path("library/article.html"),
        min_sentence_length=10,
    )

    assert [span.text for span in spans] == [
        "Treatment increased root biomass by 18%.",
        "Control plants did not change.",
        "Fig. 1. Biomass responses across treatments.",
    ]
    assert [span.evidence_id for span in spans] == [
        "10.1234_example.s0001",
        "10.1234_example.s0002",
        "10.1234_example.s0003",
    ]
    assert spans[0].doc_id == "10.1234_example"
    assert spans[0].title == "Evidence First Article"
    assert spans[0].doi == "10.1234/example"
    assert spans[0].source_url == "https://publisher.example/article"
    assert spans[0].publication_year == 2024
    assert spans[0].html_path == "library/article.html"
    assert spans[0].block_id == "10.1234_example:result-p1"
    assert spans[0].html_anchor == "result-p1-s0001"
    assert spans[0].section == "Results"
    assert spans[0].section_kind == "results"
    assert spans[0].block_type == "paragraph"
    assert spans[0].sentence_index == 1
    assert spans[0].char_start == 0
    assert spans[0].char_end == len("Treatment increased root biomass by 18%.")
    assert spans[2].block_type == "caption"


def test_write_evidence_html_injects_sentence_anchors_without_overwriting_source(tmp_path: Path):
    source = tmp_path / "paper.html"
    source.write_text(
        """
        <article class="paper" data-doi="10.1234/html">
          <h1>HTML Paper</h1>
          <h2>Methods</h2>
          <p id="methods-p1">Samples were heated for 30 minutes. Samples were then cooled.</p>
        </article>
        """,
        encoding="utf-8",
    )
    output = tmp_path / "paper.evidence.html"

    spans = write_evidence_html(
        source,
        output_path=output,
        min_sentence_length=10,
    )

    assert source.read_text(encoding="utf-8") != output.read_text(encoding="utf-8")
    assert "data-evidence-id" not in source.read_text(encoding="utf-8")
    soup = BeautifulSoup(output.read_text(encoding="utf-8"), "lxml")
    style = soup.select_one("style[data-scansci-evidence-style]")
    assert style is not None
    style_text = style.get_text()
    assert "[data-evidence-id]:target" in style_text
    assert "#79f2a6" in style_text
    assert "outline: none" in style_text
    assert "box-shadow: none" in style_text
    assert "#2457a6" not in style_text
    assert ".scansci-evidence-span::after" not in style_text
    assert "attr(data-evidence-short-id)" not in style_text
    assert ".scansci-evidence-row > :last-child::after" not in style_text
    anchors = soup.select("span[data-evidence-id]")
    assert [anchor["id"] for anchor in anchors] == ["methods-p1-s0001", "methods-p1-s0002"]
    assert [anchor["data-evidence-id"] for anchor in anchors] == [
        "10.1234_html.s0001",
        "10.1234_html.s0002",
    ]
    assert [anchor["data-evidence-short-id"] for anchor in anchors] == ["s0001", "s0002"]
    assert [anchor["class"] for anchor in anchors] == [["scansci-evidence-span"], ["scansci-evidence-span"]]
    assert anchors[0]["title"] == "10.1234_html.s0001 (methods)"
    assert [span.to_dict()["html_anchor"] for span in spans] == [
        "methods-p1-s0001",
        "methods-p1-s0002",
    ]


def test_evidence_spans_index_table_rows_and_preserve_table_cells_in_sidecar(tmp_path: Path):
    source = tmp_path / "table-paper.html"
    source.write_text(
        """
        <article class="paper" data-doi="10.1234/table">
          <h1>Table Paper</h1>
          <h2>Results</h2>
          <table>
            <thead><tr><th>Treatment</th><th>Biomass</th></tr></thead>
            <tbody>
              <tr id="row-1"><td>Control</td><td>10 g</td></tr>
              <tr><td>Fertilized</td><td>18 g</td></tr>
            </tbody>
          </table>
        </article>
        """,
        encoding="utf-8",
    )
    output = tmp_path / "table-paper.evidence.html"

    spans = write_evidence_html(source, output_path=output, min_sentence_length=3)

    table_spans = [span for span in spans if span.block_type == "table_row"]
    assert [span.text for span in table_spans] == [
        "Treatment Biomass",
        "Control 10 g",
        "Fertilized 18 g",
    ]
    assert table_spans[1].html_anchor == "row-1"
    assert table_spans[2].html_anchor == "evidence-block-0003"
    soup = BeautifulSoup(output.read_text(encoding="utf-8"), "lxml")
    row = soup.select_one("tr#row-1")
    generated_row = soup.select_one("tr#evidence-block-0003")
    assert row["data-evidence-id"] == "10.1234_table.s0002"
    assert row["data-evidence-short-id"] == "s0002"
    assert "scansci-evidence-row" in row["class"]
    assert generated_row["data-evidence-id"] == "10.1234_table.s0003"
    assert generated_row["data-evidence-short-id"] == "s0003"
    assert "scansci-evidence-row" in generated_row["class"]
    assert [cell.get_text(strip=True) for cell in generated_row.select("td")] == ["Fertilized", "18 g"]


def test_nested_subsections_inherit_parent_section_kind():
    spans = extract_evidence_spans(
        """
        <article class="paper" data-doi="10.1234/nested">
          <h1>Nested Section Paper</h1>
          <section>
            <h2>2 Results and Discussion</h2>
            <section>
              <h3>2.1 Biomass changed under warming</h3>
              <p>Warming increased root biomass in the treatment plots.</p>
            </section>
          </section>
        </article>
        """,
        html_path="nested.html",
        min_sentence_length=10,
    )

    assert len(spans) == 1
    assert spans[0].section == "2.1 Biomass changed under warming"
    assert spans[0].section_kind == "results"


def test_sentence_offsets_do_not_split_decimal_subscript_fragments():
    spans = extract_evidence_spans(
        """
        <article class="paper" data-doi="10.1234/decimal">
          <h1>Decimal Formula Paper</h1>
          <h2>Abstract</h2>
          <p>Thin films of Co<sub>8</sub>.<sub>5</sub>Zn<sub>8</sub>.<sub>5</sub>Mn<sub>3</sub> showed a nonreciprocal response.</p>
        </article>
        """,
        html_path="decimal.html",
        min_sentence_length=10,
    )

    assert [span.text for span in spans] == [
        "Thin films of Co 8 . 5 Zn 8 . 5 Mn 3 showed a nonreciprocal response."
    ]


def test_extract_evidence_spans_skips_non_article_evidence_sections():
    spans = extract_evidence_spans(
        """
        <article class="paper" data-doi="10.1234/backmatter">
          <h1>Back Matter Paper</h1>
          <h2>Results</h2>
          <p>Carbon flux increased after the intervention.</p>
          <h2>Authors and Affiliations</h2>
          <p>Department of Example Science, Example University, Example City.</p>
          <h2>References</h2>
          <p>Smith J. Example reference title. Journal Name 1, 1-10.</p>
          <h2>Data availability</h2>
          <p>All data are available from the corresponding author.</p>
        </article>
        """,
        html_path="backmatter.html",
        min_sentence_length=10,
    )

    assert [span.text for span in spans] == ["Carbon flux increased after the intervention."]
    assert [span.section_kind for span in spans] == ["results"]


def test_evidence_span_dict_is_json_serializable():
    spans = extract_evidence_spans(
        """
        <article class="paper" data-doi="10.1234/json">
          <h1>JSON Paper</h1>
          <h2>Abstract</h2>
          <p>Sentence level evidence should serialize cleanly.</p>
        </article>
        """,
        html_path="json.html",
        min_sentence_length=10,
    )

    payload = json.loads(json.dumps(spans[0].to_dict()))

    assert payload["evidence_id"] == "10.1234_json.s0001"
    assert payload["section_kind"] == "abstract"
    assert payload["publication_year"] is None


def test_extract_evidence_spans_reads_publication_year_from_meta():
    spans = extract_evidence_spans(
        """
        <html>
          <head>
            <meta name="citation_publication_date" content="2022/05/01">
          </head>
          <body>
            <article class="paper" data-doi="10.1234/year">
              <h1>Publication Year Paper</h1>
              <h2>Results</h2>
              <p>Publication year metadata should be preserved.</p>
            </article>
          </body>
        </html>
        """,
        html_path="year.html",
        min_sentence_length=10,
    )

    assert spans[0].publication_year == 2022


def test_extract_evidence_spans_keeps_hierarchical_section_identity_and_splits_chinese():
    spans = extract_evidence_spans(
        """
        <article class="paper" data-doi="10.1234/chinese">
          <h1>结构化证据论文</h1>
          <h2>Results</h2>
          <h3>2.1 样地观测</h3>
          <p id="result-p1">处理组生物量显著增加。对照组没有显著变化。该结果在第二年仍然成立。</p>
        </article>
        """,
        html_path="chinese.html",
        min_sentence_length=6,
    )

    assert [span.text for span in spans] == [
        "处理组生物量显著增加。",
        "对照组没有显著变化。",
        "该结果在第二年仍然成立。",
    ]
    assert spans[0].section_path == "结构化证据论文 / Results / 2.1 样地观测"
    assert spans[0].section_level == 3
    assert spans[0].section_id
    assert spans[0].parent_section_id
    assert spans[0].source_locator == "anchor:result-p1-s0001"
