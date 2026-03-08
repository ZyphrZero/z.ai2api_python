"""Token 数据库模型定义。"""

from app.core.config import settings

SQL_CREATE_TABLES = """
-- Token 配置表
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,              -- 提供商: zai
    token TEXT NOT NULL UNIQUE,          -- Token 值（唯一）
    token_type TEXT DEFAULT 'user',      -- Token 类型: user, guest, unknown
    is_enabled BOOLEAN DEFAULT 1,        -- 是否启用
    priority INTEGER DEFAULT 0,          -- 优先级（用于排序）
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, token)              -- 同一提供商内 Token 唯一
);

-- Token 使用统计表
CREATE TABLE IF NOT EXISTS token_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    total_requests INTEGER DEFAULT 0,
    successful_requests INTEGER DEFAULT 0,
    failed_requests INTEGER DEFAULT 0,
    last_success_time DATETIME,
    last_failure_time DATETIME,
    FOREIGN KEY (token_id) REFERENCES tokens(id) ON DELETE CASCADE
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_tokens_provider ON tokens(provider);
CREATE INDEX IF NOT EXISTS idx_tokens_enabled ON tokens(is_enabled);
CREATE INDEX IF NOT EXISTS idx_token_stats_token_id ON token_stats(token_id);

-- 触发器：自动更新 updated_at
CREATE TRIGGER IF NOT EXISTS update_tokens_timestamp
AFTER UPDATE ON tokens
BEGIN
    UPDATE tokens SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""

DB_PATH = settings.DB_PATH
