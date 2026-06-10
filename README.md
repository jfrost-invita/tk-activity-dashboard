# TK Activity Dashboard

A Flask-based internal dashboard for the DNA & Forensics Track-Kit team at InVita Health. Displays live Jira data across three tabs:

- **Epic Activity** — TK tickets with status changes in the last 24 hours, grouped by Executing epic
- **SR Activity** — Customer SR tickets (Track-Kit product) with team comments or linked TK work in the last 24 hours
- **Epic Progress** — Completion breakdown for all Executing TK epics, with hourly-refreshed data cached in S3

---

## Requirements

- Python 3.11 (must match the Lambda runtime)
- AWS CLI
- A Jira API token for `jimmy.frost@invitahealth.com`
- AWS account access with permissions for Lambda, API Gateway, S3, and IAM

---

## Environment Variables

| Variable | Description |
|---|---|
| `JIRA_USER` | Jira account email (`jimmy.frost@invitahealth.com`) |
| `JIRA_TOKEN` | Jira API token |
| `DATA_BUCKET` | S3 bucket for cached epic progress data (`invita-tk-dashboard-data`) |

For local development, set these in your shell:
```
export JIRA_USER=jimmy.frost@invitahealth.com
export JIRA_TOKEN=your_token_here
export DATA_BUCKET=invita-tk-dashboard-data
```

---

## Local Development

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app runs at `http://localhost:5000`.

---

## AWS Setup

### 1. Install the AWS CLI

```
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /
aws --version
```

### 2. Configure AWS credentials

**Standard (access key):**
```
aws configure
```
You will be prompted for your Access Key ID, Secret Access Key, region (`us-east-1`), and output format (`json`). Generate keys in the AWS Console under your account → Security credentials → Access keys.

**SSO (if InVita uses IAM Identity Center):**
```
aws configure sso
```
You will need the SSO start URL and region from whoever manages the InVita AWS account.

### 3. Create S3 buckets (one-time)

```
aws s3 mb s3://zappa-tk-activity-dashboard-deployments --region us-east-1
aws s3 mb s3://invita-tk-dashboard-data --region us-east-1
```

Use the region that matches your AWS account. If you change the region, update `aws_region` in `zappa_settings.json` to match.

---

## Deployment with Zappa

Zappa packages the Flask app and deploys it to AWS Lambda behind API Gateway.

### First deploy

```
source .venv/bin/activate
zappa deploy production
```

Zappa will output a public URL like:
```
https://xxxx.execute-api.us-east-1.amazonaws.com/production
```

### Subsequent deployments (after code changes)

```
zappa update production
```

### View live logs

```
zappa tail production
```

### Tear down

```
zappa undeploy production
```

---

## How `zappa_settings.json` Works

| Field | Value | Notes |
|---|---|---|
| `app_function` | `app.app` | `app.py` filename + `app` Flask instance variable |
| `aws_region` | `us-east-1` | Must match S3 bucket region |
| `runtime` | `python3.11` | Local venv Python version must match |
| `s3_bucket` | `zappa-tk-activity-dashboard-deployments` | Zappa uses this for deployment packages |
| `timeout_seconds` | `60` | Lambda execution limit per request |
| `memory_size` | `512` | Lambda memory in MB |
| `environment_variables` | See above | Injected into Lambda at runtime |
| `extra_permissions` | S3 read/write on data bucket | Allows Lambda to cache epic progress data |
| `events` | `generate_epic_progress.run` every hour | CloudWatch rule that keeps epic data fresh |

---

## Architecture

```
Browser
  └── API Gateway
        └── AWS Lambda (Flask via Zappa)
              ├── Jira API (live data for activity tabs)
              └── S3: invita-tk-dashboard-data (cached epic progress)
                    └── CloudWatch Event (hourly refresh via generate_epic_progress.run)
```
