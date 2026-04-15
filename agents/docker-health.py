#!/usr/bin/env python3
"""
Docker Health — checks Unraid containers aren't silently dead
Monitors container health status, restart counts, and uptime.
"""

import json
import subprocess
import sys
import re

KEY_CONTAINERS = ["gateway", "hummingbot-api", "hummingbot-postgres", "hummingbot-broker", "honcho-app-api-1", "honcho-app-database-1", "honcho-app-redis-1"]
ISOLATE = "http://127.0.0.1:5900/execute"

# ─── DATA COLLECTION ───

def fetch_container_details():
    """Get detailed container status from local Docker"""
    containers = {}
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Ports}}"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    name = parts[0].strip()
                    status = parts[1].strip() if len(parts) > 1 else "unknown"
                    ports = parts[2].strip() if len(parts) > 2 else ""
                    containers[name] = {"status": status, "ports": ports}
    except Exception as e:
        return {"_error": str(e)}
    return containers

def fetch_container_stats():
    """Get resource usage for running containers (local)"""
    stats = {}
    try:
        r = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    name = parts[0].strip()
                    stats[name] = {
                        "cpu": parts[1].strip() if len(parts) > 1 else "?",
                        "mem": parts[2].strip() if len(parts) > 2 else "?",
                        "memPct": parts[3].strip() if len(parts) > 3 else "?"
                    }
    except:
        pass
    return stats

def fetch_health_checks():
    """Check Docker health status of containers (local)"""
    health = {}
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Health}}"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    name = parts[0].strip()
                    h = parts[1].strip() if len(parts) > 1 else ""
                    if h and h != "()":
                        health[name] = h
    except:
        pass
    return health

# ─── ISOLATE ANALYSIS ───

def analyze(containers, stats, health):
    payload = {
        "code": """(function(){
  const alerts = [];
  const ctrs = env.containers;
  const sts = env.stats;
  const hlth = env.health;
  const keyContainers = env.keyContainers;

  // Connection check
  if (ctrs._error) {
    alerts.push({level:'CRITICAL', msg:`Cannot reach Unraid: ${ctrs._error}`});
    return {alerts, status:'CRITICAL'};
  }

  // Check key containers
  keyContainers.forEach(name => {
    if (!ctrs[name]) {
      alerts.push({level:'WARNING', msg:`Container ${name} NOT FOUND — may be removed or never started`});
    } else {
      const status = ctrs[name].status;
      if (status.includes('Exited') || status.includes('Dead') || status.includes('Stopped')) {
        alerts.push({level:'CRITICAL', msg:`Container ${name} is DOWN: ${status}`});
      } else if (status.includes('Up')) {
        // Check for high restart count
        const restartMatch = status.match(/Restarting\\((\\d+)\\)/);
        if (restartMatch) {
          const count = parseInt(restartMatch[1]);
          if (count > 5) {
            alerts.push({level:'CRITICAL', msg:`Container ${name} restart loop (${count} restarts)`});
          } else if (count > 2) {
            alerts.push({level:'WARNING', msg:`Container ${name} restarting (${count} times)`});
          }
        }

        // Check uptime — "Up 2 seconds" means just restarted
        const upMatch = status.match(/Up (\\d+) (second|minute)s?/);
        if (upMatch) {
          const val = parseInt(upMatch[1]);
          const unit = upMatch[2];
          if (unit === 'second' && val < 60) {
            alerts.push({level:'INFO', msg:`Container ${name} just restarted (Up ${val} seconds)`});
          }
        }
      } else {
        alerts.push({level:'WARNING', msg:`Container ${name} unusual status: ${status}`});
      }
    }
  });

  // Health check results
  Object.entries(hlth).forEach(([name, state]) => {
    if (state.includes('unhealthy')) {
      alerts.push({level:'WARNING', msg:`Container ${name} health: UNHEALTHY`});
    }
  });

  // Resource pressure — high memory or CPU
  Object.entries(sts).forEach(([name, s]) => {
    const memPct = parseFloat(s.memPct);
    if (!isNaN(memPct) && memPct > 80) {
      alerts.push({level:'WARNING', msg:`Container ${name} using ${s.memPct} memory (${s.mem})`});
    }
    const cpuPct = parseFloat(s.cpu);
    if (!isNaN(cpuPct) && cpuPct > 200) {
      alerts.push({level:'WARNING', msg:`Container ${name} CPU at ${s.cpu}`});
    }
  });

  const maxLevel = alerts.length === 0 ? 'OK' :
    alerts.some(a => a.level === 'CRITICAL') ? 'CRITICAL' :
    alerts.some(a => a.level === 'WARNING') ? 'WARNING' : 'INFO';

  const upCount = keyContainers.filter(n => ctrs[n] && ctrs[n].status.includes('Up')).length;
  return {
    alerts,
    status: maxLevel,
    summary: {
      keyContainersUp: `${upCount}/${keyContainers.length}`,
      totalContainers: Object.keys(ctrs).filter(k => k !== '_error').length
    }
  };
})()""",
        "preset": "compute",
        "env": {
            "containers": containers,
            "stats": stats,
            "health": health,
            "keyContainers": KEY_CONTAINERS
        }
    }

    try:
        tmp = "/tmp/docker-health-payload.json"
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
    containers = fetch_container_details()
    stats = fetch_container_stats()
    health = fetch_health_checks()

    result = analyze(containers, stats, health)
    status = result.get("status", "UNKNOWN")
    alerts = result.get("alerts", [])

    if status == "OK":
        summary = result.get("summary", {})
        print(f"Docker Health: OK | Key containers: {summary.get('keyContainersUp','?')} | Total: {summary.get('totalContainers',0)}")
        sys.exit(0)

    sep = "=" * 40
    lines = [f"DOCKER HEALTH ALERT — {status}", sep]
    for a in alerts:
        lines.append(f"  [{a['level']}] {a['msg']}")
    summary = result.get("summary", {})
    if summary:
        lines.append(sep)
        lines.append(f"  Key containers: {summary.get('keyContainersUp','?')}")
        lines.append(f"  Total on Unraid: {summary.get('totalContainers',0)}")

    print("\n".join(lines))
    if status == "CRITICAL": sys.exit(2)
    elif status == "WARNING": sys.exit(1)
    else: sys.exit(0)

if __name__ == "__main__":
    main()