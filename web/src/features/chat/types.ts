/**
 * Domain types for the Chat panel.
 *
 * The wire protocol (WS /chat/ws StreamEvent) lives in
 * docs/web-api.md and src/alb/agent/loop.py::run_stream — these
 * structs are the client-side projection.
 */

export type ChatRole = "user" | "assistant";

export interface ChatError {
  code: string;
  message: string;
  suggestion?: string;
}

export type ToolCallStatus = "running" | "done";

export interface ToolCallEntry {
  /** id assigned by the backend; stable across start/end events. */
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  /** Raw result payload from tool_call_end (any shape). */
  result?: unknown;
  status: ToolCallStatus;
  startedAt: number;
  endedAt?: number;
}

export type TurnStatus =
  | "pending"     // request sent, no event back yet
  | "streaming"   // tokens are flowing
  | "done"        // server emitted ok=true done
  | "error"       // server emitted ok=false done OR transport failure
  | "cancelled";  // user clicked cancel before done

export interface ChatTurn {
  id: string;
  role: ChatRole;
  content: string;
  toolCalls: ToolCallEntry[];
  artifacts: string[];
  status: TurnStatus;
  error?: ChatError;
  timingMs?: number;
  model?: string;
  /** original user prompt — only populated on assistant turns to support 重跑 */
  sourcePrompt?: string;
}

export interface ChatRequestPayload {
  message: string;
  session_id: string | null;
  backend: string;
  model: string | null;
  tools: boolean;
  max_turns: number;
}

/** A single event from the server stream. Shape mirrors loop.run_stream. */
export type StreamEvent =
  | { type: "token"; delta: string }
  | {
      type: "tool_call_start";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | { type: "tool_call_end"; id: string; name: string; result: unknown }
  | {
      type: "done";
      ok: boolean;
      data?: string;
      session_id?: string;
      artifacts?: string[];
      timing_ms?: number;
      model?: string;
      backend?: string;
      usage?: Record<string, number>;
      error?: ChatError;
    };
