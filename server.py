"""
Core server logic for the Dropbox-like file sharing simulator.

Responsibilities:
- Listen for client connections.
- Manage the central shared folder.
- Handle file/folder operations (create, delete, update).
- Track changes and notify clients.
"""

import json
import logging
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import protocol

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("file_server")


class FileChangeTracker:
    """
    Track file changes to determine what updates to send to clients.
    """

    def __init__(self, shared_folder_path: str):
        """
        Initialize the change tracker.

        Args:
            shared_folder_path: Path to the shared folder
        """
        self.shared_folder_path = shared_folder_path
        # Map of relative path -> timestamp of last modification
        self.file_timestamps: Dict[str, float] = {}
        # Set of deleted files/folders (relative paths)
        self.deleted_items: Set[str] = set()
        # Initialize with current files
        self._initialize_timestamps()

    def _initialize_timestamps(self) -> None:
        """
        Initialize timestamps for all existing files.
        """
        if not os.path.exists(self.shared_folder_path):
            os.makedirs(self.shared_folder_path)
            return

        for root, dirs, files in os.walk(self.shared_folder_path):
            rel_root = os.path.relpath(root, self.shared_folder_path)

            # Add directories (excluding the root)
            if rel_root != ".":
                rel_path = rel_root
                self.file_timestamps[rel_path] = os.path.getmtime(root)

            # Add files
            for file in files:
                if rel_root == ".":
                    rel_path = file
                else:
                    rel_path = os.path.join(rel_root, file)

                abs_path = os.path.join(root, file)
                self.file_timestamps[rel_path] = os.path.getmtime(abs_path)

    def record_file_change(self, rel_path: str, timestamp: float) -> None:
        """
        Record a file creation or update.

        Args:
            rel_path: Relative path of the file/folder
            timestamp: Modification timestamp
        """
        self.file_timestamps[rel_path] = timestamp
        # If it was previously deleted, remove from deleted items
        if rel_path in self.deleted_items:
            self.deleted_items.remove(rel_path)

    def record_deletion(self, rel_path: str) -> None:
        """
        Record a file or folder deletion.

        Args:
            rel_path: Relative path of the deleted item
        """
        if rel_path in self.file_timestamps:
            del self.file_timestamps[rel_path]

        self.deleted_items.add(rel_path)

    def get_changes_since(self, last_sync: float) -> Dict[str, Any]:
        """
        Get changes that occurred since the last synchronization.

        Args:
            last_sync: Timestamp of last synchronization

        Returns:
            Dict with updated and deleted items
        """
        updates = {}
        for path, timestamp in self.file_timestamps.items():
            if timestamp > last_sync:
                updates[path] = timestamp

        # Filter deleted items that were deleted after last_sync
        # Since we don't track deletion time, we include all deletions
        # A more sophisticated implementation would track deletion timestamps

        return {"updated": updates, "deleted": list(self.deleted_items)}


