"""
Remote monitoring dashboard for AWS gather instances
Run: python monitor.py
Access: http://localhost:5000 (or http://<instance-ip>:5000 from anywhere)
"""
import os
import json
import subprocess
from flask import Flask, jsonify, render_string
from pathlib import Path
from datetime import datetime

app = Flask(__name__)

# AWS Instance config
INSTANCES = {
    "52.91.211.79": {"name": "2021 Mar-Jun", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
    "52.90.32.39": {"name": "2022 Mar-Jun", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
    "54.235.227.33": {"name": "2023 Mar-Jun", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
    "18.207.205.132": {"name": "2024 Mar-Jun", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
    "100.54.235.127": {"name": "2021+2022 Jul-Oct", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
    "44.201.175.15": {"name": "2023+2024 Jul-Oct", "key": r"C:\Users\shian\.ssh\autooutlook-gather.pem"},
}

def ssh_run(ip, command, key):
    """Execute command on remote instance via SSH"""
    try:
        cmd = f'ssh -i "{key}" -o StrictHostKeyChecking=no ubuntu@{ip} \'{command}\''
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/api/status')
def get_status():
    """Get status of all 8 gathers"""
    status = {}
    for ip, info in INSTANCES.items():
        try:
            # Get process count
            proc_count = ssh_run(ip, "pgrep -c python3", info["key"])
            
            # Get log tail
            if "2021" in ip and "100" not in ip:  # Mar-Jun instances
                log_cmd = "tail -3 gather_2021.log 2>/dev/null || echo 'No log'"
            elif "2022" in ip and "100" not in ip:
                log_cmd = "tail -3 gather_2022.log 2>/dev/null || echo 'No log'"
            elif "2023" in ip and "44" not in ip:
                log_cmd = "tail -3 gather_2023.log 2>/dev/null || echo 'No log'"
            elif "2024" in ip:
                log_cmd = "tail -3 gather_2024.log 2>/dev/null || echo 'No log'"
            else:  # JASO instances - get latest log
                log_cmd = "ls -t gather*.log 2>/dev/null | head -1 | xargs tail -3 2>/dev/null || echo 'No log'"
            
            log_output = ssh_run(ip, log_cmd, info["key"])
            
            # Get parquet file size
            size_cmd = "du -sh backend/ml_data/*.parquet 2>/dev/null | tail -1 | awk '{print $1}' || echo 'Generating...'"
            size = ssh_run(ip, size_cmd, info["key"])
            
            status[ip] = {
                "name": info["name"],
                "processes": proc_count,
                "running": proc_count != "0",
                "log_tail": log_output.split('\n')[-3:],
                "output_size": size,
                "checked_at": datetime.now().isoformat()
            }
        except Exception as e:
            status[ip] = {"name": info["name"], "error": str(e)}
    
    return jsonify(status)

@app.route('/api/download/<year>/<season>')
def download_parquet(year, season):
    """Download parquet from instance"""
    # Map year/season to instance IP
    mapping = {
        "2021": {"jaso": "100.54.235.127", "mj": "52.91.211.79"},
        "2022": {"jaso": "100.54.235.127", "mj": "52.90.32.39"},
        "2023": {"jaso": "44.201.175.15", "mj": "54.235.227.33"},
        "2024": {"jaso": "44.201.175.15", "mj": "18.207.205.132"},
    }
    
    ip = mapping.get(year, {}).get("jaso" if season == "jaso" else "mj")
    if not ip:
        return {"error": "Invalid year/season"}, 400
    
    output_file = f"archive_{year}_{season}.parquet"
    try:
        cmd = f'scp -i "{INSTANCES[ip]["key"]}" -o StrictHostKeyChecking=no ubuntu@{ip}:/home/ubuntu/backend/ml_data/{output_file} backend/ml_data/'
        subprocess.run(cmd, shell=True, check=True, timeout=300)
        return {"status": "Downloaded", "file": output_file}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/')
def dashboard():
    """HTML dashboard"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AutoOutlook - Gather Monitor</title>
        <style>
            body { font-family: Arial; margin: 20px; background: #f5f5f5; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #333; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .card.running { border-left: 4px solid #4CAF50; }
            .card.stopped { border-left: 4px solid #f44336; }
            .status { font-size: 14px; margin: 10px 0; }
            .running-badge { background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
            .stopped-badge { background: #f44336; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
            .log { background: #f9f9f9; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 10px; max-height: 100px; overflow-y: auto; }
            button { background: #2196F3; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
            button:hover { background: #0b7dda; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🌪️ AutoOutlook Gather Monitor</h1>
            <p>Real-time status of 8 parallel data gathering instances</p>
            <button onclick="refreshStatus()">Refresh Now</button>
            <div class="grid" id="status-grid"></div>
        </div>
        
        <script>
        async function refreshStatus() {
            const response = await fetch('/api/status');
            const data = await response.json();
            
            let html = '';
            for (const [ip, info] of Object.entries(data)) {
                const running = info.running ? 'running' : 'stopped';
                const badge = running ? 
                    `<span class="running-badge">✓ RUNNING</span>` : 
                    `<span class="stopped-badge">✗ STOPPED</span>`;
                
                html += `
                <div class="card ${running}">
                    <h3>${info.name}</h3>
                    <p><strong>IP:</strong> ${ip}</p>
                    ${badge}
                    <div class="status">
                        <p><strong>Processes:</strong> ${info.processes}</p>
                        <p><strong>Output Size:</strong> ${info.output_size}</p>
                        <p><small>Last checked: ${new Date(info.checked_at).toLocaleTimeString()}</small></p>
                    </div>
                    <div class="log"><pre>${info.log_tail.join('\\n')}</pre></div>
                </div>
                `;
            }
            
            document.getElementById('status-grid').innerHTML = html;
        }
        
        // Auto-refresh every 30 seconds
        refreshStatus();
        setInterval(refreshStatus, 30000);
        </script>
    </body>
    </html>
    """
    return render_string(html)

if __name__ == '__main__':
    # Run on 0.0.0.0 to allow remote access
    app.run(host='0.0.0.0', port=5000, debug=False)
