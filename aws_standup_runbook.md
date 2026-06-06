# GRCen AWS Stand-Up Runbook (Usability Test Environment)

**Status:** Draft v1 · **Date:** 2026-06-05 · **Companion to:** `usability_testing_plan.md`

Concrete steps to stand up a live, HTTPS, seeded GRCen instance on AWS for moderated
usability testing, then reset and tear it down. Built on the repo's existing deploy
scaffolding (`docker-compose.prod.yml` + `deploy/nginx.conf` + `Dockerfile`), so this is
the documented path, not a bespoke one.

---

## Architecture

Single small EC2 host running the app via Docker Compose, with nginx terminating TLS in
front of it. Postgres runs as a co-located container (the test env is disposable — no need
for RDS). This matches `docker-compose.yml` (base: db + app) overlaid with
`docker-compose.prod.yml` (adds nginx on 80/443, internalizes the app on 8000).

```
Internet ──443──> nginx (TLS) ──8000──> app (gunicorn/uvicorn) ──> postgres (container)
                  certbot/Let's Encrypt          grcen.main:app          pgdata volume
```

**Why single-host + container Postgres:** cheapest, fastest to reset (one DB snapshot or
`grcen backup`), and trivially disposable between test rounds. Nothing here is long-lived.

---

## Decisions to lock before provisioning

| Decision | Options | Default recommendation |
|----------|---------|------------------------|
| **Region** | any | `us-east-1` (or closest to participants for latency) |
| **Instance size** | t3.small / t3.medium | **t3.small** (2 vCPU / 2 GB) is enough for a seeded demo + a few concurrent users; WeasyPrint PDFs are the heaviest op. Bump to t3.medium if PDF/export feels sluggish. |
| **DNS + TLS** | real domain + Let's Encrypt · `sslip.io` magic DNS + Let's Encrypt · self-signed | **Real domain + LE** if you have one (best trust). Otherwise `<ip-with-dashes>.sslip.io` gives a real hostname for a public IP with no domain purchase, and Let's Encrypt will still issue for it. **Avoid self-signed** — the browser warning poisons trust feedback in a *GRC* tool. |
| **AWS credentials** | root keys (current) vs. scoped IAM user | See security note below — prefer a scoped IAM user/role over root. |

> ⚠️ **Security note on credentials.** The AWS CLI in this environment is currently
> authenticated as the **account root** (`arn:aws:iam::293604177141:root`). Provisioning
> with root keys is discouraged — a leaked root key is unrecoverable. For a short-lived
> test env it's a judgment call, but the clean path is a dedicated IAM user/role scoped to
> EC2 + the needed networking, used just for this. Flagging, not blocking.

---

## Prerequisites

- AWS account + credentials (CLI authenticated — verified via `aws sts get-caller-identity`).
- An SSH keypair for the instance (`aws ec2 create-key-pair` or import your own).
- (If using a real domain) ability to create an A record pointing at the instance's
  public IP.

---

## Step 1 — Provision the EC2 host

