import os
import re
import asyncio
import aiohttp
import discord
from discord.ext import tasks

# ============================================================
# CONFIGURATION
# ============================================================

# Wiki
WIKI_BASE           = "https://ultrakillcustoms.miraheze.org"
WIKI_API            = f"{WIKI_BASE}/w/api.php"
WIKI_PAGE_URL       = f"{WIKI_BASE}/wiki/"

# Bot
COMMAND_PREFIX      = ".wiki"
CACHE_INTERVAL      = 60        # minutes between cache refreshes
MAX_RESULTS         = 10        # max matches shown in disambiguation
CATEGORIES_PER_PAGE = 5         # max categories shown per result in disambiguation

# Embed colors (hex integers)
COLOR_MATCH         = 0xFFFFFF  # single match (exact or substring)
COLOR_DISAMBIG      = 0xFFFFFF  # multiple matches
COLOR_NONE          = 0xFFFFFF  # no results

# Embed text
EMBED_MATCH_TITLE   = "Wiki Page Found"
EMBED_DISAMBIG_TITLE= "Multiple Pages Found"
EMBED_NONE_TITLE    = "No Results"
EMBED_USAGE_TITLE   = "How to Use This Command"
EMBED_USAGE_DESC    = (
    f"`{COMMAND_PREFIX} [page name]`\n"
    "Searches the ULTRAKILL Custom Levels Wiki for a matching page.\n\n"
    "• Exact and partial matches supported\n"
    "• Spaces and capitalization are ignored"
)
EMBED_FOOTER        = "https://ultrakillcustoms.miraheze.org"
NO_CATEGORIES_LABEL = "Uncategorized"

# ============================================================

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# In-memory cache: list of dicts — title, normalized, categories, url
page_cache: list[dict] = []


def normalize(text: str) -> str:
    """Lowercase and strip all whitespace for fuzzy matching."""
    return re.sub(r"\s+", "", text).lower()


def page_url(title: str) -> str:
    return WIKI_PAGE_URL + title.replace(" ", "_")


def format_categories(cats: list[str]) -> str:
    if not cats:
        return NO_CATEGORIES_LABEL
    shown = cats[:CATEGORIES_PER_PAGE]
    result = ", ".join(shown)
    if len(cats) > CATEGORIES_PER_PAGE:
        result += f" (+{len(cats) - CATEGORIES_PER_PAGE} more)"
    return result


async def fetch_all_pages(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch all page titles from the wiki, then batch-fetch their categories."""
    pages = []
    apcontinue = None

    # Step 1: page titles
    while True:
        params = {
            "action": "query",
            "list": "allpages",
            "aplimit": "500",
            "format": "json",
        }
        if apcontinue:
            params["apcontinue"] = apcontinue

        async with session.get(WIKI_API, params=params) as resp:
            data = await resp.json()

        for page in data["query"]["allpages"]:
            pages.append({"title": page["title"], "pageid": page["pageid"]})

        if "continue" in data:
            apcontinue = data["continue"]["apcontinue"]
        else:
            break

    # Step 2: categories in batches of 50
    for i in range(0, len(pages), 50):
        batch = pages[i : i + 50]
        ids = "|".join(str(p["pageid"]) for p in batch)
        params = {
            "action": "query",
            "pageids": ids,
            "prop": "categories",
            "cllimit": "50",
            "format": "json",
        }
        async with session.get(WIKI_API, params=params) as resp:
            data = await resp.json()

        for page in batch:
            pid = str(page["pageid"])
            raw_cats = data["query"]["pages"].get(pid, {}).get("categories", [])
            cats = [
                c["title"].replace("Category:", "")
                for c in raw_cats
                if "Hidden" not in c["title"]
            ]
            page["categories"] = cats
            page["normalized"] = normalize(page["title"])
            page["url"] = page_url(page["title"])

    return pages


async def refresh_cache():
    global page_cache
    print("[Cache] Refreshing page cache...")
    async with aiohttp.ClientSession() as session:
        page_cache = await fetch_all_pages(session)
    print(f"[Cache] Loaded {len(page_cache)} pages.")


def search(query: str) -> tuple[str, list[dict]]:
    """
    Returns (match_type, results).
    match_type is one of: "exact", "single", "multiple", "none"
    """
    norm_query = normalize(query)

    exact = [p for p in page_cache if p["normalized"] == norm_query]
    if exact:
        return "exact", exact[:1]

    substring = [p for p in page_cache if norm_query in p["normalized"]]
    if not substring:
        return "none", []
    if len(substring) == 1:
        return "single", substring
    return "multiple", substring


# ============================================================
# Discord client
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@tasks.loop(minutes=CACHE_INTERVAL)
async def cache_refresh_task():
    await refresh_cache()


@client.event
async def on_ready():
    print(f"[Bot] Logged in as {client.user}")
    await refresh_cache()
    cache_refresh_task.start()


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.content.lower().startswith(COMMAND_PREFIX.lower()):
        return

    query = message.content[len(COMMAND_PREFIX):].strip()

    # No argument — show usage
    if not query:
        embed = discord.Embed(
            title=EMBED_USAGE_TITLE,
            description=EMBED_USAGE_DESC,
            color=COLOR_DISAMBIG,
        )
        embed.set_footer(text=EMBED_FOOTER)
        await message.channel.send(embed=embed)
        return

    match_type, results = search(query)

    # ── Exact or single substring match ──────────────────────
    if match_type in ("exact", "single"):
        page = results[0]
        embed = discord.Embed(title=EMBED_MATCH_TITLE, color=COLOR_MATCH)
        embed.add_field(name="Page", value=f"[{page['title']}]({page['url']})", inline=False)
        embed.add_field(name="Categories", value=format_categories(page["categories"]), inline=False)
        embed.set_footer(text=EMBED_FOOTER)
        await message.channel.send(embed=embed)

    # ── Multiple matches ──────────────────────────────────────
    elif match_type == "multiple":
        shown = results[:MAX_RESULTS]
        total = len(results)
        overflow = total - MAX_RESULTS

        lines = []
        for page in shown:
            cats = format_categories(page["categories"])
            lines.append(f"[{page['title']}]({page['url']}) ({cats})")

        description = "\n".join(lines)
        if overflow > 0:
            description += f"\n\n*...and {overflow} more. Try a more specific search.*"

        embed = discord.Embed(
            title=EMBED_DISAMBIG_TITLE,
            description=description,
            color=COLOR_DISAMBIG,
        )
        embed.set_footer(text=f"{EMBED_FOOTER} • {total} matches for \"{query}\"")
        await message.channel.send(embed=embed)

    # ── No results ────────────────────────────────────────────
    else:
        embed = discord.Embed(
            title=EMBED_NONE_TITLE,
            description=f"No pages found matching **{query}**.",
            color=COLOR_NONE,
        )
        embed.set_footer(text=EMBED_FOOTER)
        await message.channel.send(embed=embed)


client.run(DISCORD_TOKEN)
