"""
Semantic Scholar 论文批量采集器
使用 /paper/search/bulk 端点进行大规模检索，并下载 OA PDF
"""

import os
import logging
import httpx
from pathlib import Path
from typing import Optional

from backend.s2_client import s2_client, limiter, S2_API_KEY
from backend.database.db import insert_paper, paper_exists, PDF_DIR

logger = logging.getLogger("ScholarQ.S2Collector")

# Bulk Search 端点（比普通 search 支持更多结果）
BULK_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
BULK_FIELDS = (
    "title,authors,year,abstract,citationCount,url,venue,"
    "openAccessPdf,externalIds,fieldsOfStudy,publicationTypes"
)


async def collect_papers(
    query: str,
    limit: int = 50,
    on_progress=None,
) -> dict:
    """
    批量采集论文并写入本地数据库。
    
    Args:
        query: 搜索关键词
        limit: 最大采集数量
        on_progress: 可选的进度回调 async fn(collected, total)
    
    Returns:
        {"total_found": int, "collected": int, "pdf_downloaded": int}
    """
    collected = 0
    pdf_downloaded = 0
    total_found = 0
    token = None  # 用于 bulk search 分页

    headers = {}
    if S2_API_KEY and S2_API_KEY != "YOUR_API_KEY_HERE":
        headers["x-api-key"] = S2_API_KEY

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        while collected < limit:
            batch_size = min(1000, limit - collected)  # bulk 每次最多 1000
            params = {
                "query": query,
                "fields": BULK_FIELDS,
            }
            if token:
                params["token"] = token

            async with limiter:
                logger.info(f"[S2 Bulk] Fetching batch, collected={collected}/{limit}")
                try:
                    response = await client.get(BULK_SEARCH_URL, params=params)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    logger.error(f"[S2 Bulk] HTTP error: {e}")
                    break
                except Exception as e:
                    logger.error(f"[S2 Bulk] Request error: {e}")
                    break

            data = response.json()
            total_found = data.get("total", 0)
            papers = data.get("data", [])
            token = data.get("token")  # 下一页 token

            if not papers:
                break

            for paper in papers:
                if collected >= limit:
                    break

                s2_id = paper.get("paperId")
                if not s2_id:
                    continue

                # 检查是否已存在
                if await paper_exists(s2_id):
                    collected += 1
                    continue

                # 提取 OA PDF 信息
                oa_pdf = paper.get("openAccessPdf")
                oa_url = None
                if isinstance(oa_pdf, dict):
                    oa_url = oa_pdf.get("url")
                elif isinstance(oa_pdf, str):
                    oa_url = oa_pdf
                pdf_path = None

                # 下载 PDF
                if oa_url:
                    pdf_path = await _download_pdf(client, s2_id, oa_url)
                    if pdf_path:
                        pdf_downloaded += 1

                # 提取外部 ID
                ext_ids = paper.get("externalIds") or {}

                # 提取研究领域（fieldsOfStudy 是字符串列表）
                fos_raw = paper.get("fieldsOfStudy") or []
                if fos_raw and isinstance(fos_raw[0], dict):
                    fields_of_study = [f.get("category", "") for f in fos_raw]
                else:
                    fields_of_study = list(fos_raw)  # 字符串列表

                # 写入数据库
                await insert_paper({
                    "s2_paper_id": s2_id,
                    "doi": ext_ids.get("DOI") if isinstance(ext_ids, dict) else None,
                    "title": paper.get("title", "Unknown"),
                    "authors": [
                        {"name": a.get("name", ""), "authorId": a.get("authorId")}
                        for a in (paper.get("authors") or [])
                        if isinstance(a, dict)
                    ],
                    "year": paper.get("year"),
                    "venue": paper.get("venue"),
                    "abstract": paper.get("abstract"),
                    "citation_count": paper.get("citationCount", 0),
                    "open_access_url": oa_url,
                    "pdf_local_path": pdf_path,
                    "fields_of_study": fields_of_study,
                    "external_ids": ext_ids if isinstance(ext_ids, dict) else {},
                })
                collected += 1

                if on_progress:
                    await on_progress(collected, total_found)

            # 如果没有更多 token，退出
            if not token:
                break

    logger.info(
        f"[S2 Bulk] Done: total_found={total_found}, "
        f"collected={collected}, pdf_downloaded={pdf_downloaded}"
    )
    return {
        "total_found": total_found,
        "collected": collected,
        "pdf_downloaded": pdf_downloaded,
    }


async def _download_pdf(
    client: httpx.AsyncClient, paper_id: str, url: str
) -> Optional[str]:
    """下载 PDF 文件到本地，返回本地路径或 None"""
    try:
        pdf_path = PDF_DIR / f"{paper_id}.pdf"
        if pdf_path.exists():
            return str(pdf_path)

        logger.info(f"[S2 PDF] Downloading: {url}")
        response = await client.get(url, follow_redirects=True, timeout=30.0)
        if response.status_code == 200 and len(response.content) > 1000:
            PDF_DIR.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(response.content)
            logger.info(f"[S2 PDF] Saved: {pdf_path} ({len(response.content)} bytes)")
            return str(pdf_path)
        else:
            logger.warning(f"[S2 PDF] Skip (status={response.status_code}, size={len(response.content)})")
            return None
    except Exception as e:
        logger.warning(f"[S2 PDF] Download failed: {e}")
        return None
