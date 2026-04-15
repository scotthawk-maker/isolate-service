#!/usr/bin/env python3
"""
LP Sentinel — Orca LP position monitor
Fetches data, sends to isolate for smart threshold logic, alerts via Telegram on issues.

Runs via cron every 5 minutes.
"""

import json
import subprocess
import sys
import os
from datetime import datetime

PAUSE_FILE = "/tmp/lp-sentinel-paused"

WALLET = "Hqf8a2Ryxeb15wNcXSXzAemBeY9VtcSrW5wE6UpcSrnG"
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
ISOLATE = "http://127.0.0.1:5900/execute"
GATEWAY = "http://localhost:15888"

# ─── DATA COLLECTION ───

def fetch_position():
    """Get Orca LP position from Gateway"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-H", "X-Gateway-Auth: admin",
             f"{GATEWAY}/connectors/orca/clmm/positions-owned?network=mainnet-beta&poolAddress={POOL}"],
            capture_output=True, text=True, timeout=15
        )
        positions = json.loads(r.stdout)
        return positions[0] if positions else None
    except Exception as e:
        return {"error": str(e)}

def fetch_sol_balance():
    """Get wallet SOL balance from Solana RPC"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", "https://api.mainnet-beta.solana.com",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                               "params": [WALLET]})],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        return data.get("result", {}).get("value", 0) / 1e9
    except:
        return -1

def fetch_service_status():
    """Check if key services are running"""
    services = {}
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "xmrig", "p2pool", "monero-wallet-rpc",
             "isolate-service", "hermes-webui"],
            capture_output=True, text=True, timeout=5
        )
        for line, svc in zip(r.stdout.strip().split("\n"),
                              ["xmrig", "p2pool", "wallet-rpc", "isolate", "webui"]):
            services[svc] = line.strip() == "active"
    except:
        pass
    return services

# ─── ISOLATE THRESHOLD LOGIC ───

def analyze(pos, sol_balance, services):
    """Send data to isolate for smart analysis"""
    payload = {
        "code": """(function(){
  const p = env.pos;
  const s = env.solBalance;
  const svc = env.services;
  const alerts = [];

  // No position = critical
  if (!p || p.error) {
    alerts.push({level:'CRITICAL', msg:'No LP position found — bot may have crashed or position closed'});
    return {alerts, status:'CRITICAL'};
  }

  // Position analysis
  const inRange = p.price >= p.lowerPrice && p.price <= p.upperPrice;
  const totalValue = (p.baseTokenAmount * p.price) + p.quoteTokenAmount;
  const rangeWidth = ((p.upperPrice - p.lowerPrice) / p.lowerPrice * 100);
  const distToLower = ((p.price - p.lowerPrice) / p.lowerPrice * 100);
  const distToUpper = ((p.upperPrice - p.price) / p.price * 100);

  // Check: out of range
  if (!inRange) {
    const direction = p.price > p.upperPrice ? 'ABOVE' : 'BELOW';
    alerts.push({level:'WARNING', msg:`LP OUT OF RANGE (${direction}) — price $${p.price.toFixed(2)} vs range $${p.lowerPrice.toFixed(2)}-$${p.upperPrice.toFixed(2)}`});
  }

  // Check: SOL buffer low
  if (s >= 0 && s < 0.05) {
    alerts.push({level:'CRITICAL', msg:`SOL buffer critically low: ${s.toFixed(4)} SOL — cannot rebalance if position goes OOR`});
  } else if (s >= 0 && s < 0.1) {
    alerts.push({level:'WARNING', msg:`SOL buffer low: ${s.toFixed(4)} SOL — may not be enough for rebalance`});
  }

  // Check: position value dropped significantly from expected
  // side=1 positions are ~$40 by design, only warn if way below
  if (totalValue < 20) {
    alerts.push({level:'WARNING', msg:`Position value critically low: $${totalValue.toFixed(2)}`});
  }

  // Check: near range edge (within 0.1% of boundary)
  if (inRange && distToLower < 0.1) {
    alerts.push({level:'INFO', msg:`Price near lower range edge (${distToLower.toFixed(3)}% away)`});
  }
  if (inRange && distToUpper < 0.1) {
    alerts.push({level:'INFO', msg:`Price near upper range edge (${distToUpper.toFixed(3)}% away)`});
  }

  // Check: services down
  Object.entries(svc).forEach(([name, up]) => {
    if (!up) alerts.push({level:'WARNING', msg:`Service DOWN: ${name}`});
  });

  const maxLevel = alerts.length === 0 ? 'OK' :
    alerts.some(a => a.level === 'CRITICAL') ? 'CRITICAL' :
    alerts.some(a => a.level === 'WARNING') ? 'WARNING' : 'INFO';

  return {
    alerts,
    status: maxLevel,
    summary: {
      price: +p.price.toFixed(2),
      range: `$${p.lowerPrice.toFixed(2)}-$${p.upperPrice.toFixed(2)}`,
      inRange,
      totalValue: +totalValue.toFixed(2),
      solBuffer: +(s).toFixed(4),
      rangeWidth: +rangeWidth.toFixed(3)
    }
  };
})()""",
        "preset": "compute",
        "env": {
            "pos": pos if pos and not (isinstance(pos, dict) and "error" in pos) else None,
            "solBalance": sol_balance,
            "services": services
        }
    }

    try:
        tmp = "/tmp/lp-sentinel-payload.json"
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
            return {"alerts": [{"level": "CRITICAL", "msg": f"Isolate engine failed: {result.get('error')}"}],
                    "status": "CRITICAL"}
    except Exception as e:
        return {"alerts": [{"level": "CRITICAL", "msg": f"Analysis failed: {e}"}],
                "status": "CRITICAL"}

# ─── MAIN ───

def main():
    # Check if sentinel is paused
    if os.path.exists(PAUSE_FILE):
        with open(PAUSE_FILE, 'r') as f:
            reason = f.read().strip()
        ts = os.path.getmtime(PAUSE_FILE)
        since = datetime.fromtimestamp(ts).strftime("%H:%M UTC")
        print(f"LP Sentinel: PAUSED since {since} — {reason}")
        sys.exit(0)

    pos = fetch_position()
    sol = fetch_sol_balance()
    svc = fetch_service_status()

    result = analyze(pos, sol, svc)
    status = result.get("status", "UNKNOWN")
    alerts = result.get("alerts", [])

    # Only output if there are actionable alerts (skip INFO when OK)
    if status == "OK":
        # All good — only print for cron logs
        summary = result.get("summary", {})
        print(f"LP Sentinel: OK | SOL ${summary.get('price','?')} | {summary.get('range','?')} | ${summary.get('totalValue','?')}")
        sys.exit(0)

    # Build alert message
    sep = "=" * 40
    lines = [f"LP SENTINEL ALERT — {status}", sep]
    for a in alerts:
        lines.append(f"  [{a['level']}] {a['msg']}")
    summary = result.get("summary", {})
    if summary:
        lines.append(sep)
        lines.append(f"  SOL: ${summary.get('price','?')} | Range: {summary.get('range','?')}")
        lines.append(f"  In Range: {summary.get('inRange','?')} | Value: ${summary.get('totalValue','?')}")
        lines.append(f"  SOL Buffer: {summary.get('solBuffer','?')} SOL")

    msg = "\n".join(lines)
    print(msg)

    # Exit with code based on severity (cron can use this)
    if status == "CRITICAL":
        sys.exit(2)
    elif status == "WARNING":
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()