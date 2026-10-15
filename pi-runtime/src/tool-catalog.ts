export type CatalogRisk = "read_only" | "reversible" | "high";
export type CatalogExecutionMode = "parallel" | "sequential";

export const DISCOVERY_DECISION_SCHEMA_VERSION = "scansci.discovery-decision.v1";
export const DEFAULT_DISCOVERY_THRESHOLD = 0.65;

export interface CatalogToolDefinition {
  name: string;
  label: string;
  description: string;
}

export interface ToolCatalogEntry {
  name: string;
  label: string;
  description: string;
  aliases: string[];
  tags: string[];
  group: string;
  risk: CatalogRisk;
  availability: "ready";
  executionMode: CatalogExecutionMode;
}

export interface ToolCatalogMatch extends ToolCatalogEntry {
  score: number;
  confidence: number;
  match_basis: string[];
  active: boolean;
}

export interface SkillCatalogEntry {
  id: string;
  name: string;
  description: string;
  source: string;
  package_hash: string;
}

const MAX_SKILL_CATALOG_ITEMS = 64;
const MAX_SKILL_CATALOG_BYTES = 16 * 1024;

export function boundedSkillCatalog(value: unknown): SkillCatalogEntry[] {
  if (!Array.isArray(value)) return [];
  const result: SkillCatalogEntry[] = [];
  const seen = new Set<string>();
  for (const raw of value.slice(0, MAX_SKILL_CATALOG_ITEMS)) {
    if (!raw || typeof raw !== "object") continue;
    const record = raw as Record<string, unknown>;
    const id = String(record.id || "").trim().toLowerCase().slice(0, 100);
    const packageHash = String(record.package_hash || "").slice(0, 80);
    const source = String(record.source || "").trim().slice(0, 500);
    const safeSource = (
      new RegExp(`^(?:builtin|installed):${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`).test(source)
      || /^https:\/\/[^\s]+$/i.test(source)
    );
    if (
      !/^[a-z0-9][a-z0-9._-]{0,99}$/.test(id)
      || seen.has(id)
      || !/^sha256:[a-f0-9]{64}$/.test(packageHash)
      || !safeSource
    ) continue;
    const item = {
      id,
      name: String(record.name || id).slice(0, 100),
      description: String(record.description || "").slice(0, 240),
      source,
      package_hash: packageHash,
    };
    const encoded = Buffer.byteLength(JSON.stringify([...result, item]), "utf8");
    if (encoded > MAX_SKILL_CATALOG_BYTES) break;
    seen.add(id);
    result.push(item);
  }
  return result;
}

const REVERSIBLE_TOOLS = new Set([
  "download_and_index",
  "create_document",
  "create_pdf",
  "create_spreadsheet",
  "create_presentation",
  "generate_image",
  "compile_latex",
  "edit_section",
  "edit_slide",
  "delegate_scientific_agents",
  "cancel_scientific_agents",
]);

// Keep this allow-list explicit: a read-only risk label does not prove that a
// composite, control, plugin, or deferred MCP implementation is thread-safe.
const THREAD_SAFE_READ_TOOLS = new Set([
  "inspect_available_tools",
  "zotero_status",
  "zotero_fulltext",
  "zotero_attachment",
  "zotero_export_bibtex",
  "zotero_citations",
  "obsidian_status",
  "obsidian_search",
  "obsidian_read",
  "obsidian_backlinks",
  "verify_doi",
  "search_web",
  "agent_reach",
  "search_journal",
  "audit_references",
  "list_scientific_agents",
  "collect_scientific_agents",
]);

export function executionModeForTool(
  name: string,
  risk: CatalogRisk,
): CatalogExecutionMode {
  return risk === "read_only" && THREAD_SAFE_READ_TOOLS.has(String(name || ""))
    ? "parallel"
    : "sequential";
}

