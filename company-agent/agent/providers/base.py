"""제공자 공통 인터페이스.

표준(canonical) 대화 형식은 Anthropic 블록 형태를 쓴다. brain.py는 이 형식만 다루고,
각 제공자 어댑터가 자기 API 형식으로 변환한다.

messages: list[dict]
  - {"role": "user", "content": "<텍스트>"}                      사용자 입력
  - {"role": "assistant", "content": [block, ...]}              모델 응답(텍스트/도구호출)
  - {"role": "user", "content": [tool_result, ...]}             도구 실행 결과
  - {"role": "system", "content": "<텍스트>"}                    (선택) 운영 지시
block:
  - {"type": "text", "text": "..."}
  - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
tool_result:
  - {"type": "tool_result", "tool_use_id": "...", "name": "...", "content": "...", "is_error": bool}

tools: list[dict]  (Anthropic 스키마) {"name", "description", "input_schema": {JSON Schema}}
"""


class Response:
    """제공자 응답의 표준 형태."""

    def __init__(self, content, stop_reason, usage=None, error=None):
        self.content = content or []            # assistant 블록 리스트 (text / tool_use)
        self.stop_reason = stop_reason           # end_turn | tool_use | max_tokens | refusal | error
        self.usage = usage or {}                 # {"input","output","cache_read","cache_write"} (토큰)
        self.error = error                       # stop_reason == "error" 일 때 사람이 읽을 메시지

    @property
    def text_blocks(self):
        return [b["text"] for b in self.content if b.get("type") == "text" and b.get("text", "").strip()]

    @property
    def tool_uses(self):
        return [b for b in self.content if b.get("type") == "tool_use"]


class Provider:
    """제공자 어댑터 기반 클래스."""

    name = "base"

    def __init__(self, model, price):
        self.model = model
        self.price = price or {}

    def available(self):
        """(사용 가능 여부, 안내 메시지). 키가 필요한데 없으면 False."""
        return True, ""

    def generate(self, system, messages, tools):
        """표준 형식을 받아 Response를 돌려준다. 하위 클래스가 구현."""
        raise NotImplementedError

    def cost_usd(self, usage):
        return round(sum(usage.get(k, 0) * self.price.get(k, 0) for k in self.price) / 1_000_000, 6)
