import logging
import os


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    # Sin handlers propios ni propagate=False:
    # el runtime de Azure Functions captura los logs desde el root logger
    # y los envía al streaming y a Application Insights.
    return logger