class ClientHandler(threading.Thread):
    """
    Handle communication with a client.
    """

    def __init__(
        self,
        client_socket: socket.socket,
        client_address: Tuple[str, int],
        server: "FileServer",
    ):
        """
        Initialize the client handler.

        Args:
            client_socket: The socket for this client
            client_address: The client's address (host, port)
            server: The parent server instance
        """
        super().__init__()
        self.socket = client_socket
        self.address = client_address
        self.server = server
        self.running = True

    def run(self) -> None:
        """
        Handle client communication.
        """
        logger.info(f"Client connected: {self.address}")

        try:
            while self.running:
                # Receive and process messages
                try:
                    message = protocol.receive_message(self.socket)
                    self._handle_message(message)
                except (ConnectionError, ValueError) as e:
                    logger.error(f"Error receiving message: {e}")
                    break

        except Exception as e:
            logger.exception(f"Error handling client: {e}")
        finally:
            logger.info(f"Client disconnected: {self.address}")
            self.socket.close()
            self.server.remove_client(self)

    def _handle_message(self, message: Dict[str, Any]) -> None:
        """
        Process a message from the client.

        Args:
            message: The parsed message
        """
        action = message.get("action")
        payload = message.get("payload", {})

        logger.debug(f"Received {action} from {self.address}")

        try:
            if action == protocol.ACTION_CONNECT:
                self._handle_connect()
            elif action == protocol.ACTION_DISCONNECT:
                self._handle_disconnect()
            elif action == protocol.ACTION_CREATE_FILE:
                self._handle_create_file(payload)
            elif action == protocol.ACTION_UPDATE_FILE:
                self._handle_update_file(payload)
            elif action == protocol.ACTION_DELETE_FILE:
                self._handle_delete_file(payload)
            elif action == protocol.ACTION_CREATE_FOLDER:
                self._handle_create_folder(payload)
            elif action == protocol.ACTION_DELETE_FOLDER:
                self._handle_delete_folder(payload)
            elif action == protocol.ACTION_REQUEST_UPDATES:
                self._handle_request_updates(payload)
            elif action == protocol.ACTION_REQUEST_ALL_FILES:
                self._handle_request_all_files()
            else:
                logger.warning(f"Unknown action: {action}")
                self._send_error(f"Unknown action: {action}")
        except Exception as e:
            logger.exception(f"Error handling {action}: {e}")
            self._send_error(f"Error processing {action}: {str(e)}")

    def _handle_connect(self) -> None:
        """
        Handle client connection initialization.
        """
        self._send_ack()

    def _handle_disconnect(self) -> None:
        """
        Handle client disconnection.
        """
        self._send_ack()
        self.running = False

    def _handle_create_file(self, payload: Dict[str, Any]) -> None:
        """
        Handle file creation.

        Args:
            payload: Message payload with path, data and timestamp
        """
        path = payload.get("path")
        data = payload.get("data")
        timestamp = payload.get("timestamp")

        if not path or not isinstance(path, str):
            self._send_error("Missing or invalid path for file creation")
            return

        # Allow data to be empty (for empty files), but it must be a string if provided
        if data is not None and not isinstance(data, str):
            self._send_error("Invalid data for file creation")
            return

        if not timestamp or not isinstance(timestamp, (int, float)):
            self._send_error("Missing or invalid timestamp for file creation")
            return

        abs_path = os.path.join(self.server.shared_folder_path, path)

        try:
            # Create the file (empty string if data is None)
            if data is None:
                # Create an empty file
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "wb") as f:
                    pass  # Just create an empty file
            else:
                protocol.decode_file_data(data, abs_path)

            # Update the change tracker
            self.server.change_tracker.record_file_change(path, float(timestamp))

            # Send acknowledgment
            self._send_ack()

            # Notify other clients
            self.server.broadcast_file_update(path, abs_path, self)

        except Exception as e:
            logger.exception(f"Error creating file {path}: {e}")
            self._send_error(f"Error creating file: {str(e)}")

    def _handle_update_file(self, payload: Dict[str, Any]) -> None:
        """
        Handle file update.

        Args:
            payload: Message payload with path, data and timestamp
        """
        path = payload.get("path")
        data = payload.get("data")
        timestamp = payload.get("timestamp")

        if not path or not isinstance(path, str):
            self._send_error("Missing or invalid path for file update")
            return

        # Allow data to be empty (for empty files), but it must be a string if provided
        if data is not None and not isinstance(data, str):
            self._send_error("Invalid data for file update")
            return

        if not timestamp or not isinstance(timestamp, (int, float)):
            self._send_error("Missing or invalid timestamp for file update")
            return

        abs_path = os.path.join(self.server.shared_folder_path, path)

        try:
            # Update the file (empty string if data is None)
            if data is None:
                # Create an empty file
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "wb") as f:
                    pass  # Just create an empty file
            else:
                protocol.decode_file_data(data, abs_path)

            # Update the change tracker
            self.server.change_tracker.record_file_change(path, float(timestamp))

            # Send acknowledgment
            self._send_ack()

            # Notify other clients
            self.server.broadcast_file_update(path, abs_path, self)

        except Exception as e:
            logger.exception(f"Error updating file {path}: {e}")
            self._send_error(f"Error updating file: {str(e)}")

    def _handle_delete_file(self, payload: Dict[str, Any]) -> None:
        """
        Handle file deletion.

        Args:
            payload: Message payload with path
        """
        path = payload.get("path")

        if not path:
            self._send_error("Missing required fields for file deletion")
            return

        abs_path = os.path.join(self.server.shared_folder_path, path)

        try:
            # Delete the file if it exists
            if os.path.exists(abs_path) and os.path.isfile(abs_path):
                os.remove(abs_path)

            # Update the change tracker
            self.server.change_tracker.record_deletion(path)

            # Send acknowledgment
            self._send_ack()

            # Notify other clients
            self.server.broadcast_file_deletion(path, self)

        except Exception as e:
            logger.exception(f"Error deleting file {path}: {e}")
            self._send_error(f"Error deleting file: {str(e)}")

    def _handle_create_folder(self, payload: Dict[str, Any]) -> None:
        """
        Handle folder creation.

        Args:
            payload: Message payload with path and timestamp
        """
        path = payload.get("path")
        timestamp = payload.get("timestamp")

        if not path or not isinstance(path, str):
            self._send_error("Missing or invalid path for folder creation")
            return

        if not timestamp or not isinstance(timestamp, (int, float)):
            self._send_error("Missing or invalid timestamp for folder creation")
            return

        abs_path = os.path.join(self.server.shared_folder_path, path)

        try:
            # Create the folder if it doesn't exist
            if not os.path.exists(abs_path):
                os.makedirs(abs_path, exist_ok=True)

            # Update the change tracker
            self.server.change_tracker.record_file_change(path, float(timestamp))

            # Send acknowledgment
            self._send_ack()

            # Notify other clients
            self.server.broadcast_folder_creation(path, self)

        except Exception as e:
            logger.exception(f"Error creating folder {path}: {e}")
            self._send_error(f"Error creating folder: {str(e)}")

    def _handle_delete_folder(self, payload: Dict[str, Any]) -> None:
        """
        Handle folder deletion.

        Args:
            payload: Message payload with path
        """
        path = payload.get("path")

        if not path or not isinstance(path, str):
            self._send_error("Missing or invalid path for folder deletion")
            return

        abs_path = os.path.join(self.server.shared_folder_path, path)

        try:
            # Delete the folder if it exists
            if os.path.exists(abs_path) and os.path.isdir(abs_path):
                # Collect all affected paths (folder and all contents)
                deleted_paths = []

                # Walk the directory from the bottom up (to delete contents first)
                for root, dirs, files in os.walk(abs_path, topdown=False):
                    # Get path relative to shared folder for each file/dir
                    rel_root = os.path.relpath(root, self.server.shared_folder_path)

                    # Add files to deleted list
                    for file in files:
                        rel_path = os.path.join(rel_root, file)
                        deleted_paths.append(rel_path)

                        # Remove the file
                        os.remove(os.path.join(root, file))

                        # Update the change tracker
                        self.server.change_tracker.record_deletion(rel_path)

                    # Add this directory to deleted list
                    if rel_root != ".":
                        deleted_paths.append(rel_root)

                    # Remove the directory
                    os.rmdir(root)

                    # Update the change tracker for this directory
                    if rel_root != ".":
                        self.server.change_tracker.record_deletion(rel_root)

                # Send acknowledgment
                self._send_ack()

                # Notify other clients about each deletion
                for deleted_path in deleted_paths:
                    if os.path.splitext(deleted_path)[
                        1
                    ]:  # Has extension, likely a file
                        self.server.broadcast_file_deletion(deleted_path, self)
                    else:
                        self.server.broadcast_folder_deletion(deleted_path, self)

                logger.info(
                    f"Deleted folder {path} and all its contents ({len(deleted_paths)} items)"
                )
            else:
                # Folder doesn't exist or isn't a directory
                logger.warning(f"Attempted to delete non-existent folder: {path}")
                self._send_ack()

        except Exception as e:
            logger.exception(f"Error deleting folder {path}: {e}")
            self._send_error(f"Error deleting folder: {str(e)}")

    def _handle_request_updates(self, payload: Dict[str, Any]) -> None:
        """
        Handle request for updates.

        Args:
            payload: Message payload with last_sync timestamp
        """
        last_sync = payload.get("last_sync", 0)

        try:
            # Get changes since last sync
            changes = self.server.change_tracker.get_changes_since(last_sync)

            # Send response with changes
            response = protocol.create_message(protocol.ACTION_SEND_UPDATES, changes)
            protocol.send_message(self.socket, response)

        except Exception as e:
            logger.exception(f"Error handling request for updates: {e}")
            self._send_error(f"Error getting updates: {str(e)}")

    def _handle_request_all_files(self) -> None:
        """
        Handle request for all files from a client.
        This is used for initial sync and when a client comes back online.
        """
        try:
            # Record the time before scanning
            sync_start_time = time.time()

            # Create a response with all files and folders
            file_list = []
            folder_list = []

            # Walk the shared directory and collect all files and folders
            for root, dirs, files in os.walk(self.server.shared_folder_path):
                # Get path relative to shared folder
                rel_root = os.path.relpath(root, self.server.shared_folder_path)

                # Skip the root directory itself
                if rel_root != ".":
                    # Add this directory
                    folder_list.append(rel_root)

                # Add all files in this directory
                for file in files:
                    if rel_root == ".":
                        rel_path = file
                    else:
                        rel_path = os.path.join(rel_root, file)

                    abs_path = os.path.join(root, file)
                    file_list.append(
                        {
                            "path": rel_path,
                            "data": protocol.encode_file_data(abs_path),
                            "timestamp": os.path.getmtime(abs_path),
                        }
                    )

            # Send the response including the sync start time
            response = protocol.create_message(
                protocol.ACTION_SEND_ALL_FILES,
                {
                    "files": file_list,
                    "folders": folder_list,
                    "sync_start_time": sync_start_time,
                },
            )
            protocol.send_message(self.socket, response)

            logger.info(
                f"Sent all files to client {self.address} ({len(file_list)} files, {len(folder_list)} folders) as of {sync_start_time}"
            )

        except Exception as e:
            logger.exception(f"Error handling request for all files: {e}")
            self._send_error(f"Error getting all files: {str(e)}")

    def _send_ack(self, message: str = "OK") -> None:
        """
        Send an acknowledgment message.

        Args:
            message: Status message
        """
        ack = protocol.create_ack_message(protocol.STATUS_OK, message)
        protocol.send_message(self.socket, ack)

    def _send_error(self, error_message: str) -> None:
        """
        Send an error message.

        Args:
            error_message: Error description
        """
        error = protocol.create_error_message(error_message)
        protocol.send_message(self.socket, error)

    def send_file_update(self, rel_path: str, file_path: str) -> None:
        """
        Send a file update to this client.

        Args:
            rel_path: Relative path of the file
            file_path: Absolute path to the file
        """
        try:
            message = protocol.create_update_file_message(rel_path, file_path)
            protocol.send_message(self.socket, message)
        except Exception as e:
            logger.exception(f"Error sending file update to {self.address}: {e}")

    def send_file_deletion(self, rel_path: str) -> None:
        """
        Send a file deletion notification to this client.

        Args:
            rel_path: Relative path of the file
        """
        try:
            message = protocol.create_delete_file_message(rel_path)
            protocol.send_message(self.socket, message)
        except Exception as e:
            logger.exception(f"Error sending file deletion to {self.address}: {e}")

    def send_folder_creation(self, rel_path: str) -> None:
        """
        Send a folder creation notification to this client.

        Args:
            rel_path: Relative path of the folder
        """
        try:
            message = protocol.create_folder_message(rel_path)
            protocol.send_message(self.socket, message)
        except Exception as e:
            logger.exception(f"Error sending folder creation to {self.address}: {e}")

    def send_folder_deletion(self, rel_path: str) -> None:
        """
        Send a folder deletion notification to this client.

        Args:
            rel_path: Relative path of the folder
        """
        try:
            message = protocol.create_delete_folder_message(rel_path)
            protocol.send_message(self.socket, message)
        except Exception as e:
            logger.exception(f"Error sending folder deletion to {self.address}: {e}")


