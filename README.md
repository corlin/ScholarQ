# ScholarQ

基于 Python 3.12 与 `uv` 管理的 Semantic Scholar 文献查询与分析系统。

## 项目结构
- `backend/`: FastAPI 后端，处理 API 请求和限流 (1 request/second)
- `frontend/`: Streamlit 前端，提供用户界面
- `.env`: 环境变量，用于配置 Semantic Scholar API Key 等信息

## 配置
1. 在项目根目录下，编辑 `.env` 文件。
2. 将 `S2_API_KEY=YOUR_API_KEY_HERE` 替换为您收到的 Semantic Scholar API Key。

## 启动服务

本项目使用 `uv` 进行环境与依赖管理。

### 启动后端 (FastAPI)
在项目根目录运行:
```bash
uv run uvicorn backend.main:app --reload --port 8001
```
后端将在 `http://localhost:8001` 提供服务。

### 启动前端 (Streamlit)
再打开一个终端窗口，在项目根目录运行:
```bash
uv run streamlit run frontend/app.py
```
前端界面将自动在浏览器中打开 (默认地址 `http://localhost:8501`)。
