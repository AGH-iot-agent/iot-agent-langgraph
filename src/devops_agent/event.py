from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass

class AgentEvent:
    kind: str         
    title: str        
    body: str = ""    
    context: dict[str, Any] = field(default_factory=dict) 

    def to_prompt(self) -> str:
        """Convert event to a natural language prompt for LangGraph."""
        return f"[{self.kind.upper()}] {self.title}\n\n{self.body}"
