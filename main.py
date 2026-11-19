import os
import sqlite3
import threading
import time
import re
from flask import Flask, jsonify, request, render_template_string
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
PORT = int(os.environ.get("PORT", 5000))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}") # ganti ke url railway lu nanti

# --- Database ---
db = sqlite3.connect("nobar.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS rooms (
    guild_id INTEGER PRIMARY KEY,
    room_id TEXT NOT NULL,
    url TEXT DEFAULT '',
    type TEXT DEFAULT 'idle',
    status TEXT DEFAULT 'paused',
    position REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
""")
db.commit()

def get_room(guild_id):
    row = db.execute("SELECT guild_id, room_id, url, type, status, position, updated_at FROM rooms WHERE guild_id=?", (guild_id,)).fetchone()
    if not row: return None
    return {"guild_id": row[0], "room_id": row[1], "url": row[2], "type": row[3], "status": row[4], "position": row[5], "updated_at": row[6]}

def get_current_position(room):
    if not room: return 0
    if room["status"] == "playing":
        return room["position"] + (time.time() - room["updated_at"])
    return room["position"]

def detect_type(url: str):
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "mp4"

def extract_youtube_id(url: str):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else ""

# --- Web Player (Flask) ---
app = Flask(__name__)

PLAYER_HTML = """
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nobar</title><style>
body{background:#0f0f0f;color:white;font-family:sans-serif;text-align:center;margin:0;padding:16px}
#player{width:100%;max-width:900px;aspect-ratio:16/9;background:black;margin:auto}
video{width:100%;height:100%}
button{background:#5865F2;color:white;border:none;padding:10px 18px;border-radius:8px;margin:5px;font-size:16px}
#info{margin-top:10px;opacity:.7}
</style></head><body>
<h2>🍿 NOBAR - Room {{room_id}}</h2>
<div id="player"></div>
<div id="info">Sinkron otomatis tiap 2 detik...</div>
<script>
const ROOM="{{room_id}}";
let type="", ytPlayer=null, videoEl=null, lastSync=0, isYoutube=false;
function loadState(first=false){
  fetch(`/api/state/${ROOM}`).then(r=>r.json()).then(s=>{
    if(!s.url){ document.getElementById('info').innerText='Belum ada video. Play dari Discord dulu.'; return; }
    if(s.type!==type){ type=s.type; buildPlayer(s); }
    let target = s.position;
    if(s.status==='playing') target += (Date.now()/1000 - s.server_time);
    if(isYoutube){
      if(ytPlayer){
        let diff = Math.abs(ytPlayer.getCurrentTime() - target);
        if(diff>2) ytPlayer.seekTo(target,true);
        if(s.status==='playing' && ytPlayer.getPlayerState()!==1) ytPlayer.playVideo();
        if(s.status==='paused' && ytPlayer.getPlayerState()!==2) ytPlayer.pauseVideo();
      }
    } else if(videoEl){
      if(Math.abs(videoEl.currentTime - target)>2) videoEl.currentTime=target;
      if(s.status==='playing') videoEl.play().catch(()=>{});
      else videoEl.pause();
    }
  });
}
function buildPlayer(s){
  const c=document.getElementById('player'); c.innerHTML='';
  if(s.type==='youtube'){
    isYoutube=true;
    let div=document.createElement('div'); div.id='yt'; div.style.width='100%'; div.style.height='100%'; c.appendChild(div);
    let tag=document.createElement('script'); tag.src="https://www.youtube.com/iframe_api"; document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady=()=>{
      ytPlayer=new YT.Player('yt',{videoId:s.youtube_id, playerVars:{autoplay:1,controls:1}, events:{onReady:()=>loadState(true)}});
    };
    if(window.YT) window.onYouTubeIframeAPIReady();
  } else {
    isYoutube=false;
    videoEl=document.createElement('video'); videoEl.src=s.url; videoEl.controls=true; videoEl.playsInline=true; c.appendChild(videoEl);
  }
}
setInterval(loadState, 2000); loadState(true);
</script></body></html>
"""

@app.route("/watch/<room_id>")
def watch(room_id):
    return render_template_string(PLAYER_HTML, room_id=room_id)

@app.route("/api/state/<room_id>")
def api_state(room_id):
    row = db.execute("SELECT url, type, status, position, updated_at FROM rooms WHERE room_id=?", (room_id,)).fetchone()
    if not row: return jsonify({"error":"room not found"}), 404
    url, typ, status, pos, updated = row
    cur = pos + (time.time()-updated) if status=="playing" else pos
    return jsonify({"url":url,"type":typ,"status":status,"position":cur,"server_time":time.time(),"youtube_id":extract_youtube_id(url) if typ=="youtube" else ""})

def run_web():
    app.run(host="0.0.0.0", port=PORT)

# --- Discord Bot ---
class NobarBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
    async def setup_hook(self):
        await self.tree.sync()

bot = NobarBot()

import secrets
@bot.tree.command(name="nobar", description="Nobar bareng")
@app_commands.describe(aksi="buat/play/pause/resume/seek/stop/link", url="Link YouTube / MP4 (buat play)", detik="Detik untuk seek")
async def nobar(inter: discord.Interaction, aksi: str, url: str = "", detik: int = 0):
    gid = inter.guild_id
    if not gid:
        return await inter.response.send_message("Pake di server, jangan di DM.", ephemeral=True)

    if aksi == "buat":
        room_id = secrets.token_hex(3)
        db.execute("REPLACE INTO rooms (guild_id, room_id, url, type, status, position, updated_at) VALUES (?,?,?,?,?,?,?)",
                   (gid, room_id, "", "idle", "paused", 0, time.time()))
        db.commit()
        link = f"{BASE_URL}/watch/{room_id}"
        return await inter.response.send_message(f"🍿 **Room Nobar dibuat!**\nLink nonton (share ke semua):\n{link}\n\nLanjut: `/nobar aksi:play url:<link youtube/mp4>`")

    room = get_room(gid)
    if not room:
        return await inter.response.send_message("Belum ada room. Bikin dulu: `/nobar aksi:buat`", ephemeral=True)

    if aksi == "link":
        return await inter.response.send_message(f"Link nobar:\n{BASE_URL}/watch/{room['room_id']}")

    if aksi == "play":
        if not url:
            return await inter.response.send_message("Kasih url-nya. Contoh: `/nobar aksi:play url:https://youtu.be/xxxx`", ephemeral=True)
        typ = detect_type(url)
        db.execute("UPDATE rooms SET url=?, type=?, status='playing', position=0, updated_at=? WHERE guild_id=?", (url, typ, time.time(), gid))
        db.commit()
        return await inter.response.send_message(f"▶️ **Now Playing**\n{url}\nSemua yang buka link bakal ke-sync otomatis!\nLink: {BASE_URL}/watch/{room['room_id']}")

    if aksi == "pause":
        cur = get_current_position(room)
        db.execute("UPDATE rooms SET status='paused', position=?, updated_at=? WHERE guild_id=?", (cur, time.time(), gid))
        db.commit()
        return await inter.response.send_message(f"⏸️ Di-pause di {int(cur)} detik.")

    if aksi == "resume":
        db.execute("UPDATE rooms SET status='playing', updated_at=? WHERE guild_id=?", (time.time(), gid))
        db.commit()
        return await inter.response.send_message("▶️ Lanjut lagi!")

    if aksi == "seek":
        db.execute("UPDATE rooms SET position=?, updated_at=? WHERE guild_id=?", (detik, time.time(), gid))
        db.commit()
        return await inter.response.send_message(f"⏩ Loncat ke {detik} detik.")

    if aksi == "stop":
        db.execute("UPDATE rooms SET url='', type='idle', status='paused', position=0, updated_at=? WHERE guild_id=?", (time.time(), gid))
        db.commit()
        return await inter.response.send_message("⏹️ Nobar selesai.")

    await inter.response.send_message("Aksi gak dikenal. Pake: buat / play / pause / resume / seek / stop / link", ephemeral=True)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(TOKEN)
