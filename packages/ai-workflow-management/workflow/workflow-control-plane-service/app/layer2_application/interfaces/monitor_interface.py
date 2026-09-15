from typing import Protocol


class IMonitor(Protocol):
    def track(self, metric: str, value: float) -> None: ...
    def increment(
        self, metric: str, tags: dict | None = None, amount: int = 1,
    ) -> None:
        """Add *amount* to a counter.

        ``amount`` exists so a caller that already knows it saw N events can
        say so. Without it the only way to record N was to put N in a **tag**,
        which makes the counter read 1 and spawns a fresh series per distinct
        N — measured on tenant-1 2026-08-05: six queues produced 19 series in
        five minutes and `rate()` on that metric was meaningless.
        """
        ...
    def histogram(
        self, metric: str, value: float, tags: dict | None = None,
    ) -> None:
        """Record a histogram observation (e.g., a duration in seconds).

        Tags are free-form key→value labels that the backend may turn into
        histogram labels / tags / dimensions. Implementations that do not
        support labels should ignore the tags argument but must not error.
        """
        ...
