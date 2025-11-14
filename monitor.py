#!/usr/bin/env python3
"""
Advanced System Monitoring App with:
- Real-time curses dashboard
- CSV logging
- Email alerts on high CPU/RAM/Disk
- Sparkline graphs
- Process filtering
- Fully commented for learning
"""

import time
import csv
import os
import sys
import curses
import psutil
import datetime
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import deque
from typing import Dict, List, Any

# ===================================================================
# ========================== CONFIGURATION ==========================
# ===================================================================

# How often to update (in seconds)
REFRESH_INTERVAL = 1.0

# CSV log file
LOG_FILE = "system_monitor_log.csv"

# How many data points to keep for graphs
MAX_HISTORY = 200

# Filter processes by name (set to None to monitor all)
PROCESS_FILTER = None  # Example: "python", "chrome", "nginx"

# Email alert settings (configure your own!)
ENABLE_EMAIL_ALERTS = True
ALERT_COOLDOWN = 300  # seconds between alerts (5 min)
SMTP_SERVER = "smtp.gmail.com"  # Change if using another provider
SMTP_PORT = 587
SENDER_EMAIL = "your_email@gmail.com"  # CHANGE ME
SENDER_PASSWORD = "your_app_password"  # CHANGE ME (use App Password for Gmail)
RECIPIENT_EMAIL = "admin@yourdomain.com"  # CHANGE ME

# Alert thresholds
CPU_THRESHOLD = 90.0    # % - send alert if CPU > this
RAM_THRESHOLD = 90.0    # % - send alert if RAM > this
DISK_THRESHOLD = 95.0   # % - send alert if Disk > this

# ===================================================================
# ========================== EMAIL ALERTS ===========================
# ===================================================================

class AlertManager:
    """
    Handles sending email alerts with cooldown to avoid spam.
    """
    def __init__(self):
        self.last_alert_time = 0

    def should_send(self) -> bool:
        """Check if enough time has passed since last alert."""
        now = time.time()
        if now - self.last_alert_time >= ALERT_COOLDOWN:
            self.last_alert_time = now
            return True
        return False

    def send_email(self, subject: str, body: str):
        """Send email using SMTP."""
        if not ENABLE_EMAIL_ALERTS:
            return

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            text = msg.as_string()
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, text)
            server.quit()
            print(f"[ALERT SENT] {subject}")
        except Exception as e:
            print(f"[EMAIL FAILED] {e}")

# Create global alert manager
alert_manager = AlertManager()

# ===================================================================
# ========================== CSV LOGGER THREAD ======================
# ===================================================================

class CSVLogger(threading.Thread):
    """
    Background thread that writes monitoring data to CSV file.
    Runs forever until program exits.
    """
    def __init__(self, queue: deque):
        super().__init__(daemon=True)  # Dies when main program exits
        self.queue = queue
        self.start()  # Start thread immediately

    def run(self):
        """Write queued rows to CSV file."""
        # Write header only if file doesn't exist
        write_header = not os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a", newline="", buffering=1) as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "timestamp",
                    "cpu_percent",
                    "ram_percent",
                    "ram_used_gb",
                    "disk_percent",
                    "net_sent_mb",
                    "net_recv_mb",
                    "process_count",
                    "filtered_cpu",
                    "filtered_ram_gb"
                ])
            # Keep checking queue
            while True:
                if self.queue:
                    row = self.queue.popleft()
                    writer.writerow(row)
                time.sleep(0.1)  # Avoid busy loop

# ===================================================================
# ========================== DATA COLLECTOR =========================
# ===================================================================

