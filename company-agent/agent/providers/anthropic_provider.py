"""Anthropic Claude 제공자 (기존 동작 유지). anthropic SDK 사용."""
from .base import Provider, Response

try:
    import anthropic
except ImportError:
    anthropic = None


def _to_anthropic(messages):
    """표준 형식 -> Anthropic messages. tool_result의 name 필드는 떼어낸다(Anthropic 미지원)."""
    out = []
    for m in messages:
        if m["role"] == "system":
            # 대화 중 system 메시지는 지원 모델에서만. 간단히 user 텍스트로 합치지 않고 그대로 둔다.
            out.append({"role": "system", "content": m["content"]})
            continue
        if isinstance(m["content"], list):
            blocks = []
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    blocks.append({"type": "tool_result", "tool_use_id": b["tool_use_id"],
                                   "content": b["content"], "is_error": b.get("is_error", False)})
                else:
                    blocks.append(b)
            out.append({"role": m["role"], "content": blocks})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model, price, api_key, effort="medium", max_tokens=8000):
        super().__init__(model, price)
        self.api_key = api_key
        self.effort = effort
        self.max_tokens = max_tokens

    def available(self):
        if anthropic is None:
            return False, "anthropic 패키지가 설치되지 않았습니다."
        if not self.api_key:
            return False, "Anthropic API 키가 없습니다. 대시보드에서 등록하세요."
        return True, ""

    def generate(self, system, messages, tools):
        client = anthropic.Anthropic(api_key=self.api_key, max_retries=3)
        try:
            resp = client.beta.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools, messages=_to_anthropic(messages),
                cache_control={"type": "ephemeral"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.AuthenticationError:
            return Response([], "error", error="Claude API 키가 올바르지 않습니다. 키를 다시 등록하세요.")
        except anthropic.RateLimitError:
            return Response([], "error", error="Claude 사용량 한도에 걸렸습니다. 잠시 후 다시 시도하세요.")
        except anthropic.APIStatusError as e:
            return Response([], "error", error=f"Claude API 오류 ({e.status_code}): {e.message}")
        except anthropic.APIConnectionError:
            return Response([], "error", error="Claude에 연결하지 못했습니다. 에이전트 PC 인터넷(NAT)을 확인하세요.")

        u = resp.usage
        usage = {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
        stop = resp.stop_reason
        stop = {"pause_turn": "tool_use"}.get(stop, stop)   # pause_turn은 계속 진행으로 취급
        return Response(resp.to_dict()["content"], stop, usage=usage)
