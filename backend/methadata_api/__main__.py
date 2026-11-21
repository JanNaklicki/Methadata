"""Samodzielny start: python -m methadata_api  (domyślnie 127.0.0.1:8000)."""
import os

import uvicorn

from .app import create_app


def main() -> None:
    uvicorn.run(create_app(), host=os.environ.get("METHADATA_HOST", "127.0.0.1"),
                port=int(os.environ.get("METHADATA_PORT", "8000")), log_level=os.environ.get("METHADATA_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