class FileServer:
    """
    Server for file sharing.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8888,
        shared_folder: str = "shared_folder",
    ):
        """
        Initialize the file server.

        Args:
            host: Host address to bind to
            port: Port to listen on
            shared_folder: Path to the folder for shared files
        """
        self.host = host
        self.port = port
        self.shared_folder_path = os.path.abspath(shared_folder)

        # Ensure the shared folder exists
        os.makedirs(self.shared_folder_path, exist_ok=True)

        # Socket for accepting connections
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Track changes to files
        self.change_tracker = FileChangeTracker(self.shared_folder_path)

        # List of connected clients
        self.clients: List[ClientHandler] = []

        # Lock for thread-safe access to the clients list
        self.clients_lock = threading.Lock()

        # Flag to control the server loop
        self.running = False

    def start(self) -> None:
        """
        Start the server.
        """
        try:
            # Bind and listen
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(8)

            logger.info(f"Server started on {self.host}:{self.port}")
            logger.info(f"Shared folder: {self.shared_folder_path}")

            self.running = True

            # Accept clients in the main thread
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    self._handle_new_client(client_socket, client_address)
                except OSError:
                    # Socket was closed during accept
                    break
                except Exception as e:
                    logger.exception(f"Error accepting client: {e}")

        except Exception as e:
            logger.exception(f"Error starting server: {e}")
        finally:
            self.stop()

    def stop(self) -> None:
        """
        Stop the server and disconnect all clients.
        """
        logger.info("Stopping server...")

        self.running = False

        # Close all client connections
        with self.clients_lock:
            for client in self.clients[:]:
                try:
                    client.running = False
                    client.socket.close()
                except Exception:
                    pass
            self.clients.clear()

        # Close the server socket
        try:
            self.server_socket.close()
        except Exception:
            pass

        logger.info("Server stopped")

    def _handle_new_client(
        self, client_socket: socket.socket, client_address: Tuple[str, int]
    ) -> None:
        """
        Handle a new client connection.

        Args:
            client_socket: The client's socket
            client_address: The client's address (host, port)
        """
        client_handler = ClientHandler(client_socket, client_address, self)

        with self.clients_lock:
            self.clients.append(client_handler)

        client_handler.start()

    def remove_client(self, client: ClientHandler) -> None:
        """
        Remove a client from the active clients list.

        Args:
            client: The client to remove
        """
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)

    def broadcast_file_update(
        self,
        rel_path: str,
        file_path: str,
        exclude_client: Optional[ClientHandler] = None,
    ) -> None:
        """
        Broadcast a file update to all clients except the one that sent it.

        Args:
            rel_path: Relative path of the file
            file_path: Absolute path to the file
            exclude_client: Client to exclude from the broadcast
        """
        with self.clients_lock:
            for client in self.clients:
                if client != exclude_client:
                    try:
                        # Send the file update
                        message = protocol.create_update_file_message(
                            rel_path, file_path
                        )
                        protocol.send_message(client.socket, message)
                        logger.debug(
                            f"Broadcasted file update for {rel_path} to {client.address}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error broadcasting file update to {client.address}: {e}"
                        )

    def broadcast_file_deletion(
        self, rel_path: str, exclude_client: Optional[ClientHandler] = None
    ) -> None:
        """
        Broadcast a file deletion to all clients except the one that sent it.

        Args:
            rel_path: Relative path of the file
            exclude_client: Client to exclude from the broadcast
        """
        with self.clients_lock:
            for client in self.clients:
                if client != exclude_client:
                    try:
                        # Send the file deletion
                        message = protocol.create_delete_file_message(rel_path)
                        protocol.send_message(client.socket, message)
                        logger.debug(
                            f"Broadcasted file deletion for {rel_path} to {client.address}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error broadcasting file deletion to {client.address}: {e}"
                        )

    def broadcast_folder_creation(
        self, rel_path: str, exclude_client: Optional[ClientHandler] = None
    ) -> None:
        """
        Broadcast a folder creation to all clients except the one that sent it.

        Args:
            rel_path: Relative path of the folder
            exclude_client: Client to exclude from the broadcast
        """
        with self.clients_lock:
            for client in self.clients:
                if client != exclude_client:
                    try:
                        # Send the folder creation
                        message = protocol.create_folder_message(rel_path)
                        protocol.send_message(client.socket, message)
                        logger.debug(
                            f"Broadcasted folder creation for {rel_path} to {client.address}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error broadcasting folder creation to {client.address}: {e}"
                        )

    def broadcast_folder_deletion(
        self, rel_path: str, exclude_client: Optional[ClientHandler] = None
    ) -> None:
        """
        Broadcast a folder deletion to all clients except the one that sent it.

        Args:
            rel_path: Relative path of the folder
            exclude_client: Client to exclude from the broadcast
        """
        with self.clients_lock:
            for client in self.clients:
                if client != exclude_client:
                    try:
                        # Send the folder deletion
                        message = protocol.create_delete_folder_message(rel_path)
                        protocol.send_message(client.socket, message)
                        logger.debug(
                            f"Broadcasted folder deletion for {rel_path} to {client.address}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error broadcasting folder deletion to {client.address}: {e}"
                        )
