"""Starts the Block Blast Bot dashboard.

    python main.py
"""
import argparse

from dashboard import server


def main():
    parser = argparse.ArgumentParser(description="Start the Block Blast Bot dashboard.")
    parser.add_argument("--port", type=int, default=server.DEFAULT_PORT,
                        help="port to serve on (moves to the next free one if taken)")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open a browser tab automatically")
    args = parser.parse_args()
    server.serve(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
