# Deployment runbook: AquaCreds on Oracle Cloud Always Free ($0)

This runbook takes you from no cloud account to a running HTTPS API with nightly backups. It assumes
you have never done this before. Budget about 2 hours, most of it waiting on sign-up and DNS.

**What you get:** one Oracle Ampere A1 VM (ARM64, Ubuntu 24.04) running Docker Compose with Caddy
(automatic HTTPS), the FastAPI API, Celery workers and beat, PostgreSQL/PostGIS and Valkey. Uploads and
backups go to Cloudflare R2. Images are pulled from GHCR, where CI builds them for `linux/arm64`.

| File | Purpose |
|---|---|
| `infra/compose/compose.prod.yaml` | production stack (limits, healthchecks, networks) |
| `infra/compose/Caddyfile` | TLS, reverse proxy, security headers, docs blocking |
| `.env.prod.example` | every variable, documented; copy to `/opt/aquacreds/.env.prod` on the VM |
| `infra/scripts/bootstrap-vm.sh` | one-time VM setup (Docker, swap, firewall, deploy user, auto-updates) |
| `infra/scripts/deploy.sh` | pull, migrate, start, wait for `/readyz`, roll back on failure |
| `infra/scripts/backup.sh` / `restore.sh` | nightly `pg_dump` to R2 with 14-day retention / restore into a fresh DB |
| `infra/scripts/compose.sh` | `docker compose` with the right env file, compose file and image tag |
| `infra/scripts/systemd/` | backup timer (plus a cron alternative) |
| `infra/scripts/smoke-prod-local.sh` | runs the whole production stack on a laptop, with no cloud involved |
| `.github/workflows/deploy.yml` | tag `v*` or manual run, then deploy over SSH (inert until secrets exist) |

---

## 1. Architecture

```
 Internet ──443/80──▶ OCI Security List ──▶ VM iptables ──▶ caddy ──(edge net)──▶ api:8000
                                                                                     │
                     worker-default (default,ml,reports)   worker-geo (geo, c=1)     │ (data net,
                     worker-chain (chain, c=1)   beat        migrate (one-shot)      │  internal)
                                   │                                                 ▼
                                   └────────────────────────────────▶ db (PostGIS)   valkey
 App containers ──(egress net)──▶ Cloudflare R2, STAC, Polygon RPC, Sentry
```

- Only Caddy publishes ports (80, 443). `db` and `valkey` sit on an `internal` network with no
  internet route and no host port. Valkey requires a password.
- `migrate` runs `aqua-api migrate && aqua-api bootstrap` and must exit 0 before the API starts.
- `APP_ENV=production` is fixed in compose, so the API refuses to start with the development JWT
  secret or the development S3 secret (`Settings.assert_production_safe`).
- `/docs`, `/redoc` and `/openapi.json` return 404 at the edge unless `EXPOSE_API_DOCS=true`.

## 2. Capacity (2 OCPU / 12 GB plan)

Reports since mid-2026 say new tenancies get **2 OCPU / 12 GB** of Ampere A1 instead of 4/24. The
stack is sized for 2/12. If you get 4/24, raise `worker-geo` and `db` first.

| Service | `mem_limit` | `cpus` cap | `cpu_shares` | Idle RSS (smoke test) | Notes |
|---|---:|---:|---:|---:|---|
| caddy | 128 MB | 0.5 | 1024 | 16 MB | TLS + proxy |
| api | 768 MB | 1.5 | 1024 | 260 MB | 2 uvicorn workers |
| worker-default | 1.5 GB | 1.0 | 512 | 160 MB | `default,ml,reports`, concurrency 2 (ONNX photo checks) |
| worker-geo | 3 GB | 1.5 | 256 | 105 MB | `geo`, concurrency 1, recycled after each task |
| worker-chain | 384 MB | 0.5 | 512 | 108 MB | `chain`, concurrency 1 (nonce ordering), single replica |
| beat | 192 MB | 0.25 | 256 | 89 MB | exactly one instance |
| db (PostGIS 17) | 2.5 GB | 1.5 | 1024 | 62 MB | `shared_buffers=640MB`, `work_mem=16MB`, `max_connections=100` |
| valkey | 384 MB | 0.5 | 512 | 13 MB | `maxmemory 256mb`, `noeviction`, AOF |
| **always-on total** | **8.84 GB** | | | **~0.8 GB** | |
| migrate (one-shot) | 512 MB | 1.0 | | | runs only during deploys |
| uptime-kuma (optional) | 256 MB | 0.25 | 256 | | profile `monitoring` |

