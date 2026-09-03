import asyncio

import uvicorn

from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from worker_sdk.layer3_adapters.controllers.worker_headless import run_headless_worker

container = build_app_container()

if __name__ == "__main__":
    mode = settings.APP_MODE

    if mode == "SERVER":
        print(f"Mode: SERVER on {settings.SERVER_HOST}:{settings.SERVER_PORT}")
        app = create_worker_app(container)
        uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)

    elif mode == "HEADLESS":
        print("Mode: HEADLESS")
        asyncio.run(run_headless_worker(container))

    else:
        raise ValueError(f"Unknown APP_MODE: {mode}. Use SERVER or HEADLESS.")
