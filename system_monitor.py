#!/usr/bin/env python3
"""
ULTIMATE Web System Monitor
- Flask + SocketIO
- Live Chart.js graphs
- Top processes, Disk I/O, Uptime, Dark Mode
- CSV export, Email alerts
- Mobile-first, responsive
"""

import os
import csv
import time
import psutil
import threading
import datetime
import smtplib
from flask import Flask, render_template_string, send_from_directory
from flask_socketio import SocketIO
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import deque
from typing import Dict, List, Any

# ========================= CONFIG =========================
REFRESH_INTERVAL = 1.0
MAX_HISTORY = 200
PROCESS_FILTER = None  # e.g. "python", "chrome"

# Email
ENABLE_EMAIL_ALERTS = True
ALERT_COOLDOWN = 300
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_email@gmail.com"          # CHANGE ME
SENDER_PASSWORD = "your_app_password"          # CHANGE ME
RECIPIENT_EMAIL = "admin@yourdomain.com"       # CHANGE ME

# Thresholds
CPU_THRESHOLD = 90.0
RAM_THRESHOLD = 90.0
DISK_THRESHOLD = 95.0

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-2025'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Data
history: Dict[str, deque] = {}
net_io_prev = {"sent": 0, "recv": 0, "time": time.time()}
disk_io_prev = {"read": 0, "write": 0, "time": time.time()}
alert_manager = None
CSV_LOG = "web_monitor_pro.csv"
csv_lock = threading.Lock()

# ======================= EMAIL ALERTS =======================
class AlertManager:
    def __init__(self):
        self.last_sent = 0

    def can_send(self):
        now = time.time()
        if now - self.last_sent >= ALERT_COOLDOWN:
            self.last_sent = now
            return True
        return False

    def send(self, subject: str, body: str):
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
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
            server.quit()
            print(f"[ALERT] {subject}")
        except Exception as e:
            print(f"[EMAIL ERROR] {e}")

alert_manager = AlertManager()

# ======================= DATA COLLECTOR =======================
def collect_data() -> dict:
    now = datetime.datetime.now()
    ts = now.isoformat(timespec='seconds')
    current_time = time.time()

    # System
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    net = psutil.net_io_counters()
    disk_io = psutil.disk_io_counters()
    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = now - boot_time
    load = os.getloadavg() if hasattr(os, 'getloadavg') else (0, 0, 0)

    # Network speed
    elapsed_net = current_time - net_io_prev["time"]
    sent_mbps = (net.bytes_sent - net_io_prev["sent"]) / (1024**2) / max(elapsed_net, 0.1)
    recv_mbps = (net.bytes_recv - net_io_prev["recv"]) / (1024**2) / max(elapsed_net, 0.1)
    net_io_prev["sent"] = net.bytes_sent
    net_io_prev["recv"] = net.bytes_recv
    net_io_prev["time"] = current_time

    # Disk I/O speed
    elapsed_disk = current_time - disk_io_prev["time"]
    read_mbps = (disk_io.read_bytes - disk_io_prev["read"]) / (1024**2) / max(elapsed_disk, 0.1)
    write_mbps = (disk_io.write_bytes - disk_io_prev["write"]) / (1024**2) / max(elapsed_disk, 0.1)
    disk_io_prev["read"] = disk_io.read_bytes
    disk_io_prev["write"] = disk_io.write_bytes
    disk_io_prev["time"] = current_time

    # Top processes
    processes = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            info = p.info
            if info['cpu_percent'] is not None:
                processes.append({
                    'pid': info['pid'],
                    'name': info['name'],
                    'cpu': round(info['cpu_percent'], 1),
                    'ram': round(info['memory_info'].rss / (1024**2), 1)
                })
        except:
            continue
    top_cpu = sorted(processes, key=lambda x: x['cpu'], reverse=True)[:5]
    top_ram = sorted(processes, key=lambda x: x['ram'], reverse=True)[:5]

    # Filtered
    f_cpu = f_ram = 0
    for p in psutil.process_iter(['name', 'cpu_percent', 'memory_info']):
        try:
            if PROCESS_FILTER and PROCESS_FILTER.lower() not in p.info['name'].lower():
                continue
            f_cpu += p.info['cpu_percent'] or 0
            f_ram += p.info['memory_info'].rss
        except:
            continue
    f_ram_gb = round(f_ram / (1024**3), 2)

    # Update history
    for k, v in [
        ('cpu', cpu), ('ram', mem.percent), ('disk', disk.percent),
        ('sent', sent_mbps), ('recv', recv_mbps),
        ('read', read_mbps), ('write', write_mbps)
    ]:
        h = history.setdefault(k, deque(maxlen=MAX_HISTORY))
        h.append(round(v, 3))

    # CSV
    row = [
        ts, round(cpu,1), round(mem.percent,1), round(mem.used/(1024**3),2),
        round(disk.percent,1), round(sent_mbps,3), round(recv_mbps,3),
        round(read_mbps,3), round(write_mbps,3),
        len(psutil.pids()), round(f_cpu,1), f_ram_gb
    ]
    with csv_lock:
        write_header = not os.path.exists(CSV_LOG)
        with open(CSV_LOG, 'a', newline='') as f:
            w = csv.writer(f)
            if write_header:
                w.writerow([
                    'ts','cpu%','ram%','ram_gb','disk%','net_sent','net_recv',
                    'disk_read','disk_write','procs','f_cpu%','f_ram_gb'
                ])
            w.writerow(row)

    # Alerts
    alerts = []
    if cpu > CPU_THRESHOLD: alerts.append(f"CPU {cpu:.1f}%")
    if mem.percent > RAM_THRESHOLD: alerts.append(f"RAM {mem.percent:.1f}%")
    if disk.percent > DISK_THRESHOLD: alerts.append(f"Disk {disk.percent:.1f}%")
    if alerts and alert_manager.can_send():
        threading.Thread(target=alert_manager.send,
                         args=("HIGH USAGE ALERT", f"{ts}\n" + "\n".join(alerts)),
                         daemon=True).start()

    return {
        'ts': ts[11:19],
        'cpu': round(cpu,1),
        'ram_pct': round(mem.percent,1),
        'ram_gb': round(mem.used/(1024**3),2),
        'disk': round(disk.percent,1),
        'sent': round(sent_mbps,3),
        'recv': round(recv_mbps,3),
        'read': round(read_mbps,3),
        'write': round(write_mbps,3),
        'procs': len(psutil.pids()),
        'uptime': str(uptime).split('.')[0],
        'load': [round(x,2) for x in load],
        'top_cpu': top_cpu,
        'top_ram': top_ram,
        'f_cpu': round(f_cpu,1),
        'f_ram': f_ram_gb,
        'history': {k: list(v) for k, v in history.items()}
    }

