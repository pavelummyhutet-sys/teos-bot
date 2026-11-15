import re
import time as time_mod
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

MSK = ZoneInfo("Europe/Moscow")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

URL = "https://playteos.com/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CHANNEL_ID = 154473823562230969  # <-- ID вашего канала
TOKEN = "ВАШ-БОТ-ТОКЕН"         # <-- ваш токен (не показывайте его никому!)

SITE_CACHE_TTL = 60  # сайт опрашивается не чаще, чем раз в 60 секунд

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
          "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

BOSS_NAMES = [
    "Старшая Птица-Инквизитор", "Шипастый Наместник Бездны",
    "Верховный Ломатель", "Гриан-центурион", "Птица-Инквизитор",
    "Дентатис", "Кимураку", "Тайнор", "Люмен", "Алес",
    "Вердис", "Ноктхар", "Судья",
]

CUSTOM_WINDOWS = {
    # "Вердис": (20, 24, 28),
}

# --- кеш, чтобы не долбить сайт каждые 10 секунд ---
_html_cache = None
_html_cache_time = 0.0


def fetch_html(force: bool = False):
    global _html_cache, _html_cache_time
    now_ts = time_mod.time()
    if force or _html_cache is None or now_ts - _html_cache_time > SITE_CACHE_TTL:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        _html_cache = r.text
        _html_cache_time = now_ts
    return _html_cache


def parse_page(html: str):
    """(имя, убийца, время_убийства, часы_с_сайта) — все времена в МСК"""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    result = []
    working = text
    for name in BOSS_NAMES:
        pattern = (re.escape(name) + r"(.*?)"
                   r"(\d{1,2}\s+[а-яё]{3},\s*\d{2}:\d{2})\s*(~\d+ч)")
        m = re.search(pattern, working, re.S)
        if not m:
            continue
        killer = " ".join(m.group(1).split())
        raw_date = m.group(2).replace(" ", "")
        month = MONTHS[re.search(r"[а-яё]+", raw_date).group()[:3]]
        day = int(re.match(r"\d+", raw_date).group())
        hh, mm = map(int, re.search(r"(\d{2}):(\d{2})$", raw_date).groups())
        kill_time = datetime.now(MSK).replace(month=month, day=day, hour=hh,
                                              minute=mm, second=0,
                                              microsecond=0)
        hours = int(re.search(r"\d+", m.group(3)).group())
        result.append((name, killer, kill_time, hours))
        working = working.replace(m.group(0), "\x00", 1)

    for m in re.finditer(r"(\d{2}:\d{2})\s*/\s*(\d{2}:\d{2})", text):
        daily = re.search(r"след\.\s*в\s*(\d{2}:\d{2})\s*МСК",
                          text[m.end():m.end() + 60])
        if daily:
            hh, mm = map(int, daily.group(1).split(":"))
            now = datetime.now(MSK)
            nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            result.append((f"Ежедневно {m.group(1)}/{m.group(2)}",
                           "", None, nxt))
    return result


def respawn_window(kill_time: datetime, hours: int, name: str):
    if name in CUSTOM_WINDOWS:
        mn, av, mx = CUSTOM_WINDOWS[name]
    else:
        mn, av, mx = hours - 1, hours, hours + 1
    return (kill_time + timedelta(hours=mn),
            kill_time + timedelta(hours=av),
            kill_time + timedelta(hours=mx))


def format_boss(killer, kill_time, hours, name):
    now = datetime.now(MSK)

    if kill_time is None:
        return f"След. спавн: <t:{int(hours.timestamp())}:F> (фиксированный)"

    t_min, t_avg, t_max = respawn_window(kill_time, hours, name)

    if now < t_min:
        status = f"⏳ Не раньше <t:{int(t_min.timestamp())}:t>"
    elif now < t_max:
        status = "🟢 Окно спавна — может появиться в любой момент"
    else:
        status = "🔴 Точно жив"

    return (f"Убит: {killer or '—'}\n"
            f"Окно: ±1ч (от ~{hours}ч после убийства)\n"
            f"Мин: <t:{int(t_min.timestamp())}:F> | "
            f"Ср: <t:{int(t_avg.timestamp())}:F> | "
            f"Макс: <t:{int(t_max.timestamp())}:F>\n{status}")


def build_embed():
    embed = discord.Embed(title="💀 Респаун боссов — Теос: Пир Ворона",
                          color=0x00b0f0)
    for name, killer, kill_time, hours in parse_page(fetch_html()):
        embed.add_field(name=name,
                        value=format_boss(killer, kill_time, hours, name),
                        inline=False)
    return embed


last_message_id = None


@tasks.loop(seconds=10)
async def update_schedule():
    global last_message_id
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    try:
        embed = build_embed()
        if last_message_id:
            try:
                msg = await channel.fetch_message(last_message_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass
        msg = await channel.send(embed=embed)
        last_message_id = msg.id
    except Exception as e:
        print("Ошибка обновления:", e)


@bot.command(name="boss")
async def boss(ctx, *, query: str = None):
    if query is None:
        await ctx.send("Укажите имя босса: `!boss Алес` или `!boss инквизитор`")
        return
    try:
        bosses = parse_page(fetch_html())
    except Exception as e:
        await ctx.send(f"Не удалось получить данные с сайта: {e}")
        return

    query_low = query.lower()
    matches = [b for b in bosses if query_low in b[0].lower()]

    if not matches:
        await ctx.send(f"Босс «{query}» не найден. Попробуйте: `!boss Алес`")
        return

    embed = discord.Embed(title=f"🔍 {matches[0][0]}", color=0xffc107)
    for name, killer, kill_time, hours in matches[:5]:
        embed.add_field(name=name,
                        value=format_boss(killer, kill_time, hours, name),
                        inline=False)
    await ctx.send(embed=embed)


@bot.command(name="debug")
async def debug(ctx):
    try:
        bosses = parse_page(fetch_html(force=True))
    except Exception as e:
        await ctx.send(f"Ошибка загрузки: {e}")
        return
    if not bosses:
        await ctx.send("Парсер не нашёл НИ ОДНОГО босса — формат сайта изменился.")
        return
    lines = []
    for name, killer, kill_time, hours in bosses:
        kt = kill_time.strftime("%d.%m %H:%M") if kill_time else "фикс."
        lines.append(f"{name} | убил: {killer or '—'} | {kt} | ~{hours}ч")
    await ctx.send("```\n" + "\n".join(lines) + "\n```")


@bot.event
async def on_ready():
    print(f"Бот {bot.user} запущен!")
    if not update_schedule.is_running():
        update_schedule.start()


bot.run(TOKEN)