from abc import ABC, abstractmethod
from typing import List, Dict

class BaseSearchProvider(ABC):
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        pass

