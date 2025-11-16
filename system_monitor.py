# final_system_monitor.py
"""
Ultimate Python Web System Monitor
Features:
- Live monitoring: CPU, RAM, Disk, Disk I/O, Network I/O
- Live charts for all metrics using Chart.js
- Top 20 processes list
- High usage alerts with color coding
- CSV export and automatic logging every 10 seconds
- Modern GUI using CSS cards, charts, tables
- Fully commented for learning
"""

from flask import Flask, render_template_string, send_file
from flask_socketio import SocketIO
import subprocess, threading, time, csv
from datetime import datetime

# ------------------------- Flask & SocketIO setup -------------------------
app = Flask(__name__)
socketio = SocketIO(app)

# ------------------------- HTML + CSS + JS Template -------------------------
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Ultimate System Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        body { font-family: Arial; margin:0; background:#f4f4f4; }
        h1 { text-align:center; padding:20px; }
        .metrics { display:flex; justify-content: space-around; flex-wrap:wrap; margin:20px; }
        .card { background:#fff; padding:20px; margin:10px; border-radius:10px; width:200px; text-align:center;
                box-shadow:0 4px 6px rgba(0,0,0,0.1);}
        .value { font-size:24px; font-weight:bold; margin-top:10px; }
        .table-container { max-height:250px; overflow-y:auto; margin:20px; }
        table { width:100%; border-collapse:collapse; }
        th, td { border:1px solid #ccc; padding:8px; text-align:left; }
        th { background:#eee; position:sticky; top:0; }
        canvas { background:#fff; margin:20px auto; display:block; border-radius:10px; padding:10px; }
        button { padding:10px 20px; margin:20px; border:none; border-radius:5px; background:#28a745; color:#fff; cursor:pointer; }
        button:hover { background:#218838; }
        .green { color:green; font-weight:bold; }
        .yellow { color:orange; font-weight:bold; }
        .red { color:red; font-weight:bold; }
    </style>
</head>
<body>
<h1>Ultimate System Monitor</h1>

<!-- Metrics Cards -->
<div class="metrics">
    <div class="card">CPU Usage<div class="value" id="cpu">--%</div></div>
    <div class="card">RAM Usage<div class="value" id="ram">--%</div></div>
    <div class="card">Disk Usage<div class="value" id="disk">--%</div></div>
    <div class="card">Disk I/O<div class="value" id="diskio">-- B/s</div></div>
    <div class="card">Network I/O<div class="value" id="netio">-- B</div></div>
    <div class="card">Uptime<div class="value" id="uptime">--</div></div>
</div>

<!-- Charts -->
<canvas id="cpuChart" width="600" height="200"></canvas>
<canvas id="ramChart" width="600" height="200"></canvas>
<canvas id="diskChart" width="600" height="200"></canvas>
<canvas id="diskioChart" width="600" height="200"></canvas>
<canvas id="netioChart" width="600" height="200"></canvas>

<!-- Processes Table -->
<h2 style="text-align:center;">Top Processes</h2>
<div class="table-container">
    <table id="processes">
        <thead><tr><th>PID</th><th>Name</th></tr></thead>
        <tbody></tbody>
    </table>
</div>

<!-- CSV Export -->
<div style="text-align:center;"><button onclick="window.location.href='/export_csv'">Export CSV</button></div>

<script>
    // ------------------------- Chart.js Setup -------------------------
    const labels = Array.from({length:30}, (_,i)=> -29+i); // last 30 points
    let cpuData = Array(30).fill(0);
    let ramData = Array(30).fill(0);
    let diskData = Array(30).fill(0);
    let diskioData = Array(30).fill(0);
    let netioData = Array(30).fill(0);

    function createChart(id,label,color,data){
        return new Chart(document.getElementById(id),{
            type:'line',
            data:{labels:labels,datasets:[{label:label,data:data,borderColor:color,fill:false}]},
            options:{responsive:true,animation:false,scales:{y:{min:0}}}
        });
    }

    const cpuChart = createChart('cpuChart','CPU %','green',cpuData);
    const ramChart = createChart('ramChart','RAM %','blue',ramData);
    const diskChart = createChart('diskChart','Disk %','orange',diskData);
    const diskioChart = createChart('diskioChart','Disk I/O','purple',diskioData);
    const netioChart = createChart('netioChart','Network I/O','brown',netioData);

    // ------------------------- SocketIO -------------------------
    var socket = io();
    socket.on('update', function(data){
        function colorClass(val){
            if(val<60) return 'green';
            if(val<85) return 'yellow';
            return 'red';
        }

        // Update cards with alert colors
        document.getElementById('cpu').innerHTML = `<span class="${colorClass(data.cpu)}">${data.cpu}%</span>`;
        document.getElementById('ram').innerHTML = `<span class="${colorClass(data.ram)}">${data.ram}%</span>`;
        document.getElementById('disk').innerHTML = `<span class="${colorClass(parseFloat(data.disk.split(":")[0]))}">${data.disk}</span>`;
        document.getElementById('diskio').innerText = data.diskio;
        document.getElementById('netio').innerText = data.netio;
        document.getElementById('uptime').innerText = data.uptime;

        // Update process table
        let tbody = document.querySelector("#processes tbody");
        tbody.innerHTML = "";
        data.processes.forEach(p=>{
            let row = tbody.insertRow();
            row.insertCell(0).innerText = p.pid;
            row.insertCell(1).innerText = p.name;
        });

        // Update charts
        cpuData.push(data.cpu); cpuData.shift(); cpuChart.update();
        ramData.push(data.ram); ramData.shift(); ramChart.update();
        diskData.push(parseFloat(data.disk.split(":")[0])); diskData.shift(); diskChart.update();
        diskioData.push(parseFloat(data.diskio.split(" ")[0])); diskioData.shift(); diskioChart.update();
        netioData.push(parseFloat(data.netio.split(" ")[1])); netioData.shift(); netioChart.update();
    });
</script>
</body>
</html>
"""

# ------------------------- System Data Functions -------------------------
def get_cpu_usage():
    """Return CPU usage %"""
    try:
        output = subprocess.check_output("wmic cpu get loadpercentage", shell=True)
        for l in output.decode().splitlines():
            if l.strip().isdigit(): return int(l.strip())
    except: return 0

def get_ram_usage():
    """Return RAM usage %"""
    try:
        output = subprocess.check_output("wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", shell=True)
        free = total = 0
        for l in output.decode().splitlines():
            if "FreePhysicalMemory" in l: free=int(l.split("=")[1])
            if "TotalVisibleMemorySize" in l: total=int(l.split("=")[1])
        return round((1-free/total)*100,2)
    except: return 0

def get_disk_usage():
    """Return disk usage % per drive"""
    try:
        output = subprocess.check_output("wmic logicaldisk get name,size,freespace", shell=True)
        disks=[]
        for l in output.decode().splitlines()[1:]:
            parts=l.split()
            if len(parts)>=3:
                name,free,size=parts[0],parts[1],parts[2]
                usage=round((1-int(free)/int(size))*100,2)
                disks.append(f"{name}: {usage}%")
        return ", ".join(disks)
    except: return "N/A"

def get_disk_io():
    """Return Disk I/O B/s"""
    try:
        output = subprocess.check_output('typeperf "\\PhysicalDisk(_Total)\\Disk Bytes/sec" -sc 1', shell=True)
        val=float(output.decode().splitlines()[2].split(',')[1].replace('"',''))
        return f"{val:.2f} B/s"
    except: return "0 B/s"

def get_net_io():
    """Return Network I/O: Received/Sent"""
    try:
        output = subprocess.check_output("netstat -e", shell=True)
        lines=output.decode().splitlines()
        for i,l in enumerate(lines):
            if "Bytes" in l:
                vals=lines[i+1].split()
                return f"Received: {vals[0]} Sent: {vals[1]}"
        return "0 0"
    except: return "0 0"

def get_process_list():
    """Return top 20 processes"""
    procs=[]
    try:
        output=subprocess.check_output("tasklist", shell=True)
        for l in output.decode().splitlines()[3:]:
            if l.strip()=="": continue
            parts=l.split()
            procs.append({"pid":parts[1],"name":parts[0]})
        return procs[:20]
    except: return []

def get_uptime():
    """Return uptime string"""
    try:
        output=subprocess.check_output('wmic os get lastbootuptime /Value', shell=True)
        boot_str=output.decode().split("=")[1].strip()
        boot=datetime.strptime(boot_str[:14],"%Y%m%d%H%M%S")
        uptime_sec=(datetime.now()-boot).total_seconds()
        h, rem = divmod(uptime_sec,3600); m,s=divmod(rem,60)
        return f"{int(h)}h {int(m)}m {int(s)}s"
    except: return "N/A"

# ------------------------- Background Monitoring Thread -------------------------
def monitor():
    """Continuously collect system data, emit to frontend, auto-log CSV"""
    while True:
        data = {
            "cpu": get_cpu_usage(),
            "ram": get_ram_usage(),
            "disk": get_disk_usage(),
            "diskio": get_disk_io(),
            "netio": get_net_io(),
            "processes": get_process_list(),
            "uptime": get_uptime()
        }
        # Emit to frontend
        socketio.emit('update', data)

        # Auto-logging CSV every 10 seconds
        if int(time.time()) % 10 == 0:
            with open("auto_log.csv","a",newline="") as f:
                writer=csv.writer(f)
                writer.writerow([datetime.now(),data["cpu"],data["ram"],data["disk"],data["diskio"],data["netio"],data["uptime"]])
        time.sleep(1)

# ------------------------- Routes -------------------------
@app.route('/')
def index(): return render_template_string(HTML)

@app.route('/export_csv')
def export_csv():
    """Export current metrics as CSV"""
    filename="system_report.csv"
    fields=["CPU","RAM","Disk","DiskIO","NetIO","Uptime"]
    data=[[get_cpu_usage(),get_ram_usage(),get_disk_usage(),get_disk_io(),get_net_io(),get_uptime()]]
    with open(filename,'w',newline='') as f:
        writer=csv.writer(f)
        writer.writerow(fields)
        writer.writerows(data)
    return send_file(filename,as_attachment=True)

# ------------------------- Run App -------------------------
if __name__=="__main__":
    threading.Thread(target=monitor,daemon=True).start()
    socketio.run(app,debug=True)


