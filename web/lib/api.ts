// Client for the 10k-agent API (tenk serve). Research mode streams Server-Sent Events over
// a POST, so it reads the response body directly rather than using EventSource (GET only).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type Status = "verified" | "calculated" | "cited" | "unverified" | "unchecked";

export interface Claim {
  id: number;
  text: string;
  value: number | string | null;
  unit: string | null;
  company: string | null;
  fiscal_year: number | null;
  chunk_ids: string[];
  calculation: Record<string, unknown> | null;
  status: Status;
  status_reason: string;
}

export interface Citation {
  chunk_id: string;
  company: string;
  company_name: string;
  fiscal_year: number;
  period_end_date: string | null;
  item: string;
  section_title: string | null;
  url: string | null;
  kind: string;
  text: string;
}

export interface Answer {
  mode: "ask" | "research";
  question: string;
  answer: string;
  claims: Claim[];
  citations: Record<string, Citation>;
  table: { columns: string[]; rows: (string | number | null)[][] } | null;
  scope_notice: string | null;
  abstained: boolean;
  unverified: number[];
  format_error: boolean;
  model: string;
  trace_id: string;
  usage: {
    latency_s: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    llm_calls: number;
    tool_calls: number;
  };
}

export interface Step {
  n: number;
  tool: string;
  arguments: Record<string, unknown>;
  label: string;
  summary: string;
  is_error: boolean;
  latency_s: number;
}

export interface Span {
  type: "llm" | "tool" | "retrieval";
  name: string;
  model?: string;
  input: unknown;
  output: unknown;
  input_tokens?: number;
  output_tokens?: number;
  latency_s: number;
  cost_usd?: number;
  is_error?: boolean;
}

export interface Trace {
  id: string;
  created_at: string;
  mode: string;
  question: string;
  model: string;
  spans: Span[];
  totals: Answer["usage"];
  error: string | null;
}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail.slice(0, 300)}`);
  }
  return response;
}

export async function ask(question: string): Promise<Answer> {
  const response = await fetch(`${API_URL}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  return (await check(response)).json();
}

export async function getTrace(id: string): Promise<Trace> {
  return (await check(await fetch(`${API_URL}/api/traces/${id}`))).json();
}

export interface Handlers {
  onStep: (step: Step) => void;
  onThought: (text: string) => void;
  onAnswer: (answer: Answer) => void;
  onError: (message: string) => void;
}

export async function research(question: string, handlers: Handlers, signal?: AbortSignal) {
  const response = await check(
    await fetch(`${API_URL}/api/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal,
    }),
  );
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const message = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = message
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (!data) continue;
      const event = JSON.parse(data);
      if (event.type === "step") handlers.onStep(event as Step);
      else if (event.type === "thought") handlers.onThought(event.text);
      else if (event.type === "answer") handlers.onAnswer(event.answer);
      else if (event.type === "error") handlers.onError(event.message);
    }
  }
}
