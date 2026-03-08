# OpenAI 代理服务

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python: 3.9-3.12](https://img.shields.io/badge/python-3.9--3.12-green.svg)
![FastAPI](https://img.shields.io/badge/framework-FastAPI-009688.svg)

基于 FastAPI 的高性能 OpenAI API 兼容代理服务，当前支持 Z.AI 的 GLM-4.5/4.6/4.7/5 系列模型。

## ✨ 核心特性

- 🔌 **OpenAI API 兼容** - 无缝对接现有 OpenAI 客户端
- 🧬 **数据库管理** - SQLite + Web 后台统一管理 Token
- 🚀 **流式响应** - 高性能 SSE 实时流式输出
- 🧠 **思考模式** - 支持 Thinking 模型的推理过程展示
- 🐳 **容器化部署** - Docker/Docker Compose 一键部署
- 🔄 **Token 池** - 智能轮询、容错恢复、健康检查
- 📊 **管理后台** - 实时监控、配置管理
- 🔐 **安全认证** - 密码保护的管理后台访问

❤️ 感谢各位的反馈推动项目改进！

## 🚀 快速开始

### 环境要求

- Python 3.9-3.12
- pip 或 uv (推荐)

### 本地运行

```bash
# 1. 克隆项目
git clone https://github.com/ZyphrZero/z.ai2api_python.git
cd z.ai2api_python

# 2. 安装依赖（使用 uv 推荐）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 或使用 pip
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，设置 AUTH_TOKEN 等配置

# 4. 启动服务
uv run python main.py  # 或 python main.py
```

**首次运行会自动初始化数据库**，访问以下地址：
- API 文档：http://localhost:8080/docs
- 管理后台：http://localhost:8080/admin（**需要登录**）
- Token 管理：http://localhost:8080/admin/tokens

> ⚠️ **重要**：
> - 请妥善保管 `AUTH_TOKEN`，不要泄露给他人
> - 管理后台默认密码为 `admin123`，**首次使用后请立即修改**

### Docker 部署

从 Docker Hub 拉取镜像：

```bash
# 拉取最新镜像
docker pull zyphrzero/z-ai2api-python:latest

# 快速启动（创建数据目录）
mkdir -p data logs

# 运行容器
docker run -d \
  --name z-ai-api-server \
  -p 8080:8080 \
  -e ADMIN_PASSWORD=admin123 \
  -e AUTH_TOKEN=sk-your-api-key \
  -e ANONYMOUS_MODE=true \
  -e DB_PATH=/app/data/tokens.db \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  zyphrzero/z-ai2api-python:latest
```

启动服务：

```bash
docker compose up -d
```

#### 方式二：本地构建

```bash
# 进入部署目录
cd deploy

# 启动服务（会自动构建镜像）
docker compose up -d

# 查看日志
docker compose logs -f api-server
```

#### 数据持久化

容器使用卷映射自动持久化数据：

```
data/                  # 数据库文件存储目录
├── tokens.db          # SQLite 数据库（自动创建）
logs/                  # 日志文件存储目录
```

数据在容器重启或重建后仍然保留，无需担心丢失。

> 📖 **详细文档**：[Docker 部署指南](deploy/README_DOCKER.md)

## 📖 支持的模型

当前服务仅对接 Z.AI 提供的 GLM 系列模型。

| 模型 | 上游 ID | 特性 |
|------|---------|------|
| `GLM-4.5` | 0727-360B-API | 标准模型，通用对话 |
| `GLM-4.5-Thinking` | 0727-360B-API | 思考模型，显示推理过程 |
| `GLM-4.5-Search` | 0727-360B-API | 搜索模型，实时联网 |
| `GLM-4.5-Air` | 0727-106B-API | 轻量模型，快速响应 |
| `GLM-4.6V` | glm-4.6v | 多模态模型，支持图像理解 |
| `GLM-5` | glm-5 | 新一代通用模型 |
| `GLM-4.7` | glm-4.7 | 新版标准模型，200K 上下文 |
| `GLM-4.7-Thinking` | glm-4.7 | 新版思考模型，增强推理 |
| `GLM-4.7-Search` | glm-4.7 | 新版搜索模型，改进联网能力 |
| `GLM-4.7-advanced-search` | glm-4.7 | 高级搜索模型，深度研究 |

## ⚙️ 配置说明

### 核心环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `AUTH_TOKEN` | `sk-your-api-key` | 客户端访问密钥（必填） |
| `ADMIN_PASSWORD` | `admin123` | 管理后台登录密码（**强烈建议修改**） |
| `LISTEN_PORT` | `8080` | 服务监听端口 |
| `DEBUG_LOGGING` | `false` | 调试日志（支持热重载） |
| `ANONYMOUS_MODE` | `true` | Z.AI 匿名模式 |
| `TOOL_SUPPORT` | `true` | Function Call 开关 |
| `SKIP_AUTH_TOKEN` | `false` | 跳过认证（仅开发） |
| `DB_PATH` | `tokens.db` | 数据库文件路径（Docker: `/app/data/tokens.db`） |

### Token 配置

| 变量名 | 说明 |
|--------|------|
| `TOKEN_FAILURE_THRESHOLD` | Token 失败阈值（默认 3） |
| `TOKEN_RECOVERY_TIMEOUT` | Token 恢复超时（默认 1800 秒） |

> 💡 详细配置请参考 [.env.example](.env.example) 或 [deploy/.env.example](deploy/.env.example)

## 🔐 管理后台登录

### 首次登录

1. 启动服务后访问：http://localhost:8080/admin
2. 自动跳转到登录页面
3. 输入管理密码（默认：`admin123`）
4. 登录成功后进入仪表盘

### 修改密码

在 `.env` 文件中修改 `ADMIN_PASSWORD`：

```bash
# 使用强密码（推荐 12 位以上）
ADMIN_PASSWORD=Your_Secure_Password_2025!
```

重启服务后生效。

### 安全特性

- ✅ **Session 管理**：基于 Cookie 的安全 Session
- ✅ **自动过期**：登录后 24 小时自动失效
- ✅ **HttpOnly Cookie**：防止 XSS 攻击
- ✅ **SameSite 保护**：防止 CSRF 攻击
- ✅ **随机 Token**：使用加密安全的随机数生成

> 💡 详细文档：[管理后台登录功能使用说明](管理后台登录功能使用说明.md)

## 🔄 Token 管理

### 数据库方式（推荐）

项目使用 SQLite 数据库统一管理 Token，首次运行会自动初始化：

```bash
# 首次运行自动创建 tokens.db
python main.py

# 访问 Web 管理后台
http://localhost:8080/admin
```

### 管理后台功能

- ✅ **密码保护** - 安全的登录认证
- ✅ Token 增删改查
- ✅ 批量导入/导出
- ✅ 启用/禁用 Token
- ✅ Token 有效性检测

### Token 池机制

- **负载均衡**：轮询使用多个 Token 分散请求
- **自动容错**：Token 失败时自动切换
- **自动恢复**：失败 Token 超时后重试
- **智能去重**：自动检测重复 Token
- **回退机制**：认证失败自动降级匿名模式

## ❓ 常见问题

### Q: 如何获取 AUTH_TOKEN？
A: `AUTH_TOKEN` 是自定义的 API 密钥，用于客户端访问本服务，需在 `.env` 文件或 `docker-compose.yml` 中配置，确保客户端与服务端一致。

### Q: 匿名模式是什么？
A: 匿名模式使用临时 Token 访问 Z.AI，避免对话历史共享，保护隐私。设置 `ANONYMOUS_MODE=true` 启用。

### Q: 如何管理 Token？
A: 访问 Web 管理后台 http://localhost:8080/admin/tokens（需要先登录）即可增删改查 Token，支持批量导入导出。

### Q: 忘记管理后台密码怎么办？
A: 在 `.env` 文件或 `docker-compose.yml` 中修改 `ADMIN_PASSWORD` 为新密码，然后重启服务即可。

### Q: Docker 部署时数据库初始化失败？
A: 错误提示 `unable to open database file` 通常是权限问题。解决方案：
```bash
cd deploy
mkdir -p ./data ./logs
chmod 755 ./data ./logs
docker compose down && docker compose up -d --build
```
详见 [Docker 部署指南](deploy/README_DOCKER.md#故障排查)

### Q: 如何禁用管理后台登录？
A: 当前版本暂不支持禁用登录功能。如有需要，请手动移除路由中的 `dependencies=[Depends(require_auth)]`。


## 🔑 获取 Token

### Z.AI Token

1. 访问 [Z.AI 官网](https://chat.z.ai) 并登录
2. 按 F12 打开开发者工具
3. 进入 Application → Local Storage → Cookies
4. 复制 `token` 值

> ⚠️ 多模态功能需要非匿名 Token

## 🛠️ 技术栈

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| Web 框架 | [FastAPI](https://fastapi.tiangolo.com/) | 0.116.1 | 高性能异步框架 |
| ASGI 服务器 | [Granian](https://github.com/emmett-framework/granian) | 2.5.2 | Rust 高性能服务器 |
| HTTP 客户端 | [HTTPX](https://www.python-httpx.org/) | 0.28.1 | 异步 HTTP 客户端 |
| 数据验证 | [Pydantic](https://pydantic.dev/) | 2.11.7 | 类型安全验证 |
| 数据库 | SQLite (aiosqlite) | 0.20.0 | Token 存储 |
| 模板引擎 | Jinja2 | 3.1.4 | Web 后台模板 |
| 日志系统 | [Loguru](https://loguru.readthedocs.io/) | 0.7.3 | 结构化日志 |

## 🏗️ 系统架构

```
┌─────────────┐      ┌────────────────────────────────┐      ┌──────────────┐
│   OpenAI    │      │      FastAPI Server            │      │   Z.AI API   │
│   Client    │─────▶│                                │─────▶│   (GLM-4.x)  │
└─────────────┘      │  ┌──────────────────────────┐  │      └──────────────┘
                     │  │   Z.AI Provider          │  │
                     │  │   Request Transform      │  │
                     │  │   SSE / Tool Calls       │  │
                     │  └──────────────────────────┘  │
                     │                                │
                     │  ┌──────────────────────────┐  │
                     │  │   Web Admin Dashboard    │  │
                     │  │   (Token/Stats/Monitor)  │  │
                     │  └──────────────────────────┘  │
                     └────────────────────────────────┘
                               ↕
                          ┌─────────┐
                          │SQLite DB│
                          │(tokens) │
                          └─────────┘
```

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！请确保代码符合 PEP 8 规范。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ZyphrZero/z.ai2api_python&type=Date)](https://star-history.com/#ZyphrZero/z.ai2api_python&Date)

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

## ⚠️ 免责声明

- 本项目与 Z.AI 官方无关
- 使用前请确保遵守 Z.AI 的服务条款
- 请勿用于商业用途或违反使用条款的场景
- 项目仅供学习和研究使用
- 用户需自行承担使用风险

---

<div align="center">
Made with ❤️ by the community
</div>
