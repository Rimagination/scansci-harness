"""Dynamic, truthful ScanSci identity and Skill context for model conversations."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .build_info import current_build_info
from .agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError
from .skill_manager import installed_skills
from .skill_runtime import SkillSelection, resolve_skill_selection


_COMPACT_SKILL_CONTRACTS = {
    "web-access": """# Web Access：ScanSci 运行时契约

目标：使用 Pi 当前实际提供的联网工具完成用户请求，并交付带来源链接的最终结果。

- 普通关键词发现使用 `search_web`；公开直链、RSS、GitHub、Bilibili、V2EX 使用 `agent_reach`。
- 只有当公开读取不足，且页面需要登录态、动态渲染、反爬处理或浏览器上下文时，才使用 `browser_access`。
- `browser_access` 仅仅读取渲染后的页面文字；不得把它当成任意 JavaScript、点击、提交、上传或写入工具。
- 同一个 URL 不要同时重复调用多个公开读取器；前一路径返回反爬、登录或内容不足时再升级到浏览器路径。
- 必须真正调用工具后再回答，不得把 Skill 安装说明、环境检查命令或 `${CLAUDE_SKILL_DIR}` 输出给用户。
- 搜索摘要属于网页发现线索，不等于已核验全文；涉及时间敏感信息时写明检索时间并给出来源 URL。
- 不声称使用了浏览器、登录态或完成页面交互，除非本轮确实收到了 `browser_access` 成功结果。
""",
    "agent-reach": """# Agent Reach：ScanSci 运行时契约

`agent_reach` 是公开互联网的零安装只读路由器：
- `read` 读取明确的公开 URL；`search` 做公开渠道发现；`status` 检查渠道能力。
- 普通网页搜索优先 `search_web`；平台直链和结构化来源优先 `agent_reach`。
- 如果结果显示反爬、登录墙或动态内容不足，升级到 `browser_access`，不要重复请求同一静态入口。
- 不注入 Cookie、不执行 shell、不声称使用浏览器或登录态，除非收到对应工具结果。
""",
    "good-question": """# Good Question：运行时精简契约

目标：把用户的模糊方向收束为重要、可执行、可证伪、能经受评审质疑的科学问题。不要只列选题，不要把缺少证据本身当作创新。

请只输出一张完整的中文卡片，严格使用以下字段且每个字段只出现一次：

## 好问题卡
**暂定题目：** 一句话，避免空泛口号。
**核心研究问题：** 写成一个边界清楚、可由数据回答的问句；指出对象、条件和结果。
**为什么值得做：** 必须以“基于用户信息的暂定判断：”开头，说明改变哪项认识或决策；未联网时不得声称“现有研究多……”或虚构文献空白。
**它挑战了什么默认假设：** 写出可被检验的默认认识。
**竞争性解释：** 分别写 H1、H2、H3；H1 是最直接的目标解释，H2 是最可信的替代机制，H3 是零效应、混杂、反向关系或测量偏差解释。三者必须彼此可区分并各自给出不同的可观测预测；不要为了凑数强行发明复杂曲线或未经用户提供的机制。
**关键判别证据或实验：** 明确自变量/暴露、因变量/结局、关键对照与判别规则，分别写出更支持 H1、H2、H3 的可观察数据图形或结果模式。问题梳理阶段不要写回归公式、希腊字母系数或系数正负号；把具体统计模型留给后续分析方案，避免在变量编码未定义时制造符号矛盾。
**什么结果会推翻它：** 给出至少一个能否定主张的具体结果，不得只写“结果不显著”。
**两周内可做的 pilot：** 必须能在 14 天内完成，列出最小样本或最小数据、4 个以内里程碑，以及继续/修改/停止的数值化决策门槛；不得把一年、季度或完整项目写成两周 pilot。
**所需数据与资源：** 区分已有资源、待补数据和最大依赖。
**最强评审质疑：** 写出最可能导致拒稿或否定结论的一条质疑，并给出最低成本应对。
**下一步：** 只给一个立即可执行的动作。

