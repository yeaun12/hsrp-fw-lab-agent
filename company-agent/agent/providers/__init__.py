"""제공자 팩토리. config의 선택과 키에 따라 Provider 인스턴스를 만든다."""
import config

from .anthropic_provider import AnthropicProvider
from .base import Provider, Response
from .gemini import GeminiProvider
from .mock import MockProvider


def get_provider(name=None):
    """활성 제공자 인스턴스를 돌려준다. 키가 없으면 Provider.available()이 False."""
    name = name or config.active_provider()
    cfg = config.PROVIDERS[name]
    model, price = cfg["model"], cfg["price"]
    if name == "mock":
        return MockProvider(model, price)
    if name == "gemini":
        return GeminiProvider(model, price, config.provider_key("gemini"), max_tokens=config.MAX_TOKENS)
    if name == "anthropic":
        return AnthropicProvider(model, price, config.provider_key("anthropic"),
                                 effort=config.EFFORT, max_tokens=config.MAX_TOKENS)
    return MockProvider(config.PROVIDERS["mock"]["model"], config.PROVIDERS["mock"]["price"])


def provider_info():
    """대시보드 표시용: 활성 제공자, 라벨, 모델, 준비 여부."""
    name = config.active_provider()
    p = get_provider(name)
    ok, msg = p.available()
    return {"provider": name, "label": config.PROVIDERS[name]["label"], "model": p.model,
            "ready": ok, "message": msg,
            "options": [{"name": k, "label": v["label"], "needs_key": bool(v["key_secret"])}
                        for k, v in config.PROVIDERS.items()]}
