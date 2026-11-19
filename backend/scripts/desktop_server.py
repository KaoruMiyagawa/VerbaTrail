"""Serve the built desktop UI and existing API from one loopback origin."""

import os
from pathlib import Path
import sys
from threading import Thread

from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
import uvicorn

from app.main import app


class DesktopFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            headers = dict(scope["headers"])
            if (
                error.status_code != 404
                or Path(path).suffix
                or path.partition(os.sep)[0] == "api"
                or b"text/html" not in headers.get(b"accept", b"")
            ):
                raise
            response = await super().get_response("index.html", scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


class DesktopServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            print("VERBATRAIL_DESKTOP_READY", flush=True)


def main():
    ui_path, port = sys.argv[1:]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.mount("/", DesktopFiles(directory=ui_path, html=True))
    server = DesktopServer(uvicorn.Config(app, host="127.0.0.1", port=int(port)))

    def wait_for_desktop_exit():
        os.read(sys.stdin.fileno(), 1)
        server.should_exit = True

    Thread(target=wait_for_desktop_exit, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
