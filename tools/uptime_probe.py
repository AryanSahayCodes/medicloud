"""Uptime + TTR probe for MediCloud.

Calls <url>/health every second, logs every result to a CSV, and prints each
outage as it happens. Press Ctrl+C (or use --duration) to stop and get the
summary: availability %, latency, and the length of every outage (= measured TTR).

    python tools/uptime_probe.py http://YOUR-ELASTIC-IP
    python tools/uptime_probe.py http://YOUR-ELASTIC-IP --interval 1 --duration 600
"""
import argparse
import csv
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime


def now_str(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def probe(url, timeout):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
            return r.status == 200, r.status, (time.perf_counter() - t0) * 1000, ""
    except urllib.error.HTTPError as e:
        return False, e.code, (time.perf_counter() - t0) * 1000, f"HTTP {e.code}"
    except Exception as e:  # connection refused, timeout, DNS ...
        return False, 0, (time.perf_counter() - t0) * 1000, type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="base URL, e.g. http://13.200.1.2")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between probes")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    args = ap.parse_args()

    target = args.url.rstrip("/") + "/health"
    os.makedirs("results", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = f"results/probe-{stamp}.csv"
    print(f"Probing {target} every {args.interval}s  ->  {csv_path}\nCtrl+C to stop.\n")

    results, outages = [], []
    down_since, last_ok = None, None
    start = time.time()
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "time", "ok", "status", "latency_ms", "error"])
        try:
            while True:
                ts = time.time()
                ok, status, ms, err = probe(target, args.timeout)
                results.append((ts, ok, ms))
                w.writerow([f"{ts:.3f}", now_str(ts), int(ok), status, f"{ms:.1f}", err])
                f.flush()
                if not ok and down_since is None:
                    down_since = ts
                    print(f"[{now_str(ts)}]  DOWN detected  ({err or status})")
                elif ok and down_since is not None:
                    dur = ts - down_since
                    outages.append({"down_at": now_str(down_since), "up_at": now_str(ts),
                                    "last_ok_before": now_str(last_ok) if last_ok else None,
                                    "seconds": round(dur, 1)})
                    print(f"[{now_str(ts)}]  RECOVERED after {dur:.1f} s")
                    down_since = None
                elif ok and len(results) % 30 == 1:
                    print(f"[{now_str(ts)}]  up   {ms:.0f} ms")
                if ok:
                    last_ok = ts
                if args.duration and ts - start >= args.duration:
                    break
                time.sleep(max(0, args.interval - (time.time() - ts)))
        except KeyboardInterrupt:
            pass

    if down_since is not None:  # still down when stopped
        outages.append({"down_at": now_str(down_since), "up_at": None, "seconds": round(time.time() - down_since, 1)})

    n = len(results)
    if not n:
        return
    ok_n = sum(1 for _, ok, _ in results if ok)
    lat = sorted(ms for _, ok, ms in results if ok)
    total_s = results[-1][0] - results[0][0] + args.interval
    downtime = sum(o["seconds"] for o in outages)
    summary = {
        "target": target, "started": now_str(results[0][0]), "duration_s": round(total_s, 1),
        "probes": n, "successful": ok_n,
        "availability_by_probes_pct": round(100 * ok_n / n, 3),
        "availability_by_time_pct": round(100 * (1 - downtime / total_s), 3),
        "latency_avg_ms": round(statistics.mean(lat), 1) if lat else None,
        "latency_p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "outages": outages, "total_downtime_s": round(downtime, 1),
        "measured_ttr_s": [o["seconds"] for o in outages],
    }
    with open(f"results/probe-{stamp}-summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\n================ SUMMARY ================")
    print(f"Duration          : {summary['duration_s']} s  ({n} probes)")
    print(f"Availability      : {summary['availability_by_time_pct']} %  (by time)")
    print(f"Health latency    : avg {summary['latency_avg_ms']} ms, P95 {summary['latency_p95_ms']} ms")
    print(f"Outages           : {len(outages)}  (total downtime {summary['total_downtime_s']} s)")
    for i, o in enumerate(outages, 1):
        print(f"  #{i}: down {o['down_at']} -> up {o['up_at']}   TTR = {o['seconds']} s")
    print(f"Saved             : {csv_path} and -summary.json")


if __name__ == "__main__":
    main()
