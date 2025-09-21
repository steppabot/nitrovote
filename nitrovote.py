# nitrovote.py
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
SQL_TOP3_PREV_MONTH = """
WITH totals AS (
  SELECT user_id,
         COUNT(*)::int AS votes,
         MAX(voted_at) AS last_vote_at  -- when they reached their final total
  FROM vote_events
  WHERE voted_at >= %s AND voted_at < %s
  GROUP BY user_id
)
SELECT user_id, votes, last_vote_at
FROM totals
WHERE votes >= %s
ORDER BY votes DESC, last_vote_at ASC, user_id
LIMIT 3;
"""
SQL_TOP10_MONTH = """
WITH month_rows AS (
  SELECT id, user_id, voted_at
  FROM vote_events
  WHERE voted_at >= %s AND voted_at < %s
),
agg AS (
  SELECT
    user_id,
    COUNT(*)::int AS votes,
    MIN(voted_at) AS first_vote_at,   -- earliest vote this month
    MIN(id)       AS first_vote_id,   -- strict insertion-order tiebreaker
    MAX(voted_at) AS last_vote_at     -- when they reached their current total
  FROM month_rows
  GROUP BY user_id
)
SELECT user_id, votes, first_vote_at, first_vote_id, last_vote_at
FROM agg
ORDER BY votes DESC, first_vote_at ASC, first_vote_id ASC, user_id
LIMIT 10;
"""

# ---- Month bounds: previous month in CT, as UTC ----
def prev_month_ct_bounds_utc(now_ct: datetime | None = None):
    if now_ct is None:
        now_ct = datetime.now(CT)
    first_this = now_ct.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # start of previous month
    if first_this.month == 1:
        start_prev = first_this.replace(year=first_this.year - 1, month=12)
    else:
        start_prev = first_this.replace(month=first_this.month - 1)
    end_prev = first_this  # start of this month
    return start_prev.astimezone(timezone.utc), end_prev.astimezone(timezone.utc)

# ---- Pick a “main chat” channel without config ----
def pick_announcement_channel(guild: discord.Guild) -> discord.TextChannel | None:
    me = guild.me
    def can_post(ch: discord.abc.GuildChannel):
        p = ch.permissions_for(me)
        return getattr(p, "view_channel", False) and getattr(p, "send_messages", False)

    # 1) Prefer common names
    preferred = {"general","chat","lobby","main","talk","discussion","welcome"}
    named = [c for c in guild.text_channels if c.name.lower() in preferred and can_post(c) and not c.is_nsfw()]
    if named:
        return sorted(named, key=lambda c: (c.category.position if c.category else -1, c.position))[0]

    # 2) System channel (if sendable)
    if guild.system_channel and can_post(guild.system_channel) and not guild.system_channel.is_nsfw():
        return guild.system_channel

    # 3) Top-most text channel we can post in (non-NSFW)
    for c in sorted(guild.text_channels, key=lambda c: (c.category.position if c.category else -1, c.position)):
        if can_post(c) and not c.is_nsfw():
            return c
    return None

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
    lines = []
    for i, r in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"#{i}"
        try:
            user = await client.fetch_user(r["user_id"])
            name = user.name
        except discord.NotFound:
            name = f"User {r['user_id']}"

        first_ct = r["first_vote_at"].astimezone(CT).strftime("%b %d, %I:%M %p")
        lines.append(f"{medal} **{name}** — **{r['votes']}** _(first vote {first_ct} CT)_")

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
        f"• Type **`/vote`** on Veil bot to open the vote link.\n"
        "• You can vote **every 12 hours**.\n"
        "• Track your progress with **`/myvotes`** and see standings with **`/voteleaders`**.\n\n"
        "_See **/rules** for eligibility and prize details._"
    )
    e = brand_embed("About NitroVote", desc, tone="purple")
    await inter.response.send_message(embed=e, ephemeral=False)


@tree.command(name="winners", description="(Admin) Post last month's Top 3 winners.")
@app_commands.describe(channel="Channel to post in (optional)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def announce_winners(inter: discord.Interaction, channel: discord.TextChannel | None = None):
    # runtime permission check (in case defaults were changed)
    perms = inter.user.guild_permissions
    if not (perms.manage_guild or perms.administrator):
        await inter.response.send_message("You need **Manage Server** to run this.", ephemeral=True)
        return

    start_utc, end_utc = prev_month_ct_bounds_utc()
    month_label = start_utc.astimezone(CT).strftime("%B %Y")

    # compute winners (global, not per-guild)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TOP3_PREV_MONTH, (start_utc, end_utc, MIN_VOTES_TO_WIN))
        winners = cur.fetchall()

    if not winners:
        await inter.response.send_message(f"No eligible winners for **{month_label}**.", ephemeral=True)
        return

    medals = ["🥇","🥈","🥉"]
    lines = [f"{medals[i]} <@{w['user_id']}> — **{w['votes']}** votes"
             for i, w in enumerate(winners)]

    e = brand_embed(
        title=f"NitroVote Winners — {month_label}",
        desc="\n".join(lines),
        tone="gold"
    )
    e.set_footer(text="Top 3 win Nitro • Ties broken by who reached the total first • Central Time")

    target = channel or pick_announcement_channel(inter.guild)
    if not target:
        await inter.response.send_message(
            "I couldn't find a channel I can post in. Please pass a channel like `/announce_winners #general`.",
            ephemeral=True
        )
        return

    try:
        await target.send(embed=e)  # add view=vote_button_view() if you want a CTA
        await inter.response.send_message(f"Posted winners in {target.mention}.", ephemeral=False)
    except discord.Forbidden:
        await inter.response.send_message(
            f"I don’t have permission to send messages in {target.mention}.", ephemeral=True
        )

# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env (raw bot token, no quotes or 'Bot ' prefix)")
    if TOKEN.lower().startswith("bot "):
        raise SystemExit("Remove the 'Bot ' prefix — use only the raw token")
    if not DATABASE_URL:
        raise SystemExit("Set DATABASE_URL in your .env")
    client.run(TOKEN)
