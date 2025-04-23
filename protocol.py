"""
Defines the communication protocol between the server and clients.

Specifies:
- Message formats (e.g., using JSON).
- Actions (e.g., CREATE_FILE, UPDATE_FILE, DELETE_FOLDER, etc.).
- Data serialization methods.
"""

import base64
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

# Message actions
ACTION_CONNECT = "CONNECT"
ACTION_DISCONNECT = "DISCONNECT"
ACTION_CREATE_FILE = "CREATE_FILE"
ACTION_UPDATE_FILE = "UPDATE_FILE"
ACTION_DELETE_FILE = "DELETE_FILE"
ACTION_CREATE_FOLDER = "CREATE_FOLDER"
ACTION_DELETE_FOLDER = "DELETE_FOLDER"
ACTION_REQUEST_UPDATES = "REQUEST_UPDATES"
ACTION_SEND_UPDATES = "SEND_UPDATES"
ACTION_REQUEST_ALL_FILES = "REQUEST_ALL_FILES"
ACTION_SEND_ALL_FILES = "SEND_ALL_FILES"
ACTION_ACK = "ACK"
ACTION_ERROR = "ERROR"

# Status codes
STATUS_OK = 200
STATUS_ERROR = 500
STATUS_NOT_FOUND = 404
STATUS_BAD_REQUEST = 400

# Maximum message size in bytes (header + payload)
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB

# Message header size (in bytes)
HEADER_SIZE = 8


def create_message(action: str, payload: Optional[Dict[str, Any]] = None) -> bytes:
    """
    Create a message with the given action and payload.

    Args:
        action: The action to perform
        payload: The message payload (will be converted to JSON)

    Returns:
        bytes: The encoded message
    """
    if payload is None:
        payload = {}

    message = {"action": action, "payload": payload}

    # Convert to JSON and then to bytes
    json_data = json.dumps(message).encode("utf-8")

    # Create header with message length
    header = len(json_data).to_bytes(HEADER_SIZE, byteorder="big")

    return header + json_data


def parse_message(message_bytes: bytes) -> Dict[str, Any]:
    """
    Parse a message from bytes.

    Args:
        message_bytes: The raw message bytes

    Returns:
        dict: The parsed message as a dictionary
    """
    # Skip the header (first HEADER_SIZE bytes)
    json_data = message_bytes[HEADER_SIZE:]

    # Convert from JSON
    return json.loads(json_data.decode("utf-8"))


def encode_file_data(file_path: str) -> Optional[str]:
    """
    Read a file and encode its content as base64.

    Args:
        file_path: Path to the file

    Returns:
        str: Base64 encoded file content, or None for empty files
    """
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return None

    with open(file_path, "rb") as f:
        file_data = f.read()
    return base64.b64encode(file_data).decode("utf-8")


