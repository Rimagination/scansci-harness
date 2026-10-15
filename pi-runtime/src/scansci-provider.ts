import type {
  Api,
  AssistantMessageEventStream,
  Context,
  FetchFunction,
  Model,
  SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import { streamSimple as streamAnthropic } from "@earendil-works/pi-ai/api/anthropic-messages";
import { streamSimple as streamOpenAICompletions } from "@earendil-works/pi-ai/api/openai-completions";
import { streamSimple as streamOpenAIResponses } from "@earendil-works/pi-ai/api/openai-responses";
import {
  estimatePiImageTokens,
  validatePiImages,
  type ModelRuntimeDescriptor,
  type PiImageContent,
} from "./multimodal.js";
import { conservativeTextTokens } from "./token-estimate.js";

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function dataUri(value: unknown): { mimeType: string; data: string } | undefined {
  if (typeof value !== "string") return undefined;
  const match = /^data:(image\/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=]+)$/.exec(value);
  return match ? { mimeType: match[1].toLowerCase(), data: match[2] } : undefined;
}

function providerImage(value: JsonRecord): { mimeType: string; data: string } | undefined {
  if (value.type === "image_url" && isRecord(value.image_url)) return dataUri(value.image_url.url);
  if (value.type === "input_image") return dataUri(value.image_url);
  if (value.type === "image" && isRecord(value.source) && value.source.type === "base64") {
    if (typeof value.source.media_type !== "string" || typeof value.source.data !== "string") return undefined;
    return { mimeType: value.source.media_type.toLowerCase(), data: value.source.data };
  }
  return undefined;
}

function safeProviderPayload(value: unknown, images: PiImageContent[], depth = 0): unknown {
  if (depth > 40) throw new Error("Provider payload nesting exceeds the safe limit");
  if (Array.isArray(value)) return value.map((item) => safeProviderPayload(item, images, depth + 1));
  if (!isRecord(value)) return value;
  const image = providerImage(value);
  if (image) {
    images.push({
      type: "image",
      data: image.data,
      mimeType: image.mimeType as PiImageContent["mimeType"],
    });
    return {
      type: String(value.type),
      mime_type: image.mimeType,
      decoded_bytes: Buffer.byteLength(image.data, "base64"),
      data: "[VALIDATED IMAGE]",
    };
  }
  const type = String(value.type || "").toLowerCase();
  if (type.includes("image") || "image_url" in value || "source" in value && isRecord(value.source) && value.source.type === "base64") {
    throw new Error("Provider payload contains an unsupported image shape");
  }
  const output: JsonRecord = {};
  for (const [key, item] of Object.entries(value)) output[key] = safeProviderPayload(item, images, depth + 1);
  return output;
}

function conservativeTokens(value: unknown, descriptor: ModelRuntimeDescriptor): number {
  const images: PiImageContent[] = [];
  const text = JSON.stringify(safeProviderPayload(value, images));
  validatePiImages(images);
  if (images.length && !descriptor.input_modalities.includes("image")) {
    throw new Error("Provider payload contains images for a text-only model descriptor");
  }
  return conservativeTextTokens(text, descriptor) + estimatePiImageTokens(images);
}

const OLLAMA_LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
const OLLAMA_NATIVE_OPTION_KEYS = [
  "temperature",
  "top_p",
  "top_k",
  "min_p",
  "repeat_penalty",
  "presence_penalty",
  "frequency_penalty",
  "seed",
  "stop",
] as const;

function isOllamaEndpoint(value: string, providerId = ""): boolean {
  try {
    const url = new URL(value);
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    return url.protocol === "http:"
      && OLLAMA_LOOPBACK_HOSTS.has(url.hostname.toLowerCase())
      && (port === "11434" || providerId === "local-runtime-ollama" || providerId.startsWith("local-runtime-ollama-"));
  } catch {
    return false;
  }
}

function requestUrl(input: Parameters<FetchFunction>[0]): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return String((input as { url?: unknown }).url || input);
}

function requestBodyText(body: unknown): string {
  if (typeof body === "string") return body;
  if (body instanceof Uint8Array) return new TextDecoder().decode(body);
  throw new Error("Ollama adapter requires a JSON request body");
}

