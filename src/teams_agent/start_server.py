from os import environ
from typing import Optional, Callable
from aiohttp.web import Application, Request, Response, run_app

from microsoft_agents.hosting.core import AgentApplication, AgentAuthConfiguration
from microsoft_agents.hosting.aiohttp import (
    start_agent_process,
    jwt_authorization_middleware,
    CloudAdapter,
)

# Hook to allow external startup customization (e.g., scheduler init)
StartupHook = Optional[Callable[[Application, AgentApplication], None]]


#def start_server(
#    agent_application: AgentApplication,
#    auth_configuration: Optional[AgentAuthConfiguration] = None,
#    on_startup: StartupHook = None,
#):
#    async def entry_point(req: Request) -> Response:
#        agent: AgentApplication = req.app["agent_app"]
#        adapter: CloudAdapter = req.app["adapter"]
#        return await start_agent_process(req, agent, adapter)
#
#    app = Application(middlewares=[jwt_authorization_middleware])
#    adapter: CloudAdapter = agent_application.adapter
#    if auth_configuration is not None and getattr(adapter, "auth_configuration", None) is None:
#        try:
#            adapter.auth_configuration = auth_configuration
#        except Exception:
#            pass
#    app.router.add_post("/api/messages", entry_point)
#    app.router.add_get("/api/messages", lambda _: Response(status=200))
#    app["agent_configuration"] = auth_configuration
#    app["agent_app"] = agent_application
#    app["adapter"] = adapter
#
#    if on_startup:
#        on_startup(app, agent_application)
#
#    try:
#        run_app(app, host="localhost", port=int(environ.get("PORT", 3978)))
#    except Exception as error:
#        raise error

def start_server(
    agent_application: AgentApplication,
    on_startup: StartupHook = None,
):
    async def entry_point(req: Request) -> Response:
        agent: AgentApplication = req.app["agent_app"]
        adapter: CloudAdapter = req.app["adapter"]
        return await start_agent_process(req, agent, adapter)

    app = Application(middlewares=[jwt_authorization_middleware])
    adapter: CloudAdapter = agent_application.adapter
    
    # --- This entire block can now be removed ---
    #if auth_configuration is not None and getattr(adapter, "auth_configuration", None) is None:
    #    try:
    #        adapter.auth_configuration = auth_configuration
    #    except Exception:
    #        pass

    app.router.add_post("/api/messages", entry_point)
    app.router.add_get("/api/messages", lambda _: Response(status=200))

    # --- This line can also be removed ---
    #app["agent_configuration"] = auth_configuration
    
    app["agent_app"] = agent_application
    app["adapter"] = adapter

    if on_startup:
        on_startup(app, agent_application)

    try:
        run_app(app, host="localhost", port=int(environ.get("PORT", 3978)))
    except Exception as error:
        raise error
