"""Google Gemini 제공자 — REST(generateContent)를 표준 라이브러리로 호출(추가 의존성 없음).

무료 티어(Google AI Studio 키)로 Function Calling을 사용한다. 도구 스키마(Anthropic 형식)를
Gemini functionDeclarations로, 대화 기록을 Gemini contents로 변환한다.
"""
import json
import time
import urllib.error
import urllib.request

from .base import Provider, Response

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _to_contents(messages):
    contents = []
    for m in messages:
        role = m["role"]
        if role == "system":
            contents.append({"role": "user", "parts": [{"text": "[시스템 지시] " + str(m["content"])}]})
            continue
        if isinstance(m["content"], str):
            contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": m["content"]}]})
            continue
        parts = []
        for b in m["content"]:
            t = b.get("type")
            if t == "text":
                parts.append({"text": b["text"]})
            elif t == "tool_use":
                parts.append({"functionCall": {"name": b["name"], "args": b.get("input") or {}}})
            elif t == "tool_result":
                parts.append({"functionResponse": {"name": b.get("name", "tool"),
                                                    "response": {"result": str(b.get("content", ""))}}})
        contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return contents


def _to_tools(tools):
    decls = []
    for t in tools:
        schema = dict(t.get("input_schema") or {"type": "object", "properties": {}})
        schema.pop("additionalProperties", None)
        decls.append({"name": t["name"], "description": t.get("description", ""), "parameters": schema})
    return [{"functionDeclarations": decls}]


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, model, price, api_key, max_tokens=8000):
        super().__init__(model, price)
        self.api_key = api_key
        self.max_tokens = max_tokens

    def available(self):
        if not self.api_key:
            return False, "Gemini API 키가 없습니다. 대시보드에서 등록하세요 (Google AI Studio 무료 키)."
        return True, ""

    def _post(self, body):
        url = ENDPOINT.format(model=self.model, key=self.api_key)
        data = json.dumps(body).encode()
        delay = 2
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = e.read()[:400].decode(errors="replace")
                if e.code in (429, 500, 503) and attempt < 2:
                    time.sleep(delay); delay *= 2; continue
                raise RuntimeError(f"Gemini HTTP {e.code}: {detail}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(delay); delay *= 2; continue
                raise RuntimeError(f"Gemini 연결 실패: {e}")
        raise RuntimeError("Gemini 호출 재시도 초과")

    def generate(self, system, messages, tools):
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": _to_contents(messages),
                "tools": _to_tools(tools),
                "generationConfig": {"maxOutputTokens": self.max_tokens, "temperature": 0.2}}
        try:
            data = self._post(body)
        except RuntimeError as e:
            msg = str(e)
            if "HTTP 400" in msg and ("API_KEY" in msg or "API key" in msg):
                return Response([], "error", error="Gemini API 키가 올바르지 않습니다. 키를 다시 등록하세요.")
            if "HTTP 429" in msg:
                return Response([], "error", error="Gemini 무료 티어 호출 한도에 걸렸습니다. 잠시 후 다시 시도하세요.")
            return Response([], "error", error=msg)

        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "")
            return Response([], "refusal", error=f"Gemini가 응답을 거부했습니다. ({reason})")
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        content, i = [], 0
        for p in parts:
            if "text" in p and p["text"].strip():
                content.append({"type": "text", "text": p["text"]})
            elif "functionCall" in p:
                fc = p["functionCall"]
                content.append({"type": "tool_use", "id": f"gm-{i}", "name": fc["name"], "input": fc.get("args") or {}})
                i += 1
        um = data.get("usageMetadata") or {}
        usage = {"input": um.get("promptTokenCount", 0), "output": um.get("candidatesTokenCount", 0)}
        finish = cand.get("finishReason", "STOP")
        if any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            stop = "refusal"
        else:
            stop = "end_turn"
        return Response(content, stop, usage=usage)
