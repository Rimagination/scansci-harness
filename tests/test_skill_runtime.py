import json
from pathlib import Path

import pytest

from scansci_html.agent_context import build_agent_system_context, selected_skill_ids
from scansci_html.skill_runtime import infer_research_skill, resolve_skill_selection


def test_runtime_infers_one_most_specific_research_skill() -> None:
    messages = [{"role": "user", "content": "请逐条回复审稿人的意见，并标出每项修改"}]

    selection = resolve_skill_selection({}, messages)

    assert selection.selected_ids == ("nature-response",)
    assert selection.inferred_ids == ("nature-response",)
    assert selection.explicit_ids == ()
    assert selected_skill_ids({}, messages) == ["nature-response"]


def test_explicit_selection_wins_and_concrete_skill_suppresses_suite() -> None:
    payload = {"skills": ["academic-research-suite", "nature-writing"]}
    messages = [{"role": "user", "content": "$nature-statistics 请检查结果段"}]

    selection = resolve_skill_selection(payload, messages)

    assert selection.selected_ids == ("nature-writing", "nature-statistics")
    assert selection.explicit_ids == ("nature-writing", "nature-statistics")
    assert selection.inferred_ids == ()
    assert selection.suppressed_ids == ("academic-research-suite",)


def test_existing_scan_sci_phase_skill_also_suppresses_the_suite_router() -> None:
    selection = resolve_skill_selection(
        {"skills": ["academic-research-suite", "good-question"]},
        [{"role": "user", "content": "帮我收束研究问题"}],
    )

    assert selection.selected_ids == ("good-question",)
    assert selection.suppressed_ids == ("academic-research-suite",)


def test_explicit_skill_prevents_a_second_automatic_contract() -> None:
    selection = resolve_skill_selection(
        {"skills": ["nature-writing"]},
        [{"role": "user", "content": "请润色这段论文讨论"}],
    )

    assert selection.selected_ids == ("nature-writing",)
    assert "nature-polishing" not in selection.selected_ids


def test_automatic_selection_can_be_disabled_per_request() -> None:
    selection = resolve_skill_selection(
        {"auto_select_skills": False},
        [{"role": "user", "content": "请润色这段英文摘要"}],
    )

    assert selection.selected_ids == ()


def test_inference_covers_research_pipeline_entrypoints() -> None:
    assert infer_research_skill("帮我检索近五年的土壤微生物论文") == "nature-academic-search"
    assert infer_research_skill("请写一篇关于城市热岛的文献综述") == "literature-review"
    assert infer_research_skill("从选题到投稿，帮我规划科研全流程") == "academic-research-suite"


def _skill_record(
    root: Path,
    identifier: str,
    *,
    text: str = "# Instructions\n\nUse the supplied evidence.\n",
    enabled: bool = True,
    builtin: bool = True,
    verdict: str = "SAFE",
) -> dict[str, object]:
    package = root / identifier
    package.mkdir(parents=True, exist_ok=True)
    skill_file = package / "SKILL.md"
    skill_file.write_text(text, encoding="utf-8")
    record: dict[str, object] = {
        "id": identifier,
        "name": identifier.replace("-", " ").title(),
        "description": f"Progressive instructions for {identifier}",
        "enabled": enabled,
        "available": True,
        "builtin": builtin,
        "package_path": str(package),
        "skill_file": str(skill_file),
        "source_type": "builtin" if builtin else "local",
        "source": "ScanSci" if builtin else str(package),
    }
    if not builtin:
        record["security_scan"] = {
            "verdict": verdict,
            "fingerprint": "sha256:test-fixture",
        }
    return record


