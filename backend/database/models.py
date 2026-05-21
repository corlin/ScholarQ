"""
数据模型定义：论文、专利、采集任务
包含 SQLite 建表语句和 Pydantic 响应模型
"""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ============================================================
# SQLite DDL
# ============================================================

CREATE_PAPERS_TABLE = """
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    s2_paper_id     TEXT UNIQUE,
    doi             TEXT,
    title           TEXT NOT NULL,
    authors_json    TEXT,
    year            INTEGER,
    venue           TEXT,
    abstract        TEXT,
    citation_count  INTEGER DEFAULT 0,
    open_access_url TEXT,
    pdf_local_path  TEXT,
    fields_of_study TEXT,
    external_ids    TEXT,
    collected_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_PATENTS_TABLE = """
CREATE TABLE IF NOT EXISTS patents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL CHECK(source IN ('EPO', 'USPTO')),
    patent_number       TEXT NOT NULL,
    title               TEXT,
    applicant           TEXT,
    inventor            TEXT,
    filing_date         TEXT,
    abstract            TEXT,
    claims_text         TEXT,
    description_text    TEXT,
    legal_status        TEXT,
    ipc_classes         TEXT,
    family_members_json TEXT,
    raw_biblio_json     TEXT,
    collected_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, patent_number)
);
"""

CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS collection_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT NOT NULL,
    source          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'done', 'failed')),
    total_found     INTEGER DEFAULT 0,
    collected_count INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    error_msg       TEXT
);
"""

ALL_DDL = [CREATE_PAPERS_TABLE, CREATE_PATENTS_TABLE, CREATE_TASKS_TABLE]


# ============================================================
# Pydantic 响应模型
# ============================================================

class PaperRecord(BaseModel):
    id: int
    s2_paper_id: Optional[str] = None
    doi: Optional[str] = None
    title: str
    authors_json: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    citation_count: int = 0
    open_access_url: Optional[str] = None
    pdf_local_path: Optional[str] = None
    fields_of_study: Optional[str] = None
    collected_at: str


class PatentRecord(BaseModel):
    id: int
    source: str
    patent_number: str
    title: Optional[str] = None
    applicant: Optional[str] = None
    inventor: Optional[str] = None
    filing_date: Optional[str] = None
    abstract: Optional[str] = None
    claims_text: Optional[str] = None
    description_text: Optional[str] = None
    legal_status: Optional[str] = None
    ipc_classes: Optional[str] = None
    family_members_json: Optional[str] = None
    collected_at: str


class TaskRecord(BaseModel):
    id: int
    query: str
    source: str
    status: str
    total_found: int = 0
    collected_count: int = 0
    created_at: str
    finished_at: Optional[str] = None
    error_msg: Optional[str] = None


class LibraryStats(BaseModel):
    total_papers: int = 0
    total_patents: int = 0
    epo_patents: int = 0
    uspto_patents: int = 0
    papers_with_pdf: int = 0
    db_size_mb: float = 0.0


class CollectRequest(BaseModel):
    query: str
    sources: List[str] = ["s2", "epo", "uspto"]
    limit: int = 10
