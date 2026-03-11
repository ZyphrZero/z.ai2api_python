# z-ai2api_python

基于 FastAPI + Granian 的 GLM 代理服务
适合本地开发、自托管代理、Token 池管理和兼容客户端接入

[English Version](english/README.md)

## 特性

- 兼容 `OpenAI`、`Claude Code`、`Anthropic` 风格请求
- 支持流式响应、工具调用、Thinking 模型
- 内置 Token 池，支持轮询、失败熔断、恢复和健康检查
- 提供后台页面：仪表盘、Token 管理、配置管理、实时日志
- 使用 SQLite 存储 Token 和请求日志，部署简单
- 支持本地运行和 Docker / Docker Compose 部署

## 快速开始

### 环境要求

- Python `3.9` 到 `3.12`
- 推荐使用 `uv`

### 本地启动

```bash
git clone https://github.com/ZyphrZero/z.ai2api_python.git
cd z.ai2api_python

uv sync
cp .env.example .env
uv run python main.py
```

首次启动会自动初始化数据库。

默认地址：

- API 根路径：`http://127.0.0.1:8080`
- OpenAI 文档：`http://127.0.0.1:8080/docs`
- 管理后台：`http://127.0.0.1:8080/admin`

### Docker Compose

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

更多部署说明见 [deploy/README_DOCKER.md](deploy/README_DOCKER.md)。

## 最小配置

至少建议确认这些环境变量：

| 变量 | 说明 |
| --- | --- |
| `AUTH_TOKEN` | 客户端访问本服务使用的 Bearer Token |
| `ADMIN_PASSWORD` | 管理后台登录密码，默认值必须修改 |
| `LISTEN_PORT` | 服务监听端口，默认 `8080` |
| `ANONYMOUS_MODE` | 是否启用匿名模式 |
| `GUEST_POOL_SIZE` | 匿名池容量 |
| `DB_PATH` | SQLite 数据库路径 |
| `TOKEN_FAILURE_THRESHOLD` | Token 连续失败阈值 |
| `TOKEN_RECOVERY_TIMEOUT` | Token 恢复等待时间 |

完整配置请看 [.env.example](.env.example)。

## 管理后台

管理后台统一入口：

- `/admin`：仪表盘
- `/admin/tokens`：Token 管理
- `/admin/config`：配置管理
- `/admin/logs`：实时日志

## 常用命令

```bash
# 启动服务
uv run python main.py

# 运行测试
uv run pytest

# 运行一个现有 smoke test
uv run python tests/test_simple_signature.py

# Lint
uv run ruff check app tests main.py
```

## 兼容接口

常见接口入口：

- OpenAI 兼容：`/v1/chat/completions`
- Anthropic 兼容：`/v1/messages`
- Claude Code 兼容：`/anthropic/v1/messages`

模型映射和默认模型可在 `.env` 或后台配置页中调整。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ZyphrZero/z.ai2api_python&type=Date)](https://star-history.com/#ZyphrZero/z.ai2api_python&Date)

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

## 免责声明

- **本项目仅供学习和研究使用，切勿用于其他用途**
- 本项目与 Z.AI 官方无关
- 使用前请确保遵守 Z.AI 的服务条款
- 请勿用于商业用途或违反使用条款的场景
- 用户需自行承担使用风险

---

<div align="center">
Made with ❤️ by the community
</div>
