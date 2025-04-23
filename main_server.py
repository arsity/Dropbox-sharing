"""
Main entry point for the Dropbox-like server.
"""

import argparse
import signal
import sys

from server import FileServer


def parse_arguments():
    """
    Parse command line arguments.

    Returns:
        Namespace containing the parsed arguments
    """
    parser = argparse.ArgumentParser(description="Dropbox-like file server")
    parser.add_argument(
        "--host", type=str, default="localhost", help="Host to bind the server to"
    )
    parser.add_argument("--port", type=int, default=8888, help="Port to listen on")
    parser.add_argument(
        "--folder",
        type=str,
        default="server_shared_folder",
        help="Path to the shared folder",
    )

    return parser.parse_args()


def handle_signal(sig, frame):
    """
    Handle termination signals.
    """
    print("\nShutting down gracefully...")
    sys.exit(0)


def main():
    """
    Start the file server.
    """
    # Parse command line arguments
    args = parse_arguments()

    print(f"Starting server on {args.host}:{args.port}")
    print(f"Shared folder: {args.folder}")

    # Initialize the server
    server = FileServer(host=args.host, port=args.port, shared_folder=args.folder)

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Start the server
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
