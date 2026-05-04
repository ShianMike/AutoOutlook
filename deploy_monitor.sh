#!/bin/bash
# Deploy monitoring dashboard to instance 1 (52.91.211.79)
# This creates a web interface accessible at http://52.91.211.79:5000

cat > /tmp/monitor_setup.sh << 'EOF'
#!/bin/bash
cd /home/ubuntu
pip install flask -q

cat > monitor.py << 'PYTHON'
from flask import Flask, jsonify
import subprocess
import os

app = Flask(__name__)

def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except:
        return "Error"

@app.route('/api/status')
def status():
    status = {
        "instances": {}
    }
    
    # Local instance status
    procs = run_cmd("pgrep -c python3")
    latest_log = run_cmd("tail -2 gather_*.log 2>/dev/null | tail -5")
    size = run_cmd("du -sh backend/ml_data/*.parquet 2>/dev/null | tail -1")
    
    status["instances"]["local"] = {
        "processes": procs,
        "log_tail": latest_log,
        "size": size
    }
    
    return jsonify(status)

@app.route('/')
def dashboard():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>AutoOutlook Gather Status</title>
        <style>
            body { font-family: Arial; margin: 20px; background: #f0f2f5; }
            .container { max-width: 900px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #1a73e8; margin-bottom: 5px; }
            .subtitle { color: #666; margin-bottom: 20px; }
            .status-box { background: #e8f5e9; padding: 15px; border-radius: 4px; margin: 15px 0; border-left: 4px solid #4CAF50; }
            .log { background: #f5f5f5; padding: 15px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-top: 15px; max-height: 200px; overflow-y: auto; border: 1px solid #ddd; }
            .refresh-time { color: #999; font-size: 12px; margin-top: 20px; }
            button { background: #1a73e8; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; }
            button:hover { background: #1557b0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🌪️ AutoOutlook Gather Status</h1>
            <p class="subtitle">Real-time monitoring of data collection</p>
            
            <button onclick="refreshData()">Refresh Status</button>
            
            <div id="content">Loading...</div>
            
            <p class="refresh-time">Auto-refreshes every 30 seconds</p>
        </div>
        
        <script>
        async function refreshData() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                let html = '<div class="status-box">';
                html += '<h2>Instance 1 - 2021 Mar-Jun</h2>';
                html += '<p><strong>Processes Running:</strong> ' + data.instances.local.processes + '</p>';
                html += '<p><strong>Output Size:</strong> ' + data.instances.local.size + '</p>';
                html += '<h3>Recent Log:</h3>';
                html += '<div class="log"><pre>' + data.instances.local.log_tail + '</pre></div>';
                html += '</div>';
                
                document.getElementById('content').innerHTML = html;
            } catch(e) {
                document.getElementById('content').innerHTML = '<p style="color:red;">Error: ' + e.message + '</p>';
            }
        }
        
        refreshData();
        setInterval(refreshData, 30000);
        </script>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
PYTHON

nohup python3 monitor.py > monitor.log 2>&1 &
echo "Monitor started on port 5000"
EOF

chmod +x /tmp/monitor_setup.sh
bash /tmp/monitor_setup.sh
