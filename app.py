import streamlit as st
import requests
import pandas as pd
import os
from datetime import datetime

# ── Page config ────────────────────────────────────────────────────────────────
# layout="wide" gives us more horizontal space for the results section.
# The dark theme is set in .streamlit/config.toml — I prefer doing it
# there rather than calling st.markdown() CSS hacks, which feel fragile.
st.set_page_config(
    page_title="SignalRank Lite",
    page_icon="📡",
    layout="wide",
)

HISTORY_FILE = "history.csv"

# Pull API key from Streamlit Cloud secrets (env var) if deployed,
# otherwise fall back to empty string and ask the user at runtime.
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


# ══════════════════════════════════════════════════════════════════════════════
# MATCHING LOGIC
#
# This was the trickiest part to get right. My first version just did a
# simple substring check — "kfc" in title — which worked for obvious cases
# but kept failing on real brands. The problems I hit:
#
# 1. "Starbucks" doesn't appear in "starbucks.com.my" unless you lowercase both
# 2. "Mixue" doesn't appear in "Mixue Ice Cream & Tea" (it does, but only
#    after lowercasing consistently)
# 3. Multi-word names like "Secret Recipe" could match on just "Secret" which
#    is risky — but in practice it's fine because we're already in a branded
#    keyword context
#
# My fix: two-pass matching. Full name first (fast, precise), then word-level
# (catches partial brand names in longer titles/URLs).
# ══════════════════════════════════════════════════════════════════════════════

def name_matches(business_name: str, title: str, snippet: str, link: str) -> bool:
    """
    Two-strategy matching to handle real-world brand name variations.
    Strategy 1: full name match (lowercase normalised)
    Strategy 2: word-by-word match, filtering stop words to avoid false positives
    """
    biz       = business_name.lower().strip()
    title_l   = title.lower()
    snippet_l = snippet.lower()
    link_l    = link.lower()

    # Pass 1 — exact full name anywhere in the result
    if biz in title_l or biz in link_l or biz in snippet_l:
        return True

    # Pass 2 — word-level match (handles "Mixue" inside "Mixue Ice Cream & Tea")
    stop_words = {"the", "and", "of", "a", "an", "in", "at", "for", "&", "to", "by"}
    tokens = [w for w in biz.split() if w not in stop_words and len(w) > 1]

    for token in tokens:
        if token in title_l or token in link_l:
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# SERP FETCHING
#
# num=30 covers 3 pages of Google results. I tried 50 but SerpApi's
# free tier throttles harder past 30 and rank >30 has basically 0 CTR
# (Ahrefs research puts it at under 0.5%). So 30 is the sweet spot.
#
# gl="my" + hl="en" was a crucial addition. Without geo-targeting, searches
# like "best coffee KL" returned results for the UK or US which makes no
# sense for a Malaysian business visibility tool.
# ══════════════════════════════════════════════════════════════════════════════

def fetch_serp_data(keyword: str, api_key: str, num_results: int = 30) -> dict:
    """
    Hits SerpApi and returns the raw JSON response.
    Separated from matching so I can reuse organic_results for the
    top 10 table without a second API call.
    """
    params = {
        "engine":  "google",
        "q":       keyword,
        "num":     num_results,
        "api_key": api_key,
        "gl":      "my",   # geo: Malaysia
        "hl":      "en",   # language: English
    }
    resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_business_in_results(business_name: str, organic: list) -> dict:
    """
    Iterates through organic results (1-indexed) and returns the first match.
    Returns found=False if no match in the full result set.
    """
    for i, result in enumerate(organic, start=1):
        if name_matches(
            business_name,
            result.get("title",   ""),
            result.get("snippet", ""),
            result.get("link",    ""),
        ):
            return {
                "found":    True,
                "position": i,
                "title":    result.get("title",   ""),
                "snippet":  result.get("snippet", ""),
                "link":     result.get("link",    ""),
            }
    return {"found": False, "position": None}


# ══════════════════════════════════════════════════════════════════════════════
# SCORING ALGORITHM
#
# I wanted the score to feel meaningful, not just a linear rank number.
# Real CTR data shows a steep cliff from rank 1 → 3 → 10, then it
# basically flatlines. So my tiers reflect that:
#
#   Rank 1:    100  (the single best position)
#   Rank 2-3:  90-95 (still excellent, top of page)
#   Rank 4-10: 71-89 (page 1, diminishing returns)
#   Rank 11-20: 41-68 (page 2, most users never reach here)
#   Rank 21-30: 11-38 (page 3, functionally invisible)
#   Not found:  0
# ══════════════════════════════════════════════════════════════════════════════

