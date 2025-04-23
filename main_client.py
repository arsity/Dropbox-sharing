"""
Main entry point for the Dropbox-like client.
"""

import argparse
import signal
import sys

from client import FileClient


def parse_arguments():
    """
    Parse command line arguments.

    Returns:
        Namespace containing the parsed arguments
    """
    parser = argparse.ArgumentParser(description="Dropbox-like file client")
    parser.add_argument(
        "--host", type=str, default="localhost", help="Server host to connect to"
    )
    parser.add_argument(
        "--port", type=int, default=8888, help="Server port to connect to"
    )
    parser.add_argument(
        "--folder", type=str, default="client_folder", help="Path to the local folder"
    )
    parser.add_argument("--poll", type=int, default=1, help="Poll interval in seconds")

    return parser.parse_args()


def handle_signal(sig, frame):
    """
    Handle termination signals.
    """
    print("\nShutting down gracefully...")
    sys.exit(0)


def main():
    """
    Start the file client.
    """
    # Parse command line arguments
    args = parse_arguments()

    print(f"Starting client, connecting to {args.host}:{args.port}")
    print(f"Local folder: {args.folder}")
    print(f"Poll interval: {args.poll} seconds")

    # Initialize the client
    client = FileClient(
        server_host=args.host,
        server_port=args.port,
        local_folder=args.folder,
        poll_interval=args.poll,
    )

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Start the client
        client.start()

        # Keep the main thread alive while the client runs in background threads
        while True:
            # Sleep to avoid busy waiting
            import time

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        client.stop()


if __name__ == "__main__":
    main()
