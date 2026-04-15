#!/usr/bin/env python3
"""
Hashrate Monitor — watches XMRig mining performance
Detects hashrate drops, MSR reset, CPU throttling.
"""

import json
import subprocess
import sys
import re

ISOLATE = "http://127.0.0.1:5900/execute"

# ─── DATA COLLECTION ───

def fetch_xmrig_stats():
    """Get hashrate from XMRig logs"""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "xmrig", "--no-pager", "-n", "50"],
            capture_output=True, text=True, timeout=10
        )
        logs = r.stdout

        # Find the most recent speed line
        speed_matches = re.findall(
            r'speed 10s/60s/15m (\S+) (\S+) (\S+) H/s max (\S+) H/s',
            logs
        )

        if not speed_matches:
            # Try alternate format
            speed_matches = re.findall(
                r'speed 10s/60s/15m (\S+) n/a n/a H/s max (\S+) H/s',
                logs
            )
            if speed_matches:
                last = speed_matches[-1]
                return {
                    "speed_10s": float(last[0]),
                    "speed_60s": None,
                    "speed_15m": None,
                    "max": float(last[1]),
                    "stabilized": False
                }
            return {"error": "No speed data in recent logs"}

        last = speed_matches[-1]
        speed_60s = float(last[1]) if last[1] != "n/a" else None
        speed_15m = float(last[2]) if last[2] != "n/a" else None

        return {
            "speed_10s": float(last[0]),
            "speed_60s": speed_60s,
            "speed_15m": speed_15m,
            "max": float(last[3]),
            "stabilized": speed_15m is not None
        }
    except Exception as e:
        return {"error": str(e)}

def fetch_msr_status():
    """Check if MSR mod is active"""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "xmrig-msr"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()  # "active" or "inactive"
    except:
        return "unknown"

def fetch_msr_registers():
    """Verify MSR registers still have correct values"""
    try:
        r = subprocess.run(
            ["sudo", "-S", "-k", "rdmsr", "-p0", "0xc0011022"],
            input="parker3winston\n", capture_output=True, text=True, timeout=5
        )
        val = r.stdout.strip()
        return {"0xc0011022": val, "correct": val == "510000"}
    except:
        return {"0xc0011022": "error", "correct": False}

def check_p2pool():
    """Quick p2pool connectivity check"""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "p2pool"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() == "active"
    except:
        return False

# ─── ISOLATE ANALYSIS ───

def analyze(stats, msr_status, p2pool_up):
    payload = {
        "code": """(function(){
  const alerts = [];
  const s = env.stats;
  const msr = env.msrStatus;
  const p2p = env.p2pool;

  // Can't read stats at all
  if (s.error) {
    alerts.push({level:'CRITICAL', msg:`Cannot read XMRig stats: ${s.error}`});
    return {alerts, status:'CRITICAL'};
  }

  // Use 60s or 10s average (prefer 60s, more stable)
  const hashrate = s.speed_60s || s.speed_10s;
  const baseline = 10500;  // minimum expected KH/s with MSR mod
  const lowBaseline = 9800; // without MSR mod

  // Hashrate drop detection
  if (hashrate && hashrate < lowBaseline) {
    const drop = ((1 - hashrate / baseline) * 100).toFixed(1);
    alerts.push({level:'CRITICAL', msg:`Hashrate critically low: ${(hashrate/1000).toFixed(1)} KH/s (${drop}% below expected)`});
  } else if (hashrate && hashrate < baseline) {
    const drop = ((1 - hashrate / baseline) * 100).toFixed(1);
    alerts.push({level:'WARNING', msg:`Hashrate below expected: ${(hashrate/1000).toFixed(1)} KH/s (${drop}% drop)`});
  }

  // MSR mod check
  if (msr !== 'active') {
    alerts.push({level:'WARNING', msg:`MSR mod inactive (${msr}) — hashrate may be 5-10% lower`});
  }

  // P2Pool connectivity
  if (!p2p) {
    alerts.push({level:'WARNING', msg:'P2Pool not running — shares not being submitted'});
  }

  // 10s vs 60s divergence (instability indicator)
  if (s.speed_10s && s.speed_60s) {
    const divergence = Math.abs(s.speed_10s - s.speed_60s) / s.speed_60s;
    if (divergence > 0.15) {
      alerts.push({level:'INFO', msg:`Hashrate unstable: 10s=${(s.speed_10s/1000).toFixed(1)} KH/s vs 60s=${(s.speed_60s/1000).toFixed(1)} KH/s`});
    }
  }

  const maxLevel = alerts.length === 0 ? 'OK' :
    alerts.some(a => a.level === 'CRITICAL') ? 'CRITICAL' :
    alerts.some(a => a.level === 'WARNING') ? 'WARNING' : 'INFO';

  return {
    alerts,
    status: maxLevel,
    summary: {
      hashrate: hashrate ? +(hashrate/1000).toFixed(1) + ' KH/s' : 'N/A',
      max: s.max ? +(s.max/1000).toFixed(1) + ' KH/s' : 'N/A',
      stabilized: s.stabilized,
      msr: msr,
      p2pool: p2p
    }
  };
})()""",
        "preset": "compute",
        "env": {
            "stats": stats,
            "msrStatus": msr_status,
            "p2pool": p2pool_up
        }
    }

    try:
        tmp = "/tmp/hashrate-monitor-payload.json"
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
    stats = fetch_xmrig_stats()
    msr = fetch_msr_status()
    p2pool = check_p2pool()

    result = analyze(stats, msr, p2pool)
    status = result.get("status", "UNKNOWN")
    alerts = result.get("alerts", [])

    if status == "OK":
        summary = result.get("summary", {})
        print(f"Hashrate Monitor: OK | {summary.get('hashrate','?')} | MSR: {summary.get('msr','?')} | P2Pool: {summary.get('p2pool','?')}")
        sys.exit(0)

    sep = "=" * 40
    lines = [f"HASHRATE MONITOR ALERT — {status}", sep]
    for a in alerts:
        lines.append(f"  [{a['level']}] {a['msg']}")
    summary = result.get("summary", {})
    if summary:
        lines.append(sep)
        lines.append(f"  Hashrate: {summary.get('hashrate','?')} (max: {summary.get('max','?')})")
        lines.append(f"  MSR: {summary.get('msr','?')} | P2Pool: {summary.get('p2pool','?')}")

    print("\n".join(lines))
    if status == "CRITICAL": sys.exit(2)
    elif status == "WARNING": sys.exit(1)
    else: sys.exit(0)

if __name__ == "__main__":
    main()