import os
import asyncio
import logging
from collections import deque

import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp

# ---------------- CONFIG ----------------

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("minicasper")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

queues = {}
now_playing = {}
volumes = {}
loop_enabled = set()

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


# ---------------- MUSIC ----------------

def search_track(query):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)

        if "entries" in info:
            info = next(
                (item for item in info["entries"] if item),
                None
            )

        if not info:
            raise RuntimeError("ไม่พบเพลง")

        return {
            "title": info.get("title", "Unknown"),
            "url": info["url"],
            "webpage": info.get("webpage_url", ""),
            "duration": info.get("duration"),
        }


async def get_track(query):
    return await asyncio.to_thread(search_track, query)


def get_queue(guild_id):
    return queues.setdefault(guild_id, deque())


async def play_next(guild):
    guild_id = guild.id
    queue = get_queue(guild_id)
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return

    if loop_enabled and guild_id in loop_enabled:
        current = now_playing.get(guild_id)
        if current:
            queue.appendleft(current)

    if not queue:
        now_playing.pop(guild_id, None)
        return

    track = queue.popleft()

    try:
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(
                track["url"],
                **FFMPEG_OPTIONS
            ),
            volume=volumes.get(guild_id, 0.5)
        )

        now_playing[guild_id] = track

        def after_play(error):
            if error:
                logger.error("Audio error: %s", error)

            future = asyncio.run_coroutine_threadsafe(
                play_next(guild),
                bot.loop
            )

        vc.play(source, after=after_play)

    except Exception:
        logger.exception("Playback failed")
        await play_next(guild)


async def connect_voice(interaction):
    if not interaction.user.voice:
        await interaction.followup.send(
            "กรุณาเข้าห้องเสียงก่อน",
            ephemeral=True
        )
        return None

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc is None:
        vc = await channel.connect()
    elif vc.channel != channel:
        await vc.move_to(channel)

    return vc


# ---------------- EVENTS ----------------

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logger.info("Synced %s commands", len(synced))
    except Exception:
        logger.exception("Command sync failed")

    logger.info("Logged in as %s", bot.user)


# ---------------- COMMANDS ----------------

@bot.tree.command(
    name="help",
    description="ดูคำสั่งของ minicasper"
)
async def help_command(interaction):
    await interaction.response.send_message(
        "**MINICCASPER MUSIC**\n\n"
        "`/play` เล่นเพลง\n"
        "`/pause` พักเพลง\n"
        "`/resume` เล่นต่อ\n"
        "`/skip` ข้ามเพลง\n"
        "`/queue` ดูคิว\n"
        "`/volume` ปรับเสียง\n"
        "`/loop` เปิด/ปิดวนเพลง\n"
        "`/stop` หยุดและล้างคิว\n"
        "`/leave` ออกจากห้องเสียง",
        ephemeral=True
    )


@bot.tree.command(
    name="play",
    description="ค้นหาเพลงหรือเล่นจากลิงก์"
)
@app_commands.describe(query="ชื่อเพลงหรือลิงก์เพลง")
async def play_command(interaction, query: str):
    await interaction.response.defer()

    if not interaction.guild:
        return await interaction.followup.send(
            "คำสั่งนี้ใช้ในเซิร์ฟเวอร์เท่านั้น"
        )

    vc = await connect_voice(interaction)

    if not vc:
        return

    try:
        track = await get_track(query)
        queue = get_queue(interaction.guild.id)
        queue.append(track)

        if not vc.is_playing() and not vc.is_paused():
            await play_next(interaction.guild)

        await interaction.followup.send(
            f"เพิ่มเพลงเข้าคิวแล้ว: **{track['title']}**"
        )

    except Exception as error:
        logger.exception("Search failed")
        await interaction.followup.send(
            f"ค้นหาเพลงไม่สำเร็จ: {error}"
        )


@bot.tree.command(name="pause", description="พักเพลง")
async def pause_command(interaction):
    vc = interaction.guild.voice_client

    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("พักเพลงแล้ว")
    else:
        await interaction.response.send_message(
            "ไม่มีเพลงที่กำลังเล่น",
            ephemeral=True
        )


@bot.tree.command(name="resume", description="เล่นเพลงต่อ")
async def resume_command(interaction):
    vc = interaction.guild.voice_client

    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("เล่นเพลงต่อแล้ว")
    else:
        await interaction.response.send_message(
            "ไม่มีเพลงที่พักอยู่",
            ephemeral=True
        )


@bot.tree.command(name="skip", description="ข้ามเพลง")
async def skip_command(interaction):
    vc = interaction.guild.voice_client

    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("ข้ามเพลงแล้ว")
    else:
        await interaction.response.send_message(
            "ไม่มีเพลงให้ข้าม",
            ephemeral=True
        )


@bot.tree.command(name="queue", description="ดูคิวเพลง")
async def queue_command(interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)
    current = now_playing.get(guild_id)

    lines = []

    if current:
        lines.append(
            f"กำลังเล่น: **{current['title']}**"
        )

    for index, track in enumerate(list(queue)[:15], 1):
        lines.append(f"{index}. {track['title']}")

    await interaction.response.send_message(
        "\n".join(lines) if lines else "คิวเพลงว่าง"
    )


@bot.tree.command(name="volume", description="ปรับเสียง")
@app_commands.describe(level="ระดับเสียง 1 ถึง 100")
async def volume_command(
    interaction,
    level: app_commands.Range[int, 1, 100]
):
    guild_id = interaction.guild.id
    volumes[guild_id] = level / 100

    vc = interaction.guild.voice_client

    if vc and vc.source:
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = level / 100

    await interaction.response.send_message(
        f"ปรับเสียงเป็น {level}% แล้ว"
    )


@bot.tree.command(name="loop", description="เปิดหรือปิดวนเพลง")
async def loop_command(interaction):
    guild_id = interaction.guild.id

    if guild_id in loop_enabled:
        loop_enabled.remove(guild_id)
        message = "ปิดวนเพลงแล้ว"
    else:
        loop_enabled.add(guild_id)
        message = "เปิดวนเพลงแล้ว"

    await interaction.response.send_message(message)


@bot.tree.command(name="stop", description="หยุดเพลงและล้างคิว")
async def stop_command(interaction):
    guild_id = interaction.guild.id
    get_queue(guild_id).clear()
    loop_enabled.discard(guild_id)

    vc = interaction.guild.voice_client

    if vc:
        vc.stop()

    now_playing.pop(guild_id, None)

    await interaction.response.send_message(
        "หยุดเพลงและล้างคิวแล้ว"
    )


@bot.tree.command(name="leave", description="ออกจากห้องเสียง")
async def leave_command(interaction):
    vc = interaction.guild.voice_client

    if vc:
        get_queue(interaction.guild.id).clear()
        await vc.disconnect()
        await interaction.response.send_message(
            "ออกจากห้องเสียงแล้ว"
        )
    else:
        await interaction.response.send_message(
            "บอทยังไม่ได้เข้าห้องเสียง",
            ephemeral=True
        )


# ---------------- PREFIX ----------------

@bot.command(name="ping")
async def ping_command(ctx):
    await ctx.reply(
        f"Pong! {round(bot.latency * 1000)} ms"
    )


# ---------------- START ----------------

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "กรุณาตั้ง DISCORD_BOT_TOKEN ใน Environment"
        )

    bot.run(TOKEN)
