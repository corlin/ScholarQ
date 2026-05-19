import os
import asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage
from backend.s2_client import s2_client
import langchain_openai.chat_models.base as base

# ---- 解决 DeepSeek 推理模型的多轮对话报错 (Patch) ----
import langchain_openai.chat_models.base as base

# 1. Patch langchain_openai's _convert_message_to_dict
original_convert_to_dict = getattr(base, "_convert_message_to_dict", None)

if original_convert_to_dict:
    def patched_convert_message_to_dict(message, *args, **kwargs):
        d = original_convert_to_dict(message, *args, **kwargs)
        if hasattr(message, "additional_kwargs") and "reasoning_content" in message.additional_kwargs:
            d["reasoning_content"] = message.additional_kwargs["reasoning_content"]
        elif d.get("role") == "assistant" and "tool_calls" in d:
            d["reasoning_content"] = "" # Fallback
        return d
    base._convert_message_to_dict = patched_convert_message_to_dict

# 2. Patch openai's AsyncCompletions.create directly to intercept JSON before it goes out
import openai
original_create = openai.resources.chat.completions.AsyncCompletions.create

async def patched_create(self, *args, **kwargs):
    if "messages" in kwargs:
        for msg in kwargs["messages"]:
            if msg.get("role") == "assistant":
                if "reasoning_content" not in msg:
                    msg["reasoning_content"] = ""
    return await original_create(self, *args, **kwargs)

openai.resources.chat.completions.AsyncCompletions.create = patched_create
# --------------------------------------------------------

load_dotenv()

# 初始化支持 Tool Calling 的模型
llm = ChatOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    api_key=os.getenv("LLM_API_KEY", "dummy"),
    model=os.getenv("LLM_MODEL", "gpt-3.5-turbo"),
    temperature=0.1
)

@tool
async def search_materials_literature(query: str, limit: int = 5) -> str:
    """
    Skill: 专门用于在 Semantic Scholar 上检索材料学相关文献（如配方、工艺、性能等）。
    当用户需要查新、找现有技术（Prior Art）、或者对比材料性能时调用此工具。
    输入参数 query 为检索关键词（请尽量翻译为英文关键词组合，如 'SiC ceramic alumina'）。
    返回相关文献的标题、年份和摘要信息。
    """
    try:
        data = await s2_client.search_papers(query, limit=limit)
        papers = data.get("data", [])
        if not papers:
            return "未检索到相关文献，请尝试更换关键词。"
        
        result = []
        for p in papers:
            title = p.get('title', 'Unknown Title')
            abstract = p.get('abstract', '无摘要')
            year = p.get('year', 'Unknown Year')
            url = p.get('url', '')
            url_str = f" [原文链接]({url})" if url else ""
            result.append(f"【标题】: {title} ({year}){url_str}\n【摘要】: {abstract}")
        return "\n---\n".join(result)
    except Exception as e:
        return f"检索技能调用失败: {str(e)}"

async def build_patent_query(query: str) -> str:
    from backend.llm_service import aclient
    prompt = f"""
    You are an expert patent searcher. Convert the following user request into a broad Boolean query for a patent database.
    Rules:
    1. Extract ONLY the essential nouns (e.g., materials, core processes).
    2. DO NOT include specific numbers or parameters (like 5%, 1500°C), as they make the search too narrow and result in 0 hits.
    3. Use ' AND ' / ' OR ' operators. You can use quotes for exact phrases.
    4. Example: If user says "SiC with 5% alumina hot pressed at 1500C", output: (SiC OR "silicon carbide") AND (alumina OR Al2O3) AND "hot press*"
    5. Output ONLY the query string, no other text.
    
    User Request: {query}
    """
    try:
        MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        response = await aclient.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        en_query = response.choices[0].message.content.strip()
        # Clean up possible markdown artifacts
        en_query = en_query.replace('```', '').replace('`', '').strip()
        print(f"[Agent Tool] Built query for '{query}' -> '{en_query}'")
        return en_query
    except Exception as e:
        print(f"[Agent Tool] Query build failed: {e}")
        return query

@tool
async def search_epo_patent(query: str) -> str:
    """
    Skill: 专门用于检索欧洲专利局（EPO）的相关专利信息。
    当用户要求查询欧洲局（欧局、EPO）专利时调用此工具。
    支持任何语言输入，系统将自动使用大模型转换为英文关键词。
    """
    en_query = await build_patent_query(query)
    try:
        from backend.patent_clients import epo_client
        data = await epo_client.search_patents(en_query)
        return f"EPO Search Result for '{en_query}':\n{data}"
    except Exception as e:
        return f"EPO检索失败: {str(e)}"

@tool
async def search_uspto_patent(query: str) -> str:
    """
    Skill: 专门用于检索美国专利商标局（USPTO）的相关专利信息。
    当用户要求查询美国局（美局、USPTO）专利时调用此工具。
    支持任何语言输入，系统将自动使用大模型转换为英文关键词。
    """
    en_query = await build_patent_query(query)
    try:
        from backend.patent_clients import uspto_client
        data = await uspto_client.search_patents(en_query)
        return f"USPTO Search Result for '{en_query}':\n{data}"
    except Exception as e:
        return f"USPTO检索失败: {str(e)}"

tools = [search_materials_literature, search_epo_patent, search_uspto_patent]

system_message = """你是一名顶尖的材料学专家和资深专利代理师（Agent）。
你的任务是辅助用户进行专利材料（配方、工艺、结构）的新颖性排查和技术交底书素材挖掘。
当用户提出了一个新的材料配方/工艺，或要求检索“专利/论文/现有技术”时，你必须**全面主动**地执行以下步骤：
1. 调用 `search_materials_literature` 检索学术文献。
2. 调用 `search_epo_patent` 检索欧洲专利局（EPO）的专利。
3. 调用 `search_uspto_patent` 检索美国专利局（USPTO）的专利。
必须确保三个工具都被调用（除非用户明确指定只查某一个库）。

在完成全面检索后，你的回复必须遵循以下结构：
1. **检索总结**：简述你在文献库、欧局、美局分别查到了什么。在列举具体文献或专利时，必须原样输出并附上检索结果中提供的 `[原文链接](...)`，方便用户点击。
2. **差异化对比**：提取这三个数据源中最相关的现有技术参数（如温度、时间、掺杂比例等），与用户的方案进行详细的横向对比。
3. **新颖性/创造性建议**：基于文献和真实专利的反馈，为用户提供专业的专利申请建议。

请尽量用清晰、有条理的中文进行回复。**强制要求：绝不能丢失或省略任何引用内容的【原文链接】！**"""

# 使用 LangGraph 的最佳实践 create_react_agent
agent_executor = create_react_agent(llm, tools, prompt=system_message)
