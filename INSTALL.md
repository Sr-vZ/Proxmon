# Installation Guide

## Installing from Source

1. Clone the repository:
```bash
git clone <repository-url>
cd Proxmon
```

2. Install the package in development mode:
```bash
pip install -e .
```

Or install normally:
```bash
pip install .
```

## Installing from Wheel

1. Build the package:
```bash
python -m build
```

2. Install the wheel:
```bash
pip install dist/proxmon-0.1.0-py3-none-any.whl
```

## Usage

After installation, you can run the application using:

```bash
proxmon
```

## Configuration

Create a `.env` file in your home directory or current working directory with the following variables:

```env
PROXMOX_HOST=https://your-proxmox-host:8006
TOKEN_ID=your_token_id
TOKEN_SECRET=your_token_secret
NODE=your_node_name
SSH_HOST=your-proxmox-host
SSH_PORT=22
SSH_USER=your_username
SSH_PASSWORD=your_password
```

## Requirements

- Python 3.12 or higher
- Proxmox VE server with API access
- SSH access to the Proxmox node

## Dependencies

The package automatically installs the following dependencies:
- paramiko (SSH connections)
- psutil (system monitoring)
- python-dotenv (environment variables)
- readchar (keyboard input)
- requests (HTTP requests)
- rich (rich text formatting)
- textual (TUI framework)