- **Memory:** the hard limits add up to about 9.1 GB with everything on, which leaves about 3 GB for
  the kernel, Docker and the page cache (Postgres relies on it). The 4 GB swap file is only a safety
  net. A runaway geo job gets OOM-killed inside its own 3 GB limit and doesn't take down the API or
  the database.
- **CPU:** the `cpus` values are per-container ceilings, each at or below the VM's 2 OCPUs. They
  overlap on purpose so idle CPU isn't wasted. When the CPU is contended, `cpu_shares` gives the API,
  the database and Caddy four times the weight of `worker-geo`.

## 3. One-time setup

### 3.1 Oracle Cloud account
1. Sign up at <https://signup.cloud.oracle.com/>. A credit or debit card is required for identity
   verification. Always Free resources aren't charged, but small temporary authorisation holds are
   normal. Some Indian debit cards are declined; a credit card usually works.
2. **Choose the home region carefully:** you can't change it, and Always Free A1 capacity only exists
   there. Pick a region close to your users, such as `ap-mumbai-1` or `ap-hyderabad-1`. If A1 capacity
   there is persistently exhausted, you would need a new account.
3. Leave the account on **Free Tier**. Upgrading to *Pay As You Go* keeps Always Free resources free
   and protects them from idle reclamation (see §8), but anything else you create is billed. If you
   upgrade, set a budget alert first.

### 3.2 Create the VM
Console: **Compute → Instances → Create instance**.
- **Image:** Canonical Ubuntu **24.04** (the *aarch64* build is selected automatically with Ampere).
- **Shape:** *Change shape → Ampere → VM.Standard.A1.Flex*, **2 OCPU, 12 GB** (or the most your
  tenancy allows within Always Free).
- **Networking:** create a new VCN with a public subnet and **assign a public IPv4 address**.
- **SSH keys:** paste your public key. If you don't have one, run `ssh-keygen -t ed25519 -C oracle-vm`
  on your laptop and paste the contents of `~/.ssh/id_ed25519.pub`.
- **Boot volume:** set it to 100–150 GB. The Always Free allowance is 200 GB of block storage in
  total, so stay under it.

**"Out of capacity for shape VM.Standard.A1.Flex"** is common. Try each **availability domain**
(AD-1/2/3) in the placement section, try 1 OCPU / 6 GB and resize later (*Edit → Shape*), or retry at
a different time of day. Don't use scripts that hammer the API: they can get the account flagged.

### 3.3 Open ports 80/443 (two layers, both required)
1. **OCI Security List** (cloud firewall, console only): *Networking → Virtual Cloud Networks → your
   VCN → Security Lists → Default Security List → Add Ingress Rules*:

   | Source CIDR | Protocol | Destination port | Why |
   |---|---|---|---|
   | 0.0.0.0/0 | TCP | 80 | ACME HTTP-01 challenge + redirect to HTTPS |
   | 0.0.0.0/0 | TCP | 443 | HTTPS |
   | 0.0.0.0/0 | UDP | 443 | HTTP/3 (optional) |

   Keep TCP 22 open. Narrow it to your own IP if it's static.
2. **The VM's iptables:** Oracle's Ubuntu images REJECT everything except SSH. `bootstrap-vm.sh`
   inserts ACCEPT rules before the REJECT and persists them in `/etc/iptables/rules.v4`. Don't
   enable `ufw`.

### 3.4 Bootstrap the VM
```bash
scp infra/scripts/bootstrap-vm.sh ubuntu@<VM_IP>:
ssh ubuntu@<VM_IP> 'sudo bash bootstrap-vm.sh'        # idempotent; prints a summary at the end
ssh deploy@<VM_IP> 'docker run --rm hello-world'       # the deploy user can use Docker
```
The script installs Docker Engine and the compose plugin, adds a 4 GB swap file, opens the host
firewall, creates the `deploy` user with your SSH key (copied from `ubuntu`), restricts SSH to key
logins with no root, adds fail2ban, and enables unattended security upgrades.
`AUTO_REBOOT=true` lets the upgrades reboot at 04:30 UTC for kernel fixes. Every service restarts
automatically after a reboot.

