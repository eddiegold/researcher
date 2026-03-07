from typing import List, Dict
from search_providers.base import BaseSearchProvider

SEARCH_QUERIES = [
    "{tool} tutorial getting started guide",
    "{tool} advanced usage best practices",
    "{tool} production tips gotchas senior engineer",
    "{tool} github repository examples",
    "{tool} official documentation site:docs.{tool}.io OR site:{tool}.io",
    "{tool} vs alternatives when to use",
    "{tool} performance scale limitations",
]


class ResearcherAgent:
    def __init__(self, search_provider: BaseSearchProvider, max_results: int = 10):
        self.provider = search_provider
        self.max_results = max_results

    def run(self, tool: str) -> List[Dict]:
        """Search multiple queries and deduplicate by URL."""
        print(f"\n[Researcher] Finding sources for: {tool}")
        seen_urls = set()
        all_results = []

        for query_template in SEARCH_QUERIES:
            query = query_template.format(tool=tool)
            print(f"  → Searching: '{query}'")
            try:
                results = self.provider.search(query, max_results=4)
                for r in results:
                    if r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        r["source_query"] = query
                        all_results.append(r)
            except Exception as e:
                print(f"  ✗ Search failed: {e}")

        final = all_results[:self.max_results]
        print(f"[Researcher] Collected {len(final)} unique sources\n")
        return final