def collect_data(history: Dict[str, deque], net_io_prev: Dict[str, int], log_queue: deque) -> dict:
    """
    Collect system metrics and return current values + CSV row.
    Also updates history for graphs and checks for alerts.
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")

    # --------------------- System Metrics ---------------------
    cpu_percent = psutil.cpu_percent(interval=None)  # % CPU usage
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net_io = psutil.net_io_counters()
    process_count = len(psutil.pids())

    # --------------------- Network Delta ---------------------
    # Calculate MB sent/received since last check
    sent_mb = (net_io.bytes_sent - net_io_prev.get("sent", 0)) / (1024 ** 2)
    recv_mb = (net_io.bytes_recv - net_io_prev.get("recv", 0)) / (1024 ** 2)
    net_io_prev["sent"] = net_io.bytes_sent
    net_io_prev["recv"] = net_io.bytes_recv

    # --------------------- Filtered Processes ---------------------
    filtered_cpu = 0.0
    filtered_ram_bytes = 0
    for p in psutil.process_iter(['name', 'cpu_percent', 'memory_info']):
        try:
            if PROCESS_FILTER and PROCESS_FILTER.lower() not in p.info['name'].lower():
                continue
            filtered_cpu += p.info['cpu_percent'] or 0.0
            filtered_ram_bytes += p.info['memory_info'].rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue  # Skip dead or inaccessible processes
    filtered_ram_gb = round(filtered_ram_bytes / (1024 ** 3), 2)

    # --------------------- Update History (for graphs) ---------------------
    for key, value in [
        ("cpu", cpu_percent),
        ("ram", memory.percent),
        ("disk", disk.percent),
        ("net_sent", sent_mb),
        ("net_recv", recv_mb),
        ("filtered_cpu", filtered_cpu),
        ("filtered_ram", filtered_ram_gb),
    ]:
        h = history.setdefault(key, deque(maxlen=MAX_HISTORY))
        h.append(value)

    # --------------------- Build CSV Row ---------------------
    csv_row = [
        now,
        round(cpu_percent, 1),
        round(memory.percent, 1),
        round(memory.used / (1024 ** 3), 2),
        round(disk.percent, 1),
        round(sent_mb, 3),
        round(recv_mb, 3),
        process_count,
        round(filtered_cpu, 1),
        filtered_ram_gb
    ]

    # --------------------- Check Alert Thresholds ---------------------
    alerts = []
    if cpu_percent > CPU_THRESHOLD:
        alerts.append(f"CPU usage is {cpu_percent:.1f}% (> {CPU_THRESHOLD}%)")
    if memory.percent > RAM_THRESHOLD:
        alerts.append(f"RAM usage is {memory.percent:.1f}% (> {RAM_THRESHOLD}%)")
    if disk.percent > DISK_THRESHOLD:
        alerts.append(f"Disk usage is {disk.percent:.1f}% (> {DISK_THRESHOLD}%)")

    if alerts and alert_manager.should_send():
        subject = "SYSTEM ALERT: High Resource Usage"
        body = f"Timestamp: {now}\n\n" + "\n".join(alerts)
        threading.Thread(target=alert_manager.send_email, args=(subject, body), daemon=True).start()

    # Return everything needed
    return {
        "csv_row": csv_row,
        "net_io_prev": net_io_prev,
        "current": {
            "timestamp": now,
            "cpu": cpu_percent,
            "ram_percent": memory.percent,
            "ram_used_gb": round(memory.used / (1024 ** 3), 2),
            "disk": disk.percent,
            "net_sent": sent_mb,
            "net_recv": recv_mb,
            "processes": process_count,
            "filtered_cpu": filtered_cpu,
            "filtered_ram_gb": filtered_ram_gb,
        }
    }

# ===================================================================
# ========================== CURSES DASHBOARD =======================
# ===================================================================

def draw_sparkline(stdscr, y: int, x: int, data: List[float], width: int, label: str):
    """Draw a small sparkline graph (like ▁▃▄▅█)."""
    if not data:
        return
    max_val = max(data) if max(data) > 0 else 1
    bars = "▁▂▃▄▅▆▇█"
    spark = "".join(bars[min(int(v / max_val * (len(bars) - 1)), len(bars) - 1)] for v in data[-width:])
    line = f"{label:<10}: [{spark}]"
    try:
        stdscr.addstr(y, x, line[:width + 12])
    except:
        pass  # Ignore if out of bounds

def draw_dashboard(stdscr, history: dict, current: dict):
    """Draw the full terminal dashboard."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, -1, -1)           # Default
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_RED, -1)

    stdscr.clear()
    h, w = stdscr.getmaxyx()

    # Title
    title = " SYSTEM MONITOR (q to quit) "
    stdscr.addstr(0, 0, title.center(w), curses.A_BOLD | curses.color_pair(2))

    row = 2

    def print_stat(label: str, value: Any, unit: str = "", threshold=None, is_percent=False):
        nonlocal row
        if row >= h - 1:
            return
        text = f"{label:<20}: {value}{unit}"
        color = 1
        if threshold is not None:
            if is_percent and value > threshold * 0.95:
                color = 4
            elif is_percent and value > threshold * 0.8:
                color = 3
            elif value > threshold:
                color = 4
        stdscr.addstr(row, 2, text.ljust(w - 4), curses.color_pair(color))
        row += 1

    # System Stats
    print_stat("Timestamp", current["timestamp"][11:19])  # Time only
    print_stat("CPU Usage", f"{current['cpu']:.1f}", "%", CPU_THRESHOLD, True)
    print_stat("RAM Usage", f"{current['ram_percent']:.1f}", "%", RAM_THRESHOLD, True)
    print_stat("RAM Used", f"{current['ram_used_gb']:.2f}", " GB")
    print_stat("Disk Usage", f"{current['disk']:.1f}", "%", DISK_THRESHOLD, True)
    print_stat("Net Sent (Δ)", f"{current['net_sent']:.3f}", " MB")
    print_stat("Net Recv (Δ)", f"{current['net_recv']:.3f}", " MB")
    print_stat("Total Processes", current["processes"])

    if PROCESS_FILTER:
        row += 1
        stdscr.addstr(row, 2, f"--- Filtered: '{PROCESS_FILTER}' ---".center(w-4), curses.A_DIM)
        row += 1
        print_stat("Filtered CPU", f"{current['filtered_cpu']:.1f}", "%")
        print_stat("Filtered RAM", f"{current['filtered_ram_gb']:.2f}", " GB")

    row += 1

    # Sparkline Graphs
    graph_width = min(50, w - 25)
    metrics = [
        ("cpu", "CPU", history.get("cpu", [])),
        ("ram", "RAM", history.get("ram", [])),
        ("disk", "Disk", history.get("disk", [])),
    ]
    for metric, label, data in metrics:
        if row >= h - 1:
            break
        draw_sparkline(stdscr, row, 2, list(data), graph_width, label)
        row += 1

    # Footer
    footer = "Press 'q' to quit | Alerts: Email enabled" if ENABLE_EMAIL_ALERTS else "Alerts: Disabled"
    stdscr.addstr(h-1, 0, footer.center(w), curses.A_DIM)

    stdscr.refresh()

