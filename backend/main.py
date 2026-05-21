from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from backend.s2_client import s2_client
from contextlib import asynccontextmanager

from backend.database.db import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await s2_client.close()

app = FastAPI(title="ScholarQ API", lifespan=lifespan)

@app.get("/api/search")
async def search_papers(query: str, limit: int = Query(10, le=100), offset: int = 0):
    try:
        data = await s2_client.search_papers(query, limit, offset)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/paper/{paper_id}")
async def get_paper(paper_id: str):
    try:
        data = await s2_client.get_paper_details(paper_id)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ExtractRequest(BaseModel):
    title: str
    abstract: str

from backend.llm_service import extract_material_info_robust

@app.post("/api/extract")
async def extract_material_features(request: ExtractRequest):
    if not request.abstract or request.abstract == "无摘要":
        raise HTTPException(status_code=400, detail="没有可用的摘要进行提取")
    
    try:
        result = await extract_material_info_robust(request.title, request.abstract)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from typing import List, Optional

class ChatMessage(BaseModel):
    role: str
    content: str
    reasoning_content: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []

from backend.agent import agent_executor
from langchain_core.messages import AIMessage, HumanMessage
from fastapi.responses import StreamingResponse
import json
import time

# 全局工具名映射（覆盖所有 Agent 工具）
TOOL_NAME_MAP = {
    "search_materials_literature": "📚 检索学术文献 (Semantic Scholar)",
    "search_epo_patent": "🇪🇺 检索欧局专利 (EPO)",
    "search_uspto_patent": "🇺🇸 检索美局专利 (USPTO)",
    "get_epo_patent_details": "📄 获取专利说明书与权利要求",
    "get_epo_patent_family": "🌐 查询同族专利布局",
    "get_epo_legal_status": "⚖️ 查询法律状态",
    "get_epo_patent_biblio": "🔖 分析书目与引文 (Citations)",
    "get_epo_patent_equivalents": "🔗 查询同等专利文献",
}

