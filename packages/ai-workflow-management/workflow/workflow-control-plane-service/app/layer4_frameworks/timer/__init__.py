"""Durable-timer framework wiring (SA-1371).

Holds the leader-election lease (``SingleOwnerLease``) and the poll-loop
``TimerDispatcher`` that appends ``TimerFired`` for due timers.
"""
