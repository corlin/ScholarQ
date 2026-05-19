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
original_convert_to_dict = base._convert_message_to_dict
original_convert_from_dict = getattr(base, "_convert_dict_to_message", None)

def patched_convert_message_to_dict(message, *args, **kwargs):
    d = original_convert_to_dict(message, *args, **kwargs)
    if isinstance(message, AIMessage) and "reasoning_content" in message.additional_kwargs:
        d["reasoning_content"] = message.additional_kwargs["reasoning_content"]
    return d

base._convert_message_to_dict = patched_convert_message_to_dict

if original_convert_from_dict:
    def patched_convert_dict_to_message(_dict, *args, **kwargs):
        msg = original_convert_from_dict(_dict, *args, **kwargs)
        if isinstance(msg, AIMessage) and "reasoning_content" in _dict:
            msg.additional_kwargs["reasoning_content"] = _dict["reasoning_content"]
        return msg
    base._convert_dict_to_message = patched_convert_dict_to_message
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
            result.append(f"【标题】: {title} ({year})\n【摘要】: {abstract}")
        return "\n---\n".join(result)
    except Exception as e:
        return f"检索技能调用失败: {str(e)}"

async def translate_query_to_english(query: str) -> str:
    from backend.llm_service import aclient
    prompt = f"Please translate the following text into English keywords suitable for a patent search query. Output ONLY the English translation, no other text. Text: {query}"
    try:
        MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        response = await aclient.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        en_query = response.choices[0].message.content.strip()
        print(f"[Agent Tool] Translated query '{query}' -> '{en_query}'")
        return en_query
    except Exception as e:
        print(f"[Agent Tool] Translation failed: {e}")
        return query

@tool
async def search_epo_patent(query: str) -> str:
    """
    Skill: 专门用于检索欧洲专利局（EPO）的相关专利信息。
    当用户要求查询欧洲局（欧局、EPO）专利时调用此工具。
    支持任何语言输入，系统将自动使用大模型转换为英文关键词。
    """
    en_query = await translate_query_to_english(query)
    try:
        from backend.patent_clients import epo_client
        import json
        data = await epo_client.search_patents(en_query)
        # 简单截断返回，防止上下文超长
        return f"EPO Search Result for '{en_query}':\n" + json.dumps(data, ensure_ascii=False)[:2000]
    except Exception as e:
        return f"EPO检索失败: {str(e)}"

@tool
async def search_uspto_patent(query: str) -> str:
    """
    Skill: 专门用于检索美国专利商标局（USPTO）的相关专利信息。
    当用户要求查询美国局（美局、USPTO）专利时调用此工具。
    支持任何语言输入，系统将自动使用大模型转换为英文关键词。
    """
    en_query = await translate_query_to_english(query)
    try:
        from backend.patent_clients import uspto_client
        import json
        data = await uspto_client.search_patents(en_query)
        return f"USPTO Search Result for '{en_query}':\n" + json.dumps(data, ensure_ascii=False)[:2000]
    except Exception as e:
        return f"USPTO检索失败: {str(e)}"

tools = [search_materials_literature, search_epo_patent, search_uspto_patent]

system_message = """你是一名顶尖的材料学专家和资深专利代理师（Agent）。
你的任务是辅助用户进行专利材料（配方、工艺、结构）的新颖性排查和技术交底书素材挖掘。
如果用户提出了一个新的材料配方或工艺，你必须**主动调用** `search_materials_literature` 工具来检索现有的学术文献作为现有技术（Prior Art）。
此外，如果用户请求查询欧洲局（EPO）或美国局（USPTO）的专利，请分别调用 `search_epo_patent` 或 `search_uspto_patent`。
在回复时：
1. 首先告诉用户你正在理解其需求。
2. 提取检索到的文献或专利中的具体参数（如温度、时间、掺杂比例等），与用户的方案进行详细的【差异化对比】。
3. 为用户提供专业的专利新颖性/创造性建议。
请尽量用清晰、有条理的中文进行回复。"""

# 使用 LangGraph 的最佳实践 create_react_agent
agent_executor = create_react_agent(llm, tools, prompt=system_message)
