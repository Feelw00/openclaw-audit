#!/usr/bin/env node
/**
 * mock-openai-server.mjs (scripts/e2e/) 의 fault injection 변형.
 *
 * 기본 모드 (MOCK_FAULT_MODE=normal 또는 미설정) 는 mock-openai-server.mjs 와 거의 동일.
 * fault 모드는 N 번째 /v1/responses 요청부터 다른 행동:
 *  - stream-hold-then-close: SSE 응답 시작 → MOCK_HOLD_MS 동안 chunk 없이 hold → res.destroy()
 *  - stream-immediate-close: 헤더만 보내고 즉시 destroy
 *  - stream-error-event: SSE 로 error event 보낸 뒤 destroy
 *
 * env 인터페이스:
 *  MOCK_PORT (필수, mock-openai-server.mjs 와 동일)
 *  SUCCESS_MARKER (기본 OPENCLAW_E2E_OK)
 *  MOCK_REQUEST_LOG (선택, JSONL append)
 *  MOCK_FAULT_MODE (normal|stream-hold-then-close|stream-immediate-close|stream-error-event, 기본 normal)
 *  MOCK_HOLD_MS (기본 5000)
 *  REQUEST_INDEX_FAULT (몇 번째 /v1/responses 부터 fault 적용, 기본 1)
 */
import fs from "node:fs";
import http from "node:http";

const port = Number(process.env.MOCK_PORT ?? process.env.OPENCLAW_MOCK_OPENAI_PORT);
const successMarker = process.env.SUCCESS_MARKER ?? "OPENCLAW_E2E_OK";
const requestLog = process.env.MOCK_REQUEST_LOG;
const faultMode = process.env.MOCK_FAULT_MODE ?? "normal";
const holdMs = Number(process.env.MOCK_HOLD_MS ?? "5000");
const faultThreshold = Number(process.env.REQUEST_INDEX_FAULT ?? "1");

if (!Number.isInteger(port) || port <= 0) {
  throw new Error("missing valid MOCK_PORT");
}

let responsesRequestCount = 0;

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

function resolveResponseText(bodyText) {
  const matches = Array.from(bodyText.matchAll(/\bOPENCLAW_E2E_OK(?:_\w+)?\b/gu));
  return matches.at(-1)?.[0] ?? successMarker;
}

async function handleResponsesEndpoint(req, res, bodyText, body) {
  responsesRequestCount += 1;
  const applyFault = faultMode !== "normal" && responsesRequestCount >= faultThreshold;

  if (!applyFault) {
    const responseText = resolveResponseText(bodyText);
    if (body.stream === false) {
      writeJson(res, 200, {
        id: "resp_e2e",
        object: "response",
        status: "completed",
        output: [
          {
            type: "message",
            id: "msg_e2e_1",
            role: "assistant",
            status: "completed",
            content: [{ type: "output_text", text: responseText, annotations: [] }],
          },
        ],
        usage: { input_tokens: 11, output_tokens: 7, total_tokens: 18 },
      });
      return;
    }
    writeSseNormal(res, responseEvents(responseText));
    return;
  }

  // fault 모드 branch
  if (faultMode === "stream-immediate-close") {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-store",
      connection: "keep-alive",
    });
    res.destroy(new Error("mock fault: immediate close"));
    return;
  }

  if (faultMode === "stream-hold-then-close") {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-store",
      connection: "keep-alive",
    });
    // SSE 시작 marker 만 보내 SUT 가 streaming 상태 진입하도록 유도
    res.write(`data: ${JSON.stringify(responseEvents("hold-start")[0])}\n\n`);
    await new Promise((resolve) => setTimeout(resolve, holdMs));
    res.destroy(new Error("mock fault: hold then abrupt close"));
    return;
  }

  if (faultMode === "stream-error-event") {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-store",
      connection: "keep-alive",
    });
    res.write(
      `data: ${JSON.stringify({ type: "response.error", error: { message: "mock fault: error event", type: "server_error" } })}\n\n`,
    );
    res.end();
    return;
  }

  // 알려지지 않은 mode → fallback to normal
  const responseText = resolveResponseText(bodyText);
  writeSseNormal(res, responseEvents(responseText));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/health") {
    writeJson(res, 200, { ok: true, fault_mode: faultMode, responses_count: responsesRequestCount });
    return;
  }
  if (req.method === "GET" && url.pathname === "/v1/models") {
    writeJson(res, 200, {
      object: "list",
      data: [{ id: "gpt-5.5", object: "model", owned_by: "openclaw-e2e" }],
    });
    return;
  }

  const bodyText = await readBody(req);
  if (requestLog) {
    fs.appendFileSync(
      requestLog,
      `${JSON.stringify({
        method: req.method,
        path: url.pathname,
        body: bodyText,
        responses_count_before: responsesRequestCount,
      })}\n`,
    );
  }
  let body = {};
  try {
    body = bodyText ? JSON.parse(bodyText) : {};
  } catch {
    body = {};
  }

  if (req.method === "POST" && url.pathname === "/v1/responses") {
    await handleResponsesEndpoint(req, res, bodyText, body);
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/chat/completions") {
    const responseText = resolveResponseText(bodyText);
    writeSseNormal(
      res,
      [
        {
          id: "chatcmpl_e2e",
          object: "chat.completion.chunk",
          choices: [{ index: 0, delta: { role: "assistant", content: responseText } }],
        },
        {
          id: "chatcmpl_e2e",
          object: "chat.completion.chunk",
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        },
      ],
    );
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/embeddings") {
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
  console.log(`mock-openai-fault listening on ${port} mode=${faultMode}`);
});
