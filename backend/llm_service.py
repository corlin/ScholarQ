import os
import json
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# Initialize async client
aclient = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY if LLM_API_KEY else "dummy-key"
)

class MaterialExtraction(BaseModel):
    material_system: str = Field(description="核心材料体系，如：固态电解质、铝合金、钛合金等")
    composition: str = Field(description="配方及比例，如：掺杂量、元素质量分数、化学式等")
    process_conditions: str = Field(description="关键工艺条件，包含温度、压力、时间、烧结/搅拌等加工方式")
    performance_metrics: str = Field(description="性能指标，包含具体数值，如：抗拉强度 500MPa、电导率等")
    novelty: str = Field(description="该文献的核心创新点或区别技术特征简述")

async def extract_material_info(title: str, abstract: str) -> Optional[dict]:
    if not LLM_API_KEY or LLM_API_KEY == "YOUR_LLM_API_KEY_HERE":
        raise ValueError("LLM_API_KEY is not configured.")

    prompt = f"""
请作为一名资深的材料学专利审查员和研究员，阅读以下文献标题和摘要，提取核心的材料配方与工艺参数。
如果摘要中没有明确提及某项信息，请填写 "未提及"。

【文献标题】
{title}

【摘要内容】
{abstract}
"""

    try:
        response = await aclient.chat.completions.create(
            model="gpt-3.5-turbo", # You can map this to deepseek-chat or others based on base_url
            messages=[
                {"role": "system", "content": "你是一个材料学数据提取助手，仅以严格的 JSON 格式输出数据。"},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        # Using JSON mode instead of strict Structured Output to maximize compatibility with various OpenAI-compatible endpoints (like DeepSeek)
        
        # We need to explicitly ask the model to return JSON matching the schema
        # Let's adjust the system prompt slightly to include schema instruction
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"LLM Extraction Error: {e}")
        return None

async def extract_material_info_robust(title: str, abstract: str) -> Optional[dict]:
    if not LLM_API_KEY or LLM_API_KEY == "YOUR_LLM_API_KEY_HERE":
        raise ValueError("请先在 .env 中配置 LLM_API_KEY")

    schema_str = json.dumps(MaterialExtraction.model_json_schema(), ensure_ascii=False)
    
    prompt = f"""
请作为一名资深的材料学专利审查员和研究员，阅读以下文献标题和摘要，提取核心的材料配方与工艺参数。
如果摘要中没有明确提及某项信息，请填写 "未提及"。

【文献标题】
{title}

【摘要内容】
{abstract}

请务必返回 JSON 格式数据，并严格符合以下 JSON Schema：
{schema_str}
"""

    try:
        # We use a generic model name that often defaults to the provided base_url's primary model
        # Or user can change it. Let's use 'gpt-4o-mini' as default, some APIs ignore it.
        # DeepSeek uses 'deepseek-chat', so we might want to make MODEL an env var.
        MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        response = await aclient.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个材料学数据提取助手，必须以严格的 JSON 格式返回结果。"},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        raise Exception(f"大模型提取失败: {str(e)}")
