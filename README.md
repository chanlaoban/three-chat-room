# 🎭 三人群聊室 — Three-Way Chat Room

小福（Hermes）🤖 · 王小美 🌸 · 主人 👑 三人在线聊天室

## 功能

### 💬 智能聊天
- 三人实时对话，支持 **@提及** 定向提问
- `@Hermes` / `@王小福` — 只有小福回答
- `@王小美` — 只有小美回答
- 不加 `@` — 两个一起回答
- 输入 `@` 自动弹出选人菜单，支持方向键 + 回车选择

### 🤝 协作模式
- 小福和小美能就一个任务**多轮讨论、分工协作**
- 可配置讨论深度（2/4/6轮）
- 讨论过程实时展示在聊天框

## 架构

```
┌─────────────┐     HTTP      ┌──────────────┐
│   主人 (你)   │ ──────────→  │  FastAPI 服务器 │
│  (浏览器)    │ ←──────────  │  (端口 8891)   │
└─────────────┘              └──────┬───────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐
              │ DeepSeek V4│  │ 王小美 API  │
              │  (小福/Hermes)│  │ (Windows)   │
              └───────────┘  └───────────┘
```

## 快速启动

```bash
# 安装依赖
pip install fastapi uvicorn httpx

# 启动服务
DEEPSEEK_API_KEY="你的key" DEEPSEEK_MODEL="deepseek-v4-flash" python3 three_chat.py

# 打开浏览器访问
# http://localhost:8891
```

## 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API密钥 | 内置默认值 |
| `DEEPSEEK_MODEL` | 小福使用的模型 | `deepseek-v4-flash` |
| `CHAT_PORT` | 服务端口 | `8891` |

## 王小美API配置

王小美需要运行 Hermes Agent 并开启 api_server 平台：

```yaml
# ~/.hermes/config.yaml
platforms:
  api_server:
    enabled: true
    extra:
      host: 0.0.0.0
      port: 8091
      key: xiaomei-mimi-key-2024
```

---

Built with ❤️ by chanlaoban
