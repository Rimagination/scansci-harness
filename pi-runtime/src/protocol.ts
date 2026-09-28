import type { ModelRuntimeDescriptor, PiImageContent } from "./multimodal.js";

export const PI_PROTOCOL_VERSION = 7;

export const PI_PROTOCOL_FEATURES = [
  "multi_session",
  "task_contracts",
  "bounded_autonomy",
  "ask_user",
  "plan_approval",
  "follow_up",
  "structured_recovery",
  "session_fork",
  "task_contract_v2",
  "explicit_empty_leases",
  "host_tool_authorization",
  "structured_mcp_effects",
  "current_request_context",
  "dynamic_tools",
  "ephemeral_sessions",
  "progressive_skills",
  "parallel_tool_dispatch",
  "lifecycle_hooks_v1",
  "acked_session_commands",
  "model_runtime_descriptor",
  "token_envelope",
  "multimodal_turns",
  "scientific_subagents_v1",
  "session_controls_v2",
  "thinking_max",
  "deferred_mcp_v2",
  "mcp_effect_audit_v1",
  "mcp_run_cache_v1",
] as const;

type ProtocolRecord = Record<string, unknown>;

export interface SkillCatalogEntry extends ProtocolRecord {
  id: string;
  name: string;
  description: string;
  source: string;
  package_hash: string;
}

export interface SkillSelectionEntry extends ProtocolRecord {
  id: string;
  provenance: "explicit" | "inferred" | "suppressed" | string;
  status: "loaded" | "hint" | "suppressed" | string;
  package_hash?: string;
  content_hash?: string;
  resource?: string;
  bytes?: number;
}

export interface RunStartMessage extends ProtocolRecord {
  type: "run.start";
  pi_protocol_version?: number;
  required_features?: string[];
  request_id: string;
  session_id: string;
  ephemeral_session?: boolean;
  session_file?: string;
  cwd: string;
  agent_dir: string;
  provider_kind: string;
  provider_id?: string;
  base_url: string;
  model_id: string;
  api_surface?: string;
  responses_enabled?: boolean;
  thinking_level?: string;
  system_prompt: string;
  prompt: string;
  images?: PiImageContent[];
  model_runtime: ModelRuntimeDescriptor;
  task_mode?: string;
  task_contract?: ProtocolRecord;
  prefix_shape?: ProtocolRecord;
  context_policy?: ProtocolRecord;
  mcp_servers?: ProtocolRecord[];
  disabled_tools?: string[];
  background?: boolean;
  skill_catalog?: SkillCatalogEntry[];
  skill_selection?: SkillSelectionEntry[];
  skill_state?: ProtocolRecord;
}

export interface ProtocolNegotiation {
  ok: boolean;
  protocol: number;
  missingFeatures: string[];
  error?: string;
}

export function negotiateProtocol(message: ProtocolRecord): ProtocolNegotiation {
  const protocol = Number(message.pi_protocol_version || 0);
  const required = Array.isArray(message.required_features)
    ? [...new Set(message.required_features.map(String).filter(Boolean))]
    : [];
  const supported = new Set<string>(PI_PROTOCOL_FEATURES);
  const missingFeatures = required.filter((feature) => !supported.has(feature));
  if (protocol !== PI_PROTOCOL_VERSION) {
    return {
      ok: false,
      protocol,
      missingFeatures,
      error: `Pi protocol ${protocol || "missing"} is incompatible; version ${PI_PROTOCOL_VERSION} is required.`,
    };
  }
  if (missingFeatures.length) {
    return {
      ok: false,
      protocol,
      missingFeatures,
      error: `Pi runtime is missing required features: ${missingFeatures.join(", ")}.`,
    };
  }
  return { ok: true, protocol, missingFeatures: [] };
}
