# ScanSci Evidence 插件

`plugins/scansci-evidence` 是 ScanSci Harness 证据检索能力的公开插件包。
它把模型侧的检索规则、Host 工具契约和结果边界校验放在一个小目录里，
便于其他 Harness 复用。

## 交付内容

- `skills/evidence-retrieval/SKILL.md`：模型只在需要本地证据时加载的检索规则；
- `skills/evidence-retrieval/references/host-tool-contract.md`：跨 Host 的语义工具契约；
- `scripts/normalize_evidence.py`：只使用 Python 标准库的结果 envelope 校验器；
- `src/scansci_html/builtin_skill_assets/evidence-retrieval/`：桌面版内置副本。

Skill 只改变指令，插件脚本只校验和规整结果。Workspace、Evidence Store、
证据 ID、HTML anchor、引用验证、权限、密钥和持久化都仍由 Host 管理。
JEV 是可选的 Host 侧语义排序/覆盖判断阶段；没有配置或服务失败时，
`not_run`/`degraded` 必须保留，并继续使用本地检索结果。

## 兼容 Host 的最小接口

Host 至少提供只读 `search_local_evidence`：

```json
{
  "query": "focused question",
  "result_limit": 6
}
```

返回的每个候选应包含 `evidence_id`、`doc_id`、`matched_text` 和可用的
`html_anchor`。可选的 `build_verified_answer` 用于由 Host 完成引用后处理。
没有稳定 ID 或原文片段的候选不能直接变成引用。

## 开源边界

插件使用 MIT 许可；其中没有 API key、Cookie、用户文献或 JEV 网络客户端。
发布或复用时应把实际服务凭据放在目标 Host 的密钥链路中，并按目标项目的
许可证审查整合代码。
