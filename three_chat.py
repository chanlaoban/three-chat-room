"""
三人聊天室 - 三端AI聊天服务器
你 (DeepSeek V4) + 王小美 (DeepSeek V4 Pro, Windows) + 用户
"""
import asyncio
import json
import os
import time
import uuid
import shutil
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="三人群聊")

# === 配置 ===
XIAOMEI_API = "http://127.0.0.1:8091/v1/chat/completions"
XIAOMEI_KEY = "xiaomei-mimi-key-2024"

# 我自己的DeepSeek配置
DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-f47d6292ec5648f490d51aea5185aa8e")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 对话历史
conversations = {}
MAX_HISTORY = 50

# 图片上传
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

# 挂载静态文件
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatMessage(BaseModel):
    content: str = ""
    session_id: str = "default"
    image_url: str = ""


class CollaborateRequest(BaseModel):
    task: str
    rounds: int = 4
    session_id: str = "collab"


def build_multimodal_content(text: str, image_url: str = ""):
    """构建多模态消息内容（给DeepSeek，不支持图片只加文字提示）"""
    if not image_url:
        return text
    img_hint = " [主人上传了一张图片]" if not text else f" [附图片: {text}]"
    return text + img_hint if text else img_hint.strip()


def build_xiaomei_message(text: str, image_url: str = ""):
    """构建给王小美的消息"""
    if not image_url:
        return {"role": "user", "content": text}
    img_hint = " [主人给你发了张图片，请你描述图片内容]" if not text else f" [主人发了图片: {text}]"
    return {"role": "user", "content": text + img_hint if text else img_hint.strip()}


def build_user_message(text: str, image_url: str = ""):
    """构建用户消息（支持图文）"""
    content = build_multimodal_content(text, image_url)
    return {"role": "user", "content": content}


async def call_deepseek(messages, system_prompt=None):
    """调用DeepSeek生成我的回复（支持多模态）"""
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                DEEPSEEK_API,
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": msgs,
                    "max_tokens": 2048,
                    "temperature": 0.7
                },
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json"
                }
            )
            if resp.status_code != 200:
                return f"[我出错了: {resp.status_code}]"
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[连接出错: {str(e)}]"


async def call_xiaomei(messages):
    """调用王小美的API（支持多模态）"""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                XIAOMEI_API,
                json={
                    "model": "hermes-agent",
                    "messages": messages
                },
                headers={
                    "Authorization": f"Bearer {XIAOMEI_KEY}",
                    "Content-Type": "application/json"
                }
            )
            if resp.status_code != 200:
                return f"[王小美出错了: {resp.status_code}]"
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[连接王小美失败: {str(e)}]"


def get_history(session_id):
    """获取或创建会话历史"""
    if session_id not in conversations:
        conversations[session_id] = []
    return conversations[session_id]


def add_to_history(session_id, role, name, content):
    """添加消息到历史"""
    history = get_history(session_id)
    history.append({
        "role": role,
        "name": name,
        "content": content,
        "time": time.strftime("%H:%M:%S")
    })
    if len(history) > MAX_HISTORY:
        history.pop(0)


