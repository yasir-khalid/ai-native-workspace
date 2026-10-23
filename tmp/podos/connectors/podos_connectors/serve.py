"""Run a connector MCP server.

    python -m podos_connectors.serve --connector edgar --mode mock --port 8421
    PODOS_SEC_USER_AGENT="Your Firm research-platform contact@yourfirm.example" \\
        python -m podos_connectors.serve --connector edgar --mode sec --port 8421
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from . import edgar

DEV_CONTROL_TOKEN = "dev-connector-control-token"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--connector", choices=["edgar"], default="edgar")
    p.add_argument("--mode", choices=["mock", "sec"], default="mock")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8421)
    a = p.parse_args()
    token = os.environ.get("PODOS_CONNECTOR_CONTROL_TOKEN", DEV_CONTROL_TOKEN if a.mode == "mock" else "")
    uvicorn.run(edgar.build_app(a.mode, token), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
