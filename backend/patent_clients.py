import os
import httpx
import base64
import logging
import asyncio
from functools import wraps
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PatentClients")

def async_retry(max_retries=3, base_delay=2):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    if e.response.status_code in [400, 403, 404]:
                        logger.error(f"Client error {e.response.status_code}, aborting retry.")
                        raise e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Request failed: {e}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Request failed after {max_retries} attempts. Final error: {e}")
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Request failed: {e}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Request failed after {max_retries} attempts. Final error: {e}")
            raise last_exception
        return wrapper
    return decorator

load_dotenv()

EPO_CONSUMER_KEY = os.getenv("EPO_CONSUMER_KEY", "")
EPO_CONSUMER_SECRET = os.getenv("EPO_CONSUMER_SECRET", "")
USPTO_API_KEY = os.getenv("USPTO_API_KEY", "")

import json

def format_patent_results(data, source="EPO"):
    """
    通用专利数据清洗器：从复杂的 API 返回值中提取核心字段，
    生成对 LLM 友好的 Markdown 文本。
    """
    try:
        results = []
        if source == "EPO":
            # 尝试按 EPO OPS v3.2 结构解析
            docs = data.get("ops:world-patent-data", {}).get("ops:biblio-search", {}).get("ops:search-result", {}).get("exchange-documents", [])
            # 有时只有一条记录时，API 可能会返回 dict 而不是 list
            if isinstance(docs, dict):
                docs = [docs]
            elif not isinstance(docs, list):
                docs = []
            
            for d in docs:
                doc = d.get("exchange-document", d)
                
                # 提取标题
                title = "Unknown Title"
                biblio = doc.get("bibliographic-data", {})
                t_data = biblio.get("invention-title", [])
                if isinstance(t_data, list) and len(t_data) > 0:
                    title = t_data[0].get("$", title)
                elif isinstance(t_data, dict):
                    title = t_data.get("$", title)
                    
                # 提取摘要
                abstract = "无摘要"
                abs_data = doc.get("abstract", [])
                if isinstance(abs_data, list) and len(abs_data) > 0:
                    p_data = abs_data[0].get("p", {})
                    abstract = p_data.get("$", abstract) if isinstance(p_data, dict) else str(p_data)
                elif isinstance(abs_data, dict):
                    p_data = abs_data.get("p", {})
                    abstract = p_data.get("$", abstract) if isinstance(p_data, dict) else str(p_data)

                # 提取专利号并添加原文链接
                doc_number = doc.get("@doc-number", "Unknown ID")
                url = f"https://worldwide.espacenet.com/patent/search?q={doc_number}"
                results.append(f"【专利号】: [EPO-{doc_number}]({url})\n【标题】: {title}\n【摘要】: {abstract}")
                
        elif source == "USPTO":
            # USPTO 的数据通常是一个列表或者包含 results 的字典
            docs = data.get("results", data) if isinstance(data, dict) else data
            if not isinstance(docs, list):
                docs = [docs]
                
            for doc in docs[:10]: # 最多看前10条
                if not isinstance(doc, dict):
                    continue
                title = doc.get("inventionTitle") or doc.get("title") or "Unknown Title"
                abstract = doc.get("abstractText") or doc.get("abstract") or "无摘要"
                patent_id = doc.get("patentNumber") or doc.get("documentId") or "Unknown ID"
                app_date = doc.get("filingDate") or doc.get("appDate") or "Unknown Date"
                url = f"https://patentcenter.uspto.gov/applications/{patent_id}"
                results.append(f"【专利号】: [US-{patent_id}]({url}) (申请日: {app_date})\n【标题】: {title}\n【摘要】: {abstract}")
        
        if not results:
            return f"未能精确解析出专利列表，原始返回节选：\n{json.dumps(data, ensure_ascii=False)[:1500]}"
            
        # 限制返回给 LLM 的条数，最多返回 5 条
        return "\n---\n".join(results[:5])
        
    except Exception as e:
        logger.error(f"解析专利数据失败: {e}")
        return f"解析数据异常，原始数据节选：\n{json.dumps(data, ensure_ascii=False)[:1000]}"

def extract_all_text(data, ignore_keys=None):
    if ignore_keys is None:
        ignore_keys = {"@document-id-type", "@system", "@family-id", "@country", "@doc-number", "@kind"}
    if isinstance(data, dict):
        if "$" in data:
            return str(data["$"])
        texts = []
        for k, v in data.items():
            if k in ignore_keys or k.startswith("@"):
                continue
            t = extract_all_text(v, ignore_keys)
            if t and t.strip():
                texts.append(t.strip())
        return "\n".join(texts)
    elif isinstance(data, list):
        texts = []
        for item in data:
            t = extract_all_text(item, ignore_keys)
            if t and t.strip():
                texts.append(t.strip())
        return "\n".join(texts)
    elif isinstance(data, str):
        return data
    else:
        return str(data) if data else ""

