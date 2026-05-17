# 📡 SignalRank Lite

A micro-SaaS utility that evaluates and tracks local business search visibility on Google — in real time.

## What it does

**Analyzer:** Enter a business name and a target keyword. SignalRank fetches live Google search data via SerpApi, locates the business in the top 20 organic results, and returns a calculated Visibility Score (0–100) with a rank position and status label.

**History Dashboard:** Every search is saved automatically. The dashboard shows all past queries, summary metrics, and a score trend chart per business so you can track visibility changes over time.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Frontend + UI | Streamlit |
| Data fetching | SerpApi (Google SERP JSON) |
| Scoring | Custom deterministic algorithm (Python) |
| Data storage | CSV via pandas |
| Deployment | Streamlit Community Cloud |

---

## Scoring Algorithm

The score is calculated from the rank position returned by SerpApi:

| Rank Position | Score | Label |
|---|---|---|
| #1 | 100 | 🟢 High Visibility |
| #2 | 95 | 🟢 High Visibility |
| #3 | 90 | 🟢 High Visibility |
| #4–10 | 71–89 | 🟡 Good Visibility |
| #11–20 | 42–69 | 🟠 Needs Work |
| Not Found | 0 | 🔴 Invisible |

---

## How to Run Locally

**1. Clone the repo**
```bash
git clone https://github.com/YOUR_USERNAME/signalrank-lite
cd signalrank-lite
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Run the app**
```bash
streamlit run app.py
```

**4. Enter your SerpApi key**
Get a free key at [serpapi.com](https://serpapi.com) (100 free searches/month). Enter it in the sidebar when the app opens.

---

## Deploying to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Under **Secrets**, add:
```toml
SERPAPI_KEY = "your_key_here"
```
5. Deploy — done.

---

## Architecture

```
User Input (business name + keyword)
        │
        ▼
Streamlit Frontend (app.py)
        │
        ▼
fetch_rank() → GET https://serpapi.com/search?q={keyword}&num=20
        │
        ▼
Parse JSON → search organic_results for business name/URL
        │
        ▼
calculate_score(position) → deterministic weighted score
        │
        ▼
Display result card + save to history.csv
```

---

## Challenges & Decisions

**Why Streamlit?**
Streamlit lets you build a fully interactive web app in pure Python — no HTML, CSS, or JavaScript needed. For a one-week build, this was the right tradeoff: focus on logic and product thinking, not frontend boilerplate.

**Why CSV instead of a database?**
For this scope, a CSV file handled by pandas is sufficient and keeps the app completely dependency-free on the storage side. A production version would use PostgreSQL or Supabase.

**Why not mock the data?**
The scoring is only meaningful if the data is real. SerpApi's free tier (100 searches/month) was more than enough for development and demo purposes, and it meant the app could be honestly described as a real-time visibility tool — not a prototype.

**Edge cases handled:**
- Business not found in top 20 → score of 0, "Invisible" label
- Empty form inputs → validation warning before API call
- SerpApi HTTP errors → caught and displayed clearly
- Missing API key → prompt shown before any call is made

---

## What I'd build next

- Multiple keyword tracking per business (keyword portfolio)
- Weekly automated re-checks with email alerts
- Competitor comparison (track two businesses on the same keyword)
- Supabase backend for multi-user support
- Export history as PDF report
