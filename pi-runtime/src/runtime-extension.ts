import {
  defineTool,
  type BeforeAgentStartEvent,
  type BeforeAgentStartEventResult,
  type BeforeProviderRequestEvent,
  type ContextEvent,
  type ContextEventResult,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  estimatePiImageTokens,
  type ModelRuntimeDescriptor,
  type PiImageContent,
} from "./multimodal.js";
import {
  DEFAULT_DISCOVERY_THRESHOLD,
  DISCOVERY_DECISION_SCHEMA_VERSION,
  normalizeDiscoveryThreshold,
  searchToolCatalog,
  type ToolCatalogEntry,
} from "./tool-catalog.js";
import { conservativeTextTokens } from "./token-estimate.js";

type JsonRecord = Record<string, unknown>;

export interface ContextViewResult {
  messages: ContextEvent["messages"];
  report?: JsonRecord;
}

export class ContextEnvelopeError extends Error {
  readonly code = "SCANSCI_CONTEXT_MANDATORY_OVERFLOW";

  constructor(message: string) {
    super(message);
    this.name = "ContextEnvelopeError";
  }
}

function contextSafeValue(value: unknown, depth = 0): unknown {
  if (depth > 32) return "[NESTED CONTENT]";
  if (Array.isArray(value)) return value.map((item) => contextSafeValue(item, depth + 1));
  if (!value || typeof value !== "object") return value;
  const record = value as JsonRecord;
  if (record.type === "image") {
    return { type: "image", mimeType: String(record.mimeType || ""), data: "[IMAGE BYTES]" };
  }
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [
      key,
      key === "data" && typeof item === "string" ? "[BINARY DATA]" : contextSafeValue(item, depth + 1),
    ]),
  );
}

function collectContextImages(value: unknown, images: PiImageContent[], depth = 0): void {
  if (depth > 32 || value === null || value === undefined) return;
  if (Array.isArray(value)) {
    for (const item of value) collectContextImages(item, images, depth + 1);
    return;
  }
  if (typeof value !== "object") return;
  const record = value as JsonRecord;
  if (record.type === "image" && typeof record.data === "string" && typeof record.mimeType === "string") {
    images.push({
      type: "image",
      data: record.data,
      mimeType: record.mimeType as PiImageContent["mimeType"],
    });
    return;
  }
  for (const item of Object.values(record)) collectContextImages(item, images, depth + 1);
}

function conservativeContextTokens(value: unknown, descriptor?: ModelRuntimeDescriptor): number {
  const text = JSON.stringify(contextSafeValue(value));
  const images: PiImageContent[] = [];
  collectContextImages(value, images);
  let visualTokens = 0;
  try {
    visualTokens = estimatePiImageTokens(images);
  } catch {
    visualTokens = images.length * 1200;
  }
  return conservativeTextTokens(text, descriptor) + visualTokens + 6;
}

