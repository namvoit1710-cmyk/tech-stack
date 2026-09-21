from dataclasses import dataclass


@dataclass
class ExecutionPolicy:
    supports_streaming: bool = False
    supports_human_in_the_loop: bool = False
