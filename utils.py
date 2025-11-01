import logging
import os
import re

import paramiko
import requests
from dotenv import load_dotenv

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

HEADERS = {"Authorization": f"PVEAPIToken={API_TOKEN_ID}={API_TOKEN_SECRET}"}

# Global variables
TABLE_CURSOR = dict()
selected_vm = {
    "vmid": None,
    "type": None,
    "name": None,
    "status": None,
}


def ssh_execute_command(host, username, password, command, port=22):
    """SSH into a Proxmox node and execute a command."""
    if not host:
        return None

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host, port=port, username=username, password=password, timeout=10
        )
        _, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        client.close()
        return output if output else error
    except Exception as e:
        return f"SSH Connection failed: {str(e)}"


# Fetch data from Proxmox API
def get_data_from_proxapi(url):
    """Fetch data from Proxmox API"""
    response = requests.get(url, headers=HEADERS, verify=False)
    response.raise_for_status()
    return response.json()["data"]


def get_vm_config(vmid, vm_type):
    """Fetch vm/lxc config"""
    if vm_type == "vm":
        guesttype = "qemu"
    else:
        guesttype = "lxc"
    url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{guesttype}/{vmid}/config"
    return get_data_from_proxapi(url)


def get_vmids():
    """Retrieve VM and LXC IDs from Proxmox."""
    vmids = {"vm": [], "lxc": []}
    vm_data = get_data_from_proxapi(f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/qemu")
    vmids["vm"].extend([vm["vmid"] for vm in vm_data])
    lxc_data = get_data_from_proxapi(f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/lxc")
    vmids["lxc"].extend([lxc["vmid"] for lxc in lxc_data])
    return vmids


def get_vm_data(vmid, type="vm"):
    if type == "vm":
        url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/qemu/{vmid}/status/current"
    elif type == "lxc":
        url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/lxc/{vmid}/status/current"
    else:
        return None
    return get_data_from_proxapi(url)


def get_rrd_data(vmid, type="vm"):
    if type == "vm":
        guesttype = "qemu"
    else:
        guesttype = "lxc"
    url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{guesttype}/{vmid}/rrddata?timeframe=hour"
    return get_data_from_proxapi(url)


def draw_vertical_bar_chart(
    data,
    height=10,
    value_width=5,
    decimal_places=1,
    chart_width=None,
    color="white",
    char="░",
    max_output_width=200,
):
    """
    Draws a vertical bar chart with consistent padding and adjustable width.

    Args:
        data: A list of numerical values to chart.
        height: The height of the chart.
        value_width: The total width of the formatted numerical value.
        decimal_places: The number of decimal places to display.
        chart_width: The width of the bars in the chart. If None, it defaults to the length of the data.
        max_output_width: Maximum width of the output string.
    Returns:
        A string representation of the chart.
    """
    if chart_width is None:
        chart_width = len(data)

    max_value = max(data)
    min_value = min(data)
    range_value = max_value - min_value

    chart = [[" " for _ in range(chart_width)] for _ in range(height)]

    for i, value in enumerate(data):
        normalized_value = (
            int((value - min_value) / range_value * (height - 1))
            if range_value > 0
            else 0
        )
        if i < chart_width:
            for h in range(normalized_value + 1):
                if h < height:
                    chart[height - 1 - h][i] = f"{char}"

    chart_str = ""
    format_string = f">{value_width}.{decimal_places}f"
    for h in range(height):
        value_at_height = (
            min_value + (range_value * (height - 1 - h) / (height - 1))
            if range_value > 0
            else min_value
        )
        formatted_value = f"{value_at_height:{format_string}}"
        chart_str += f"{formatted_value} ┤ " + "".join(chart[h]) + "\n"

    chart_str += f"{' ' * value_width} ╰" + "─" * chart_width + "\n"
    # chart_str += "    " + ' '.join(str(i) for i in range(len(data))) + "\n"

    # Limit the output width
    lines = chart_str.splitlines()
    truncated_lines = []
    for line in lines:
        if len(line) > max_output_width:
            truncated_lines.append(line[:max_output_width] + "...")
        else:
            truncated_lines.append(line)
    chart_str = "\n".join(truncated_lines)

    return f"[{color}]{chart_str}[/{color}]"


def get_vmids_dict():
    """Retrieve VM and LXC IDs as a dictionary."""
    vmids = {}
    for vm in get_data_from_proxapi(f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/qemu"):
        vmids[vm["vmid"]] = {"type": "qemu"}
    for lxc in get_data_from_proxapi(f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/lxc"):
        vmids[lxc["vmid"]] = {"type": "lxc"}
    return vmids


def get_pve_subnets():
    """Retrieve Proxmox subnets."""
    url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/network/"
    data = get_data_from_proxapi(url)
    return [item["cidr"] for item in data if "cidr" in item]


def find_vm_ip_address():
    """Find IP addresses of VMs based on their MAC addresses."""
    ip_table = {}
    if SSH_HOST:
        neigh_data = ssh_execute_command(
            SSH_HOST, SSH_USER, SSH_PASSWORD, "arp -a", SSH_PORT
        )
        pattern = re.compile(r"\(\s*(\d+\.\d+\.\d+\.\d+)\s*\) at (\S+)")
        matches = pattern.findall(neigh_data)
        mac_to_ip = {mac.upper(): ip for ip, mac in matches}

        vmid_data = get_vmids_dict()
        for vmid, info in vmid_data.items():
            url = (
                f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{info['type']}/{vmid}/config"
            )
            config_data = get_data_from_proxapi(url)
            net_config = config_data.get("net0", "")
            mac_match = re.search(
                r"(?:hwaddr=)?([0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5})", net_config
            )
            mac_address = mac_match.group(1) if mac_match else None
            if mac_address:
                ip_table[vmid] = {
                    "mac": mac_address,
                    "ip": mac_to_ip.get(mac_address, "N/A"),
                }
    else:
        vmid_data = get_vmids_dict()
        for vmid, info in vmid_data.items():
            if info["type"] == "qemu":
                ip_table[vmid] = {
                    "mac": "N/A",
                    "ip": "N/A",
                }
                continue
            # url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{info['type']}/{vmid}/config"
            url = f"{PROXMOX_API_URL}/api2/json/nodes/{NODE}/{info['type']}/{vmid}/interfaces"
            config_data = get_data_from_proxapi(url)
            for data in config_data:
                name = data.get("name", "")
                if name != "lo":
                    ip_table[vmid] = {
                        "mac": data.get("hwaddr"),
                        "ip": data.get("inet"),
                    }
    return ip_table


def get_cpu_temperature():
    """Get the CPU temperature using psutil or SSH command."""
    # if hasattr(psutil, "sensors_temperatures"):
    #     temps = psutil.sensors_temperatures()
    #     if "coretemp" in temps:
    #         return {entry.label or "CPU": entry.current for entry in temps["coretemp"]}
    #     elif "cpu_thermal" in temps:
    #         return {"CPU": temps["cpu_thermal"][0].current}
    try:
        temp = ssh_execute_command(
            SSH_HOST,
            SSH_USER,
            SSH_PASSWORD,
            "cat /sys/class/thermal/thermal_zone0/temp",
            SSH_PORT,
        )
        temp = float(temp) / 1000
    except Exception as ex:
        logging.getLogger().exception(ex, "Could not execute ssh command")
        temp = None
    return {"CPU": ("%.1f" % temp) if temp else "N/A"}