### 3.5 Domain: DuckDNS (free)
1. Sign in at <https://www.duckdns.org>, create a subdomain such as `aquacreds-demo`, and set its IP
   to the VM's public IP.
2. Check it: `dig +short aquacreds-demo.duckdns.org` must return the VM IP **before** the first
   deploy. Otherwise Caddy's certificate request fails, and repeated failures hit Let's Encrypt rate
   limits.
3. Oracle keeps an instance's ephemeral public IP until the instance is terminated. To be safe across
   rebuilds, promote it to a *reserved* public IP (*Networking → IP Management*).
4. To use a real domain later, change `API_DOMAIN` and run the deploy again.

### 3.6 Cloudflare R2 (object storage + backups)
1. In the Cloudflare dashboard, go to **R2** and enable it. A card is required; the free tier covers
   10 GB-month of storage, 1M Class A and 10M Class B operations, and egress is free.
2. Create two buckets: `aquacreds-prod` (app uploads) and `aquacreds-backups`. Under *Settings* on
   the backups bucket, consider an **Object lifecycle rule** that deletes objects after 30 days, as a
   backstop to the script's own 14-day retention.
3. Under **Manage R2 API Tokens**, create two tokens with *Object Read & Write*, each scoped to one
   bucket. Keep the app's token unable to delete backups. Each token gives you an *Access Key ID*, a
   *Secret Access Key* and the S3 endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
4. Phones and browsers upload straight to R2 with presigned URLs, so add a **CORS policy** to
   `aquacreds-prod` (*Settings → CORS policy*):
   ```json
   [{"AllowedOrigins": ["https://app.example.org"], "AllowedMethods": ["PUT", "GET"],
     "AllowedHeaders": ["content-type"], "MaxAgeSeconds": 3600}]
   ```

### 3.7 Secrets file on the VM
```bash
ssh deploy@<VM_IP>
cd /opt/aquacreds
# from your laptop first:  scp .env.prod.example deploy@<VM_IP>:/opt/aquacreds/
cp .env.prod.example .env.prod && chmod 600 .env.prod
openssl rand -hex 32    # -> JWT_SECRET
openssl rand -hex 24    # -> POSTGRES_PASSWORD, and again for VALKEY_PASSWORD
nano .env.prod          # replace EVERY placeholder (API_DOMAIN, ACME_EMAIL, R2 keys, CORS_ORIGINS, ...)
```
Never commit `.env.prod`; it's git-ignored. Back it up in a password manager, because without it the
backups can't be used with a new VM.

### 3.8 Registry access (GHCR)
- **Public images** need no login.
- **Private images** (the default while the repository is private): the deploy workflow logs the VM
  in with the job's short-lived `GITHUB_TOKEN` and logs out afterwards. For **manual** deploys, create
  a fine-grained or classic PAT with only `read:packages`, then on the VM run
  `echo <PAT> | docker login ghcr.io -u <github-user> --password-stdin`.

## 4. First deploy

CI pushes `ghcr.io/dkhatke/aquacreds-{api,worker}` with tags `main` and `sha-<7 chars>` on every
commit to `main`. Deploy an immutable `sha-…` tag. `main` moves, so it's not a good tag to roll back
to.

