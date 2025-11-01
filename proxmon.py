import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Static

from utils import (
    draw_vertical_bar_chart,
    find_vm_ip_address,
    get_cpu_temperature,
    get_data_from_proxapi,
    get_vm_config,
    get_vm_data,
    get_vmids,
)

# Configure logging
logging.basicConfig(level=logging.ERROR)
log_file_path = Path.cwd() / "proxmon.log"
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
logging.getLogger().addHandler(file_handler)

# Load environment variables
load_dotenv()

# Proxmox API configuration
PROXMOX_API_URL = os.getenv("PROXMOX_HOST")
API_TOKEN_ID = os.getenv("TOKEN_ID")
API_TOKEN_SECRET = os.getenv("TOKEN_SECRET")
NODE = os.getenv("NODE")
SSH_HOST = os.getenv("SSH_HOST")
SSH_PORT = int(os.getenv("SSH_PORT", 22))
SSH_USER = os.getenv("SSH_USER")
SSH_PASSWORD = os.getenv("SSH_PASSWORD")

# Disable warnings for unverified HTTPS requests
requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

HEADERS = {"Authorization": f"PVEAPIToken={API_TOKEN_ID}={API_TOKEN_SECRET}"}

# Global variables
TABLE_CURSOR = dict()
selected_vm = {
    "vmid": None,
    "type": None,
    "name": None,
    "status": None,
}


def toggle_vm(self):
    """Start or stop the selected VM or LXC."""
    global selected_vm
    if selected_vm["vmid"] is None:
        self.notify("Select a VM/LXC first!", severity="warning")
        return

    vmid = selected_vm["vmid"]
    vm_type = selected_vm["type"]
    vm_status = selected_vm["status"].split(" ")[1].lower()
    data = get_vm_data(selected_vm["vmid"], selected_vm["type"])
    vm_status = data["status"]
    vm_name = selected_vm["name"]
    guesttype = "qemu" if vm_type == "vm" else "lxc"
    if vm_status == "running":
        self.notify(f"{vm_name} {vm_type} Shutting down!")
        url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{guesttype}/{vmid}/status/shutdown"
    else:
        self.notify(f"{vm_name} {vm_type} Starting!")
        url = (
            f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{guesttype}/{vmid}/status/start"
        )
    response = requests.post(url, headers=HEADERS, verify=False)
    response.raise_for_status()
    return response.json()


