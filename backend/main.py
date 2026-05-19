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

from typing import List

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []

from backend.agent import agent_executor
from langchain_core.messages import AIMessage, HumanMessage

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
        response = await agent_executor.ainvoke({
            "messages": messages
        })
        
        # 获取最后一条 AI 消息作为回复
        final_message = response["messages"][-1].content
        
        return {"reply": final_message}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
