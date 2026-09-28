import { createHash } from "node:crypto";

export type McpEffect = "read" | "reversible" | "write" | "destructive" | "unknown";
export type McpFreshness = "volatile" | "turn" | "run";

type JsonRecord = Record<string, unknown>;

export interface McpToolPolicy {
  serverId: string;
  serverAlias: string;
  remoteName: string;
  effect: McpEffect;
  idempotent: boolean;
  freshness: McpFreshness;
  annotations: JsonRecord;
}

export interface ClassifyMcpToolPolicyOptions {
  rawServerId: string;
  serverAlias: string;
  remoteTool: JsonRecord;
  configuredEffects?: JsonRecord;
  configuredPolicies?: unknown;
}

function stableValue(value: unknown, depth = 0): unknown {
  if (depth > 24) return "[depth-limit]";
  if (Array.isArray(value)) return value.slice(0, 256).map((item) => stableValue(item, depth + 1));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as JsonRecord)
      .sort(([left], [right]) => left.localeCompare(right))
      .slice(0, 256)
      .map(([key, item]) => [key, stableValue(item, depth + 1)]),
  );
}

function digest(value: unknown): string {
  const encoded = JSON.stringify(stableValue(value));
  return `sha256:${createHash("sha256").update(encoded).digest("hex")}`;
}

function boundedIdentifier(value: unknown, limit = 160): string {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function toolSegment(value: unknown): string {
  return String(value || "mcp")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "") || "mcp";
}

export function safeMcpLocalToolName(serverAlias: unknown, remoteName: unknown): string {
  const full = `mcp__${toolSegment(serverAlias)}__${toolSegment(remoteName)}`;
  if (full.length <= 64) return full;
  const suffix = createHash("sha256").update(full).digest("hex").slice(0, 10);
  return `${full.slice(0, 53).replace(/_+$/g, "")}_${suffix}`.slice(0, 64);
}

export function normalizeMcpEffect(value: unknown): McpEffect {
  const normalized = String(value || "").trim().toLowerCase();
  if (["read", "read_only", "readonly"].includes(normalized)) return "read";
  if (normalized === "reversible") return "reversible";
  if (["write", "mutation"].includes(normalized)) return "write";
  if (["destructive", "delete", "high"].includes(normalized)) return "destructive";
  return "unknown";
}

export function normalizeMcpFreshness(value: unknown, effect: McpEffect): McpFreshness {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "turn") return "turn";
  if (normalized === "run" && effect === "read") return "run";
  return "volatile";
}

function configuredPolicyFor(value: unknown, remoteName: string): JsonRecord | undefined {
  if (Array.isArray(value)) {
    return value.find((item) => (
      Boolean(item && typeof item === "object")
      && String((item as JsonRecord).name || (item as JsonRecord).tool || "") === remoteName
    )) as JsonRecord | undefined;
  }
  if (value && typeof value === "object") {
    const item = (value as JsonRecord)[remoteName];
    return item && typeof item === "object" ? item as JsonRecord : undefined;
  }
  return undefined;
}

/**
 * Resolve host-owned MCP policy. Remote annotations are untrusted hints: they
 * may only raise risk or reduce idempotency/freshness, never grant authority.
 */
