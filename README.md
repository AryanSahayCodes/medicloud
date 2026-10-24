# MediCloud

Cloud-hosted OPD appointment booking for a multi-specialty hospital.
Mini project for **Cloud Computing**: an industry use case deployed on AWS with Docker,
virtualization, defined SLAs and measured Time to Recovery (TTR).

**Features:** book an appointment with a doctor and slot (double-booking prevented by a DB
constraint), view or cancel your appointments by mobile number, doctor's daily schedule, live
service status.

## SLA targets
| Metric | Target |
|---|---|
| Availability | 99.9 % |
| Response time | P95 < 2 s |
| TTR (detection → restored) | ≤ 15 min |

## Stack
- **App:** Python Flask REST API + HTML/JS front end, served by Gunicorn
- **Database:** PostgreSQL 16 (SQLite when run locally without Docker)
- **Containers:** Docker image `medicloud-api`, Docker Compose (api + db), restart policy for self-healing
- **AWS:** EC2 t3.micro (Amazon Linux 2023 on the Nitro hypervisor), Elastic IP, Security Group,
  IAM role, Systems Manager Session Manager, CloudWatch Logs / Alarms / Dashboard, Route 53 health check, SNS

## Layout
```
app/                 Flask app, HTML template, Dockerfile, requirements
docker-compose.yml   api + PostgreSQL
docker-compose.aws.yml  CloudWatch Logs driver (AWS only)
deploy/              EC2 user-data bootstrap and update script
tools/               uptime/TTR probe, load test, failure injection
DEPLOY.md            step-by-step AWS deployment and experiments
```

## Run locally (no Docker needed)
```bash
pip install flask
python app/app.py          # http://localhost:5000
```

With Docker:
```bash
echo DB_PASSWORD=localdev > .env
docker compose up --build  # http://localhost
```

## Deploy on AWS
See [DEPLOY.md](DEPLOY.md).
