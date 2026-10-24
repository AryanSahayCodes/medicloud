"""Load test for MediCloud (no extra installs needed).

Simulates N concurrent users for D seconds with a realistic mix:
  70% check available slots, 20% list doctors, 10% book an appointment.
Prints throughput, error rate and latency (avg / P50 / P95 / P99).

    python tools/loadtest.py http://YOUR-ELASTIC-IP --users 50 --duration 120
"""
import argparse
import http.client
import json
import os
import random
import statistics
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
         "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]


def pct(sorted_vals, p):
    return round(sorted_vals[min(len(sorted_vals) - 1, int(p / 100 * len(sorted_vals)))], 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--users", type=int, default=50, help="concurrent virtual users")
    ap.add_argument("--duration", type=int, default=60, help="seconds")
    ap.add_argument("--think", type=float, default=0.0, help="pause between a user's requests (s)")
    args = ap.parse_args()

    u = urlparse(args.url)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    Conn = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection

    lock = threading.Lock()
    samples = defaultdict(list)   # endpoint -> [latency_ms]
    codes = defaultdict(int)
    errors = defaultdict(int)
    stop_at = time.time() + args.duration

    def user(uid):
        rnd = random.Random(uid)
        conn = Conn(host, port, timeout=10)
        while time.time() < stop_at:
            r = rnd.random()
            doctor = rnd.randint(1, 6)
            day = (date.today() + timedelta(days=rnd.randint(1, 89))).isoformat()
            if r < 0.70:
                name, method, path, body = "GET /api/slots", "GET", f"/api/slots?doctor_id={doctor}&date={day}", None
            elif r < 0.90:
                name, method, path, body = "GET /api/doctors", "GET", "/api/doctors", None
            else:
                name, method, path = "POST /api/appointments", "POST", "/api/appointments"
                body = json.dumps({"doctor_id": doctor, "date": day, "slot": rnd.choice(SLOTS),
                                   "patient_name": f"Load User {uid}",
                                   "phone": f"9{rnd.randint(100000000, 999999999)}"})
            t0 = time.perf_counter()
            try:
                conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                resp.read()
                ms = (time.perf_counter() - t0) * 1000
                # 409 = slot already taken: a correct business answer, not a failure
                ok = resp.status < 400 or resp.status == 409
                with lock:
                    codes[resp.status] += 1
                    if ok:
                        samples[name].append(ms)
                    else:
                        errors[f"HTTP {resp.status}"] += 1
            except Exception as e:
                with lock:
                    errors[type(e).__name__] += 1
                conn.close()
                conn = Conn(host, port, timeout=10)
            if args.think:
                time.sleep(args.think)
        conn.close()

    print(f"Load test: {args.users} users for {args.duration}s against {args.url} ...")
    t_start = time.time()
    threads = [threading.Thread(target=user, args=(i,), daemon=True) for i in range(args.users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t_start

    all_ms = sorted(ms for v in samples.values() for ms in v)
    n_ok, n_err = len(all_ms), sum(errors.values())
    total = n_ok + n_err
    if not total:
        print("No requests completed.")
        return
    report = {
        "url": args.url, "users": args.users, "duration_s": round(elapsed, 1),
        "requests": total, "successful": n_ok, "failed": n_err,
        "error_rate_pct": round(100 * n_err / total, 3),
        "throughput_rps": round(total / elapsed, 1),
        "latency_ms": {"avg": round(statistics.mean(all_ms), 1), "p50": pct(all_ms, 50),
                       "p95": pct(all_ms, 95), "p99": pct(all_ms, 99), "max": round(all_ms[-1], 1)} if all_ms else {},
        "by_endpoint": {k: {"count": len(v), "p95_ms": pct(sorted(v), 95)} for k, v in samples.items()},
        "status_codes": dict(codes), "errors": dict(errors),
    }
    os.makedirs("results", exist_ok=True)
    out = f"results/loadtest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2)

    L = report["latency_ms"]
    print("\n================ RESULTS ================")
    print(f"Requests     : {total}  ({n_err} failed, error rate {report['error_rate_pct']} %)")
    print(f"Throughput   : {report['throughput_rps']} requests/sec")
    if L:
        print(f"Latency      : avg {L['avg']} ms | P50 {L['p50']} | P95 {L['p95']} | P99 {L['p99']} | max {L['max']}")
    for k, v in report["by_endpoint"].items():
        print(f"  {k:<26} {v['count']:>7} req   P95 {v['p95_ms']} ms")
    print(f"SLA check    : P95 < 2000 ms -> {'MET' if L and L['p95'] < 2000 else 'NOT MET'}")
    print(f"Saved        : {out}")


if __name__ == "__main__":
    main()
