"""
Defines the communication protocol between the server and clients.

Specifies:
- Message formats (e.g., using JSON).
- Actions (e.g., CREATE_FILE, UPDATE_FILE, DELETE_FOLDER, etc.).
- Data serialization methods.
"""

# TODO: Define message structures and constants
# Example actions:
ACTION_CONNECT = "CONNECT"
ACTION_DISCONNECT = "DISCONNECT"
ACTION_CREATE_FILE = "CREATE_FILE"
ACTION_UPDATE_FILE = "UPDATE_FILE"
ACTION_DELETE_FILE = "DELETE_FILE"
ACTION_CREATE_FOLDER = "CREATE_FOLDER"
ACTION_DELETE_FOLDER = "DELETE_FOLDER"
ACTION_REQUEST_UPDATES = "REQUEST_UPDATES"
ACTION_SEND_UPDATES = "SEND_UPDATES"
ACTION_ACK = "ACK"
ACTION_ERROR = "ERROR"

# TODO: Define helper functions for creating/parsing messages 