import { isTauri, resolveApiBase } from "@/lib/tauri";
import { getPathAccessToken, globalApi } from "@/services/api";
import type { GlobalBusEnvelope } from "@/types";

type EnvelopeHandler = (envelope: GlobalBusEnvelope) => void;
type ErrorHandler = (error: Error) => void;

interface GlobalBusClientState {
  closed: boolean;
  abortController: AbortController | null;
  websocket: WebSocket | null;
  envelopeHandlers: Set<EnvelopeHandler>;
  errorHandlers: Set<ErrorHandler>;
  runner: Promise<void> | null;
}

function parseEventBlocks(chunk: string): GlobalBusEnvelope[] {
  const blocks = chunk.split("\n\n");
  const envelopes: GlobalBusEnvelope[] = [];
  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;
    const dataLines = trimmed
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());
    if (dataLines.length === 0) continue;
    try {
      const parsed = JSON.parse(dataLines.join("\n")) as GlobalBusEnvelope;
      envelopes.push(parsed);
    } catch (error) {
      console.warn("[global-bus] failed to parse envelope", error);
    }
  }
  return envelopes;
}

async function buildWebSocketUrl(): Promise<string> {
  const base = resolveApiBase().replace(/\/+$/, "");
  const wsBase = base.startsWith("https://")
    ? `wss://${base.slice("https://".length)}`
    : base.startsWith("http://")
      ? `ws://${base.slice("http://".length)}`
      : base;
  const url = new URL(`${wsBase}/global/ws`, window.location.href);
  const token = await getPathAccessToken("/global/ws");
  if (token && !url.searchParams.has("token")) {
    url.searchParams.set("token", token);
  }
  return url.toString();
}

export function subscribeGlobalBus(
  onEnvelope: EnvelopeHandler,
  onError?: ErrorHandler,
): () => void {
  GLOBAL_BUS_CLIENT.envelopeHandlers.add(onEnvelope);
  if (onError) GLOBAL_BUS_CLIENT.errorHandlers.add(onError);
  ensureGlobalBusConnection();

  return () => {
    GLOBAL_BUS_CLIENT.envelopeHandlers.delete(onEnvelope);
    if (onError) GLOBAL_BUS_CLIENT.errorHandlers.delete(onError);
    teardownGlobalBusConnectionIfIdle();
  };
}

const GLOBAL_BUS_CLIENT: GlobalBusClientState = {
  closed: true,
  abortController: null,
  websocket: null,
  envelopeHandlers: new Set<EnvelopeHandler>(),
  errorHandlers: new Set<ErrorHandler>(),
  runner: null,
};

function emitEnvelope(envelope: GlobalBusEnvelope): void {
  for (const handler of [...GLOBAL_BUS_CLIENT.envelopeHandlers]) {
    handler(envelope);
  }
}

function emitError(error: unknown): void {
  const normalized = error instanceof Error ? error : new Error(String(error));
  for (const handler of [...GLOBAL_BUS_CLIENT.errorHandlers]) {
    handler(normalized);
  }
}

async function runWebSocketConnection(): Promise<void> {
  const url = await buildWebSocketUrl();
  await new Promise<void>((resolve) => {
    let settled = false;
    const socket = new WebSocket(url);
    GLOBAL_BUS_CLIENT.websocket = socket;

    const cleanup = () => {
      if (settled) return;
      settled = true;
      if (GLOBAL_BUS_CLIENT.websocket === socket) {
        GLOBAL_BUS_CLIENT.websocket = null;
      }
      try {
        socket.close();
      } catch {
        // ignore close errors during teardown
      }
      resolve();
    };

    socket.onmessage = (event) => {
      try {
        const envelope = JSON.parse(String(event.data || "")) as GlobalBusEnvelope;
        emitEnvelope(envelope);
      } catch (error) {
        console.warn("[global-bus] failed to parse WebSocket envelope", error);
      }
    };

    socket.onerror = () => {
      if (GLOBAL_BUS_CLIENT.closed) {
        cleanup();
        return;
      }
      emitError(new Error("global websocket stream disconnected"));
    };

    socket.onclose = () => {
      if (!GLOBAL_BUS_CLIENT.closed) {
        emitError(new Error("global websocket stream closed"));
      }
      cleanup();
    };

    if (GLOBAL_BUS_CLIENT.closed) {
      cleanup();
    }
  });
}

async function runFetchConnection(): Promise<void> {
  GLOBAL_BUS_CLIENT.abortController = new AbortController();
  const currentAbort = GLOBAL_BUS_CLIENT.abortController;
  try {
    const response = await globalApi.events({ signal: currentAbort.signal });
    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("global event stream is missing body");
    }
    const decoder = new TextDecoder();
    let buffer = "";

    while (!GLOBAL_BUS_CLIENT.closed) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const boundary = buffer.lastIndexOf("\n\n");
      if (boundary < 0) continue;
      const ready = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const envelope of parseEventBlocks(ready)) {
        emitEnvelope(envelope);
      }
    }

    if (buffer.trim()) {
      for (const envelope of parseEventBlocks(buffer)) {
        emitEnvelope(envelope);
      }
    }
  } finally {
    if (GLOBAL_BUS_CLIENT.abortController === currentAbort) {
      GLOBAL_BUS_CLIENT.abortController = null;
    }
  }
}

async function runGlobalBusConnection(): Promise<void> {
  while (!GLOBAL_BUS_CLIENT.closed) {
    try {
      if (isTauri() && typeof WebSocket !== "undefined") {
        await runWebSocketConnection();
      } else {
        await runFetchConnection();
      }
    } catch (error) {
      if (GLOBAL_BUS_CLIENT.closed) break;
      emitError(error);
    }

    if (!GLOBAL_BUS_CLIENT.closed) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }
}

function ensureGlobalBusConnection(): void {
  GLOBAL_BUS_CLIENT.closed = false;
  if (GLOBAL_BUS_CLIENT.runner) return;
  GLOBAL_BUS_CLIENT.runner = runGlobalBusConnection().finally(() => {
    GLOBAL_BUS_CLIENT.runner = null;
    GLOBAL_BUS_CLIENT.abortController = null;
    GLOBAL_BUS_CLIENT.websocket = null;
  });
}

function teardownGlobalBusConnectionIfIdle(): void {
  if (GLOBAL_BUS_CLIENT.envelopeHandlers.size > 0 || GLOBAL_BUS_CLIENT.errorHandlers.size > 0) {
    return;
  }
  GLOBAL_BUS_CLIENT.closed = true;
  GLOBAL_BUS_CLIENT.abortController?.abort();
  GLOBAL_BUS_CLIENT.websocket?.close();
  GLOBAL_BUS_CLIENT.websocket = null;
}
