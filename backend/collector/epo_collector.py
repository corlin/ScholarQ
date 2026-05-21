"""
EPO 专利批量采集器
基于 EPOClient 进行搜索 + 全文获取（权利要求、说明书、法律状态、同族专利）
"""

import asyncio
import json
import logging
from typing import Optional

from backend.patent_clients import epo_client, extract_all_text
from backend.database.db import insert_patent, patent_exists

logger = logging.getLogger("ScholarQ.EPOCollector")


async def collect_patents(
    query: str,
    limit: int = 10,
    fetch_fulltext: bool = True,
    on_progress=None,
) -> dict:
    """
    批量采集 EPO 专利并写入本地数据库。
    
    Args:
        query: CQL 搜索查询（已由 agent 的 build_patent_query 转换）
        limit: 最大采集数量
        fetch_fulltext: 是否获取全文（claims + description）
        on_progress: 可选的进度回调 async fn(collected, total)
    
    Returns:
        {"total_found": int, "collected": int}
    """
    collected = 0
    total_found = 0

    # Step 1: 搜索获取专利列表
    try:
        if not epo_client.access_token:
            await epo_client._get_access_token()

        raw_data = await _search_raw(query)
        if isinstance(raw_data, str):
            # search_patents 返回了已格式化的错误字符串
            logger.warning(f"[EPO] Search returned message: {raw_data}")
            return {"total_found": 0, "collected": 0, "message": raw_data}

        # 解析搜索结果，提取专利号列表
        patent_refs = _extract_patent_refs(raw_data)
        total_found = len(patent_refs)
        logger.info(f"[EPO] Found {total_found} patents for query: '{query}'")

    except Exception as e:
        logger.error(f"[EPO] Search failed: {e}")
        return {"total_found": 0, "collected": 0, "error": str(e)}

    # Step 2: 逐个采集详情
    for i, ref in enumerate(patent_refs[:limit]):
        patent_number = ref.get("number", "")
        if not patent_number:
            continue

        # 检查是否已存在
        if await patent_exists("EPO", patent_number):
            collected += 1
            logger.info(f"[EPO] Skip existing: {patent_number}")
            if on_progress:
                await on_progress(collected, total_found)
            continue

        patent_data = {
            "source": "EPO",
            "patent_number": patent_number,
            "title": ref.get("title", ""),
            "abstract": ref.get("abstract", ""),
        }

        # 获取全文
        if fetch_fulltext:
            await _fetch_fulltext(patent_number, patent_data)
            # 每次全文请求后暂停，遵守 EPO 限速
            await asyncio.sleep(1.5)

        await insert_patent(patent_data)
        collected += 1
        logger.info(f"[EPO] Collected [{collected}/{total_found}]: {patent_number}")

        if on_progress:
            await on_progress(collected, total_found)

    return {"total_found": total_found, "collected": collected}


async def _search_raw(query: str) -> dict:
    """执行 EPO 搜索并返回原始 JSON 数据"""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{epo_client.base_url}/rest-services/published-data/search/biblio",
            params={"q": query},
            headers={
                "Authorization": f"Bearer {epo_client.access_token}",
                "Accept": "application/json"
            }
        )
        if response.status_code == 401:
            await epo_client._get_access_token()
            response = await client.get(
                f"{epo_client.base_url}/rest-services/published-data/search/biblio",
                params={"q": query},
                headers={
                    "Authorization": f"Bearer {epo_client.access_token}",
                    "Accept": "application/json"
                }
            )
        if response.status_code == 400:
            return f"EPO CQL 语法无效: '{query}'"
        if response.status_code == 404:
            return f"EPO 未检索到结果: '{query}'"

        response.raise_for_status()
        return response.json()


def _extract_patent_refs(data: dict) -> list:
    """从 EPO 搜索结果中提取专利编号、标题、摘要"""
    refs = []
    docs = (
        data.get("ops:world-patent-data", {})
        .get("ops:biblio-search", {})
        .get("ops:search-result", {})
        .get("exchange-documents", [])
    )
    if isinstance(docs, dict):
        docs = [docs]

    for d in docs:
        doc = d.get("exchange-document", d)
        biblio = doc.get("bibliographic-data", {})

        # 提取专利号（epodoc 格式优先）
        doc_id = doc.get("@doc-number", "")
        country = doc.get("@country", "")
        kind = doc.get("@kind", "")
        number = f"{country}{doc_id}" if country else doc_id

        # 提取标题
        title = "Unknown"
        t_data = biblio.get("invention-title", [])
        if isinstance(t_data, list) and t_data:
            title = t_data[0].get("$", title)
        elif isinstance(t_data, dict):
            title = t_data.get("$", title)

        # 提取摘要
        abstract = ""
        abs_data = doc.get("abstract", [])
        if isinstance(abs_data, list) and abs_data:
            p_data = abs_data[0].get("p", {})
            abstract = p_data.get("$", "") if isinstance(p_data, dict) else str(p_data)
        elif isinstance(abs_data, dict):
            p_data = abs_data.get("p", {})
            abstract = p_data.get("$", "") if isinstance(p_data, dict) else str(p_data)

        refs.append({
            "number": number,
            "kind": kind,
            "title": title,
            "abstract": abstract,
        })

    return refs


async def _fetch_fulltext(patent_number: str, patent_data: dict):
    """获取专利全文（权利要求、说明书、法律状态、同族）"""
    # 1. 权利要求
    try:
        claims = await epo_client.get_patent_claims(patent_number)
        if not claims.startswith("未找到"):
            patent_data["claims_text"] = claims
    except Exception as e:
        logger.warning(f"[EPO] Claims fetch failed for {patent_number}: {e}")

    await asyncio.sleep(1)

    # 2. 说明书
    try:
        desc = await epo_client.get_patent_description(patent_number)
        if not desc.startswith("未找到"):
            patent_data["description_text"] = desc
    except Exception as e:
        logger.warning(f"[EPO] Description fetch failed for {patent_number}: {e}")

    await asyncio.sleep(1)

    # 3. 法律状态
    try:
        legal = await epo_client.get_legal_status(patent_number)
        if not legal.startswith("未找到"):
            patent_data["legal_status"] = legal
    except Exception as e:
        logger.warning(f"[EPO] Legal status fetch failed for {patent_number}: {e}")

    await asyncio.sleep(1)

    # 4. 同族专利
    try:
        family = await epo_client.get_patent_family(patent_number)
        if not family.startswith("未找到") and not family.startswith("未解析"):
            patent_data["family_members"] = family
    except Exception as e:
        logger.warning(f"[EPO] Family fetch failed for {patent_number}: {e}")

    # 5. 书目信息（IPC 分类）
    try:
        biblio = await epo_client.get_patent_biblio(patent_number)
        if biblio and "分类号" in biblio:
            patent_data["ipc_classes"] = biblio
    except Exception as e:
        logger.warning(f"[EPO] Biblio fetch failed for {patent_number}: {e}")
