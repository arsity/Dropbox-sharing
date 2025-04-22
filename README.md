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