export function classifyMcpToolPolicy(options: ClassifyMcpToolPolicyOptions): McpToolPolicy {
  const remoteName = boundedIdentifier(options.remoteTool.name, 200);
  const annotations = options.remoteTool.annotations && typeof options.remoteTool.annotations === "object"
    ? options.remoteTool.annotations as JsonRecord
    : {};
  const configured = configuredPolicyFor(options.configuredPolicies, remoteName);
  let effect = normalizeMcpEffect(configured?.effect ?? options.configuredEffects?.[remoteName]);
  if (effect !== "unknown" && annotations.destructiveHint === true) effect = "destructive";
  else if (effect === "read" && annotations.readOnlyHint === false) effect = "write";
  const hostIdempotent = configured?.idempotent === true;
  const idempotent = effect !== "unknown"
    && effect !== "destructive"
    && hostIdempotent
    && annotations.idempotentHint !== false;
  let freshness = normalizeMcpFreshness(configured?.freshness, effect);
  if (!idempotent || annotations.openWorldHint === true) freshness = "volatile";
  return {
    serverId: boundedIdentifier(options.rawServerId),
    serverAlias: boundedIdentifier(options.serverAlias, 80),
    remoteName,
    effect,
    idempotent,
    freshness,
    annotations: {
      readOnlyHint: annotations.readOnlyHint,
      destructiveHint: annotations.destructiveHint,
      idempotentHint: annotations.idempotentHint,
      openWorldHint: annotations.openWorldHint,
    },
  };
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return normalized === "localhost"
    || normalized === "::1"
    || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

/** Allow HTTPS, or plaintext HTTP only on the loopback interface. */
export function safeMcpEndpoint(value: unknown): string | undefined {
  try {
    const endpoint = new URL(String(value || "").trim());
    if (endpoint.username || endpoint.password || endpoint.hash) return undefined;
    if (endpoint.protocol === "https:") return endpoint.toString();
    if (endpoint.protocol === "http:" && isLoopbackHostname(endpoint.hostname)) return endpoint.toString();
  } catch {
    // Invalid endpoints are unavailable, never guessed or repaired.
  }
  return undefined;
}

/** Enable Pi's Kimi deferred-tools wire mode only from trusted host metadata. */
export function trustedDeferredToolsMode(value: {
  providerId?: unknown;
  modelId?: unknown;
  apiSurface?: unknown;
}): "kimi" | undefined {
  const providerId = String(value.providerId || "").trim().toLowerCase();
  const modelId = String(value.modelId || "").trim().toLowerCase();
  const apiSurface = String(value.apiSurface || "").trim().toLowerCase();
  const trustedProvider = providerId === "moonshot" || providerId === "moonshot-ai";
  return trustedProvider && modelId.startsWith("kimi-") && apiSurface === "chat_completions"
    ? "kimi"
    : undefined;
}

export function boundedMcpInputSchema(
  value: unknown,
  maxBytes = 12_000,
  maxDepth = 12,
): JsonRecord | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  let valid = true;
  const visit = (item: unknown, depth: number): void => {
    if (!valid || depth > maxDepth) {
      valid = false;
      return;
    }
    if (Array.isArray(item)) {
      if (item.length > 256) valid = false;
      for (const child of item) visit(child, depth + 1);
      return;
    }
    if (!item || typeof item !== "object") return;
    const entries = Object.entries(item as JsonRecord);
    if (entries.length > 256 || entries.some(([key]) => key.length > 200 || /[\u0000-\u001f\u007f]/.test(key))) {
      valid = false;
      return;
    }
    for (const [, child] of entries) visit(child, depth + 1);
  };
  visit(value, 0);
  if (!valid) return undefined;
  try {
    const encoded = JSON.stringify(value);
    if (Buffer.byteLength(encoded, "utf8") > maxBytes) return undefined;
    const schema = structuredClone(value) as JsonRecord;
    if (schema.type === undefined) schema.type = "object";
    if (schema.type !== "object") return undefined;
    return schema;
  } catch {
    return undefined;
  }
}