def decode_file_data(encoded_data: Optional[str], file_path: str) -> None:
    """
    Decode base64 data and write to a file.

    Args:
        encoded_data: Base64 encoded data, or None for empty files
        file_path: Path to write the file

    Returns:
        None
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    if encoded_data is None:
        # Create an empty file
        with open(file_path, "wb") as f:
            pass
    else:
        file_data = base64.b64decode(encoded_data)
        with open(file_path, "wb") as f:
            f.write(file_data)


def create_file_message(rel_path: str, file_path: str) -> bytes:
    """
    Create a message for sending a file.

    Args:
        rel_path: Relative path of the file in the shared folder
        file_path: Absolute path to the file

    Returns:
        bytes: The message
    """
    encoded_data = encode_file_data(file_path)
    payload = {
        "path": rel_path,
        "data": encoded_data,
        "timestamp": os.path.getmtime(file_path),
    }
    return create_message(ACTION_CREATE_FILE, payload)


def create_update_file_message(rel_path: str, file_path: str) -> bytes:
    """
    Create a message for updating a file.

    Args:
        rel_path: Relative path of the file in the shared folder
        file_path: Absolute path to the file

    Returns:
        bytes: The message
    """
    encoded_data = encode_file_data(file_path)
    payload = {
        "path": rel_path,
        "data": encoded_data,
        "timestamp": os.path.getmtime(file_path),
    }
    return create_message(ACTION_UPDATE_FILE, payload)


def create_delete_file_message(rel_path: str) -> bytes:
    """
    Create a message for deleting a file.

    Args:
        rel_path: Relative path of the file in the shared folder

    Returns:
        bytes: The message
    """
    payload = {"path": rel_path, "timestamp": time.time()}
    return create_message(ACTION_DELETE_FILE, payload)


def create_folder_message(rel_path: str) -> bytes:
    """
    Create a message for creating a folder.

    Args:
        rel_path: Relative path of the folder in the shared folder

    Returns:
        bytes: The message
    """
    payload = {"path": rel_path, "timestamp": time.time()}
    return create_message(ACTION_CREATE_FOLDER, payload)


def create_delete_folder_message(rel_path: str) -> bytes:
    """
    Create a message for deleting a folder.

    Args:
        rel_path: Relative path of the folder in the shared folder

    Returns:
        bytes: The message
    """
    payload = {"path": rel_path, "timestamp": time.time()}
    return create_message(ACTION_DELETE_FOLDER, payload)


def create_request_updates_message(last_sync: float) -> bytes:
    """
    Create a message to request updates since last_sync.

    Args:
        last_sync: Timestamp of last synchronization

    Returns:
        bytes: The message
    """
    payload = {"last_sync": last_sync}
    return create_message(ACTION_REQUEST_UPDATES, payload)


def create_ack_message(status: int = STATUS_OK, message: str = "OK") -> bytes:
    """
    Create an acknowledgment message.

    Args:
        status: Status code
        message: Status message

    Returns:
        bytes: The message
    """
    payload = {"status": status, "message": message}
    return create_message(ACTION_ACK, payload)


def create_error_message(error_message: str) -> bytes:
    """
    Create an error message.

    Args:
        error_message: Error description

    Returns:
        bytes: The message
    """
    payload = {"status": STATUS_ERROR, "message": error_message}
    return create_message(ACTION_ERROR, payload)


def create_request_all_files_message() -> bytes:
    """
    Create a message to request all files from the server.

    Returns:
        bytes: The message
    """
    return create_message(ACTION_REQUEST_ALL_FILES)


def receive_message_header(sock) -> int:
    """
    Receive the message header from a socket and return the message size.

    Args:
        sock: The socket to receive from

    Returns:
        int: The message size
    """
    header = b""
    while len(header) < HEADER_SIZE:
        chunk = sock.recv(HEADER_SIZE - len(header))
        if not chunk:
            raise ConnectionError("Connection closed while receiving header")
        header += chunk

    return int.from_bytes(header, byteorder="big")


def receive_message(sock) -> Dict[str, Any]:
    """
    Receive a complete message from a socket.

    Args:
        sock: The socket to receive from

    Returns:
        dict: The parsed message
    """
    message_size = receive_message_header(sock)

    # Check if message size is reasonable
    if message_size > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message size too large: {message_size} bytes")

    # Receive the message data
    message_data = b""
    while len(message_data) < message_size:
        chunk = sock.recv(min(4096, message_size - len(message_data)))
        if not chunk:
            raise ConnectionError("Connection closed while receiving message")
        message_data += chunk

    # Reconstruct the full message with header for parsing
    full_message = message_size.to_bytes(HEADER_SIZE, byteorder="big") + message_data

    return parse_message(full_message)


def send_message(sock, message: bytes) -> None:
    """
    Send a message over a socket.

    Args:
        sock: The socket to send over
        message: The message bytes to send

    Returns:
        None
    """
    total_sent = 0
    while total_sent < len(message):
        sent = sock.send(message[total_sent:])
        if sent == 0:
            raise ConnectionError("Socket connection broken")
        total_sent += sent
