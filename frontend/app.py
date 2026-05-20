import streamlit as st
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="ScholarQ: 材料专利 Agent", layout="wide", page_icon="🧪")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")

st.title("🧪 ScholarQ: 材料专利 Agent")
st.markdown("基于 **LangChain** 最佳实践构建。我是您的专属专利 Agent，具备**检索技能 (Tools)** 与 **上下文记忆 (Memory)**。您只需一句话告诉我您的研发想法，我将自动检索现有技术并为您排查新颖性。")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if "reasoning_content" in message and message["reasoning_content"]:
            with st.expander("🤔 Agent 思考过程"):
                st.markdown(message["reasoning_content"])
        st.markdown(message["content"])

# React to user input
if prompt := st.chat_input("示例: 我在研究加入5%氧化铝的SiC耐热陶瓷，工艺是1500度热压烧结。帮我查一下有没有相关的专利或论文？"):
    # Display user message in chat message container
    st.chat_message("user").markdown(prompt)
    
    # Prepare history for backend
    history = []
    for m in st.session_state.messages:
        h = {"role": m["role"], "content": m["content"]}
        if "reasoning_content" in m:
            h["reasoning_content"] = m["reasoning_content"]
        history.append(h)
    
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("assistant"):
        status = st.status("Agent 处理中...", expanded=True)
        reasoning_placeholder = status.empty()
        message_placeholder = st.empty()
        reply = ""
        reasoning_reply = ""
        
        try:
            import json
            with httpx.stream(
                "POST",
                f"{API_BASE_URL}/api/chat/stream",
                json={"message": prompt, "history": history},
                timeout=120.0
            ) as response:
                if response.status_code == 200:
                    for line in response.iter_lines():
                        if not line: continue
                        try:
                            data = json.loads(line)
                            event_type = data.get("type")
                            event_data = data.get("data")
                            
                            if event_type == "tool_start":
                                status.write(f"⚙️ 开始执行技能: `{event_data}` ...")
                            elif event_type == "tool_end":
                                # 可以在这里做一些完成的视觉反馈
                                pass
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
                    status.update(label="处理完成", state="complete", expanded=False)
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": reply,
                        "reasoning_content": reasoning_reply
                    })
                else:
                    st.error(f"Agent 调用失败 (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            st.error(f"网络请求异常: {str(e)}。请确保后端 Agent 服务已启动。")
