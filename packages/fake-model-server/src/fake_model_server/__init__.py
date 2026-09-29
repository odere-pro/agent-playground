"""Fake model server. Frameworks point their OpenAI base URL at it in tests. See CLAUDE.md."""

from fake_model_server.app import create_app
from fake_model_server.script import Rule, Script

__all__ = ["Rule", "Script", "create_app"]
