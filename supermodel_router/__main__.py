"""supermodel_router/__main__.py — python -m supermodel_router entry point"""
from .app import app
from .config import config

if __name__ == "__main__":
    import uvicorn
    host = config.server.get("host", "0.0.0.0")
    port = config.server.get("port", 5678)
    uvicorn.run(app, host=host, port=port)
