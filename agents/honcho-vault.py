#!/usr/bin/env python3
"""
Honcho Memory Vault — periodically snapshots system state into Honcho conclusions.
Preserves durable context across sessions that Hermes memory can't hold.
"""

import json
import subprocess
import re
import sys
from datetime import datetime

WALLET = "Hqf8a2Ryxeb15wNcXSXzAemBeY9VtcSrW5wE6UpcSrnG"
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
GATEWAY = "http://localhost:15888"

# ─── DATA COLLECTION ───

def fetch_position():
    try:
        r = subprocess.run(
            ["curl", "-s", "-H", "X-Gateway-Auth: admin",
             f"{GATEWAY}/connectors/orca/clmm/positions-owned?network=mainnet-beta&poolAddress={POOL}"],
            capture_output=True, text=True, timeout=15
        )
        positions = json.loads(r.stdout)
        return positions[0] if positions else None
    except:
        return None

def fetch_sol_balance():
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

def fetch_hashrate():
    try:
        r = subprocess.run(
            ["journalctl", "-u", "xmrig", "--no-pager", "-n", "10"],
            capture_output=True, text=True, timeout=10
        )
        match = re.findall(r'speed 10s/60s/15m (\S+) (\S+) (\S+) H/s', r.stdout)
        if match:
            last = match[-1]
            speed_60s = last[1] if last[1] != "n/a" else last[0]
            return float(speed_60s) / 1000
        match = re.findall(r'speed 10s/60s/15m (\S+) n/a n/a H/s', r.stdout)
        if match:
            return float(match[-1]) / 1000
        return None
    except:
        return None

def fetch_services():
    status = {}
    try:
        for svc in ["xmrig", "p2pool", "monero-wallet-rpc", "isolate-service", "hermes-webui"]:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            status[svc] = r.stdout.strip()
    except:
        pass
    return status

def fetch_containers():
    status = {}
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    status[parts[0].strip()] = parts[1].strip()
    except:
        pass
    return status

def fetch_unraid():
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
        return f"height {info.get('height',0):,}, synced={info.get('synchronized',False)}"
    except:
        return "unreachable"

def fetch_sol_price():
    """Get SOL price from position data or DeFiLlama"""
    pos = fetch_position()
    if pos and "price" in pos:
        return pos["price"]
    # Fallback
    try:
        r = subprocess.run(
            ["curl", "-s", "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        return data.get("solana", {}).get("usd")
    except:
        return None

def fetch_p2pool_shares():
    """Check p2pool status for share count"""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "p2pool", "--no-pager", "-n", "50"],
            capture_output=True, text=True, timeout=10
        )
        shares = re.findall(r'Share (\d+)', r.stdout)
        return int(shares[-1]) if shares else None
    except:
        return None

# ─── BUILD CONCLUSION ───

def build_conclusion():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    pos = fetch_position()
    sol = fetch_sol_balance()
    hashrate = fetch_hashrate()
    services = fetch_services()
    containers = fetch_containers()
    unraid = fetch_unraid()
    sol_price = fetch_sol_price() or (pos.get("price") if pos else None)

    lines = [f"[System Snapshot {ts}]"]

    # Mining
    if hashrate:
        lines.append(f"Mining: XMRig {hashrate:.1f} KH/s")
    msr = services.get("xmrig-msr", "unknown")
    lines.append(f"P2Pool: {services.get('p2pool','?')} | Unraid monerod: {unraid}")

    # LP
    if pos:
        in_range = pos["price"] >= pos["lowerPrice"] and pos["price"] <= pos["upperPrice"]
        total_val = (pos["baseTokenAmount"] * pos["price"]) + pos["quoteTokenAmount"]
        lines.append(f"Orca LP: SOL ${pos['price']:.2f}, range ${pos['lowerPrice']:.2f}-${pos['upperPrice']:.2f}, {'IN RANGE' if in_range else 'OUT OF RANGE'}, value ${total_val:.2f}")
    else:
        lines.append("Orca LP: no position found")

    # Wallet
    if sol >= 0:
        lines.append(f"Wallet: {sol:.4f} SOL buffer")

    # Price
    if sol_price:
        lines.append(f"SOL price: ${sol_price:.2f}")

    # Services
    svc_ok = all(v == "active" for v in services.values())
    if svc_ok:
        lines.append(f"Services: all 5 active (xmrig, p2pool, wallet-rpc, isolate, webui)")
    else:
        down = [k for k, v in services.items() if v != "active"]
        lines.append(f"Services: DOWN = {', '.join(down)}")

    # Key containers
    key = ["gateway", "hummingbot-api", "hummingbot-postgres", "hummingbot-broker"]
    ctr_ok = all(containers.get(c, "").startswith("Up") for c in key)
    if ctr_ok:
        lines.append(f"Docker: all key containers up ({len(containers)} total)")
    else:
        down = [c for c in key if not containers.get(c, "").startswith("Up")]
        lines.append(f"Docker: DOWN = {', '.join(down)}")

    return " | ".join(lines)

# ─── MAIN ───

def main():
    conclusion = build_conclusion()
    print(conclusion)

if __name__ == "__main__":
    main()