def test_explicit_skill_stages_preload_metadata_with_hash_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_context as agent_context

    record = _skill_record(tmp_path, "explicit-helper", text="# Explicit sentinel\n\nDo the exact workflow.\n")
    monkeypatch.setattr(agent_context, "installed_skills", lambda _workspace: [record])
    selection = resolve_skill_selection(
        {"skills": ["explicit-helper"]},
        [{"role": "user", "content": "$explicit-helper run it"}],
    )

    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )

    assert '<selected_skill id="explicit-helper"' in system
    assert "Explicit sentinel" not in system
    assert 'resource="SKILL.md"' in system
    assert 'bytes="47" />' in system
    assert selected == [
        {
            "id": "explicit-helper",
            "name": "Explicit Helper",
            "source": "builtin:explicit-helper",
            "provenance": "explicit",
            "status": "loaded",
            "resource": "SKILL.md",
            "package_hash": selected[0]["package_hash"],
            "content_hash": selected[0]["content_hash"],
            "bytes": len((tmp_path / "explicit-helper" / "SKILL.md").read_bytes()),
        }
    ]
    assert str(selected[0]["package_hash"]).startswith("sha256:")
    assert str(selected[0]["content_hash"]).startswith("sha256:")


def test_inferred_skill_is_only_a_progressive_hint_not_full_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_context as agent_context

    record = _skill_record(tmp_path, "nature-response", text="# PRIVATE INFERRED SENTINEL\n")
    monkeypatch.setattr(agent_context, "installed_skills", lambda _workspace: [record])
    selection = resolve_skill_selection(
        {},
        [{"role": "user", "content": "请逐条回复审稿人的意见"}],
    )

    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )

    assert "PRIVATE INFERRED SENTINEL" not in system
    assert '<selected_skill id="nature-response"' not in system
    assert '<skill_hint id="nature-response" provenance="inferred"' in system
    assert selected[0]["provenance"] == "inferred"
    assert selected[0]["status"] == "hint"
    assert "content_hash" not in selected[0]


def test_general_chat_defers_unselected_skill_catalog_to_search_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_context as agent_context

    records = [
        _skill_record(tmp_path, f"skill-{index:02d}", text="# safe\n")
        for index in range(20)
    ]
    monkeypatch.setattr(agent_context, "installed_skills", lambda _workspace: records)

    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=[],
    )

    assert selected == []
    assert "普通对话不预载完整目录" in system
    assert "skill-00" not in system
    assert "<skill_catalog>[]</skill_catalog>" in system


def test_skill_catalog_is_bounded_and_excludes_disabled_or_uncleared_packages(
    tmp_path: Path,
) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    records = [
        _skill_record(tmp_path, f"safe-{index:02d}", text="# safe\n")
        for index in range(70)
    ]
    records.extend(
        [
            _skill_record(tmp_path, "disabled", enabled=False),
            {**_skill_record(tmp_path, "uninstalled"), "uninstalled": True},
            _skill_record(tmp_path, "blocked", builtin=False, verdict="BLOCKED"),
            {
                **_skill_record(tmp_path, "unscanned", builtin=False),
                "security_scan": {},
            },
        ]
    )

    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=records)
    catalog = runtime.catalog()

    assert len(catalog) == 64
    assert len(json.dumps(catalog, ensure_ascii=False).encode("utf-8")) <= 16 * 1024
    assert {item["id"] for item in catalog}.isdisjoint(
        {"disabled", "uninstalled", "blocked", "unscanned"}
    )
    assert all(set(item) <= {"id", "name", "description", "source", "package_hash"} for item in catalog)


def test_search_skills_matches_compact_catalog_and_clamps_limit(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    records = [
        _skill_record(tmp_path, "statistics-audit"),
        _skill_record(tmp_path, "literature-map"),
        _skill_record(tmp_path, "reviewer-response"),
    ]
    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=records)

    result = runtime.search_skills("statistical statistics", limit=999)

    assert result["count"] == 1
    assert result["skills"][0]["id"] == "statistics-audit"
    assert result["limit"] == 20