def calculate_score(position: int | None) -> tuple[int, str, str]:
    """Returns (score, label, colour_emoji)."""
    if position is None:
        return 0,   "Invisible",          "🔴"
    elif position == 1:
        return 100, "Top Result",          "🟢"
    elif position <= 3:
        return 90 + (3 - position) * 5,   "High Visibility",      "🟢"
    elif position <= 10:
        return 89 - (position - 4) * 3,   "Good Visibility",      "🟡"
    elif position <= 20:
        return 68 - (position - 11) * 3,  "Needs Work",           "🟠"
    elif position <= 30:
        return max(38 - (position-21)*3, 10), "Very Low Visibility", "🔴"
    else:
        return 0, "Invisible", "🔴"


# ══════════════════════════════════════════════════════════════════════════════
# SUGGESTIONS ENGINE
#
# After building the score display I realised just showing a number isn't
# enough — a business owner's next question is always "okay so what do I
# do about it?" So I wrote a rule-based suggestion generator.
#
# I deliberately kept this as pure if/else rather than calling the AI API
# because: (a) it's explainable, (b) it's free, (c) I can reason about
# exactly what advice gets shown and why. No black box.
# ══════════════════════════════════════════════════════════════════════════════

def generate_suggestions(business: str, keyword: str, position: int | None) -> list[str]:
    """Tier-based actionable recommendations. More urgent as rank drops."""

    if position is None:
        return [
            f"🔍 **{business}** isn't in the top 30 results for **'{keyword}'** — most customers searching this will never find you.",
            "📋 **Claim your Google Business Profile** at business.google.com. It's free and the single biggest lever for local search visibility.",
            f"🏷️ **Add '{keyword}' to your website** — in the page title, main heading, and first paragraph. Google needs clear relevance signals.",
            "🔗 **Get listed on local directories** (Foursquare, local chambers of commerce). Each listing counts as a backlink.",
            "⭐ **Actively collect Google reviews.** Review volume and recency are strong ranking signals for local businesses.",
        ]
    elif position == 1:
        return [
            f"🏆 **Rank #1 — you're dominating this keyword.** The job now is to stay here.",
            "📊 **Re-check weekly** — rankings shift, and catching a drop early is much easier than recovering from one.",
            f"🎯 **Expand to related keywords.** Strong rank #1 authority here likely means you can rank for similar terms — test them.",
        ]
    elif position <= 3:
        return [
            f"🌟 **Rank #{position} is excellent** — you're capturing a strong share of clicks. The goal is pushing to #1.",
            "📸 **Keep your Google Business Profile active** — fresh photos, weekly posts, and quick review responses all reinforce ranking signals.",
            "📊 **Track this keyword weekly** so you catch any movement before it becomes a real drop.",
        ]
    elif position <= 10:
        return [
            f"📈 **Rank #{position} — Page 1, but ranks 1-3 get 5-10x more clicks.** Worth the push.",
            "✍️ **Rewrite your page title and meta description** to be more compelling. Higher click-through rate itself improves ranking.",
            "🔗 **Build 2-3 quality local backlinks** — a mention in a local blog or news site carries real weight.",
            "📱 **Test your mobile load speed** — Google uses mobile-first indexing and penalises slow sites.",
        ]
    elif position <= 20:
        return [
            f"⚠️ **Rank #{position} is Page 2** — studies show under 1% of users ever go past Page 1. This needs work.",
            f"📝 **Create a dedicated page targeting '{keyword}'** with at least 500 words of useful, original content.",
            "🗺️ **Fill out every field on your Google Business Profile** — hours, photos, services, weekly posts, review responses.",
            "🔍 **Study the Page 1 results** — check their page titles, content depth, and review count to understand what you're up against.",
        ]
    else:
        return [
            f"🚨 **Rank #{position} is Page 3** — under 0.5% of users reach here. Treat this as starting from scratch.",
            "🏗️ **Start with fundamentals:** claim your Google Business Profile, verify your address, complete every field.",
            f"🎯 **Try a more specific keyword** — instead of '{keyword}', add your city or neighbourhood. Narrower = easier to rank.",
            "📅 **Set realistic expectations** — SEO takes 3-6 months of consistent effort. Track weekly to measure progress.",
        ]


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY — CSV via pandas
#
# I chose CSV over a proper database for this version. The reasoning:
# - No backend infra needed
# - pandas reads/writes natively
# - Perfectly fine for single-user usage
# - If this scaled to multi-user I'd migrate to Supabase or PostgreSQL,
#   but that's a future version problem
# ══════════════════════════════════════════════════════════════════════════════

def load_history() -> pd.DataFrame:
    if os.path.exists(HISTORY_FILE):
        return pd.read_csv(HISTORY_FILE)
    return pd.DataFrame(columns=["Date", "Business", "Keyword", "Position", "Score", "Label"])