@app.post("/api/chat/stream")
async def chat_with_agent_stream(request: ChatRequest):
    async def event_generator():
        try:
            chat_history = []
            for msg in request.history:
                if msg.role == "user":
                    chat_history.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    ai_msg = AIMessage(content=msg.content)
                    if msg.reasoning_content:
                        ai_msg.additional_kwargs["reasoning_content"] = msg.reasoning_content
                    chat_history.append(ai_msg)
                    
            messages = chat_history + [HumanMessage(content=request.message)]
            
            # 用于追踪每个工具调用的起始时间
            tool_start_times = {}
            
            async for event in agent_executor.astream_events(
                {"messages": messages}, 
                version="v2",
                config={"recursion_limit": 100}
            ):
                kind = event["event"]
                
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False) + "\n"
                    if "reasoning_content" in chunk.additional_kwargs and chunk.additional_kwargs["reasoning_content"]:
                        yield json.dumps({"type": "reasoning", "data": chunk.additional_kwargs["reasoning_content"]}, ensure_ascii=False) + "\n"
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    tool_start_times[tool_name] = time.time()
                    human_name = TOOL_NAME_MAP.get(tool_name, tool_name)
                    yield json.dumps({"type": "tool_start", "data": human_name}, ensure_ascii=False) + "\n"
                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    human_name = TOOL_NAME_MAP.get(tool_name, tool_name)
                    elapsed = time.time() - tool_start_times.pop(tool_name, time.time())
                    yield json.dumps({"type": "tool_end", "data": human_name, "elapsed": round(elapsed, 1)}, ensure_ascii=False) + "\n"
                
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            yield json.dumps({"type": "error", "data": f"{str(e)}\n\nTraceback:\n{tb_str}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

@app.post("/api/chat")
async def chat_with_agent(request: ChatRequest):
    try:
        # 还原上下文记忆
        chat_history = []
        for msg in request.history:
            if msg.role == "user":
                chat_history.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                chat_history.append(AIMessage(content=msg.content))
                
        # 调用 Agent 执行 (LangGraph 格式)
        messages = chat_history + [HumanMessage(content=request.message)]
        response = await agent_executor.ainvoke(
            {"messages": messages},
            config={"recursion_limit": 100}
        )
        
        # 获取最后一条 AI 消息作为回复
        final_message = response["messages"][-1].content
        
        return {"reply": final_message}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# 会话主题提取
# ============================================================
class TopicRequest(BaseModel):
    messages: List[ChatMessage] = []

@app.post("/api/chat/extract_topic")
async def extract_chat_topic(request: TopicRequest):
    """从对话内容中提取 ≤10 字的会话主题"""
    import os
    from backend.llm_service import aclient
    
    # 只取前几条消息来提取主题（节省 token）
    summary_parts = []
    for msg in request.messages[:4]:
        role_label = "用户" if msg.role == "user" else "助手"
        content_snippet = msg.content[:200]
        summary_parts.append(f"{role_label}: {content_snippet}")
    
    conversation_snippet = "\n".join(summary_parts)
    
    prompt = f"""请根据以下对话内容，用 10 个中文字以内总结会话主题。

规则：
- 直接输出主题，不要加引号、标点或解释
- 抓住核心意图，如材料名称、分析目标
- 示例输出格式：SiC陶瓷专利排查

对话内容：
{conversation_snippet}"""

    try:
        MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        print(f"[TopicExtract] Using model={MODEL}, messages_count={len(request.messages)}")
        response = await aclient.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个标题生成助手。只输出一个极短的主题词，不超过10个字。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
        )
        topic = response.choices[0].message.content.strip()
        # 清洗引号和标点
        topic = topic.strip('"\'""\u201c\u201d\u2018\u2019\u3001\u3002\uff01').strip()
        if len(topic) > 10:
            topic = topic[:10]
        print(f"[TopicExtract] Result: '{topic}'")
        return {"topic": topic}
    except Exception as e:
        print(f"[TopicExtract] Error: {e}")
        return {"topic": "", "error": str(e)}

# ============================================================
# 文献采集任务管理 API
# ============================================================
from backend.database.models import CollectRequest
from backend.collector import task_manager
from backend.database import (
    search_papers as db_search_papers,
    search_patents as db_search_patents,
    get_paper_by_id,
    get_patent_by_id,
    get_library_stats,
)

@app.post("/api/collect")
async def create_collection_task(request: CollectRequest):
    """创建采集任务（支持同时采集多个数据源）"""
    try:
        tasks = await task_manager.submit_collection(
            query=request.query,
            sources=request.sources,
            limit=request.limit,
        )
        return {"message": "采集任务已创建", "tasks": tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/collect/tasks")
async def list_collection_tasks(limit: int = Query(50, le=200)):
    """查看所有采集任务"""
    tasks = await task_manager.get_all_task_statuses(limit)
    return {"tasks": tasks}

@app.get("/api/collect/tasks/{task_id}")
async def get_collection_task(task_id: int):
    """查看单个采集任务详情"""
    task = await task_manager.get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task

# ============================================================
# 本地文献库管理 API
# ============================================================

@app.get("/api/library/papers")
async def list_local_papers(
    keyword: str = "",
    limit: int = Query(20, le=100),
    offset: int = 0,
):
    """查询本地论文库"""
    return await db_search_papers(keyword=keyword, limit=limit, offset=offset)

@app.get("/api/library/papers/{paper_id}")
async def get_local_paper(paper_id: int):
    """获取单篇论文详情"""
    paper = await get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")
    return paper

@app.get("/api/library/patents")
async def list_local_patents(
    keyword: str = "",
    source: str = "",
    limit: int = Query(20, le=100),
    offset: int = 0,
):
    """查询本地专利库（可按 source=EPO/USPTO 过滤）"""
    return await db_search_patents(
        keyword=keyword, source=source, limit=limit, offset=offset
    )

@app.get("/api/library/patents/{patent_id}")
async def get_local_patent(patent_id: int):
    """获取单条专利详情"""
    patent = await get_patent_by_id(patent_id)
    if not patent:
        raise HTTPException(status_code=404, detail="专利不存在")
    return patent

@app.get("/api/library/stats")
async def library_statistics():
    """本地数据库统计信息"""
    return await get_library_stats()

