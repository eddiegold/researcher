from .base import BaseSearchProvider
from .tavily import TavilyProvider
from .brave import BraveProvider
from .serper import SerperProvider

PROVIDERS = {
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "serper": SerperProvider,
}

def get_provider(name: str, api_key: str) -> BaseSearchProvider:
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Choose from: {list(PROVIDERS.keys())}")
    return PROVIDERS[name](api_key)

