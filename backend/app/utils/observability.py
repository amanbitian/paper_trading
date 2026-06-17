from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar


F = TypeVar("F", bound=Callable[..., Any])
logger = logging.getLogger("app.timing")


def _duration_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def timed(operation: str | None = None) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        name = operation or f"{func.__module__}.{func.__name__}"

        if hasattr(func, "__call__") and getattr(func, "__code__", None) is not None:
            is_coroutine = bool(func.__code__.co_flags & 0x80)
        else:
            is_coroutine = False

        if is_coroutine:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started_at = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    logger.info("operation=%s status=ok duration_ms=%.2f", name, _duration_ms(started_at))
                    return result
                except Exception:
                    logger.exception("operation=%s status=error duration_ms=%.2f", name, _duration_ms(started_at))
                    raise

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                logger.info("operation=%s status=ok duration_ms=%.2f", name, _duration_ms(started_at))
                return result
            except Exception:
                logger.exception("operation=%s status=error duration_ms=%.2f", name, _duration_ms(started_at))
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def timed_block(operation: str):
    started_at = time.perf_counter()
    try:
        yield
        logger.info("operation=%s status=ok duration_ms=%.2f", operation, _duration_ms(started_at))
    except Exception:
        logger.exception("operation=%s status=error duration_ms=%.2f", operation, _duration_ms(started_at))
        raise