/** Reject provider-supplied MCP arguments before they reach a transport. */
export function validateMcpArguments(
  value: unknown,
  maxBytes = 64 * 1024,
  maxDepth = 16,
  maxNodes = 4_096,
): asserts value is JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("MCP tool arguments must be an object");
  }
  const ancestors = new Set<object>();
  let nodes = 0;
  const visit = (item: unknown, depth: number): void => {
    if (depth > maxDepth) throw new Error("MCP tool arguments exceed the depth limit");
    if (item === null || ["string", "number", "boolean"].includes(typeof item)) return;
    if (Array.isArray(item)) {
      nodes += 1;
      if (nodes > maxNodes || item.length > 1_024) throw new Error("MCP tool arguments exceed the node limit");
      if (ancestors.has(item)) throw new Error("MCP tool arguments must not be cyclic");
      ancestors.add(item);
      for (const child of item) visit(child, depth + 1);
      ancestors.delete(item);
      return;
    }
    if (!item || typeof item !== "object") throw new Error("MCP tool arguments must be JSON-compatible");
    const object = item as object;
    if (ancestors.has(object)) throw new Error("MCP tool arguments must not be cyclic");
    ancestors.add(object);
    const entries = Object.entries(item as JsonRecord);
    nodes += entries.length + 1;
    if (nodes > maxNodes || entries.length > 1_024) throw new Error("MCP tool arguments exceed the node limit");
    for (const [key, child] of entries) {
      if (key.length > 512 || /[\u0000-\u001f\u007f]/.test(key)) {
        throw new Error("MCP tool arguments contain an invalid key");
      }
      visit(child, depth + 1);
    }
    ancestors.delete(object);
  };
  visit(value, 0);
  let encoded: string;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new Error("MCP tool arguments must be JSON-compatible");
  }
  if (Buffer.byteLength(encoded, "utf8") > maxBytes) {
    throw new Error("MCP tool arguments exceed the byte limit");
  }
}

export interface McpAuditInput {
  phase: "start" | "end";
  requestId?: unknown;
  serverId: unknown;
  serverAlias: unknown;
  remoteName: unknown;
  effect: McpEffect;
  idempotent: boolean;
  freshness: McpFreshness;
  durationMs?: unknown;
  decision: unknown;
  arguments?: unknown;
  result?: unknown;
  error?: unknown;
}

/** Create bounded audit metadata without raw arguments, results, errors, or secrets. */
export function boundedMcpAuditRecord(input: McpAuditInput): JsonRecord {
  const record: JsonRecord = {
    schema_version: "scansci.mcp-effect.v1",
    phase: input.phase,
    request_digest: digest(String(input.requestId || "")),
    server_id: boundedIdentifier(input.serverId),
    server_alias: boundedIdentifier(input.serverAlias, 80),
    remote_name: boundedIdentifier(input.remoteName, 200),
    effect: input.effect,
    idempotent: input.idempotent,
    freshness: input.freshness,
    duration_ms: Math.max(0, Math.min(3_600_000, Math.floor(Number(input.durationMs) || 0))),
    decision: boundedIdentifier(input.decision, 64),
    call_digest: digest(input.arguments ?? {}),
  };
  if (input.result !== undefined) {
    const encoded = JSON.stringify(stableValue(input.result));
    record.result_reference = {
      digest: digest(input.result),
      bytes: Math.min(Buffer.byteLength(encoded, "utf8"), 16 * 1024 * 1024),
      bounded: Buffer.byteLength(encoded, "utf8") > 16 * 1024 * 1024,
    };
  }
  if (input.error !== undefined) {
    record.error = {
      name: boundedIdentifier(
        input.error && typeof input.error === "object"
          ? (input.error as { name?: unknown }).name || "Error"
          : "Error",
        80,
      ),
      digest: digest(String(input.error)),
    };
  }
  return record;
}

export interface McpCachePolicy {
  effect: McpEffect;
  idempotent: boolean;
  freshness: McpFreshness;
}

export function authorizeMcpPolicy(
  policy: Pick<McpToolPolicy, "serverId" | "effect">,
  authority: {
    allowedServerIds: Iterable<string>;
    riskLevel: unknown;
    allowExternalWrite: boolean;
    planApproved: boolean;
    requiresPlan: boolean;
  },
): boolean {
  if (policy.effect === "unknown") return false;
  if (!new Set(authority.allowedServerIds).has(policy.serverId)) return false;
  const ceiling = String(authority.riskLevel || "").toLowerCase();
  if (policy.effect === "read") return ["read_only", "reversible", "high", "approval_required"].includes(ceiling);
  if (policy.effect === "reversible") {
    if (!["reversible", "high", "approval_required"].includes(ceiling)) return false;
    return !authority.requiresPlan || authority.planApproved;
  }
  if (!["high", "approval_required"].includes(ceiling) || !authority.allowExternalWrite) return false;
  return authority.planApproved && (!authority.requiresPlan || authority.planApproved);
}