# ===================================================================
# ========================== MAIN LOOP ==============================
# ===================================================================

def main(stdscr):
    """Main function run by curses.wrapper()."""
    curses.curs_set(0)           # Hide cursor
    stdscr.nodelay(True)         # Non-blocking input

    # Data structures
    history: Dict[str, deque] = {}           # For graphs
    log_queue = deque(maxlen=1000)           # For CSV logger
    net_io_prev = {}                         # Track network delta

    # Start CSV logger in background
    CSVLogger(log_queue)

    # Initial dummy data
    current_data = {
        "timestamp": "0000-00-00T00:00:00",
        "cpu": 0.0, "ram_percent": 0.0, "ram_used_gb": 0.0,
        "disk": 0.0, "net_sent": 0.0, "net_recv": 0.0,
        "processes": 0, "filtered_cpu": 0.0, "filtered_ram_gb": 0.0
    }

    print("Starting system monitor... (press 'q' to quit)")

    while True:
        start_time = time.time()

        # Collect fresh data
        result = collect_data(history, net_io_prev, log_queue)
        net_io_prev = result["net_io_prev"]
        current_data = result["current"]
        log_queue.append(result["csv_row"])

        # Draw UI
        draw_dashboard(stdscr, history, current_data)

        # Sleep to maintain refresh rate
        elapsed = time.time() - start_time
        time.sleep(max(0, REFRESH_INTERVAL - elapsed))

        # Check for 'q' to quit
        key = stdscr.getch()
        if key == ord('q') or key == ord('Q'):
            break

# ===================================================================
# ========================== ENTRY POINT ============================
# ===================================================================

if __name__ == "__main__":
    print("System Monitor Starting...")
    print(f"Logging to: {os.path.abspath(LOG_FILE)}")
    if ENABLE_EMAIL_ALERTS:
        print(f"Email alerts enabled -> {RECIPIENT_EMAIL}")
    else:
        print("Email alerts disabled.")

    # Run with proper curses handling
    try:
        if sys.platform == "win32":
            # Windows needs special handling
            import curses
            curses.wrapper(main)
        else:
            curses.wrapper(main)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
    finally:
        print(f"Log saved to: {os.path.abspath(LOG_FILE)}")