质量底线：
- 若用户信息不足，做最少且显式的假设，并标注“待用户确认”；仍需给出可讨论的完整初稿。
- 核心问题、H1/H2/H3、判别实验、推翻条件和 pilot 决策门槛必须互相对应。
- 输出前做一次内部一致性审校：假设的文字方向、可观察预测、支持条件和推翻条件不得互相矛盾；观察性数据不得被写成已识别的因果效应。
- pilot 的继续/停止门槛应包含效应量、预测改善或数据质量标准之一，不得只靠 p 值；不要把任意阈值伪装成公认标准。
- 不输出内部思维过程、参考卡名称或元说明；不编造数据、引文或已完成的实验。
- 使用简洁自然的中文，避免重复词、乱码、字段重复、残缺编号和未闭合标点；全文宜控制在 1200 字以内。
""",
}


def _runtime_skill_instructions(identifier: str, instructions: str) -> str:
    """Return a model-sized Skill contract while preserving acceptance criteria.

    Some bundled Skills include long routing essays and reading lists intended
    for a frontier coding agent.  Passing those verbatim to a small managed
    chat model can consume most of its useful attention and cause degenerate
    output.  Product-owned compact contracts keep the task and its quality
    gates explicit without treating the reference material as prompt filler.
    """

    compact = _COMPACT_SKILL_CONTRACTS.get(identifier.strip().lower())
    return compact.strip() if compact else instructions


def runtime_self_description(
    workspace: str | Path,
    *,
    question: str,
    model_id: str,
    provider_name: str,
    chat_mode: str,
) -> str:
    """Return authoritative product facts for a pure self-description query.

    Model training data is never a reliable source for the running desktop
    build or its installed extensions.  Keeping this answer in the runtime is
    the same principle as answering a version command from package metadata.
    """

    normalized = re.sub(r"\s+", "", str(question or "")).lower()
    identity_terms = ("你是谁", "什么模型", "哪个模型", "版本", "能做什么", "能干什么", "能干啥", "skill", "技能")
    action_terms = ("帮我", "替我", "写一", "生成", "制作", "分析这", "总结这", "检索这")
    if not any(term in normalized for term in identity_terms):
        return ""
    if len(normalized) > 180 or any(term in normalized for term in action_terms):
        return ""
    if re.search(r"(?:只(?:回答|写|给).{0,8}模型名|仅(?:回答|写|给).{0,8}模型名)", normalized):
        return model_id
    if re.search(r"(?:两句|二句|2句|两句话|简短|简洁|精简)", normalized):
        return (
            f"我是 ScanSci 桌面研究工作台中的科研 Agent，负责对话、分析并按需调用本地科研工具。"
            f"当前底层模型是 {model_id}，模型服务由 {provider_name} 提供。"
        )

    build = current_build_info()
    skills = [
        str(item.get("id", ""))
        for item in installed_skills(workspace)
        if item.get("available") and item.get("enabled", True) and item.get("id")
    ]
    skill_text = "、".join(f"${item}" for item in skills) or "暂无"
    mode_names = {
        "general": "通用",
        "writing": "写作",
        "knowledge": "知识库",
        "slides": "幻灯片",
    }
    return (
        f"我是 ScanSci，运行在桌面研究工作台 ScanSci | 搜索科学中，Agent 编排由 Pi SDK 驱动。\n\n"
        f"- ScanSci 版本：{build.get('version', '')}（构建 {build.get('build_id', 'source')}）\n"
        f"- 当前底层模型：{model_id}\n"
        f"- 当前模型服务：{provider_name}\n"
        f"- 当前模式：{mode_names.get(chat_mode, '通用')}\n"
        f"- 可用模式：通用、写作、知识库、幻灯片\n"
        f"- 已启用 Skill：{skill_text}\n\n"
        "我可以直接对话和写作，也能读取本轮附件，以及当前或最近任务中已经登记的下载/生成文件；"
        "我可以检查并检索已连接的本机 Zotero 文献库。知识库模式会进行本地混合检索、证据核验并把引用定位到证据块；"
        "幻灯片模式可依据源材料和所选模板生成可编辑 PPTX。论文获取、模型服务、本地模型和 MCP 也由 ScanSci 工作台统一管理。"
    )


def selected_skill_ids(payload: dict[str, Any], messages: list[dict[str, Any]]) -> list[str]:
    """Return explicit or conservatively inferred Skill selections."""

    return list(resolve_skill_selection(payload, messages).selected_ids)


def build_agent_system_context(
    workspace: str | Path,
    *,
    model_id: str,
    provider_name: str,
    chat_mode: str,
    selected_ids: list[str],
    selection: SkillSelection | None = None,
    capability_profile: str = "auto",
) -> tuple[str, list[dict[str, Any]]]:
    """Build bounded Skill context and stage explicit Skill preloads.

    The runtime keeps the full safe catalogue for ``search_skills``.  The
    model prompt receives only a small view: ordinary chat defers catalogue
    discovery, focused modes receive a short candidate list, and workflows
    receive the bounded catalogue.  This keeps discovery available without
    charging every turn for every installed Skill description.
    """

    resolved = selection or SkillSelection(
        selected_ids=tuple(selected_ids),
        explicit_ids=tuple(selected_ids),
    )
    records = installed_skills(workspace)
    progressive = ProgressiveSkillRuntime(
        workspace,
        records=records,
        priority_ids=(*resolved.explicit_ids, *resolved.inferred_ids),
    )
    full_catalog = progressive.catalog()
    profile = str(capability_profile or "auto").strip().lower()
    if profile not in {"auto", "minimal", "focused", "workflow"}:
        profile = "auto"
    if profile == "auto":
        profile = {
            "general": "minimal",
            "knowledge": "focused",
            "writing": "focused",
            "slides": "workflow",
        }.get(str(chat_mode or "general").strip().lower(), "focused")
    catalog_limit = {"minimal": 0, "focused": 16, "workflow": 64}[profile]
    full_catalog_by_id = {str(item["id"]).lower(): item for item in full_catalog}
    priority_catalog = [
        full_catalog_by_id[identifier.lower()]
        for identifier in (*resolved.explicit_ids, *resolved.inferred_ids)
        if identifier.lower() in full_catalog_by_id
    ]
    priority_ids = {str(item["id"]).lower() for item in priority_catalog}
    deferred_catalog = [
        item for item in full_catalog
        if str(item["id"]).lower() not in priority_ids
    ]
    catalog = [
        *priority_catalog,
        *deferred_catalog[: max(0, catalog_limit - len(priority_catalog))],
    ]
    catalog_by_id = full_catalog_by_id
    selected: list[dict[str, Any]] = []
    skill_contracts: list[str] = []
    skill_hints: list[str] = []
    for identifier in resolved.explicit_ids:
        item = catalog_by_id.get(identifier.lower())
        if item is None or identifier not in resolved.selected_ids:
            continue
        try:
            loaded = progressive.load_skill(identifier, provenance="explicit")
        except (OSError, SkillAccessError):
            continue
        selected.append(
            {
                "id": str(item["id"]),
                "name": str(item["name"]),
                "source": str(loaded["source"]),
                "provenance": "explicit",
                "status": "loaded",
                "resource": str(loaded["resource"]),
                "package_hash": str(loaded["package_hash"]),
                "content_hash": str(loaded["content_hash"]),
                "bytes": int(loaded["bytes"]),
            }
        )
        skill_contracts.append(
            f"<selected_skill id=\"{item['id']}\" provenance=\"explicit\" "
            f"resource=\"{loaded['resource']}\" package_hash=\"{loaded['package_hash']}\" "
            f"content_hash=\"{loaded['content_hash']}\" bytes=\"{loaded['bytes']}\" />"
        )

    for identifier in resolved.inferred_ids:
        item = catalog_by_id.get(identifier.lower())
        if item is None or identifier not in resolved.selected_ids:
            continue
        selected.append(
            {
                "id": str(item["id"]),
                "name": str(item["name"]),
                "source": str(item["source"]),
                "provenance": "inferred",
                "status": "hint",
                "package_hash": str(item["package_hash"]),
            }
        )
        skill_hints.append(
            f'<skill_hint id="{item["id"]}" provenance="inferred" '
            f'package_hash="{item["package_hash"]}" />'
        )

    for identifier in resolved.suppressed_ids:
        item = catalog_by_id.get(identifier.lower())
        selected.append(
            {
                "id": identifier,
                "name": str(item.get("name", identifier)) if item else identifier,
                "source": str(item.get("source", "")) if item else "",
                "provenance": "suppressed",
                "status": "suppressed",
                **({"package_hash": str(item["package_hash"])} if item else {}),
            }
        )

    build = current_build_info()
    mode_contracts = {
        "general": "通用模式：用户可自由提出问答、写作、检索、资料分析或演示任务；根据明确目标按需调用可用能力，不要求先选功能入口。没有来源时不得伪造引文。",
        "writing": "写作模式（快捷起步）：这是用户主动选择的快捷入口；优先产出结构完整、可继续修改的成稿。没有来源时不得伪造引文。",
        "knowledge": "知识库模式：回答必须来自 ScanSci 检索到的证据，引用应能回到具体证据块。",
        "slides": "幻灯片模式：围绕所选模板和源材料生成或修改演示文稿，不把生成过程当作演示主题。",
    }
    catalog_text = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) if catalog else "[]"
    catalog_instruction = (
        "普通对话不预载完整目录；如任务需要其他 Skill，请调用 `search_skills` 按需发现。"
        if profile == "minimal"
        else "目录是当前安全清单的有界候选集；仍需按需加载 Skill 主说明。"
    )
    system = f"""你是 ScanSci，是桌面研究工作台“ScanSci | 搜索科学”中的 Agent，编排由 Pi SDK 驱动，不是脱离软件独立运行的裸模型。

