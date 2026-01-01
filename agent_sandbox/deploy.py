"""
Deployment composition for the full application.

This module serves as the **production deployment entry point** for the agent sandbox.
Use `modal deploy -m agent_sandbox.deploy` to deploy the complete application.

Why a separate deploy.py?
-------------------------
Modal's `App.include()` method enables composing multiple apps into a single
deployable unit. This pattern is documented in the Modal API reference:

    https://modal.com/docs/reference/modal.App#include

    "Include another App's objects in this one. Useful for splitting up
    Modal Apps across different self-contained files."

Currently, this file simply re-exports the main app from `app.py`. However,
the separation provides architectural benefits:

1. **Clear entry points**: Developers know `deploy.py` = production,
   `app.py` = development (`modal serve`).

2. **Future extensibility**: If the codebase grows to have multiple apps
   (e.g., separate sandbox, controller, and worker apps), this file becomes
   the composition layer without breaking the documented deployment command.

3. **Modular development**: Each sub-app can be developed and tested
   independently, then composed here for unified deployment.

Example (future multi-app composition):
---------------------------------------
    import modal
    from agent_sandbox.sandbox_app import sandbox_app
    from agent_sandbox.controller_app import controller_app

    app = modal.App("agent-sandbox")
    app.include(sandbox_app)
    app.include(controller_app)

For now, we simply re-export the single app:
"""

from agent_sandbox.app import app

__all__ = ["app"]
