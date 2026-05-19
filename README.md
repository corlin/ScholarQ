# ScholarQ

基于 Python 3.12 与 `uv` 管理的**智能材料文献与专利分析 Agent 系统**。

## ✨ 特性 (Features)
- **学术文献检索**: 基于 Semantic Scholar 接口，快速获取相关学术论文与摘要。
- **全球专利检索**: 原生接入了欧洲专利局 (EPO) OPS API 和美国专利商标局 (USPTO) Open Data API。
- **智能 Agent 问答**: 集成 LangChain/LangGraph，大语言模型可自动分析您的需求，执行多语言自动翻译，并智能调度不同的检索工具（Tool）进行查新与技术对比。

## 📁 项目结构
- `backend/`: FastAPI 后端服务，包含 Agent 逻辑、LLM 翻译处理及各类检索客户端。
- `frontend/`: Streamlit 前端服务，提供直观的对话式交互界面。

## ⚙️ 配置
在项目根目录下，编辑或创建 `.env` 文件，填入以下所需的环境变量：

```env
# Semantic Scholar API
S2_API_KEY=YOUR_API_KEY

# LLM Configuration (兼容 OpenAI 格式)
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=YOUR_LLM_KEY
LLM_MODEL=deepseek-v4-flash

# EPO Configuration
EPO_CONSUMER_KEY=YOUR_EPO_KEY
EPO_CONSUMER_SECRET=YOUR_EPO_SECRET

# USPTO Configuration
USPTO_API_KEY=YOUR_USPTO_KEY
```

## 🚀 启动服务

本项目使用 `uv` 进行环境与依赖管理。

### 1. 启动后端 (FastAPI)
在项目根目录打开一个终端运行:
```bash
uv run uvicorn backend.main:app --reload --port 8001
```
后端将在 `http://localhost:8001` 提供服务。

### 2. 启动前端 (Streamlit)
再打开一个终端窗口，在项目根目录运行:
```bash
uv run streamlit run frontend/app.py
```
前端界面将自动在浏览器中打开 (默认地址 `http://localhost:8501`)。