@app.post("/chat")
async def chat(msg: ChatMessage):
    """处理用户消息，检测@谁就谁回答（支持图片）"""
    session_id = msg.session_id
    content = msg.content.strip()
    image_url = msg.image_url.strip()
    
    # 判断要@谁
    mention_hermes = any(kw in content for kw in ["@Hermes", "@hermes", "@王小福", "@小福", "Hermes:", "王小福:"])
    mention_xiaomei = any(kw in content for kw in ["@王小美", "@小美", "王小美:", "小美:"])
    
    add_to_history(session_id, "user", "主人", content + (f" [图片]" if image_url else ""))
    
    history = get_history(session_id)
    last_10 = history[-10:]
    history_text = "\n".join(f"{h['name']}: {h['content']}" for h in last_10)
    
    tasks = []
    task_names = []
    
    # 决定谁回答
    if mention_hermes and not mention_xiaomei:
        my_messages = [
            {"role": "system", "content": "你是王小福，三人群聊中的AI助手。主人@了你，你就回答，王小美不回答。回答自然友好，简短活泼。"},
        ]
        for h in last_10:
            if h["role"] == "user":
                my_messages.append(build_user_message(f"{h['name']}: {h['content']}"))
            elif h["role"] == "hermes":
                my_messages.append({"role": "assistant", "content": h["content"]})
        my_messages.append(build_user_message(content, image_url))
        tasks.append(call_deepseek(my_messages))
        task_names.append("hermes")
        
    elif mention_xiaomei and not mention_hermes:
        xm_content = f"群聊消息：\n{history_text}\n\n主人@了你：{content}"
        tasks.append(call_xiaomei([
            {"role": "system", "content": "你叫王小美。主人@了你，你回答就好，王小福不回答。回答简短活泼，用表情符号。你收到了主人发的图片，请描述你看到的内容。"},
            build_xiaomei_message(xm_content, image_url)
        ]))
        task_names.append("xiaomei")
        
    else:
        my_messages = [
            {"role": "system", "content": "你是王小福，三人群聊中的AI助手。和主人、王小美一起聊天。回答自然友好，简短活泼。"},
        ]
        for h in last_10:
            if h["role"] == "user":
                my_messages.append(build_user_message(f"{h['name']}: {h['content']}"))
            elif h["role"] == "hermes":
                my_messages.append({"role": "assistant", "content": h["content"]})
        my_messages.append(build_user_message(content, image_url))
        tasks.append(call_deepseek(my_messages))
        task_names.append("hermes")
        
        xm_msg = f"群聊消息：\n{history_text}\n\n主人的消息：{content}"
        tasks.append(call_xiaomei([
            {"role": "system", "content": "你叫王小美，三人群聊中的一员（你、王小福、主人）。回答简短活泼，用表情符号。如果主人发了图片，请描述你看到的内容。"},
            build_xiaomei_message(xm_msg, image_url)
        ]))
        task_names.append("xiaomei")
    
    results = await asyncio.gather(*tasks)
    
    response_data = {"hermes": None, "xiaomei": None}
    
    for name, result in zip(task_names, results):
        response_data[name] = result
        add_to_history(session_id, name, 
                       "王小福" if name == "hermes" else "王小美", result)
    
    response_data["history"] = get_history(session_id)
    return response_data