function nativeOllamaMessages(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("Ollama request is missing messages");
  return value.map((item) => {
    if (!isRecord(item)) return item;
    const message = { ...item };
    if (message.role === "developer") message.role = "system";
    if (message.content === null || message.content === undefined) message.content = "";
    // OpenAI multimodal arrays (text + image_url data URLs) must become the
    // native form: a plain string plus per-message base64 ``images``.  The
    // array cannot pass through — Ollama's /api/chat rejects it outright.
    if (Array.isArray(message.content)) {
      const textParts: string[] = [];
      const images: string[] = [];
      for (const part of message.content) {
        if (!isRecord(part)) continue;
        if (typeof part.text === "string" && part.text) {
          textParts.push(part.text);
          continue;
        }
        const imageUrl = isRecord(part.image_url) ? String(part.image_url.url || "") : "";
        if (imageUrl) {
          const comma = imageUrl.indexOf(",");
          images.push(comma >= 0 && imageUrl.startsWith("data:") ? imageUrl.slice(comma + 1) : imageUrl);
        }
      }
      message.content = textParts.join("\n").trim();
      if (images.length) message.images = images;
    }
    if (message.role === "tool" && typeof message.name === "string" && message.name) {
      message.tool_name = message.name;
      delete message.name;
    }
    if (Array.isArray(message.tool_calls)) {
      message.tool_calls = message.tool_calls.map((rawCall, index) => {
        if (!isRecord(rawCall)) return rawCall;
        const call = { ...rawCall };
        const fn = isRecord(call.function) ? { ...call.function } : {};
        if (typeof fn.arguments === "string") {
          try {
            fn.arguments = JSON.parse(fn.arguments);
          } catch {
            // Let Ollama report malformed model-produced arguments. The
            // request still retains the original data for diagnostics.
          }
        }
        if (!fn.name && typeof call.name === "string") fn.name = call.name;
        call.function = fn;
        if (typeof call.index !== "number") call.index = index;
        delete call.custom;
        return call;
      });
    }
    return message;
  });
}

function nativeOllamaPayload(payload: JsonRecord, contextWindowTokens: number, modelId: string): JsonRecord {
  const native = { ...payload };
  native.messages = nativeOllamaMessages(native.messages);
  const options = isRecord(native.options) ? { ...native.options } : {};
  const outputBudget = native.max_tokens ?? native.max_completion_tokens ?? native.max_output_tokens;
  if (typeof outputBudget === "number" && Number.isSafeInteger(outputBudget) && outputBudget > 0) {
    options.num_predict = outputBudget;
  }
  options.num_ctx = contextWindowTokens;
  for (const key of OLLAMA_NATIVE_OPTION_KEYS) {
    if (native[key] !== undefined) {
      options[key] = native[key];
      delete native[key];
    }
  }
  native.options = options;
  if (typeof native.enable_thinking === "boolean") {
    native.think = native.enable_thinking;
  } else if (typeof native.reasoning_effort === "string") {
    native.think = native.reasoning_effort.toLowerCase() !== "none";
  } else if (isRecord(native.thinking) && typeof native.thinking.type === "string") {
    native.think = native.thinking.type === "enabled";
  } else if (modelId.toLowerCase().startsWith("qwen3.5:")) {
    native.think = false;
  }
  delete native.enable_thinking;
  delete native.reasoning_effort;
  delete native.thinking;
  delete native.max_tokens;
  delete native.max_completion_tokens;
  delete native.max_output_tokens;
  delete native.stream_options;
  delete native.store;
  delete native.prompt_cache_key;
  delete native.prompt_cache_retention;
  native.stream = true;
  return native;
}

function nativeOllamaToolCall(rawCall: unknown, fallbackIndex: number): JsonRecord | undefined {
  if (!isRecord(rawCall)) return undefined;
  const fn = isRecord(rawCall.function) ? rawCall.function : {};
  const name = String(fn.name || rawCall.name || "").trim();
  if (!name) return undefined;
  const args = fn.arguments;
  const argumentsText = typeof args === "string"
    ? args
    : JSON.stringify(args && typeof args === "object" ? args : {});
  return {
    index: typeof rawCall.index === "number" ? rawCall.index : fallbackIndex,
    id: String(rawCall.id || `ollama-call-${fallbackIndex}`),
    type: "function",
    function: { name, arguments: argumentsText },
  };
}

