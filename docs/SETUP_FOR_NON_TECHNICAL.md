# AEGIS Pulse — Setup Guide for Non-Technical Users

> **What this is**: the only document you need to read to go from a fresh Windows
> laptop to a running AEGIS system. No prior programming knowledge required.
> **Time needed**: about 45–60 minutes total. Most of it is unattended waiting
> while things download.
> **If anything fails**: every error has a code like `AEGIS-BOOT-0013`. Search
> the `docs/errors/` folder for that code to find the fix.

---

## Step 0 — What you need before starting

- [ ] A Windows **10** (build 19044 or newer) or **Windows 11** laptop.
- [ ] At least **16 GB of RAM** (32 GB strongly recommended).
- [ ] At least **80 GB of free disk space** (250 GB recommended).
- [ ] A **stable internet connection** for the full 45–60 minutes.
- [ ] **Administrator access** to the laptop (you'll be asked for a password).
- [ ] About **60 minutes** where you can leave the laptop alone.
- [ ] _(Optional)_ A **Telegram** account — if you want to receive live alerts on your phone.
- [ ] _(Optional)_ An **NVIDIA GPU** with 8 GB+ VRAM — AEGIS runs without one, just slower.

> **Tip**: Plug the laptop into power. Do **not** close the lid during Steps 1, 5, 6 and 7.

---

## Step 1 — Turn on WSL2 (about 5 minutes)

WSL2 is the Linux environment that sits alongside Windows. AEGIS runs inside it.

1. Press the **Windows key**. Type **PowerShell**.
2. In the results, **right-click** on "Windows PowerShell" and choose **Run as administrator**.
3. A blue window opens. Click **Yes** on any permission prompt.
4. Copy-paste this exactly and press **Enter**:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

5. Wait for the text to stop scrolling (~2 minutes).
6. When prompted, **restart the laptop**.
7. After restart, a **black window** titled "Ubuntu" opens by itself.
   - It asks for a **UNIX username** — type anything lowercase like `jane` and press Enter.
   - It asks for a **password** — type one and press Enter. **Write it down.** You won't see the characters as you type; that is normal.
   - Repeat the password.
8. **✓ You should see** a prompt like `jane@your-laptop:~$`.

> **Stuck?** If the black window never appears, open the Start menu and search for "Ubuntu 24.04 LTS" — click it.

---

## Step 2 — Install Docker Desktop (about 5 minutes)

Docker runs the databases and services AEGIS needs.

1. Open your web browser and go to: <https://www.docker.com/products/docker-desktop/>
2. Click **Download for Windows**.
3. Double-click the downloaded `Docker Desktop Installer.exe`.
4. On the **Configuration** screen, make sure **"Use WSL 2 instead of Hyper-V"** is **checked**.
5. Click **OK** and wait for installation (about 3 minutes).
6. When prompted, click **Close and log out**. Log back in.
7. Docker Desktop opens itself. **Accept the license terms** and complete any onboarding screens (you can skip sign-up).
8. Click the **gear icon** (⚙️) → **Resources** → **WSL Integration**.
   - Make sure **"Enable integration with my default WSL distro"** is **on**.
   - Under "Enable integration with additional distros", make sure **Ubuntu-24.04** is **on**.
   - Click **Apply & Restart**.
9. Wait until the **whale icon** in your system tray (bottom-right) is solid (not animating).
10. **✓ Verification**: open Ubuntu (Start menu → Ubuntu 24.04 LTS). In the black window, type:

    ```bash
    docker version
    ```

    You should see two sections — **Client** and **Server** — with version numbers. No red errors.

---

## Step 3 — Install VS Code + Remote-WSL extension (about 3 minutes)

VS Code is the editor you'll use to view files and watch logs.

1. Go to <https://code.visualstudio.com/> and click **Download for Windows**.
2. Run the installer. On the **Select Additional Tasks** page, check **all** boxes (especially "Add to PATH" and "Register Code as editor").
3. Finish installation and launch VS Code.
4. Click the **Extensions icon** on the left sidebar (it looks like four small squares, with one detached).
5. In the search box type: **Remote - WSL** (exactly). Click **Install** on the first result (publisher: Microsoft).
6. Wait 10 seconds.
7. **✓ Verification**: at the very bottom-left corner of VS Code, click the green `><` icon. A menu drops down. Choose **Connect to WSL**.
   - A new VS Code window opens.
   - The bottom-left corner now says **`WSL: Ubuntu-24.04`** in green.

---

## Step 4 — Get the AEGIS code (about 2 minutes)

1. In the new VS Code window (the one with `WSL: Ubuntu-24.04` in the corner), open a terminal: menu bar → **Terminal** → **New Terminal**.
2. A terminal panel opens at the bottom. Type this exactly and press **Enter** after each line:

   ```bash
   mkdir -p ~/code
   cd ~/code
   git clone https://github.com/YOUR-ORG/aegis-pulse.git aegis-pulse
   cd aegis-pulse
   ```

   > Replace `YOUR-ORG/aegis-pulse` with the actual repository URL you were given.

3. **✓ Verification**: type `ls` and press Enter. You should see folders like:
   ```
   bootstrap/   docs/   src/   docker-compose.yml   README.md
   ```

> **Why `~/code` and not the Desktop?** Files under `~/code` are on WSL's fast native disk. Files under `/mnt/c/Users/…` (your Windows Desktop) are **10× slower** to read/write because of a cross-filesystem bridge. You **will** notice it.

---

## Step 5 — Run the bootstrapper (about 15 minutes, mostly unattended)

This one command installs Python, configures the system, and pulls everything AEGIS needs.

1. Still in the VS Code terminal (inside `~/code/aegis-pulse`), run:

   ```bash
   bash bootstrap/wsl/00_all.sh
   ```

2. Progress logs scroll by. Somewhere in the first 30 seconds it asks:

   ```
   [sudo] password for jane:
   ```

   Type the password you set in **Step 1** and press Enter. (You still won't see characters — normal.)

3. Leave it running. You may walk away. Typical run: 15–20 minutes, depending on your internet speed.

   During this run the script will:
   - Install ~50 Ubuntu packages (build tools, Python dependencies, TLS libs)
   - Compile Python **3.12.7** (takes about 3 minutes)
   - Install `uv` (the fast Python package manager)
   - Configure DNS, clock-sync (chrony), and file-descriptor limits
   - Detect your NVIDIA GPU if present, and install CUDA 12.4 — or skip to CPU mode if not
   - Verify Docker is reachable
   - Download Playwright browsers (~400 MB)
   - Generate an SSH key for you
   - Install local TLS certificate tooling (`mkcert`)

4. At the end you will see a **table of green checks**. If anything is red, stop and open `docs/errors/` for the matching error code.

5. If the script ends with a **yellow box** telling you to run `wsl --shutdown`:
   - Open a **Windows PowerShell** window (not inside VS Code).
   - Run: `wsl --shutdown`
   - Reopen Ubuntu (Start menu → Ubuntu 24.04 LTS).
   - Reopen VS Code's WSL window.

6. **✓ Verification**: back in the Ubuntu terminal (VS Code), run:

   ```bash
   bash bootstrap/aegis-doctor
   ```

   You should see a table where **every row ends in `[OK]`** (a few `[WARN]` rows are acceptable — yellow is fine, red is not).

---

## Step 6 — Create your secrets file (about 3 minutes)

1. In the VS Code terminal, run:

   ```bash
   cp .env.example .env
   ```

2. In the VS Code file tree on the left, click **`.env`** to open it.
3. Find any line that has the comment `# REQUIRED` after it. For each such line, fill in a value.
   - _(Optional)_ If you want Telegram alerts, create a bot via [@BotFather](https://t.me/botfather) on Telegram; paste the resulting token into `TELEGRAM_BOT_TOKEN=`.
   - Every other line has a sensible default — leave them as-is unless you know what you're doing.
4. Save the file (**Ctrl+S**).
5. **✓ Verification**: run

   ```bash
   bash bootstrap/aegis-doctor --secrets
   ```

   The last row should read `[OK] Required secrets (.env) all required secrets present`.

---

## Step 7 — Start the system (5 min first time, 30 s thereafter)

1. In the terminal, run:

   ```bash
   aegis up
   ```

   The first time, Docker pulls about 3–5 GB of images. Subsequent starts take under 30 seconds.

2. While it runs, you can open <http://localhost:8000> in your Windows web browser — the AEGIS dashboard loads as each service becomes ready.

3. **✓ Verification**: run

   ```bash
   aegis status
   ```

   You should see every service listed as **`healthy`** (or `running` for stateless ones).

4. Open these URLs in your browser:
   - **AEGIS Dashboard**: <http://localhost:8000>
   - **Grafana** (metrics): <http://localhost:3000>   (login: admin / admin)
   - **Jaeger** (traces): <http://localhost:16686>

---

## Step 8 — Run your first scrape (about 2 minutes)

1. In the terminal:

   ```bash
   aegis scrape --source reddit --subreddit buyitforlife --limit 50
   ```

2. **✓ Verification**:
   ```bash
   aegis signals tail
   ```

   Rows stream in as they arrive. On the dashboard, the **Live Signals** panel lights up.

You now have a working AEGIS installation. 🎉

---

## Step 9 — Daily use

| When           | Command / action                              |
|----------------|-----------------------------------------------|
| Morning        | `aegis up` (or set Docker Desktop to auto-start on login) |
| Watch activity | `aegis tail`                                  |
| One-page brief | `aegis report daily`                          |
| Stop at night  | `aegis down` (optional — it auto-scales to zero when idle) |
| Full reset     | `aegis reset` (wipes signals; keeps models)   |

---

## Step 10 — Troubleshooting

1. **Every error** AEGIS emits starts with a code like `AEGIS-BOOT-0013` or `AEGIS-SCRAPE-0042`.
2. Paste that code into a file search under `docs/errors/` — each has its own `.md` file with the exact diagnosis and fix.
3. If still stuck, run:

   ```bash
   aegis support-bundle
   ```

   This generates a **sanitised** zip (no secrets, no personal data) you can share for help: `~/.aegis/support-bundle-<timestamp>.zip`

### Common first-day issues

| Symptom | Cause | Fix |
|---|---|---|
| `docker: command not found` in WSL | Docker Desktop WSL integration is off | Docker Desktop → Settings → Resources → WSL Integration → enable Ubuntu-24.04, click **Apply & Restart**. Close and reopen the VS Code terminal. |
| `localhost:8000` won't load in Windows browser | WSL localhost forwarding glitch | In PowerShell: `wsl --shutdown`, then reopen Ubuntu. |
| Clock is wrong, TLS errors everywhere | WSL clock drifted after host sleep | `sudo chronyc -a makestep` |
| Bootstrap script stopped midway | Network flake | Just re-run `bash bootstrap/wsl/00_all.sh` — completed steps are skipped. |
| VS Code terminal opens inside Windows, not WSL | Wrong window | Click the bottom-left `><` icon and choose **Connect to WSL**. |

---

## FAQ

**Q: Can I run this on a Mac or Linux machine?**
Yes, but these instructions are Windows-specific. On macOS/Linux, skip Steps 1–3 and start at Step 4. Run `bootstrap/wsl/00_all.sh` directly (it detects non-WSL and warns you).

**Q: Do I need to know Python?**
No. But if you want to customise anything beyond what the `aegis` CLI offers, a basic grasp helps.

**Q: How much does this cost to run?**
On the default free-tier path: **$0.00/month**. Everything runs locally or on free tiers. Paid providers are optional and explicitly gated — see `docs/COSTS.md`.

**Q: Can I close my laptop?**
Yes. AEGIS checkpoints to disk; on wake it catches up. If you need it running 24/7 without your laptop being on, the free-tier guide in `docs/COSTS.md` shows you how to move it to Oracle Cloud's always-free ARM VM.

**Q: How do I update AEGIS?**
```bash
cd ~/code/aegis-pulse
git pull
bash bootstrap/wsl/00_all.sh      # idempotent, fast on re-run
aegis up --force-recreate
```

---

## Where things live

| Thing                     | Path                                     |
|---------------------------|------------------------------------------|
| Code                      | `~/code/aegis-pulse/`                    |
| Bootstrap logs            | `~/.aegis/bootstrap.log`                 |
| Runtime config            | `~/.aegis/config.env`                    |
| Dev TLS certs             | `~/.aegis/tls/`                          |
| Your secrets              | `~/code/aegis-pulse/.env`                |
| SSH key (you generated)   | `~/.ssh/id_ed25519` (private) + `.pub`  |
| Docker data               | Managed by Docker Desktop (use `aegis reset` to wipe) |

---

**Next**: once everything is green, read `docs/USER_GUIDE.md` for a tour of the dashboard and the `aegis` CLI.