export function buildTokenEnvelopeContextView(
  messages: ContextEvent["messages"],
  runtime: number | ModelRuntimeDescriptor,
  providerPrefixTokens = 0,
): ContextViewResult {
  const clean = structuredClone(messages);
  const descriptor = typeof runtime === "number" ? undefined : runtime;
  const inputLimit = typeof runtime === "number" ? runtime : runtime.provider_input_tokens;
  const limit = Math.max(4096, Math.floor(Number(inputLimit) || 0));
  const prefixTokens = Math.max(0, Math.floor(Number(providerPrefixTokens) || 0));
  const finalUserIndex = (() => {
    for (let index = clean.length - 1; index >= 0; index -= 1) {
      const role = String((clean[index] as unknown as JsonRecord).role || "").toLowerCase();
      if (role === "user") return index;
    }
    return -1;
  })();
  if (finalUserIndex < 0) return { messages: clean, report: { token_envelope: "no_user_message" } };
  const originalTokens = clean.reduce(
    (total, message) => total + conservativeContextTokens(message, descriptor),
    0,
  );
  // The final user request and every assistant/tool message emitted after it
  // form the current active turn.  A provider must never see a tool result
  // without the assistant call that produced it (or vice versa).
  const currentTurnIndices = Array.from(
    { length: clean.length - finalUserIndex },
    (_value, offset) => finalUserIndex + offset,
  );
  const mandatoryTokens = currentTurnIndices.reduce(
    (total, index) => total + conservativeContextTokens(clean[index], descriptor),
    0,
  );
  if (mandatoryTokens + prefixTokens > limit) {
    throw new ContextEnvelopeError("Current active tool turn exceeds the provider input token limit");
  }

  const units: Array<{ indices: number[]; priority: number; recency: number }> = [];
  let turn = 0;
  const dialogue = new Map<number, number[]>();
  for (let index = 0; index < finalUserIndex; index += 1) {
    const message = clean[index] as unknown as JsonRecord;
    const role = String(message.role || "").toLowerCase();
    if (role === "user") turn += 1;
    if (["tool", "toolresult", "tool_result"].includes(role) && turn === 0) {
      units.push({ indices: [index], priority: 1, recency: index });
    } else {
      const indices = dialogue.get(turn) || [];
      indices.push(index);
      dialogue.set(turn, indices);
    }
  }
  for (const indices of dialogue.values()) {
    if (indices.length) units.push({ indices, priority: 4, recency: Math.max(...indices) });
  }
  units.sort((left, right) => right.priority - left.priority || right.recency - left.recency);

  const retained = new Map<number, ContextEvent["messages"][number]>(
    currentTurnIndices.map((index) => [index, clean[index]]),
  );
  let used = mandatoryTokens + prefixTokens;
  let omitted = 0;
  for (const unit of units) {
    const tokens = unit.indices.reduce(
      (total, index) => total + conservativeContextTokens(clean[index], descriptor),
      0,
    );
    if (used + tokens <= limit) {
      for (const index of unit.indices) retained.set(index, clean[index]);
      used += tokens;
      continue;
    }
    omitted += unit.indices.length;
  }
  const output = [...retained.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, message]) => message) as ContextEvent["messages"];
  return {
    messages: output,
    report: {
      token_envelope: "model_aware_token_envelope_v1",
      provider_input_tokens: limit,
      provider_prefix_tokens: prefixTokens,
      estimated_tokens_before: originalTokens,
      estimated_tokens: used,
      retained_messages: output.length,
      omitted_messages: omitted,
    },
  };
}

export function buildNonDestructiveContextView(
  messages: ContextEvent["messages"],
  keepRecentTurns = 2,
): ContextViewResult {
  const output = structuredClone(messages);
  let currentTurn = 0;
  const locations: Array<{ index: number; turn: number }> = [];
  for (let index = 0; index < output.length; index += 1) {
    const message = output[index] as unknown as JsonRecord;
    const role = String(message.role || "").toLowerCase();
    if (role === "user") currentTurn += 1;
    if (["tool", "toolresult", "tool_result"].includes(role)) locations.push({ index, turn: currentTurn });
  }
  let pruned = 0;
  let originalChars = 0;
  let retainedChars = 0;
  for (const location of locations) {
    const message = output[location.index] as unknown as JsonRecord;
    const encoded = JSON.stringify(message.content ?? "");
    const size = encoded.length;
    originalChars += size;
    if (currentTurn - location.turn >= Math.max(1, keepRecentTurns)) {
      const notice = {
        _scansci_pruned: true,
        tool: String(message.toolName || message.tool_name || message.name || "tool").slice(0, 80),
        original_chars: size,
        notice: "Stale tool output was pruned in the provider context view; rerun a focused tool if needed.",
      };
      message.content = [{ type: "text", text: JSON.stringify(notice) }];
      message._scansci_context_pruned = true;
      pruned += 1;
    }
    retainedChars += JSON.stringify(message.content ?? "").length;
  }
  return {
    messages: output,
    report: {
      policy: "non_destructive_stale_tool_result_view",
      examined_tool_results: locations.length,
      pruned_tool_results: pruned,
      preserved_tool_results: Math.max(0, locations.length - pruned),
      original_chars: originalChars,
      retained_chars: retainedChars,
      saved_chars: Math.max(0, originalChars - retainedChars),
    },
  };
}

