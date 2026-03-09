#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
热重载配置模块
定义 Granian 服务器热重载时需要忽略的目录和文件模式
"""

# 忽略的目录列表
RELOAD_IGNORE_DIRS = [
    "logs",  # 忽略日志目录
    "storage",  # 忽略存储目录
    "__pycache__",  # 忽略 Python 缓存
    ".git",  # 忽略 git 目录
    ".github",  # 忽略 GitHub 相关目录
    ".vscode",  # 忽略 VSCode 配置目录
    "deploy",  # 忽略部署相关目录
    ".idea",  # 忽略 IntelliJ IDEA 配置目录
    "node_modules",  # 忽略 node_modules
    "migrations",  # 忽略数据库迁移目录
    ".pytest_cache",  # 忽略 pytest 缓存
    ".venv",  # 忽略虚拟环境
    "venv",  # 忽略虚拟环境
    "env",  # 忽略环境目录
    ".mypy_cache",  # 忽略 mypy 缓存
    ".ruff_cache",  # 忽略 ruff 缓存
    "dist",  # 忽略构建分发目录
    "build",  # 忽略构建目录
    ".coverage",  # 忽略测试覆盖率文件
    "htmlcov",  # 忽略覆盖率报告目录
    "tests",  # 忽略测试目录
    "z-ai2api-server.pid",  # 忽略 PID 文件
    "app\\templates" # 忽略模板目录
]

# 忽略的文件模式（正则表达式）
RELOAD_IGNORE_PATTERNS = [
    # 日志文件
    r".*\.log$",
    r".*\.log\.\d+$",
    # 数据库文件
    r".*\.sqlite3.*",
    r".*\.db$",
    r".*\.db-.*$",
    # Python 相关
    r".*\.pyc$",
    r".*\.pyo$",
    r".*\.pyd$",
    # 临时文件
    r".*\.tmp$",
    r".*\.temp$",
    r".*\.swp$",
    r".*\.swo$",
    r".*~$",
    # 系统文件
    r".*\.DS_Store$",
    r".*Thumbs\.db$",
    r".*\.directory$",
    # 编辑器文件
    r".*\.vscode.*",
    r".*\.idea.*",
    # 测试和覆盖率
    r".*\.coverage$",
    r".*\.pytest_cache.*",
    # 构建文件
    r".*\.egg-info.*",
    r".*\.wheel$",
    r".*\.whl$",
    # 版本控制
    r".*\.git.*",
    r".*\.gitignore$",
    r".*\.gitkeep$",
    # 配置文件备份
    r".*\.bak$",
    r".*\.backup$",
    r".*\.orig$",
    # 锁文件
    r".*\.lock$",
    r".*\.pid$",
]

# 监视的路径（只监视应用相关代码）
RELOAD_WATCH_PATHS = [
    "app",  # 应用主目录
    "main.py",  # 主入口文件
]

# 热重载配置
RELOAD_CONFIG = {
    "reload_ignore_dirs": RELOAD_IGNORE_DIRS,
    "reload_ignore_patterns": RELOAD_IGNORE_PATTERNS,
    "reload_paths": RELOAD_WATCH_PATHS,
    "reload_tick": 500,  # 监视频率（毫秒）
}
