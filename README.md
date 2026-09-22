# Puckwire

A personal, Rotoworld-style fantasy hockey news page: player notes, today's starting goalies, line combinations, and your ESPN roster flagged throughout.

No database. Everything runs from one private GitHub repo plus a free Streamlit app.

## How it works

Every 20 minutes (7am to 1am Toronto time), a GitHub Action does four things:

- It fetches new notes from RotoWire and CBS Sports, and starting goalies from Daily Faceoff.
- Once an hour, it also refreshes line combinations and your ESPN roster.
- It deletes news older than 3 days and goalie starts from past days.
- It saves what's left as a few small JSON files on a branch called `data`.

The `data` branch is wiped and replaced with a single commit on every run. The repo never builds up history, so it stays under 1 MB. The Streamlit app reads those files and shows the page.

## Setup

You'll need a GitHub account and a Streamlit Community Cloud account (share.streamlit.io, sign in with GitHub).

### 1. Put the code on GitHub

1. On github.com, create a new repository named `puckwire` and set it to **Private**.
2. On the new repo page, click "uploading an existing file." Drag in everything from the unzipped `puckwire` folder, then click "Commit changes."
   - The `.github` and `.streamlit` folders are hidden on a Mac. In Finder, press Cmd+Shift+. to show them. The `.github` folder is required.
   - Skip the `data` folder; it's only for local previews.

### 2. Add your ESPN league (optional, for the star on your players)

In the repo, go to Settings > Secrets and variables > Actions > New repository secret. Add:

- `ESPN_LEAGUE_ID`: the number after `leagueId=` in your ESPN league URL.
- `ESPN_TEAM_ID`: the number after `teamId=` when you're viewing your team.
- Private leagues only: `ESPN_S2` and `ESPN_SWID`. To find them, log in to fantasy.espn.com, open developer tools > Application > Cookies, and copy those two values. Keep the braces in SWID.
- Optional: `ANTHROPIC_API_KEY` turns on one-line AI takes for your players, capped at 10 a day.

### 3. Run it the first time

Go to the Actions tab. Enable workflows if GitHub asks, then click **collect** > **Run workflow**. After about a minute, a `data` branch appears in the branch dropdown. From then on it runs by itself.

### 4. Make a read-only key for the website

1. Go to GitHub > your profile picture > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token.
2. Under Repository access, choose "Only select repositories" and pick `puckwire`.
3. Under Permissions, set **Contents: Read-only**. Leave everything else alone.
4. Generate the token and copy it. It starts with `github_pat_`.

### 5. Turn on the website

1. On share.streamlit.io, click Create app, pick your `puckwire` repo and branch `main`, and set the main file to `app.py`.
2. Under Advanced settings > Secrets, paste the following (see `.streamlit/secrets.toml.example`):
   ```
   GITHUB_REPO = "your-github-username/puckwire"
   GITHUB_TOKEN = "github_pat_..."
   ```
3. Click Deploy. Afterwards, go to the app's Settings > Sharing, make it private, and invite only your email.

## Local preview (optional)

```bash
pip install -r requirements.txt
python collector/collect.py all     # writes ./data from live sources
streamlit run app.py                # with no secrets, reads ./data
```

## Things to know

- **Changing retention:** edit `NEWS_RETENTION_DAYS` in `.github/workflows/collect.yml`. The default is 3.
- **Scrapers break.** CBS and Daily Faceoff can change their pages without notice. If a section goes stale, the Actions tab shows which step failed.
- **Keep it private.** The notes belong to RotoWire and CBS; the page is for your eyes only.
- **Schedules sleep.** GitHub pauses scheduled workflows after 60 days without a commit to `main`, and it emails you first. Click "Enable workflow" in the Actions tab to wake it.
- **The token expires.** Fine-grained tokens expire on the date you choose. When it does, make a new one and paste it into the Streamlit secrets.
