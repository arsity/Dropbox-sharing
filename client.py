"""
Core client logic for the Dropbox-like file sharing simulator.

Responsibilities:
- Connect to the server.
- Manage the local replica of the shared folder.
- Monitor local changes using watchdog.
- Send local changes to the server.
- Fetch and apply remote changes from the server.
"""

import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import protocol

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("file_client")


class FileSystemChangeHandler(FileSystemEventHandler):
    """
    Handle file system events for the client's local folder.
    """

    def __init__(self, client: "FileClient"):
        """
        Initialize the handler.

        Args:
            client: The client instance
        """
        self.client = client
        self._ignore_next_events: Set[str] = set()

    def ignore_next_event_for(self, path: str) -> None:
        """
        Ignore the next event for the given path.
        This is used to avoid processing events that we caused ourselves.

        Args:
            path: The path to ignore
        """
        self._ignore_next_events.add(path)

    def on_created(self, event: FileSystemEvent) -> None:
        """
        Handle file/directory creation.

        Args:
            event: The file system event
        """
        src_path = self._ensure_str(event.src_path)

        if src_path in self._ignore_next_events:
            self._ignore_next_events.remove(src_path)
            return

        # Don't process temporary files (e.g., editor temp files)
        if self._is_temp_file(src_path):
            return

        # Handle the creation
        self.client.handle_local_creation(src_path, event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """
        Handle file/directory deletion.

        Args:
            event: The file system event
        """
        src_path = self._ensure_str(event.src_path)

        if src_path in self._ignore_next_events:
            self._ignore_next_events.remove(src_path)
            return

        # Don't process temporary files
        if self._is_temp_file(src_path):
            return

        # Handle the deletion
        self.client.handle_local_deletion(src_path, event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        """
        Handle file modification.

        Args:
            event: The file system event
        """
        src_path = self._ensure_str(event.src_path)

        if src_path in self._ignore_next_events:
            self._ignore_next_events.remove(src_path)
            return

        # Only handle file modifications (not directory modifications)
        if not event.is_directory:
            # Don't process temporary files
            if self._is_temp_file(src_path):
                return

            # Handle the modification
            self.client.handle_local_modification(src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        """
        Handle file/directory move/rename.

        Args:
            event: The file system event
        """
        src_path = self._ensure_str(event.src_path)
        dest_path = self._ensure_str(event.dest_path)

        if src_path in self._ignore_next_events:
            self._ignore_next_events.remove(src_path)
            return

        # For simplicity, treat moves as delete+create
        # This could be optimized in a full implementation
        self.client.handle_local_deletion(src_path, event.is_directory)
        self.client.handle_local_creation(dest_path, event.is_directory)

    def _is_temp_file(self, path: str) -> bool:
        """
        Check if a file is a temporary file.

        Args:
            path: The file path

        Returns:
            bool: True if the file is temporary
        """
        # Common temporary file patterns
        temp_patterns = [
            "~$",
            ".swp",
            ".tmp",
            ".temp",
            ".bak",
            "._",
            ".DS_Store",
            "Thumbs.db",
        ]

        file_name = os.path.basename(path)
        return any(
            file_name.endswith(pattern) or file_name.startswith(pattern)
            for pattern in temp_patterns
        )

    def _ensure_str(self, path: Union[str, bytes]) -> str:
        """
        Ensure that the path is a string.

        Args:
            path: The path (string or bytes)

        Returns:
            str: The path as a string
        """
        if isinstance(path, bytes):
            return path.decode("utf-8")
        elif isinstance(path, bytearray):
            return path.decode("utf-8")
        elif isinstance(path, memoryview):
            return bytes(path).decode("utf-8")
        return str(path)


class FileClient:
    """
    Client for the Dropbox-like file sharing service.
    """

    def __init__(
        self,
        server_host: str,
        server_port: int,
        local_folder: str,
        poll_interval: int = 5,
    ):
        """
        Initialize the file client.

        Args:
            server_host: Server hostname or IP
            server_port: Server port
            local_folder: Path to the local replica folder
            poll_interval: How often to poll the server for updates (in seconds)
        """
        self.server_host = server_host
        self.server_port = server_port
        self.local_folder_path = os.path.abspath(local_folder)
        self.poll_interval = poll_interval

        # Ensure the local folder exists
        os.makedirs(self.local_folder_path, exist_ok=True)

        # Socket for communicating with the server
        self.socket = None

        # Last synchronization timestamp
        self.last_sync_time = 0

        # File system event handler
        self.event_handler = FileSystemChangeHandler(self)

        # File system observer
        self.observer = Observer()

        # Synchronization lock
        self.sync_lock = threading.Lock()

        # Flag to control client loop
        self.running = False

        # Thread for polling the server
        self.poll_thread = threading.Thread(target=self._poll_server_thread)

    def start(self) -> None:
        """
        Start the client.
        """
        try:
            # Connect to the server
            self._connect_to_server()

            # Initialize synchronization time
            self.last_sync_time = time.time()

            # Perform initial full synchronization
            self._full_sync_with_server()

            # Start the file system observer
            self.observer.schedule(
                self.event_handler, self.local_folder_path, recursive=True
            )
            self.observer.start()

            logger.info(f"Watching for changes in {self.local_folder_path}")

            # Start polling thread
            self.running = True
            self.poll_thread.start()

            logger.info("Client started")

        except Exception as e:
            logger.exception(f"Error starting client: {e}")
            self.stop()

    def stop(self) -> None:
        """
        Stop the client.
        """
        logger.info("Stopping client...")

        self.running = False

        # Stop the observer
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()

        # Wait for the polling thread
        if self.poll_thread.is_alive():
            self.poll_thread.join(timeout=1)

        # Disconnect from the server
        self._disconnect_from_server()

        logger.info("Client stopped")

    def _connect_to_server(self) -> None:
        """
        Connect to the server.
        """
        try:
            # Create a socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Connect to the server
            self.socket.connect((self.server_host, self.server_port))

            # Send connect message
            connect_msg = protocol.create_message(protocol.ACTION_CONNECT)
            protocol.send_message(self.socket, connect_msg)

            # Wait for acknowledgment
            response = protocol.receive_message(self.socket)
            if response.get("action") != protocol.ACTION_ACK:
                raise ConnectionError("Failed to connect to server")

            logger.info(f"Connected to server at {self.server_host}:{self.server_port}")

        except Exception as e:
            logger.exception(f"Error connecting to server: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            raise

    def _disconnect_from_server(self) -> None:
        """
        Disconnect from the server.
        """
        if self.socket:
            try:
                # Send disconnect message
                disconnect_msg = protocol.create_message(protocol.ACTION_DISCONNECT)
                protocol.send_message(self.socket, disconnect_msg)

                # Wait for acknowledgment (with a timeout)
                self.socket.settimeout(2)
                try:
                    protocol.receive_message(self.socket)
                except:
                    pass

            except Exception as e:
                logger.exception(f"Error disconnecting from server: {e}")
            finally:
                self.socket.close()
                self.socket = None

    def _poll_server_thread(self) -> None:
        """
        Thread function for periodically polling the server for updates.
        """
        while self.running:
            try:
                # First, check if there are any pending messages (non-blocking)
                if self.socket and self._socket_has_data():
                    with self.sync_lock:
                        self._process_incoming_message()

                time.sleep(self.poll_interval)

                if not self.running:
                    break

                with self.sync_lock:
                    if self.socket:
                        self._sync_with_server()
                    else:
                        # Try to reconnect
                        try:
                            self._connect_to_server()
                            # If reconnection successful, do a full sync
                            self._full_sync_with_server()
                        except Exception as e:
                            logger.error(f"Failed to reconnect: {e}")

            except Exception as e:
                logger.exception(f"Error in polling thread: {e}")
                # If we lost connection, set socket to None
                if isinstance(e, (ConnectionError, OSError)):
                    self.socket = None

    def _socket_has_data(self) -> bool:
        """
        Check if there is data available to read from the socket (non-blocking).

        Returns:
            bool: True if there is data to read
        """
        if not self.socket:
            return False

        try:
            import select

            readable, _, _ = select.select([self.socket], [], [], 0)
            return bool(readable)
        except Exception:
            return False

    def _process_incoming_message(self) -> None:
        """
        Process a single message from the server.
        """
        if not self.socket:
            return

        try:
            # Receive the message
            message = protocol.receive_message(self.socket)
            action = message.get("action")
            payload = message.get("payload", {})

            logger.debug(f"Received message: {action}")

            # Process based on action type
            if (
                action == protocol.ACTION_CREATE_FILE
                or action == protocol.ACTION_UPDATE_FILE
            ):
                # File creation or update
                path = payload.get("path")
                data = payload.get("data")

                if path:
                    abs_path = os.path.join(self.local_folder_path, path)

                    # Tell the event handler to ignore this event
                    self.event_handler.ignore_next_event_for(abs_path)

                    # Ensure parent directory exists
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

                    # Create/update the file
                    protocol.decode_file_data(data, abs_path)
                    logger.info(f"Received file update: {path}")

            elif action == protocol.ACTION_DELETE_FILE:
                # File deletion
                path = payload.get("path")
                if path:
                    self._apply_remote_deletion(path)

            elif action == protocol.ACTION_CREATE_FOLDER:
                # Folder creation
                path = payload.get("path")
                if path:
                    self._apply_remote_folder_creation(path)

            elif action == protocol.ACTION_DELETE_FOLDER:
                # Folder deletion
                path = payload.get("path")
                if path:
                    self._apply_remote_deletion(path)

            elif action == protocol.ACTION_SEND_UPDATES:
                # Updates list
                self._process_updates(payload)

            elif action == protocol.ACTION_ERROR:
                # Error from server
                error_message = payload.get("message", "Unknown error")
                logger.error(f"Server error: {error_message}")

        except Exception as e:
            logger.exception(f"Error processing incoming message: {e}")

    def _sync_with_server(self) -> None:
        """
        Synchronize with the server.
        """
        if not self.socket:
            return

        try:
            # Request updates since last sync
            self._request_updates()

            # Update sync time
            self.last_sync_time = time.time()

        except Exception as e:
            logger.exception(f"Error synchronizing with server: {e}")

    def _request_updates(self) -> None:
        """
        Request updates from the server.
        """
        # Create and send the request
        request = protocol.create_request_updates_message(self.last_sync_time)
        protocol.send_message(self.socket, request)

        # Wait for response
        response = protocol.receive_message(self.socket)
        action = response.get("action")

        # Process the response based on action type
        if action == protocol.ACTION_SEND_UPDATES:
            # Standard update response with a list of changes
            self._process_updates(response.get("payload", {}))
        elif (
            action == protocol.ACTION_CREATE_FILE
            or action == protocol.ACTION_UPDATE_FILE
        ):
            # Direct file update from server (broadcast from another client)
            payload = response.get("payload", {})
            path = payload.get("path")
            data = payload.get("data")

            if path:
                abs_path = os.path.join(self.local_folder_path, path)
                try:
                    # Tell the event handler to ignore this event
                    self.event_handler.ignore_next_event_for(abs_path)

                    # Ensure the parent directory exists
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

                    # Create/update the file
                    protocol.decode_file_data(data, abs_path)
                    logger.info(f"Received file update: {path}")
                except Exception as e:
                    logger.exception(f"Error applying file update for {path}: {e}")
        elif action == protocol.ACTION_DELETE_FILE:
            # File deletion from server
            payload = response.get("payload", {})
            path = payload.get("path")

            if path:
                self._apply_remote_deletion(path)
        elif action == protocol.ACTION_CREATE_FOLDER:
            # Folder creation from server
            payload = response.get("payload", {})
            path = payload.get("path")

            if path:
                self._apply_remote_folder_creation(path)
        elif action == protocol.ACTION_DELETE_FOLDER:
            # Folder deletion from server
            payload = response.get("payload", {})
            path = payload.get("path")

            if path:
                self._apply_remote_deletion(path)
        elif action == protocol.ACTION_ERROR:
            # Error from server
            error_message = response.get("payload", {}).get("message", "Unknown error")
            logger.error(f"Server error: {error_message}")
        else:
            logger.warning(f"Unexpected response to update request: {action}")

    def _process_updates(self, updates: Dict[str, Any]) -> None:
        """
        Process updates from the server.

        Args:
            updates: Dictionary with updated and deleted items
        """
        # Process deletions first
        for path in updates.get("deleted", []):
            self._apply_remote_deletion(path)

        # Then process updates
        updated_items = updates.get("updated", {})
        for path, timestamp in updated_items.items():
            # If it's a directory, we need to create it
            if path.endswith("/") or path.endswith("\\"):
                self._apply_remote_folder_creation(path)
            else:
                # We need to request the file content
                self._request_file_content(path)

    def _apply_remote_deletion(self, rel_path: str) -> None:
        """
        Apply a deletion from the server.

        Args:
            rel_path: Relative path of the item to delete
        """
        abs_path = os.path.join(self.local_folder_path, rel_path)

        try:
            # Tell the event handler to ignore this event
            self.event_handler.ignore_next_event_for(abs_path)

            if os.path.isdir(abs_path):
                # It's a directory - remove it and its contents
                self._remove_directory_recursive(abs_path)
                logger.info(f"Deleted directory {rel_path}")
            elif os.path.exists(abs_path):
                # It's a file
                os.remove(abs_path)
                logger.info(f"Deleted file {rel_path}")

        except Exception as e:
            logger.exception(f"Error applying remote deletion: {e}")

    def _remove_directory_recursive(self, dir_path: str) -> None:
        """
        Recursively remove a directory and all its contents.

        Args:
            dir_path: Absolute path to the directory
        """
        if not os.path.exists(dir_path):
            return

        # Walk the directory tree bottom-up to delete files first, then directories
        for root, dirs, files in os.walk(dir_path, topdown=False):
            # First remove all files in this directory
            for file in files:
                file_path = os.path.join(root, file)
                # Tell the event handler to ignore this event
                self.event_handler.ignore_next_event_for(file_path)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error removing file {file_path}: {e}")

            # Then remove the directory itself
            # Tell the event handler to ignore this event
            self.event_handler.ignore_next_event_for(root)
            try:
                os.rmdir(root)
            except Exception as e:
                logger.error(f"Error removing directory {root}: {e}")

    def _apply_remote_folder_creation(self, rel_path: str) -> None:
        """
        Apply a folder creation from the server.

        Args:
            rel_path: Relative path of the folder to create
        """
        abs_path = os.path.join(self.local_folder_path, rel_path)

        try:
            # Tell the event handler to ignore this event
            self.event_handler.ignore_next_event_for(abs_path)

            # Create the directory if it doesn't exist
            if not os.path.exists(abs_path):
                os.makedirs(abs_path, exist_ok=True)
                logger.info(f"Created directory {rel_path}")

        except Exception as e:
            logger.exception(f"Error applying remote folder creation: {e}")

    def _request_file_content(self, rel_path: str) -> None:
        """
        Request a file's content from the server.

        Args:
            rel_path: Relative path of the file
        """
        if not self.socket:
            return

        # FIXME: This is not implemented in the protocol/server
        # In a real implementation, we would have a specific message type for requesting
        # individual file content. For now, we'll assume we get file updates automatically.

    def handle_local_creation(self, path: str, is_directory: bool) -> None:
        """
        Handle a local file or directory creation.

        Args:
            path: Absolute path that was created
            is_directory: Whether it's a directory
        """
        if not path.startswith(self.local_folder_path):
            return

        # Get the path relative to the local folder
        rel_path = os.path.relpath(path, self.local_folder_path)

        with self.sync_lock:
            if not self.socket:
                return

            try:
                if is_directory:
                    # Create a message for folder creation
                    message = protocol.create_folder_message(rel_path)
                else:
                    # Create a message for file creation
                    message = protocol.create_file_message(rel_path, path)

                # Send the message
                protocol.send_message(self.socket, message)

                # Wait for acknowledgment
                response = protocol.receive_message(self.socket)
                if response.get("action") == protocol.ACTION_ACK:
                    logger.info(
                        f"Created {'directory' if is_directory else 'file'} {rel_path} on server"
                    )
                else:
                    logger.warning(f"Server failed to create {rel_path}: {response}")

            except Exception as e:
                logger.exception(f"Error handling local creation: {e}")

    def handle_local_deletion(self, path: str, is_directory: bool) -> None:
        """
        Handle a local file or directory deletion.

        Args:
            path: Absolute path that was deleted
            is_directory: Whether it's a directory
        """
        if not path.startswith(self.local_folder_path):
            return

        # Get the path relative to the local folder
        rel_path = os.path.relpath(path, self.local_folder_path)

        with self.sync_lock:
            if not self.socket:
                return

            try:
                if is_directory:
                    # Create a message for folder deletion
                    message = protocol.create_delete_folder_message(rel_path)
                else:
                    # Create a message for file deletion
                    message = protocol.create_delete_file_message(rel_path)

                # Send the message
                protocol.send_message(self.socket, message)

                # Wait for acknowledgment
                response = protocol.receive_message(self.socket)
                if response.get("action") == protocol.ACTION_ACK:
                    logger.info(
                        f"Deleted {'directory' if is_directory else 'file'} {rel_path} on server"
                    )
                else:
                    logger.warning(f"Server failed to delete {rel_path}: {response}")

            except Exception as e:
                logger.exception(f"Error handling local deletion: {e}")

    def handle_local_modification(self, path: str) -> None:
        """
        Handle a local file modification.

        Args:
            path: Absolute path that was modified
        """
        if not path.startswith(self.local_folder_path):
            return

        # Get the path relative to the local folder
        rel_path = os.path.relpath(path, self.local_folder_path)

        with self.sync_lock:
            if not self.socket:
                return

            try:
                # Create a message for file update
                message = protocol.create_update_file_message(rel_path, path)

                # Send the message
                protocol.send_message(self.socket, message)

                # Wait for acknowledgment
                response = protocol.receive_message(self.socket)
                if response.get("action") == protocol.ACTION_ACK:
                    logger.info(f"Updated file {rel_path} on server")
                else:
                    logger.warning(f"Server failed to update {rel_path}: {response}")

            except Exception as e:
                logger.exception(f"Error handling local modification: {e}")

    def _full_sync_with_server(self) -> None:
        """
        Perform a full synchronization with the server.
        This is used during startup or when resuming after being offline.
        """
        if not self.socket:
            return

        try:
            logger.info("Performing full sync with server...")

            # Create and send the request for all files
            request = protocol.create_request_all_files_message()
            protocol.send_message(self.socket, request)

            # Wait for response
            response = protocol.receive_message(self.socket)

            # Process the response
            if response.get("action") == protocol.ACTION_SEND_ALL_FILES:
                payload = response.get("payload", {})
                sync_start_time = payload.get("sync_start_time")

                self._process_all_files(payload)

                # Set the sync time to when the server started the scan
                if sync_start_time:
                    self.last_sync_time = sync_start_time
                    logger.info(
                        f"Full sync completed. Set last_sync_time to {sync_start_time}"
                    )
                else:
                    # Fallback if timestamp wasn't received (e.g., older server)
                    self.last_sync_time = time.time()
                    logger.warning(
                        "Full sync completed, but no sync_start_time received. Using current time."
                    )

            else:
                logger.warning(
                    f"Unexpected response to full sync request: {response.get('action')}"
                )
                # Fallback sync time
                self.last_sync_time = time.time()

        except Exception as e:
            logger.exception(f"Error during full sync with server: {e}")
            # Fallback sync time on error
            self.last_sync_time = time.time()

    def _process_all_files(self, data: Dict[str, Any]) -> None:
        """
        Process all files and folders received from the server.

        Args:
            data: Dictionary with files and folders information
        """
        # First, create all folders
        for folder_path in data.get("folders", []):
            abs_folder_path = os.path.join(self.local_folder_path, folder_path)
            try:
                # Tell the event handler to ignore this event
                self.event_handler.ignore_next_event_for(abs_folder_path)

                # Create the folder
                os.makedirs(abs_folder_path, exist_ok=True)
                logger.info(f"Created folder: {folder_path}")
            except Exception as e:
                logger.exception(f"Error creating folder {folder_path}: {e}")

        # Then, create all files
        for file_info in data.get("files", []):
            path = file_info.get("path")
            if not path:
                continue

            abs_path = os.path.join(self.local_folder_path, path)

            try:
                # Tell the event handler to ignore this event
                self.event_handler.ignore_next_event_for(abs_path)

                # Ensure the parent directory exists
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)

                # Get the file data
                file_data = file_info.get("data")

                # Create the file
                protocol.decode_file_data(file_data, abs_path)

                logger.info(f"Created/updated file: {path}")
            except Exception as e:
                logger.exception(f"Error creating/updating file {path}: {e}")
