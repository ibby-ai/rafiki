"""Tests for image builder resolution."""

from agent_sandbox.config.settings import Settings
from agent_sandbox.images import resolve_image_builder_name


def test_resolve_image_builder_default():
    settings = Settings()
    assert resolve_image_builder_name(settings) == settings.agent_provider


def test_resolve_image_builder_override():
    settings = Settings(agent_image_override="my-org/my-agent:latest")
    assert resolve_image_builder_name(settings) == "custom"


def test_resolve_image_builder_explicit_builder():
    settings = Settings(agent_image_builder="custom")
    assert resolve_image_builder_name(settings) == "custom"
