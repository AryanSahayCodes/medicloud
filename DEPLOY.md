# Deploying MediCloud on AWS: step by step

Total time: about 2–3 hours, including the experiments. Do the parts in order.
Region used everywhere: **Asia Pacific (Mumbai) – ap-south-1** (top-right corner of the AWS console).

What you will end up with:

```
Your browser ──HTTP:80──▶ Elastic IP ──▶ EC2 t3.micro (Amazon Linux 2023, a Nitro VM)
                                          └─ Docker Compose
                                             ├─ api container  (Flask + Gunicorn, restart: unless-stopped)
                                             └─ db container   (PostgreSQL 16, data on a Docker volume)
                                          logs ──▶ CloudWatch Logs
Route 53 health check ──▶ CloudWatch alarm ──▶ SNS ──▶ your email
Admin access: Systems Manager Session Manager (no SSH port open)
```

---

## Part A: Put the code on GitHub (10 min)

1. Sign in to <https://github.com> (create a free account if needed).
2. Click **New repository** → name `medicloud` → **Public** → do **not** add a README → **Create repository**.
3. In a terminal in the `medicloud` folder (the commit is already made):

   ```bash
   git remote add origin https://github.com/YOUR-GITHUB-USERNAME/medicloud.git
   ```

   ```bash
   git push -u origin main
   ```

   A browser window asks you to sign in to GitHub the first time. Refresh the repo page afterwards: you should see the files.

4. Open `deploy/ec2-user-data.sh` and change this line to your repo URL, then save:

   ```
   REPO_URL="https://github.com/YOUR-GITHUB-USERNAME/medicloud.git"
   ```

## Part B: AWS account and a cost alarm (15 min)

1. Create an AWS account at <https://aws.amazon.com> (needs a card; you do this yourself).
   New accounts get Free Tier credits that should cover this project.
2. Sign in to the console. Set the region (top right) to **Asia Pacific (Mumbai)**.
3. **Set a budget so you're never surprised:** search **Budgets** → **Create budget** →
   **Use a template** → **Monthly cost budget** → amount `5` USD → your email → **Create budget**.

## Part C: IAM role for the server (5 min)

The server needs permission to (1) be managed by Session Manager and (2) write logs to CloudWatch.

1. Search **IAM** → **Roles** → **Create role**.
2. Trusted entity: **AWS service** → Use case: **EC2** → **Next**.
3. Tick these two policies (use the search box):
   - `AmazonSSMManagedInstanceCore`
   - `CloudWatchAgentServerPolicy`
4. **Next** → Role name: `medicloud-ec2-role` → **Create role**.

## Part D: Launch the EC2 server (10 min)

Search **EC2** → **Launch instance**:

| Setting | Value |
|---|---|
| Name | `medicloud-server` |
| AMI | **Amazon Linux 2023** (default), 64-bit (x86) |
| Instance type | **t3.micro** (marked *Free tier eligible*) |
| Key pair | **Proceed without a key pair** (we use Session Manager instead of SSH) |
| Network settings → **Edit** | Create security group `medicloud-sg`. **Remove the SSH rule.** Add rule: Type **HTTP**, Source **Anywhere (0.0.0.0/0)** |
| Storage | `20` GiB **gp3** |
| Advanced details → IAM instance profile | `medicloud-ec2-role` |
| Advanced details → User data | paste the **whole** content of `deploy/ec2-user-data.sh` (with your REPO_URL) |

Click **Launch instance**.

## Part E: Fixed public address = your App URL (3 min)

1. EC2 → **Elastic IPs** → **Allocate Elastic IP address** → **Allocate**.
2. Select it → **Actions** → **Associate Elastic IP address** → Instance: `medicloud-server` → **Associate**.
3. Note the IP, e.g. `13.233.10.20`. **Your App URL is `http://13.233.10.20/`.**

## Part F: Check it works (5–10 min)

The server needs ~5 minutes on first boot to install Docker and build the app.

- Open `http://YOUR-IP/`: the booking page should load. Book a test appointment.
- Open `http://YOUR-IP/health`: should show `"status": "ok"`.

**Connect to the server** (for checks and the experiments): EC2 → select the instance → **Connect** →
**Session Manager** tab → **Connect**. A terminal opens in your browser. Useful commands:

```bash
sudo -i
cd /opt/medicloud
docker compose ps                 # both containers should be "Up (healthy)"
docker compose logs --tail 30 api
tail -40 /var/log/cloud-init-output.log   # first-boot install log, if something failed
```

> If Session Manager says the instance isn't connected, wait 2–3 minutes and retry.
> If it still fails, check the instance has the `medicloud-ec2-role` (Actions → Security → Modify IAM role).

## Part G: Monitoring, alarms and the SLA dashboard (20 min)

### G1. Logs
CloudWatch → **Log groups** → `/medicloud/api`: every request is a JSON line (path, status, latency).
Open **Logs Insights**, choose `/medicloud/api` and run:

```
fields @timestamp, method, path, status, ms
| filter ispresent(status)
| stats count() as requests, avg(ms) as avg_ms, pct(ms, 95) as p95_ms by bin(1m)
```

Errors only:

```
fields @timestamp, path, status, error | filter level = "error" | sort @timestamp desc
```

