import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

DeferredFactory = Callable[[], Awaitable[None]]
_deferred_factories: ContextVar[list[DeferredFactory] | None] = ContextVar(
    "slack_deferred_factories",
    default=None,
)


@contextmanager
def capture_deferred_work() -> Iterator[list[DeferredFactory]]:
    """Collect work that must run after Slack receives its HTTP response."""

    factories: list[DeferredFactory] = []
    token = _deferred_factories.set(factories)
    try:
        yield factories
    finally:
        _deferred_factories.reset(token)


async def defer(factory: DeferredFactory) -> None:
    """Queue work in HTTP handling, but execute it inline in direct unit tests."""

    factories = _deferred_factories.get()
    if factories is None:
        await factory()
        return
    factories.append(factory)


async def run_deferred_work(factories: list[DeferredFactory]) -> None:
    for factory in factories:
        try:
            await factory()
        except Exception:
            logger.exception("Deferred Slack work failed")
