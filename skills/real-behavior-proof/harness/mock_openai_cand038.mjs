#!/usr/bin/env node
/**
 * mock-openai-server.mjs 의 CAND-038 fork.
 *
 * 차이:
 *  - 모든 /v1/responses 요청에 `req.on("close")` listener 추가 → client disconnect 발생 시
 *    MOCK_REQUEST_LOG 에 `{event: "client_disconnected", ts, requestIndex, path, completed}`
 *    append. completed=true 면 정상 SSE end 후 close, false 면 stream 도중 close.
 *  - hold-then-complete 모드: SSE 시작 → MOCK_HOLD_MS hold → 정상 SSE chunk send + end.
 *    hold 도중 client 가 끊으면 client_disconnected 기록 (with-fix 발현 측정).
 *
 * env:
 *  MOCK_PORT (필수)
 *  SUCCESS_MARKER (기본 OPENCLAW_E2E_OK)
 *  MOCK_REQUEST_LOG (선택, JSONL append — 측정 핵심)
 *  MOCK_HOLD_MS (기본 5000)
 *  MOCK_MODE (normal|hold-then-complete, 기본 hold-then-complete)
 *  REQUEST_INDEX_FAULT (몇 번째 /v1/responses 부터 hold 적용, 기본 1)
 *
 * CAND-038 e2e 측정:
 *  - probe: chat.send → SUT 가 LLM /v1/responses POST → mock 이 hold 시작
 *  - probe: hold 도중 ws.close → SUT close handler 실행 (결함 발현 측정 대상)
 *  - without-fix: SUT 의 chat runner 가 close 받아도 abort 안 함 → LLM fetch 그대로 → hold 끝
 *    + SSE complete + req close 시 completed=true 로 기록 (또는 hold 끝나기 전 timeout 으로 EVT 없음)
 *  - with-fix: SUT 의 close handler 가 chatAbortControllers iterate + abort →
 *    LLM client (fetch) signal.aborted → fetch abort → mock 측 req.on("close") fire 시점이
 *    hold 도중 + completed=false 로 기록
 *
 *  하한선: MOCK_REQUEST_LOG 에 `event:"client_disconnected"` + `completed:false` 라인 출현 →
 *  with-fix 발현 증거. without-fix 면 completed=true 또는 line 자체 미출현.
 */
import fs from "node:fs";
import http from "node:http";

const port = Number(process.env.MOCK_PORT ?? process.env.OPENCLAW_MOCK_OPENAI_PORT);
const successMarker = process.env.SUCCESS_MARKER ?? "OPENCLAW_E2E_OK";
const requestLog = process.env.MOCK_REQUEST_LOG;
const mode = process.env.MOCK_MODE ?? "hold-then-complete";
const holdMs = Number(process.env.MOCK_HOLD_MS ?? "5000");
const holdThreshold = Number(process.env.REQUEST_INDEX_FAULT ?? "1");

if (!Number.isInteger(port) || port <= 0) {
  throw new Error("missing valid MOCK_PORT");
}

let responsesRequestCount = 0;