export interface RuntimeLifecycleOptions {
  current: () => { request_id?: unknown; session_id?: unknown };
  emit: (payload: JsonRecord) => void;
  beforeAgentStart?: (event: BeforeAgentStartEvent) => BeforeAgentStartEventResult | void;
  context?: (event: ContextEvent) => ContextViewResult | ContextEventResult | void;
  beforeProviderRequest?: (event: BeforeProviderRequestEvent) => unknown;
  onContextReport?: (report: JsonRecord) => void;
}

/**
 * Register authority-neutral lifecycle hooks with bounded audit telemetry.
 *
 * Pi intentionally treats most extension failures as fail-open.  These hooks
 * therefore never grant authority: the Node lease check and Python host
 * reauthorization remain the only execution gates.
 */
export function registerRuntimeLifecycleHooks(
  pi: ExtensionAPI,
  options: RuntimeLifecycleOptions,
): void {
  let sequence = 0;
  const audit = (
    name: string,
    startedAt: number,
    decision: "observed" | "projected" | "failed" = "observed",
    details: JsonRecord = {},
  ): void => {
    try {
      const current = options.current();
      options.emit({
        type: "status.update",
        request_id: String(current.request_id || ""),
        session_id: String(current.session_id || ""),
        status: "hook",
        name,
        duration_ms: Math.max(0, Date.now() - startedAt),
        details: {
          sequence: ++sequence,
          decision,
          ...details,
        },
      });
    } catch {
      // Telemetry must never change model behavior or broaden authority.
    }
  };

  pi.on("before_agent_start", (event) => {
    const startedAt = Date.now();
    try {
      const result = options.beforeAgentStart?.(event);
      audit("before_agent_start", startedAt, result ? "projected" : "observed", {
        image_count: Array.isArray(event.images) ? event.images.length : 0,
      });
      return result;
    } catch {
      audit("before_agent_start", startedAt, "failed");
      return undefined;
    }
  });
  pi.on("context", (event) => {
    const startedAt = Date.now();
    try {
      const result = options.context?.(event);
      const report = result && "report" in result && result.report && typeof result.report === "object"
        ? result.report as JsonRecord
        : {};
      let reportTelemetryFailed = false;
      try {
        options.onContextReport?.(report);
      } catch {
        reportTelemetryFailed = true;
      }
      audit("context", startedAt, result ? "projected" : "observed", {
        message_count: event.messages.length,
        pruned_tool_results: Number(report.pruned_tool_results || 0),
        report_telemetry_failed: reportTelemetryFailed,
      });
      return result ? { messages: result.messages } : undefined;
    } catch {
      audit("context", startedAt, "failed", { message_count: event.messages.length });
      return undefined;
    }
  });
  pi.on("before_provider_request", (event) => {
    const startedAt = Date.now();
    try {
      const result = options.beforeProviderRequest?.(event);
      audit("before_provider_request", startedAt, result === undefined ? "observed" : "projected", {
        payload_kind: Array.isArray(event.payload) ? "array" : typeof event.payload,
      });
      return result;
    } catch {
      // Pi's runner is fail-open here as well.  Make that outcome explicit;
      // hard request limits must live below this hook (Task 5).
      audit("before_provider_request", startedAt, "failed");
      return undefined;
    }
  });
  pi.on("after_provider_response", (event) => {
    const startedAt = Date.now();
    audit("after_provider_response", startedAt, "observed", { status_code: Number(event.status || 0) });
  });
  pi.on("tool_call", (event) => {
    const startedAt = Date.now();
    audit("tool_call", startedAt, "observed", { tool_name: String(event.toolName || "").slice(0, 120) });
  });
  pi.on("tool_result", (event) => {
    const startedAt = Date.now();
    audit("tool_result", startedAt, "observed", {
      tool_name: String(event.toolName || "").slice(0, 120),
      is_error: event.isError === true,
      content_blocks: Array.isArray(event.content) ? event.content.length : 0,
    });
  });
  pi.on("agent_settled", () => {
    const startedAt = Date.now();
    audit("settled", startedAt);
  });
}

