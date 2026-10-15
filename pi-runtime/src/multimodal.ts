import { createHash } from "node:crypto";

type JsonRecord = Record<string, unknown>;

export interface PiImageContent {
  type: "image";
  data: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp" | "image/gif";
}

export interface ModelRuntimeDescriptor extends JsonRecord {
  schema_version: "scansci.model-runtime.v1";
  provider_id: string;
  provider_kind: string;
  model_id: string;
  api_surface: string;
  context_window_tokens: number;
  provider_input_tokens: number;
  context_guard_tokens: number;
  compaction_reserve_tokens: number;
  keep_recent_tokens: number;
  max_output_tokens: number;
  input_modalities: ("text" | "image")[];
  capabilities: string[];
  reasoning: boolean;
  tool_use: boolean;
  context_provenance: string;
  capability_provenance: string;
  degraded: boolean;
  degradation_reasons: string[];
}

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_BYTES = 10 * 1024 * 1024;
const MAX_IMAGE_DIMENSION = 16_384;
const MAX_IMAGE_PIXELS = 40_000_000;
const MAX_CONTEXT_WINDOW = 4 * 1024 * 1024;
const IMAGE_KEYS = ["data", "mimeType", "type"] as const;
const DESCRIPTOR_KEYS = [
  "api_surface",
  "capabilities",
  "capability_provenance",
  "compaction_reserve_tokens",
  "context_guard_tokens",
  "context_provenance",
  "context_window_tokens",
  "degradation_reasons",
  "degraded",
  "input_modalities",
  "keep_recent_tokens",
  "max_output_tokens",
  "model_id",
  "provider_id",
  "provider_input_tokens",
  "provider_kind",
  "reasoning",
  "schema_version",
  "tool_use",
] as const;
const MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const CAPABILITIES = new Set(["reasoning", "tool", "vision", "audio", "coding", "embedding", "reranking"]);

