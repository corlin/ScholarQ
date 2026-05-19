import streamlit as st
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="ScholarQ: 材料专利 Agent", layout="wide", page_icon="🧪")

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8001")

st.title("🧪 ScholarQ: 材料专利 Agent")
st.markdown("基于 **LangChain** 最佳实践构建。我是您的专属专利 Agent，具备**检索技能 (Tools)** 与 **上下文记忆 (Memory)**。您只需一句话告诉我您的研发想法，我将自动检索现有技术并为您排查新颖性。")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# React to user input
if prompt := st.chat_input("示例: 我在研究加入5%氧化铝的SiC耐热陶瓷，工艺是1500度热压烧结。帮我查一下有没有相关的专利或论文？"):
    # Display user message in chat message container
    st.chat_message("user").markdown(prompt)
    
    # Prepare history for backend
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("assistant"):
        with st.spinner("Agent 正在深度思考并执行文献检索技能...这可能需要一点时间..."):
            try:
                response = httpx.post(
                    f"{API_BASE_URL}/api/chat",
                    json={"message": prompt, "history": history},
                    timeout=120.0
                )
                if response.status_code == 200:
                    reply = response.json().get("reply", "无回复")
                    st.markdown(reply)
                    st.session_state.messages.append({"role": "assistant", "content": reply})
                else:
                    st.error(f"Agent 调用失败: {response.text}")
            except Exception as e:
                st.error(f"网络请求异常: {str(e)}。请确保后端 Agent 服务已启动。")
