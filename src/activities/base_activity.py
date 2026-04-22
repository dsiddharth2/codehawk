"""
Base Activity Class.

All activities inherit from this base class.
"""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar
from utils.logger import get_logger
from utils.url_sanitizer import sanitize_sensitive_data

TInput = TypeVar('TInput')
TOutput = TypeVar('TOutput')


class BaseActivity(ABC, Generic[TInput, TOutput]):
    """Base class for all activities."""

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)

    @abstractmethod
    def execute(self, input_data: TInput) -> TOutput:
        """Execute the activity."""
        pass

    def _log_start(self, **kwargs):
        self.logger.info(
            f"Starting {self.__class__.__name__}",
            extra={'extra_data': kwargs}
        )

    def _log_success(self, **kwargs):
        self.logger.info(
            f"Completed {self.__class__.__name__}",
            extra={'extra_data': kwargs}
        )

    def _log_error(self, error: Exception, **kwargs):
        sanitized_kwargs = sanitize_sensitive_data(kwargs)
        self.logger.exception(
            f"Failed {self.__class__.__name__}",
            extra={'extra_data': sanitized_kwargs}
        )
