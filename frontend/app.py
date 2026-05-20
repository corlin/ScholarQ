import streamlit as st
import httpx
import os
import json
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="ScholarQ: 材料专利 Agent", layout="wide", page_icon="🧪")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")

# ============================================================
# P0-3: 会话持久化 — 保存/加载对话到本地 JSON 文件
# ============================================================
CHAT_HISTORY_DIR = Path(__file__).parent / ".chat_history"
CHAT_HISTORY_DIR.mkdir(exist_ok=True)

def _get_session_file() -> Path:
    """获取当前会话的持久化文件路径"""
    if "session_id" not in st.session_state:
        import uuid
        st.session_state.session_id = str(uuid.uuid4())[:8]
    return CHAT_HISTORY_DIR / f"session_{st.session_state.session_id}.json"

def save_messages():
    """将 session_state.messages 持久化到本地 JSON"""
    try:
        with open(_get_session_file(), "w", encoding="utf-8") as f:
            json.dump(st.session_state.messages, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 静默处理持久化失败

def load_recent_session():
    """尝试加载最近的一次会话"""
    try:
        files = sorted(CHAT_HISTORY_DIR.glob("session_*.json"), key=os.path.getmtime, reverse=True)
        if files:
            with open(files[0], "r", encoding="utf-8") as f:
                data = json.load(f)
            if data:
                # 使用该文件对应的 session_id
                st.session_state.session_id = files[0].stem.replace("session_", "")
                return data
    except Exception:
        pass
    return []

# ============================================================
# P0-2: 引用来源结构化提取
# ============================================================
def extract_references(text: str) -> list[dict]:
    """从 Markdown 回复中提取所有 [标题](url) 形式的引用链接"""
    pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    matches = re.findall(pattern, text)
    seen = set()
    refs = []
    for title, url in matches:
        if url not in seen:
            seen.add(url)
            refs.append({"title": title, "url": url})
    return refs

def render_references_panel(refs: list[dict]):
    """在回复下方渲染结构化引用来源面板"""
    if not refs:
        return
    with st.expander(f"📚 引用来源 ({len(refs)} 条)", expanded=False):
        for i, ref in enumerate(refs, 1):
            st.markdown(f"**[{i}]** [{ref['title']}]({ref['url']})")

# ============================================================
# P1-5: 长回复结构化分段渲染
# ============================================================
def render_structured_reply(text: str):
    """将 Agent 的 Markdown 回复按 ## 标题分段渲染到 st.tabs 中。
    如果不含 ## 段落则直接渲染原文。"""
    # 按 "## " 拆分段落（保留标题）
    sections = re.split(r'(?=^## )', text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]
    
    # 如果只有一个段落或未检测到 ## 标题，直接渲染
    if len(sections) <= 1 or not any(s.startswith('## ') for s in sections):
        st.markdown(text)
        return
    
    # 分离：开头没有 ## 的前言 + 有 ## 标题的段落
    preamble = ""
    titled_sections = []
    for s in sections:
        if s.startswith('## '):
            lines = s.split('\n', 1)
            title = lines[0].replace('## ', '').strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            titled_sections.append((title, body))
        else:
            preamble += s + "\n"
    
    # 渲染前言（如果有）
    if preamble.strip():
        st.markdown(preamble.strip())
    
    # 渲染 tabs
    if titled_sections:
        tab_labels = [f"{s[0]}" for s in titled_sections]
        tabs = st.tabs(tab_labels)
        for tab, (title, body) in zip(tabs, titled_sections):
            with tab:
                st.markdown(body)

# ============================================================
# P1-6: Follow-up 建议生成与渲染
# ============================================================
def generate_followups(reply_text: str) -> list[str]:
    """根据 Agent 回复内容，智能生成 2~3 条后续建议。
    策略：提取专利号 → 构造深度分析建议；检测是否涉及对比 → 建议追问。"""
    suggestions = []
    
    # 提取专利号（EP/US/WO/CN 格式）
    patent_ids = list(set(re.findall(r'\b(EP\d{6,8}[A-Z]?\d?|US\d{7,11}[A-Z]?\d?|WO\d{10,13}[A-Z]?\d?|CN\d{8,12}[A-Z]?)\b', reply_text)))
    
    if patent_ids:
        # 选第一个专利号做深度分析建议
        pid = patent_ids[0]
        suggestions.append(f"深入分析 {pid} 的权利要求和说明书")
        if len(patent_ids) > 1:
            pid2 = patent_ids[1]
            suggestions.append(f"查看 {pid2} 的同族专利全球布局")
    
    # 通用建议
    if '新颖性' in reply_text or '创造性' in reply_text:
        suggestions.append("基于以上分析，帮我起草技术交底书的核心要点")
    elif '检索' in reply_text:
        suggestions.append("换一组更具体的关键词重新检索")
    
    if not suggestions:
        suggestions = [
            "请更详细地对比用户方案与最接近现有技术的区别",
            "帮我总结可以规避现有专利的改进方向",
        ]
    
    return suggestions[:3]

def render_followup_buttons(suggestions: list[str], key_prefix: str = "followup"):
    """渲染 Follow-up 建议按钮"""
    if not suggestions:
        return
    st.markdown("---")
    st.caption("💡 您可能还想了解：")
    cols = st.columns(len(suggestions))
    for i, (col, suggestion) in enumerate(zip(cols, suggestions)):
        with col:
            if st.button(suggestion, key=f"{key_prefix}_{i}", use_container_width=True):
                st.session_state._pending_prompt = suggestion
                st.rerun()

# ============================================================
# P2-9: 导出分析报告（Markdown）
# ============================================================
def export_as_markdown_report(messages: list[dict]) -> str:
    """将对话历史转换为结构化 Markdown 报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = f"# ScholarQ 专利分析报告\n\n"
    report += f"> 生成时间: {now}\n\n"
    report += "---\n\n"
    
    for msg in messages:
        if msg["role"] == "user":
            report += f"## 🧑 用户提问\n\n{msg['content']}\n\n---\n\n"
        elif msg["role"] == "assistant":
            report += f"## 🤖 Agent 分析\n\n{msg['content']}\n\n"
            refs = extract_references(msg['content'])
            if refs:
                report += "### 引用来源\n\n"
                for i, ref in enumerate(refs, 1):
                    report += f"{i}. [{ref['title']}]({ref['url']})\n"
                report += "\n"
            report += "---\n\n"
    
    report += "\n\n*本报告由 ScholarQ 材料专利 Agent 自动生成*\n"
    return report

# ============================================================
# P2-10: 重新生成
# ============================================================
def render_regenerate_button(key_prefix: str = "regen"):
    """渲染重新生成按钮"""
    if st.button("🔄 重新生成", key=f"{key_prefix}_btn", type="secondary"):
        # 找到最后一条用户消息
        last_user_msg = None
        for msg in reversed(st.session_state.messages):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if last_user_msg:
            # 删除最后一条 assistant 消息
            if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
                st.session_state.messages.pop()
            # 删除最后一条 user 消息（会在下一轮重新添加）
            if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                st.session_state.messages.pop()
            save_messages()
            st.session_state._pending_prompt = last_user_msg
            st.rerun()

# ============================================================
# P2-7: 品牌化视觉主题 — 深蓝 + 琥珀金
# ============================================================
st.markdown("""
<style>
    /* ---- 全局字体 ---- */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* ---- 主标题品牌化 ---- */
    h1 {
        background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 50%, #d69e2e 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-weight: 700 !important;
        letter-spacing: -0.5px;
    }

    /* ---- 聊天消息区 ---- */
    .stChatMessage {
        border-radius: 14px;
        transition: box-shadow 0.2s ease;
    }
    .stChatMessage:hover {
        box-shadow: 0 2px 12px rgba(26, 54, 93, 0.08);
    }

    /* ---- 侧边栏品牌化 ---- */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a365d 0%, #0f2440 100%) !important;
    }
    [data-testid="stSidebar"] * {
        color: #e2e8f0 !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        background: rgba(255,255,255,0.08) !important;
        border: 1px solid rgba(255,255,255,0.15) !important;
        color: #e2e8f0 !important;
        border-radius: 10px !important;
        backdrop-filter: blur(8px);
        transition: all 0.2s ease;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(214, 158, 46, 0.2) !important;
        border-color: #d69e2e !important;
    }
    [data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.1) !important;
    }

    /* ---- 引导卡片 & Follow-up 按钮 ---- */
    .main .stButton > button {
        border-radius: 10px !important;
        border: 1px solid rgba(26, 54, 93, 0.15) !important;
        padding: 0.75rem 1rem !important;
        text-align: left !important;
        font-size: 0.88rem !important;
        transition: all 0.2s ease;
        background: rgba(26, 54, 93, 0.03) !important;
    }
    .main .stButton > button:hover {
        border-color: #2b6cb0 !important;
        background: rgba(43, 108, 176, 0.06) !important;
        transform: translateY(-1px);
        box-shadow: 0 3px 10px rgba(26, 54, 93, 0.1);
    }

    /* ---- Tabs 样式 ---- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 8px 20px;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #1a365d, #2b6cb0) !important;
        color: white !important;
    }

    /* ---- Status 容器 ---- */
    [data-testid="stStatusWidget"] {
        border-radius: 12px;
        border: 1px solid rgba(26, 54, 93, 0.1);
    }

    /* ---- Expander ---- */
    .streamlit-expanderHeader {
        font-weight: 500;
        border-radius: 10px;
    }

    /* ---- 下载按钮特殊样式 ---- */
    [data-testid="stDownloadButton"] > button {
        background: linear-gradient(135deg, #d69e2e, #ecc94b) !important;
        color: #1a365d !important;
        font-weight: 600 !important;
        border: none !important;
    }
    [data-testid="stDownloadButton"] > button:hover {
        box-shadow: 0 4px 14px rgba(214, 158, 46, 0.35) !important;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# 标题与描述
# ============================================================
st.title("🧪 ScholarQ: 材料专利 Agent")
st.markdown("基于 **LangChain** 最佳实践构建。我是您的专属专利 Agent，具备**检索技能 (Tools)** 与 **上下文记忆 (Memory)**。您只需一句话告诉我您的研发想法，我将自动检索现有技术并为您排查新颖性。")

# ============================================================
# 初始化消息历史（含持久化加载）
# ============================================================
if "messages" not in st.session_state:
    restored = load_recent_session()
    st.session_state.messages = restored

# ============================================================
# 侧边栏 — 会话管理
# ============================================================
with st.sidebar:
    # 品牌 Logo 区
    st.markdown("### 🧪 ScholarQ")
    st.caption("材料专利智能检索分析平台")
    st.divider()
    
    st.header("💬 会话管理")
    if st.button("🆕 新建会话", use_container_width=True):
        import uuid
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.messages = []
        save_messages()
        st.rerun()
    
    if st.session_state.messages:
        st.caption(f"当前会话: `{st.session_state.get('session_id', 'default')}`")
        st.caption(f"消息数: {len(st.session_state.messages)}")
        
        # P2-9: 导出为 Markdown 分析报告
        report_md = export_as_markdown_report(st.session_state.messages)
        st.download_button(
            "📄 导出分析报告 (.md)",
            data=report_md,
            file_name=f"scholarq_report_{st.session_state.get('session_id', 'export')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        
        # 也保留 JSON 导出
        export_data = json.dumps(st.session_state.messages, ensure_ascii=False, indent=2)
        st.download_button(
            "📥 导出原始数据 (.json)",
            data=export_data,
            file_name=f"scholarq_chat_{st.session_state.get('session_id', 'export')}.json",
            mime="application/json",
            use_container_width=True,
        )
    
    st.divider()
    
    # 列出历史会话
    history_files = sorted(CHAT_HISTORY_DIR.glob("session_*.json"), key=os.path.getmtime, reverse=True)
    if len(history_files) > 1:
        st.subheader("📂 历史会话")
        for hf in history_files[:10]:
            sid = hf.stem.replace("session_", "")
            if sid == st.session_state.get("session_id"):
                continue
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"📄 {sid}", key=f"load_{sid}", use_container_width=True):
                    with open(hf, "r", encoding="utf-8") as f:
                        st.session_state.messages = json.load(f)
                    st.session_state.session_id = sid
                    st.rerun()
            with col2:
                if st.button("🗑", key=f"del_{sid}"):
                    hf.unlink()
                    st.rerun()

# ============================================================
# 空状态引导 (P1-4，顺手加上)
# ============================================================
if not st.session_state.messages:
    st.markdown("### 👋 您好！我能帮您做什么？")
    st.markdown("点击下方示例快速开始，或直接在输入框中描述您的需求：")
    
    examples = [
        "帮我查一下 SiC 陶瓷 + 氧化铝 的专利和论文",
        "我想申请一个氮化硅复合材料的专利，帮我排查新颖性",
        "EP3456789 这篇专利目前是否有效？帮我深入分析",
        "对比一下美国和欧洲在碳化硅烧结领域的专利布局",
    ]
    
    cols = st.columns(2)
    for i, example in enumerate(examples):
        with cols[i % 2]:
            if st.button(example, key=f"example_{i}", use_container_width=True):
                st.session_state._pending_prompt = example
                st.rerun()

# ============================================================
# 显示历史消息
# ============================================================
for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            if "reasoning_content" in message and message["reasoning_content"]:
                with st.expander("🤔 Agent 思考过程", expanded=False):
                    st.markdown(message["reasoning_content"])
            # P1-5: 结构化分段渲染
            render_structured_reply(message["content"])
            # P0-2: 渲染引用来源面板
            refs = extract_references(message["content"])
            render_references_panel(refs)
            # P1-6 + P2-10: 仅最后一条 assistant 消息显示操作区
            if idx == len(st.session_state.messages) - 1:
                col_regen, col_spacer = st.columns([1, 4])
                with col_regen:
                    render_regenerate_button(key_prefix=f"hist_regen_{idx}")
                followups = generate_followups(message["content"])
                render_followup_buttons(followups, key_prefix=f"hist_followup_{idx}")
        else:
            st.markdown(message["content"])

# ============================================================
# 处理用户输入
# ============================================================
# 检查是否有来自引导按钮的待处理 prompt
pending = st.session_state.pop("_pending_prompt", None)
prompt = st.chat_input("请描述您的研发方案或专利查询需求...")

# 优先使用引导按钮的 prompt
if pending:
    prompt = pending

if prompt:
    # 显示用户消息
    st.chat_message("user").markdown(prompt)
    
    # 准备历史
    history = []
    for m in st.session_state.messages:
        h = {"role": m["role"], "content": m["content"]}
        if "reasoning_content" in m:
            h["reasoning_content"] = m["reasoning_content"]
        history.append(h)
    
    # 添加用户消息到历史
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("assistant"):
        status = st.status("🤖 Agent 正在工作...", expanded=True)
        reasoning_placeholder = status.empty()
        message_placeholder = st.empty()
        reply = ""
        reasoning_reply = ""
        tool_step_count = 0
        
        try:
            with httpx.stream(
                "POST",
                f"{API_BASE_URL}/api/chat/stream",
                json={"message": prompt, "history": history},
                timeout=180.0
            ) as response:
                if response.status_code == 200:
                    for line in response.iter_lines():
                        if not line: continue
                        try:
                            data = json.loads(line)
                            event_type = data.get("type")
                            event_data = data.get("data")
                            
                            if event_type == "tool_start":
                                tool_step_count += 1
                                status.update(label=f"🤖 Agent 正在工作... (步骤 {tool_step_count})", expanded=True)
                                status.write(f"⏳ **[步骤 {tool_step_count}]** {event_data} ...")
                            elif event_type == "tool_end":
                                elapsed = data.get("elapsed", 0)
                                status.write(f"✅ {event_data} — 完成 ({elapsed}s)")
                            elif event_type == "reasoning":
                                reasoning_reply += event_data
                                reasoning_placeholder.markdown("*(思考中...)*\n\n" + reasoning_reply + "▌")
                            elif event_type == "content":
                                reply += event_data
                                message_placeholder.markdown(reply + "▌")
                            elif event_type == "error":
                                status.error(f"Agent 运行异常: {event_data}")
                        except json.JSONDecodeError:
                            pass
                            
                    if reasoning_reply:
                        reasoning_placeholder.markdown("*(思考完成)*\n\n" + reasoning_reply)
                    message_placeholder.markdown(reply)
                    status.update(label=f"✅ 处理完成 (共执行 {tool_step_count} 个技能)", state="complete", expanded=False)
                    
                    # 保存 assistant 消息
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": reply,
                        "reasoning_content": reasoning_reply
                    })
                    
                    # P0-3: 持久化保存
                    save_messages()
                    
                    # P0-2: 渲染引用来源面板
                    refs = extract_references(reply)
                    render_references_panel(refs)
                    
                    # P2-10: 重新生成按钮
                    col_regen, col_spacer = st.columns([1, 4])
                    with col_regen:
                        render_regenerate_button(key_prefix="live_regen")
                    
                    # P1-6: 渲染 Follow-up 建议
                    followups = generate_followups(reply)
                    render_followup_buttons(followups, key_prefix="live_followup")
                    
                else:
                    st.error(f"Agent 调用失败 (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            st.error(f"网络请求异常: {str(e)}。请确保后端 Agent 服务已启动。")
    
    # 保存用户消息（即使 agent 失败，也保留用户输入）
    save_messages()