function logEvent(record) {
  if (!requestLog) return;
  try {
    fs.appendFileSync(requestLog, `${JSON.stringify(record)}\n`);
  } catch {}
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function writeJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function responseEvents(text) {
  return [
    {
      type: "response.output_item.added",
      item: {
        type: "message",
        id: "msg_e2e_1",
        role: "assistant",
        content: [],
        status: "in_progress",
      },
    },
    {
      type: "response.output_item.done",
      item: {
        type: "message",
        id: "msg_e2e_1",
        role: "assistant",
        status: "completed",
        content: [{ type: "output_text", text, annotations: [] }],
      },
    },
    {
      type: "response.completed",
      response: {
        status: "completed",
        usage: {
          input_tokens: 11,
          output_tokens: 7,
          total_tokens: 18,
          input_tokens_details: { cached_tokens: 0 },
        },
      },
    },
  ];
}

function resolveResponseText(bodyText) {
  const matches = Array.from(bodyText.matchAll(/\bOPENCLAW_E2E_OK(?:_\w+)?\b/gu));
  return matches.at(-1)?.[0] ?? successMarker;
}

async function handleResponsesEndpoint(req, res, bodyText) {
  responsesRequestCount += 1;
  const requestIndex = responsesRequestCount;
  const applyHold = mode === "hold-then-complete" && requestIndex >= holdThreshold;
  const startedAt = Date.now();
  let completed = false;
  let holdElapsed = 0;

  // req close listener — client 가 fetch abort 또는 socket close 한 경우 fire.
  req.on("close", () => {
    logEvent({
      event: "client_disconnected",
      ts: Date.now(),
      requestIndex,
      path: "/v1/responses",
      completed,
      holdElapsed,
      startedAt,
    });
  });

  if (!applyHold) {
    const responseText = resolveResponseText(bodyText);
    writeSseNormal(res, responseEvents(responseText));
    completed = true;
    return;
  }

  // hold-then-complete: SSE 헤더 + 시작 event 1개 후 hold → 정상 SSE chunk + end.
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-store",
    connection: "keep-alive",
  });
  res.write(`data: ${JSON.stringify(responseEvents("hold-start")[0])}\n\n`);
  logEvent({ event: "hold_started", ts: Date.now(), requestIndex });

  const holdStartAt = Date.now();
  await new Promise((resolve) => setTimeout(resolve, holdMs));
  holdElapsed = Date.now() - holdStartAt;

  if (req.destroyed || res.destroyed || res.closed) {
    logEvent({
      event: "hold_aborted_before_complete",
      ts: Date.now(),
      requestIndex,
      holdElapsed,
    });
    return;
  }

  const responseText = resolveResponseText(bodyText);
  const tailEvents = responseEvents(responseText).slice(1); // first event already sent
  for (const event of tailEvents) {
    if (req.destroyed || res.destroyed || res.closed) {
      logEvent({
        event: "hold_completed_but_client_gone",
        ts: Date.now(),
        requestIndex,
        holdElapsed,
      });
      return;
    }
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  }
  res.write("data: [DONE]\n\n");
  res.end();
  completed = true;
  logEvent({ event: "stream_completed", ts: Date.now(), requestIndex, holdElapsed });
}

function writeSseNormal(res, events) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-store",
    connection: "keep-alive",
  });
  for (const event of events) {
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  }
  res.write("data: [DONE]\n\n");
  res.end();
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/health") {
    writeJson(res, 200, { ok: true, mode, responses_count: responsesRequestCount });
    return;
  }
  if (req.method === "GET" && url.pathname === "/v1/models") {
    writeJson(res, 200, {
      object: "list",
      data: [{ id: "gpt-5.5", object: "model", owned_by: "openclaw-cand038" }],
    });
    return;
  }

  const bodyText = await readBody(req);
  if (requestLog) {
    fs.appendFileSync(
      requestLog,
      `${JSON.stringify({
        event: "request_started",
        method: req.method,
        path: url.pathname,
        ts: Date.now(),
        responses_count_before: responsesRequestCount,
      })}\n`,
    );
  }

  if (req.method === "POST" && url.pathname === "/v1/responses") {
    await handleResponsesEndpoint(req, res, bodyText);
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/embeddings") {
    let body = {};
    try { body = JSON.parse(bodyText); } catch {}
    const input = Array.isArray(body.input) ? body.input : [body.input ?? ""];
    writeJson(res, 200, {
      object: "list",
      data: input.map((_, index) => ({
        object: "embedding",
        index,
        embedding: [1, index / 100, 0, 0],
      })),
      model: body.model ?? "text-embedding-3-small",
      usage: { prompt_tokens: input.length, total_tokens: input.length },
    });
    return;
  }

  writeJson(res, 404, {
    error: { message: `unhandled mock route: ${req.method} ${url.pathname}` },
  });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`mock-openai-cand038 listening on ${port} mode=${mode}`);
});
