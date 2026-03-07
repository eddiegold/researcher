import requests
from typing import List, Dict
from .base import BaseSearchProvider


class TavilyProvider(BaseSearchProvider):
    BASE_URL = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_raw_content": True,
        }
        response = requests.post(self.BASE_URL, json=payload)
        response.raise_for_status()
        data = response.json()

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("content", "")[:500],
                "content": r.get("raw_content") or r.get("content", ""),
            })
        return results
