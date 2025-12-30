import sys
from loguru import logger
import os

# 确保 logs 目录存在
if not os.path.exists("logs"):
    os.makedirs("logs")

# 配置 logger
logger.remove()  # 移除默认 handler
if sys.stderr:
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="7 days",
    level="DEBUG",
    encoding="utf-8"
)

def get_logger():
    return logger