function isPlainObject(value: unknown): value is JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(record: JsonRecord, expected: readonly string[]): boolean {
  const keys = Object.keys(record).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function bounded(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function roundedDown(value: number): number {
  return Math.max(1024, Math.floor(value / 1024) * 1024);
}

export function modelRuntimeBudgets(contextWindow: number): {
  provider_input_tokens: number;
  context_guard_tokens: number;
  compaction_reserve_tokens: number;
  keep_recent_tokens: number;
  max_output_tokens: number;
} {
  const window = Math.max(16 * 1024, Math.floor(contextWindow));
  if (window < 32 * 1024) {
    const contextGuard = 1024;
    const reserve = bounded(roundedDown(Math.floor(window / 8)), 1024, 32 * 1024);
    const keepRecent = bounded(roundedDown(Math.floor(window / 10)), 1024, 32 * 1024);
    return {
      provider_input_tokens: window - reserve - contextGuard,
      context_guard_tokens: contextGuard,
      compaction_reserve_tokens: reserve,
      keep_recent_tokens: keepRecent,
      max_output_tokens: reserve,
    };
  }
  const contextGuard = bounded(roundedDown(Math.floor(window / 32)), 1024, 8192);
  const reserve = bounded(roundedDown(Math.floor(window / 8)), 4096, 32 * 1024);
  const keepRecent = bounded(roundedDown(Math.floor(window / 10)), 4096, 32 * 1024);
  const maxOutput = bounded(roundedDown(Math.floor(window / 16)), 4096, 16 * 1024);
  return {
    provider_input_tokens: Math.max(4096, window - reserve - contextGuard),
    context_guard_tokens: contextGuard,
    compaction_reserve_tokens: reserve,
    keep_recent_tokens: keepRecent,
    max_output_tokens: Math.min(maxOutput, reserve),
  };
}

function requireBoundedString(value: unknown, label: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > maxLength) {
    throw new Error(`Invalid model runtime descriptor field: ${label}`);
  }
  return value;
}

export function validateModelRuntimeDescriptor(
  value: unknown,
  route?: { provider_kind?: unknown; model_id?: unknown; api_surface?: unknown },
): ModelRuntimeDescriptor {
  if (!isPlainObject(value) || !hasExactKeys(value, DESCRIPTOR_KEYS)) {
    throw new Error("Model runtime descriptor must be a canonical v1 object");
  }
  if (value.schema_version !== "scansci.model-runtime.v1") {
    throw new Error("Invalid model runtime descriptor schema");
  }
  const providerId = requireBoundedString(value.provider_id, "provider_id", 160);
  const providerKind = requireBoundedString(value.provider_kind, "provider_kind", 80);
  const modelId = requireBoundedString(value.model_id, "model_id", 300);
  const apiSurface = requireBoundedString(value.api_surface, "api_surface", 40);
  const contextProvenance = requireBoundedString(value.context_provenance, "context_provenance", 120);
  const capabilityProvenance = requireBoundedString(value.capability_provenance, "capability_provenance", 120);
  const contextWindow = value.context_window_tokens;
  if (
    typeof contextWindow !== "number"
    || !Number.isSafeInteger(contextWindow)
    || contextWindow < 16 * 1024
    || contextWindow > MAX_CONTEXT_WINDOW
  ) {
    throw new Error("Invalid model runtime context window");
  }
  const budgets = modelRuntimeBudgets(contextWindow);
  for (const [key, expected] of Object.entries(budgets)) {
    if (value[key] !== expected) throw new Error(`Invalid model runtime budget: ${key}`);
  }
  if (
    !Array.isArray(value.input_modalities)
    || value.input_modalities.length < 1
    || value.input_modalities.length > 2
    || value.input_modalities[0] !== "text"
    || value.input_modalities.some((item) => item !== "text" && item !== "image")
    || new Set(value.input_modalities).size !== value.input_modalities.length
  ) {
    throw new Error("Invalid model runtime input modalities");
  }
  if (
    !Array.isArray(value.capabilities)
    || value.capabilities.some((item) => typeof item !== "string" || !CAPABILITIES.has(item))
    || new Set(value.capabilities).size !== value.capabilities.length
    || !Array.isArray(value.degradation_reasons)
    || value.degradation_reasons.some((item) => typeof item !== "string" || !item || item.length > 160)
    || typeof value.reasoning !== "boolean"
    || typeof value.tool_use !== "boolean"
    || typeof value.degraded !== "boolean"
  ) {
    throw new Error("Invalid model runtime capability metadata");
  }
  if (
    value.input_modalities.includes("image") !== value.capabilities.includes("vision")
    || value.reasoning !== value.capabilities.includes("reasoning")
    || value.tool_use !== value.capabilities.includes("tool")
    || value.degraded !== (value.degradation_reasons.length > 0)
  ) {
    throw new Error("Invalid model runtime modality/capability consistency");
  }
  if (
    route?.provider_kind !== undefined && providerKind !== String(route.provider_kind)
    || route?.model_id !== undefined && modelId !== String(route.model_id)
    || route?.api_surface !== undefined && apiSurface !== String(route.api_surface || "chat_completions")
  ) {
    throw new Error("Model runtime descriptor does not match the selected provider route");
  }
  return {
    ...value,
    schema_version: "scansci.model-runtime.v1",
    provider_id: providerId,
    provider_kind: providerKind,
    model_id: modelId,
    api_surface: apiSurface,
    context_window_tokens: contextWindow,
    provider_input_tokens: budgets.provider_input_tokens,
    context_guard_tokens: budgets.context_guard_tokens,
    compaction_reserve_tokens: budgets.compaction_reserve_tokens,
    keep_recent_tokens: budgets.keep_recent_tokens,
    max_output_tokens: budgets.max_output_tokens,
    input_modalities: [...value.input_modalities] as ("text" | "image")[],
    capabilities: [...value.capabilities] as string[],
    reasoning: value.reasoning,
    tool_use: value.tool_use,
    context_provenance: contextProvenance,
    capability_provenance: capabilityProvenance,
    degraded: value.degraded,
    degradation_reasons: [...value.degradation_reasons] as string[],
  };
}

function matchesMagic(mimeType: string, raw: Buffer): boolean {
  if (mimeType === "image/png") return raw.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"));
  if (mimeType === "image/jpeg") return raw.length >= 3 && raw[0] === 0xff && raw[1] === 0xd8 && raw[2] === 0xff;
  if (mimeType === "image/gif") return raw.subarray(0, 6).toString("ascii") === "GIF87a" || raw.subarray(0, 6).toString("ascii") === "GIF89a";
  if (mimeType === "image/webp") return raw.length >= 12 && raw.subarray(0, 4).toString("ascii") === "RIFF" && raw.subarray(8, 12).toString("ascii") === "WEBP";
  return false;
}

function imageDimensions(mimeType: string, raw: Buffer): [number, number] | undefined {
  if (mimeType === "image/png" && raw.length >= 24 && raw.subarray(12, 16).toString("ascii") === "IHDR") {
    return [raw.readUInt32BE(16), raw.readUInt32BE(20)];
  }
  if (mimeType === "image/gif" && raw.length >= 10) return [raw.readUInt16LE(6), raw.readUInt16LE(8)];
  if (mimeType === "image/jpeg") {
    let offset = 2;
    while (offset + 9 <= raw.length) {
      if (raw[offset] !== 0xff) { offset += 1; continue; }
      const marker = raw[offset + 1];
      offset += 2;
      if (marker === 0xd8 || marker === 0xd9) continue;
      if (marker === 0xda || offset + 2 > raw.length) break;
      const segment = raw.readUInt16BE(offset);
      if (segment < 2 || offset + segment > raw.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        if (segment >= 7) return [raw.readUInt16BE(offset + 5), raw.readUInt16BE(offset + 3)];
        break;
      }
      offset += segment;
    }
  }
  if (mimeType === "image/webp" && raw.length >= 30) {
    const kind = raw.subarray(12, 16).toString("ascii");
    if (kind === "VP8X") return [1 + raw.readUIntLE(24, 3), 1 + raw.readUIntLE(27, 3)];
    if (kind === "VP8L" && raw.length >= 25 && raw[20] === 0x2f) {
      const bits = raw.readUInt32LE(21);
      return [1 + (bits & 0x3fff), 1 + ((bits >>> 14) & 0x3fff)];
    }
    if (kind === "VP8 " && raw.subarray(23, 26).equals(Buffer.from([0x9d, 0x01, 0x2a]))) {
      return [raw.readUInt16LE(26) & 0x3fff, raw.readUInt16LE(28) & 0x3fff];
    }
  }
  return undefined;
}

function validateImageBytes(mimeType: string, raw: Buffer): void {
  if (!matchesMagic(mimeType, raw)) throw new Error("Image MIME type does not match its content");
  const dimensions = imageDimensions(mimeType, raw);
  if (!dimensions) throw new Error("Image dimensions are invalid or could not be parsed");
  const [width, height] = dimensions;
  if (
    width <= 0
    || height <= 0
    || width > MAX_IMAGE_DIMENSION
    || height > MAX_IMAGE_DIMENSION
    || width * height > MAX_IMAGE_PIXELS
  ) {
    throw new Error("Image dimensions exceed the safe limit");
  }
}

export function validatePiImages(value: unknown): PiImageContent[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("Pi images must be a list");
  if (value.length > MAX_IMAGES) throw new Error("A Pi turn accepts at most 4 images");
  const output: PiImageContent[] = [];
  let totalBytes = 0;
  const encodedLimit = 4 * Math.ceil(MAX_IMAGE_BYTES / 3);
  for (const candidate of value) {
    if (!isPlainObject(candidate) || !hasExactKeys(candidate, IMAGE_KEYS)) {
      throw new Error("Pi image blocks require exactly type, data, and mimeType");
    }
    if (candidate.type !== "image") throw new Error("Pi image block type must be image");
    if (typeof candidate.mimeType !== "string" || !MIME_TYPES.has(candidate.mimeType)) {
      throw new Error("Pi image MIME type is unsupported");
    }
    const encoded = candidate.data;
    if (
      typeof encoded !== "string"
      || !encoded
      || encoded.length > encodedLimit
      || encoded.length % 4 !== 0
      || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)
    ) {
      throw new Error("Pi image data is not canonical base64");
    }
    const raw = Buffer.from(encoded, "base64");
    if (!raw.length || raw.length > MAX_IMAGE_BYTES || raw.toString("base64") !== encoded) {
      throw new Error("Pi image data is not canonical base64");
    }
    totalBytes += raw.length;
    if (totalBytes > MAX_TOTAL_BYTES) throw new Error("Pi image payload exceeds the 10 MiB turn limit");
    validateImageBytes(candidate.mimeType, raw);
    output.push({ type: "image", data: encoded, mimeType: candidate.mimeType as PiImageContent["mimeType"] });
  }
  return output;
}

export function estimatePiImageTokens(value: unknown): number {
  const images = validatePiImages(value);
  return images.reduce((total, image) => {
    const raw = Buffer.from(image.data, "base64");
    const dimensions = imageDimensions(image.mimeType, raw);
    if (!dimensions) throw new Error("Image dimensions are invalid or could not be parsed");
    const [width, height] = dimensions;
    const tiles = Math.max(1, Math.ceil(width / 512)) * Math.max(1, Math.ceil(height / 512));
    return total + tiles * 1024;
  }, 0);
}

export function imageTelemetry(images: PiImageContent[]): JsonRecord {
  return {
    count: images.length,
    total_bytes: images.reduce((total, image) => total + Buffer.byteLength(image.data, "base64"), 0),
    items: images.map((image) => ({
      mime_type: image.mimeType,
      bytes: Buffer.byteLength(image.data, "base64"),
      digest: createHash("sha256").update(image.data, "base64").digest("hex"),
    })),
  };
}