@pytest.mark.parametrize(
    "resource",
    [
        "../outside.md",
        "references/../../outside.md",
        "/etc/passwd",
        r"C:\\Windows\\win.ini",
        r"\\\\server\\share\\secret.txt",
        "file:///etc/passwd",
        "references/bad\x00name.md",
    ],
)
def test_load_skill_rejects_traversal_absolute_uri_unc_and_nul(
    tmp_path: Path,
    resource: str,
) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "safe-reader")
    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])

    with pytest.raises(SkillAccessError, match="resource|path|路径|资源"):
        runtime.load_skill("safe-reader", resource=resource)


def test_load_skill_rejects_symlink_escape_and_non_text_resource(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "safe-reader")
    package = Path(str(record["package_path"]))
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    link = package / "references" / "escape.md"
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    (package / "references" / "binary.bin").write_bytes(b"\x00\x01\x02")
    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])

    with pytest.raises(SkillAccessError, match="symlink|escape|符号|边界"):
        runtime.load_skill("safe-reader", resource="references/escape.md")
    with pytest.raises(SkillAccessError, match="text|文本"):
        runtime.load_skill("safe-reader", resource="references/binary.bin")


def test_load_skill_enforces_individual_and_cumulative_byte_limits(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "bounded", text="1234567890")
    package = Path(str(record["package_path"]))
    (package / "one.md").write_text("abcdefghij", encoding="utf-8")
    (package / "two.md").write_text("klmnopqrst", encoding="utf-8")
    (package / "oversized.md").write_text("x" * 13, encoding="utf-8")
    runtime = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        max_resource_bytes=12,
        max_total_bytes=20,
    )

    first = runtime.load_skill("bounded", resource="one.md", provenance="model")
    assert first["bytes"] == 10
    assert first["provenance"] == "model"
    with pytest.raises(SkillAccessError, match="individual|单个|12"):
        runtime.load_skill("bounded", resource="oversized.md")
    runtime.load_skill("bounded", resource="two.md")
    with pytest.raises(SkillAccessError, match="cumulative|累计|20"):
        runtime.load_skill("bounded")


def test_load_and_restore_use_bounded_resource_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_skill_tools as skill_tools
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    record = _skill_record(tmp_path, "bounded-read", text="# bounded read\n")
    package = Path(str(record["package_path"]))
    snapshot = skill_tools._package_snapshot(package)
    seed = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    state = seed.state()
    state["loaded"] = [
        {
            key: value
            for key, value in seed.load_skill("bounded-read", provenance="explicit").items()
            if key != "content"
        }
    ]

    monkeypatch.setattr(skill_tools, "_package_snapshot", lambda _root: snapshot)
    read_sizes: list[int] = []
    original_open = Path.open

    class ObservedBinaryFile:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._handle.__exit__(exc_type, exc_value, traceback)

        def read(self, size: int = -1):
            read_sizes.append(size)
            return self._handle.read(size)

    def observed_open(path: Path, *args, **kwargs):
        return ObservedBinaryFile(original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", observed_open)
    fresh = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    fresh.load_skill("bounded-read")
    ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        restored_state=state,
    )

    assert read_sizes == [
        skill_tools.MAX_SKILL_RESOURCE_BYTES + 1,
        skill_tools.MAX_SKILL_RESOURCE_BYTES + 1,
    ]


def test_oversized_load_and_restore_fail_from_stat_without_opening_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_skill_tools as skill_tools
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "oversized-read", text="x" * 13)
    package = Path(str(record["package_path"]))
    snapshot = skill_tools._package_snapshot(package)
    seed = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    loaded = seed.load_skill("oversized-read", provenance="explicit")
    restored_state = {
        "schema": skill_tools.SKILL_STATE_SCHEMA,
        "loaded": [{key: value for key, value in loaded.items() if key != "content"}],
    }

    monkeypatch.setattr(skill_tools, "_package_snapshot", lambda _root: snapshot)

    def forbidden_open(_path: Path, *_args, **_kwargs):
        raise AssertionError("oversized Skill resource was opened instead of rejected by stat")

    monkeypatch.setattr(Path, "open", forbidden_open)
    runtime = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        max_resource_bytes=12,
    )
    with pytest.raises(SkillAccessError, match="individual|12"):
        runtime.load_skill("oversized-read")

    restored = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        restored_state=restored_state,
        max_resource_bytes=12,
    )
    assert restored.state()["loaded"] == []


