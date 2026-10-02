"""The public interfaces: the FastAPI routers over `RunPipeline`, one module per interface.

The pure format mapping is under `chassis.adapters.*_compat`, which this package reaches without
importing a product SDK itself (PoC-3 open note, section 1). Shared here: `ids` (the id rule and
the re-mint), `hold` (the hold rule), `serve` (one call over an `InboundAdapter`), and `errors`
(a validation error in the route's format). `native` is `POST /v1/run`.

`mount_interfaces(app, config)` mounts native always and each other interface that
`spec.interfaces` leaves on, each route on the app's own router (FastAPI 0.141 analyzes an included
route a second time, and the SDK bodies make that cost). MCP comes last: its tool is generated
from the `/v1/run` operation of the app's spec, so `/v1/run` must be there. `/v1/run` is described
from the config (`native.describe_run`), and the MCP tool carries that description, with the
`spec.limits` ceilings. `limits.BodyLimit` caps every body on the app (`spec.limits`).
"""

from __future__ import annotations

from fastapi import FastAPI

from chassis.server.config import ChassisConfig
from chassis.server.interfaces.anthropic import mount_anthropic
from chassis.server.interfaces.errors import install_validation_errors
from chassis.server.interfaces.limits import install_body_limit
from chassis.server.interfaces.mcp import mount_agent_mcp
from chassis.server.interfaces.native import add_run_route, describe_run
from chassis.server.interfaces.openai import mount_openai
from chassis.server.pipeline import RunPipeline

__all__ = ["mount_interfaces"]


def mount_interfaces(app: FastAPI, config: ChassisConfig) -> None:
    """Mount native `/v1/run`, then the interfaces `config.spec.interfaces` turns on.
    `app.state.pipeline` must be set.
    """
    pipeline: RunPipeline = app.state.pipeline
    install_validation_errors(app)
    install_body_limit(app, config.spec.limits)
    summary, description = describe_run(config.agent.name, config.agent.version, config.spec.limits)
    add_run_route(app.router, pipeline, summary=summary, description=description)
    enabled = config.spec.interfaces
    if enabled.openai:
        mount_openai(app)
    if enabled.anthropic:
        mount_anthropic(app)
    if enabled.mcp:
        mount_agent_mcp(app, config)  # last: built from the /v1/run operation
