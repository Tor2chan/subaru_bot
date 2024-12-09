import discord
from discord.ext import commands
import yt_dlp
import asyncio
import logging
from imageio_ffmpeg import get_ffmpeg_exe

# ตั้งค่า Logging
logging.basicConfig(level=logging.INFO)

# ตั้งค่า Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ตั้งค่า yt_dlp
yt_dlp.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': 'in_playlist'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        # ใช้ imageio-ffmpeg เพื่อกำหนดเส้นทาง FFmpeg
        ffmpeg_path = get_ffmpeg_exe()
        ffmpeg_options = {
            'options': '-vn',  # ไม่ใช้วิดีโอ
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        }
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, executable=ffmpeg_path, **ffmpeg_options), data=data)

class MusicPlayer:

    def __init__(self, ctx):
        self.bot = ctx.bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None
        self.processing_playlist = False
        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with asyncio.timeout(300):  # 5 minutes
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self.guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.from_url(source,
                                                       loop=self.bot.loop)
                except Exception as e:
                    await self.channel.send(f'มีบางอย่างผิดพลาด: {e}')
                    continue

            source.volume = self.volume
            self.current = source

            def after_playing(error):
                if error:
                    print(f"Error occurred during playback: {error}")
                self.bot.loop.call_soon_threadsafe(self.next.set)

            # เล่นเพลง
            self.guild.voice_client.play(source, after=after_playing)

            # ส่ง Embed ข้อมูลเพลง
            embed = discord.Embed(title="กำลังเล่น",
                                  description=source.title,
                                  color=discord.Color.green())
            self.np = await self.channel.send(embed=embed)

            # รอให้เพลงเล่นจบ
            await self.next.wait()

            # ล้างข้อมูลเพลงปัจจุบัน
            source.cleanup()
            self.current = None

    def destroy(self, guild):
        return self.bot.loop.create_task(self.cog.cleanup(guild))

class Music(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            player = self.players[guild.id]
            player.processing_playlist = False  # Stop
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    async def process_playlist(self, url, player, ctx):
        player.processing_playlist = True
        info = await self.bot.loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False))

        if 'entries' not in info:
            if player.processing_playlist:
                await player.queue.put(info['webpage_url'])
                await ctx.send(f'เพิ่ม 1 เพลงในคิว')
            return

        entries = info['entries']
        await ctx.send(
            f'พบ {len(entries)} เพลงในเพลย์ลิสต์ กำลังเพิ่มเข้าคิว...')

        for i, entry in enumerate(entries):
            if not player.processing_playlist:
                await ctx.send("การเพิ่มเพลย์ลิสต์ถูกยกเลิก")
                return
            await player.queue.put(entry['url'])

    @commands.command()
    async def play(self, ctx, *, url):
        if not ctx.voice_client or not ctx.voice_client.is_connected():
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("คุณต้องอยู่ในช่องเสียงเพื่อใช้คำสั่งนี้!")
                return

        player = self.get_player(ctx)

        await ctx.send("กำลังเพิ่มเพลงในคิว...")
        self.bot.loop.create_task(self.process_playlist(url, player, ctx))

    @commands.command()
    async def stop(self, ctx):
        player = self.get_player(ctx)
        player.processing_playlist = False  # Stop
        await self.cleanup(ctx.guild)
        await ctx.send("shuba...")

    @commands.command()
    async def skip(self, ctx):
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            return await ctx.send("ไม่มีเพลงกำลังเล่น")
        ctx.voice_client.stop()
        await ctx.send("skippa!")

@bot.event
async def on_ready():
    print(f'เข้าสู่ระบบแล้วด้วย {bot.user}!')
    await bot.add_cog(Music(bot))

# ใส่โทเคนของคุณตรงนี้
bot.run('MTI4MTg4MTY1MzE5NDc4NDg3MA.GQX7tG.LT7nH3prYK5ZAEkBZYfRmhHITJYUcAjSNORA9U')
