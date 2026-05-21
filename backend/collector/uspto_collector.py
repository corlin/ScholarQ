"""
USPTO 专利批量采集器
基于 USPTOClient 进行搜索和元数据采集
"""

import logging
from typing import Optional

from backend.patent_clients import uspto_client
from backend.database.db import insert_patent, patent_exists

logger = logging.getLogger("ScholarQ.USPTOCollector")


async def collect_patents(
    query: str,
    limit: int = 10,
    on_progress=None,
) -> dict:
    """
    批量采集 USPTO 专利并写入本地数据库。
    
    Args:
        query: 搜索关键词
        limit: 最大采集数量
        on_progress: 可选的进度回调 async fn(collected, total)
    
    Returns:
        {"total_found": int, "collected": int}
    """
    collected = 0
    total_found = 0

    try:
        raw_data = await _search_raw(query, limit)
        if isinstance(raw_data, str):
            logger.warning(f"[USPTO] Search returned message: {raw_data}")
            return {"total_found": 0, "collected": 0, "message": raw_data}

        patent_records = _extract_patents(raw_data)
        total_found = raw_data.get("recordTotalQuantity", len(patent_records))
        logger.info(f"[USPTO] Found {total_found} patents for query: '{query}'")

    except Exception as e:
        logger.error(f"[USPTO] Search failed: {e}")
        return {"total_found": 0, "collected": 0, "error": str(e)}

    for record in patent_records[:limit]:
        patent_number = record.get("patent_number", "")
        if not patent_number:
            continue

        if await patent_exists("USPTO", patent_number):
            collected += 1
            logger.info(f"[USPTO] Skip existing: {patent_number}")
            if on_progress:
                await on_progress(collected, total_found)
            continue

        await insert_patent(record)
        collected += 1
        logger.info(f"[USPTO] Collected [{collected}/{total_found}]: {patent_number}")

        if on_progress:
            await on_progress(collected, total_found)

    return {"total_found": total_found, "collected": collected}


async def _search_raw(query: str, limit: int = 10) -> dict:
    """执行 USPTO 搜索并返回原始 JSON 数据"""
    import httpx
    import os

    search_query = uspto_client._build_query(query)
    logger.info(f"[USPTO] Searching with query: '{search_query}'")

    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{uspto_client.base_url}/search?q={search_query}&limit={limit}&offset=0"
        headers = {
            "X-API-KEY": os.getenv("USPTO_API_KEY", ""),
            "Accept": "application/json",
        }
        response = await client.get(url, headers=headers)

        if response.status_code == 404:
            body = {}
            try:
                body = response.json()
            except Exception:
                pass
            detail = body.get("detailedMessage", "")
            if "No matching records" in detail:
                return f"USPTO 未检索到匹配结果: '{query}'"
            return f"USPTO API 端点不可用 (404)"

        response.raise_for_status()
        return response.json()


def _extract_patents(data: dict) -> list:
    """从 USPTO 搜索结果中提取专利记录"""
    records = []
    docs = data.get("patentFileWrapperDataBag", [])
    if not isinstance(docs, list):
        docs = [docs]

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        meta = doc.get("applicationMetaData", {})
        app_no = doc.get("applicationNumberText", meta.get("applicationNumberText", ""))

        records.append({
            "source": "USPTO",
            "patent_number": app_no,
            "title": meta.get("inventionTitle", ""),
            "applicant": meta.get("firstApplicantName", ""),
            "inventor": meta.get("firstInventorName", ""),
            "filing_date": meta.get("filingDate", ""),
            "abstract": "",  # USPTO ODP API 搜索结果不含摘要
        })

    return records