const TOOL_GROUPS: Record<string, string> = {
  inspect_workspace: "workspace",
  inspect_available_tools: "workspace",
  read_task_documents: "documents",
  summarize_documents: "documents",
  check_task_completion: "documents",
  search_local_evidence: "evidence",
  build_verified_answer: "evidence",
  kb_search: "knowledge",
  zotero_search: "zotero",
  zotero_status: "zotero",
  zotero_fulltext: "zotero",
  zotero_attachment: "zotero",
  zotero_export_bibtex: "zotero",
  zotero_citations: "zotero",
  obsidian_status: "obsidian",
  obsidian_search: "obsidian",
  obsidian_read: "obsidian",
  obsidian_backlinks: "obsidian",
  verify_doi: "scholarly",
  discover_papers: "scholarly",
  search_journal: "scholarly",
  audit_references: "scholarly",
  search_web: "web",
  agent_reach: "web",
  browser_access: "web",
  download_and_index: "acquisition",
  build_presentation_outline: "artifacts",
  create_document: "artifacts",
  create_pdf: "artifacts",
  create_spreadsheet: "artifacts",
  create_presentation: "artifacts",
  generate_image: "artifacts",
  compile_latex: "artifacts",
  edit_section: "artifacts",
  edit_slide: "artifacts",
  delegate_scientific_agents: "scientific-agents",
  list_scientific_agents: "scientific-agents",
  collect_scientific_agents: "scientific-agents",
  cancel_scientific_agents: "scientific-agents",
  self_assess: "control",
};

const TOOL_ALIASES: Record<string, string[]> = {
  inspect_workspace: ["workspace status", "library status", "工作区", "资料库状态"],
  inspect_available_tools: ["capabilities", "tool inventory", "能力目录", "工具清单"],
  search_local_evidence: ["local search", "evidence search", "本地证据", "知识库检索"],
  build_verified_answer: ["grounded answer", "citation answer", "核验回答", "引文回答"],
  discover_papers: ["academic search", "paper search", "论文发现", "学术检索"],
  search_web: ["internet search", "public web", "联网搜索", "网络检索"],
  agent_reach: ["public url", "rss", "github", "公开网页", "频道检索"],
  browser_access: ["rendered page", "logged in browser", "浏览器", "动态网页"],
  download_and_index: ["fetch papers", "acquire full text", "下载论文", "建立索引"],
  create_presentation: ["pptx", "slides", "演示文稿", "幻灯片"],
  generate_image: ["text to image", "image generation", "illustration", "文生图", "生图", "生成图片"],
};

function normalize(value: unknown): string {
  return String(value || "").trim().toLocaleLowerCase();
}

function catalogRisk(name: string): CatalogRisk {
  if (name.startsWith("mcp__")) return "high";
  return REVERSIBLE_TOOLS.has(name) ? "reversible" : "read_only";
}

function groupFor(name: string): string {
  if (name.startsWith("mcp__")) return "mcp";
  return TOOL_GROUPS[name] || "scansci";
}

function tokensFor(entry: ToolCatalogEntry): string[] {
  return [
    entry.name,
    entry.label,
    entry.description,
    ...entry.aliases,
    ...entry.tags,
    entry.group,
    entry.risk,
    entry.availability,
  ].map(normalize).filter(Boolean);
}

export function buildToolCatalog(
  definitions: CatalogToolDefinition[],
  riskResolver: (name: string) => CatalogRisk = catalogRisk,
): ToolCatalogEntry[] {
  return definitions
    .map((definition) => {
      const name = String(definition.name || "").trim();
      const risk = riskResolver(name);
      const group = groupFor(name);
      const aliases = [...new Set([
        name.replace(/_/g, " "),
        String(definition.label || "").trim(),
        ...(TOOL_ALIASES[name] || []),
      ].filter(Boolean))];
      return {
        name,
        label: String(definition.label || name),
        description: String(definition.description || ""),
        aliases,
        tags: [...new Set([group, risk, name.split("_")[0]].filter(Boolean))],
        group,
        risk,
        availability: "ready" as const,
        executionMode: executionModeForTool(name, risk),
      };
    })
    .filter((entry) => Boolean(entry.name))
    .sort((left, right) => left.name.localeCompare(right.name));
}

