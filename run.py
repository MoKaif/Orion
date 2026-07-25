"""Orion entrypoint. Reads server config and launches the ASGI app."""
import uvicorn

from orion.core.config import config

if __name__ == "__main__":
    server = config.section("settings").get("server", {})
    uvicorn.run(
        "orion.core.api.app:app",
        host=server.get("host", "127.0.0.1"),
        port=server.get("port", 8000),
        reload=server.get("reload", False),
        log_level=server.get("log_level", "info"),
    )