export async function invokeAuthorizedMcp<T>(
  policy: Pick<McpToolPolicy, "serverId" | "effect">,
  authority: Parameters<typeof authorizeMcpPolicy>[1],
  invoke: () => Promise<T>,
): Promise<T> {
  if (!authorizeMcpPolicy(policy, authority)) {
    throw new Error("MCP effect denied by the current host authority");
  }
  return invoke();
}

function mcpErrorDetail(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const record = value as JsonRecord;
  const texts = Array.isArray(record.content)
    ? record.content
      .filter((item) => item && typeof item === "object" && (item as JsonRecord).type === "text")
      .map((item) => String((item as JsonRecord).text || "").trim())
      .filter(Boolean)
    : [];
  const structured = record.structuredContent && typeof record.structuredContent === "object"
    ? record.structuredContent as JsonRecord
    : {};
  const detail = texts.join(" ") || String(structured.error || structured.message || "").trim();
  return detail
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 1000);
}

export function ensureMcpCallResult<T>(value: T): T {
  if (value && typeof value === "object" && (value as { isError?: unknown }).isError === true) {
    const detail = mcpErrorDetail(value);
    throw new Error(`MCP tool returned an error result${detail ? `: ${detail}` : ""}`);
  }
  return value;
}

interface CachedValue {
  value: unknown;
  turn: number;
}

export interface McpRunCache {
  get<T = unknown>(key: string, policy?: McpCachePolicy, turn?: number): T | undefined;
  set(key: string, value: unknown, policy: McpCachePolicy, turn?: number): void;
  clear(): void;
}

/** Bounded, memory-only cache owned by one ActiveRun. */
export function createMcpRunCache(maxEntries = 64): McpRunCache {
  const values = new Map<string, CachedValue>();
  const policies = new Map<string, McpCachePolicy>();
  return {
    get<T = unknown>(key: string, currentPolicy?: McpCachePolicy, turn = 0): T | undefined {
      const policy = policies.get(key);
      const cached = values.get(key);
      if (!policy || !cached) return undefined;
      if (currentPolicy && (
        currentPolicy.effect !== "read"
        || !currentPolicy.idempotent
        || currentPolicy.freshness === "volatile"
        || currentPolicy.effect !== policy.effect
        || currentPolicy.idempotent !== policy.idempotent
        || currentPolicy.freshness !== policy.freshness
      )) return undefined;
      if (policy.freshness === "volatile") return undefined;
      if (policy.freshness === "turn" && cached.turn !== turn) return undefined;
      return structuredClone(cached.value) as T;
    },
    set(key: string, value: unknown, policy: McpCachePolicy, turn = 0): void {
      if (policy.effect !== "read" || !policy.idempotent || policy.freshness === "volatile") return;
      if (values.size >= Math.max(1, Math.min(256, maxEntries)) && !values.has(key)) {
        const oldest = values.keys().next().value;
        if (oldest !== undefined) {
          values.delete(oldest);
          policies.delete(oldest);
        }
      }
      values.set(key, { value: structuredClone(value), turn });
      policies.set(key, { ...policy });
    },
    clear(): void {
      values.clear();
      policies.clear();
    },
  };
}

export function isRetryableMcpError(error: unknown): boolean {
  const text = String(error instanceof Error ? error.message : error || "").toLowerCase();
  return /timeout|timed out|econnreset|econnrefused|socket|disconnect|connection closed|503|504|temporar/.test(text);
}

export async function callMcpWithRetry<T>(
  operation: () => Promise<T>,
  policy: Pick<McpToolPolicy, "idempotent">,
): Promise<T> {
  const attempts = policy.idempotent ? 2 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (attempt + 1 >= attempts || !isRetryableMcpError(error)) throw error;
    }
  }
  throw new Error("MCP retry loop ended unexpectedly");
}
