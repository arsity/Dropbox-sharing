# Dropbox-like File Sharing Simulator

In this file, we implement a dropbox-like file sharing simulator based on Python. As 
one of requirements, we start from Socket Programing instead of using libraries directly.

For simplicity, we only consider one user and no GUI, authentication, and security design.


## Functions

- File (create / delete / update)
  - including binary file and text file (incremental update)
- Folder (create / delete / update)
- Client should periodically fetch updates from server
  - If one client update content, another client should auto fetch and reflects the update from the server
  - The content retrieved from multiple clients should be synchronized.


## Design

Server-client mode.

### File Structure

```
/
├── README.md
├── main_server.py    # Entry point to start the server
├── server.py         # Core server logic
├── main_client.py    # Entry point to start a client
├── client.py         # Core client logic
├── protocol.py       # Defines communication protocol
├── requirements.txt  # Project dependencies
└── shared_folder/    # (Created by server/client) Directory for shared files
```

### Update Design

The system handles updates efficiently by transmitting only necessary information:

1.  **Local Changes (Client -> Server):** When a file or folder is changed locally (created, updated, deleted), the client sends a message to the server containing only the details of that specific change.
2.  **Real-time Broadcasts (Server -> Other Clients):** After processing a change from one client, the server immediately broadcasts a message (using persistent TCP connection) about that specific change (e.g., file content for an update, deletion notification) to all *other* connected clients. This allows for near real-time synchronization.
3.  **Periodic Polling (Client <- Server):** Clients periodically (e.g., every second) ask the server for any updates that occurred since their last check-in time. The server responds with a list containing only the items (files/folders) that were modified or deleted during that interval.
4.  **Initial Sync:** When a client first connects, it performs a full sync, requesting the complete list of all current files and folders from the server.

This ensures that, except for the initial connection, only relevant changes are transmitted over the network, minimizing bandwidth usage.

