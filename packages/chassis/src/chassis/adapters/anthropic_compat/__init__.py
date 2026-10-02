"""The Anthropic Messages wire format, not a client: `inbound.AnthropicInbound` (the Anthropic
interface, `POST /v1/messages` on the public port, onto the canonical request and back) and
`types` (the official `anthropic` types it speaks, re-exported for `chassis.server`). The SDK is
imported only here.
"""

from chassis.adapters.anthropic_compat.inbound import AnthropicInbound

__all__ = ["AnthropicInbound"]
