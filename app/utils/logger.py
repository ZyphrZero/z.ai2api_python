#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
from pathlib import Path
from loguru import logger
from typing import Optional

# Global logger instance
app_logger = None


def setup_logger(log_dir, log_retention_days=7, log_rotation="1 day", debug_mode=False, function_call_debug=False):
    """
    Create a logger instance with enhanced function call debugging

    Parameters:
        log_dir (str): 日志目录
        log_retention_days (int): 日志保留天数
        log_rotation (str): 日志轮转间隔
        debug_mode (bool): 是否开启调试模式
        function_call_debug (bool): 是否开启function call专用调试
    """
    global app_logger

    try:
        logger.remove()

        log_level = "DEBUG" if debug_mode else "INFO"

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # 基础控制台格式
        console_format = (
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
            if not debug_mode
            else "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>"
        )

        # Function call专用格式（更详细）
        if function_call_debug:
            console_format = (
                "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
                "<magenta>[FC]</magenta> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            )

        logger.add(sys.stderr, level=log_level, format=console_format, colorize=True)

        # 通用日志文件
        log_file = log_path / "{time:YYYY-MM-DD}.log"
        file_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"

        logger.add(
            str(log_file),
            level=log_level,
            format=file_format,
            rotation=log_rotation,
            retention=f"{log_retention_days} days",
            encoding="utf-8",
            compression="zip",
            enqueue=True,
            catch=True,
        )

        # Function call专用日志文件（如果启用）
        if function_call_debug:
            fc_log_file = log_path / "function_call_{time:YYYY-MM-DD}.log"
            fc_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | [FC] " "{name}:{function}:{line} | {message}"

            # 仅记录包含function call相关的日志
            def function_call_filter(record):
                message = record["message"].lower()
                return any(
                    keyword in message
                    for keyword in ["工具调用", "tool_call", "参数", "glm_block", "🔧", "🔍", "📝", "✅", "❌", "⚠️"]
                )

            logger.add(
                str(fc_log_file),
                level="DEBUG",
                format=fc_format,
                rotation=log_rotation,
                retention=f"{log_retention_days} days",
                encoding="utf-8",
                compression="zip",
                filter=function_call_filter,
                enqueue=True,
                catch=True,
            )

        app_logger = logger

        return logger

    except Exception as e:
        logger.remove()
        logger.add(sys.stderr, level="ERROR")
        logger.error(f"日志系统配置失败: {e}")
        raise


def get_logger(context: Optional[str] = None):
    """
    Get the logger instance with optional context

    Args:
        context: 上下文标识，用于区分不同模块的日志
    """
    global app_logger
    if app_logger is None:
        app_logger = logger
        logger.add(sys.stderr, level="INFO")

    if context:
        return logger.bind(context=context)
    return app_logger


def get_function_call_logger():
    """
    获取专用于function call调试的logger
    """
    return get_logger("function_call")


def log_function_call_phase(phase: str, data: dict, extra_info: str = ""):
    """
    记录function call处理阶段的详细日志

    Args:
        phase: 处理阶段 (tool_call, other, parsing, etc.)
        data: 相关数据
        extra_info: 额外信息
    """
    fc_logger = get_function_call_logger()

    # 截断过长的数据
    truncated_data = {}
    for key, value in data.items():
        if isinstance(value, str) and len(value) > 200:
            truncated_data[key] = value[:200] + "..."
        else:
            truncated_data[key] = value

    log_msg = f"📊 阶段[{phase.upper()}] {extra_info}"
    if truncated_data:
        log_msg += f" | 数据: {truncated_data}"

    fc_logger.debug(log_msg)


if __name__ == "__main__":
    """Test the logger"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            setup_logger(temp_dir, debug_mode=True)

            logger.debug("这是一条调试日志")
            logger.info("这是一条信息日志")
            logger.warning("这是一条警告日志")
            logger.error("这是一条错误日志")
            logger.critical("这是一条严重日志")

            try:
                1 / 0
            except ZeroDivisionError:
                logger.exception("发生了除零异常")

            print("✅ 日志测试完成")

            logger.remove()

        except Exception as e:
            print(f"❌ 日志测试失败: {e}")
            logger.remove()
            raise
