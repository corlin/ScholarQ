import os
import httpx
from aiolimiter import AsyncLimiter
from dotenv import load_dotenv

load_dotenv()

S2_API_KEY = os.getenv("S2_API_KEY", "")

# 1 request per second according to rate limit
limiter = AsyncLimiter(1, 1)

class SemanticScholarClient:
    def __init__(self):
        self.base_url = "https://api.semanticscholar.org/graph/v1"
        self.headers = {}
        if S2_API_KEY and S2_API_KEY != "YOUR_API_KEY_HERE":
            self.headers["x-api-key"] = S2_API_KEY
            
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=30.0)

    async def search_papers(self, query: str, limit: int = 10, offset: int = 0):
        async with limiter:
            response = await self.client.get(
                "/paper/search",
                params={
                    "query": query,
                    "limit": limit,
                    "offset": offset,
                    "fields": "title,authors,year,abstract,citationCount,url,venue"
                }
            )
            response.raise_for_status()
            return response.json()
            
    async def get_paper_details(self, paper_id: str):
        async with limiter:
            response = await self.client.get(
                f"/paper/{paper_id}",
                params={
                    "fields": "title,authors,year,abstract,citationCount,references,citations,url,venue"
                }
            )
            response.raise_for_status()
            return response.json()

    async def close(self):
        await self.client.aclose()

s2_client = SemanticScholarClient()
