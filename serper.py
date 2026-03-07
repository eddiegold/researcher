import requests
from typing import List, Dict
from .base import BaseSearchProvider


class SerperProvider(BaseSearchProvider):
    BASE_URL = "https://google.serper.dev/search"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": max_results}
        response = requests.post(self.BASE_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        results = []
        for r in data.get("organic", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("snippet", ""),
                "content": r.get("snippet", ""),
            })
        return results
