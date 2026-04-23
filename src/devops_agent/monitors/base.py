from __future__ import annotations
from abc import ABC, abstractmethod
from devops_agent.event import AgentEvent

class BaseMonitor(ABC):
    """
    A monitor is responsible for detecting new events of a specific kind.
    It must be stateless between instantiations — store deduplication state
    as instance attributes initialized in __init__.
    """

    @abstractmethod
    def poll(self) -> list[AgentEvent]:
        """
        Check for new events. Return a list of AgentEvent objects.
        Must never raise — catch all exceptions internally and log them.
        Must deduplicate — never return the same event twice.
        """
        ...
