import requests
from typing import List, Dict
from .base import BaseSearchProvider


class BraveProvider(BaseSearchProvider):
    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        params = {"q": query, "count": max_results}
        response = requests.get(self.BASE_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        results = []
        for r in data.get("web", {}).get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
                "content": r.get("description", ""),
            })
        return results