function openAIChunkFromOllama(
  event: JsonRecord,
  modelId: string,
  requestId: string,
  created: number,
  sawToolCall: { value: boolean },
): JsonRecord {
  if (event.error) {
    const detail = typeof event.error === "string" ? event.error : JSON.stringify(event.error);
    throw new Error(`Ollama: ${detail}`);
  }
  const message = isRecord(event.message) ? event.message : {};
  const delta: JsonRecord = {};
  if (typeof message.role === "string" && message.role) delta.role = message.role;
  if (typeof message.content === "string" && message.content) delta.content = message.content;
  if (typeof message.thinking === "string" && message.thinking) {
    delta.reasoning_content = message.thinking;
  }
  if (Array.isArray(message.tool_calls)) {
    const toolCalls = message.tool_calls
      .map((call, index) => nativeOllamaToolCall(call, index))
      .filter((call): call is JsonRecord => Boolean(call));
    if (toolCalls.length) {
      delta.tool_calls = toolCalls;
      sawToolCall.value = true;
    }
  }
  const choice: JsonRecord = {
    index: 0,
    delta,
    finish_reason: null,
  };
  if (event.done) {
    choice.finish_reason = sawToolCall.value
      ? "tool_calls"
      : String(event.done_reason || "stop");
  }
  const chunk: JsonRecord = {
    id: requestId,
    object: "chat.completion.chunk",
    created,
    model: modelId,
    choices: [choice],
  };
  const promptTokens = Number(event.prompt_eval_count);
  const completionTokens = Number(event.eval_count);
  if (event.done && Number.isSafeInteger(promptTokens) && Number.isSafeInteger(completionTokens)) {
    chunk.usage = {
      prompt_tokens: promptTokens,
      completion_tokens: completionTokens,
      total_tokens: promptTokens + completionTokens,
    };
  }
  return chunk;
}

function ollamaNativeFetch(
  originalFetch: FetchFunction,
  model: Model<Api>,
  contextWindowTokens: number,
  providerId: string,
): FetchFunction {
  return async (input, init) => {
    const url = requestUrl(input);
    if (!isOllamaEndpoint(model.baseUrl, providerId) || !url.includes("/chat/completions")) {
      return originalFetch(input, init);
    }
    const bodyText = requestBodyText(init?.body);
    const payload = JSON.parse(bodyText) as unknown;
    if (!isRecord(payload)) throw new Error("Ollama request body must be an object");
    const nativeUrl = new URL(url);
    nativeUrl.pathname = "/api/chat";
    nativeUrl.search = "";
    const nativeResponse = await originalFetch(nativeUrl, {
      ...init,
      body: JSON.stringify(nativeOllamaPayload(payload, contextWindowTokens, model.id)),
    });
    if (!nativeResponse.ok || !nativeResponse.body) return nativeResponse;

    const reader = nativeResponse.body.getReader();
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    const requestId = `ollama-${Date.now().toString(36)}`;
    const created = Math.floor(Date.now() / 1000);
    const sawToolCall = { value: false };
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        void (async () => {
          let buffer = "";
          try {
            while (true) {
              const next = await reader.read();
              if (next.done) break;
              buffer += decoder.decode(next.value, { stream: true });
              const lines = buffer.split(/\r?\n/);
              buffer = lines.pop() || "";
              for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;
                const event = JSON.parse(trimmed) as unknown;
                if (!isRecord(event)) throw new Error("Ollama returned a non-object event");
                const chunk = openAIChunkFromOllama(event, model.id, requestId, created, sawToolCall);
                controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
              }
            }
            const tail = decoder.decode();
            buffer += tail;
            if (buffer.trim()) {
              const event = JSON.parse(buffer) as unknown;
              if (!isRecord(event)) throw new Error("Ollama returned a non-object event");
              const chunk = openAIChunkFromOllama(event, model.id, requestId, created, sawToolCall);
              controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
            }
            controller.enqueue(encoder.encode("data: [DONE]\n\n"));
            controller.close();
          } catch (error) {
            controller.error(error);
          }
        })();
      },
      cancel(reason) {
        void reader.cancel(reason);
      },
    });
    const headers = new Headers(nativeResponse.headers);
    headers.set("content-type", "text/event-stream");
    headers.delete("content-length");
    headers.delete("content-encoding");
    return new Response(stream, {
      status: nativeResponse.status,
      statusText: nativeResponse.statusText,
      headers,
    });
  };
}

