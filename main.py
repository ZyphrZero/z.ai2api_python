#!/usr/bin/env python
# -*- coding: utf-8 -*-

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core import openai
from app.utils.reload_config import RELOAD_CONFIG
from app.utils.logger import setup_logger

from granian import Granian


# Setup logger
logger = setup_logger(log_dir="logs", debug_mode=settings.DEBUG_LOGGING, function_call_debug=True)

# Create FastAPI app
app = FastAPI(
    title="OpenAI Compatible API Server",
    description="An OpenAI-compatible API server for Z.AI chat service",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Include API routers
app.include_router(openai.router)


@app.options("/")
async def handle_options():
    """Handle OPTIONS requests"""
    return Response(status_code=200)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "OpenAI Compatible API Server"}


def run_server():
    Granian(
        "main:app",
        interface="asgi",
        address="0.0.0.0",
        port=settings.LISTEN_PORT,
        reload=False,  # 生产环境请关闭热重载
        **RELOAD_CONFIG,
    ).serve()


if __name__ == "__main__":
    run_server()