### G2. Email alerts (SNS)
1. Search **SNS** → **Topics** → **Create topic** → Standard → name `medicloud-alerts` → **Create**.
2. **Create subscription** → Protocol **Email** → your email → **Create**.
3. Open the email from AWS and click **Confirm subscription**.

### G3. External availability check (Route 53 health check, ~US$0.50/month)
This is your SLA probe: AWS checks `/health` from several locations around the world.

1. Search **Route 53** → **Health checks** → **Create health check**.
2. Name `medicloud-health` · Endpoint by **IP address** · Protocol **HTTP** · IP = your Elastic IP · Port `80` · Path `/health`.
3. Advanced: Request interval **Standard (30 s)**, Failure threshold `2`.
4. **Next** → Create alarm: **Yes** → Send notification to existing SNS topic `medicloud-alerts` → **Create**.

### G4. Server alarms
EC2 → select instance → **Actions → Monitor and troubleshoot → Manage CloudWatch alarms** → Create:
- **Status check failed (system)** ≥ 1 for 1 minute → notify `medicloud-alerts` and Alarm action **Recover**.
- **CPU utilization** > 80 % for 5 minutes → notify `medicloud-alerts`.

### G5. Dashboard (great slide screenshot)
CloudWatch → **Dashboards** → **Create dashboard** `MediCloud-SLA` and add widgets:
- Line: EC2 `CPUUtilization` and `StatusCheckFailed` for `medicloud-server`
- Line: Route 53 `HealthCheckPercentageHealthy` (choose region **US East (N. Virginia)** when picking this metric)
- Logs table: the Logs Insights query from G1
- Alarm status: the alarms you created

## Part H: Experiments: your real numbers for the slides (45 min)

Run these on your laptop from the `medicloud` folder. Results are saved in `results/`.

### H1. Performance / load test → slide 10
```bash
python tools/loadtest.py http://YOUR-IP --users 50 --duration 120
```
Note **throughput (req/s), avg, P95, P99 and error rate**. Optionally repeat with `--users 100`.
(Latency includes the internet trip from your location to Mumbai; say so on the slide.)

### H2. TTR experiments → slides 9 and 13
Open **two windows**:

**Window 1 (laptop), leave it running for all of H2:**
```bash
python tools/uptime_probe.py http://YOUR-IP
```

**Window 2 (Session Manager on the server):**

1. Container crash (do it 3 times, ~1 minute apart):
   ```bash
   sudo bash /opt/medicloud/tools/inject_failure.sh crash-api
   ```
2. Database crash:
   ```bash
   sudo bash /opt/medicloud/tools/inject_failure.sh crash-db
   ```
3. Whole-server reboot: EC2 console → **Instance state → Reboot**. Docker starts on boot and the
   restart policy brings both containers back automatically.

Window 1 prints `DOWN detected` and `RECOVERED after X s` for each failure: **that X is your measured TTR**.
While the server is down you should also get the Route 53 alarm email (screenshot it!).

Let the probe keep running a while (the longer, the more meaningful the availability %), then press
**Ctrl+C** for the summary: availability %, every outage and its TTR.

TTR breakdown for the slide: **detection** = time until the first failed probe / health check,
**recovery** = Docker restart, **verification** = first successful `/health`.

### H3. What to put in the deck
| Slide | Take from |
|---|---|
| 9 – TTR workflow | Probe outages: TTR per failure type |
| 10 – Performance | `loadtest` results + probe availability % |
| 12 – Demo | App URL, GitHub URL, screenshots below |
| 13 – Results | Target vs observed using the numbers above |

Send me the outputs (or the files in `results/`) and I'll update the presentation with them.

## Part I: Screenshots to take
1. Booking page with a confirmed appointment (shows the URL)
2. EC2 instance list showing `medicloud-server` running
3. Session Manager: `docker compose ps` with both containers healthy
4. `uptime_probe` window showing DOWN → RECOVERED
5. Route 53 alarm email or the alarm going ALARM → OK
6. CloudWatch dashboard `MediCloud-SLA`
7. Logs Insights query result
8. `loadtest` result window
9. GitHub repository page

## Part J: Clean up after your evaluation (stops all charges)
1. EC2 → **Terminate** `medicloud-server`.
2. EC2 → Elastic IPs → **Release** the address (an unattached Elastic IP is billed).
3. Route 53 → delete the health check. CloudWatch → delete alarms, dashboard and log groups `/medicloud/*`.
4. SNS → delete the topic. (IAM role and security group can stay; they're free.)

## Troubleshooting
| Symptom | Fix |
|---|---|
| Page doesn't load after 10 min | Session Manager → `tail -60 /var/log/cloud-init-output.log`. Most common: wrong `REPO_URL` or repo is private. Fix with `git clone` manually then `cd /opt/medicloud && docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build` (create `.env` first, see user data script). |
| `api` container keeps restarting | `docker compose logs api`. If it mentions `awslogs`, the IAM role is missing CloudWatchAgentServerPolicy. |
| Session Manager tab greyed out | Instance has no IAM role, or wait a few minutes after launch. |
| `git push` rejected (GH007 email privacy) | GitHub → Settings → Emails → untick "Block command line pushes that expose my email", or ask me to switch the commit to your no-reply address. |
| Changed code | Commit + push, then on the server: `sudo bash /opt/medicloud/deploy/update.sh` |