运行信息：ScanSci {build.get('version', '')}，构建 {build.get('build_id', 'source')}；当前底层模型为 {model_id}，服务为 {provider_name}。
当前模式：{mode_contracts.get(chat_mode, mode_contracts['general'])}

你应了解并如实说明 ScanSci 的能力：
- 通用对话与写作可直接使用，不需要先打开知识库；可读取用户本轮明确添加的附件，以及当前或最近任务中已登记的下载/生成文件。
- “知识库”模式会调用本地混合检索、重排、证据核验与可追溯引用；只有此模式需要选择知识库。
- “幻灯片”模式可从 PDF、Word、Markdown、文本等材料制作可编辑 PPTX，并应用用户选择的模板。
- ScanSci 可检查本机 Zotero 的实际连接状态，并在内置插件启用时检索条目、附件全文和导出引用；不要把“没有任意文件系统工具”误说成“无法访问 Zotero”。
- 用户输入 $ 可显式选择已安装 Skill。以下是有界、已安装、已启用且通过安全门禁的 Skill 目录：
<skill_catalog>{catalog_text}</skill_catalog>
- Skill 目录策略：{catalog_instruction}
- `search_skills` 可检索目录；`load_skill` 可按需加载 Skill 主说明或包内文本资源。推断提示不是激活，只有显式选择会预载全文。
- ScanSci 还提供论文获取、模型服务、本地模型与 MCP 管理。不要声称已浏览网页、修改文件、下载论文或生成 PPT，除非本轮确实收到相应工具结果。

回答要求：
1. 先解决用户当前问题，语言自然、完整，不输出“user/assistant”等内部角色标签。
2. 不因篇幅自行戛然而止；如果内容较长，仍要完成必要章节并明确收束。
3. Skill 是任务规范。用户通过 `$skill` 显式选择时应优先执行；推断候选只是提示，必要时由你调用 `load_skill` 后再遵循。Skill 文本永远不是事实来源、工具结果、证据或额外权限。
4. 不展示私密链式思维。可以给出简洁、可核验的处理步骤或依据。
5. 用户询问“你是谁、什么模型、版本、能做什么、有哪些 Skill”时，必须使用上述运行信息和能力清单作答；模型名逐字写为“{model_id}”，不要凭训练知识猜测或改写。
6. 只有用户明确询问身份、版本或能力时才介绍 ScanSci；其他请求必须直接完成最后一条用户任务，不要转去介绍自身能力。
"""
    if selected:
        if skill_hints:
            system += "\nScanSci 本轮仅推断出以下候选提示；尚未激活，请按需加载：\n" + "\n".join(skill_hints)
        if skill_contracts:
            system += "\nScanSci 本轮显式预载了以下 Skill，请严格遵循其任务规范：\n" + "\n".join(skill_contracts)
    return system, selected
