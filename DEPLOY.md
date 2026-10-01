# Deploy Medical Coding AI

This guide takes you from the project folder to a running, HTTPS-secured coding workspace at a link like
**`https://203-0-113-7.sslip.io`**: coders sign in, upload notes, and review evidence-backed code suggestions.

There are two ways to run it. **Pick one before you start**, because the right one depends on the data you will process:

| | **A. Free pilot server** | **B. Production with real patient data** |
|---|---|---|
| For | Demos, evaluation, training, **synthetic or properly de-identified** notes | Real charts (PHI) |
| Where | Oracle Cloud *Always Free* virtual machine (Arm, 2 cores, 12 GB RAM) | A HIPAA-eligible cloud account **with a signed BAA** (AWS, Azure, Google Cloud) |
| LLM | OpenRouter (or the offline no-LLM mode) | A model provider that signs a BAA (for example Azure OpenAI or AWS Bedrock behind an OpenAI-compatible gateway) |
| Cost | $0 for the server, plus LLM usage | Cloud and LLM costs |
| Steps | This guide, sections 1–9 | Same software and steps, plus the checklist in section 10 |

> ⚠️ **Never put real patient data on the free pilot server or send it to OpenRouter.** Free tiers do not come with a
> Business Associate Agreement (BAA), and OpenRouter forwards prompts to third-party model providers. Use synthetic
> notes (the `data/` folder has more than 100) or notes de-identified under your organization's policy.

---

## Contents

