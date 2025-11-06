"""
Deployment composition for the full application.

This module composes all component apps into a single deployable unit.
Use `modal deploy -m agent_sandbox.deploy` to deploy the complete application.

See Modal docs on App composition for details.
"""

from agent_sandbox.app import app

# Export the app for deployment
# If you split into multiple apps later, use:
# app = modal.App("full-app").include(sandbox_app).include(controller_app)

