"""The OpenAI chat wire format, inbound and shared with the model proxy. A wire format, not a
client: `adapters/litellm` stays the only outbound model client. The `openai` SDK is imported only
under `chassis.adapters` (`make lint`).
"""
