"""按应用配置初始化 Python 标准日志系统。"""

import logging

from app.core.config import settings


def configure_logging() -> None:
    """设置全局日志级别及包含时间、级别和模块名的输出格式。"""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