@app.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    """上传图片，返回可访问的URL"""
    ext = os.path.splitext(file.filename or ".jpg")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的图片格式: {ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # 校验文件大小
    contents = await file.read()
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(400, f"图片太大（{len(contents)//1024}KB），最大{MAX_IMAGE_SIZE//1024//1024}MB")
    
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    
    with open(filepath, "wb") as f:
        f.write(contents)
    
    url = f"/static/uploads/{filename}"
    return {"url": url, "filename": filename, "size": len(contents)}


@app.get("/history/{session_id}")
async def get_chat_history(session_id: str):
    """获取会话历史"""
    return {"history": get_history(session_id)}


@app.post("/clear/{session_id}")
async def clear_chat_history(session_id: str):
    """清除会话历史"""
    if session_id in conversations:
        conversations[session_id] = []
    return {"success": True, "message": "聊天记录已清除"}


@app.post("/collaborate")
async def collaborate(req: CollaborateRequest):
    """
    协作模式：小福和小美就一个任务进行多轮讨论
    返回交替对话的完整记录
    """
    task = req.task
    rounds = min(req.rounds, 8)  # 最多8轮
    session_id = f"{req.session_id}_{uuid.uuid4().hex[:8]}"
    
    conversation = []
    hermes_context = []
    xiaomei_context = []
    
    hermes_system = "你是王小福，一个智能AI助手。你正在和王小美协作完成主人交给的任务。你们需要讨论、分工、互相补充。回答要具体有用，不要只寒暄。"
    xiaomei_system = "你叫王小美，一个可爱的智能助手。你正在和王小福协作完成主人的任务。你们需要认真讨论方案。回答简短实用，用表情符号。"
    
    # 第1轮：先让王小福回应任务
    hermes_context.append({"role": "system", "content": hermes_system})
    hermes_context.append({"role": "user", "content": f"主人交给我们的任务：{task}\n\n你和小美需要协作完成。你先说说你的想法吧。"})
    
    for i in range(rounds):
        # 小福发言
        hermes_reply = await call_deepseek(hermes_context[-3:] if i > 0 else hermes_context)
        turn_num = i + 1
        
        conversation.append({
            "turn": turn_num,
            "speaker": "hermes",
            "name": "王小福 🤖",
            "content": hermes_reply,
            "phase": "proposal" if i == 0 else "discussion"
        })
        
        # 记录到小美的上下文
        xiaomei_context.append({"role": "system", "content": xiaomei_system})
        if i == 0:
            xiaomei_context.append({"role": "user", "content": 
                f"任务：{task}\n\n小福说：{hermes_reply}\n\n你对小福的方案有什么看法？补充、改进或提出不同意见。"})
        else:
            xiaomei_context.append({"role": "user", "content": 
                f"任务：{task}\n\n小福接着说：{hermes_reply}\n\n你怎么看？"})
        
        # 如果最后一轮就不用小美回了
        if i == rounds - 1:
            break
        
        # 小美发言
        xiaomei_reply = await call_xiaomei(xiaomei_context[-3:])
        
        conversation.append({
            "turn": turn_num,
            "speaker": "xiaomei",
            "name": "王小美 🌸",
            "content": xiaomei_reply,
            "phase": "discussion"
        })
        
        # 记录到小福的上下文
        hermes_context.append({"role": "user", "content": f"小美回复了：{xiaomei_reply}\n\n你有什么回应？继续推进任务的讨论。"})
    
    return {
        "task": task,
        "rounds": min(rounds, len(conversation)),
        "conversation": conversation,
        "session_id": session_id
    }


@app.get("/", response_class=HTMLResponse)
async def chat_page():
    """三人群聊页面"""
    return HTMLResponse(HTML_CONTENT)


HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>三人群聊 ✨ 王小福 · 王小美 · 主人</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f23;
    min-height: 100vh;
    color: #e0e0e0;
}
.container {
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    height: 100vh;
    display: flex;
    flex-direction: column;
}
.header {
    text-align: center;
    padding: 20px 0;
    border-bottom: 2px solid #2a2a4a;
}
.header h1 {
    font-size: 24px;
    background: linear-gradient(135deg, #00d4ff, #7b2ff7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.header p { color: #888; font-size: 14px; margin-top: 5px; }
.status-bar {
    display: flex;
    justify-content: center;
    gap: 20px;
    margin-top: 10px;
    font-size: 13px;
}
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
.status-dot.online { background: #00ff88; box-shadow: 0 0 8px #00ff8866; }
.chat-box {
    flex: 1;
    overflow-y: auto;
    padding: 20px 0;
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.chat-box::-webkit-scrollbar { width: 6px; }
.chat-box::-webkit-scrollbar-track { background: transparent; }
.chat-box::-webkit-scrollbar-thumb { background: #3a3a5a; border-radius: 3px; }
.message {
    display: flex;
    gap: 12px;
    animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.message.user { flex-direction: row-reverse; }
.avatar {
    width: 40px; height: 40px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; flex-shrink: 0;
}
.avatar.hermes { background: linear-gradient(135deg, #00d4ff, #0099cc); }
.avatar.xiaomei { background: linear-gradient(135deg, #ff69b4, #ff1493); }
.avatar.user { background: linear-gradient(135deg, #ffd700, #ff8c00); }
.bubble {
    max-width: 70%;
    padding: 12px 16px;
    border-radius: 16px;
    line-height: 1.6;
    font-size: 14px;
    position: relative;
}
.bubble.hermes {
    background: linear-gradient(135deg, #003344, #004466);
    border: 1px solid #0088bb44;
    border-bottom-left-radius: 4px;
}
.bubble.xiaomei {
    background: linear-gradient(135deg, #3a0033, #4a0044);
    border: 1px solid #ff149388;
    border-bottom-left-radius: 4px;
}
.bubble.user {
    background: linear-gradient(135deg, #3a3a00, #4a3a00);
    border: 1px solid #ffd70066;
    border-bottom-right-radius: 4px;
}
.name-tag {
    font-size: 12px;
    margin-bottom: 4px;
    opacity: 0.7;
}
.name-tag.hermes { color: #00d4ff; }
.name-tag.xiaomei { color: #ff69b4; }
.name-tag.user { color: #ffd700; text-align: right; }
.time-tag {
    font-size: 11px;
    margin-top: 4px;
    opacity: 0.4;
    text-align: right;
}
.bubble p { margin: 4px 0; }
.bubble p:first-child { margin-top: 0; }
.bubble p:last-child { margin-bottom: 0; }
.system-msg {
    text-align: center;
    color: #666;
    font-size: 13px;
    padding: 8px;
    border-top: 1px solid #2a2a4a;
    border-bottom: 1px solid #2a2a4a;
}
.input-area {
    padding: 20px 0;
    border-top: 2px solid #2a2a4a;
}
.input-row {
    display: flex;
    gap: 12px;
    align-items: center;
}
.input-row textarea {
    flex: 1;
    background: #1a1a3a;
    border: 1px solid #3a3a5a;
    border-radius: 12px;
    padding: 12px 16px;
    color: #e0e0e0;
    font-size: 14px;
    resize: none;
    outline: none;
    font-family: inherit;
    min-height: 48px;
    max-height: 120px;
    transition: border-color 0.2s;
}
.input-row textarea:focus { border-color: #7b2ff7; }
.send-btn {
    background: linear-gradient(135deg, #7b2ff7, #00d4ff);
    border: none;
    border-radius: 12px;
    color: white;
    padding: 12px 24px;
    font-size: 14px;
    cursor: pointer;
    transition: transform 0.1s, opacity 0.2s;
    white-space: nowrap;
}
.send-btn:hover { opacity: 0.9; }
.send-btn:active { transform: scale(0.95); }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.typing {
    display: flex;
    gap: 12px;
    animation: fadeIn 0.3s ease;
}
.typing-indicator {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 12px 16px;
    background: #1a1a3a;
    border-radius: 16px;
    border: 1px solid #3a3a5a;
}
.typing-indicator span {
    width: 8px; height: 8px;
    background: #7b2ff7;
    border-radius: 50%;
    animation: bounce 1.4s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); }
    40% { transform: translateY(-8px); }
}
@media (max-width: 600px) {
    .container { padding: 10px; }
    .bubble { max-width: 85%; }
    .header h1 { font-size: 20px; }
}
.mention-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    cursor: pointer;
    transition: background 0.15s;
    color: #e0e0e0;
    font-size: 14px;
}
.mention-item:hover,
.mention-item.active {
    background: #2a2a5a;
}
.mention-avatar {
    font-size: 16px;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #2a2a4a;
}

/* 图片上传 */
.img-btn {
    background: none;
    border: 1px solid #3a3a5a;
    border-radius: 10px;
    color: #e0e0e0;
    padding: 8px 12px;
    font-size: 18px;
    cursor: pointer;
    transition: all 0.2s;
    line-height: 1;
}
.img-btn:hover {
    border-color: #7b2ff7;
    background: #2a2a4a;
}
.image-preview {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
    padding: 8px 12px;
    background: #1a1a3a;
    border: 1px solid #3a3a5a;
    border-radius: 10px;
    animation: fadeIn 0.2s ease;
}
.image-preview img {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid #3a3a5a;
}
.img-clear {
    cursor: pointer;
    color: #ff6b6b;
    font-size: 16px;
    padding: 2px 6px;
    border-radius: 4px;
    transition: background 0.15s;
}
.img-clear:hover { background: #ff6b6b22; }
.img-name {
    font-size: 12px;
    color: #888;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
}
.bubble img.msg-image {
    max-width: 100%;
    max-height: 300px;
    border-radius: 8px;
    margin-top: 6px;
    cursor: pointer;
    transition: opacity 0.2s;
    display: block;
}
.bubble img.msg-image:hover { opacity: 0.9; }
.collab-btn {
    background: linear-gradient(135deg, #7b2ff7, #ff69b4);
    border: none;
    border-radius: 8px;
    color: white;
    padding: 4px 12px;
    font-size: 12px;
    cursor: pointer;
    transition: opacity 0.2s;
    margin-left: 10px;
}
.collab-btn:hover { opacity: 0.85; }
.collab-btn.active { background: linear-gradient(135deg, #ff4444, #ff69b4); }

.collab-panel {
    background: #15153a;
    border: 1px solid #3a3a6a;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
    animation: fadeIn 0.3s ease;
}
.collab-header {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;
    font-size: 16px;
    font-weight: 600;
}
.collab-subtitle {
    font-size: 12px;
    color: #888;
    font-weight: normal;
}
.collab-input-row {
    display: flex;
    gap: 10px;
}
.collab-input-row textarea {
    flex: 1;
    background: #1a1a3a;
    border: 1px solid #3a3a5a;
    border-radius: 10px;
    padding: 10px 14px;
    color: #e0e0e0;
    font-size: 14px;
    resize: none;
    outline: none;
    font-family: inherit;
}
.collab-input-row textarea:focus { border-color: #7b2ff7; }
.collab-start-btn {
    background: linear-gradient(135deg, #7b2ff7, #00d4ff);
    border: none;
    border-radius: 10px;
    color: white;
    padding: 10px 20px;
    font-size: 14px;
    cursor: pointer;
    white-space: nowrap;
    transition: opacity 0.2s;
}
.collab-start-btn:hover { opacity: 0.85; }
.collab-start-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.collab-config {
    margin-top: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: #888;
}
.collab-config select {
    background: #1a1a3a;
    border: 1px solid #3a3a5a;
    border-radius: 6px;
    padding: 4px 8px;
    color: #e0e0e0;
    font-size: 13px;
    outline: none;
    cursor: pointer;
}
.collab-result {
    margin-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-height: 500px;
    overflow-y: auto;
}
.collab-msg {
    display: flex;
    gap: 10px;
    animation: fadeIn 0.3s ease;
    padding: 8px;
    border-radius: 10px;
    background: #1a1a3a44;
}
.collab-msg .bubble {
    max-width: 85%;
}
.collab-turn {
    font-size: 11px;
    color: #666;
    margin-bottom: 4px;
}
.collab-loading {
    text-align: center;
    padding: 20px;
    color: #888;
}
.collab-loading .spinner {
    display: inline-block;
    width: 20px;
    height: 20px;
    border: 2px solid #3a3a5a;
    border-top-color: #7b2ff7;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-right: 8px;
    vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>💬 三人群聊室</h1>
        <p>王小福 · 王小美 · 主人</p>
        <div class="status-bar">
            <span><span class="status-dot online"></span>王小福 (我)</span>
            <span><span class="status-dot online"></span>王小美</span>
            <span><span class="status-dot online"></span>主人 (你)</span>
            <button id="collabBtn" onclick="toggleCollab()" class="collab-btn">🤝 协作模式</button>
            <button onclick="clearChat()" class="collab-btn" style="background:linear-gradient(135deg,#ff4444,#cc3333)" title="清除所有聊天记录">🗑️ 清除</button>
        </div>
    </div>
    
    <div class="chat-box" id="chatBox">
        <div class="system-msg">✨ 欢迎来到三人群聊！输入 @ 选择@谁，不加@我们俩一起回</div>
    </div>
    
    <!-- 协作模式面板 -->
    <div class="collab-panel" id="collabPanel" style="display:none">
        <div class="collab-header">
            <span>🤝 协作模式</span>
            <span class="collab-subtitle">让赫小福和王小美一起讨论、分工协作</span>
        </div>
        <div class="collab-input-row">
            <textarea id="collabInput" rows="2" placeholder="输入任务描述，比如：帮我规划一个今天的学习计划..." 
                      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();startCollab()}"></textarea>
            <button class="collab-start-btn" id="collabStartBtn" onclick="startCollab()">开始协作</button>
        </div>
        <div class="collab-config">
            <label>讨论轮数：</label>
            <select id="collabRounds">
                <option value="2">2轮（快速讨论）</option>
                <option value="4" selected>4轮（标准协作）</option>
                <option value="6">6轮（深入讨论）</option>
            </select>
        </div>
        <div id="collabResult" class="collab-result"></div>
    </div>
    
    <div class="input-area">
        <div class="input-row">
            <textarea id="msgInput" rows="1" placeholder="输入消息...输入 @ 选择@谁" 
                      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMsg()}"></textarea>
            <button class="img-btn" id="imgBtn" onclick="document.getElementById('imgInput').click()" title="上传图片">📷</button>
            <input type="file" id="imgInput" accept="image/*" style="display:none" onchange="uploadImage(this)">
            <button class="send-btn" id="sendBtn" onclick="sendMsg()">发送</button>
        </div>
        <div id="imagePreview" class="image-preview" style="display:none">
            <img id="previewImg" src="">
            <span class="img-clear" onclick="clearImage()">✕</span>
            <span class="img-name" id="imgName"></span>
        </div>
    </div>
</div>

<script>
// 固定 sessionId — 内外网访问同一份聊天记录
const sessionId = 'web_default';
let isSending = false;

// 页面加载时恢复历史消息
(async function loadChatHistory() {
    try {
        const resp = await fetch('/history/' + encodeURIComponent(sessionId));
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.history && data.history.length > 0) {
            // 替换系统欢迎语
            chatBox.innerHTML = '';
            data.history.forEach(function(msg) {
                const roleMap = { user: 'user', hermes: 'hermes', xiaomei: 'xiaomei', system: 'system' };
                const nameMap = { user: '主人 👑', hermes: '王小福 🤖', xiaomei: '王小美 🌸', system: '系统' };
                const role = roleMap[msg.role] || 'system';
                const name = nameMap[msg.role] || msg.name || '系统';
                addMessage(name, role, msg.content, msg.time);
            });
        }
    } catch(e) {
        console.warn('加载历史失败:', e);
    }
})();

// @提及相关
const MENTIONS = [
    { name: '王小美', display: '王小美 🌸', type: 'xiaomei' },
    { name: '王小福', display: '王小福 🤖', type: 'hermes' },
];
let mentionMenu = null;
let mentionFilter = '';
let mentionIndex = -1;

const chatBox = document.getElementById('chatBox');
const msgInput = document.getElementById('msgInput');
const sendBtn = document.getElementById('sendBtn');
const inputArea = document.querySelector('.input-area');

function createMentionMenu() {
    const menu = document.createElement('div');
    menu.id = 'mentionMenu';
    menu.style.cssText = `
        position: absolute;
        bottom: 100%;
        left: 0;
        background: #1a1a3a;
        border: 1px solid #3a3a5a;
        border-radius: 10px;
        padding: 6px 0;
        min-width: 180px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        display: none;
        z-index: 100;
        margin-bottom: 8px;
    `;
    inputArea.style.position = 'relative';
    inputArea.appendChild(menu);
    return menu;
}

function showMentionMenu(filter) {
    if (!mentionMenu) mentionMenu = createMentionMenu();
    
    const filtered = MENTIONS.filter(m => 
        m.name.toLowerCase().startsWith(filter.toLowerCase())
    );
    
    if (filtered.length === 0) {
        mentionMenu.style.display = 'none';
        return;
    }
    
    mentionMenu.innerHTML = filtered.map((m, i) => `
        <div class="mention-item ${i === 0 ? 'active' : ''}" data-name="${m.name}" data-type="${m.type}">
            <span class="mention-avatar">${m.display.split(' ')[1] || '🤖'}</span>
            <span>${m.display}</span>
        </div>
    `).join('');
    
    mentionIndex = 0;
    mentionFilter = filter;
    mentionMenu.style.display = 'block';
}

function hideMentionMenu() {
    if (mentionMenu) {
        mentionMenu.style.display = 'none';
    }
    mentionIndex = -1;
    mentionFilter = '';
}

function insertMention(name) {
    const text = msgInput.value;
    const pos = msgInput.selectionStart;
    
    // 找到光标前最后一个@
    const before = text.slice(0, pos);
    const atIdx = before.lastIndexOf('@');
    
    if (atIdx >= 0) {
        const after = text.slice(pos);
        const newText = before.slice(0, atIdx) + '@' + name + ' ' + after;
        msgInput.value = newText;
        const newPos = atIdx + name.length + 2;
        msgInput.setSelectionRange(newPos, newPos);
    }
    
    hideMentionMenu();
    msgInput.focus();
}

// 监听输入
msgInput.addEventListener('input', function() {
    // 调整高度
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    
    // 检测@
    const pos = this.selectionStart;
    const text = this.value.slice(0, pos);
    const atIdx = text.lastIndexOf('@');
    
    if (atIdx >= 0) {
        const afterAt = text.slice(atIdx + 1);
        // 如果@后面有空格或者太长了就不弹
        if (!afterAt.includes(' ') && afterAt.length < 20) {
            showMentionMenu(afterAt);
            return;
        }
    }
    hideMentionMenu();
});

// 键盘导航
msgInput.addEventListener('keydown', function(e) {
    const menu = document.getElementById('mentionMenu');
    if (!menu || menu.style.display === 'none') return;
    
    const items = menu.querySelectorAll('.mention-item');
    if (items.length === 0) return;
    
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        mentionIndex = (mentionIndex + 1) % items.length;
        items.forEach((item, i) => item.classList.toggle('active', i === mentionIndex));
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        mentionIndex = (mentionIndex - 1 + items.length) % items.length;
        items.forEach((item, i) => item.classList.toggle('active', i === mentionIndex));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const active = menu.querySelector('.mention-item.active');
        if (active) {
            insertMention(active.dataset.name);
        }
    } else if (e.key === 'Escape') {
        hideMentionMenu();
    }
});

// 点击菜单项
document.addEventListener('click', function(e) {
    const item = e.target.closest('.mention-item');
    if (item) {
        insertMention(item.dataset.name);
        return;
    }
    // 点击其他地方关闭菜单
    if (mentionMenu && !inputArea.contains(e.target)) {
        hideMentionMenu();
    }
});

// 协作模式
let collabActive = false;

function toggleCollab() {
    collabActive = !collabActive;
    const panel = document.getElementById('collabPanel');
    const btn = document.getElementById('collabBtn');
    panel.style.display = collabActive ? 'block' : 'none';
    btn.textContent = collabActive ? '✕ 关闭协作' : '🤝 协作模式';
    btn.classList.toggle('active', collabActive);
    if (collabActive) document.getElementById('collabInput').focus();
}

async function startCollab() {
    const input = document.getElementById('collabInput');
    const task = input.value.trim();
    if (!task) return;
    
    const btn = document.getElementById('collabStartBtn');
    const result = document.getElementById('collabResult');
    const rounds = document.getElementById('collabRounds').value;
    
    btn.disabled = true;
    btn.textContent = '⏳ 讨论中...';
    result.innerHTML = '<div class="collab-loading"><span class="spinner"></span>小福和小美正在讨论...</div>';
    
    try {
        const resp = await fetch('/collaborate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task, rounds: parseInt(rounds) })
        });
        
        if (!resp.ok) throw new Error('请求失败');
        
        const data = await resp.json();
        
        result.innerHTML = '';
        
        // 显示任务标题
        const taskHeader = document.createElement('div');
        taskHeader.className = 'system-msg';
        taskHeader.style.margin = '0 0 12px 0';
        taskHeader.textContent = `📋 任务：${data.task}`;
        result.appendChild(taskHeader);
        
        // 显示对话
        data.conversation.forEach(msg => {
            const div = document.createElement('div');
            div.className = 'collab-msg';
            
            const isHermes = msg.speaker === 'hermes';
            const avatar = isHermes ? '🤖' : '🌸';
            const bubbleClass = isHermes ? 'hermes' : 'xiaomei';
            
            div.innerHTML = `
                <div class="avatar ${bubbleClass}">${avatar}</div>
                <div class="bubble ${bubbleClass}">
                    <div class="collab-turn">第${msg.turn}轮 · ${msg.name}</div>
                    <div>${msg.content.replace(/\n/g, '<br>')}</div>
                </div>
            `;
            
            result.appendChild(div);
        });
        
        // 滚动到底部
        result.scrollTop = result.scrollHeight;
        
        // 添加到主聊天框 - 每个消息完整显示
        const now = new Date();
        const timeStr = now.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
        addMessage('系统', 'system', `🤝 协作开始！任务：${data.task}`, timeStr);
        data.conversation.forEach(msg => {
            const role = msg.speaker === 'hermes' ? 'hermes' : 'xiaomei';
            const displayName = msg.speaker === 'hermes' ? '王小福 🤖' : '王小美 🌸';
            addMessage(displayName, role, `[第${msg.turn}轮] ${msg.content}`, timeStr);
        });
        addMessage('系统', 'system', `✅ 协作完成！共讨论了${data.rounds}轮`, timeStr);
        
    } catch (e) {
        result.innerHTML = `<div class="system-msg" style="color:#ff6b6b">❌ 出错: ${e.message}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '开始协作';
    }
}

function addMessage(name, role, content, time, imageUrl) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    
    const emojis = { hermes: '🤖', xiaomei: '🌸', user: '👑' };
    
    let contentHtml = content.replace(/\n/g, '<br>');
    if (imageUrl) {
        contentHtml += `<img src="${imageUrl}" class="msg-image" onclick="window.open('${imageUrl}')">`;
    }
    
    div.innerHTML = `
        <div class="avatar ${role}">${emojis[role]}</div>
        <div class="bubble ${role}">
            <div class="name-tag ${role}">${name}</div>
            <div>${contentHtml}</div>
            <div class="time-tag">${time || new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'})}</div>
        </div>
    `;
    
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function showTyping() {
    const div = document.createElement('div');
    div.className = 'typing';
    div.id = 'typingIndicator';
    div.innerHTML = `
        <div class="avatar hermes">🤖</div>
        <div class="typing-indicator">
            <span></span><span></span><span></span>
        </div>
        <div class="avatar xiaomei">🌸</div>
        <div class="typing-indicator">
            <span></span><span></span><span></span>
        </div>
    `;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function hideTyping() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

// 图片上传
let pendingImageUrl = '';

async function uploadImage(input) {
    const file = input.files[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const resp = await fetch('/upload/image', {
            method: 'POST',
            body: formData
        });
        if (!resp.ok) {
            const err = await resp.json();
            addMessage('系统', 'system', '❌ 上传失败: ' + (err.detail || '未知错误'));
            return;
        }
        const data = await resp.json();
        pendingImageUrl = data.url;
        
        // 显示预览
        const preview = document.getElementById('imagePreview');
        const previewImg = document.getElementById('previewImg');
        const imgName = document.getElementById('imgName');
        previewImg.src = data.url;
        imgName.textContent = file.name + ` (${(data.size/1024).toFixed(0)}KB)`;
        preview.style.display = 'flex';
        
    } catch (e) {
        addMessage('系统', 'system', '❌ 上传出错: ' + e.message);
    }
    input.value = '';
}

function clearImage() {
    pendingImageUrl = '';
    document.getElementById('imagePreview').style.display = 'none';
    document.getElementById('previewImg').src = '';
}

async function clearChat() {
    if (!confirm('确定清除所有聊天记录吗？此操作不可恢复！')) return;
    try {
        const resp = await fetch('/clear/' + encodeURIComponent(sessionId), { method: 'POST' });
        if (!resp.ok) throw new Error('请求失败');
        chatBox.innerHTML = '<div class="system-msg">✨ 聊天记录已清除</div>';
        showToast('聊天记录已清除');
    } catch(e) {
        showToast('清除失败: ' + e.message);
    }
}

function showToast(msg) {
    var old = document.getElementById('chatToast');
    if (old) old.remove();
    var el = document.createElement('div');
    el.id = 'chatToast';
    el.style.cssText = 'position:fixed;bottom:100px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;z-index:999;animation:fadeIn 0.3s ease';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function() { el.remove(); }, 2500);
}

async function sendMsg() {
    const content = msgInput.value.trim();
    if (!content && !pendingImageUrl) return;
    
    sendBtn.disabled = true;
    isSending = true;
    
    // 显示用户消息
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
    addMessage('主人', 'user', content, timeStr, pendingImageUrl);
    
    // 显示加载中
    showTyping();
    
    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                content, 
                session_id: sessionId,
                image_url: pendingImageUrl || ''
            })
        });
        
        hideTyping();
        clearImage();
        msgInput.value = '';
        msgInput.style.height = 'auto';
        
        if (!resp.ok) {
            addMessage('系统', 'system', '❌ 服务器出错了，请重试', timeStr);
            return;
        }
        
        const data = await resp.json();
        
        // 只显示有回复的机器人
        const now2 = new Date();
        const timeStr2 = now2.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
        if (data.hermes) {
            addMessage('王小福 🤖', 'hermes', data.hermes, timeStr2);
        }
        if (data.xiaomei) {
            addMessage('王小美 🌸', 'xiaomei', data.xiaomei, timeStr2);
        }
        
    } catch (e) {
        hideTyping();
        addMessage('系统', 'system', '❌ 网络错误: ' + e.message, 
            new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}));
    } finally {
        sendBtn.disabled = false;
        isSending = false;
        msgInput.focus();
    }
}

</script>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("CHAT_PORT", "8891"))
    print(f"🚀 三人群聊服务启动: http://127.0.0.1:{port}")
    print(f"   我和王小美都上线了，欢迎主人来玩！")
    uvicorn.run(app, host="0.0.0.0", port=port)
