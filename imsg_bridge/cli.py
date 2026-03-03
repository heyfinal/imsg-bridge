import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="imsg-bridge",
        description="Lightweight iMessage REST + WebSocket bridge",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5100, help="Bind port (default: 5100)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    uvicorn.run(
        "imsg_bridge.bridge:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
