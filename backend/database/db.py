"""
异步 SQLite 数据库操作层
使用 aiosqlite 提供连接管理和 CRUD 操作
"""

import os
import json
import aiosqlite
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from backend.database.models import ALL_DDL

logger = logging.getLogger("ScholarQ.DB")

# 数据库文件路径
DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "scholarq.db"
PDF_DIR = DB_DIR / "pdfs"


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（每次调用都新建连接）"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    """初始化数据库，创建所有表"""
    db = await get_db()
    try:
        for ddl in ALL_DDL:
            await db.execute(ddl)
        await db.commit()
        logger.info(f"Database initialized at {DB_PATH}")
    finally:
        await db.close()


# ============================================================
# Papers CRUD
# ============================================================

async def paper_exists(s2_paper_id: str) -> bool:
    """检查论文是否已存在"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM papers WHERE s2_paper_id = ?", (s2_paper_id,)
        )
        return await cursor.fetchone() is not None
    finally:
        await db.close()


async def insert_paper(data: Dict[str, Any]) -> int:
    """插入一条论文记录，返回 id。已存在则跳过并返回已有 id。"""
    db = await get_db()
    try:
        # 检查重复
        cursor = await db.execute(
            "SELECT id FROM papers WHERE s2_paper_id = ?",
            (data.get("s2_paper_id"),)
        )
        existing = await cursor.fetchone()
        if existing:
            return existing[0]

        cursor = await db.execute(
            """INSERT INTO papers 
            (s2_paper_id, doi, title, authors_json, year, venue, abstract,
             citation_count, open_access_url, pdf_local_path, fields_of_study, external_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("s2_paper_id"),
                data.get("doi"),
                data.get("title", "Unknown"),
                json.dumps(data.get("authors", []), ensure_ascii=False) if data.get("authors") else None,
                data.get("year"),
                data.get("venue"),
                data.get("abstract"),
                data.get("citation_count", 0),
                data.get("open_access_url"),
                data.get("pdf_local_path"),
                json.dumps(data.get("fields_of_study", []), ensure_ascii=False) if data.get("fields_of_study") else None,
                json.dumps(data.get("external_ids", {}), ensure_ascii=False) if data.get("external_ids") else None,
            )
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def search_papers(
    keyword: str = "",
    limit: int = 20,
    offset: int = 0
) -> Dict[str, Any]:
    """搜索本地论文库"""
    db = await get_db()
    try:
        if keyword:
            where = "WHERE title LIKE ? OR abstract LIKE ?"
            params = (f"%{keyword}%", f"%{keyword}%")
            count_cursor = await db.execute(
                f"SELECT COUNT(*) FROM papers {where}", params
            )
            data_cursor = await db.execute(
                f"SELECT * FROM papers {where} ORDER BY year DESC, citation_count DESC LIMIT ? OFFSET ?",
                params + (limit, offset)
            )
        else:
            count_cursor = await db.execute("SELECT COUNT(*) FROM papers")
            data_cursor = await db.execute(
                "SELECT * FROM papers ORDER BY collected_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )

        total = (await count_cursor.fetchone())[0]
        rows = await data_cursor.fetchall()
        return {
            "total": total,
            "data": [dict(r) for r in rows],
            "limit": limit,
            "offset": offset,
        }
    finally:
        await db.close()


async def get_paper_by_id(paper_id: int) -> Optional[Dict]:
    """按 id 获取单篇论文"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ============================================================
# Patents CRUD
# ============================================================

async def patent_exists(source: str, patent_number: str) -> bool:
    """检查专利是否已存在"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM patents WHERE source = ? AND patent_number = ?",
            (source, patent_number)
        )
        return await cursor.fetchone() is not None
    finally:
        await db.close()


async def insert_patent(data: Dict[str, Any]) -> int:
    """插入一条专利记录，返回 id。已存在则更新全文字段。"""
    db = await get_db()
    try:
        # 检查重复
        cursor = await db.execute(
            "SELECT id FROM patents WHERE source = ? AND patent_number = ?",
            (data.get("source"), data.get("patent_number"))
        )
        existing = await cursor.fetchone()
        if existing:
            # 更新全文字段（可能之前只有元数据）
            update_fields = []
            update_values = []
            for field in ["claims_text", "description_text", "legal_status",
                          "ipc_classes", "family_members_json", "abstract"]:
                if data.get(field):
                    update_fields.append(f"{field} = ?")
                    update_values.append(data[field])
            if update_fields:
                update_values.append(existing[0])
                await db.execute(
                    f"UPDATE patents SET {', '.join(update_fields)} WHERE id = ?",
                    update_values
                )
                await db.commit()
            return existing[0]

        cursor = await db.execute(
            """INSERT INTO patents
            (source, patent_number, title, applicant, inventor, filing_date,
             abstract, claims_text, description_text, legal_status, ipc_classes,
             family_members_json, raw_biblio_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("source"),
                data.get("patent_number"),
                data.get("title"),
                data.get("applicant"),
                data.get("inventor"),
                data.get("filing_date"),
                data.get("abstract"),
                data.get("claims_text"),
                data.get("description_text"),
                data.get("legal_status"),
                data.get("ipc_classes"),
                json.dumps(data.get("family_members", []), ensure_ascii=False) if data.get("family_members") else None,
                json.dumps(data.get("raw_biblio", {}), ensure_ascii=False) if data.get("raw_biblio") else None,
            )
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def search_patents(
    keyword: str = "",
    source: str = "",
    limit: int = 20,
    offset: int = 0
) -> Dict[str, Any]:
    """搜索本地专利库"""
    db = await get_db()
    try:
        conditions = []
        params = []
        if keyword:
            conditions.append("(title LIKE ? OR abstract LIKE ? OR claims_text LIKE ?)")
            params.extend([f"%{keyword}%"] * 3)
        if source:
            conditions.append("source = ?")
            params.append(source.upper())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_cursor = await db.execute(
            f"SELECT COUNT(*) FROM patents {where}", params
        )
        data_cursor = await db.execute(
            f"SELECT * FROM patents {where} ORDER BY collected_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        )

        total = (await count_cursor.fetchone())[0]
        rows = await data_cursor.fetchall()
        return {
            "total": total,
            "data": [dict(r) for r in rows],
            "limit": limit,
            "offset": offset,
        }
    finally:
        await db.close()


async def get_patent_by_id(patent_id: int) -> Optional[Dict]:
    """按 id 获取单条专利"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM patents WHERE id = ?", (patent_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ============================================================
# Collection Tasks CRUD
# ============================================================

async def create_collection_task(query: str, source: str) -> int:
    """创建采集任务，返回 task_id"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO collection_tasks (query, source) VALUES (?, ?)",
            (query, source)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_task_status(
    task_id: int,
    status: str = None,
    total_found: int = None,
    collected_count: int = None,
    error_msg: str = None,
    finished: bool = False,
):
    """更新任务状态"""
    db = await get_db()
    try:
        updates = []
        values = []
        if status:
            updates.append("status = ?")
            values.append(status)
        if total_found is not None:
            updates.append("total_found = ?")
            values.append(total_found)
        if collected_count is not None:
            updates.append("collected_count = ?")
            values.append(collected_count)
        if error_msg is not None:
            updates.append("error_msg = ?")
            values.append(error_msg)
        if finished:
            updates.append("finished_at = ?")
            values.append(datetime.utcnow().isoformat())

        if updates:
            values.append(task_id)
            await db.execute(
                f"UPDATE collection_tasks SET {', '.join(updates)} WHERE id = ?",
                values
            )
            await db.commit()
    finally:
        await db.close()


async def get_task(task_id: int) -> Optional[Dict]:
    """获取单个任务"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM collection_tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_tasks(limit: int = 50) -> List[Dict]:
    """获取所有任务"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM collection_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_library_stats() -> Dict[str, Any]:
    """获取本地数据库统计信息"""
    db = await get_db()
    try:
        stats = {}
        cursor = await db.execute("SELECT COUNT(*) FROM papers")
        stats["total_papers"] = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM patents")
        stats["total_patents"] = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM patents WHERE source = 'EPO'")
        stats["epo_patents"] = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM patents WHERE source = 'USPTO'")
        stats["uspto_patents"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM papers WHERE pdf_local_path IS NOT NULL AND pdf_local_path != ''"
        )
        stats["papers_with_pdf"] = (await cursor.fetchone())[0]

        # 数据库文件大小
        db_size = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
        stats["db_size_mb"] = round(db_size, 2)

        return stats
    finally:
        await db.close()