def test_loaded_skill_state_restores_stable_hashes_without_content(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    record = _skill_record(tmp_path, "stable", text="# Stable hash sentinel\n")
    first_runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    first_loaded = first_runtime.load_skill("stable", provenance="explicit")
    state = first_runtime.state()

    assert all("content" not in item for item in state["loaded"])
    resumed = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        restored_state=state,
    )
    second_loaded = resumed.load_skill("stable", provenance="resume")

    assert second_loaded["package_hash"] == first_loaded["package_hash"]
    assert second_loaded["content_hash"] == first_loaded["content_hash"]
    assert resumed.state()["loaded"] == state["loaded"]


def test_load_skill_rejects_content_changed_after_security_snapshot(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "snapshot-bound", text="# Cleared snapshot\n")
    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    original_package_hash = runtime.catalog()[0]["package_hash"]

    Path(str(record["skill_file"])).write_text("# Replaced after catalog\n", encoding="utf-8")

    with pytest.raises(SkillAccessError, match="snapshot|hash|changed|fingerprint"):
        runtime.load_skill("snapshot-bound")
    assert runtime.catalog()[0]["package_hash"] == original_package_hash


def test_duplicate_load_is_metadata_only_and_instruction_calls_are_bounded(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime, SkillAccessError

    record = _skill_record(tmp_path, "deduplicated", text="# One bounded transmission\n")
    runtime = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        max_instruction_calls=5,
    )

    first = runtime.load_skill("deduplicated")
    repeats = [runtime.load_skill("deduplicated") for _ in range(4)]

    assert first["content"].splitlines() == ["# One bounded transmission"]
    assert all(item["already_loaded"] is True for item in repeats)
    assert all("content" not in item for item in repeats)
    assert len(runtime.state()["loaded"]) == 1
    with pytest.raises(SkillAccessError, match="call|operation|instruction"):
        runtime.load_skill("deduplicated")


def test_https_skill_source_with_credentials_is_not_exposed(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime
    from scansci_html.skill_security import scan_skill_packages

    source = "https://user:secret@example.test/repo.git?access_token=top-secret#fragment"
    record = _skill_record(tmp_path, "credential-source", builtin=False)
    package = Path(str(record["package_path"]))
    record["source_type"] = "git"
    record["source"] = source
    record["security_scan"] = scan_skill_packages([package], source_type="git", source=source)

    runtime = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    catalog = runtime.catalog()
    loaded = runtime.load_skill("credential-source")

    assert catalog[0]["source"] == "installed:credential-source"
    assert loaded["source"] == "installed:credential-source"
    assert "secret" not in json.dumps({"catalog": catalog, "loaded": loaded}, ensure_ascii=False)


def test_explicit_skill_is_prioritized_into_bounded_catalog_and_preloaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_context as agent_context
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    records = [
        _skill_record(tmp_path, f"safe-{index:02d}", text=f"# safe {index}\n")
        for index in range(65)
    ]
    explicit_id = "safe-64"
    runtime = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=records,
        priority_ids=[explicit_id],
    )
    assert explicit_id in {item["id"] for item in runtime.catalog()}

    monkeypatch.setattr(agent_context, "installed_skills", lambda _workspace: records)
    selection = resolve_skill_selection(
        {"skills": [explicit_id]},
        [{"role": "user", "content": f"${explicit_id} run it"}],
    )
    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )

    assert "# safe 64" not in system
    assert f'<selected_skill id="{explicit_id}" provenance="explicit"' in system
    assert selected[0]["id"] == explicit_id
    assert selected[0]["status"] == "loaded"