Create a security group that is **tight**: SSH only from your IP, HTTP/HTTPS from anywhere
(needed for Let's Encrypt + participant access). **Do not** open Postgres (5432).

```bash
# Variables
REGION=us-east-1
KEY_NAME=grcen-uxtest
MY_IP=$(curl -s https://checkip.amazonaws.com)/32

# Keypair (skip if importing your own)
aws ec2 create-key-pair --region $REGION --key-name $KEY_NAME \
  --query KeyMaterial --output text > ~/.ssh/$KEY_NAME.pem && chmod 600 ~/.ssh/$KEY_NAME.pem

# Security group
SG_ID=$(aws ec2 create-security-group --region $REGION \
  --group-name grcen-uxtest --description "GRCen usability test" \
  --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG_ID \
  --ip-permissions \
    IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges="[{CidrIp=$MY_IP}]" \
    IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges="[{CidrIp=0.0.0.0/0}]" \
    IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges="[{CidrIp=0.0.0.0/0}]"

# Launch (Amazon Linux 2023, x86_64). Resolve the latest AMI via SSM.
AMI=$(aws ssm get-parameters --region $REGION \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameters[0].Value' --output text)
aws ec2 run-instances --region $REGION --image-id $AMI \
  --instance-type t3.small --key-name $KEY_NAME --security-group-ids $SG_ID \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=grcen-uxtest}]' \
  --count 1
```

Grab the public IP:
```bash
aws ec2 describe-instances --region $REGION \
  --filters Name=tag:Name,Values=grcen-uxtest Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text
```

## Step 2 — DNS

- **Real domain:** create an A record `grcen-test.yourdomain.com -> <public-ip>`.
- **No domain:** use `sslip.io` — hostname becomes `<ip-with-dashes>.sslip.io`
  (e.g. `52-1-2-3.sslip.io`). No setup; resolves to the embedded IP.

Set `DOMAIN` to whichever you chose; it's used in nginx + the cert + `APP_BASE_URL`.

## Step 3 — Install Docker + clone

```bash
ssh -i ~/.ssh/$KEY_NAME.pem ec2-user@$DOMAIN
sudo dnf -y install docker git && sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user && newgrp docker   # re-login if needed
# Compose v2 plugin
sudo mkdir -p /usr/libexec/docker/cli-plugins
sudo curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/libexec/docker/cli-plugins/docker-compose && sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose

git clone <this-repo-url> grcen && cd grcen
```

## Step 4 — Configure `.env`

```bash
cp .env.example .env
# Generate and set a real secret (required: app refuses to boot with DEBUG=false + default key)
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# Edit .env:
#   DEBUG=false
#   SECRET_KEY=<SECRET>
#   APP_BASE_URL=https://$DOMAIN          # correct links in any outbound emails
#   DATABASE_URL=postgresql://grcen:grcen@db:5432/grcen   # container-internal
# Optional (to demo encryption-at-rest, Task stretch): ENCRYPTION_KEY=<grcen generate-key>
```

`SECRET_KEY` is also consumed directly by `docker-compose.prod.yml` (`${SECRET_KEY:?...}`),
so it must be exported or present in `.env`.

## Step 5 — TLS certificate (Let's Encrypt)

Per `configure_https.md`, standalone mode (nothing is on :80 yet):
```bash
sudo dnf -y install certbot
sudo certbot certonly --standalone -d $DOMAIN --agree-tos -m you@example.com -n
mkdir -p deploy/ssl
sudo cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem deploy/ssl/fullchain.pem
sudo cp /etc/letsencrypt/live/$DOMAIN/privkey.pem   deploy/ssl/privkey.pem
sudo chown $USER deploy/ssl/*.pem
```

## Step 6 — Point nginx at the domain

Edit `deploy/nginx.conf`: replace both `grcen.example.com` occurrences with `$DOMAIN`
(the cert paths `/etc/nginx/ssl/fullchain.pem|privkey.pem` already map to `deploy/ssl`).

## Step 7 — Bring up the stack

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps          # db healthy, app + nginx up
curl -skI https://$DOMAIN/ # expect 200 / redirect; schema auto-inits on first app boot
```

> Hardening (optional but recommended): the base compose publishes Postgres on the host
> (`5432:5432`). The EC2 security group already blocks it externally, but to be safe you
> can add a tiny prod override that drops the published port. Not required for function.

## Step 8 — Bootstrap the first admin + org

```bash
docker compose exec app grcen createadmin
#   Username: admin     Password: <strong>     Org slug: (blank = default)
```

## Step 9 — Seed realistic data

The sample CSVs and seed scripts aren't baked into the image, so copy them in, then run
them inside the app container (where `grcen` is installed and `DATABASE_URL` points at
`db:5432`):

```bash
docker compose cp ./sample_data app:/app/sample_data
docker compose exec app python sample_data/seed_data.py     # 187 assets + 366 relationships
docker compose exec app python sample_data/seed_alerts.py   # 44 alerts + 7 notifications
docker compose exec app python sample_data/seed_answers.py  # 8 answers + 1 questionnaire
```

This lights up the dashboard, asset graph, risk heatmap (16 risks + `mitigated_by`
control rollups), the **Frameworks** dashboard (4 frameworks / 7 requirements /
7 controls / `satisfies` edges), and the **answer library + one inbound questionnaire** —
covering all plan Tasks 1–10.

**Answer library (plan Task 8):** `seed_answers.py` adds 8 canonical Q&A entries wired to
existing Control/Policy/Framework/Audit assets via `substantiated_by`, plus one
questionnaire ("Acme Corp — Vendor Security Assessment 2026") with 2 questions pre-mapped
(auto-filled) and 4 left blank for the participant to map/fill. It deliberately seeds
three freshness states so the engine visibly flags review work: 5 fresh, and 3 needing
review (one unbacked, one backed by a decommissioned control, one whose policy
substantiator is `archived`). This exercises reuse + auto-fill + freshness end-to-end.

**Frameworks via external catalog (optional):** if you have an autocomply catalog export,
`docker compose exec app grcen sync-catalog /app/<export>.json` adds more framework
coverage on top of the seeded set.

## Step 10 — Prepare test accounts + scenarios

- Create one user per role (Admin already exists): `docker compose exec app grcen createadmin`
  won't make non-admins — use the UI at `/admin/users/new` (logged in as admin) to make an
  **Editor**, **Viewer**, and **Auditor** with known passwords.
- **Workflow gating (Task 10):** in the UI, `/admin/workflow` → require approval for
  *Policy* delete (or System update). Then, as the Editor, attempt a gated change so a
  **pending change** sits in `/approvals` ready for the participant to action.
- **Import task (Task 4):** have a small synthetic vendor CSV ready to hand the participant
  (a 5-row file with `name,type,description,status,owner` columns).

## Step 11 — Stage 0 smoke test (this IS your expert self-eval)

Walk every task path in `usability_testing_plan.md` §5 yourself. Confirm: login, dashboard
populated, asset create + drag-to-link on the graph, import preview→commit, risk heatmap,
framework gap report PDF renders, evidence upload, saved search, approvals queue. Tail logs
in another pane: `docker compose logs -f app`. Log defects with the §9 severity scale.

## Step 12 — Reset between participants

Each participant should start from the same seeded state. Snapshot after seeding, restore
before the next session:

```bash
# Capture the golden state once (after Step 10):
docker compose exec app grcen backup /app/uploads/golden.grcen   # encrypted (ENCRYPTION_KEY)
# ...participant does their session (creates/edits data)...
# Reset:
docker compose exec app grcen restore /app/uploads/golden.grcen
```
Alternatively, snapshot the `pgdata` volume / EBS volume between sessions. (`grcen backup`
needs `ENCRYPTION_KEY` set; if you skipped it, use a raw `pg_dump`/volume snapshot instead.)

## Step 13 — Teardown

```bash
# On the host:
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
# From your workstation, when the round is fully done:
aws ec2 terminate-instances --region $REGION --instance-ids <id>
aws ec2 delete-security-group --region $REGION --group-id $SG_ID   # after instance gone
```

---

## Cost (rough, us-east-1)

- t3.small on-demand ≈ \$0.0208/hr ≈ **\$0.50/day**; 20 GB gp3 ≈ \$1.60/mo.
- Run it only during test rounds and `stop` (not just leave running) between days; storage
  is the only charge while stopped. Total for a week-long study: a few dollars.

## Security checklist

- [ ] Synthetic data only (sample_data is synthetic) — safe to screen-share/record.
- [ ] SSH restricted to your IP; 5432 never exposed in the security group.
- [ ] Real TLS cert (no self-signed warnings).
- [ ] Strong `SECRET_KEY`; strong admin + per-role passwords.
- [ ] Prefer a scoped IAM user over root for provisioning (see note above).
- [ ] Terminate the instance + delete the SG when the study ends.
