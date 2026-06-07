# ULTRAKILL Custom Levels Wiki Bot by Smallkloon
# See requirements.txt for dependencies, set up environment variable "DISCORD_TOKEN" then simply run this file.

import os
import re
import asyncio
import aiohttp
import discord
from discord.ext import tasks

# cf (formatted nicely yayy thank you copilot inline suggestions)
# wiki
WIKI_BASE           = "https://ultrakillcustoms.miraheze.org"
WIKI_API            = f"{WIKI_BASE}/w/api.php"
WIKI_PAGE_URL       = f"{WIKI_BASE}/wiki/"

# Bot (time in mins)
COMMAND_PREFIX      = ".wiki"
CACHE_INTERVAL      = 60 # might be good to run some experiments on optimal cache interval
MAX_RESULTS         = 10    
CATEGORIES_PER_PAGE = 5         

# Embed colors (hex)
COLOR_RESULTS       = 0xFFFFFF # change these later maybe?
COLOR_NONE          = 0xFFFFFF

# Embed text
EMBED_RESULTS_TITLE = "Wiki Search Results"
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

# key/envs

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# cache, organized as list of dicts (title, normalized, categories, url)
page_cache: list[dict] = []
# set once the cache has been populated; queries wait on this so they never search an empty cache
cache_ready = asyncio.Event()

# helpers

def normalize(text: str) -> str:
    """Lowercase and strip all whitespace for fuzzy matching."""
    return re.sub(r"\s+", "", text).lower()


def page_url(title: str) -> str:
    """Sub spaces with underscores for mediawiki URLs."""
    return WIKI_PAGE_URL + title.replace(" ", "_")


def format_categories(cats: list[str]) -> str:
    """Format a list of categories for display, showing only the first few."""
    if not cats: return NO_CATEGORIES_LABEL
    shown = cats[:CATEGORIES_PER_PAGE]
    result = ", ".join(shown)
    if len(cats) > CATEGORIES_PER_PAGE: result += f" (+{len(cats) - CATEGORIES_PER_PAGE} more)"
    return result


async def fetch_all_pages(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch all page titles from the wiki, batch-fetch their categories."""
    pages = []
    apcontinue = None
    cont = True
    
    # page titles
    while cont:
        params = {
            "action": "query",
            "list": "allpages",
            "aplimit": "500",
            "format": "json",
        }
        if apcontinue: params["apcontinue"] = apcontinue
        async with session.get(WIKI_API, params=params) as resp: data = await resp.json()
        for page in data["query"]["allpages"]: pages.append({"title": page["title"], "pageid": page["pageid"]}) 
        if "continue" in data: apcontinue = data["continue"]["apcontinue"]
        else: cont = False

    # categories in batches of 50
    for i in range(0, len(pages), 50):
        batch = pages[i : i + 50]
        ids = "|".join(str(p["pageid"]) for p in batch)

        # accumulate categories across continuations (cllimit caps total per response, not per page)
        cats_by_id: dict[str, list[str]] = {}
        clcontinue = None
        while True:
            params = {
                "action": "query",
                "pageids": ids,
                "prop": "categories",
                "cllimit": "500",
                "format": "json",
            }
            if clcontinue: params["clcontinue"] = clcontinue
            async with session.get(WIKI_API, params=params) as resp: data = await resp.json()
            for pid, page_data in data["query"]["pages"].items():
                for c in page_data.get("categories", []):
                    if "Hidden" not in c["title"]:
                        cats_by_id.setdefault(pid, []).append(c["title"].replace("Category:", ""))
            if "continue" in data: clcontinue = data["continue"]["clcontinue"]
            else: break

        for page in batch:
            pid = str(page["pageid"])
            page["categories"] = cats_by_id.get(pid, [])
            page["normalized"] = normalize(page["title"])
            page["url"] = page_url(page["title"])

    return pages


async def refresh_cache():
    global page_cache
    print("[Cache] Refreshing page cache...")
    async with aiohttp.ClientSession() as session: page_cache = await fetch_all_pages(session)
    cache_ready.set()
    print(f"[Cache] Loaded {len(page_cache)} pages.")


def search(query: str) -> list[dict]:
    """Return matching pages, ranked exact -> prefix -> alphabetical."""
    norm_query = normalize(query)
    matches = [p for p in page_cache if norm_query in p["normalized"]]

    def rank(p: dict) -> tuple:
        n = p["normalized"]
        if n == norm_query:            tier = 0  # exact
        elif n.startswith(norm_query): tier = 1  # prefix
        else:                          tier = 2  # substring
        return (tier, n)  # within a tier, sort alphabetically

    return sorted(matches, key=rank)

# Discord client

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@tasks.loop(minutes=CACHE_INTERVAL)
async def cache_refresh_task():
    await refresh_cache()


@client.event
async def on_ready():
    print(f"[Bot] Logged in as {client.user}")
    # .start() runs the first iteration immediately, so it handles the initial cache too.
    # Guard against on_ready firing again on reconnect (would RuntimeError on a running loop).
    if not cache_refresh_task.is_running():
        cache_refresh_task.start()

# kinda no comments in this entire project but "the code speaks for itself"
@client.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if not message.content.lower().startswith(COMMAND_PREFIX.lower()): return
    query = message.content[len(COMMAND_PREFIX):].strip()

    # ".wiki" -> show help
    if not query:
        embed = discord.Embed(
            title=EMBED_USAGE_TITLE,
            description=EMBED_USAGE_DESC,
            color=COLOR_RESULTS,
        )
        embed.set_footer(text=EMBED_FOOTER)
        await message.channel.send(embed=embed)
        return

    await cache_ready.wait()  # block until the cache has been populated at least once
    results = search(query)

    if results:
        shown = results[:MAX_RESULTS]
        total = len(results)

        lines = []
        for page in shown:
            cats = format_categories(page["categories"])
            lines.append(f"[{page['title']}]({page['url']}) ({cats})")

        description = "\n".join(lines)
        if total > MAX_RESULTS:
            description += f"\n\n*...and {total - MAX_RESULTS} more. Try a more specific search.*"

        embed = discord.Embed(
            title=EMBED_RESULTS_TITLE,
            description=description,
            color=COLOR_RESULTS,
        )
        embed.set_footer(text=f"{EMBED_FOOTER} • {total} matches for \"{query}\"")
        await message.channel.send(embed=embed)
        return
    
    # no match found
    embed = discord.Embed(
        title=EMBED_NONE_TITLE,
        description=f"No pages found matching **{query}**.",
        color=COLOR_NONE,
    )
    embed.set_footer(text=EMBED_FOOTER)
    await message.channel.send(embed=embed)

client.run(DISCORD_TOKEN)
