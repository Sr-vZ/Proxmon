"""
Proxmon - A Proxmox monitoring tool with a Textual-based TUI.

This package provides a terminal-based interface for monitoring Proxmox VMs and LXCs,
including real-time statistics, VM management, and system monitoring.
"""

__version__ = "0.1.0"
__author__ = "Proxmon Team"
__description__ = "A Proxmox monitoring tool with Textual-based TUI"

from .main import ProxmonApp, toggle_vm

__all__ = ["ProxmonApp", "toggle_vm"]