**Option A — from GitHub (recommended).** Add these repository secrets (*Settings → Secrets and
variables → Actions*):

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | VM public IP (or the DuckDNS name) |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | a **new** ed25519 private key used only for CI (`ssh-keygen -t ed25519 -f ci_deploy -N ''`); append `ci_deploy.pub` to `/home/deploy/.ssh/authorized_keys` |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 <VM_IP>`. Compare the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the VM through the OCI console's serial connection or your first SSH session |

Optional variables: `DEPLOY_DIR` (default `/opt/aquacreds`) and `API_DOMAIN`, which enables a
public `/readyz` check after the deploy. Then push a tag (`git tag v0.1.0 && git push origin v0.1.0`)
or run **Actions → Deploy → Run workflow**. The workflow waits until both images exist with a
`linux/arm64` variant. It then copies `infra/compose` and `infra/scripts` to the VM and runs
`deploy.sh sha-<commit>`. Until all four secrets exist, the workflow just ends with a notice.

**Option B — by hand on the VM:**
```bash
cd /opt/aquacreds
# put infra/ on the VM once (from your laptop):  git archive HEAD infra | ssh deploy@<VM_IP> 'tar -x -C /opt/aquacreds'
infra/scripts/deploy.sh sha-1a2b3c4
```
`deploy.sh` pulls the images, takes a pre-deploy `pg_dump` (the 3 most recent are kept in
`/opt/aquacreds/backups`), runs `migrate`, then runs `up -d`. It waits up to 5 minutes until
`/readyz` passes inside the API container **and** through Caddy, and every container is healthy. If
any of that fails, it redeploys the previous tag and exits non-zero. The tag history is in
`/opt/aquacreds/.deploy/history.log`.

Check it:
```bash
curl -fsS https://<API_DOMAIN>/readyz          # {"status":"ready", "checks": {database, redis, storage}}
infra/scripts/compose.sh ps                      # all (healthy)
```

**Create the first registry admin:**
```bash
infra/scripts/compose.sh exec api aqua-api create-user --email admin@example.org
# (equivalent to: docker compose exec api aqua-api create-user --email ..., with the prod env/compose files)
```
It prompts for a password of at least 10 characters.

## 5. Backups and restore

**Enable the nightly backup** (01:30 IST = 20:00 UTC):
```bash
sudo cp /opt/aquacreds/infra/scripts/systemd/aquacreds-backup.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aquacreds-backup.timer
sudo systemctl start aquacreds-backup.service && journalctl -u aquacreds-backup -n 30
systemctl list-timers aquacreds-backup.timer
```
(Cron alternative: `infra/scripts/systemd/backup.cron.example`.) Each run does `pg_dump`
(custom format), gzips it, checks it with `gzip -t` and `pg_restore --list`, writes a `.sha256`,
uploads to `aquacreds-backups/postgres/` with rclone (in a container, so nothing is installed on the
host), confirms the object exists, deletes remote dumps older than 14 days, and keeps 3 local copies.
Any failure exits non-zero, so the systemd unit shows as `failed`.

**Restore drill (do this once now, then monthly).** It's non-destructive: the backup goes into a
**new** database and the live one is untouched.
```bash
infra/scripts/restore.sh --list
infra/scripts/restore.sh aquacreds-20261019T200000Z.dump.gz     # asks you to type the target DB name
# -> "restored aquacreds_restore_<ts>: alembic revision / tables = 0003_field_evidence / 16 tables"
infra/scripts/compose.sh exec db psql -U aquacreds -c 'DROP DATABASE aquacreds_restore_<ts>'
```
**Real recovery:** add `--replace`. After a second typed confirmation, it stops Caddy, the API and the
workers, renames the live DB to `aquacreds_pre_restore_<ts>`, swaps the restored DB in and starts
everything again. Drop the old DB once you're satisfied. Pre-deploy dumps (`backups/predeploy-*.dump`)
can be restored the same way by passing their path.

**Disaster (VM lost):** create a new VM (§3.2–3.4), restore `.env.prod` from your password manager,
deploy the last good tag, then run `restore.sh <latest>.dump.gz --replace`. Objects in R2 are
unaffected.

## 6. Monitoring

- **Uptime Kuma** (optional, 256 MB): set `COMPOSE_PROFILES=monitoring` in `.env.prod`, then run
  `infra/scripts/compose.sh --profile monitoring up -d uptime-kuma`. It listens on `127.0.0.1:3001`
  only. Open it with `ssh -L 3001:127.0.0.1:3001 deploy@<VM_IP>`, then go to <http://localhost:3001>
  and create the admin account straight away. Add an HTTP(s) monitor for `https://<API_DOMAIN>/readyz`
  (keyword `"ready"`), plus a Telegram, email or Discord notification.
- **External check:** a free UptimeRobot or Better Stack monitor on `/readyz` also alerts you when
  the whole VM is down, which a monitor on the same VM can't do.
- **Sentry** (optional, free developer plan): set `SENTRY_DSN`. The variable is passed to every app
  container; the SDK hook-up is part of the Phase 9 hardening work.
- **Logs:** `infra/scripts/compose.sh logs -f --tail 100 api`. Logs are JSON, and each container keeps
  at most 5 × 10 MB.
- **Resources:** `docker stats --no-stream`, `free -h`, `df -h /`.

## 7. Updating, rolling back, routine operations

