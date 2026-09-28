"""Built-in, non-executable skill contracts used by ScanSci workflows.

The records in this module are deliberately data and prompt contracts rather
than arbitrary plug-ins.  They are shipped with the desktop application and
are applied only by ScanSci-owned workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_RIMAGINATION_MIT_NOTICE = """MIT License

Copyright (c) 2026 Rimagination

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "id": "literature-search",
        "name": "literature-search",
        "description": "在当前证据库中检索并定位原文。",
        "path": "builtin:literature-search",
        "enabled": True,
        "source_type": "builtin",
        "source": "ScanSci",
    },
    {
        "id": "evidence-review",
        "name": "evidence-review",
        "description": "核验引文、记录人工判断。",
        "path": "builtin:evidence-review",
        "enabled": True,
        "source_type": "builtin",
        "source": "ScanSci",
    },
    {
        "id": "evidence-retrieval",
        "name": "evidence-retrieval",
        "description": "在当前证据库中按需检索、排序并绑定可定位原文。",
        "path": "builtin:evidence-retrieval",
        "enabled": True,
        "source_type": "builtin",
        "source": "ScanSci/scansci-evidence · MIT",
    },
    {
        "id": "good-question",
        "name": "good-question",
        "description": "从材料中收束重要、可检验且可证伪的核心研究问题。",
        "path": "builtin:good-question",
        "enabled": True,
        "source_type": "builtin",
        "source": "Rimagination/good-question · MIT",
    },
    {
        "id": "good-story",
        "name": "good-story",
        "description": "用证据支撑的张力、转折与结论组织科研叙事，避免过度宣称。",
        "path": "builtin:good-story",
        "enabled": True,
        "source_type": "builtin",
        "source": "Rimagination/good-story · MIT",
    },
    {
        "id": "scientific-slides",
        "name": "scientific-slides",
        "description": "将 PDF、Word、Markdown、TXT 或 HTML 直接整理为可编辑 PPTX。",
        "path": "builtin:scientific-slides",
        "enabled": True,
        "source_type": "builtin",
        "source": "ScanSci original",
    },
    {
        "id": "web-access",
        "name": "web-access",
        "description": "通过真实浏览器/CDP 处理搜索、网页抓取、登录态访问与页面交互。",
        "path": "builtin:web-access",
        "enabled": True,
        "source_type": "builtin",
        "source": "eze-is/web-access · MIT",
    },
    {
        "id": "typesafe-ai",
        "name": "typesafe-ai",
        "description": "使用 TypeSafe System One 与 Jev 进行结构化判断、路由、排序、抽取和证据核验。",
        "path": "builtin:typesafe-ai",
        "enabled": True,
        "source_type": "builtin",
        "source": "TypeSafe AI/typesafe-ai/skills · MIT · ScanSci adaptation",
    },
]

BUILTIN_SKILLS.append({
    "id": "agent-reach",
    "name": "agent-reach",
    "description": "Routes public web, RSS, GitHub, Bilibili, and V2EX reads/searches through built-in backends.",
    "path": "builtin:agent-reach",
    "enabled": True,
    "source_type": "builtin",
    "source": "Panniantong/Agent-Reach / MIT / ScanSci adaptation",
})


# Research-oriented Skill contracts adapted for the ScanSci runtime.  The
# upstream packages are intentionally not copied wholesale: ScanSci keeps the
# declarative workflow and provenance rules, while tool calls, evidence access,
# file writes, and optional dependencies remain owned by ScanSci.
RESEARCH_BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "id": "scientific-brainstorming",
        "name": "scientific-brainstorming",
        "description": "Evidence-bounded scientific ideation for questions, hypotheses, experiments, and falsifiers.",
        "path": "builtin:scientific-brainstorming",
        "enabled": True,
        "source_type": "builtin",
        "source": "K-Dense-AI/scientific-agent-skills · MIT · ScanSci adaptation",
    },
    {
        "id": "nature-academic-search",
        "name": "nature-academic-search",
        "description": "Multi-source academic discovery, DOI verification, citation tracing, and source-set construction.",
        "path": "builtin:nature-academic-search",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "literature-review",
        "name": "literature-review",
        "description": "Systematic, evidence-bounded literature review and thematic synthesis.",
        "path": "builtin:literature-review",
        "enabled": True,
        "source_type": "builtin",
        "source": "K-Dense-AI/scientific-agent-skills · MIT · ScanSci adaptation",
    },
    {
        "id": "academic-research-suite",
        "name": "academic-research-suite",
        "description": "End-to-end routing across question formation, search, evidence, statistics, figures, writing, review, data, and PPT.",
        "path": "builtin:academic-research-suite",
        "enabled": True,
        "source_type": "builtin",
        "source": "ScanSci composition layer",
    },
    {
        "id": "nature-statistics",
        "name": "nature-statistics",
        "description": "Statistical reporting and audit for manuscripts, figures, and supplementary materials.",
        "path": "builtin:nature-statistics",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "scientific-visualization",
        "name": "scientific-visualization",
        "description": "Scientific figure planning and review that preserves data meaning, provenance, uncertainty, and accessibility.",
        "path": "builtin:scientific-visualization",
        "enabled": True,
        "source_type": "builtin",
        "source": "K-Dense-AI/scientific-agent-skills · MIT · ScanSci adaptation",
    },
    {
        "id": "nature-figure",
        "name": "nature-figure",
        "description": "Nature-style publication figures and scientific schematics with reproducible export and source-aware review.",
        "path": "builtin:nature-figure",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-writing",
        "name": "nature-writing",
        "description": "Nature-style manuscript drafting and argument reconstruction from evidence, figures, results, or notes.",
        "path": "builtin:nature-writing",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-polishing",
        "name": "nature-polishing",
        "description": "Nature-leaning academic English polishing and translation with claim-drift checks.",
        "path": "builtin:nature-polishing",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-reviewer",
        "name": "nature-reviewer",
        "description": "Nature-style pre-submission review of significance, evidence, methods, statistics, figures, and consistency.",
        "path": "builtin:nature-reviewer",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-response",
        "name": "nature-response",
        "description": "Auditable point-by-point reviewer response letters and revision-package consistency checks.",
        "path": "builtin:nature-response",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-data",
        "name": "nature-data",
        "description": "Data Availability statements, repository plans, metadata checklists, and FAIR-readiness review.",
        "path": "builtin:nature-data",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
    {
        "id": "nature-paper2ppt",
        "name": "nature-paper2ppt",
        "description": "Evidence-linked conversion of a scientific paper into a Chinese PPTX or journal-club presentation.",
        "path": "builtin:nature-paper2ppt",
        "enabled": True,
        "source_type": "builtin",
        "source": "Yuan1z0825/nature-skills · Apache-2.0 · ScanSci adaptation",
    },
]