interface DynamicSession {
  getActiveToolNames(): string[];
  setActiveToolsByName(names: string[]): void;
}

export interface DynamicToolRuntimeOptions {
  catalog: ToolCatalogEntry[];
  getSession: () => DynamicSession | undefined;
  isAuthorized: (name: string) => boolean;
  onActivation?: (activeNames: string[], activatedNames: string[]) => void;
  emit?: (payload: JsonRecord) => void;
  requestId?: () => string;
}

export function createSearchToolsTool(options: DynamicToolRuntimeOptions) {
  return defineTool({
    name: "search_tools",
    label: "Search authorized tools",
    description: "Search the current ScanSci capability lease by tool name, label, description, aliases, tags, group, risk, or availability. Matching authorized tools can be additively activated for the next model turn. This loader never expands the host lease. The threshold is a local heuristic confidence cutoff, not an authorization decision.",
    executionMode: "sequential",
    parameters: Type.Object({
      query: Type.Optional(Type.String({ maxLength: 500 })),
      names: Type.Optional(Type.Array(Type.String({ minLength: 1, maxLength: 160 }), { maxItems: 20 })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
      threshold: Type.Optional(Type.Number({ minimum: 0, maximum: 1, default: DEFAULT_DISCOVERY_THRESHOLD })),
      activate: Type.Optional(Type.Boolean({ default: true })),
    }),
    execute: async (_toolCallId, params) => {
      const session = options.getSession();
      if (!session) throw new Error("Dynamic tool session is not ready");
      const before = session.getActiveToolNames();
      const result = searchToolCatalog(options.catalog, {
        query: params.query,
        names: params.names,
        limit: params.limit,
        activeNames: before,
        isAuthorized: options.isAuthorized,
      });
      const shouldActivate = params.activate !== false;
      const threshold = normalizeDiscoveryThreshold(params.threshold);
      const selected = shouldActivate
        ? result.matches
          .filter((match) => match.confidence >= threshold)
          .map((match) => match.name)
          .filter((name) => options.isAuthorized(name))
        : [];
      const next = [...new Set([...before, ...selected])]
        .filter((name) => name === "search_tools" || options.isAuthorized(name) || ["ask_user", "submit_plan"].includes(name))
        .sort();
      const activated = next.filter((name) => !before.includes(name));
      if (activated.length) session.setActiveToolsByName(next);
      options.onActivation?.(next, activated);
      const decision = {
        schema_version: DISCOVERY_DECISION_SCHEMA_VERSION,
        kind: "tool_activation",
        source: "local_heuristic",
        semantic_provider: "not_configured",
        fallback_used: true,
        threshold,
        candidate_count: result.matches.length,
        selected: activated,
      };
      options.emit?.({
        type: "status.update",
        request_id: options.requestId?.() || "",
        status: "tool_catalog_searched",
        name: "search_tools",
        details: {
          query: String(params.query || "").slice(0, 200),
          match_count: result.matches.length,
          decision,
          activated,
          active_tool_count: next.length,
          rejected: result.rejected,
        },
      });
      const payload = {
        schema_version: "scansci.tool-search.v1",
        query: String(params.query || ""),
        decision,
        matches: result.matches.map((match) => ({ ...match, active: next.includes(match.name) })),
        activated,
        rejected: result.rejected,
        active_tools: next,
      };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        details: payload,
      };
    },
  });
}
