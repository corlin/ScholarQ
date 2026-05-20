from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from backend.s2_client import s2_client
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
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