class EPOClient:
    def __init__(self):
        self.base_url = "https://ops.epo.org/3.2"
        self.access_token = None

    async def _get_access_token(self):
        auth_string = f"{EPO_CONSUMER_KEY}:{EPO_CONSUMER_SECRET}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        logger.info(f"===> [EPO API] Requesting EPO Access Token...")
        async with httpx.AsyncClient(timeout=30.0) as client:
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

    @async_retry(max_retries=3, base_delay=2)
    async def search_patents(self, query: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Searching patents with query: '{query}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
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
            
            
            if response.status_code == 400:
                logger.warning("<=== [EPO API] Response Status: 400 (Bad Request - Invalid CQL syntax)")
                return f"EPO 未能识别该查询语法，请尝试更简单的关键词组合: '{query}' (400 Bad Request)。"
                
            logger.info(f"<=== [EPO API] Response Status: {response.status_code}")
            response.raise_for_status()
            raw_data = response.json()
            return format_patent_results(raw_data, source="EPO")

    @async_retry(max_retries=3, base_delay=2)
    async def get_patent_claims(self, reference_id: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Fetching claims for: '{reference_id}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/rest-services/published-data/publication/epodoc/{reference_id}/claims"
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
            )
            if response.status_code == 401:
                await self._get_access_token()
                response = await client.get(url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"})
                
            if response.status_code == 404:
                return f"未找到专利 {reference_id} 的权利要求信息 (404 Not Found)。"
                
            response.raise_for_status()
            data = response.json()
            claims_text = extract_all_text(data.get("ops:world-patent-data", {}))
            return f"【{reference_id} 权利要求】:\n{claims_text[:5000]}"
            
    @async_retry(max_retries=3, base_delay=2)
    async def get_patent_description(self, reference_id: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Fetching description for: '{reference_id}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/rest-services/published-data/publication/epodoc/{reference_id}/description"
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
            )
            if response.status_code == 401:
                await self._get_access_token()
                response = await client.get(url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"})
                
            if response.status_code == 404:
                return f"未找到专利 {reference_id} 的说明书信息 (404 Not Found)。"
                
            response.raise_for_status()
            data = response.json()
            desc_text = extract_all_text(data.get("ops:world-patent-data", {}))
            return f"【{reference_id} 说明书(节选)】:\n{desc_text[:5000]}..."

    @async_retry(max_retries=3, base_delay=2)
    async def get_patent_family(self, reference_id: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Fetching family for: '{reference_id}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/rest-services/family/epodoc/{reference_id}/biblio"
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
            )
            if response.status_code == 401:
                await self._get_access_token()
                response = await client.get(url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"})
                
            if response.status_code == 404:
                return f"未找到专利 {reference_id} 的同族信息 (404 Not Found)。"
                
            response.raise_for_status()
            data = response.json()
            docs = data.get("ops:world-patent-data", {}).get("ops:family-retrieval", {}).get("ops:family-member", [])
            if not isinstance(docs, list):
                docs = [docs]
            
            res_str = []
            for doc in docs:
                pub_ref = doc.get("publication-reference", {}).get("document-id", [])
                doc_number = "Unknown"
                if isinstance(pub_ref, list):
                    for pr in pub_ref:
                        if pr.get("@document-id-type") == "epodoc":
                            doc_number = pr.get("doc-number", {}).get("$", "Unknown")
                            break
                elif isinstance(pub_ref, dict):
                    doc_number = pub_ref.get("doc-number", {}).get("$", "Unknown")
                    
                if doc_number != "Unknown":
                    res_str.append(f"- {doc_number}")
                
            if not res_str:
                return f"未解析到专利 {reference_id} 的有效同族编号。"
            return f"【{reference_id} 的同族专利】:\n" + "\n".join(res_str)

    @async_retry(max_retries=3, base_delay=2)
    async def get_legal_status(self, reference_id: str):
        if not self.access_token:
            await self._get_access_token()
            
        logger.info(f"===> [EPO API] Fetching legal status for: '{reference_id}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/rest-services/legal-status/epodoc/{reference_id}"
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
            )
            if response.status_code == 401:
                await self._get_access_token()
                response = await client.get(url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"})
                
            if response.status_code == 404:
                return f"未找到专利 {reference_id} 的法律状态 (404 Not Found)。"
                
            response.raise_for_status()
            data = response.json()
            events = data.get("ops:world-patent-data", {}).get("ops:legal-status-retrieval", {}).get("ops:legal-status-data", {}).get("ops:legal-status-event", [])
            if not isinstance(events, list):
                events = [events]
                
            event_strs = []
            for ev in events:
                code = ev.get("ops:event-code", {}).get("$", "")
                desc = ev.get("ops:event-desc", {}).get("$", "")
                date = ev.get("ops:event-date", {}).get("$", "")
                if date or desc:
                    event_strs.append(f"[{date}] Code: {code} - {desc}")
            
            if not event_strs:
                return f"未找到专利 {reference_id} 的法律事件记录。"
            return f"【{reference_id} 的法律状态事件】:\n" + "\n".join(event_strs[:10])

class USPTOClient:
    def __init__(self):
        self.base_url = "https://api.uspto.gov/api/v1/patent/applications"
        
    @async_retry(max_retries=2, base_delay=2)
    async def search_patents(self, query: str):
        logger.info(f"===> [USPTO API] Searching patents with query: '{query}'")
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "X-API-KEY": USPTO_API_KEY,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
            
            # 根据 docs/swagger.yaml，/search 是一个 POST 接口，
            # 且当没有检索到结果时，它会奇葩地返回 404 Not Found 而不是空列表 200 OK。
            response = await client.post(f"{self.base_url}/search", json={"q": query}, headers=headers)
            
            if response.status_code == 404:
                logger.info("<=== [USPTO API] Response Status: 404 (No matching records found)")
                return f"USPTO 未检索到与 '{query}' 完全匹配的专利 (404 No matching records found)。"
                
            logger.info(f"<=== [USPTO API] Final Response Status: {response.status_code}")
            response.raise_for_status()
            raw_data = response.json()
            return format_patent_results(raw_data, source="USPTO")

epo_client = EPOClient()
uspto_client = USPTOClient()