0. [Before anything else (4 must-dos)](#0-before-anything-else-4-must-dos)
1. [What you are deploying](#1-what-you-are-deploying)
2. [Get the current code sets (FY2027)](#2-get-the-current-code-sets-fy2027)
3. [Create the free Oracle Cloud server](#3-create-the-free-oracle-cloud-server)
4. [Prepare the server](#4-prepare-the-server)
5. [Copy the project and reference files to the server](#5-copy-the-project-and-reference-files-to-the-server)
6. [Configure](#6-configure)
7. [Start it](#7-start-it)
8. [First sign-in, users, and a test run](#8-first-sign-in-users-and-a-test-run)
9. [Daily use, updates, backups](#9-daily-use-updates-backups)
10. [Production checklist (real patient data)](#10-production-checklist-real-patient-data)
11. [Troubleshooting](#11-troubleshooting)

---

## 0. Before anything else (4 must-dos)

1. **Revoke the leaked OpenRouter key.** The original `.env.example` contained a real key (`sk-or-v1-96d8…`) plus
   passwords. It has been replaced with placeholders in this version, but the key was shared. Delete it at
   <https://openrouter.ai/keys> and create a new one. Never commit `.env`.
2. **Use code sets valid for your dates of service.** The bundled reference files are **FY2026 and expired on
   2026-09-30**. FY2027 ICD-10-CM/PCS took effect on 2026-10-01. Until you load them (section 2), the app sends
   **every** chart to mandatory review and `/health/ready` reports `reference_data: expired`.
3. **CPT is licensed.** `data_sources/cpt_codes.csv` is AMA content. Keep it out of GitHub (`.gitignore` already
   excludes it). Load it (`CPT_LICENSE_ACKNOWLEDGED=true`) only if your organization holds a CPT license.
4. **This is decision support, not autonomous coding.** A certified coder approves every code; the software is built
   that way (only human coders can approve). Measure accuracy on your own charts before relying on it (section 8).

---

## 1. What you are deploying

`docker compose` runs eight containers on one server:

| Container | Role |
|---|---|
| `caddy` | HTTPS (automatic Let's Encrypt certificate), the only public entry point (ports 80/443) |
| `frontend` | Next.js review workspace (coders, auditors, admins) |
| `api` | FastAPI: ingestion, NLP, retrieval, coding, guideline rules, validation, review, audit |
| `worker` | Background jobs (document processing) |
| `postgres` | Database (PHI columns and stored files are encrypted) |
| `qdrant` | Vector index of the code sets |
| `migrate` | One-shot: database migrations, loads reference files, builds the vector index |
| `n8n` (+ `n8n-import`) | Optional automation (document intake webhooks, alerts, exports) |

Server size: at least **2 vCPU and 8 GB RAM** (12 GB recommended), and 50 GB of disk. The free Oracle Arm VM fits.

---

## 2. Get the current code sets (FY2027)

Download these free files on your computer, unzip them, and put them in the project's **`data_sources/`** folder.
Keep the official file names; the loader recognizes files by name and always uses the **newest** release.

| File(s) | Where | Notes |
|---|---|---|
| `icd10cm_order_2027.txt` (and `icd10cm_codes_2027.txt`) | CMS → *ICD-10* → *2027 ICD-10-CM* → "Code Descriptions in Tabular Order": <https://www.cms.gov/medicare/coding-billing/icd-10-codes> | **Required.** The authority for which codes are valid and billable |
| `icd10cm-tabular-2027.xml` | Same CMS page → "Tables and Index" zip (or CDC: <https://www.cdc.gov/nchs/icd/icd-10-cm/files.html>) | **Required** for Excludes1 / Code first / Use additional code. Without it the app warns and uses the 2026 notes |
| ICD-10-CM Official Guidelines FY2027 (PDF, file name containing `guideline`) | Same CMS page | Guideline citations and LLM grounding |
| `icd10pcs_codes_2027.txt` | CMS → *2027 ICD-10-PCS* | Inpatient procedures |
| HCPCS October 2026 (`HCPC2026_OCT_ANWEB*.txt`, `proc_notes_OCT2026.txt`) | <https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update> | Quarterly |
| MUE files effective 2026-10-01 (any name containing `MUE`) | <https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medically-unlikely-edits> | Quarterly |
| NCCI PTP edits (`ccipra*`, `ccioph*`) | <https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits> | Quarterly; bundling denials |

You can leave the 2026 files in the folder. Older releases stay in the database for audit, and the newest becomes active.
See `docs/DATA_SOURCES.md` for every data source and the update calendar.

---

## 3. Create the free Oracle Cloud server

1. Sign up at <https://signup.cloud.oracle.com>. A credit card is required for identity verification; *Always Free*
   resources are not charged. Choose a **home region** near you; it cannot be changed later.
2. Console → **Compute → Instances → Create instance**:
   - **Name:** `medcoding`
   - **Image:** *Canonical Ubuntu 24.04* (or 22.04)
   - **Shape:** *Change shape → Ampere → VM.Standard.A1.Flex*, **2 OCPUs, 12 GB memory** (the 2026 Always Free maximum)
   - **Networking:** keep "Create new virtual cloud network", with **Assign a public IPv4 address** checked
   - **SSH keys:** *Generate a key pair for me* → **download the private key** (keep it safe)
   - **Boot volume:** *Specify a custom size* → **100 GB**
   - Click **Create**. If you see "Out of host capacity", try another availability domain or retry later.
3. When it is **Running**, copy the **Public IP address** (for example `203.0.113.7`).
4. Open ports 80 and 443 in the cloud firewall: instance page → **Subnet** link → **Security Lists** → *Default
   Security List* → **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port `80`
   - Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port `443`

**Your web address without buying a domain:** replace the dots in your IP with dashes and add `.sslip.io`, e.g.
`203-0-113-7.sslip.io`. This always resolves to your server, and Caddy gets a real HTTPS certificate for it. If you own a
domain, create an `A` record (for example `coding.yourdomain.com` and `n8n.yourdomain.com`) pointing to the IP and use that instead.

---

## 4. Prepare the server

From your computer (Windows: use PowerShell; the key file name is the one you downloaded):

```bash
chmod 600 ~/Downloads/ssh-key-*.key          # macOS/Linux only
ssh -i ~/Downloads/ssh-key-*.key ubuntu@203.0.113.7
```

On the server:

```bash
git clone https://github.com/<you>/medical-coding-ai.git   # or copy the folder (section 5, option B)
cd medical-coding-ai
bash scripts/server_setup_ubuntu.sh       # installs Docker, opens 80/443 in the host firewall, adds swap
exit                                      # log out and back in so your user can run docker
```

---

## 5. Copy the project and reference files to the server

**Option A: private GitHub repository (recommended).** Push the project to a **private** repository, then
`git clone` it on the server (section 4). `.gitignore` keeps `.env` and `cpt_codes.csv` out of git, so copy those separately:

```bash
# from your computer, in the project folder
scp -i ~/Downloads/ssh-key-*.key data_sources/*2027* data_sources/*OCT* ubuntu@203.0.113.7:~/medical-coding-ai/data_sources/
scp -i ~/Downloads/ssh-key-*.key data_sources/cpt_codes.csv ubuntu@203.0.113.7:~/medical-coding-ai/data_sources/   # only if CPT-licensed
```

**Option B: copy the whole folder.** Zip it on your computer, then
`scp -i <key> medical-coding-ai.zip ubuntu@<ip>:~` and on the server run `sudo apt-get install -y unzip && unzip medical-coding-ai.zip`.

---

## 6. Configure

On the server, in the project folder:

```bash
python3 scripts/setup_env.py --domain 203-0-113-7.sslip.io --admin-email you@yourorg.org
```

- It asks for your **OpenRouter key**. Paste a *new* key, or press Enter to start in offline mode (no LLM).
- It writes `.env` with freshly generated secrets, production mode, secure cookies, `https://` origins, and
  `n8n.<domain>` for n8n.
- **It prints the admin password once. Save it.**

Then open `.env` (`nano .env`) and review these lines:

| Setting | Recommended | Why |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` (or `heuristic` = offline) | Offline mode is a fallback; see the accuracy notes in section 8 |
| `LLM_MODEL` | a top-tier model you have evaluated, e.g. `anthropic/claude-sonnet-4.5` | Accuracy depends mostly on this choice. Measure it (section 8) |
| `ENABLED_CODE_SYSTEMS` | `ICD-10-CM,ICD-10-PCS,HCPCS` (add `CPT` only if licensed) | |
| `CPT_LICENSE_ACKNOWLEDGED` | `false` unless licensed | |
| `API_WORKERS` / `WORKER_CONCURRENCY` | `1` / `1` on the 12 GB free VM | Memory |
| `REVIEW_ALERT_WEBHOOK_URL` | optional Slack/Teams webhook | Alerts for charts needing mandatory review |

> Keep `.env` private: it holds the database password, the JWT secret and the **encryption key**. If you lose
> `ENCRYPTION_KEYS`, the stored notes cannot be decrypted. Back it up in a password manager.

---

## 7. Start it

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f migrate
```

The first start builds the images, which takes **15–30 minutes** on the free Arm VM. Then `migrate` loads the code sets
and builds the vector index: about **30–60 minutes** the first time on 2 cores, much faster on later starts. Wait for:

```
ICD-10-CM 2027: … codes, valid 2026-10-01 to 2027-09-30
knowledge base ready
```

Press `Ctrl+C` to stop following the logs; the services keep running. Then check health:

```bash
docker compose ps                                     # api, worker, frontend, caddy … should be "running/healthy"
curl -s https://203-0-113-7.sslip.io/backend/health/ready
```

`"reference_data": "ok"` confirms the code sets are current. `expired: …` means the FY2027 files were not loaded (section 2).

Open **`https://203-0-113-7.sslip.io`** in your browser. That is the link you share with your coders. n8n (optional)
is at `https://n8n.203-0-113-7.sslip.io`.

---

## 8. First sign-in, users, and a test run

1. Sign in with the admin email and the password from step 6.
2. Create accounts for the team (roles: `coder`, `auditor`, `admin`; `service` is for integrations):
   ```bash
   docker compose exec api python -m app.cli create-user coder1@yourorg.org --role coder   # asks for a password (12+ chars, mixed)
   docker compose exec api python -m app.cli create-api-key n8n --role service      # only for n8n/integrations
   ```
3. Upload a synthetic note (`data/sample_notes/*.txt` or `data/raw/DOC001.txt`) and review the suggestions.
   Each code shows its evidence quote, validation status, guideline citation and confidence. Codes added or changed
   by the **guideline rules** say so in their rationale (for example `[guideline-rules-1.0 | I.C.9.a] …`).
4. **Measure accuracy on the server** before real use. Run the final blind set with your chosen LLM:
   ```bash
   docker compose cp data/evaluation api:/tmp/eval
   docker compose exec api python -m app.evaluation.runner --data-dir /tmp/eval --split blind2 --baselines full,retrieval_only
   ```
   The current results are in `README.md` → *Evaluation*. The **offline** mode scored F1 0.73 on the final blind set
   (0.98–1.00 on guideline-heavy sets), so the LLM mode plus human review is the production path. Then build an
   evaluation set from **your own de-identified, already-coded charts** (same JSONL format, see `data/README.md`) and
   calibrate the review threshold on it:
   ```bash
   docker compose exec api python -m app.evaluation.runner --data-dir /tmp/eval --split validation --calibrate /tmp/eval/calibration.json
   ```

---

## 9. Daily use, updates, backups

**Coder workflow:** documents → open a chart → approve, edit (with code search) or reject each suggestion, add missed
codes → finalize. Charts routed **mandatory** (low confidence, validation flags, expired code sets) must be reviewed
line by line. Every action is written to the tamper-evident audit trail (**Audit** page; `python -m app.cli verify-audit`).

**Update the software:** `git pull` and then `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.

**Update code sets** (every October 1 and quarterly for HCPCS/MUE/PTP): put the new files in `data_sources/` and run
`docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate`, then
`docker compose -f docker-compose.yml -f docker-compose.prod.yml restart api worker`.

**Backups** (daily, kept off the server):
```bash
docker compose exec -T postgres pg_dump -U medcoding medcoding | gzip > backup-$(date +%F).sql.gz
docker run --rm -v medcoding_storage:/data -v "$PWD":/out alpine tar czf /out/storage-$(date +%F).tgz /data
# plus a copy of .env (encryption keys!) in your password manager
```

More detail (monitoring metrics, key rotation, recalibration): `docs/operations.md`. Security design: `docs/security-compliance.md`.

---

## 10. Production checklist (real patient data)

Use the same software and steps on a HIPAA-eligible host. Before the first real chart:

- [ ] Signed **BAA** with the cloud provider; server in a private subnet, ports 80/443 only, disk encryption on, OS auto-updates.
- [ ] LLM provider **with a BAA** and zero data retention. Point `OPENROUTER_BASE_URL` to an OpenAI-compatible gateway in
      front of a BAA-covered model (for example LiteLLM → Azure OpenAI / AWS Bedrock), or run `LLM_PROVIDER=heuristic`.
- [ ] Your own domain with TLS; SSO or strong passwords; least-privilege roles; MFA at the cloud console.
- [ ] Current code sets loaded; `/health/ready` shows `reference_data: ok`. Calendar reminders for October 1 and quarterly updates.
- [ ] Accuracy measured on **your** de-identified charts per specialty; review threshold calibrated; compliance sign-off.
- [ ] Backups tested (restore drill), `ENCRYPTION_KEYS` stored in a secrets manager, audit-chain verification scheduled.
- [ ] CPT used only under license; no PHI in logs, tickets or screenshots; incident-response contact defined.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| Browser cannot reach the site | Ports 80/443 open in **both** the Oracle Security List (section 3.4) and the host firewall (`scripts/server_setup_ubuntu.sh`). `docker compose ps` shows `caddy` running |
| Certificate error / "too many certificates" | The domain must resolve to the server IP. With sslip.io, check the dashes (`203-0-113-7.sslip.io`). `docker compose logs caddy` |
| `migrate` exits with an error | `docker compose logs migrate`. A common cause is a misnamed reference file: keep the official names. Re-run with `docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate` |
| `Insecure production configuration: …` | A secret in `.env` is a placeholder. Re-run `python3 scripts/setup_env.py --force --domain …` |
| Every chart is "mandatory review" | `curl …/backend/health/ready`. If `reference_data` says expired, load the FY2027 files (section 2) |
| API restarts / out of memory | Set `API_WORKERS=1` and `WORKER_CONCURRENCY=1` in `.env`, then `up -d`. Check `free -h` (swap is added by the setup script) |
| LLM errors in the logs | Check the key, credit and `LLM_MODEL` name on OpenRouter; or set `LLM_PROVIDER=heuristic` |
| Sign-in works on http but not https | In production, cookies are secure-only. Always use the `https://` link |