function matchEvidence(entry: ToolCatalogEntry, query: string): { score: number; basis: string[] } {
  const needle = normalize(query);
  if (!needle) return { score: 1, basis: ["catalog"] };
  const fields = tokensFor(entry);
  if (normalize(entry.name) === needle) return { score: 1_000, basis: ["exact_name"] };
  if (entry.aliases.some((alias) => normalize(alias) === needle)) {
    return { score: 900, basis: ["exact_alias"] };
  }
  const words = needle.split(/\s+/).filter(Boolean);
  let score = 0;
  const basis = new Set<string>();
  for (const field of fields) {
    if (field.includes(needle)) {
      score = Math.max(score, 500);
      basis.add("phrase_contains");
    }
    const hits = words.filter((word) => field.includes(word)).length;
    if (hits) {
      score = Math.max(score, hits * 100);
      basis.add("token_overlap");
    }
  }
  return { score, basis: [...basis] };
}

function confidenceForScore(score: number, explicitNames: boolean): number {
  if (explicitNames) return 1;
  if (score >= 1_000) return 0.99;
  if (score >= 900) return 0.95;
  if (score >= 500) return 0.86;
  if (score >= 200) return 0.72;
  if (score >= 100) return 0.58;
  return 0;
}

export function normalizeDiscoveryThreshold(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? Math.max(0, Math.min(1, numeric))
    : DEFAULT_DISCOVERY_THRESHOLD;
}

export function searchToolCatalog(
  catalog: ToolCatalogEntry[],
  options: {
    query?: unknown;
    names?: unknown;
    limit?: unknown;
    activeNames?: Iterable<string>;
    isAuthorized?: (name: string) => boolean;
  } = {},
): { matches: ToolCatalogMatch[]; rejected: Array<{ name: string; reason: string }> } {
  const authorized = options.isAuthorized || (() => true);
  const active = new Set(options.activeNames || []);
  const requestedNames = Array.isArray(options.names)
    ? [...new Set(options.names.map(String).map((name) => name.trim()).filter(Boolean))]
    : [];
  const limitValue = Number(options.limit);
  const limit = Number.isFinite(limitValue) ? Math.max(1, Math.min(20, Math.floor(limitValue))) : 8;
  const byName = new Map(catalog.map((entry) => [entry.name, entry]));
  const rejected: Array<{ name: string; reason: string }> = [];

  let candidates: ToolCatalogEntry[];
  if (requestedNames.length) {
    candidates = [];
    for (const name of requestedNames) {
      const entry = byName.get(name);
      if (!entry || !authorized(name)) rejected.push({ name, reason: "not_authorized_or_unavailable" });
      else candidates.push(entry);
    }
  } else {
    candidates = catalog.filter((entry) => authorized(entry.name));
  }
  const query = String(options.query || "");
  const explicitNames = requestedNames.length > 0;
  const matches = candidates
    .map((entry) => {
      const evidence = matchEvidence(entry, query);
      return {
        ...entry,
        score: evidence.score,
        confidence: confidenceForScore(evidence.score, explicitNames),
        match_basis: evidence.basis,
        active: active.has(entry.name),
      };
    })
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score || left.name.localeCompare(right.name))
    .slice(0, limit);
  return { matches, rejected };
}

export function initialToolNames(
  registeredNames: Iterable<string>,
  initialNames: Iterable<string>,
  bootstrapNames: Iterable<string>,
): string[] {
  const registered = new Set(registeredNames);
  const selected = new Set<string>();
  for (const name of bootstrapNames) if (registered.has(name)) selected.add(name);
  for (const name of initialNames) if (registered.has(name)) selected.add(name);
  return [...selected].sort();
}