def test_trusted_restore_does_not_consume_model_instruction_call_budget(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    record = _skill_record(tmp_path, "resume-budget", text="# primary\n")
    package = Path(str(record["package_path"]))
    (package / "one.md").write_text("# one\n", encoding="utf-8")
    (package / "two.md").write_text("# two\n", encoding="utf-8")
    restored_items = []
    for resource in ("SKILL.md", "one.md", "two.md"):
        one_resource_runtime = ProgressiveSkillRuntime(
            tmp_path / "workspace.sqlite",
            records=[record],
        )
        loaded = one_resource_runtime.load_skill("resume-budget", resource=resource)
        restored_items.append({key: value for key, value in loaded.items() if key != "content"})

    resumed = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        restored_state={"schema": "scansci.skill-state.v1", "loaded": restored_items},
        max_instruction_calls=2,
    )
    restored = [
        resumed.restore_skill("resume-budget", resource=resource)
        for resource in ("SKILL.md", "one.md", "two.md")
    ]

    assert all("content" in item for item in restored)
    assert resumed.search_skills("resume-budget")["count"] == 1
    duplicate = resumed.load_skill("resume-budget", resource="one.md")
    assert duplicate["already_loaded"] is True


def test_inferred_skill_is_prioritized_into_bounded_catalog_as_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scansci_html.agent_context as agent_context

    records = [
        _skill_record(tmp_path, f"aaa-{index:02d}", text=f"# filler {index}\n")
        for index in range(64)
    ]
    records.append(_skill_record(tmp_path, "nature-response", text="# inferred priority sentinel\n"))
    monkeypatch.setattr(agent_context, "installed_skills", lambda _workspace: records)
    selection = resolve_skill_selection(
        {},
        [{"role": "user", "content": "璇烽€愭潯鍥炲瀹＄浜虹殑鎰忚"}],
    )

    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )

    selection = resolve_skill_selection(
        {},
        [{"role": "user", "content": "Draft a response to the reviewer comments."}],
    )
    system, selected = build_agent_system_context(
        tmp_path / "workspace.sqlite",
        model_id="fixture",
        provider_name="fixture",
        chat_mode="general",
        selected_ids=list(selection.selected_ids),
        selection=selection,
    )
    assert '<skill_hint id="nature-response" provenance="inferred"' in system
    assert "inferred priority sentinel" not in system
    assert selected[0]["id"] == "nature-response"
    assert selected[0]["status"] == "hint"


def test_current_priority_keeps_first_position_when_persisted_ids_repeat_it(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    records = [
        _skill_record(tmp_path, f"safe-{index:02d}", text=f"# safe {index}\n")
        for index in range(65)
    ]
    runtime = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=records,
        priority_ids=["safe-64", *(f"safe-{index:02d}" for index in range(64)), "safe-64"],
    )

    assert runtime.catalog()[0]["id"] == "safe-64"
    assert len(runtime.catalog()) == 64


def test_resume_revokes_disabled_or_hash_changed_skill_state(tmp_path: Path) -> None:
    from scansci_html.agent_skill_tools import ProgressiveSkillRuntime

    record = _skill_record(tmp_path, "revoked-on-resume", text="# cleared\n")
    initial = ProgressiveSkillRuntime(tmp_path / "workspace.sqlite", records=[record])
    initial.load_skill("revoked-on-resume", provenance="explicit")
    state = initial.state()

    disabled = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[{**record, "enabled": False}],
        restored_state=state,
        priority_ids=["revoked-on-resume"],
    )
    assert disabled.catalog() == []
    assert disabled.state()["loaded"] == []

    Path(str(record["skill_file"])).write_text("# changed after persistence\n", encoding="utf-8")
    changed = ProgressiveSkillRuntime(
        tmp_path / "workspace.sqlite",
        records=[record],
        restored_state=state,
        priority_ids=["revoked-on-resume"],
    )
    assert changed.catalog()[0]["id"] == "revoked-on-resume"
    assert changed.state()["loaded"] == []
