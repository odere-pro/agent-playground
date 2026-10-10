"""echo-openai-agents: the simplifier as an OpenAI Agents SDK agent. `handle` is the one thing
served."""

from echo_openai_agents.handle import PROMPT_VERSION, SYSTEM_PROMPT, handle

__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT", "handle"]
