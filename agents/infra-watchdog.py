#!/usr/bin/env python3
"""
Infra Watchdog — monitors all systemd services + key Docker containers on Unraid
Fetches status, sends to isolate for analysis, alerts on issues.
"""

import json
import subprocess
import sys

# ─── CONFIG ───

LOCAL_SERVICES = ["xmrig", "p2pool", "monero-wallet-rpc", "isolate-service", "hermes-webui"]
UNRAID_HOST = "10.10.1.191"
UNRAID_USER = "shawn"
KEY_CONTAINERS = ["gateway", "hummingbot-api", "hummingbot-postgres", "hummingbot-broker", "honcho-app-api-1"]
ISOLATE = "http://127.0.0.1:5900/execute"

# ─── DATA COLLECTION ───

def fetch_local_services():
    """Check systemd services"""
    status = {}
    try:
        for svc in LOCAL_SERVICES:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5
            )
            status[svc] = r.stdout.strip()
    except:
        pass
    return status

def fetch_local_containers():
    """Check key Docker containers locally"""
    status = {}
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|" in line:
                    name, state = line.split("|", 1)
                    status[name.strip()] = state.strip()
    except:
        pass
    return status

def fetch_unraid_daemon():
    """Quick check Unraid monerod is reachable"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-u", "shawn:parker3winston", "--digest",
             "-X", "POST", "http://10.10.1.191:18081/json_rpc",
             "-d", '{"jsonrpc":"2.0","id":"0","method":"get_info","params":{}}',
             "-H", "Content-Type: application/json"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        info = data.get("result", {})
        return {"reachable": True, "synced": info.get("synchronized", False),
                "height": info.get("height", 0)}
    except:
        return {"reachable": False}

def fetch_load():
    """System load and memory"""
    try:
        r = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
        load_str = r.stdout.strip()
        # Extract load average
        parts = load_str.split("load average:")
        load_avg = parts[-1].strip().split(",")[0] if len(parts) > 1 else "0"
        return {"load": float(load_avg), "raw": load_str}
    except:
        return {"load": 0, "raw": "unknown"}

def fetch_disk():
    """Disk usage for / and /data"""
    disks = {}
    try:
        for mount in ["/", "/data"]:
            r = subprocess.run(
                ["df", "-h", mount],
                capture_output=True, text=True, timeout=5
            )
            lines = r.stdout.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 5:
                    disks[mount] = {"used_pct": parts[4].replace("%",""), "avail": parts[3]}
    except:
        pass
    return disks

# ─── ISOLATE ANALYSIS ───

def analyze(local_svc, containers, unraid, load, disk):
    payload = {
        "code": """(function(){
  const alerts = [];
  const local = env.localServices;
  const ctrs = env.containers;
  const unraid = env.unraid;
  const ld = env.load;
  const dk = env.disk;

  // Check local systemd services
  Object.entries(local).forEach(([name, state]) => {
    if (state !== 'active') {
      alerts.push({level:'WARNING', msg:`Service ${name} is ${state} (expected active)`});
    }
  });

  // Check local Docker containers
  const keyContainers = env.keyContainers;
  keyContainers.forEach(name => {
    if (!ctrs[name]) {
      alerts.push({level:'WARNING', msg:`Container ${name} NOT FOUND — may be removed`});
    } else if (!ctrs[name].includes('Up')) {
      alerts.push({level:'WARNING', msg:`Container ${name} down: ${ctrs[name]}`});
    }
  });

  // Unraid monerod check
  if (!unraid.reachable) {
    alerts.push({level:'WARNING', msg:'Unraid monerod unreachable — wallet-rpc and mining may fail'});
  } else if (!unraid.synced) {
    alerts.push({level:'WARNING', msg:`Unraid monerod not synced (height: ${unraid.height})`});
  }

  // High load check (>50 is concerning on this 16-core system)
  if (ld.load > 50) {
    alerts.push({level:'WARNING', msg:`System load very high: ${ld.load}`});
  }

  // Disk space checks
  Object.entries(dk).forEach(([mount, info]) => {
    const pct = parseInt(info.used_pct);
    if (pct > 90) {
      alerts.push({level:'CRITICAL', msg:`Disk ${mount} is ${pct}% full — ${info.avail} remaining`});
    } else if (pct > 80) {
      alerts.push({level:'WARNING', msg:`Disk ${mount} is ${pct}% full`});
    }
  });

  const maxLevel = alerts.length === 0 ? 'OK' :
    alerts.some(a => a.level === 'CRITICAL') ? 'CRITICAL' :
    alerts.some(a => a.level === 'WARNING') ? 'WARNING' : 'OK';

  return {
    alerts,
    status: maxLevel,
    summary: {
      localServices: Object.entries(local).map(([n,s]) => `${n}=${s}`).join(', '),
      loadAvg: ld.load,
      containerCount: Object.keys(ctrs).length
    }
  };
})()""",
        "preset": "compute",
        "env": {
            "localServices": local_svc,
            "containers": containers,
            "keyContainers": KEY_CONTAINERS,
            "unraid": unraid,
            "load": load,
            "disk": disk
        }
    }

    try:
        tmp = "/tmp/infra-watchdog-payload.json"
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", ISOLATE,
             "-H", "Content-Type: application/json",
             "-d", f"@{tmp}"],
            capture_output=True, text=True, timeout=5
        )
        result = json.loads(r.stdout)
        if result.get("success"):
            return result["result"]
        else:
            return {"alerts": [{"level": "CRITICAL", "msg": f"Isolate failed: {result.get('error')}"}],
                    "status": "CRITICAL"}
    except Exception as e:
        return {"alerts": [{"level": "CRITICAL", "msg": f"Analysis failed: {e}"}],
                "status": "CRITICAL"}

# ─── MAIN ───

def main():
    local = fetch_local_services()
    containers = fetch_local_containers()
    unraid = fetch_unraid_daemon()
    load = fetch_load()
    disk = fetch_disk()

    result = analyze(local, containers, unraid, load, disk)
    status = result.get("status", "UNKNOWN")
    alerts = result.get("alerts", [])

    if status == "OK":
        summary = result.get("summary", {})
        print(f"Infra Watchdog: OK | Load: {summary.get('loadAvg','?')} | Containers: {summary.get('containerCount',0)}")
        sys.exit(0)

    sep = "=" * 40
    lines = [f"INFRA WATCHDOG ALERT — {status}", sep]
    for a in alerts:
        lines.append(f"  [{a['level']}] {a['msg']}")
    summary = result.get("summary", {})
    if summary:
        lines.append(sep)
        lines.append(f"  Services: {summary.get('localServices','?')}")
        lines.append(f"  Load: {summary.get('loadAvg','?')} | Unraid containers: {summary.get('containerCount',0)}")

    print("\n".join(lines))
    if status == "CRITICAL": sys.exit(2)
    elif status == "WARNING": sys.exit(1)
    else: sys.exit(0)

if __name__ == "__main__":
    main()