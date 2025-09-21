import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import RealDictCursor
import discord
from discord import app_commands
from dotenv import load_dotenv

# ── env ──────────────────────────────────────────────────────────────────────
load_dotenv()

def _env(name: str) -> str:
    v = os.getenv(name, "") or ""
    return v.strip().strip('"').strip("'")

TOKEN = _env("DISCORD_TOKEN") or _env("N_DISCORD_TOKEN")
DATABASE_URL = _env("DATABASE_URL") or _env("N_DATABASE_URL")
LOGO_URL = _env("NITROVOTE_LOGO_URL")  # optional: set to your logo URL
VEIL_BOT_ID = _env("VEIL_BOT_ID") or "1403948162955219025"

# ── palette (from your logo) ─────────────────────────────────────────────────
COLORS = {
    "pink":   0xF793FF,  # f793ff
    "cyan":   0x4DF1FF,  # 4df1ff
    "purple": 0xAA48FF,  # aa48ff
    "gold":   0xF5A803,  # f5a803
    "blue":   0x0F8BFF,  # 0f8bff  ← new
}

MIN_VOTES_TO_WIN = 30

# ── timezone helpers ─────────────────────────────────────────────────────────
CT = ZoneInfo("America/Chicago")

def ct_month_bounds_utc(now_ct: datetime | None = None):
    if now_ct is None:
        now_ct = datetime.now(CT)
    start_ct = now_ct.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_ct = (start_ct.replace(year=start_ct.year + 1, month=1)
              if start_ct.month == 12 else
              start_ct.replace(month=start_ct.month + 1))
    return start_ct.astimezone(timezone.utc), end_ct.astimezone(timezone.utc)

# ── DB ───────────────────────────────────────────────────────────────────────
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    conn.set_session(readonly=True, autocommit=False)
    return conn

SQL_USER_MONTH = """
SELECT COUNT(*)::int AS votes_this_month
FROM vote_events
WHERE user_id = %s
  AND voted_at >= %s
  AND voted_at <  %s;
"""

SQL_TOP10_MONTH = """
SELECT user_id, COUNT(*)::int AS votes
FROM vote_events
WHERE voted_at >= %s
  AND voted_at <  %s
GROUP BY user_id
ORDER BY votes DESC, user_id
LIMIT 10;
"""

# ── Discord ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

BRAND_NAME = "NitroVote"
_brand_thumb = None  # resolved on_ready

def brand_embed(title: str, desc: str, tone: str = "purple") -> discord.Embed:
    color = COLORS.get(tone, COLORS["purple"])
    e = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(timezone.utc))
    # author line w/ icon
    if _brand_thumb:
        e.set_author(name=BRAND_NAME, icon_url=_brand_thumb)
        e.set_thumbnail(url=_brand_thumb)
    else:
        e.set_author(name=BRAND_NAME)
    e.set_footer(text="NitroVote")
    return e

@client.event
async def on_ready():
    global _brand_thumb
    # prefer explicit logo URL, otherwise use bot avatar
    _brand_thumb = LOGO_URL or (client.user.display_avatar.url if client.user else None)
    await tree.sync()
    print(f"Logged in as {client.user} ({client.user.id})")

@tree.command(name="myvotes", description="See how many votes you have this month.")
async def myvotes(inter: discord.Interaction):
    uid = inter.user.id
    start_utc, end_utc = ct_month_bounds_utc()

    # Use the CT month that the query actually uses
    month_label = start_utc.astimezone(CT).strftime("%B")

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_USER_MONTH, (uid, start_utc, end_utc))
        row = cur.fetchone() or {"votes_this_month": 0}
        votes = row["votes_this_month"] or 0

    qualified = votes >= MIN_VOTES_TO_WIN
    tone = "gold" if qualified else "pink"
    tip = (
        "You're qualified for rewards this month! 🎉"
        if qualified else
        f"Need **{max(0, MIN_VOTES_TO_WIN - votes)}** more votes to qualify."
    )

    e = brand_embed(
        title=f"{inter.user.display_name}'s Votes in {month_label}",
        desc=f"**{votes}** votes so far.\n\n{tip}",
        tone=tone
    )
    await inter.response.send_message(embed=e, ephemeral=False)

# /voteleaders — top 10 global
@tree.command(name="voteleaders", description="Show the top 10 voters this month (global).")
async def voteleaders(inter: discord.Interaction):
    start_utc, end_utc = ct_month_bounds_utc()
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TOP10_MONTH, (start_utc, end_utc))
        rows = cur.fetchall()

    if not rows:
        e = brand_embed("Monthly Voting Leaderboard", "No votes recorded this month yet.", tone="blue")
        await inter.response.send_message(embed=e)
        return

    medals = ["🥇", "🥈", "🥉"]
    tones  = ["gold", "purple", "cyan"]

    lines = []
    for i, r in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"#{i}"
        lines.append(f"{medal} <@{r['user_id']}> — **{r['votes']}**")

    e = brand_embed("Monthly Voting Leaderboard", "\n".join(lines), tone="blue")
    await inter.response.send_message(embed=e)

# /rules — reward rules
@tree.command(name="rules", description="Official NitroVote rules and eligibility.")
async def rules(inter: discord.Interaction):
    desc = (
        "• **Prizes:** **Top 3 voters** each month receive **1 month of Discord Nitro**.\n\n"
        f"• **Eligibility:** You must log **≥ {MIN_VOTES_TO_WIN} votes** during the month.\n\n"
        "• **Tiebreaker:** If totals match, the user who reached that total **first** wins.\n\n"
        "• **Timing:** Month boundaries use **Central Time (America/Chicago)**.\n\n"
        "• **Voting Cadence:** You may vote once **every 12 hours** via Veil’s **/vote**.\n\n"
        "• **Fair Play:** Fraud/alt/self-deal votes may be disqualified at our discretion.\n\n"
        "_Not affiliated with or endorsed by Discord. “Nitro” is a trademark of Discord Inc._"
    )
    e = brand_embed("Rules", desc, tone="cyan")
    await inter.response.send_message(embed=e, ephemeral=False)

# /cmds — command list
@tree.command(name="cmds", description="List NitroVote commands.")
async def cmds(inter: discord.Interaction):
    e = brand_embed("Commands", "", tone="cyan")
    e.add_field(name="/myvotes",     value="Show your votes this month", inline=False)
    e.add_field(name="/voteleaders", value="Top 10 voters (global)",     inline=False)
    e.add_field(name="/rules",       value="Rewards & qualification rules", inline=False)
    await inter.response.send_message(embed=e, ephemeral=False)

@tree.command(name="about", description="What NitroVote is and how to participate.")
async def about(inter: discord.Interaction):
    desc = (
        "**What is NitroVote?**\n"
        "NitroVote is a monthly voting game for **Veil** on top.gg.\n\n"
        "**How to play**\n"
        f"• Type **`/vote`** in <@{VEIL_BOT_ID}> to open the vote link.\n"
        "• You can vote **every 12 hours**.\n"
        "• Track your progress with **`/myvotes`** and see standings with **`/voteleaders`**.\n\n"
        "_See **/rules** for eligibility and prize details._"
    )
    e = brand_embed("About NitroVote", desc, tone="purple")
    await inter.response.send_message(embed=e, ephemeral=False)

# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env (raw bot token, no quotes or 'Bot ' prefix)")
    if TOKEN.lower().startswith("bot "):
        raise SystemExit("Remove the 'Bot ' prefix — use only the raw token")
    if not DATABASE_URL:
        raise SystemExit("Set DATABASE_URL in your .env")
    client.run(TOKEN)