def save_to_history(business: str, keyword: str, position, score: int, label: str):
    df  = load_history()
    row = pd.DataFrame([{
        "Date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Business": business,
        "Keyword":  keyword,
        "Position": f"#{position}" if position else "Not in Top 30",
        "Score":    score,
        "Label":    label,
    }])
    pd.concat([row, df], ignore_index=True).to_csv(HISTORY_FILE, index=False)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.title("📡 SignalRank Lite")
st.sidebar.markdown("Real-time Google search visibility checker for local businesses.")
st.sidebar.markdown("---")

page = st.sidebar.radio("", ["🔍 Analyzer", "📊 History Dashboard"], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.markdown("**🔑 SerpApi Key**")

# If deployed with secrets, use that silently. Otherwise show an input.
if not SERPAPI_KEY:
    api_key_input = st.sidebar.text_input(
        "Paste your key here",
        type="password",
        placeholder="Get a free key at serpapi.com",
        label_visibility="collapsed",
    )
    st.sidebar.caption("Free tier includes 100 searches/month. [Get your key →](https://serpapi.com)")
else:
    api_key_input = SERPAPI_KEY
    st.sidebar.success("✅ API key loaded")

st.sidebar.markdown("---")
st.sidebar.caption("Scores are calculated using a weighted rank algorithm based on real CTR data — not AI-generated.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

if page == "🔍 Analyzer":

    # Hero section — gives first-time users context immediately
    st.title("📡 SignalRank Lite")
    st.markdown("### Check how visible any business is on Google — in real time.")
    st.markdown(
        "Enter a business name and a keyword a customer might search. "
        "SignalRank fetches live Google results, finds the business, and returns "
        "a **Visibility Score** plus actionable recommendations."
    )
    st.markdown("---")

    # ── Input form ───────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        business = st.text_input(
            "🏢 Business Name",
            placeholder="e.g. Mixue, Starbucks, KFC, VCR Cafe",
            help="Enter the brand or business name exactly as it would appear in Google results."
        )
    with col_b:
        keyword = st.text_input(
            "🔎 Target Keyword",
            placeholder="e.g. ice cream KL, coffee bangsar, laptop shop Malaysia",
            help="The keyword a potential customer would type into Google to find this business."
        )

    check_btn = st.button("🔍 Check Visibility", type="primary", use_container_width=True)

    # Validation before hitting the API
    if check_btn:
        if not api_key_input:
            st.error("⚠️ No SerpApi key found. Paste your key in the sidebar to continue.")
            st.stop()
        if not business.strip() or not keyword.strip():
            st.warning("Please fill in both the business name and keyword.")
            st.stop()

        # ── Main analysis ────────────────────────────────────────────────────
        progress = st.progress(0, text="Connecting to SerpApi…")
        try:
            progress.progress(25, text="Fetching live Google results…")
            serp_data = fetch_serp_data(keyword, api_key_input, num_results=30)
            organic   = serp_data.get("organic_results", [])

            progress.progress(60, text="Scanning results for your business…")
            match    = find_business_in_results(business, organic)
            position = match["position"]

            progress.progress(85, text="Calculating Visibility Score…")
            score, label, colour = calculate_score(position)

            progress.progress(100, text="Done!")
            progress.empty()   # hide the bar once complete

            st.markdown("---")

            # ── Score cards ──────────────────────────────────────────────────
            c1, c2, c3 = st.columns(3)
            c1.metric("Visibility Score", f"{score} / 100")
            c2.metric("Rank Position",    f"#{position}" if position else "Not in Top 30")
            c3.metric("Status",           f"{colour} {label}")

            # Human-readable verdict
            if match["found"]:
                if position == 1:
                    st.success(f"**{business}** is the **#1 result** for '{keyword}'. 🟢")
                elif position <= 3:
                    st.success(f"**{business}** ranks **#{position}** for '{keyword}' — top 3 on Page 1. 🟢")
                elif position <= 10:
                    st.info(f"**{business}** ranks **#{position}** for '{keyword}' — Page 1, but outside the top 3. 🟡")
                elif position <= 20:
                    st.warning(f"**{business}** ranks **#{position}** for '{keyword}' — Page 2. Most searchers won't scroll here. 🟠")
                else:
                    st.warning(f"**{business}** ranks **#{position}** for '{keyword}' — Page 3. Very low visibility. 🔴")

                with st.expander("📄 View the matched Google result"):
                    st.markdown(f"**{match['title']}**")
                    st.caption(match["link"])
                    if match.get("snippet"):
                        st.markdown(f"_{match['snippet']}_")
            else:
                st.error(
                    f"**{business}** was not found in the top 30 Google results for '{keyword}'. "
                    f"Score: **0** — Invisible 🔴"
                )

            # ── Two-column layout for suggestions + top 10 ───────────────────
            st.markdown("---")
            left, right = st.columns([1, 1])

            with left:
                st.markdown("### 💡 Recommendations")
                for tip in generate_suggestions(business, keyword, position):
                    st.markdown(tip)
                    st.markdown("")   # spacing

            with right:
                st.markdown("### 🏆 Top 10 Results for this Keyword")
                st.caption("See exactly who you're competing against.")
                if organic:
                    rows = []
                    for i, r in enumerate(organic[:10], start=1):
                        is_you = match["found"] and match["position"] == i
                        rows.append({
                            "Rank":    f"#{i}" + ("  ← YOU" if is_you else ""),
                            "Title":   r.get("title", ""),
                            "URL":     r.get("link",  ""),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=350)
                    if match["found"] and position and position > 10:
                        st.caption(f"Note: {business} ranked #{position} — outside the top 10 shown above.")
                else:
                    st.caption("No organic results returned.")

            # ── Scoring breakdown (collapsed by default) ─────────────────────
            st.markdown("---")
            with st.expander("⚙️ How is the Visibility Score calculated?"):
                st.markdown("""
The score is a **deterministic weighted algorithm** — no AI involved. I based the tier
cutoffs on real click-through rate (CTR) research: rank #1 captures ~28% of all clicks,
rank #3 gets ~10%, rank #10 gets under 2%.

| Rank | Score | Label |
|---|---|---|
| #1 | 100 | 🟢 Top Result |
| #2 | 95 | 🟢 High Visibility |
| #3 | 90 | 🟢 High Visibility |
| #4–10 | 71–89 | 🟡 Good Visibility |
| #11–20 | 41–68 | 🟠 Needs Work |
| #21–30 | 11–38 | 🔴 Very Low Visibility |
| Not Found | 0 | 🔴 Invisible |
                """)

            # Save to CSV history
            save_to_history(business, keyword, position, score, label)
            st.success("✅ Result saved to History Dashboard.")

        except requests.exceptions.HTTPError as e:
            progress.empty()
            st.error(f"SerpApi error: {e} — check your API key is correct and hasn't expired.")
        except requests.exceptions.Timeout:
            progress.empty()
            st.error("Request timed out. Check your internet connection and try again.")
        except Exception as e:
            progress.empty()
            st.error(f"Unexpected error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — HISTORY DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📊 History Dashboard":
    st.title("📊 Tracking Dashboard")
    st.markdown("Every past visibility check — track how rankings change over time.")
    st.markdown("---")

    df = load_history()

    if df.empty:
        st.info("No searches yet. Run your first check in the Analyzer and results will appear here automatically.")
    else:
        # ── Summary metrics ──────────────────────────────────────────────────
        scores = pd.to_numeric(df["Score"], errors="coerce").dropna()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Searches",       len(df))
        c2.metric("Businesses Tracked",   df["Business"].nunique())
        c3.metric("Avg Visibility Score", f"{scores.mean():.0f}" if not scores.empty else "—")
        c4.metric("Best Score",           f"{scores.max():.0f}"  if not scores.empty else "—")

        st.markdown("---")

        # ── Filter ───────────────────────────────────────────────────────────
        options  = ["All"] + sorted(df["Business"].unique().tolist())
        selected = st.selectbox("Filter by Business", options)
        filtered = df if selected == "All" else df[df["Business"] == selected]

        st.dataframe(filtered, use_container_width=True)

        # ── Score trend ──────────────────────────────────────────────────────
        if selected != "All" and len(filtered) > 1:
            st.markdown(f"**📈 Visibility score trend — {selected}**")
            chart = filtered[["Date", "Score"]].copy()
            chart["Score"] = pd.to_numeric(chart["Score"], errors="coerce")
            chart = chart.dropna(subset=["Score"]).sort_values("Date")
            st.line_chart(chart.set_index("Date")["Score"])
            st.caption("Track the same keyword weekly to measure whether your SEO efforts are working.")

        # ── Weakest keywords ─────────────────────────────────────────────────
        # Quick view of where a business is most vulnerable — saves scanning the table
        if selected != "All":
            st.markdown(f"**⚠️ Keywords needing most attention — {selected}**")
            worst = filtered.copy()
            worst["Score"] = pd.to_numeric(worst["Score"], errors="coerce")
            worst = worst.dropna(subset=["Score"]).sort_values("Score").head(3)
            if not worst.empty:
                for _, row in worst.iterrows():
                    st.markdown(f"- **'{row['Keyword']}'** → Score {int(row['Score'])} ({row['Label']}) — {row['Date']}")
            else:
                st.caption("Not enough data yet.")

        st.markdown("---")
        if st.button("🗑️ Clear All History", type="secondary"):
            os.remove(HISTORY_FILE)
            st.success("History cleared.")
            st.rerun()