SCIENTIFIC_SLIDES_CONTRACT = """You are ScanSci Presentation Studio.

Apply these built-in skill contracts while preparing a scientific presentation:

1. Good Question: infer one sharp, answerable research question from the supplied
   material. Do not invent a literature gap, consensus, result, or hypothesis.
2. Good Story: organise the deck as context -> question -> approach -> key
   findings -> implication -> close. Keep uncertainty and limitations visible;
   never upgrade correlation to causation. When sources describe sibling or
   parallel model variants, do not rewrite them as a single linear replacement
   sequence; distinguish the shared foundation from each branch faithfully.
3. Scientific Slides: every slide has one job, a claim-bearing title, and a
   visual composition (comparison, cards, process, metric, figure, or timeline).
   Keep visible copy concise and audience-facing. Do not make "evidence chain",
   "source verification", or "generation process" the presentation story. Put
   source information in a small footer or final reference page instead.
   Use a process layout only for a genuine ordered sequence. Use comparison for
   parallel dimensions and branches when one shared foundation leads to sibling
   approaches. Do not imply that one model or method replaced another unless the
   supplied material explicitly supports that relationship. Avoid vague filler:
   each content slide should normally contain 3 to 5 specific, source-grounded
   points, while keeping every point short enough to present aloud.

Return one JSON object only with this exact shape:
{
  "title": "string",
  "central_question": "string",
  "story": "string",
  "slides": [
    {
      "title": "string",
      "takeaway": "string",
      "layout": "cards | comparison | process | branches",
      "bullets": ["string"],
      "source_pages": [1]
    }
  ]
}

Use only information present in the supplied text. Produce 5 to 8 content
slides in addition to the automatically generated cover, with 3 to 5 short
bullets on a slide whenever the evidence supports them. Source-page numbers must refer to
the provided source metadata; use an empty list when a page is not known.
"""


def default_skill_records() -> list[dict[str, Any]]:
    """Return copies suitable for settings defaults without exposing mutable globals."""

    return [dict(item) for item in [*BUILTIN_SKILLS, *RESEARCH_BUILTIN_SKILLS]]


def builtin_skill_asset_path(identifier: str) -> Path:
    """Return the packaged resource directory for a shipped Skill, if present."""

    safe_identifier = "".join(
        character for character in str(identifier or "").strip().lower() if character.isalnum() or character in {"-", "_", "."}
    )
    return Path(__file__).with_name("builtin_skill_assets") / safe_identifier


def third_party_notices() -> dict[str, str]:
    """Expose notices for the about/settings surface without executing a Skill."""

    return {
        "Rimagination/good-question": _RIMAGINATION_MIT_NOTICE,
        "Rimagination/good-story": _RIMAGINATION_MIT_NOTICE,
        "eze-is/web-access": "MIT License. Source: https://github.com/eze-is/web-access",
        "TypeSafe AI/typesafe-ai/skills": "MIT License. ScanSci ships a bounded adaptation for the TypeSafe System One/Jev integration. Source: https://github.com/typesafe-ai/skills",
        "Panniantong/Agent-Reach": "MIT License. ScanSci ships a read-only native adaptation of the routing and channel ideas. Source: https://github.com/Panniantong/Agent-Reach",
        "K-Dense-AI/scientific-agent-skills": "MIT License. Source: https://github.com/K-Dense-AI/scientific-agent-skills",
        "Yuan1z0825/nature-skills": "Apache-2.0 License. Source: https://github.com/Yuan1z0825/nature-skills",
        "OpenAI Zotero plugin": "MIT License. Source: https://github.com/openai/plugins/tree/main/plugins/zotero",
    }