class ProxmonApp(App):
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("", "prev_entry", "Up"),
        ("", "next_entry", "Down"),
        ("󰌑", "", "Select"),
        ("S", "toggle_vm", "Start/Stop"),
        ("Ctrl+Q", "quit", "Quit"),
    ]

    timer: Timer
    timer_rrd: Timer
    layout = Layout()
    vm_stats = {}

    def compose(self) -> ComposeResult:
        yield Header("Proxmox Monitor")
        yield Container(
            Static("Proxmox Node Monitor", id="topbar"),
            id="topbar_container",
            classes="topbar_container",
        )
        yield VerticalScroll(DataTable(id="vm_table"), id="vm_table_vs")
        yield VerticalScroll(Static(id="stats", expand=True))
        yield Footer()

    def update_node_stats(self):
        """Update and display node statistics."""
        temp = get_cpu_temperature()
        url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/status"
        node_data = get_data_from_proxapi(url)
        free_memory = node_data.get("memory").get("free", 0) / (1024 * 1024 * 1024)
        total_memory = node_data.get("memory").get("total", 0) / (1024 * 1024 * 1024)
        used_memory = round(total_memory - free_memory, 0)
        cpu_load = (
            float(node_data.get("loadavg")[0])
            / int(node_data.get("cpuinfo").get("cores"))
            * 100
        )
        uptime = int(node_data.get("uptime", 0))
        formatted_uptime = f"{uptime // 86400:02}:{(uptime % 86400) // 3600:02}:{(uptime % 3600) // 60:02}:{uptime % 60:02}"
        # disk_free = node_data.get("rootfs").get("free") / (1024 * 1024 * 1024)
        disk_total = node_data.get("rootfs").get("total") / (1024 * 1024 * 1024)
        disk_used = node_data.get("rootfs").get("used") / (1024 * 1024 * 1024)

        text = (
            f"   {node_data.get('pveversion')} "
            f"｜   CPU: {node_data.get('cpuinfo').get('model')} "
            f"｜ 󰽘 Cores: {node_data.get('cpuinfo').get('cores')} "
            f"｜ 󰄪 Load: {cpu_load:.0f}% "
            f"｜ 🌡️Temp: {str(temp.get('CPU', 'N/A'))} °C "
            f"｜   RAM: {used_memory:.0f}/{total_memory:.0f} GB "
            f"｜  Disk: {disk_used:.0f}/{disk_total:.0f} GB "
            f"｜ 󰔚 Uptime: {formatted_uptime} "
        )
        self.query_one("#topbar", Static).update(Text(text, justify="full", style=""))

    def on_mount(self):
        """Initialize the app and set up timers."""
        global selected_vm
        table = self.query_one("#vm_table", DataTable)
        table.add_columns(
            "ID",
            "Status",
            "Type",
            "Name",
            "Core",
            "CPU (%)",
            "RAM (MB)",
            "Disk (GB)",
            "NetIN (MB)",
            "NetOUT (MB)",
            "MAC",
            "IP",
        )
        table.cursor_type = "row"
        table.zebra_stripes = False
        table.border = True
        self.update_table()
        self.update_node_stats()

        self.timer = self.set_interval(10, self.update_table)
        self.timer_node_stats = self.set_interval(10, self.update_node_stats)
        self.timer_rrd = self.set_interval(2, self.update_rrd_data)

    def on_key(self, event) -> None:
        """Handle key events."""
        if event.key == "s":
            toggle_vm(self)
            self.update_table()

    def update_table(self):
        """Update the VM and LXC table."""
        global TABLE_CURSOR
        table = self.query_one("#vm_table", DataTable)

        # Store the current cursor position
        selected_row = table.cursor_row

        table.clear()

        vm_data = []
        for vmid in get_vmids()["vm"]:
            json_data = get_vm_data(vmid, type="vm")
            json_data["type"] = "VM"
            vm_data.append(json_data)

        for vmid in get_vmids()["lxc"]:
            json_data = get_vm_data(vmid, type="lxc")
            json_data["type"] = "LXC"
            vm_data.append(json_data)

        vm_data = sorted(vm_data, key=lambda d: d["vmid"])
        ip_data = find_vm_ip_address()

        for idx, data in enumerate(vm_data):
            mem_usage = (
                (data.get("maxmem", 0)) / (1024 * 1024)
                if data.get("mem", 0) != 0
                else 0
            )
            table.add_row(
                str(data["vmid"]),
                "🟢 Running" if data["status"] == "running" else "🔴 Stopped",
                data["type"],
                data["name"],
                "  " + str(data["cpus"]),
                str(round(data.get("cpu", 0) * 100, 2)),
                str(round(mem_usage, 0)),
                str(round(data.get("maxdisk", 0) / (1024 * 1024 * 1024), 0)),
                str(round(data["netin"] / (1024 * 1024), 2)),
                str(round(data["netout"] / (1024 * 1024), 2)),
                ip_data.get(data["vmid"]).get("mac", "N/A"),
                ip_data.get(data["vmid"]).get("ip", "N/A"),
            )

        # Restore the cursor position
        if selected_row is not None and selected_row < len(table.rows):
            table.move_cursor(row=selected_row)

        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection in the data table."""
        global selected_vm
        global TABLE_CURSOR
        table = self.query_one("#vm_table", DataTable)
        row_key = event.row_key
        row_data = table.get_row(row_key)

        # selected_vm.update({
        #     "name": row_data[3],
        #     "vmid": row_data[0],
        #     "type": row_data[2].lower(),
        #     "status": row_data[1],
        # })
        selected_vm["name"] = row_data[3]
        selected_vm["vmid"] = row_data[0]
        selected_vm["type"] = row_data[2].lower()
        selected_vm["status"] = row_data[1]

        TABLE_CURSOR = {"cursor_row": event.cursor_row, "row_key": event.row_key}
        self.update_rrd_data()

    def update_rrd_data(self):
        """Update RRD data for the selected VM or LXC."""
        global selected_vm
        if selected_vm["vmid"] is None:
            return

        vmid = selected_vm["vmid"]
        vm_type = selected_vm["type"]
        vm_name = selected_vm["name"]
        # rrd_data = get_rrd_data(vmid=vmid, vm_type=vm_type)
        # rrd_data = sorted(rrd_data, key=lambda d: d['time'])

        data = get_vm_data(vmid, vm_type)
        ts = time.time()
        cpu = data.get("cpu", data.get("maxcpu", 0)) * 100
        mem = data.get("mem", data.get("maxmem", 0)) / (1024 * 1024)
        netin = data.get("netin", 0) / (1024 * 1024)
        netout = data.get("netout", 0) / (1024 * 1024)

        if vmid not in self.vm_stats:
            self.vm_stats[vmid] = {
                "ts": [],
                "cpu": [],
                "mem": [],
                "netin": [],
                "netout": [],
            }

        # Update stats and keep only the last 100 values
        self.vm_stats[vmid]["ts"].append(ts)
        self.vm_stats[vmid]["ts"] = self.vm_stats[vmid]["ts"][-100:]

        self.vm_stats[vmid]["cpu"].append(cpu)
        self.vm_stats[vmid]["cpu"] = self.vm_stats[vmid]["cpu"][-100:]

        self.vm_stats[vmid]["mem"].append(mem)
        self.vm_stats[vmid]["mem"] = self.vm_stats[vmid]["mem"][-100:]

        self.vm_stats[vmid]["netin"].append(netin)
        self.vm_stats[vmid]["netin"] = self.vm_stats[vmid]["netin"][-100:]

        self.vm_stats[vmid]["netout"].append(netout)
        self.vm_stats[vmid]["netout"] = self.vm_stats[vmid]["netout"][-100:]

        layout = self.stats_layout()
        vm_type_icon = "" if vm_type == "lxc" else ""
        layout["stat_header"].update(
            Panel(
                f"VMID: {vmid} Type: {vm_type_icon}  {vm_type.upper()}"
                + f" Name: {vm_name} CPU: 󰓅 {cpu:.1f} % Mem:   {mem:.1f} MB  NetIn: 󰅢  MB {netin:.1f}  Netout: 󰅧  {netout:.1f} MB",
                title="Stats",
                border_style="blue",
            )
        )

        if data.get("status") == "running":
            layout["cpu"].update(
                Panel(
                    draw_vertical_bar_chart(
                        self.vm_stats[vmid]["cpu"],
                        height=8,
                        chart_width=90,
                        color="green",
                    ),
                    title="CPU Usage",
                    border_style="blue",
                )
            )
            layout["mem"].update(
                Panel(
                    draw_vertical_bar_chart(
                        self.vm_stats[vmid]["mem"],
                        height=8,
                        chart_width=90,
                        color="cyan",
                        decimal_places=0,
                    ),
                    title="Memory Usage",
                    border_style="blue",
                )
            )
            layout["netin"].update(
                Panel(
                    draw_vertical_bar_chart(
                        self.vm_stats[vmid]["netin"],
                        height=8,
                        chart_width=90,
                        color="yellow",
                        decimal_places=1,
                        char=".",
                    ),
                    title="Network In",
                    border_style="blue",
                )
            )
            layout["netout"].update(
                Panel(
                    draw_vertical_bar_chart(
                        self.vm_stats[vmid]["netout"],
                        height=8,
                        chart_width=90,
                        color="dodger_blue2",
                        decimal_places=1,
                        char=".",
                    ),
                    title="Network Out",
                    border_style="blue",
                )
            )
        else:
            layout["cpu"].update(
                Panel("VM/LXC Not Running!", title="CPU Usage", border_style="blue")
            )
            layout["mem"].update(
                Panel("VM/LXC Not Running!", title="Memory Usage", border_style="blue")
            )
            layout["netin"].update(
                Panel("VM/LXC Not Running!", title="Network In", border_style="blue")
            )
            layout["netout"].update(
                Panel("VM/LXC Not Running!", title="Network Out", border_style="blue")
            )

        self.query_one("#stats", Static).update(layout)
        # layout["misc"].update(Panel(str(get_vm_config(vmid, vm_type)), border_style="blue"))
        layout["misc"].update(
            Panel(
                json.dumps(get_vm_config(vmid, vm_type), indent=4, sort_keys=True),
                border_style="magenta",
            )
        )

    def stats_layout(self):
        """Define the layout for statistics display."""
        layout = self.layout
        layout.split(
            Layout(name="stat_header", size=3),
            Layout(name="main"),
            Layout(name="misc"),
        )
        layout["main"].split_column(
            Layout(name="process"),
            Layout(name="network"),
        )
        layout["process"].split_row(
            Layout(name="cpu"),
            Layout(name="mem"),
        )
        layout["network"].split_row(
            Layout(name="netin"),
            Layout(name="netout"),
        )
        return layout


if __name__ == "__main__":
    app = ProxmonApp()
    app.run()
