import os
import httpx
import base64
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PatentClients")

load_dotenv()

EPO_CONSUMER_KEY = os.getenv("EPO_CONSUMER_KEY", "")
EPO_CONSUMER_SECRET = os.getenv("EPO_CONSUMER_SECRET", "")
USPTO_API_KEY = os.getenv("USPTO_API_KEY", "")

class EPOClient:
    def __init__(self):
        self.base_url = "https://ops.epo.org/3.2"
        self.access_token = None

    async def _get_access_token(self):
        auth_string = f"{EPO_CONSUMER_KEY}:{EPO_CONSUMER_SECRET}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        logger.info(f"===> [EPO API] Requesting EPO Access Token...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/auth/accesstoken",
                headers={
                    "Authorization": f"Basic {encoded_auth}",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                data={"grant_type": "client_credentials"}
            )
            response.raise_for_status()
            self.access_token = response.json().get("access_token")
            logger.info(f"<=== [EPO API] Access Token fetched successfully.")

    async def search_patents(self, query: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Searching patents with query: '{query}'")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/rest-services/published-data/search/biblio",
                params={"q": query},
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json"
                }
            )
            if response.status_code == 401:
                logger.warning("<=== [EPO API] Token expired. Refreshing token and retrying...")
                await self._get_access_token()
                response = await client.get(
                    f"{self.base_url}/rest-services/published-data/search/biblio",
                    params={"q": query},
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Accept": "application/json"
                    }
                )
            
            logger.info(f"<=== [EPO API] Response Status: {response.status_code}")
            response.raise_for_status()
            return response.json()

class USPTOClient:
    def __init__(self):
        self.base_url = "https://api.uspto.gov/api/v1/patent/applications"
        
    async def search_patents(self, query: str):
        logger.info(f"===> [USPTO API] Searching patents with query: '{query}'")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": query},
                headers={
                    "x-api-key": USPTO_API_KEY,
                    "Accept": "application/json"
                }
            )
            logger.info(f"<=== [USPTO API] Response Status: {response.status_code}")
            response.raise_for_status()
            return response.json()

epo_client = EPOClient()
uspto_client = USPTOClient()