export function assertProviderRequest(
  payload: unknown,
  descriptor: ModelRuntimeDescriptor,
  api: Api,
): void {
  if (!isRecord(payload)) throw new Error("Provider payload must be an object");
  if (api === "openai-completions" || api === "anthropic-messages") {
    if (!Array.isArray(payload.messages)) throw new Error("Provider payload is missing messages");
  } else if (api === "openai-responses") {
    if (!Array.isArray(payload.input)) throw new Error("Provider payload is missing Responses input");
  } else {
    throw new Error(`Unsupported ScanSci provider API: ${String(api)}`);
  }
  if (payload.model !== descriptor.model_id) {
    throw new Error("Provider payload model does not match the trusted runtime descriptor");
  }
  const outputFields = ["max_tokens", "max_completion_tokens", "max_output_tokens"] as const;
  const presentOutputFields = outputFields.filter((field) => payload[field] !== undefined);
  const allowedOutputFields = api === "openai-responses"
    ? new Set(["max_output_tokens"])
    : api === "anthropic-messages"
      ? new Set(["max_tokens"])
      : new Set(["max_tokens", "max_completion_tokens"]);
  if (
    presentOutputFields.length !== 1
    || !allowedOutputFields.has(presentOutputFields[0])
  ) {
    throw new Error("Provider payload is missing its bounded output budget");
  }
  const requestedOutput = payload[presentOutputFields[0]];
  if (
    requestedOutput !== undefined
    && (typeof requestedOutput !== "number"
      || !Number.isSafeInteger(requestedOutput)
      || requestedOutput < 1
      || requestedOutput > descriptor.max_output_tokens)
  ) {
    throw new Error("Provider payload output budget exceeds the trusted runtime descriptor");
  }
  const estimatedTokens = conservativeTokens(payload, descriptor);
  if (estimatedTokens > descriptor.provider_input_tokens) {
    throw new Error(
      `Provider input budget exceeded before network request: estimated ${estimatedTokens} tokens `
      + `(limit ${descriptor.provider_input_tokens}). Compact the session before retrying.`,
    );
  }
}

export function scansciStreamSimple(descriptor: ModelRuntimeDescriptor) {
  return (
    model: Model<Api>,
    context: Context,
    options?: SimpleStreamOptions,
  ): AssistantMessageEventStream => {
    const originalOnPayload = options?.onPayload;
    const guardedOptions: SimpleStreamOptions = {
      ...options,
      maxTokens: Math.min(
        descriptor.max_output_tokens,
        Number(options?.maxTokens || descriptor.max_output_tokens),
      ),
      onPayload: async (payload, finalModel) => {
        const projected = await originalOnPayload?.(payload, finalModel);
        const finalPayload = projected === undefined ? payload : projected;
        // This check is deliberately below extension hooks. The Pi extension
        // runner catches hook exceptions, but an exception here aborts before
        // the provider SDK's HTTP create() call.
        assertProviderRequest(finalPayload, descriptor, finalModel.api);
        return finalPayload;
      },
    };
    if (model.api === "openai-completions" && isOllamaEndpoint(model.baseUrl, descriptor.provider_id)) {
      guardedOptions.fetch = ollamaNativeFetch(
        options?.fetch || globalThis.fetch,
        model,
        descriptor.context_window_tokens,
        descriptor.provider_id,
      );
    }
    if (model.api === "openai-completions") {
      return streamOpenAICompletions(model, context, guardedOptions);
    }
    if (model.api === "openai-responses") {
      return streamOpenAIResponses(model, context, guardedOptions);
    }
    if (model.api === "anthropic-messages") {
      return streamAnthropic(model, context, guardedOptions);
    }
    throw new Error(`Unsupported ScanSci provider API: ${String(model.api)}`);
  };
}