# ======================= BACKGROUND =======================
def background_task():
    while True:
        data = collect_data()
        socketio.emit('update', data, broadcast=True)
        time.sleep(REFRESH_INTERVAL)

# ======================= HTML TEMPLATE =======================
HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>System Monitor Pro</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
  <style>
    :root { --bg: #f8f9fa; --card: white; --text: #222; --accent: #1976d2; }
    .dark { --bg: #121212; --card: #1e1e1e; --text: #e0e0e0; --accent: #90caf9; }
    body { margin:0; font-family: system-ui; background: var(--bg); color: var(--text); transition: 0.3s; }
    .container { max-width: 1400px; margin: auto; padding: 1rem; }
    h1 { text-align:center; margin:0.5rem 0; }
    .controls { text-align:center; margin:1rem 0; }
    button { padding:0.5rem 1rem; margin:0 0.5rem; border:none; border-radius:6px; background:var(--accent); color:white; cursor:pointer; }
    button:hover { filter: brightness(0.9); }
    .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px,1fr)); gap:1rem; }
    .card { background:var(--card); border-radius:12px; padding:1rem; box-shadow:0 2px 8px rgba(0,0,0,0.1); }
    .card h3 { margin:0 0 0.5rem; font-size:1rem; opacity:0.8; }
    .value { font-size:2rem; font-weight:bold; margin:0.5rem 0; }
    .high { color:#d32f2f; } .warn { color:#f9a825; }
    canvas { height:70px !important; }
    table { width:100%; border-collapse:collapse; font-size:0.9rem; }
    th, td { padding:0.4rem; text-align:left; border-bottom:1px solid rgba(0,0,0,0.1); }
    .footer { text-align:center; padding:1rem; font-size:0.8rem; opacity:0.7; }
  </style>
</head>
<body>
<div class="container">
  <h1>System Monitor Pro</h1>
  <div class="controls">
    <button onclick="downloadCSV()">Download CSV</button>
    <button onclick="toggleDark()">Dark Mode</button>
  </div>

  <div class="grid" id="stats"></div>

  <div class="footer" id="status">Connecting...</div>
</div>

<script>
const socket = io();
let charts = {};
let dark = false;

socket.on('connect', () => {
  document.getElementById('status').textContent = 'Live • Updated every second';
});
socket.on('update', d => updateAll(d));

function color(val, high, warn) {
  if (val > high) return 'high';
  if (val > warn) return 'warn';
  return '';
}

function updateAll(d) {
  const c = document.getElementById('stats');
  if (!c.innerHTML) initDashboard(c);

  // Update values
  document.getElementById('time').textContent = d.ts;
  document.getElementById('cpu').textContent = d.cpu; document.getElementById('cpu').className = 'value ' + color(d.cpu,90,70);
  document.getElementById('ram').textContent = d.ram_pct; document.getElementById('ram').className = 'value ' + color(d.ram_pct,90,70);
  document.getElementById('ramgb').textContent = d.ram_gb + ' GB';
  document.getElementById('disk').textContent = d.disk; document.getElementById('disk').className = 'value ' + color(d.disk,95,80);
  document.getElementById('sent').textContent = d.sent;
  document.getElementById('recv').textContent = d.recv;
  document.getElementById('read').textContent = d.read;
  document.getElementById('write').textContent = d.write;
  document.getElementById('procs').textContent = d.procs;
  document.getElementById('uptime').textContent = d.uptime;
  document.getElementById('load').textContent = d.load.join(' | ');

  {{#if process_filter}}
  document.getElementById('fcpu').textContent = d.f_cpu;
  document.getElementById('fram').textContent = d.f_ram + ' GB';
  {{/if}}

  // Top processes
  ['cpu', 'ram'].forEach(type => {
    const tbody = document.getElementById('top-' + type).querySelector('tbody');
    tbody.innerHTML = '';
    (d['top_' + type] || []).forEach(p => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${p.name}</td><td>${p.pid}</td><td>${type==='cpu'?p.cpu+'%':p.ram+' MB'}</td>`;
      tbody.appendChild(tr);
    });
  });

  // Charts
  ['cpu','ram','disk','sent','recv','read','write'].forEach(m => {
    if (charts[m]) {
      charts[m].data.labels = Array(d.history[m].length).fill('');
      charts[m].data.datasets[0].data = d.history[m];
      charts[m].update('quiet');
    }
  });
}

function initDashboard(container) {
  const cards = [
    {id:'time', title:'Time', big:true},
    {id:'cpu', title:'CPU %', chart:true},
    {id:'ram', title:'RAM %', chart:true},
    {id:'ramgb', title:'RAM Used'},
    {id:'disk', title:'Disk %', chart:true},
    {id:'sent', title:'Net Sent (MB/s)', chart:true},
    {id:'recv', title:'Net Recv (MB/s)', chart:true},
    {id:'read', title:'Disk Read (MB/s)', chart:true},
    {id:'write', title:'Disk Write (MB/s)', chart:true},
    {id:'procs', title:'Processes'},
    {id:'uptime', title:'Uptime'},
    {id:'load', title:'Load Avg (1/5/15)'},
    {{#if process_filter}}
    {id:'fcpu', title:'Filtered CPU %'},
    {id:'fram', title:'Filtered RAM (GB)'},
    {{/if}}
  ];

  cards.forEach(c => {
    const card = document.createElement('div'); card.className = 'card';
    card.innerHTML = `<h3>${c.title}</h3><div id="${c.id}" class="value">—</div>${c.chart?'<canvas id="chart-'+c.id+'"></canvas>':''}`;
    container.appendChild(card);
    if (c.big) card.querySelector('.value').style.fontSize = '2.4rem';
    if (c.chart) initChart(c.id);
  });

  // Top processes
  ['cpu', 'ram'].forEach(t => {
    const card = document.createElement('div'); card.className = 'card';
    card.innerHTML = `<h3>Top 5 by ${t.toUpperCase()}</h3><table id="top-${t}"><thead><tr><th>Name</th><th>PID</th><th>${t==='cpu'?'%':'MB'}</th></tr></thead><tbody></tbody></table>`;
    container.appendChild(card);
  });
}

function initChart(id) {
  const ctx = document.getElementById('chart-'+id).getContext('2d');
  charts[id] = new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: '#1976d2', fill: true, tension: 0.3, pointRadius: 0 }] },
    options: { animation: false, scales: { x:{display:false}, y:{display:false} }, plugins: { legend: {display:false} } }
  });
}

function downloadCSV() {
  fetch('/download').then(r=>r.blob()).then(b=>{
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = 'system_monitor_pro.csv';
    a.click();
  });
}

function toggleDark() {
  dark = !dark;
  document.body.classList.toggle('dark', dark);
}
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML, process_filter=PROCESS_FILTER)

@app.route('/download')
def download():
    return send_from_directory('.', CSV_LOG, as_attachment=True)

# ======================= START =======================
if __name__ == '__main__':
    print("Web Monitor Pro Starting...")
    print(f"Open: http://127.0.0.1:5000")
    print(f"Log: {os.path.abspath(CSV_LOG)}")
    threading.Thread(target=background_task, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
