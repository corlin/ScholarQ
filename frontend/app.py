import streamlit as st
import httpx
import os
import json
import re
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
# 自定义 CSS — 基本视觉增强
# ============================================================
st.markdown("""
<style>
    /* 聊天消息区微调 */
    .stChatMessage {
        border-radius: 12px;
    }
    /* 引导卡片样式 */
    div[data-testid="stHorizontalBlock"] > div > div > button {
        border-radius: 10px !important;
        border: 1px solid rgba(49, 51, 63, 0.2) !important;
        padding: 0.75rem 1rem !important;
        text-align: left !important;
        font-size: 0.9rem !important;
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
        
        # 导出当前对话
        export_data = json.dumps(st.session_state.messages, ensure_ascii=False, indent=2)
        st.download_button(
            "📥 导出对话记录 (.json)",
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
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            if "reasoning_content" in message and message["reasoning_content"]:
                with st.expander("🤔 Agent 思考过程", expanded=False):
                    st.markdown(message["reasoning_content"])
            st.markdown(message["content"])
            # P0-2: 渲染引用来源面板
            refs = extract_references(message["content"])
            render_references_panel(refs)
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
                    
                else:
                    st.error(f"Agent 调用失败 (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            st.error(f"网络请求异常: {str(e)}。请确保后端 Agent 服务已启动。")
    
    # 保存用户消息（即使 agent 失败，也保留用户输入）
    save_messages()