| Task | Command |
|---|---|
| Release | merge to `main`, wait for CI, then `git tag vX.Y.Z && git push origin vX.Y.Z` |
| Deploy a specific build | Actions → Deploy → Run workflow → `image_tag: sha-1a2b3c4` |
| Roll back to the previous release | on the VM: `infra/scripts/deploy.sh --rollback`, or dispatch the workflow with the older `sha-…` tag |
| Status | `infra/scripts/compose.sh ps` · `cat /opt/aquacreds/.deploy/history.log` |
| Restart one service | `infra/scripts/compose.sh restart worker-geo` |
| Rotate a secret | edit `.env.prod`, then `infra/scripts/deploy.sh $(cat .deploy/current_tag)` |

Rollbacks **don't reverse migrations**. Write expand/contract (backwards-compatible) migrations so
the previous image still works with the newer schema. If one can't be made compatible, restore the
pre-deploy dump.

## 8. Cost guardrails (staying at $0)

- **Oracle:** stay within Always Free. That means A1 at 2 OCPU / 12 GB (or whatever your tenancy
  shows as Always Free), at most 200 GB of block storage including boot volumes and backups, and at
  most 10 TB/month of egress. Look for the *Always Free-eligible* label before creating anything.
  Don't create boot-volume backups beyond the free 5, load balancers beyond the free one, or
  non-A1/E2.1.Micro shapes.
- **Idle reclamation:** Oracle may reclaim Always Free instances that sit idle for 7 days (CPU,
  network and memory all under about 20%). A real workload, plus the nightly backup and geo jobs,
  usually keeps the VM above that. Upgrading to Pay As You Go removes the risk, but then set a
  **budget alert** (*Billing → Budgets*, for example $1) first.
- **Cloudflare R2:** 10 GB of storage. Uploads are capped at 25 MB each and backups are kept for 14
  days; check usage monthly in the R2 dashboard. Lifecycle rules stop old objects piling up.
- **GitHub:** while the repository is private, Actions minutes are limited (2,000/month on Free), and
  arm64 builds under QEMU take a while. Making the repository public makes Actions free. GHCR storage
  for private images counts against the Packages quota, so prune old `sha-…` tags occasionally.
- **DuckDNS, Let's Encrypt, Sentry developer plan, Uptime Kuma:** free. Keep `caddy_data` so you
  don't get re-issued certificates and hit rate limits.

## 9. Security checklist

- [x] Only 22/80/443 reachable; `db`/`valkey` not published and on an internal-only network
- [x] TLS everywhere (Caddy automatic HTTPS); HSTS, `nosniff`, `Referrer-Policy: no-referrer`,
      `X-Frame-Options: DENY`, restrictive CSP, `Permissions-Policy`; `Server` header removed
- [x] API schema/docs not public in production (`EXPOSE_API_DOCS=false`)
- [x] Production refuses development secrets (`APP_ENV=production`); Valkey password; SCRAM for Postgres
- [x] Python containers run as non-root (uid 10001) with `cap_drop: ALL`; `no-new-privileges` on all
- [x] Secrets only in `/opt/aquacreds/.env.prod` (mode 600, git-ignored); each container receives
      only the variables it needs
- [x] SSH: keys only, no root login, fail2ban; CI uses a dedicated key with strict host key checking
- [x] Unattended security upgrades; log rotation; memory limits on every container
- [x] Off-VM backups with a separate, bucket-scoped token; restore tested by `smoke-prod-local.sh`
- [ ] (manual) enable 2FA on the Oracle, Cloudflare, DuckDNS (via its login provider) and GitHub accounts
- [ ] (manual) store `.env.prod` in a password manager; restrict SSH ingress to your IP if it's static

## 10. Local smoke test of the production stack

```bash
infra/scripts/smoke-prod-local.sh     # ~3 min; builds images, runs, verifies, tears down (down -v)
```
This runs `compose.prod.yaml` plus the test-only `compose.smoke.yaml` (a throwaway SeaweedFS in place
of R2) as project `aquacreds-prodtest` on ports 18080/18443, with random secrets and Caddy's internal
CA for `localhost`. It checks: `deploy.sh`, `/readyz` through Caddy, blocked docs, security headers,
the HTTP→HTTPS redirect, an API → queue → worker round trip, `backup.sh` → S3, `restore.sh` into a
fresh DB, and an automatic rollback after a broken deploy. Nothing contacts Oracle or Cloudflare.
