import asyncio
import datetime as dt
import os
import threading
import time
import json
import base64
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox

import aiohttp
import customtkinter as ctk
import discord
from discord.ext import commands
from dotenv import load_dotenv, set_key
from PIL import Image, ImageTk

APP_DIR = Path(__file__).parent
ENV_PATH = APP_DIR / ".env"
load_dotenv(ENV_PATH)

PINK = "#ff4fd8"
PINK_2 = "#ff86e6"
PINK_DARK = "#9b1b7e"
BG = "#07040b"
BG_2 = "#0d0813"
CARD = "#15101c"
CARD_2 = "#21162b"
TEXT = "#fff0fb"
MUTED = "#b990c8"
BAD = "#ff5370"
GOOD = "#64ffda"

COMMON_ROLE_PERMISSIONS = [
    ("administrator", "Administrator"),
    ("manage_guild", "Manage Server"),
    ("manage_roles", "Manage Roles"),
    ("manage_channels", "Manage Channels"),
    ("manage_messages", "Manage Messages"),
    ("kick_members", "Kick Members"),
    ("ban_members", "Ban Members"),
    ("moderate_members", "Timeout Members"),
    ("send_messages", "Send Messages"),
    ("embed_links", "Embed Links"),
    ("attach_files", "Attach Files"),
    ("read_message_history", "Read History"),
    ("mention_everyone", "Mention Everyone"),
    ("add_reactions", "Add Reactions"),
    ("use_application_commands", "Use Slash Commands"),
    ("create_public_threads", "Create Public Threads"),
    ("create_private_threads", "Create Private Threads"),
    ("manage_threads", "Manage Threads"),
    ("connect", "Voice Connect"),
    ("speak", "Voice Speak"),
    ("mute_members", "Mute Members"),
    ("deafen_members", "Deafen Members"),
    ("move_members", "Move Members"),
    ("use_voice_activation", "Voice Activation"),
    ("priority_speaker", "Priority Speaker"),
    ("stream", "Stream"),
]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


def safe_name(obj):
    try:
        return str(obj)
    except Exception:
        return "Unknown"


class DiscordWorker:
    def __init__(self, app):
        self.app = app
        self.loop = None
        self.thread = None
        self.bot = None
        self.ready = False
        self.monitor_channel_ids = set()
        self.monitor_dm_user_ids = set()
        self.monitor_servers = True
        self.monitor_dms = True
        self.auto_reply_enabled = False
        self.auto_reply_keywords = []
        self.auto_reply_text = ""
        self.auto_reply_cooldown = 30
        self._reply_cooldowns = {}
        self.auto_react_enabled = False
        self.auto_react_keywords = []
        self.auto_react_emoji = "💖"
        self.clean_stop = False
        self.roles_by_guild = {}
        self.members_by_guild = {}
        self.categories_by_guild = {}

    def start(self, token: str):
        self.token = token
        if self.thread and self.thread.is_alive():
            self.app.log("Already connected.")
            return
        self.thread = threading.Thread(target=self._thread_main, args=(token,), daemon=True)
        self.thread.start()

    def _thread_main(self, token):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_bot(token))

    async def _start_bot(self, token):
        self.bot = commands.Bot(command_prefix="!", self_bot=True)

        @self.bot.event
        async def on_ready():
            self.ready = True
            user_text = f"{self.bot.user}"
            uid = getattr(self.bot.user, "id", "")
            self.app.after(0, lambda: self.app.on_connected(user_text, uid))
            self.app.log(f"Connected as {user_text}")
            await self.refresh_cache()

        @self.bot.event
        async def on_message(message):
            await self.handle_message(message)

        try:
            await self.bot.start(token)
        except Exception as e:
            self.app.after(0, lambda: self.app.log(f"Connection error: {e}"))
            self.ready = False

    async def handle_message(self, message):
        try:
            if not self.bot or not self.bot.user:
                return
            is_dm = not getattr(message, "guild", None)
            if message.author.id == self.bot.user.id:
                return
            if is_dm:
                if not self.monitor_dms:
                    return
                if self.monitor_dm_user_ids and message.author.id not in self.monitor_dm_user_ids:
                    return
            else:
                if not self.monitor_servers:
                    return
                if self.monitor_channel_ids and message.channel.id not in self.monitor_channel_ids:
                    return

            author = getattr(message.author, "display_name", safe_name(message.author))
            channel_name = "DM" if is_dm else f"{getattr(message.guild, 'name', 'Guild')} / #{getattr(message.channel, 'name', message.channel.id)}"
            content_raw = message.content or ""
            content = content_raw.lower()
            self.app.log(f"MONITOR {channel_name}: {author}: {content_raw}")

            if self.auto_react_enabled:
                if not self.auto_react_keywords or any(k.lower() in content for k in self.auto_react_keywords):
                    try:
                        await message.add_reaction(self.auto_react_emoji)
                    except Exception as e:
                        self.app.log(f"Auto react failed: {e}")

            if self.auto_reply_enabled and self.auto_reply_text:
                if not self.auto_reply_keywords or any(k.lower() in content for k in self.auto_reply_keywords):
                    now = time.time()
                    key = f"{message.channel.id}:{message.author.id}"
                    if now - self._reply_cooldowns.get(key, 0) >= max(3, int(self.auto_reply_cooldown or 30)):
                        self._reply_cooldowns[key] = now
                        try:
                            await message.channel.send(self.auto_reply_text)
                        except Exception as e:
                            self.app.log(f"Auto reply failed: {e}")
        except Exception as e:
            self.app.log(f"Message handler error: {e}")

    def run_coro(self, coro):
        if not self.loop or not self.ready:
            self.app.log("Not connected yet.")
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def refresh_cache(self):
        guilds, channels, admin_guilds = [], [], []
        roles_by_guild, members_by_guild, categories_by_guild = {}, {}, {}
        for g in list(getattr(self.bot, "guilds", [])):
            guilds.append({"name": g.name, "id": str(g.id), "members": getattr(g, "member_count", 0), "icon": (str(g.icon.url) if getattr(g, "icon", None) else "")})
            me = g.get_member(self.bot.user.id) or getattr(g, "me", None)
            perms = getattr(me, "guild_permissions", None)
            is_admin = bool(getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False) or getattr(perms, "manage_messages", False) or getattr(perms, "manage_channels", False) or getattr(perms, "manage_roles", False))
            if is_admin:
                admin_guilds.append({"name": g.name, "id": str(g.id)})
            cats = []
            for cat in getattr(g, "categories", []) or []:
                cats.append({"name": cat.name, "id": str(cat.id), "position": getattr(cat, "position", 0)})
            categories_by_guild[str(g.id)] = sorted(cats, key=lambda x: x.get("position", 0))
            for ch in getattr(g, "text_channels", []):
                channels.append({"name": f"#{ch.name}", "full_name": f"{g.name} / #{ch.name}", "id": str(ch.id), "guild_id": str(g.id), "type": "guild", "category": (ch.category.name if getattr(ch, "category", None) else "No category")})
            roles = []
            for r in sorted(getattr(g, "roles", []), key=lambda x: getattr(x, "position", 0), reverse=True):
                roles.append({"name": r.name, "id": str(r.id), "position": getattr(r, "position", 0), "members": len(getattr(r, "members", []) or [])})
            roles_by_guild[str(g.id)] = roles
            members = []
            for m in list(getattr(g, "members", []) or [])[:1000]:
                members.append({"name": str(m), "display": getattr(m, "display_name", str(m)), "id": str(m.id), "bot": getattr(m, "bot", False)})
            members_by_guild[str(g.id)] = members
        self.roles_by_guild = roles_by_guild
        self.members_by_guild = members_by_guild
        self.categories_by_guild = categories_by_guild
        dms = await self.get_open_dms()
        self.app.after(0, lambda: self.app.update_lists(guilds, channels, dms, admin_guilds, roles_by_guild, members_by_guild, categories_by_guild))

    async def get_open_dms(self):
        dms = []
        seen = set()
        private_channels = list(getattr(self.bot, "private_channels", []) or [])
        private_channels += list(getattr(self.bot, "cached_private_channels", []) or [])
        for ch in private_channels:
            user = getattr(ch, "recipient", None)
            if not user or user.id in seen:
                continue
            seen.add(user.id)
            dms.append(self._dm_payload(ch, user))
        dms.sort(key=lambda x: x.get("name", "").lower())
        return dms

    def _avatar_url(self, user):
        try:
            return str(user.display_avatar.url)
        except Exception:
            try:
                return str(user.avatar.url)
            except Exception:
                return ""

    def _dm_payload(self, ch, user):
        return {
            "name": safe_name(user),
            "display": getattr(user, "display_name", safe_name(user)),
            "id": str(user.id),
            "channel_id": str(getattr(ch, "id", "")),
            "avatar": self._avatar_url(user),
        }

    def _prepare_avatar_image(self, data):
        try:
            img = Image.open(BytesIO(data))
            img.load()
            if getattr(img, "is_animated", False):
                img.seek(0)
            img = img.convert("RGBA")
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
            img = img.resize((512, 512), Image.LANCZOS)
            out = BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue()
        except Exception as e:
            raise RuntimeError(f"Avatar image could not be prepared: {e}")

    async def _apply_avatar_bytes(self, data):
        data = self._prepare_avatar_image(data)
        first_error = None
        try:
            await self.bot.user.edit(avatar=data)
            return True
        except Exception as e:
            first_error = e
        token = getattr(self, "token", "")
        if not token:
            raise RuntimeError(f"Avatar edit failed: {first_error}")
        avatar_payload = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        headers = {"Authorization": token, "Content-Type": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.patch("https://discord.com/api/v9/users/@me", json={"avatar": avatar_payload}) as resp:
                body = await resp.text()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Avatar edit failed: {first_error}; HTTP {resp.status}: {body[:250]}")
        return True

    async def set_avatar_url(self, url):
        if not url:
            raise RuntimeError("No image URL entered.")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Image URL could not be loaded: HTTP {resp.status}")
                data = await resp.read()
        return await self._apply_avatar_bytes(data)

    async def set_avatar_file(self, path):
        with open(path, "rb") as f:
            data = f.read()
        return await self._apply_avatar_bytes(data)

    async def set_presence(self, status_text, activity_type, online_status):
        activity = None
        status_map = {
            "online": getattr(discord.Status, "online", None),
            "idle": getattr(discord.Status, "idle", None),
            "dnd": getattr(discord.Status, "dnd", None),
            "invisible": getattr(discord.Status, "invisible", None),
        }
        status = status_map.get((online_status or "online").lower())
        if status_text.strip():
            if activity_type == "Watching":
                activity = discord.Activity(type=discord.ActivityType.watching, name=status_text.strip())
            elif activity_type == "Listening":
                activity = discord.Activity(type=discord.ActivityType.listening, name=status_text.strip())
            elif activity_type == "Streaming":
                activity = discord.Streaming(name=status_text.strip(), url="https://www.twitch.tv/discord")
            elif activity_type == "Competing":
                activity = discord.Activity(type=discord.ActivityType.competing, name=status_text.strip())
            else:
                activity = discord.Game(name=status_text.strip())
        kwargs = {"activity": activity}
        if status is not None:
            kwargs["status"] = status
        await self.bot.change_presence(**kwargs)
        return True

    async def set_nick(self, guild_id, nick):
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            raise RuntimeError("Guild not found")
        member = guild.get_member(self.bot.user.id) or guild.me
        await member.edit(nick=nick)
        return True

    async def send_dm(self, user_id, text):
        if not text.strip():
            raise RuntimeError("Message text is empty")
        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        await user.send(text)
        return True

    async def get_dm_history(self, user_id, limit=50):
        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        ch = getattr(user, "dm_channel", None)
        if ch is None:
            ch = await user.create_dm()
        rows = []
        async for msg in ch.history(limit=int(limit)):
            when = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
            author = "Me" if msg.author.id == self.bot.user.id else safe_name(msg.author)
            body = msg.content or ""
            if getattr(msg, "attachments", None):
                body += " " + " ".join(f"[Attachment: {a.filename}]" for a in msg.attachments)
            rows.append(f"[{when}] {author}: {body}")
        rows.reverse()
        return "\n".join(rows) if rows else "No messages found."

    async def get_channel_history(self, channel_id, limit=50):
        ch = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
        rows = []
        async for msg in ch.history(limit=int(limit)):
            when = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
            author = safe_name(msg.author)
            rows.append(f"[{when}] {author}: {msg.content}")
        rows.reverse()
        return "\n".join(rows) if rows else "No messages found."

    async def preview_own_messages(self, target_type, target_id, limit):
        ch = await self._resolve_target_channel(target_type, target_id)
        rows = []
        count = 0
        async for msg in ch.history(limit=int(limit)):
            if msg.author.id == self.bot.user.id:
                count += 1
                when = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
                rows.append(f"[{when}] {msg.content[:180]}")
        if not rows:
            return "No own messages found in the search limit."
        return f"Found: {count}\n\n" + "\n".join(rows[:200])

    async def delete_own_messages(self, target_type, target_id, limit):
        self.clean_stop = False
        self.roles_by_guild = {}
        self.members_by_guild = {}
        ch = await self._resolve_target_channel(target_type, target_id)
        deleted = 0
        scanned = 0
        async for msg in ch.history(limit=int(limit)):
            if self.clean_stop:
                break
            scanned += 1
            if msg.author.id == self.bot.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.45)
                except Exception as e:
                    self.app.log(f"Delete failed: {e}")
        return {"deleted": deleted, "scanned": scanned, "stopped": self.clean_stop}

    async def _resolve_target_channel(self, target_type, target_id):
        if target_type == "DM User ID":
            user = self.bot.get_user(int(target_id)) or await self.bot.fetch_user(int(target_id))
            ch = getattr(user, "dm_channel", None) or await user.create_dm()
            return ch
        return self.bot.get_channel(int(target_id)) or await self.bot.fetch_channel(int(target_id))

    def stop_cleaner(self):
        self.clean_stop = True

    async def get_user_info(self, user_id):
        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        return (
            f"User: {user}\n"
            f"Display: {getattr(user, 'display_name', '')}\n"
            f"ID: {user.id}\n"
            f"Bot: {getattr(user, 'bot', False)}\n"
            f"Created: {getattr(user, 'created_at', '')}\n"
            f"Avatar: {self._avatar_url(user)}"
        )

    async def get_server_info(self, guild_id):
        g = self.bot.get_guild(int(guild_id))
        if not g:
            raise RuntimeError("Server not found")
        return (
            f"Server: {g.name}\nID: {g.id}\nMembers: {g.member_count}\nOwner: {g.owner}\n"
            f"Channels: {len(g.channels)}\nText Channels: {len(getattr(g, 'text_channels', []))}\n"
            f"Roles: {len(g.roles)}\nCreated: {g.created_at}"
        )

    async def get_channel_info(self, channel_id):
        ch = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
        guild = getattr(ch, "guild", None)
        return (
            f"Channel: {getattr(ch, 'name', ch)}\nID: {ch.id}\n"
            f"Guild: {getattr(guild, 'name', 'DM/None')}\n"
            f"Type: {type(ch).__name__}\nTopic: {getattr(ch, 'topic', '')}"
        )

    async def get_roles(self, guild_id):
        g = self.bot.get_guild(int(guild_id))
        if not g:
            raise RuntimeError("Server not found")
        roles = sorted(g.roles, key=lambda r: r.position, reverse=True)
        return "\n".join([f"{r.name} | ID: {r.id} | Members: {len(getattr(r, 'members', []))}" for r in roles]) or "No roles found."

    async def search_members(self, guild_id, query, limit=30):
        g = self.bot.get_guild(int(guild_id))
        if not g:
            raise RuntimeError("Server not found")
        q = (query or "").lower().strip()
        results = []
        for m in list(getattr(g, "members", [])):
            if not q or q in str(m).lower() or q in getattr(m, "display_name", "").lower() or q in str(m.id):
                results.append(f"{m} | Display: {getattr(m, 'display_name', '')} | ID: {m.id}")
            if len(results) >= int(limit):
                break
        return "\n".join(results) if results else "No cached members found."

    async def admin_overview(self, guild_id):
        g = self.bot.get_guild(int(guild_id))
        if not g:
            raise RuntimeError("Server not found")
        me = g.get_member(self.bot.user.id) or g.me
        p = getattr(me, "guild_permissions", None)
        perms = []
        if p:
            for name, value in p:
                if value:
                    perms.append(name)
        return (
            f"Admin overview for {g.name}\n"
            f"Dein Member: {me}\n"
            f"Top Rolle: {getattr(getattr(me, 'top_role', None), 'name', '-')}\n"
            f"Active permissions:\n- " + "\n- ".join(perms[:80])
        )

    async def admin_action(self, action, guild_id, channel_id="", user_id="", role_id="", value="", extra="", category_id="", options_text=""):
        g = self.bot.get_guild(int(guild_id))
        if not g:
            raise RuntimeError("Server not found")
        me = g.get_member(self.bot.user.id) or g.me
        perms = getattr(me, "guild_permissions", None)
        if not perms:
            raise RuntimeError("Permissions could not be read")

        def can(*names):
            return bool(getattr(perms, "administrator", False) or any(getattr(perms, n, False) for n in names))

        async def get_channel(required=False):
            if channel_id and not str(channel_id).startswith("No "):
                return self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
            if required:
                raise RuntimeError("Select a channel for this command")
            return None

        async def get_member(required=False):
            if user_id and not str(user_id).startswith("No "):
                return g.get_member(int(user_id)) or await g.fetch_member(int(user_id))
            if required:
                raise RuntimeError("Select a member or enter a member ID")
            return None

        def get_role(required=False):
            if role_id and not str(role_id).startswith("No "):
                r = g.get_role(int(role_id))
                if r:
                    return r
            if required:
                raise RuntimeError("Select a role for this command")
            return None

        def get_category(required=False):
            if category_id and not str(category_id).startswith("No ") and str(category_id) != "0":
                cat = g.get_channel(int(category_id))
                if cat:
                    return cat
            if required:
                raise RuntimeError("Select a category for this command")
            return None

        try:
            opts = json.loads(options_text or "{}")
        except Exception:
            opts = {}

        def opt(name, default=""):
            return str(opts.get(name, default) or "").strip()

        def opt_bool(name, default=False):
            raw = opt(name, "True" if default else "False").lower()
            return raw in {"true", "1", "yes", "y", "on"}

        def parse_int(text, default=0):
            try:
                return int(str(text).strip())
            except Exception:
                return default

        def parse_color(text):
            txt = str(text or "").strip().lstrip("#")
            try:
                return discord.Color(int(txt, 16)) if txt else discord.Color.default()
            except Exception:
                return discord.Color.default()

        def parse_permissions(text):
            p = discord.Permissions.none()
            for perm_name, _label in COMMON_ROLE_PERMISSIONS:
                if opt_bool(f"perm_{perm_name}", False) and hasattr(p, perm_name):
                    try:
                        setattr(p, perm_name, True)
                    except Exception:
                        pass
            for name in [x.strip() for x in str(text or "").split(",") if x.strip()]:
                if hasattr(p, name):
                    try:
                        setattr(p, name, True)
                    except Exception:
                        pass
            return p

        reason = extra or "Alpha Self Bot"

        if action == "Admin Overview":
            return await self.admin_overview(guild_id)
        if action == "Permission List":
            lines = [name for name, enabled in perms if enabled]
            return "Active permissions:\n- " + "\n- ".join(lines)
        if action == "Audit Log Preview":
            if not can("view_audit_log"):
                raise RuntimeError("Missing view_audit_log permission")
            rows = []
            async for entry in g.audit_logs(limit=15):
                rows.append(f"{entry.created_at:%Y-%m-%d %H:%M} | {entry.action} | user={entry.user} | target={entry.target} | reason={entry.reason}")
            return "\n".join(rows) if rows else "No audit log entries returned."

        if action == "Create Instant Invite":
            if not can("create_instant_invite"):
                raise RuntimeError("Missing create_instant_invite permission")
            ch = await get_channel(True)
            invite = await ch.create_invite(
                max_age=parse_int(opt("invite_max_age", value or 86400), 86400),
                max_uses=parse_int(opt("invite_max_uses", 0), 0),
                temporary=opt_bool("invite_temporary", False),
                unique=opt_bool("invite_unique", True),
                reason=reason,
            )
            return f"Invite created for #{getattr(ch, 'name', ch.id)}: {invite}"
        if action in {"Create Channel", "Create Text Channel"}:
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            raw_name = opt("channel_name", value or "new-channel")
            name = raw_name.strip().replace(" ", "-").lower() or "new-channel"
            kind = opt("channel_type", "Text")
            cat = get_category(False)
            topic = opt("topic", "") or None
            slowmode = parse_int(opt("slowmode", 0), 0)
            nsfw = opt_bool("nsfw", False)
            pos_text = opt("position", "")
            pos = parse_int(pos_text, None) if pos_text else None
            kwargs = {"reason": reason}
            if pos is not None:
                kwargs["position"] = pos
            if kind == "Category":
                ch = await g.create_category(name=name, **kwargs)
            elif kind == "Voice":
                ch = await g.create_voice_channel(name=name, category=cat, bitrate=parse_int(opt("bitrate", 64000), 64000), user_limit=parse_int(opt("user_limit", 0), 0), **kwargs)
            elif kind == "Stage":
                ch = await g.create_stage_channel(name=name, category=cat, **kwargs)
            elif kind == "Announcement":
                ch = await g.create_text_channel(name=name, category=cat, topic=topic, slowmode_delay=slowmode, nsfw=nsfw, news=True, **kwargs)
            elif kind == "Forum":
                if hasattr(g, "create_forum"):
                    ch = await g.create_forum(name=name, category=cat, topic=topic, nsfw=nsfw, **kwargs)
                else:
                    ch = await g.create_text_channel(name=name, category=cat, topic=topic, slowmode_delay=slowmode, nsfw=nsfw, **kwargs)
            else:
                ch = await g.create_text_channel(name=name, category=cat, topic=topic, slowmode_delay=slowmode, nsfw=nsfw, **kwargs)
            where = f" in {cat.name}" if cat else " without category"
            await self.refresh_cache()
            return f"{kind} channel created: {getattr(ch, 'name', ch.id)} ({ch.id}){where}"
        if action == "Create Category":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            name = (opt("category_name", value or "new-category").strip() or "new-category")
            pos_text = opt("position", "")
            kwargs = {"reason": reason}
            if pos_text:
                kwargs["position"] = parse_int(pos_text, 0)
            cat = await g.create_category(name=name, **kwargs)
            await self.refresh_cache()
            return f"Category created: {cat.name} ({cat.id})"
        if action == "Delete Channel":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            name = getattr(ch, "name", ch.id)
            await ch.delete(reason=reason)
            await self.refresh_cache()
            return f"Channel deleted: {name}"
        if action == "Rename Channel":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            if not value.strip():
                raise RuntimeError("Enter the new channel name")
            await ch.edit(name=value.strip(), reason=reason)
            await self.refresh_cache()
            return f"Channel renamed to: {value.strip()}"
        if action == "Move Channel To Category":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            cat = get_category(False)
            await ch.edit(category=cat, reason=reason)
            await self.refresh_cache()
            return f"Channel moved: {getattr(ch, 'name', ch.id)} -> {getattr(cat, 'name', 'No category')}"
        if action == "Set Channel Topic":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            await ch.edit(topic=value or None, reason=reason)
            return f"Topic updated for #{getattr(ch, 'name', ch.id)}."
        if action == "Set Channel NSFW":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            await ch.edit(nsfw=opt_bool("bool_value", False), reason=reason)
            return f"NSFW set to {opt_bool('bool_value', False)} for #{getattr(ch, 'name', ch.id)}."
        if action == "Clone Channel":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            clone = await ch.clone(name=value.strip() or None, reason=reason)
            await self.refresh_cache()
            return f"Channel cloned: {getattr(clone, 'name', clone.id)} ({clone.id})"
        if action in ["Lock Channel", "Unlock Channel"]:
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            overwrite = ch.overwrites_for(g.default_role)
            overwrite.send_messages = False if action == "Lock Channel" else None
            await ch.set_permissions(g.default_role, overwrite=overwrite, reason=reason)
            return f"{action} completed for #{getattr(ch, 'name', ch.id)}."
        if action == "Set Slowmode":
            if not can("manage_channels"):
                raise RuntimeError("Missing manage_channels permission")
            ch = await get_channel(True)
            seconds = int(value or 0)
            await ch.edit(slowmode_delay=seconds, reason=reason)
            return f"Slowmode set to {seconds}s for #{getattr(ch, 'name', ch.id)}."
        if action == "Clear Own Messages In Channel":
            ch = await get_channel(True)
            limit = int(value or 100)
            deleted = 0
            async for msg in ch.history(limit=limit):
                if msg.author.id == self.bot.user.id:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.45)
            return f"Deleted {deleted} own messages in #{getattr(ch, 'name', ch.id)}."
        if action == "Pin Latest Message":
            if not can("pin_messages", "manage_messages"):
                raise RuntimeError("Missing pin_messages/manage_messages permission")
            ch = await get_channel(True)
            async for msg in ch.history(limit=1):
                await msg.pin(reason=reason)
                return f"Pinned latest message in #{getattr(ch, 'name', ch.id)}."
            return "No message found to pin."

        if action == "Kick Member":
            if not can("kick_members"):
                raise RuntimeError("Missing kick_members permission")
            member = await get_member(True)
            await member.kick(reason=reason)
            return f"Member kicked: {member}"
        if action == "Ban Member":
            if not can("ban_members"):
                raise RuntimeError("Missing ban_members permission")
            member = await get_member(True)
            await member.ban(reason=reason, delete_message_days=parse_int(opt("delete_message_days", 0), 0))
            return f"Member banned: {member}"
        if action == "Timeout Member":
            if not can("moderate_members"):
                raise RuntimeError("Missing moderate_members permission")
            member = await get_member(True)
            minutes = int(value or 10)
            until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
            await member.edit(timed_out_until=until, reason=reason)
            return f"Member timed out for {minutes} minutes: {member}"
        if action == "Remove Timeout":
            if not can("moderate_members"):
                raise RuntimeError("Missing moderate_members permission")
            member = await get_member(True)
            await member.edit(timed_out_until=None, reason=reason)
            return f"Timeout removed: {member}"
        if action == "Change Member Nickname":
            if not can("manage_nicknames"):
                raise RuntimeError("Missing manage_nicknames permission")
            member = await get_member(True)
            await member.edit(nick=value or None, reason=reason)
            return f"Nickname changed for {member}: {value or 'reset'}"
        if action == "Set My Nickname":
            if not can("change_nickname", "manage_nicknames"):
                raise RuntimeError("Missing change_nickname/manage_nicknames permission")
            await me.edit(nick=value or None, reason=reason)
            return f"Own nickname changed: {value or 'reset'}"

        if action == "Create Role":
            if not can("manage_roles"):
                raise RuntimeError("Missing manage_roles permission")
            name = opt("role_name", value or "New Role") or "New Role"
            role = await g.create_role(
                name=name,
                colour=parse_color(opt("role_color", "")),
                permissions=parse_permissions(opt("role_permissions", "")),
                hoist=opt_bool("role_hoist", False),
                mentionable=opt_bool("role_mentionable", False),
                reason=reason,
            )
            pos_text = opt("role_position", "")
            if pos_text:
                try:
                    await role.edit(position=parse_int(pos_text, role.position), reason=reason)
                except Exception:
                    pass
            await self.refresh_cache()
            return f"Role created: {role.name} ({role.id})"
        if action == "Edit Role":
            if not can("manage_roles"):
                raise RuntimeError("Missing manage_roles permission")
            role = get_role(True)
            kwargs = {"reason": reason}
            if opt("role_name", ""):
                kwargs["name"] = opt("role_name")
            if opt("role_color", ""):
                kwargs["colour"] = parse_color(opt("role_color"))
            has_guided_permission = any(opt_bool(f"perm_{perm_name}", False) for perm_name, _label in COMMON_ROLE_PERMISSIONS)
            if opt("role_permissions", "") or has_guided_permission:
                kwargs["permissions"] = parse_permissions(opt("role_permissions"))
            kwargs["hoist"] = opt_bool("role_hoist", getattr(role, "hoist", False))
            kwargs["mentionable"] = opt_bool("role_mentionable", getattr(role, "mentionable", False))
            await role.edit(**kwargs)
            pos_text = opt("role_position", "")
            if pos_text:
                try:
                    await role.edit(position=parse_int(pos_text, role.position), reason=reason)
                except Exception:
                    pass
            await self.refresh_cache()
            return f"Role edited: {role.name} ({role.id})"
        if action == "Delete Role":
            if not can("manage_roles"):
                raise RuntimeError("Missing manage_roles permission")
            role = get_role(True)
            name = role.name
            await role.delete(reason=reason)
            await self.refresh_cache()
            return f"Role deleted: {name}"
        if action in ["Add Role", "Remove Role"]:
            if not can("manage_roles"):
                raise RuntimeError("Missing manage_roles permission")
            member = await get_member(True)
            role = get_role(True)
            if action == "Add Role":
                await member.add_roles(role, reason=reason)
                return f"Role {role.name} added to {member}."
            await member.remove_roles(role, reason=reason)
            return f"Role {role.name} removed from {member}."

        if action == "Send Message":
            if not can("send_messages"):
                raise RuntimeError("Missing send_messages permission")
            ch = await get_channel(True)
            if not value.strip():
                raise RuntimeError("Enter a message in the value field")
            file_path = opt("file_path", "")
            if file_path and Path(file_path).exists():
                await ch.send(value.strip() or None, file=discord.File(file_path))
            else:
                await ch.send(value.strip())
            return f"Message sent to #{getattr(ch, 'name', ch.id)}."
        if action == "Send TTS Message":
            if not can("send_tts_messages"):
                raise RuntimeError("Missing send_tts_messages permission")
            ch = await get_channel(True)
            if not value.strip():
                raise RuntimeError("Enter a TTS message in the value field")
            await ch.send(value.strip(), tts=True)
            return f"TTS message sent to #{getattr(ch, 'name', ch.id)}."
        if action == "Add Reaction To Latest Message":
            if not can("add_reactions"):
                raise RuntimeError("Missing add_reactions permission")
            ch = await get_channel(True)
            emoji = value.strip() or "👍"
            async for msg in ch.history(limit=1):
                await msg.add_reaction(emoji)
                return f"Reaction {emoji} added to latest message in #{getattr(ch, 'name', ch.id)}."
            return "No message found."
        if action == "Delete Latest Own Message":
            if not can("manage_messages"):
                pass
            ch = await get_channel(True)
            async for msg in ch.history(limit=int(value or 25)):
                if msg.author.id == self.bot.user.id:
                    await msg.delete()
                    return f"Deleted your latest message in #{getattr(ch, 'name', ch.id)}."
            return "No own message found in the selected scan limit."

        if action in ["Create Public Thread", "Create Private Thread"]:
            required_perm = "create_public_threads" if action == "Create Public Thread" else "create_private_threads"
            if not can(required_perm, "manage_threads"):
                raise RuntimeError(f"Missing {required_perm}/manage_threads permission")
            ch = await get_channel(True)
            thread_type = discord.ChannelType.public_thread if action == "Create Public Thread" else discord.ChannelType.private_thread
            thread = await ch.create_thread(name=value.strip() or "alpha-thread", type=thread_type, reason=reason)
            return f"Thread created: {thread.name} ({thread.id})"

        return "Unknown or unsupported command."



class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Alpha Self Bot")
        self.geometry("1500x900")
        self.minsize(1300, 800)
        self.after(250, lambda: self.state("zoomed"))
        self.configure(fg_color=BG)
        try:
            icon_img = Image.open(APP_DIR / "assets" / "logo.png").resize((32, 32))
            self.window_icon = ImageTk.PhotoImage(icon_img)
            self.iconphoto(False, self.window_icon)
        except Exception:
            pass
        self.worker = DiscordWorker(self)
        self.guilds = []
        self.channels = []
        self.dms = []
        self.admin_guilds = []
        self.dm_avatar_refs = []
        self.preview_avatar_ref = None
        self.selected_avatar_file = None
        self.selected_dm = None
        self.current_tab = None
        self._build_ui()

    def style_entry(self, parent, placeholder=""):
        return ctk.CTkEntry(parent, placeholder_text=placeholder, fg_color=CARD_2, border_color=PINK_DARK, text_color=TEXT, placeholder_text_color=MUTED)

    def style_button(self, parent, text, cmd, width=120):
        return ctk.CTkButton(parent, text=text, command=cmd, width=width, fg_color=PINK_DARK, hover_color=PINK, text_color="white", corner_radius=12)

    def style_option(self, parent, values):
        return ctk.CTkOptionMenu(parent, values=values, fg_color=CARD_2, button_color=PINK_DARK, button_hover_color=PINK, text_color=TEXT, dropdown_fg_color=CARD_2, dropdown_hover_color=PINK_DARK)

    def small_label(self, parent, text, width=115):
        return ctk.CTkLabel(parent, text=text, text_color=PINK_2, width=width, anchor="w", font=("Segoe UI", 12, "bold"))

    def target_row(self, parent, label):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=5)
        self.small_label(row, label).pack(side="left", padx=4)
        return row

    def card(self, parent):
        return ctk.CTkFrame(parent, fg_color=CARD, border_color=PINK_DARK, border_width=1, corner_radius=18)

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="#0f0815", corner_radius=0, height=58)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")

        try:
            logo_path = APP_DIR / "assets" / "logo.png"
            logo_img = Image.open(logo_path).resize((38, 38))
            self.header_logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(38, 38))
            ctk.CTkLabel(header, image=self.header_logo, text="").pack(side="left", padx=(18, 8), pady=10)
        except Exception:
            pass
        ctk.CTkLabel(header, text="Alpha Self Bot", font=("Segoe UI", 25, "bold"), text_color=PINK).pack(side="left", padx=(6, 12), pady=13)
        ctk.CTkLabel(header, text="private dashboard", font=("Segoe UI", 13), text_color=MUTED).pack(side="left", padx=8)

        self.sidebar = ctk.CTkFrame(self, fg_color=BG_2, corner_radius=0, width=210)
        self.sidebar.grid(row=1, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_propagate(False)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.frames = {}
        self.nav_buttons = {}
        nav = ["Connect", "Profile", "DM Center", "Servers", "Admin", "Cleaner", "Monitor", "Logs"]
        ctk.CTkLabel(self.sidebar, text="MENU", text_color=MUTED, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(20, 8))
        for name in nav:
            b = ctk.CTkButton(self.sidebar, text=name, command=lambda n=name: self.show_tab(n), anchor="w", fg_color="transparent", hover_color=CARD_2, text_color=TEXT, corner_radius=12)
            b.pack(fill="x", padx=12, pady=4)
            self.nav_buttons[name] = b
            f = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
            f.grid(row=0, column=0, sticky="nsew")
            f.grid_propagate(False)
            self.frames[name] = f

        self.build_connect_tab(self.frames["Connect"])
        self.build_profile_tab(self.frames["Profile"])
        self.build_dm_tab(self.frames["DM Center"])
        self.build_servers_tab(self.frames["Servers"])
        self.build_admin_tab(self.frames["Admin"])
        self.build_cleaner_tab(self.frames["Cleaner"])
        self.build_monitor_tab(self.frames["Monitor"])
        self.build_logs_tab(self.frames["Logs"])

        self.footer = ctk.CTkFrame(self, fg_color="#0f0815", corner_radius=0, height=34)
        self.footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.footer_label = ctk.CTkLabel(self.footer, text="Offline | User: - | Servers: 0 | DMs: 0", text_color=MUTED)
        self.footer_label.pack(side="left", padx=18)
        self.transition_overlay = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self.transition_overlay.grid(row=0, column=0, sticky="nsew")
        self.transition_overlay.grid_remove()
        self._transition_running = False

        self.show_tab("Connect", instant=True)

    def page_title(self, parent, title, subtitle=""):
        ctk.CTkLabel(parent, text=title, text_color=PINK, font=("Segoe UI", 26, "bold")).pack(anchor="w", padx=24, pady=(20, 0))
        if subtitle:
            ctk.CTkLabel(parent, text=subtitle, text_color=MUTED, font=("Segoe UI", 13)).pack(anchor="w", padx=24, pady=(2, 12))

    def show_tab(self, name, instant=False):
        """Soft tab switch.

        Tk/CustomTkinter can flicker on Windows when a large page is raised.
        Instead of rebuilding pages or forcing update_idletasks(), this method briefly
        covers the content area with the app background, switches the page behind it,
        then removes the cover. The result is a softer transition instead of a hard
        black flash.
        """
        if getattr(self, "current_tab", None) == name:
            return

        old = getattr(self, "current_tab", None)
        self.current_tab = name
        frame = self.frames.get(name)
        if frame is None:
            return

        def update_nav():
            if old in getattr(self, "nav_buttons", {}):
                try:
                    self.nav_buttons[old].configure(fg_color="transparent", text_color=TEXT)
                except Exception:
                    pass
            if name in getattr(self, "nav_buttons", {}):
                try:
                    self.nav_buttons[name].configure(fg_color=PINK_DARK, text_color="white")
                except Exception:
                    pass

        def do_raise():
            try:
                frame.tkraise()
                frame.lift()
            except Exception:
                pass
            update_nav()

        if instant or not hasattr(self, "transition_overlay"):
            do_raise()
            return

        if getattr(self, "_transition_running", False):
            do_raise()
            return

        self._transition_running = True
        try:
            self.transition_overlay.configure(fg_color=BG)
            self.transition_overlay.grid()
            self.transition_overlay.tkraise()
        except Exception:
            do_raise()
            self._transition_running = False
            return

        def finish():
            do_raise()
            try:
                self.transition_overlay.grid_remove()
            except Exception:
                pass
            self._transition_running = False

        self.after(80, finish)

    def build_connect_tab(self, tab):
        self.page_title(tab, "Connect", "Sign in, save the local .env token, and refresh cached servers, channels, and DMs.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(1, weight=2)
        wrap.grid_rowconfigure(0, weight=1)

        left = self.card(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8)
        right = self.card(wrap)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(left, text="Connection", text_color=PINK_2, font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        token_card = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        token_card.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(token_card, text="Token", text_color=PINK_2, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        self.token_entry = self.style_entry(token_card, "Token from .env or paste here")
        self.token_entry.configure(show="*")
        self.token_entry.pack(fill="x", padx=14, pady=(4, 12))
        token = os.getenv("DISCORD_TOKEN", "")
        if token and token != "PASTE_TOKEN_HERE":
            self.token_entry.insert(0, token)

        self.token_visible = False
        self.style_button(left, "Connect", self.connect).pack(fill="x", padx=16, pady=(8, 5))
        self.style_button(left, "Save .env", self.save_token).pack(fill="x", padx=16, pady=5)
        self.style_button(left, "Show / Hide token", self.toggle_token_visibility).pack(fill="x", padx=16, pady=5)
        self.style_button(left, "Refresh cache", self.refresh_lists).pack(fill="x", padx=16, pady=(5, 14))

        ctk.CTkLabel(right, text="Session status", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        status_card = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        status_card.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        ctk.CTkLabel(status_card, text="Use the left card to connect. After connecting, refresh cache to populate all dropdowns and lists.", text_color=TEXT, justify="left", wraplength=780).pack(anchor="w", padx=16, pady=14)
        ctk.CTkLabel(status_card, text="The token field is hidden by default and is stored only in the local .env file inside this extracted folder.", text_color=MUTED, justify="left", wraplength=780).pack(anchor="w", padx=16, pady=(0, 14))
        self.connect_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.connect_output.grid(row=2, column=0, sticky="nsew", padx=16, pady=(8, 16))

    def build_profile_tab(self, tab):
        self.page_title(tab, "Profile", "Change avatar, activity, online state, and server nickname from unified cards.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(1, weight=2)
        wrap.grid_rowconfigure(0, weight=1)

        left = self.card(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)
        right = self.card(wrap)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(left, text="Avatar", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        preview_card = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        preview_card.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        self.avatar_preview = ctk.CTkLabel(preview_card, text="Preview", width=128, height=128, fg_color=CARD_2, text_color=MUTED, corner_radius=16)
        self.avatar_preview.pack(anchor="center", padx=16, pady=14)
        self.avatar_file_label = ctk.CTkLabel(preview_card, text="No file selected", text_color=MUTED, wraplength=360)
        self.avatar_file_label.pack(anchor="center", padx=16, pady=(0, 14))

        avatar_controls = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        avatar_controls.grid(row=2, column=0, sticky="nsew", padx=16, pady=(8, 16))
        avatar_controls.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(avatar_controls, text="Image URL", text_color=PINK_2, font=("Segoe UI", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 2))
        self.avatar_url = self.style_entry(avatar_controls, "Paste image URL")
        self.avatar_url.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=6)
        self.style_button(avatar_controls, "Preview link", self.preview_avatar_url).grid(row=2, column=0, sticky="ew", padx=(14, 5), pady=6)
        self.style_button(avatar_controls, "Set link", self.set_avatar_url).grid(row=2, column=1, sticky="ew", padx=(5, 14), pady=6)
        self.style_button(avatar_controls, "Choose image", self.choose_avatar_file).grid(row=3, column=0, sticky="ew", padx=(14, 5), pady=(6, 14))
        self.style_button(avatar_controls, "Set file", self.set_avatar_file).grid(row=3, column=1, sticky="ew", padx=(5, 14), pady=(6, 14))

        ctk.CTkLabel(right, text="Status & nickname", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        form = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        form.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
        form.grid_columnconfigure(1, weight=1)
        self.small_label(form, "Online status", width=130).grid(row=0, column=0, sticky="w", padx=14, pady=(16, 6))
        self.online_status = self.style_option(form, ["online", "idle", "dnd", "invisible"])
        self.online_status.grid(row=0, column=1, sticky="ew", padx=14, pady=(16, 6))
        self.small_label(form, "Activity type", width=130).grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.status_type = self.style_option(form, ["Playing", "Watching", "Listening", "Streaming", "Competing"])
        self.status_type.grid(row=1, column=1, sticky="ew", padx=14, pady=6)
        self.small_label(form, "Activity text", width=130).grid(row=2, column=0, sticky="w", padx=14, pady=6)
        self.status_text = self.style_entry(form, "Activity name / text")
        self.status_text.grid(row=2, column=1, sticky="ew", padx=14, pady=6)
        self.style_button(form, "Apply status", self.set_status).grid(row=3, column=1, sticky="ew", padx=14, pady=(10, 20))
        ctk.CTkFrame(form, height=1, fg_color=PINK_DARK).grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=8)
        self.small_label(form, "Server", width=130).grid(row=5, column=0, sticky="w", padx=14, pady=6)
        self.nick_guild = self.style_option(form, ["No servers loaded"])
        self.nick_guild.grid(row=5, column=1, sticky="ew", padx=14, pady=6)
        self.small_label(form, "Nickname", width=130).grid(row=6, column=0, sticky="w", padx=14, pady=6)
        self.nick_entry = self.style_entry(form, "New nickname")
        self.nick_entry.grid(row=6, column=1, sticky="ew", padx=14, pady=6)
        self.style_button(form, "Apply nickname", self.set_nick).grid(row=7, column=1, sticky="ew", padx=14, pady=(10, 16))

    def build_dm_tab(self, tab):
        self.page_title(tab, "DM Center", "Search open DMs, select users, read history, and send messages directly.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent"); wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1); wrap.grid_columnconfigure(1, weight=2)
        wrap.grid_rowconfigure(0, weight=1)
        left = self.card(wrap); left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8)
        right = self.card(wrap); right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8)

        top = ctk.CTkFrame(left, fg_color="transparent"); top.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(top, text="Offene DMs", text_color=PINK_2, font=("Segoe UI", 18, "bold")).pack(side="left", padx=4)
        self.style_button(top, "Refresh", self.refresh_lists, width=90).pack(side="right", padx=4)
        self.dm_search = self.style_entry(left, "Suche nach Username, Displayname oder ID")
        self.dm_search.pack(fill="x", padx=16, pady=(0, 8))
        self.dm_search.bind("<KeyRelease>", lambda e: self.render_dm_list())
        self.dm_scroll = ctk.CTkScrollableFrame(left, fg_color="#0b0710", border_color=PINK_DARK, border_width=1)
        self.dm_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.dm_header = ctk.CTkLabel(right, text="No user selected", text_color=PINK_2, font=("Segoe UI", 18, "bold"))
        self.dm_header.pack(anchor="w", padx=16, pady=(14, 4))
        dm_id_row = ctk.CTkFrame(right, fg_color="transparent"); dm_id_row.pack(fill="x", padx=12, pady=4)
        self.dm_target = self.style_entry(dm_id_row, "User ID")
        self.dm_target.pack(side="left", fill="x", expand=True, padx=4)
        self.dm_history_limit = self.style_option(dm_id_row, ["25", "50", "100", "200"])
        self.dm_history_limit.pack(side="left", padx=4)
        self.style_button(dm_id_row, "Load Verlauf", self.load_dm_history, width=120).pack(side="left", padx=4)
        self.dm_history = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.dm_history.pack(fill="both", expand=True, padx=16, pady=10)
        self.dm_text = ctk.CTkTextbox(right, height=110, fg_color=CARD_2, text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.dm_text.pack(fill="x", padx=16, pady=(0, 8))
        self.style_button(right, "DM senden", self.send_dm).pack(fill="x", padx=16, pady=(0, 16))

    def build_servers_tab(self, tab):
        self.page_title(tab, "Servers", "Browse servers, channels, roles, members, and message history in a DM-Center style layout.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(1, weight=2)
        wrap.grid_rowconfigure(0, weight=1)
        left = self.card(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8)
        left.grid_columnconfigure(0, weight=1)
        right = self.card(wrap)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(left, text="Selection", text_color=PINK_2, font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        pick = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        pick.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        ctk.CTkLabel(pick, text="Server", text_color=PINK_2, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        self.info_guild = self.style_option(pick, ["No servers loaded"])
        self.info_guild.pack(fill="x", padx=14, pady=6)
        btns = ctk.CTkFrame(pick, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=6)
        btns.grid_columnconfigure((0, 1), weight=1)
        self.style_button(btns, "Server info", self.server_info).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.style_button(btns, "Roles", self.roles_info).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(pick, text="Channel", text_color=PINK_2, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        self.info_channel = self.style_option(pick, ["No channels loaded"])
        self.info_channel.pack(fill="x", padx=14, pady=6)
        row = ctk.CTkFrame(pick, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=6)
        row.grid_columnconfigure((0, 1), weight=1)
        self.style_button(row, "Channel info", self.channel_info).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.style_button(row, "Read messages", self.channel_history).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(pick, text="History limit", text_color=PINK_2, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        self.server_history_limit = self.style_option(pick, ["25", "50", "100", "200"])
        self.server_history_limit.pack(fill="x", padx=14, pady=(6, 14))

        ctk.CTkLabel(right, text="User / member lookup", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        lookup = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        lookup.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        lookup.grid_columnconfigure(0, weight=1)
        lookup.grid_columnconfigure(1, weight=1)
        self.user_info_id = self.style_entry(lookup, "User ID for user info")
        self.user_info_id.grid(row=0, column=0, sticky="ew", padx=(14, 5), pady=(14, 6))
        self.style_button(lookup, "User info", self.user_info).grid(row=0, column=1, sticky="ew", padx=(5, 14), pady=(14, 6))
        self.member_search = self.style_entry(lookup, "Search member: name, display name, or ID")
        self.member_search.grid(row=1, column=0, sticky="ew", padx=(14, 5), pady=(6, 14))
        self.style_button(lookup, "Search member", self.search_members).grid(row=1, column=1, sticky="ew", padx=(5, 14), pady=(6, 14))
        ctk.CTkLabel(right, text="Output", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=2, column=0, sticky="w", padx=16, pady=(8, 4))
        self.info_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.info_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(8, 16))

    def build_admin_tab(self, tab):
        self.page_title(tab, "Admin Dashboard", "Choose a server, pick a command page on the left, then use the guided controls on the right.")
        box = self.card(tab)
        box.pack(fill="both", expand=True, padx=24, pady=18)
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)

        self.admin_commands_by_category = {
            "Overview": ["Admin Overview", "Permission List", "Audit Log Preview", "Server Summary"],
            "Channels": ["Create Channel", "Create Text Channel", "Create Category", "Delete Channel", "Rename Channel", "Move Channel To Category", "Set Channel Topic", "Set Channel NSFW", "Set Slowmode", "Lock Channel", "Unlock Channel", "Clone Channel", "Create Instant Invite", "Clear Own Messages In Channel", "Delete Latest Own Message", "Pin Latest Message"],
            "Messages": ["Send Message", "Send TTS Message", "Add Reaction To Latest Message"],
            "Members": ["Kick Member", "Ban Member", "Timeout Member", "Remove Timeout", "Change Member Nickname", "Set My Nickname"],
            "Roles": ["Create Role", "Edit Role", "Delete Role", "Add Role", "Remove Role"],
            "Threads": ["Create Public Thread", "Create Private Thread"],
        }
        self.admin_selected_category = "Overview"
        self.admin_selected_command = "Admin Overview"

        top = ctk.CTkFrame(box, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        top.grid_columnconfigure(1, weight=1)
        self.admin_badge = ctk.CTkLabel(top, text="● Admin access detected", text_color=GOOD, font=("Segoe UI", 16, "bold"))
        self.admin_badge.grid(row=0, column=0, sticky="w", padx=(4, 12))
        self.admin_guild = self.style_option(top, ["No admin servers loaded"])
        self.admin_guild.configure(command=lambda _: self.on_admin_guild_change())
        self.admin_guild.grid(row=0, column=1, sticky="ew", padx=4)
        self.style_button(top, "Refresh", self.refresh_lists, width=115).grid(row=0, column=2, padx=4)
        self.style_button(top, "Overview", self.admin_overview, width=115).grid(row=0, column=3, padx=4)

        body = ctk.CTkFrame(box, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))
        body.grid_columnconfigure(0, weight=2, minsize=360)
        body.grid_columnconfigure(1, weight=6)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=CARD_2, corner_radius=18, border_color=PINK_DARK, border_width=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=4)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(3, weight=2)
        ctk.CTkLabel(left, text="Command pages", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        group_list = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
        group_list.grid(row=1, column=0, sticky="nsew", padx=14, pady=(8, 8))
        self.admin_category_buttons = {}
        for cat in self.admin_commands_by_category.keys():
            btn = ctk.CTkButton(
                group_list,
                text=cat,
                anchor="w",
                fg_color=CARD,
                hover_color=PINK_DARK,
                text_color=TEXT,
                corner_radius=14,
                command=lambda c=cat: self.select_admin_category(c),
            )
            btn.pack(fill="x", padx=8, pady=5)
            self.admin_category_buttons[cat] = btn

        ctk.CTkLabel(left, text="Actions", text_color=PINK_2, font=("Segoe UI", 15, "bold")).grid(row=2, column=0, sticky="w", padx=16, pady=(8, 4))
        self.admin_command_list = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
        self.admin_command_list.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.admin_command_buttons = {}

        right = ctk.CTkFrame(body, fg_color=CARD_2, corner_radius=18, border_color=PINK_DARK, border_width=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=4)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(right, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        header.grid_columnconfigure(0, weight=1)
        self.admin_command_title = ctk.CTkLabel(header, text="Command Center", text_color=PINK_2, font=("Segoe UI", 20, "bold"), anchor="w")
        self.admin_command_title.grid(row=0, column=0, sticky="ew")
        self.admin_hint = ctk.CTkLabel(header, text="Select a command page on the left. This area changes to only show the controls needed for that command.", text_color=MUTED, anchor="w", justify="left")
        self.admin_hint.grid(row=1, column=0, sticky="ew", pady=(4, 2))

        self.admin_targets_card = ctk.CTkFrame(right, fg_color="#050408", corner_radius=16, border_color=PINK_DARK, border_width=1)
        self.admin_targets_card.grid(row=1, column=0, sticky="nsew", padx=14, pady=(8, 8))
        self.admin_targets_card.grid_columnconfigure(0, weight=1)
        self.admin_targets_card.grid_rowconfigure(1, weight=1)
        self.admin_targets_title = ctk.CTkLabel(self.admin_targets_card, text="Guided command controls", text_color=GOOD, font=("Segoe UI", 16, "bold"))
        self.admin_targets_title.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        self.admin_targets_frame = ctk.CTkScrollableFrame(self.admin_targets_card, fg_color="transparent")
        self.admin_targets_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        bottom = ctk.CTkFrame(right, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        bottom.grid_columnconfigure(0, weight=1)
        self.admin_run_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        self.admin_run_frame.grid(row=0, column=0, sticky="ew")
        self.admin_confirm = ctk.CTkSwitch(self.admin_run_frame, text="No confirmation needed", progress_color=PINK_DARK, text_color=TEXT)
        self.admin_confirm.pack(side="left", padx=4)
        self.style_button(self.admin_run_frame, "Run command", self.run_admin_action, width=170).pack(side="right", padx=4)
        self.admin_output = ctk.CTkTextbox(bottom, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1, height=110)
        self.admin_output.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.select_admin_category("Overview")

    def select_admin_category(self, category):
        self.admin_selected_category = category
        for c, btn in getattr(self, 'admin_category_buttons', {}).items():
            btn.configure(fg_color=PINK_DARK if c == category else CARD, text_color="white" if c == category else TEXT)
        values = self.admin_commands_by_category.get(category, ["Admin Overview"])
        self.admin_selected_command = values[0]
        self.render_admin_command_buttons(values)
        self.on_admin_command_change()

    def render_admin_command_buttons(self, values):
        if not hasattr(self, 'admin_command_list'):
            return
        for w in self.admin_command_list.winfo_children():
            w.destroy()
        self.admin_command_buttons = {}
        for cmd in values:
            spec = self.admin_command_spec(cmd)
            btn = ctk.CTkButton(
                self.admin_command_list,
                text=cmd,
                anchor="w",
                fg_color=PINK_DARK if cmd == getattr(self, 'admin_selected_command', '') else CARD,
                hover_color=PINK_DARK,
                text_color="white" if cmd == getattr(self, 'admin_selected_command', '') else TEXT,
                corner_radius=14,
                command=lambda c=cmd: self.select_admin_command(c),
            )
            btn.pack(fill="x", padx=8, pady=5)
            self.admin_command_buttons[cmd] = btn
            short = spec.get("short", spec.get("hint", ""))
            if short:
                ctk.CTkLabel(self.admin_command_list, text=short, text_color=MUTED, anchor="w", justify="left", wraplength=290).pack(fill="x", padx=16, pady=(0, 5))

    def select_admin_command(self, cmd):
        self.admin_selected_command = cmd
        for c, btn in getattr(self, 'admin_command_buttons', {}).items():
            btn.configure(fg_color=PINK_DARK if c == cmd else CARD, text_color="white" if c == cmd else TEXT)
        self.on_admin_command_change()

    def current_admin_command(self):
        return getattr(self, 'admin_selected_command', 'Admin Overview')

    def build_cleaner_tab(self, tab):
        self.page_title(tab, "Cleaner", "Preview and delete your own messages. Server channels and DMs are separated with searchable cards.")
        box = self.card(tab); box.pack(fill="both", expand=True, padx=24, pady=18)
        box.grid_columnconfigure(0, weight=2)
        box.grid_columnconfigure(1, weight=3)
        box.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(box, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=14)
        left.grid_rowconfigure(3, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        mode_row = ctk.CTkFrame(left, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        mode_row.grid_columnconfigure((0, 1), weight=1)
        self.clean_server_btn = self.style_button(mode_row, "Server Channels", lambda: self.set_cleaner_mode("server"), width=120)
        self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=4)
        self.clean_dm_btn = self.style_button(mode_row, "DM Users", lambda: self.set_cleaner_mode("dm"), width=120)
        self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=4)
        self.clean_search = self.style_entry(left, "Search server, channel, user, or ID")
        self.clean_search.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
        self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=12)
        self.clean_target_scroll.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))

        right = ctk.CTkFrame(box, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=14)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(right, text="Selected target", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        detail.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=74, height=74, font=("Segoe UI", 34))
        self.clean_icon.pack(side="left", padx=12, pady=12)
        self.clean_detail = ctk.CTkLabel(detail, text="Choose a server channel or DM user from the list.", text_color=TEXT, justify="left", anchor="w")
        self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)

        opts = ctk.CTkFrame(right, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=10, pady=6)
        opts.grid_columnconfigure(1, weight=1)
        self.small_label(opts, "Scan limit", width=90).grid(row=0, column=0, sticky="w", padx=4, pady=5)
        self.clean_limit = self.style_option(opts, ["25", "50", "100", "200", "500", "1000"])
        self.clean_limit.grid(row=0, column=1, sticky="ew", padx=4, pady=5)
        self.clean_manual_label = self.small_label(opts, "Manual DM ID", width=90)
        self.clean_manual_label.grid(row=1, column=0, sticky="w", padx=4, pady=5)
        self.clean_dm_user = self.style_entry(opts, "Optional manual DM user ID")
        self.clean_dm_user.grid(row=1, column=1, sticky="ew", padx=4, pady=5)

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=8)
        actions.grid_columnconfigure((0,1,2,3), weight=1)
        self.style_button(actions, "Preview", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
        self.style_button(actions, "Delete own", self.delete_own_messages).grid(row=0, column=1, sticky="ew", padx=4)
        self.style_button(actions, "Stop", self.stop_cleaner).grid(row=0, column=2, sticky="ew", padx=4)
        self.style_button(actions, "Refresh lists", self.refresh_lists).grid(row=0, column=3, sticky="ew", padx=4)
        self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.clean_output.grid(row=4, column=0, sticky="nsew", padx=14, pady=(0, 14))

        self.clean_mode = "server"
        self.clean_selected_type = "Server Channel"
        self.clean_selected_target = ""
        self.clean_card_images = []
        self.set_cleaner_mode("server")

    def build_monitor_tab(self, tab):
        self.page_title(tab, "Monitor / Auto", "Configure server and DM monitoring with the same card layout as DM Center.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(1, weight=2)
        wrap.grid_rowconfigure(0, weight=1)
        left = self.card(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8)
        left.grid_columnconfigure(0, weight=1)
        right = self.card(wrap)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(left, text="Targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        target_box = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        target_box.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self.monitor_server_switch = ctk.CTkSwitch(target_box, text="Server monitoring", progress_color=PINK_DARK, text_color=TEXT)
        self.monitor_server_switch.select(); self.monitor_server_switch.pack(anchor="w", padx=14, pady=(14, 6))
        self.monitor_channel = self.style_option(target_box, ["No channels loaded"])
        self.monitor_channel.pack(fill="x", padx=14, pady=6)
        row = ctk.CTkFrame(target_box, fg_color="transparent"); row.pack(fill="x", padx=10, pady=6)
        row.grid_columnconfigure((0, 1), weight=1)
        self.style_button(row, "Add channel", self.add_monitor_channel).grid(row=0, column=0, sticky="ew", padx=4)
        self.style_button(row, "Clear list", self.clear_monitor_channels).grid(row=0, column=1, sticky="ew", padx=4)
        self.monitor_label = ctk.CTkLabel(target_box, text="Active server channels: all / unrestricted", text_color=MUTED, justify="left", wraplength=360)
        self.monitor_label.pack(anchor="w", padx=14, pady=(2, 14))
        ctk.CTkFrame(target_box, height=1, fg_color=PINK_DARK).pack(fill="x", padx=14, pady=8)
        self.monitor_dm_switch = ctk.CTkSwitch(target_box, text="DM monitoring", progress_color=PINK_DARK, text_color=TEXT)
        self.monitor_dm_switch.select(); self.monitor_dm_switch.pack(anchor="w", padx=14, pady=(8, 6))
        self.monitor_dm_select = self.style_option(target_box, ["All DMs"])
        self.monitor_dm_select.pack(fill="x", padx=14, pady=6)
        rowdm = ctk.CTkFrame(target_box, fg_color="transparent"); rowdm.pack(fill="x", padx=10, pady=6)
        rowdm.grid_columnconfigure((0, 1), weight=1)
        self.style_button(rowdm, "Add DM", self.add_monitor_dm).grid(row=0, column=0, sticky="ew", padx=4)
        self.style_button(rowdm, "Clear list", self.clear_monitor_dms).grid(row=0, column=1, sticky="ew", padx=4)
        self.monitor_dm_label = ctk.CTkLabel(target_box, text="Active DMs: all", text_color=MUTED, justify="left", wraplength=360)
        self.monitor_dm_label.pack(anchor="w", padx=14, pady=(2, 14))

        ctk.CTkLabel(right, text="Automation rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        rules = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
        rules.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        rules.grid_columnconfigure(1, weight=1)
        self.reply_switch = ctk.CTkSwitch(rules, text="Auto Reply", progress_color=PINK_DARK, text_color=TEXT)
        self.reply_switch.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))
        self.reply_keywords = self.style_entry(rules, "Reply keywords separated by comma, empty = all")
        self.reply_keywords.grid(row=0, column=1, sticky="ew", padx=14, pady=(14, 6))
        self.small_label(rules, "Reply text", width=120).grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.reply_text = self.style_entry(rules, "Reply text")
        self.reply_text.grid(row=1, column=1, sticky="ew", padx=14, pady=6)
        self.small_label(rules, "Cooldown", width=120).grid(row=2, column=0, sticky="w", padx=14, pady=6)
        self.reply_cooldown = self.style_entry(rules, "Cooldown per user/channel in seconds")
        self.reply_cooldown.insert(0, "30")
        self.reply_cooldown.grid(row=2, column=1, sticky="ew", padx=14, pady=6)
        ctk.CTkFrame(rules, height=1, fg_color=PINK_DARK).grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=8)
        self.react_switch = ctk.CTkSwitch(rules, text="Auto React", progress_color=PINK_DARK, text_color=TEXT)
        self.react_switch.grid(row=4, column=0, sticky="w", padx=14, pady=6)
        self.react_keywords = self.style_entry(rules, "React keywords, empty = all")
        self.react_keywords.grid(row=4, column=1, sticky="ew", padx=14, pady=6)
        self.small_label(rules, "Emoji", width=120).grid(row=5, column=0, sticky="w", padx=14, pady=(6, 14))
        self.react_emoji = self.style_entry(rules, "Emoji")
        self.react_emoji.insert(0, "💖")
        self.react_emoji.grid(row=5, column=1, sticky="ew", padx=14, pady=(6, 14))
        self.style_button(right, "Apply auto settings", self.apply_auto_settings).grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        self.monitor_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.monitor_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(8, 16))

    def build_logs_tab(self, tab):
        self.page_title(tab, "Logs", "Live output with timestamp, user, channel, and message content.")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)
        header = self.card(wrap)
        header.grid(row=0, column=0, sticky="ew", pady=(8, 12))
        ctk.CTkLabel(header, text="Log controls", text_color=PINK_2, font=("Segoe UI", 18, "bold")).pack(side="left", padx=16, pady=14)
        self.style_button(header, "Clear logs", lambda: self.logbox.delete("1.0", "end")).pack(side="right", padx=16, pady=12)
        box = self.card(wrap)
        box.grid(row=1, column=0, sticky="nsew")
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.logbox = ctk.CTkTextbox(box, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
        self.logbox.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)

    def log(self, text):
        def write():
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            try:
                self.logbox.insert("end", f"[{stamp}] {text}\n")
                self.logbox.see("end")
            except Exception:
                pass
        self.after(0, write)

    def selected_value_id(self, option_text):
        if " | " in option_text:
            return option_text.rsplit(" | ", 1)[-1]
        return option_text

    def connect(self):
        token = self.token_entry.get().strip()
        if not token or token == "PASTE_TOKEN_HERE":
            messagebox.showerror("Missing token", "Enter your token.")
            return
        self.worker.start(token)

    def save_token(self):
        token = self.token_entry.get().strip()
        ENV_PATH.touch(exist_ok=True)
        set_key(str(ENV_PATH), "DISCORD_TOKEN", token)
        self.log("Token saved to .env")

    def on_connected(self, name, uid):
        self.footer_label.configure(text=f"Online | User: {name} ({uid}) | Servers: {len(self.guilds)} | DMs: {len(self.dms)}", text_color=PINK_2)

    def refresh_lists(self):
        fut = self.worker.run_coro(self.worker.refresh_cache())
        if fut:
            fut.add_done_callback(lambda f: self.log("Cache refreshed" if not f.exception() else f"Refresh error: {f.exception()}"))

    def update_lists(self, guilds, channels, dms, admin_guilds, roles_by_guild=None, members_by_guild=None, categories_by_guild=None):
        self.guilds, self.channels, self.dms, self.admin_guilds = guilds, channels, dms, admin_guilds
        self.roles_by_guild = roles_by_guild or {}
        self.members_by_guild = members_by_guild or {}
        self.categories_by_guild = categories_by_guild or {}
        guild_values = [f"{g['name']} | {g['id']}" for g in guilds] or ["No servers loaded"]
        channel_values = [f"{c.get('full_name') or c['name']} | {c['id']}" for c in channels] or ["No channels loaded"]
        admin_values = [f"{g['name']} | {g['id']}" for g in admin_guilds] or ["No admin servers loaded"]

        def keep_or_first(menu, values):
            try:
                current = menu.get()
            except Exception:
                current = ""
            menu.configure(values=values)
            if current in values:
                menu.set(current)
            else:
                menu.set(values[0])

        for menu in [self.nick_guild, self.info_guild]:
            keep_or_first(menu, guild_values)
        dm_values = [f"{d.get('display') or d['name']} | {d['id']}" for d in dms] or ["No DMs loaded"]
        for menu in [self.info_channel, self.monitor_channel]:
            keep_or_first(menu, channel_values)
        if hasattr(self, "render_cleaner_targets"):
            self.render_cleaner_targets()
        monitor_values = ["All DMs"] + dm_values
        keep_or_first(self.monitor_dm_select, monitor_values)
        keep_or_first(self.admin_guild, admin_values)
        self.admin_badge.configure(text=("● Admin access detected" if admin_guilds else "● No admin servers detected"), text_color=(GOOD if admin_guilds else MUTED))
        self.on_admin_guild_change()
        self.footer_label.configure(text=f"Online | User: {getattr(self.worker.bot, 'user', '-')} | Servers: {len(guilds)} | DMs: {len(dms)}", text_color=PINK_2)
        self.render_dm_list()

    def render_dm_list(self):
        for w in self.dm_scroll.winfo_children():
            w.destroy()
        self.dm_avatar_refs.clear()
        q = self.dm_search.get().lower().strip() if hasattr(self, "dm_search") else ""
        dms = [d for d in self.dms if not q or q in d.get("name", "").lower() or q in d.get("display", "").lower() or q in d.get("id", "")]
        if not dms:
            ctk.CTkLabel(self.dm_scroll, text="No DMs found. Press refresh or change the search text.", text_color=MUTED).pack(padx=10, pady=10)
            return
        for dm in dms:
            row = ctk.CTkFrame(self.dm_scroll, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
            row.pack(fill="x", padx=8, pady=6)
            avatar_label = ctk.CTkLabel(row, text="", width=52, height=52)
            avatar_label.pack(side="left", padx=8, pady=8)
            self.load_avatar_async(dm.get("avatar"), avatar_label, 52)
            info_text = f"{dm.get('display') or dm['name']}\n{dm['name']}\nID: {dm['id']}"
            ctk.CTkLabel(row, text=info_text, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
            self.style_button(row, "Open", lambda d=dm: self.select_dm(d), width=86).pack(side="right", padx=8, pady=8)

    def load_avatar_async(self, url, label, size=48):
        def task():
            try:
                if not url:
                    raise RuntimeError("no url")
                import requests
                data = requests.get(url, timeout=8).content
                img = Image.open(BytesIO(data)).convert("RGB").resize((size, size))
                photo = ImageTk.PhotoImage(img)
                self.dm_avatar_refs.append(photo)
                self.after(0, lambda: label.configure(image=photo, text=""))
            except Exception:
                self.after(0, lambda: label.configure(text="👤", text_color=PINK))
        threading.Thread(target=task, daemon=True).start()

    def set_avatar_preview_image(self, image):
        image = image.convert("RGB")
        image.thumbnail((180, 180))
        photo = ImageTk.PhotoImage(image)
        self.preview_avatar_ref = photo
        self.avatar_preview.configure(image=photo, text="")

    def preview_avatar_url(self):
        url = self.avatar_url.get().strip()
        if not url:
            return self.log("Avatar URL is missing")
        def task():
            try:
                import requests
                data = requests.get(url, timeout=8).content
                img = Image.open(BytesIO(data))
                self.after(0, lambda: self.set_avatar_preview_image(img))
            except Exception as e:
                self.log(f"Preview error: {e}")
        threading.Thread(target=task, daemon=True).start()

    def choose_avatar_file(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("All files", "*.*")])
        if path:
            self.selected_avatar_file = path
            self.avatar_file_label.configure(text=path)
            try:
                self.set_avatar_preview_image(Image.open(path))
            except Exception as e:
                self.log(f"Preview error: {e}")

    def select_dm(self, dm):
        self.selected_dm = dm
        self.dm_target.delete(0, "end"); self.dm_target.insert(0, dm["id"])
        self.clean_dm_user.delete(0, "end"); self.clean_dm_user.insert(0, dm["id"])
        self.dm_header.configure(text=f"{dm.get('display') or dm['name']} | {dm['id']}")
        self.show_tab("DM Center")
        self.load_dm_history()

    def toggle_token_visibility(self):
        self.token_visible = not getattr(self, "token_visible", False)
        self.token_entry.configure(show="" if self.token_visible else "*")

    def admin_current_guild_id(self):
        return self.selected_value_id(self.admin_guild.get())

    def values_for_guild_channels(self, gid):
        vals = []
        guild_name = next((g.get('name') for g in self.guilds if str(g.get('id')) == str(gid)), "Server")
        for c in self.channels:
            if str(c.get('guild_id')) == str(gid):
                name = c.get('name') or c.get('full_name', 'channel')
                name = name if str(name).startswith('#') else f"#{name}"
                vals.append(f"{guild_name} > {name} | {c['id']}")
        return vals or ["No channels loaded"]

    def values_for_guild_categories(self, gid):
        vals = ["No category | 0"]
        for c in self.categories_by_guild.get(str(gid), []):
            vals.append(f"{c['name']} | {c['id']}")
        return vals

    def values_for_guild_roles(self, gid):
        vals = []
        for r in self.roles_by_guild.get(str(gid), []):
            if r.get('name') != '@everyone':
                vals.append(f"{r['name']} | pos {r.get('position', 0)} | {r['id']}")
        return vals or ["No roles loaded"]

    def values_for_guild_members(self, gid, query=""):
        q = (query or "").lower().strip()
        vals = []
        for m in self.members_by_guild.get(str(gid), []):
            hay = f"{m.get('display','')} {m.get('name','')} {m.get('id','')}".lower()
            if not q or q in hay:
                tag = "bot" if m.get('bot') else "user"
                vals.append(f"{m.get('display') or m['name']} ({m['name']}) [{tag}] | {m['id']}")
        return vals[:300] or ["No members found"]

    def on_admin_guild_change(self):
        self.render_admin_permissions()
        if hasattr(self, 'admin_targets_frame'):
            self.on_admin_command_change()

    def on_admin_category_change(self):
        category = getattr(self, 'admin_selected_category', 'Overview')
        self.select_admin_category(category)

    def render_admin_permissions(self):
        if not hasattr(self, 'admin_perm_box'):
            return
        for w in self.admin_perm_box.winfo_children():
            w.destroy()
        gid = self.admin_current_guild_id()
        if not gid or gid.startswith('No '):
            ctk.CTkLabel(self.admin_perm_box, text="No admin server selected.", text_color=MUTED).pack(anchor="w", padx=10, pady=10)
            return
        fut = self.worker.run_coro(self.worker.admin_overview(gid))
        if fut:
            def done(f):
                try:
                    text = f.result()
                except Exception as e:
                    text = f"Could not load permissions: {e}"
                self.after(0, lambda: self._render_perm_text(text))
            fut.add_done_callback(done)

    def _render_perm_text(self, text):
        for w in self.admin_perm_box.winfo_children():
            w.destroy()
        active = []
        capture = False
        for line in text.splitlines():
            if line.strip() == "Active permissions:":
                capture = True
                continue
            if capture and line.startswith("- "):
                active.append(line[2:])
        if not active:
            active = ["No permission details available"]
        cols = 2 if len(active) < 24 else 3
        for col in range(cols):
            self.admin_perm_box.grid_columnconfigure(col, weight=1)
        for i, perm in enumerate(active):
            row = i // cols
            col = i % cols
            ctk.CTkLabel(
                self.admin_perm_box,
                text=f"✓ {perm}",
                text_color=GOOD,
                anchor="w",
                font=("Segoe UI", 12)
            ).grid(row=row, column=col, sticky="w", padx=10, pady=4)

    def admin_command_spec(self, cmd):
        specs = {
            "Admin Overview": {"hint": "Shows your role, top role, and detected permissions.", "needs": []},
            "Permission List": {"hint": "Lists the detected permissions for the selected server.", "needs": []},
            "Audit Log Preview": {"hint": "Shows the latest audit log entries. Requires view_audit_log.", "needs": []},
            "Server Summary": {"hint": "Shows server information.", "needs": []},
            "Create Instant Invite": {"hint": "Channel + invite settings.", "needs": ["channel", "invite_options", "reason"]},
            "Create Channel": {"hint": "Create a channel using a guided page with type, category, topic, NSFW, slowmode and voice settings.", "short": "Guided page for all channel types.", "needs": ["category", "channel_create_options", "reason"]},
            "Create Text Channel": {"hint": "Legacy shortcut. Creates a text channel with detailed options.", "needs": ["category", "channel_create_options", "reason"]},
            "Create Category": {"hint": "Create a new category.", "needs": ["category_create_options", "reason"]},
            "Delete Channel": {"hint": "Choose the exact channel to delete. Confirmation is required.", "needs": ["channel", "reason", "confirm"], "destructive": True},
            "Rename Channel": {"hint": "Choose channel and enter the new name.", "needs": ["channel", "value", "reason"], "value": "New channel name"},
            "Move Channel To Category": {"hint": "Choose a channel and its new category. Use No category to move out of categories.", "needs": ["channel", "category", "reason"]},
            "Set Channel Topic": {"hint": "Choose text/forum channel and set topic.", "needs": ["channel", "value", "reason"], "value": "New topic"},
            "Set Channel NSFW": {"hint": "Choose channel and set NSFW true/false.", "needs": ["channel", "bool", "reason"]},
            "Clone Channel": {"hint": "Clone a selected channel. Optional value = new name.", "needs": ["channel", "value", "reason"], "value": "Optional new clone name"},
            "Lock Channel": {"hint": "Choose channel. Disables @everyone send_messages overwrite.", "needs": ["channel", "reason"]},
            "Unlock Channel": {"hint": "Choose channel. Resets @everyone send_messages overwrite.", "needs": ["channel", "reason"]},
            "Set Slowmode": {"hint": "Choose channel. Value = slowmode seconds.", "needs": ["channel", "value", "reason"], "value": "Seconds, e.g. 10"},
            "Clear Own Messages In Channel": {"hint": "Choose channel. Value = scan limit for your own messages.", "needs": ["channel", "value"], "value": "Scan limit, e.g. 100"},
            "Delete Latest Own Message": {"hint": "Choose channel. Deletes your latest message found in the scan limit.", "needs": ["channel", "value"], "value": "Scan limit, e.g. 25"},
            "Pin Latest Message": {"hint": "Choose channel. Pins the latest visible message.", "needs": ["channel", "reason"]},
            "Send Message": {"hint": "Choose a channel, type your message and optionally attach a file path.", "short": "Send text or a file.", "needs": ["channel", "message", "file_optional"]},
            "Send TTS Message": {"hint": "Choose channel and enter TTS message text.", "needs": ["channel", "message"]},
            "Add Reaction To Latest Message": {"hint": "Choose channel. Value = emoji to add to the latest message.", "needs": ["channel", "value"], "value": "Emoji, e.g. 👍"},
            "Kick Member": {"hint": "Search/select a member. Confirmation is required.", "needs": ["member", "reason", "confirm"], "destructive": True},
            "Ban Member": {"hint": "Search/select a member. Optional delete-message days. Confirmation required.", "needs": ["member", "ban_options", "reason", "confirm"], "destructive": True},
            "Timeout Member": {"hint": "Search/select a member. Value = timeout minutes.", "needs": ["member", "value", "reason"], "value": "Minutes, e.g. 10"},
            "Remove Timeout": {"hint": "Search/select a member.", "needs": ["member", "reason"]},
            "Change Member Nickname": {"hint": "Search/select a member. Value = new nickname, empty resets.", "needs": ["member", "value", "reason"], "value": "New nickname, empty = reset"},
            "Set My Nickname": {"hint": "Value = your new nickname, empty resets.", "needs": ["value", "reason"], "value": "Your new nickname"},
            "Create Role": {"hint": "Create a role with name, color, switches and guided permission toggles.", "short": "Name, color and permission flags.", "needs": ["role_create_options", "reason"]},
            "Edit Role": {"hint": "Select a role and edit name/color/flags/permissions.", "needs": ["role", "role_create_options", "reason"]},
            "Delete Role": {"hint": "Choose the role to delete. Confirmation is required.", "needs": ["role", "reason", "confirm"], "destructive": True},
            "Add Role": {"hint": "Search/select a member, then choose the role to add.", "needs": ["member", "role", "reason"]},
            "Remove Role": {"hint": "Search/select a member, then choose the role to remove.", "needs": ["member", "role", "reason"]},
            "Create Public Thread": {"hint": "Choose channel. Value = thread name.", "needs": ["channel", "value", "reason"], "value": "Thread name"},
            "Create Private Thread": {"hint": "Choose channel. Value = thread name.", "needs": ["channel", "value", "reason"], "value": "Thread name"},
        }
        return specs.get(cmd, {"hint": "Select command-specific targets below.", "needs": []})

    def bool_option(self, parent, label, default="False"):
        row = self.target_row(parent, label)
        opt = self.style_option(row, ["False", "True"])
        opt.set(default)
        opt.pack(side="left", fill="x", expand=True, padx=4)
        return opt

    def add_extra_entry(self, key, label, placeholder=""):
        row = self.target_row(self.admin_targets_frame, label)
        e = self.style_entry(row, placeholder)
        e.pack(side="left", fill="x", expand=True, padx=4)
        self.admin_extra_widgets[key] = e
        return e

    def add_extra_option(self, key, label, values, default=None):
        row = self.target_row(self.admin_targets_frame, label)
        opt = self.style_option(row, values)
        opt.set(default or values[0])
        opt.pack(side="left", fill="x", expand=True, padx=4)
        self.admin_extra_widgets[key] = opt
        return opt

    def add_extra_switch(self, key, label, default=False):
        row = self.target_row(self.admin_targets_frame, label)
        sw = ctk.CTkSwitch(row, text="Enabled" if default else "Disabled", progress_color=PINK_DARK, text_color=TEXT)
        if default:
            sw.select()
        sw.configure(command=lambda s=sw: s.configure(text="Enabled" if s.get() else "Disabled"))
        sw.pack(side="left", fill="x", expand=True, padx=4)
        self.admin_extra_widgets[key] = sw
        return sw

    def add_extra_slider(self, key, label, minimum, maximum, default=0, suffix=""):
        row = self.target_row(self.admin_targets_frame, label)
        value_label = ctk.CTkLabel(row, text=str(default) + suffix, text_color=TEXT, width=90)
        slider = ctk.CTkSlider(row, from_=minimum, to=maximum, progress_color=PINK_DARK, button_color=PINK_2)
        slider.set(default)
        slider.configure(command=lambda v, lab=value_label: lab.configure(text=str(int(float(v))) + suffix))
        slider.pack(side="left", fill="x", expand=True, padx=4)
        value_label.pack(side="left", padx=4)
        self.admin_extra_widgets[key] = slider
        return slider

    def add_permission_switch_grid(self):
        section = ctk.CTkFrame(self.admin_targets_frame, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
        section.pack(fill="x", padx=8, pady=(8, 8))
        for col in range(4):
            section.grid_columnconfigure(col, weight=1)
        ctk.CTkLabel(
            section,
            text="Quick permissions",
            text_color=PINK_2,
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            section,
            text="Turn on common role permissions here. Use Advanced permissions for rare permission names.",
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=950,
        ).grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 6))
        for idx, (perm_name, label) in enumerate(COMMON_ROLE_PERMISSIONS):
            row = 2 + idx // 4
            col = idx % 4
            sw = ctk.CTkSwitch(
                section,
                text=label,
                progress_color=PINK_DARK,
                button_color=PINK_2,
                text_color=TEXT,
                font=("Segoe UI", 11),
                width=170,
            )
            sw.grid(row=row, column=col, sticky="w", padx=8, pady=3)
            self.admin_extra_widgets[f"perm_{perm_name}"] = sw
        return section

    def add_emoji_picker(self):
        section = ctk.CTkFrame(self.admin_targets_frame, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
        section.pack(fill="x", padx=8, pady=(8, 8))
        ctk.CTkLabel(section, text="Emoji picker", text_color=PINK_2, font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(section, text="Click an emoji to fill the value field. You can still paste custom emojis manually.", text_color=MUTED, anchor="w").pack(fill="x", padx=12, pady=(0, 6))
        grid = ctk.CTkFrame(section, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(0, 10))
        emojis = ["👍", "👎", "💖", "😂", "🔥", "✅", "❌", "👀", "🎉", "🙏", "😭", "🤔", "😎", "⭐", "🚀", "⚠️", "💯", "😅", "🫡", "☠️"]
        for i, emoji in enumerate(emojis):
            btn = ctk.CTkButton(
                grid,
                text=emoji,
                width=42,
                height=34,
                fg_color=CARD,
                hover_color=PINK_DARK,
                text_color=TEXT,
                corner_radius=10,
                command=lambda e=emoji: self.set_admin_value(e),
            )
            btn.grid(row=i // 10, column=i % 10, padx=4, pady=4)
        return section

    def set_admin_value(self, text):
        if self.admin_value:
            self.admin_value.delete(0, "end")
            self.admin_value.insert(0, text)

    def collect_admin_options(self):
        data = {}
        for k, widget in getattr(self, "admin_extra_widgets", {}).items():
            try:
                if hasattr(widget, "get"):
                    raw = widget.get()
                    if isinstance(raw, float):
                        data[k] = str(int(raw))
                    elif isinstance(raw, int):
                        data[k] = "True" if raw == 1 else "False"
                    else:
                        data[k] = str(raw).strip()
                else:
                    data[k] = ""
            except Exception:
                data[k] = ""
        return data

    def on_admin_command_change(self):
        cmd = self.current_admin_command()
        spec = self.admin_command_spec(cmd)
        needs = spec.get("needs", [])
        category = getattr(self, 'admin_selected_category', '')
        if hasattr(self, 'admin_command_title'):
            self.admin_command_title.configure(text=f"{category} / {cmd}" if category else cmd)
        self.admin_hint.configure(text=spec.get("hint", ""))
        for w in self.admin_targets_frame.winfo_children():
            w.destroy()
        self.admin_channel = None
        self.admin_role = None
        self.admin_category_select = None
        self.admin_member = None
        self.admin_member_search = None
        self.admin_value = None
        self.admin_reason = None
        self.admin_extra_widgets = {}

        gid = self.admin_current_guild_id()
        target_names = []
        if "member" in needs: target_names.append("Member")
        if "channel" in needs: target_names.append("Channel")
        if "category" in needs: target_names.append("Category")
        if "role" in needs: target_names.append("Role")
        if "value" in needs: target_names.append("Text/Number")
        if "message" in needs: target_names.append("Message")
        if "channel_create_options" in needs: target_names.append("Channel details")
        if "category_create_options" in needs: target_names.append("Category details")
        if "role_create_options" in needs: target_names.append("Role details")
        if "invite_options" in needs: target_names.append("Invite details")
        if "ban_options" in needs: target_names.append("Ban details")
        if "bool" in needs: target_names.append("True/False")
        if "file_optional" in needs: target_names.append("Optional file")
        if "reason" in needs: target_names.append("Reason")
        self.admin_targets_title.configure(text=("Command page controls: " + ", ".join(target_names)) if target_names else "Command page controls")

        if not needs:
            ctk.CTkLabel(self.admin_targets_frame, text="This command has no settings. Press Run command to execute it for the selected server.", text_color=MUTED, anchor="w").pack(fill="x", padx=10, pady=8)

        if "member" in needs:
            row = self.target_row(self.admin_targets_frame, "Member search")
            self.admin_member_search = self.style_entry(row, "Type name, display name, or ID, then press Search")
            self.admin_member_search.pack(side="left", fill="x", expand=True, padx=4)
            self.style_button(row, "Search", self.filter_admin_members, width=90).pack(side="left", padx=4)
            self.style_button(row, "Reload", lambda: self.filter_admin_members(), width=80).pack(side="left", padx=4)
            rowm = self.target_row(self.admin_targets_frame, "Select member")
            vals = self.values_for_guild_members(gid)
            self.admin_member = self.style_option(rowm, vals)
            self.admin_member.set(vals[0])
            self.admin_member.pack(side="left", fill="x", expand=True, padx=4)

        if "channel" in needs:
            row = self.target_row(self.admin_targets_frame, "Select channel")
            vals = self.values_for_guild_channels(gid)
            self.admin_channel = self.style_option(row, vals)
            self.admin_channel.set(vals[0])
            self.admin_channel.pack(side="left", fill="x", expand=True, padx=4)

        if "category" in needs:
            row = self.target_row(self.admin_targets_frame, "Select category")
            vals = self.values_for_guild_categories(gid)
            self.admin_category_select = self.style_option(row, vals)
            self.admin_category_select.set(vals[0])
            self.admin_category_select.pack(side="left", fill="x", expand=True, padx=4)

        if "role" in needs:
            row = self.target_row(self.admin_targets_frame, "Select role")
            vals = self.values_for_guild_roles(gid)
            self.admin_role = self.style_option(row, vals)
            self.admin_role.set(vals[0])
            self.admin_role.pack(side="left", fill="x", expand=True, padx=4)
        if "channel_create_options" in needs:
            self.add_extra_entry("channel_name", "Channel name", "new-channel")
            self.add_extra_option("channel_type", "Channel type", ["Text", "Voice", "Category", "Announcement", "Stage", "Forum"], "Text")
            self.add_extra_entry("topic", "Topic", "Optional topic")
            self.add_extra_slider("slowmode", "Slowmode", 0, 21600, 0, "s")
            self.add_extra_switch("nsfw", "NSFW", False)
            self.add_extra_slider("bitrate", "Voice bitrate", 8000, 96000, 64000, " bps")
            self.add_extra_slider("user_limit", "Voice user limit", 0, 99, 0, " users")
            self.add_extra_entry("position", "Position", "Optional number")

        if "category_create_options" in needs:
            self.add_extra_entry("category_name", "Category name", "new-category")
            self.add_extra_entry("position", "Position", "Optional number")

        if "role_create_options" in needs:
            self.add_extra_entry("role_name", "Role name", "New Role")
            self.add_extra_entry("role_color", "Color hex", "#ff4fd8")
            self.add_permission_switch_grid()
            self.add_extra_entry("role_permissions", "Advanced permissions", "Comma list for rare perms, e.g. manage_events,send_polls")
            flags = ctk.CTkFrame(self.admin_targets_frame, fg_color="transparent")
            flags.pack(fill="x", padx=4, pady=2)
            flags.grid_columnconfigure((0, 1, 2), weight=1)
            row1 = self.target_row(flags, "Show separately")
            row1.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
            sw1 = ctk.CTkSwitch(row1, text="Disabled", progress_color=PINK_DARK, text_color=TEXT)
            sw1.configure(command=lambda s=sw1: s.configure(text="Enabled" if s.get() else "Disabled"))
            sw1.pack(side="left", fill="x", expand=True, padx=4)
            self.admin_extra_widgets["role_hoist"] = sw1
            row2 = self.target_row(flags, "Mentionable")
            row2.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
            sw2 = ctk.CTkSwitch(row2, text="Disabled", progress_color=PINK_DARK, text_color=TEXT)
            sw2.configure(command=lambda s=sw2: s.configure(text="Enabled" if s.get() else "Disabled"))
            sw2.pack(side="left", fill="x", expand=True, padx=4)
            self.admin_extra_widgets["role_mentionable"] = sw2
            self.add_extra_entry("role_position", "Position", "Optional number")

        if "invite_options" in needs:
            self.add_extra_slider("invite_max_age", "Max age", 0, 604800, 86400, "s")
            self.add_extra_slider("invite_max_uses", "Max uses", 0, 100, 0, " uses")
            self.add_extra_switch("invite_temporary", "Temporary membership", False)
            self.add_extra_switch("invite_unique", "Unique invite", True)

        if "ban_options" in needs:
            self.add_extra_slider("delete_message_days", "Delete message days", 0, 7, 0, " days")

        if "bool" in needs:
            self.add_extra_switch("bool_value", "Value", False)

        if "message" in needs:
            row = self.target_row(self.admin_targets_frame, "Message")
            self.admin_value = self.style_entry(row, spec.get("value", "Message text"))
            self.admin_value.pack(side="left", fill="x", expand=True, padx=4)

        if "file_optional" in needs:
            self.add_extra_entry("file_path", "Optional file path", "C:/path/file.png")

        if "value" in needs:
            row = self.target_row(self.admin_targets_frame, "Value")
            self.admin_value = self.style_entry(row, spec.get("value", "Value"))
            self.admin_value.pack(side="left", fill="x", expand=True, padx=4)
            if cmd == "Add Reaction To Latest Message":
                self.add_emoji_picker()

        if "reason" in needs:
            row = self.target_row(self.admin_targets_frame, "Reason")
            self.admin_reason = self.style_entry(row, "Optional audit-log reason")
            self.admin_reason.pack(side="left", fill="x", expand=True, padx=4)

        if "confirm" in needs:
            self.admin_confirm.configure(text="Confirm destructive command")
        else:
            self.admin_confirm.deselect()
            self.admin_confirm.configure(text="No confirmation needed")

    def filter_admin_members(self):
        if not self.admin_member:
            return
        gid = self.admin_current_guild_id()
        query = self.admin_member_search.get() if self.admin_member_search else ""
        vals = self.values_for_guild_members(gid, query)
        self.admin_member.configure(values=vals)
        self.admin_member.set(vals[0])

    def selected_admin_channel_id(self):
        if self.admin_channel:
            return self.selected_value_id(self.admin_channel.get())
        return ""

    def selected_admin_category_id(self):
        if getattr(self, "admin_category_select", None):
            return self.selected_value_id(self.admin_category_select.get())
        return ""

    def selected_admin_role_id(self):
        if self.admin_role:
            return self.selected_value_id(self.admin_role.get())
        return ""

    def selected_member_id(self):
        if self.admin_member:
            val = self.selected_value_id(self.admin_member.get())
            if val and not val.startswith("No "):
                return val
        return ""

    def run_admin_action(self):
        cmd = self.current_admin_command()
        spec = self.admin_command_spec(cmd)
        if spec.get("destructive") and not bool(self.admin_confirm.get()):
            messagebox.showwarning("Confirmation required", "Enable the confirmation switch before running this command.")
            return
        gid = self.admin_current_guild_id()
        cid = self.selected_admin_channel_id()
        rid = self.selected_admin_role_id()
        catid = self.selected_admin_category_id()
        uid = self.selected_member_id()
        value = self.admin_value.get().strip() if self.admin_value else ""
        reason = self.admin_reason.get().strip() if self.admin_reason else ""
        options_text = json.dumps(self.collect_admin_options(), ensure_ascii=False)
        fut = self.worker.run_coro(self.worker.admin_action(cmd, gid, cid, uid, rid, value, reason, catid, options_text))
        if fut:
            def done(f):
                self.after(0, lambda: self.show_text(self.admin_output, f))
                if cmd in {"Create Channel", "Create Text Channel", "Create Category", "Delete Channel", "Rename Channel", "Move Channel To Category", "Clone Channel", "Create Role", "Edit Role", "Delete Role"}:
                    self.after(350, self.on_admin_command_change)
            fut.add_done_callback(done)

    def add_monitor_dm(self):
        val = self.monitor_dm_select.get()
        if val == "All DMs":
            self.worker.monitor_dm_user_ids.clear()
            self.monitor_dm_label.configure(text="Active DMs: all")
            return
        try:
            uid = int(self.selected_value_id(val))
            self.worker.monitor_dm_user_ids.add(uid)
            self.monitor_dm_label.configure(text=f"Active DMs: {len(self.worker.monitor_dm_user_ids)} selected")
        except Exception as e:
            self.log(f"Monitor DM add error: {e}")

    def clear_monitor_dms(self):
        self.worker.monitor_dm_user_ids.clear()
        self.monitor_dm_label.configure(text="Active DMs: all")

    def set_avatar_url(self):
        url = self.avatar_url.get().strip()
        fut = self.worker.run_coro(self.worker.set_avatar_url(url))
        if fut: fut.add_done_callback(lambda f: self.log("Avatar changed by URL" if not f.exception() else f"Avatar error: {f.exception()}"))

    def set_avatar_file(self):
        if not self.selected_avatar_file:
            return self.log("No avatar file selected")
        fut = self.worker.run_coro(self.worker.set_avatar_file(self.selected_avatar_file))
        if fut: fut.add_done_callback(lambda f: self.log("Avatar changed by file" if not f.exception() else f"Avatar error: {f.exception()}"))

    def set_status(self):
        if self.status_image_note.get().strip():
            self.log("Activity image note: Discord activity images require Rich Presence assets/RPC; this library can set text/type/status, not arbitrary images.")
        fut = self.worker.run_coro(self.worker.set_presence(self.status_text.get(), self.status_type.get(), self.online_status.get()))
        if fut: fut.add_done_callback(lambda f: self.log("Status changed" if not f.exception() else f"Status error: {f.exception()}"))

    def set_nick(self):
        gid = self.selected_value_id(self.nick_guild.get())
        fut = self.worker.run_coro(self.worker.set_nick(gid, self.nick_entry.get()))
        if fut: fut.add_done_callback(lambda f: self.log("Nickname changed" if not f.exception() else f"Nick error: {f.exception()}"))

    def send_dm(self):
        text = self.dm_text.get("1.0", "end").strip()
        fut = self.worker.run_coro(self.worker.send_dm(self.dm_target.get().strip(), text))
        if fut: fut.add_done_callback(lambda f: self.log("DM sent" if not f.exception() else f"DM error: {f.exception()}"))

    def load_dm_history(self):
        uid = self.dm_target.get().strip()
        if not uid:
            return self.log("No user ID selected")
        fut = self.worker.run_coro(self.worker.get_dm_history(uid, int(self.dm_history_limit.get())))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.dm_history, f)))

    def server_info(self):
        gid = self.selected_value_id(self.info_guild.get())
        fut = self.worker.run_coro(self.worker.get_server_info(gid))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def roles_info(self):
        gid = self.selected_value_id(self.info_guild.get())
        fut = self.worker.run_coro(self.worker.get_roles(gid))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def channel_info(self):
        cid = self.selected_value_id(self.info_channel.get())
        fut = self.worker.run_coro(self.worker.get_channel_info(cid))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def channel_history(self):
        cid = self.selected_value_id(self.info_channel.get())
        fut = self.worker.run_coro(self.worker.get_channel_history(cid, int(self.server_history_limit.get())))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def user_info(self):
        fut = self.worker.run_coro(self.worker.get_user_info(self.user_info_id.get().strip()))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def search_members(self):
        gid = self.selected_value_id(self.info_guild.get())
        fut = self.worker.run_coro(self.worker.search_members(gid, self.member_search.get()))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))

    def admin_overview(self):
        gid = self.selected_value_id(self.admin_guild.get())
        fut = self.worker.run_coro(self.worker.admin_overview(gid))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.admin_output, f)))

    def set_cleaner_mode(self, mode):
        self.clean_mode = mode
        self.clean_selected_target = ""
        self.clean_selected_type = "DM User ID" if mode == "dm" else "Server Channel"
        try:
            self.clean_server_btn.configure(fg_color=PINK_DARK if mode == "server" else CARD_2)
            self.clean_dm_btn.configure(fg_color=PINK_DARK if mode == "dm" else CARD_2)
        except Exception:
            pass
        try:
            if mode == "server":
                self.clean_manual_label.grid_remove()
                self.clean_dm_user.grid_remove()
                self.clean_dm_user.delete(0, "end")
            else:
                self.clean_manual_label.grid()
                self.clean_dm_user.grid()
        except Exception:
            pass
        self.render_cleaner_targets()
        self.clean_detail.configure(text="Choose a server channel from the list." if mode == "server" else "Choose a DM user from the list or enter a manual ID.")
        self.clean_icon.configure(text="🏠" if mode == "server" else "👤", image=None)

    def render_cleaner_targets(self):
        if not hasattr(self, "clean_target_scroll"):
            return
        for w in self.clean_target_scroll.winfo_children():
            w.destroy()
        self.clean_card_images = []
        q = self.clean_search.get().lower().strip() if hasattr(self, "clean_search") else ""
        if getattr(self, "clean_mode", "server") == "dm":
            items = [d for d in self.dms if not q or q in d.get("name", "").lower() or q in d.get("display", "").lower() or q in d.get("id", "")]
            if not items:
                ctk.CTkLabel(self.clean_target_scroll, text="No DMs found.", text_color=MUTED).pack(padx=10, pady=10)
                return
            for dm in items:
                row = ctk.CTkFrame(self.clean_target_scroll, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
                row.pack(fill="x", padx=8, pady=6)
                avatar = ctk.CTkLabel(row, text="👤", width=54, height=54, font=("Segoe UI", 24), text_color=PINK)
                avatar.pack(side="left", padx=8, pady=8)
                self.load_avatar_async(dm.get("avatar"), avatar, 54)
                info = f"{dm.get('display') or dm.get('name')}\n{dm.get('name')}\nID: {dm.get('id')}"
                ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
                self.style_button(row, "Select", lambda d=dm: self.select_cleaner_dm(d), width=82).pack(side="right", padx=8, pady=8)
        else:
            guild_map = {str(g.get("id")): g for g in self.guilds}
            items = []
            for c in self.channels:
                g = guild_map.get(str(c.get("guild_id")), {})
                hay = f"{g.get('name','')} {c.get('name','')} {c.get('full_name','')} {c.get('id','')} {c.get('category','')}".lower()
                if not q or q in hay:
                    items.append((g, c))
            if not items:
                ctk.CTkLabel(self.clean_target_scroll, text="No server channels found.", text_color=MUTED).pack(padx=10, pady=10)
                return
            last_gid = None
            for g, c in items:
                if c.get("guild_id") != last_gid:
                    last_gid = c.get("guild_id")
                    ctk.CTkLabel(self.clean_target_scroll, text=f"● {g.get('name','Server')}  |  {g.get('members',0)} members", text_color=GOOD, anchor="w", font=("Segoe UI", 13, "bold")).pack(fill="x", padx=10, pady=(10, 3))
                row = ctk.CTkFrame(self.clean_target_scroll, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
                row.pack(fill="x", padx=8, pady=5)
                ctk.CTkLabel(row, text="#", width=42, height=42, font=("Segoe UI", 22, "bold"), text_color=PINK).pack(side="left", padx=8, pady=8)
                info = f"{c.get('name')}\nCategory: {c.get('category','No category')}\nID: {c.get('id')}"
                ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
                self.style_button(row, "Select", lambda gg=g, cc=c: self.select_cleaner_channel(gg, cc), width=82).pack(side="right", padx=8, pady=8)

    def select_cleaner_channel(self, guild, channel):
        self.clean_selected_type = "Server Channel"
        self.clean_selected_target = str(channel.get("id"))
        self.clean_detail.configure(text=f"Server: {guild.get('name')}\nChannel: {channel.get('name')}\nCategory: {channel.get('category','No category')}\nChannel ID: {channel.get('id')}\nServer ID: {guild.get('id')}")
        self.clean_icon.configure(text="#", image=None)

    def select_cleaner_dm(self, dm):
        self.clean_selected_type = "DM User ID"
        self.clean_selected_target = str(dm.get("id"))
        self.clean_dm_user.delete(0, "end")
        self.clean_dm_user.insert(0, self.clean_selected_target)
        self.clean_detail.configure(text=f"Display: {dm.get('display') or dm.get('name')}\nUsername: {dm.get('name')}\nUser ID: {dm.get('id')}")
        self.clean_icon.configure(text="👤", image=None)
        self.load_avatar_async(dm.get("avatar"), self.clean_icon, 74)

    def cleaner_target(self):
        manual = self.clean_dm_user.get().strip() if hasattr(self, "clean_dm_user") else ""
        if getattr(self, "clean_mode", "server") == "dm":
            target = manual or getattr(self, "clean_selected_target", "")
            return "DM User ID", target
        return "Server Channel", getattr(self, "clean_selected_target", "")

    def preview_cleaner(self):
        t, target = self.cleaner_target()
        if not target:
            self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
            return
        fut = self.worker.run_coro(self.worker.preview_own_messages(t, target, int(self.clean_limit.get())))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.clean_output, f)))

    def delete_own_messages(self):
        if not messagebox.askyesno("Confirm", "Delete your own messages in the selected target?"):
            return
        t, target = self.cleaner_target()
        if not target:
            self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
            return
        fut = self.worker.run_coro(self.worker.delete_own_messages(t, target, int(self.clean_limit.get())))
        if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.clean_done(f)))

    def clean_done(self, f):
        if f.exception():
            self.log(f"Delete error: {f.exception()}")
            return
        r = f.result()
        self.log(f"Cleaner done: deleted={r['deleted']} scanned={r['scanned']} stopped={r['stopped']}")
        self.clean_output.insert("end", f"\nDone: deleted={r['deleted']} scanned={r['scanned']} stopped={r['stopped']}\n")

    def stop_cleaner(self):
        self.worker.stop_cleaner()
        self.log("Cleaner stop requested")

    def show_text(self, widget, f):
        widget.delete("1.0", "end")
        widget.insert("end", f"Error: {f.exception()}" if f.exception() else str(f.result()))

    def add_monitor_channel(self):
        cid = self.selected_value_id(self.monitor_channel.get())
        try:
            self.worker.monitor_channel_ids.add(int(cid))
            self.monitor_label.configure(text=f"Active channels: {len(self.worker.monitor_channel_ids)}")
        except Exception as e:
            self.log(f"Monitor add error: {e}")

    def clear_monitor_channels(self):
        self.worker.monitor_channel_ids.clear()
        self.monitor_label.configure(text="Active channels: all / unrestricted")

    def apply_auto_settings(self):
        self.worker.monitor_servers = bool(self.monitor_server_switch.get())
        self.worker.monitor_dms = bool(self.monitor_dm_switch.get())
        self.worker.auto_reply_enabled = bool(self.reply_switch.get())
        self.worker.auto_reply_keywords = [x.strip() for x in self.reply_keywords.get().split(",") if x.strip()]
        self.worker.auto_reply_text = self.reply_text.get().strip()
        try:
            self.worker.auto_reply_cooldown = int(self.reply_cooldown.get() or 30)
        except Exception:
            self.worker.auto_reply_cooldown = 30
        self.worker.auto_react_enabled = bool(self.react_switch.get())
        self.worker.auto_react_keywords = [x.strip() for x in self.react_keywords.get().split(",") if x.strip()]
        self.worker.auto_react_emoji = self.react_emoji.get().strip() or "💖"
        self.log("Auto settings applied")



V23_EMOJIS = ["👍", "💖", "😂", "🔥", "✅", "❌", "👀", "🙏", "🎉", "😅", "😎", "💀"]
V23_LIMITS = ["25", "50", "100", "200", "500", "1000"]


def _v25_parse_limit(value):
    try:
        return max(1, min(2000, int(str(value).strip())))
    except Exception:
        return 100


async def _v25_send_dm(self, user_id, text, file_path=""):
    text = (text or "").strip()
    file_path = (file_path or "").strip()
    if not text and not file_path:
        raise RuntimeError("Message text or file is required")
    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
    if file_path and Path(file_path).exists():
        await user.send(text or None, file=discord.File(file_path))
    else:
        await user.send(text)
    return True


async def _v25_send_channel_message(self, channel_id, text, file_path="", tts=False):
    text = (text or "").strip()
    file_path = (file_path or "").strip()
    if not text and not file_path:
        raise RuntimeError("Message text or file is required")
    ch = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
    if file_path and Path(file_path).exists():
        await ch.send(text or None, file=discord.File(file_path), tts=bool(tts))
    else:
        await ch.send(text, tts=bool(tts))
    return True


async def _v25_get_dm_history(self, user_id, limit=100):
    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
    ch = getattr(user, "dm_channel", None) or await user.create_dm()
    rows = []
    async for msg in ch.history(limit=_v25_parse_limit(limit)):
        when = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
        author = "Me" if msg.author.id == self.bot.user.id else safe_name(msg.author)
        body = msg.content or ""
        if getattr(msg, "attachments", None):
            body += " " + " ".join(f"[Attachment: {a.filename} | {a.url}]" for a in msg.attachments)
        if getattr(msg, "embeds", None):
            body += f" [Embeds: {len(msg.embeds)}]"
        rows.append(f"[{when}] {author}: {body}".rstrip())
    rows.reverse()
    return "\n".join(rows) if rows else "No messages found. Increase the limit or refresh DMs."


async def _v25_get_channel_history(self, channel_id, limit=100):
    ch = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
    rows = []
    async for msg in ch.history(limit=_v25_parse_limit(limit)):
        when = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
        author = safe_name(msg.author)
        body = msg.content or ""
        if getattr(msg, "attachments", None):
            body += " " + " ".join(f"[Attachment: {a.filename} | {a.url}]" for a in msg.attachments)
        rows.append(f"[{when}] {author}: {body}".rstrip())
    rows.reverse()
    return "\n".join(rows) if rows else "No messages found."


async def _v25_get_server_info(self, guild_id):
    g = self.bot.get_guild(int(guild_id))
    if not g:
        raise RuntimeError("Server not found")
    owner = getattr(g, "owner", None)
    features = ", ".join(getattr(g, "features", []) or []) or "None"
    text_count = len(getattr(g, "text_channels", []) or [])
    voice_count = len(getattr(g, "voice_channels", []) or [])
    cat_count = len(getattr(g, "categories", []) or [])
    thread_count = len(getattr(g, "threads", []) or [])
    role_count = len(getattr(g, "roles", []) or [])
    emoji_count = len(getattr(g, "emojis", []) or [])
    sticker_count = len(getattr(g, "stickers", []) or [])
    return (
        f"Server: {g.name}\n"
        f"ID: {g.id}\n"
        f"Owner: {owner}\n"
        f"Members: {getattr(g, 'member_count', 0)}\n"
        f"Created: {getattr(g, 'created_at', '')}\n"
        f"Verification: {getattr(g, 'verification_level', '')}\n"
        f"Boost tier: {getattr(g, 'premium_tier', 0)} | Boosts: {getattr(g, 'premium_subscription_count', 0)}\n"
        f"Channels: {len(getattr(g, 'channels', []) or [])} total | Text: {text_count} | Voice: {voice_count} | Categories: {cat_count} | Threads: {thread_count}\n"
        f"Roles: {role_count} | Emojis: {emoji_count} | Stickers: {sticker_count}\n"
        f"System channel: {getattr(getattr(g, 'system_channel', None), 'name', None)}\n"
        f"Rules channel: {getattr(getattr(g, 'rules_channel', None), 'name', None)}\n"
        f"Description: {getattr(g, 'description', '') or '-'}\n"
        f"Features: {features}"
    )


DiscordWorker.send_dm = _v25_send_dm
DiscordWorker.send_channel_message = _v25_send_channel_message
DiscordWorker.get_dm_history = _v25_get_dm_history
DiscordWorker.get_channel_history = _v25_get_channel_history
DiscordWorker.get_server_info = _v25_get_server_info


def _v25_make_emoji_row(app, parent, insert_func):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    try:
        has_grid = bool(parent.grid_slaves())
        has_pack = bool(parent.pack_slaves())
    except Exception:
        has_grid = False
        has_pack = False

    if has_grid and not has_pack:
        cols, rows = parent.grid_size()
        row.grid(row=rows, column=0, columnspan=max(cols, 1), sticky="ew", padx=12, pady=(2, 6))
        try:
            parent.grid_columnconfigure(0, weight=1)
        except Exception:
            pass
    else:
        row.pack(fill="x", padx=12, pady=(2, 6))

    for emoji in V23_EMOJIS:
        b = ctk.CTkButton(row, text=emoji, width=38, height=30, fg_color=CARD_2, hover_color=PINK_DARK, text_color=TEXT, corner_radius=10, command=lambda e=emoji: insert_func(e))
        b.pack(side="left", padx=3)
    return row


def _v25_build_dm_tab(self, tab):
    self.page_title(tab, "DM Center", "Read more DM history, send messages, attach files and insert emojis from one focused view.")
    wrap = ctk.CTkFrame(tab, fg_color="transparent")
    wrap.pack(fill="both", expand=True, padx=24, pady=10)
    wrap.grid_columnconfigure(0, weight=1)
    wrap.grid_columnconfigure(1, weight=2)
    wrap.grid_rowconfigure(0, weight=1)
    left = self.card(wrap); left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=8); left.grid_columnconfigure(0, weight=1); left.grid_rowconfigure(2, weight=1)
    right = self.card(wrap); right.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=8); right.grid_columnconfigure(0, weight=1); right.grid_rowconfigure(2, weight=1)
    ctk.CTkLabel(left, text="DM users", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.dm_search = self.style_entry(left, "Search username, display name, or ID")
    self.dm_search.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8)); self.dm_search.bind("<KeyRelease>", lambda e: self.render_dm_list())
    self.dm_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    self.dm_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
    self.dm_header = ctk.CTkLabel(right, text="No DM selected", text_color=PINK_2, font=("Segoe UI", 18, "bold"), anchor="w")
    self.dm_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
    controls = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    controls.grid(row=1, column=0, sticky="ew", padx=16, pady=6); controls.grid_columnconfigure(0, weight=1)
    self.dm_target = self.style_entry(controls, "Selected user ID"); self.dm_target.grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=10)
    self.dm_history_limit = self.style_option(controls, V23_LIMITS); self.dm_history_limit.set("200"); self.dm_history_limit.grid(row=0, column=1, padx=6, pady=10)
    self.style_button(controls, "Load history", self.load_dm_history, width=120).grid(row=0, column=2, padx=(6, 12), pady=10)
    self.dm_history = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.dm_history.grid(row=2, column=0, sticky="nsew", padx=16, pady=(8, 8))
    composer = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    composer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16)); composer.grid_columnconfigure(0, weight=1)
    self.dm_text = ctk.CTkTextbox(composer, height=78, fg_color=CARD_2, text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.dm_text.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(12, 6))
    self.dm_file_path = self.style_entry(composer, "Optional file path"); self.dm_file_path.grid(row=1, column=0, sticky="ew", padx=(12, 5), pady=6)
    self.style_button(composer, "Choose file", self.choose_dm_file, width=110).grid(row=1, column=1, padx=5, pady=6)
    self.style_button(composer, "Send", self.send_dm, width=110).grid(row=1, column=2, padx=5, pady=6)
    self.style_button(composer, "Refresh", self.refresh_lists, width=110).grid(row=1, column=3, padx=(5, 12), pady=6)
    _v25_make_emoji_row(self, composer, lambda e: self.dm_text.insert("insert", e))


def _v25_build_servers_tab(self, tab):
    self.page_title(tab, "Servers", "Select one server, view only its channels, inspect details, read messages, and send text or files.")
    wrap = ctk.CTkFrame(tab, fg_color="transparent"); wrap.pack(fill="both", expand=True, padx=24, pady=10)
    wrap.grid_columnconfigure(0, weight=1); wrap.grid_columnconfigure(1, weight=1); wrap.grid_columnconfigure(2, weight=2); wrap.grid_rowconfigure(0, weight=1)
    server_card = self.card(wrap); server_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8); server_card.grid_columnconfigure(0, weight=1); server_card.grid_rowconfigure(2, weight=1)
    channel_card = self.card(wrap); channel_card.grid(row=0, column=1, sticky="nsew", padx=8, pady=8); channel_card.grid_columnconfigure(0, weight=1); channel_card.grid_rowconfigure(2, weight=1)
    detail_card = self.card(wrap); detail_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8); detail_card.grid_columnconfigure(0, weight=1); detail_card.grid_rowconfigure(3, weight=1)
    ctk.CTkLabel(server_card, text="Servers", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.server_search = self.style_entry(server_card, "Search server or ID"); self.server_search.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8)); self.server_search.bind("<KeyRelease>", lambda e: self.render_server_browser())
    self.server_scroll = ctk.CTkScrollableFrame(server_card, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1); self.server_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
    ctk.CTkLabel(channel_card, text="Channels in selected server", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.server_channel_search = self.style_entry(channel_card, "Search channel, category or ID"); self.server_channel_search.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8)); self.server_channel_search.bind("<KeyRelease>", lambda e: self.render_server_channels())
    self.server_channel_scroll = ctk.CTkScrollableFrame(channel_card, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1); self.server_channel_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
    self.server_detail_title = ctk.CTkLabel(detail_card, text="Choose a server", text_color=PINK_2, font=("Segoe UI", 18, "bold"), anchor="w"); self.server_detail_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
    top_actions = ctk.CTkFrame(detail_card, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1); top_actions.grid(row=1, column=0, sticky="ew", padx=16, pady=6); top_actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(top_actions, "Server info", self.server_info, width=120).grid(row=0, column=0, sticky="ew", padx=6, pady=8)
    self.style_button(top_actions, "Roles", self.roles_info, width=120).grid(row=0, column=1, sticky="ew", padx=6, pady=8)
    self.style_button(top_actions, "Channel info", self.channel_info, width=120).grid(row=0, column=2, sticky="ew", padx=6, pady=8)
    msg_actions = ctk.CTkFrame(detail_card, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1); msg_actions.grid(row=2, column=0, sticky="ew", padx=16, pady=6); msg_actions.grid_columnconfigure(0, weight=1)
    self.server_history_limit = self.style_option(msg_actions, V23_LIMITS); self.server_history_limit.set("100"); self.server_history_limit.grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=(12, 6))
    self.style_button(msg_actions, "Read messages", self.channel_history, width=130).grid(row=0, column=1, padx=6, pady=(12, 6))
    self.server_message_text = ctk.CTkTextbox(msg_actions, height=64, fg_color=CARD_2, text_color=TEXT, border_color=PINK_DARK, border_width=1); self.server_message_text.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=6)
    self.server_file_path = self.style_entry(msg_actions, "Optional file path"); self.server_file_path.grid(row=2, column=0, sticky="ew", padx=(12, 6), pady=(6, 10))
    self.style_button(msg_actions, "Choose file", self.choose_server_file, width=120).grid(row=2, column=1, padx=6, pady=(6, 10))
    self.style_button(msg_actions, "Send", self.send_server_message, width=120).grid(row=2, column=2, padx=(6, 12), pady=(6, 10))
    _v25_make_emoji_row(self, msg_actions, lambda e: self.server_message_text.insert("insert", e))
    self.info_output = ctk.CTkTextbox(detail_card, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1); self.info_output.grid(row=3, column=0, sticky="nsew", padx=16, pady=(8, 16))
    self.info_guild = self.style_option(detail_card, ["No servers loaded"]); self.info_channel = self.style_option(detail_card, ["No channels loaded"])
    self.user_info_id = self.style_entry(detail_card, "User ID"); self.member_search = self.style_entry(detail_card, "Member search")
    self.selected_server_id = ""; self.selected_server_channel_id = ""


def _v25_render_server_browser(self):
    if not hasattr(self, "server_scroll"):
        return
    for w in self.server_scroll.winfo_children(): w.destroy()
    q = self.server_search.get().lower().strip() if hasattr(self, "server_search") else ""
    items = [g for g in self.guilds if not q or q in g.get("name", "").lower() or q in str(g.get("id", ""))]
    if not items:
        ctk.CTkLabel(self.server_scroll, text="No servers found.", text_color=MUTED).pack(padx=10, pady=10); return
    for g in items:
        row = ctk.CTkFrame(self.server_scroll, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1); row.pack(fill="x", padx=8, pady=6)
        icon = ctk.CTkLabel(row, text="🏠", width=52, height=52, font=("Segoe UI", 22), text_color=PINK); icon.pack(side="left", padx=8, pady=8); self.load_avatar_async(g.get("icon"), icon, 52)
        info = f"{g.get('name')}\nMembers: {g.get('members', 0)}\nID: {g.get('id')}"
        ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        self.style_button(row, "Select", lambda gg=g: self.select_server_browser(gg), width=82).pack(side="right", padx=8, pady=8)


def _v25_select_server_browser(self, guild):
    self.selected_server_id = str(guild.get("id")); label = f"{guild.get('name')} | {guild.get('id')}"
    try: self.info_guild.set(label)
    except Exception: pass
    self.server_detail_title.configure(text=label); self.selected_server_channel_id = ""; self.render_server_channels()
    fut = self.worker.run_coro(self.worker.get_server_info(self.selected_server_id))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


def _v25_render_server_channels(self):
    if not hasattr(self, "server_channel_scroll"): return
    for w in self.server_channel_scroll.winfo_children(): w.destroy()
    gid = getattr(self, "selected_server_id", "")
    if not gid and self.guilds:
        gid = str(self.guilds[0].get("id")); self.selected_server_id = gid
    q = self.server_channel_search.get().lower().strip() if hasattr(self, "server_channel_search") else ""
    items = [c for c in self.channels if str(c.get("guild_id")) == str(gid)]
    if q: items = [c for c in items if q in c.get("name", "").lower() or q in c.get("category", "").lower() or q in str(c.get("id", ""))]
    if not items:
        ctk.CTkLabel(self.server_channel_scroll, text="No text channels for selected server.", text_color=MUTED).pack(padx=10, pady=10); return
    last_cat = None
    for c in items:
        cat = c.get("category") or "No category"
        if cat != last_cat:
            last_cat = cat; ctk.CTkLabel(self.server_channel_scroll, text=f"● {cat}", text_color=GOOD, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 2))
        row = ctk.CTkFrame(self.server_channel_scroll, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1); row.pack(fill="x", padx=8, pady=5)
        ctk.CTkLabel(row, text="#", width=42, height=42, font=("Segoe UI", 22, "bold"), text_color=PINK).pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(row, text=f"{c.get('name')}\nID: {c.get('id')}", text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        self.style_button(row, "Open", lambda cc=c: self.select_server_channel(cc), width=82).pack(side="right", padx=8, pady=8)


def _v25_select_server_channel(self, channel):
    self.selected_server_channel_id = str(channel.get("id")); label = f"{channel.get('full_name') or channel.get('name')} | {channel.get('id')}"
    try: self.info_channel.set(label)
    except Exception: pass
    self.info_output.delete("1.0", "end"); self.info_output.insert("end", f"Selected channel:\n{channel.get('full_name') or channel.get('name')}\nCategory: {channel.get('category')}\nChannel ID: {channel.get('id')}\nServer ID: {channel.get('guild_id')}")


def _v25_choose_dm_file(self):
    path = filedialog.askopenfilename(filetypes=[("All files", "*.*")])
    if path: self.dm_file_path.delete(0, "end"); self.dm_file_path.insert(0, path)


def _v25_choose_server_file(self):
    path = filedialog.askopenfilename(filetypes=[("All files", "*.*")])
    if path: self.server_file_path.delete(0, "end"); self.server_file_path.insert(0, path)


def _v25_send_dm(self):
    uid = self.dm_target.get().strip(); text = self.dm_text.get("1.0", "end").strip(); file_path = self.dm_file_path.get().strip() if hasattr(self, "dm_file_path") else ""
    if not uid: return self.log("No DM user selected")
    fut = self.worker.run_coro(self.worker.send_dm(uid, text, file_path))
    if fut:
        def done(f):
            if f.exception(): self.log(f"DM error: {f.exception()}")
            else:
                self.after(0, lambda: self.dm_text.delete("1.0", "end")); self.after(0, self.load_dm_history); self.log("DM sent")
        fut.add_done_callback(done)


def _v25_send_server_message(self):
    cid = getattr(self, "selected_server_channel_id", "") or self.selected_value_id(self.info_channel.get())
    text = self.server_message_text.get("1.0", "end").strip(); file_path = self.server_file_path.get().strip() if hasattr(self, "server_file_path") else ""
    if not cid: return self.log("No server channel selected")
    fut = self.worker.run_coro(self.worker.send_channel_message(cid, text, file_path))
    if fut:
        def done(f):
            if f.exception(): self.log(f"Channel send error: {f.exception()}")
            else:
                self.after(0, lambda: self.server_message_text.delete("1.0", "end")); self.after(0, self.channel_history); self.log("Channel message sent")
        fut.add_done_callback(done)


def _v25_load_dm_history(self):
    uid = self.dm_target.get().strip()
    if not uid: return self.log("No user ID selected")
    limit = self.dm_history_limit.get() if hasattr(self, "dm_history_limit") else "200"
    fut = self.worker.run_coro(self.worker.get_dm_history(uid, limit))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.dm_history, f)))


def _v25_channel_history(self):
    cid = getattr(self, "selected_server_channel_id", "") or self.selected_value_id(self.info_channel.get())
    if not cid: return self.log("No server channel selected")
    limit = self.server_history_limit.get() if hasattr(self, "server_history_limit") else "100"
    fut = self.worker.run_coro(self.worker.get_channel_history(cid, limit))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


def _v25_server_info(self):
    gid = getattr(self, "selected_server_id", "") or self.selected_value_id(self.info_guild.get())
    if not gid: return self.log("No server selected")
    fut = self.worker.run_coro(self.worker.get_server_info(gid))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


def _v25_roles_info(self):
    gid = getattr(self, "selected_server_id", "") or self.selected_value_id(self.info_guild.get())
    if not gid: return self.log("No server selected")
    fut = self.worker.run_coro(self.worker.get_roles(gid))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


def _v25_channel_info(self):
    cid = getattr(self, "selected_server_channel_id", "") or self.selected_value_id(self.info_channel.get())
    if not cid: return self.log("No server channel selected")
    fut = self.worker.run_coro(self.worker.get_channel_info(cid))
    if fut: fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


_old_v25_update_lists = App.update_lists

def _v25_update_lists(self, guilds, channels, dms, admin_guilds, roles_by_guild=None, members_by_guild=None, categories_by_guild=None):
    _old_v25_update_lists(self, guilds, channels, dms, admin_guilds, roles_by_guild, members_by_guild, categories_by_guild)
    if hasattr(self, "server_scroll"):
        if not getattr(self, "selected_server_id", "") and guilds:
            self.selected_server_id = str(guilds[0].get("id"))
            try: self.server_detail_title.configure(text=f"{guilds[0].get('name')} | {guilds[0].get('id')}")
            except Exception: pass
        self.render_server_browser(); self.render_server_channels()


App.build_dm_tab = _v25_build_dm_tab
App.build_servers_tab = _v25_build_servers_tab
App.render_server_browser = _v25_render_server_browser
App.select_server_browser = _v25_select_server_browser
App.render_server_channels = _v25_render_server_channels
App.select_server_channel = _v25_select_server_channel
App.choose_dm_file = _v25_choose_dm_file
App.choose_server_file = _v25_choose_server_file
App.send_dm = _v25_send_dm
App.send_server_message = _v25_send_server_message
App.load_dm_history = _v25_load_dm_history
App.channel_history = _v25_channel_history
App.server_info = _v25_server_info
App.roles_info = _v25_roles_info
App.channel_info = _v25_channel_info
App.update_lists = _v25_update_lists


def _v25_bind_click_recursive(widget, callback):
    """Make a whole card clickable, not only the small Select/Open button."""
    try:
        widget.configure(cursor="hand2")
    except Exception:
        pass
    try:
        widget.bind("<Button-1>", lambda _e: callback())
    except Exception:
        pass
    for child in widget.winfo_children():
        _v25_bind_click_recursive(child, callback)


def _v25_render_server_browser(self):
    if not hasattr(self, "server_scroll"):
        return
    for w in self.server_scroll.winfo_children():
        w.destroy()
    q = self.server_search.get().lower().strip() if hasattr(self, "server_search") else ""
    items = [g for g in self.guilds if not q or q in g.get("name", "").lower() or q in str(g.get("id", ""))]
    if not items:
        ctk.CTkLabel(self.server_scroll, text="No servers found.", text_color=MUTED).pack(padx=10, pady=10)
        return
    for g in items:
        selected = str(g.get("id")) == str(getattr(self, "selected_server_id", ""))
        row = ctk.CTkFrame(
            self.server_scroll,
            fg_color=PINK_DARK if selected else CARD_2,
            corner_radius=14,
            border_color=PINK if selected else PINK_DARK,
            border_width=2 if selected else 1,
        )
        row.pack(fill="x", padx=8, pady=6)
        icon = ctk.CTkLabel(row, text="🏠", width=56, height=56, font=("Segoe UI", 24), text_color=PINK)
        icon.pack(side="left", padx=8, pady=8)
        self.load_avatar_async(g.get("icon"), icon, 56)
        info = (
            f"{g.get('name')}\n"
            f"Members: {g.get('members', 0)}  |  Channels: {len([c for c in self.channels if str(c.get('guild_id')) == str(g.get('id'))])}\n"
            f"ID: {g.get('id')}"
        )
        ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        btn = self.style_button(row, "Selected" if selected else "Select", lambda gg=g: self.select_server_browser(gg), width=86)
        btn.pack(side="right", padx=8, pady=8)
        _v25_bind_click_recursive(row, lambda gg=g: self.select_server_browser(gg))


def _v25_select_server_browser(self, guild):
    self.selected_server_id = str(guild.get("id"))
    label = f"{guild.get('name')} | {guild.get('id')}"
    try:
        self.info_guild.set(label)
    except Exception:
        pass
    self.server_detail_title.configure(text=label)
    self.selected_server_channel_id = ""
    self.render_server_browser()
    self.render_server_channels()
    fut = self.worker.run_coro(self.worker.get_server_info(self.selected_server_id))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.info_output, f)))


def _v25_render_server_channels(self):
    if not hasattr(self, "server_channel_scroll"):
        return
    for w in self.server_channel_scroll.winfo_children():
        w.destroy()
    gid = getattr(self, "selected_server_id", "")
    if not gid and self.guilds:
        gid = str(self.guilds[0].get("id")); self.selected_server_id = gid
    q = self.server_channel_search.get().lower().strip() if hasattr(self, "server_channel_search") else ""
    items = [c for c in self.channels if str(c.get("guild_id")) == str(gid)]
    if q:
        items = [c for c in items if q in c.get("name", "").lower() or q in c.get("category", "").lower() or q in str(c.get("id", ""))]
    if not items:
        ctk.CTkLabel(self.server_channel_scroll, text="No text channels for selected server.", text_color=MUTED).pack(padx=10, pady=10)
        return
    last_cat = None
    for c in items:
        cat = c.get("category") or "No category"
        if cat != last_cat:
            last_cat = cat
            ctk.CTkLabel(self.server_channel_scroll, text=f"● {cat}", text_color=GOOD, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 2))
        selected = str(c.get("id")) == str(getattr(self, "selected_server_channel_id", ""))
        row = ctk.CTkFrame(
            self.server_channel_scroll,
            fg_color=PINK_DARK if selected else CARD_2,
            corner_radius=14,
            border_color=PINK if selected else PINK_DARK,
            border_width=2 if selected else 1,
        )
        row.pack(fill="x", padx=8, pady=5)
        ctk.CTkLabel(row, text="#", width=42, height=42, font=("Segoe UI", 22, "bold"), text_color=PINK).pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(row, text=f"{c.get('name')}\nCategory: {cat}\nID: {c.get('id')}", text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        self.style_button(row, "Open", lambda cc=c: self.select_server_channel(cc), width=82).pack(side="right", padx=8, pady=8)
        _v25_bind_click_recursive(row, lambda cc=c: self.select_server_channel(cc))


def _v25_select_server_channel(self, channel):
    self.selected_server_channel_id = str(channel.get("id"))
    label = f"{channel.get('full_name') or channel.get('name')} | {channel.get('id')}"
    try:
        self.info_channel.set(label)
    except Exception:
        pass
    self.render_server_channels()
    self.info_output.delete("1.0", "end")
    self.info_output.insert("end", f"Selected channel:\n{channel.get('full_name') or channel.get('name')}\nCategory: {channel.get('category')}\nChannel ID: {channel.get('id')}\nServer ID: {channel.get('guild_id')}")


def _v25_render_cleaner_targets(self):
    if not hasattr(self, "clean_target_scroll"):
        return
    for w in self.clean_target_scroll.winfo_children():
        w.destroy()
    self.clean_card_images = []
    q = self.clean_search.get().lower().strip() if hasattr(self, "clean_search") else ""
    if getattr(self, "clean_mode", "server") == "dm":
        items = [d for d in self.dms if not q or q in d.get("name", "").lower() or q in d.get("display", "").lower() or q in d.get("id", "")]
        if not items:
            ctk.CTkLabel(self.clean_target_scroll, text="No DMs found.", text_color=MUTED).pack(padx=10, pady=10)
            return
        for dm in items:
            selected = str(dm.get("id")) == str(getattr(self, "clean_selected_target", ""))
            row = ctk.CTkFrame(self.clean_target_scroll, fg_color=PINK_DARK if selected else CARD_2, corner_radius=14, border_color=PINK if selected else PINK_DARK, border_width=2 if selected else 1)
            row.pack(fill="x", padx=8, pady=6)
            avatar = ctk.CTkLabel(row, text="👤", width=54, height=54, font=("Segoe UI", 24), text_color=PINK)
            avatar.pack(side="left", padx=8, pady=8)
            self.load_avatar_async(dm.get("avatar"), avatar, 54)
            info = f"{dm.get('display') or dm.get('name')}\n{dm.get('name')}\nID: {dm.get('id')}"
            ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
            self.style_button(row, "Select", lambda d=dm: self.select_cleaner_dm(d), width=82).pack(side="right", padx=8, pady=8)
            _v25_bind_click_recursive(row, lambda d=dm: self.select_cleaner_dm(d))
    else:
        guild_map = {str(g.get("id")): g for g in self.guilds}
        grouped = []
        for c in self.channels:
            g = guild_map.get(str(c.get("guild_id")), {})
            hay = f"{g.get('name','')} {c.get('name','')} {c.get('full_name','')} {c.get('id','')} {c.get('category','')}".lower()
            if not q or q in hay:
                grouped.append((g, c))
        if not grouped:
            ctk.CTkLabel(self.clean_target_scroll, text="No server channels found.", text_color=MUTED).pack(padx=10, pady=10)
            return
        last_gid = None
        for g, c in grouped:
            gid = c.get("guild_id")
            if gid != last_gid:
                last_gid = gid
                header = ctk.CTkFrame(self.clean_target_scroll, fg_color="#08060d", corner_radius=14, border_color=PINK_DARK, border_width=1)
                header.pack(fill="x", padx=8, pady=(12, 4))
                icon = ctk.CTkLabel(header, text="🏠", width=46, height=46, font=("Segoe UI", 20), text_color=PINK)
                icon.pack(side="left", padx=8, pady=8)
                self.load_avatar_async(g.get("icon"), icon, 46)
                channel_count = len([x for x in self.channels if str(x.get("guild_id")) == str(gid)])
                ctk.CTkLabel(header, text=f"{g.get('name','Server')}\nMembers: {g.get('members',0)} | Channels: {channel_count}\nID: {g.get('id', gid)}", text_color=GOOD, justify="left", anchor="w", font=("Segoe UI", 13, "bold")).pack(side="left", fill="x", expand=True, padx=8)
            selected = str(c.get("id")) == str(getattr(self, "clean_selected_target", ""))
            row = ctk.CTkFrame(self.clean_target_scroll, fg_color=PINK_DARK if selected else CARD_2, corner_radius=14, border_color=PINK if selected else PINK_DARK, border_width=2 if selected else 1)
            row.pack(fill="x", padx=18, pady=5)
            ctk.CTkLabel(row, text="#", width=42, height=42, font=("Segoe UI", 22, "bold"), text_color=PINK).pack(side="left", padx=8, pady=8)
            info = f"{c.get('name')}\nCategory: {c.get('category','No category')}\nID: {c.get('id')}"
            ctk.CTkLabel(row, text=info, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
            self.style_button(row, "Select", lambda gg=g, cc=c: self.select_cleaner_channel(gg, cc), width=82).pack(side="right", padx=8, pady=8)
            _v25_bind_click_recursive(row, lambda gg=g, cc=c: self.select_cleaner_channel(gg, cc))


def _v25_select_cleaner_channel(self, guild, channel):
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = str(channel.get("id"))
    self.clean_detail.configure(text=f"Server: {guild.get('name')}\nChannel: {channel.get('name')}\nCategory: {channel.get('category','No category')}\nChannel ID: {channel.get('id')}\nServer ID: {guild.get('id')}")
    self.clean_icon.configure(text="#", image=None)
    self.render_cleaner_targets()


def _v25_select_cleaner_dm(self, dm):
    self.clean_selected_type = "DM User ID"
    self.clean_selected_target = str(dm.get("id"))
    self.clean_dm_user.delete(0, "end")
    self.clean_dm_user.insert(0, self.clean_selected_target)
    self.clean_detail.configure(text=f"Display: {dm.get('display') or dm.get('name')}\nUsername: {dm.get('name')}\nUser ID: {dm.get('id')}")
    self.clean_icon.configure(text="👤", image=None)
    self.load_avatar_async(dm.get("avatar"), self.clean_icon, 74)
    self.render_cleaner_targets()


App.render_server_browser = _v25_render_server_browser
App.select_server_browser = _v25_select_server_browser
App.render_server_channels = _v25_render_server_channels
App.select_server_channel = _v25_select_server_channel
App.render_cleaner_targets = _v25_render_cleaner_targets
App.select_cleaner_channel = _v25_select_cleaner_channel
App.select_cleaner_dm = _v25_select_cleaner_dm





def _v26_int(value, default=100):
    try:
        if str(value).lower().strip() == "all":
            return None
        return int(str(value).strip())
    except Exception:
        return default


def _v26_contains_any(text, words):
    if not words:
        return True
    low = (text or "").lower()
    return any(w.lower() in low for w in words if w)


async def _v26_preview_messages(self, target_type, target_id, limit=100, scope="Own messages", include_pinned=False, text_filter="", user_filter=""):
    ch = await self._resolve_target_channel(target_type, target_id)
    is_dm = target_type == "DM User ID"
    lim = _v26_int(limit, 100)
    text_filter = (text_filter or "").strip().lower()
    user_filter = (user_filter or "").strip()
    rows = []
    matched = 0
    scanned = 0
    async for msg in ch.history(limit=lim):
        scanned += 1
        if not include_pinned and getattr(msg, "pinned", False):
            continue
        if text_filter and text_filter not in (msg.content or "").lower():
            continue
        if user_filter and str(getattr(msg.author, "id", "")) != user_filter:
            continue
        if is_dm or scope == "Own messages":
            if msg.author.id != self.bot.user.id:
                continue
        elif scope == "Bot messages":
            if not getattr(msg.author, "bot", False):
                continue
        elif scope == "Matching text":
            if not text_filter:
                continue
        elif scope == "All messages (admin)":
            pass
        else:
            if msg.author.id != self.bot.user.id:
                continue
        matched += 1
        when = msg.created_at.strftime("%d.%m.%Y %H:%M") if getattr(msg, "created_at", None) else ""
        author = getattr(msg.author, "display_name", str(msg.author))
        content = (msg.content or "").replace("\n", " ")[:220]
        att = ""
        if getattr(msg, "attachments", None):
            att = f" [attachments: {len(msg.attachments)}]"
        rows.append(f"[{when}] {author} ({msg.author.id}): {content}{att}")
        if len(rows) >= 250:
            rows.append("... preview capped at 250 rows ...")
            break
    if not rows:
        return f"No matching messages found. Scanned: {scanned}"
    return f"Preview scope: {scope}\nTarget: {getattr(ch, 'name', 'DM')} | Scanned: {scanned} | Matches: {matched}\n\n" + "\n".join(rows)


async def _v26_delete_messages(self, target_type, target_id, limit=100, scope="Own messages", include_pinned=False, text_filter="", user_filter=""):
    self.clean_stop = False
    ch = await self._resolve_target_channel(target_type, target_id)
    is_dm = target_type == "DM User ID"
    if is_dm and scope != "Own messages":
        scope = "Own messages"
    if not is_dm and scope == "All messages (admin)":
        guild = getattr(ch, "guild", None)
        me = guild.get_member(self.bot.user.id) if guild else None
        perms = getattr(me, "guild_permissions", None)
        channel_perms = ch.permissions_for(me) if me and hasattr(ch, "permissions_for") else perms
        if not (getattr(channel_perms, "manage_messages", False) or getattr(channel_perms, "administrator", False)):
            raise RuntimeError("Manage Messages permission is required for deleting all messages in this channel.")
    lim = _v26_int(limit, 100)
    text_filter = (text_filter or "").strip().lower()
    user_filter = (user_filter or "").strip()
    deleted = 0
    scanned = 0
    skipped = 0
    async for msg in ch.history(limit=lim):
        if self.clean_stop:
            break
        scanned += 1
        if not include_pinned and getattr(msg, "pinned", False):
            skipped += 1
            continue
        if text_filter and text_filter not in (msg.content or "").lower():
            skipped += 1
            continue
        if user_filter and str(getattr(msg.author, "id", "")) != user_filter:
            skipped += 1
            continue
        allowed = False
        if is_dm or scope == "Own messages":
            allowed = msg.author.id == self.bot.user.id
        elif scope == "All messages (admin)":
            allowed = True
        elif scope == "Bot messages":
            allowed = bool(getattr(msg.author, "bot", False))
        elif scope == "Matching text":
            allowed = bool(text_filter)
        if not allowed:
            skipped += 1
            continue
        try:
            await msg.delete()
            deleted += 1
            await asyncio.sleep(0.45)
        except Exception as e:
            skipped += 1
            self.app.log(f"Cleaner delete failed: {e}")
    return {"deleted": deleted, "scanned": scanned, "skipped": skipped, "stopped": self.clean_stop, "scope": scope}


async def _v26_handle_message(self, message):
    try:
        if not self.bot or not self.bot.user:
            return
        is_dm = not getattr(message, "guild", None)
        is_self = getattr(message.author, "id", None) == self.bot.user.id
        include_own = bool(getattr(self, "monitor_include_own", False))
        ignore_bots = bool(getattr(self, "monitor_ignore_bots", True))
        if is_self and not include_own:
            return
        if ignore_bots and getattr(message.author, "bot", False):
            return
        if is_dm:
            if not self.monitor_dms:
                return
            if self.monitor_dm_user_ids and message.author.id not in self.monitor_dm_user_ids:
                return
        else:
            if not self.monitor_servers:
                return
            if self.monitor_channel_ids and message.channel.id not in self.monitor_channel_ids:
                return
        content_raw = message.content or ""
        watch_words = getattr(self, "monitor_keywords", []) or []
        only_keywords = bool(getattr(self, "monitor_only_keywords", False))
        matched_keyword = _v26_contains_any(content_raw, watch_words)
        if only_keywords and not matched_keyword:
            return
        author = getattr(message.author, "display_name", str(message.author))
        target = f"DM / {author}" if is_dm else f"{getattr(message.guild, 'name', 'Guild')} / #{getattr(message.channel, 'name', message.channel.id)}"
        when = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{when}] {target}\n{author} ({message.author.id}): {content_raw}\n"
        if getattr(message, "attachments", None):
            line += "Attachments: " + ", ".join(a.filename for a in message.attachments) + "\n"
        self.app.after(0, lambda l=line: self.app.monitor_log(l))
        self.app.log(f"MONITOR {target}: {author}: {content_raw}")
        content = content_raw.lower()
        if self.auto_react_enabled:
            keys = self.auto_react_keywords or watch_words
            if _v26_contains_any(content, keys):
                try:
                    await message.add_reaction(self.auto_react_emoji)
                except Exception as e:
                    self.app.log(f"Auto react failed: {e}")
        if self.auto_reply_enabled and self.auto_reply_text:
            keys = self.auto_reply_keywords or watch_words
            if _v26_contains_any(content, keys):
                now = time.time()
                key = f"{message.channel.id}:{message.author.id}"
                if now - self._reply_cooldowns.get(key, 0) >= max(3, int(self.auto_reply_cooldown or 30)):
                    self._reply_cooldowns[key] = now
                    try:
                        await message.channel.send(self.auto_reply_text)
                    except Exception as e:
                        self.app.log(f"Auto reply failed: {e}")
    except Exception as e:
        self.app.log(f"Message handler error: {e}")


DiscordWorker.preview_messages = _v26_preview_messages
DiscordWorker.delete_messages = _v26_delete_messages
DiscordWorker.handle_message = _v26_handle_message


def _v26_make_action_card(app, parent, title, subtitle, command):
    row = ctk.CTkFrame(parent, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    row.pack(fill="x", padx=8, pady=6)
    txt = ctk.CTkLabel(row, text=title, text_color=TEXT, font=("Segoe UI", 14, "bold"), anchor="w")
    txt.pack(anchor="w", padx=12, pady=(10, 0))
    sub = ctk.CTkLabel(row, text=subtitle, text_color=MUTED, anchor="w", justify="left", wraplength=320)
    sub.pack(fill="x", padx=12, pady=(2, 10))
    for w in (row, txt, sub):
        w.bind("<Button-1>", lambda _e: command())
    return row


def _v26_build_cleaner_tab(self, tab):
    self.page_title(tab, "Cleaner", "Delete and preview messages with clear scopes. Server channels and DMs are separated.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=2)
    outer.grid_columnconfigure(1, weight=2)
    outer.grid_columnconfigure(2, weight=3)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8)
    for frame in (left, mid, right):
        frame.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(3, weight=1)
    mid.grid_rowconfigure(2, weight=1)
    right.grid_rowconfigure(5, weight=1)

    ctk.CTkLabel(left, text="Target type", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    mode = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    mode.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    mode.grid_columnconfigure((0, 1), weight=1)
    self.clean_server_btn = self.style_button(mode, "Server channels", lambda: self.set_cleaner_mode("server"))
    self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=8, pady=10)
    self.clean_dm_btn = self.style_button(mode, "DM users", lambda: self.set_cleaner_mode("dm"))
    self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
    self.clean_search = self.style_entry(left, "Search selected target list")
    self.clean_search.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
    self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
    self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.clean_target_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))

    ctk.CTkLabel(mid, text="Cleanup mode", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.clean_action_list = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.clean_action_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    self.clean_scope = "Own messages"
    self.clean_scope_cards = {}
    scopes = [
        ("Own messages", "Delete only messages sent by your account. Works in DMs and server channels."),
        ("All messages (admin)", "Delete messages from any author in the selected server channel. Requires Manage Messages."),
        ("Bot messages", "Delete messages written by bots in the selected server channel."),
        ("Matching text", "Delete messages containing the filter text. In DMs this still only affects your own messages."),
    ]
    for name, sub in scopes:
        card = _v26_make_action_card(self, self.clean_action_list, name, sub, lambda n=name: self.set_cleaner_scope(n))
        self.clean_scope_cards[name] = card
    opts = ctk.CTkFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
    opts.grid_columnconfigure(1, weight=1)
    self.small_label(opts, "Scan limit", width=110).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
    self.clean_limit = self.style_option(opts, ["25", "50", "100", "200", "500", "1000", "All"])
    self.clean_limit.set("100")
    self.clean_limit.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 6))
    self.clean_include_pins = ctk.CTkSwitch(opts, text="Include pinned messages", progress_color=PINK_DARK, text_color=TEXT)
    self.clean_include_pins.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=6)
    self.small_label(opts, "Text filter", width=110).grid(row=2, column=0, sticky="w", padx=12, pady=6)
    self.clean_text_filter = self.style_entry(opts, "Optional text that must be contained")
    self.clean_text_filter.grid(row=2, column=1, sticky="ew", padx=12, pady=6)
    self.small_label(opts, "Author ID", width=110).grid(row=3, column=0, sticky="w", padx=12, pady=(6, 12))
    self.clean_author_filter = self.style_entry(opts, "Optional author ID for server channels")
    self.clean_author_filter.grid(row=3, column=1, sticky="ew", padx=12, pady=(6, 12))

    ctk.CTkLabel(right, text="Selected target", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    detail.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=74, height=74, font=("Segoe UI", 34), text_color=PINK)
    self.clean_icon.pack(side="left", padx=12, pady=12)
    self.clean_detail = ctk.CTkLabel(detail, text="Select a target card first.", text_color=TEXT, justify="left", anchor="w")
    self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)
    self.clean_dm_user = self.style_entry(right, "Manual DM user ID fallback")
    self.clean_dm_user.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
    actions.grid_columnconfigure((0,1,2,3), weight=1)
    self.style_button(actions, "Preview", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Delete selected scope", self.delete_cleaner_scope).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Stop", self.stop_cleaner).grid(row=0, column=2, sticky="ew", padx=4)
    self.style_button(actions, "Refresh", self.refresh_lists).grid(row=0, column=3, sticky="ew", padx=4)
    hint = ctk.CTkLabel(right, text="Tip: Preview first. Admin deletion is channel-only and requires Manage Messages.", text_color=MUTED, anchor="w")
    hint.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 6))
    self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.clean_output.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 16))

    self.clean_mode = "server"
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = ""
    self.clean_card_images = []
    self.set_cleaner_scope("Own messages")
    self.set_cleaner_mode("server")


def _v26_set_cleaner_scope(self, scope):
    self.clean_scope = scope
    for name, card in getattr(self, "clean_scope_cards", {}).items():
        card.configure(fg_color=PINK_DARK if name == scope else CARD_2, border_color=PINK if name == scope else PINK_DARK, border_width=2 if name == scope else 1)


def _v26_set_cleaner_mode(self, mode):
    self.clean_mode = mode
    self.clean_selected_target = ""
    self.clean_selected_type = "DM User ID" if mode == "dm" else "Server Channel"
    if hasattr(self, "clean_dm_user"):
        if mode == "dm":
            self.clean_dm_user.grid()
        else:
            self.clean_dm_user.grid_remove()
    self.clean_server_btn.configure(fg_color=PINK_DARK if mode == "server" else CARD)
    self.clean_dm_btn.configure(fg_color=PINK_DARK if mode == "dm" else CARD)
    if mode == "dm":
        self.set_cleaner_scope("Own messages")
    self.clean_detail.configure(text="Select a target card first.")
    self.clean_icon.configure(text="🧹", image=None)
    self.render_cleaner_targets()


def _v26_cleaner_common(self):
    t, target = self.cleaner_target()
    return {
        "target_type": t,
        "target_id": target,
        "limit": self.clean_limit.get(),
        "scope": getattr(self, "clean_scope", "Own messages"),
        "include_pinned": bool(self.clean_include_pins.get()) if hasattr(self, "clean_include_pins") else False,
        "text_filter": self.clean_text_filter.get().strip() if hasattr(self, "clean_text_filter") else "",
        "user_filter": self.clean_author_filter.get().strip() if hasattr(self, "clean_author_filter") else "",
    }


def _v26_preview_cleaner(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
        return
    fut = self.worker.run_coro(self.worker.preview_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.clean_output, f)))


def _v26_delete_cleaner_scope(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
        return
    msg = f"Delete messages with scope '{args['scope']}' in the selected target?\n\nAlways preview first when using admin deletion."
    if not messagebox.askyesno("Confirm cleaner", msg):
        return
    fut = self.worker.run_coro(self.worker.delete_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.clean_done(f)))


def _v26_clean_done(self, f):
    if f.exception():
        self.log(f"Cleaner error: {f.exception()}")
        self.clean_output.insert("end", f"\nError: {f.exception()}\n")
        return
    r = f.result()
    self.log(f"Cleaner done: scope={r.get('scope')} deleted={r['deleted']} scanned={r['scanned']} skipped={r.get('skipped', 0)} stopped={r['stopped']}")
    self.clean_output.insert("end", f"\nDone\nScope: {r.get('scope')}\nDeleted: {r['deleted']}\nScanned: {r['scanned']}\nSkipped: {r.get('skipped', 0)}\nStopped: {r['stopped']}\n")


App.build_cleaner_tab = _v26_build_cleaner_tab
App.set_cleaner_mode = _v26_set_cleaner_mode
App.set_cleaner_scope = _v26_set_cleaner_scope
App.cleaner_common = _v26_cleaner_common
App.preview_cleaner = _v26_preview_cleaner
App.delete_cleaner_scope = _v26_delete_cleaner_scope
App.delete_own_messages = _v26_delete_cleaner_scope
App.clean_done = _v26_clean_done


def _v26_render_monitor_cards(self):
    if not hasattr(self, "monitor_server_scroll"):
        return
    for w in self.monitor_server_scroll.winfo_children():
        w.destroy()
    for w in self.monitor_dm_scroll.winfo_children():
        w.destroy()
    q = self.monitor_target_search.get().lower().strip() if hasattr(self, "monitor_target_search") else ""
    guild_map = {str(g.get("id")): g for g in self.guilds}
    for c in self.channels:
        g = guild_map.get(str(c.get("guild_id")), {})
        hay = f"{g.get('name','')} {c.get('name','')} {c.get('category','')} {c.get('id','')}".lower()
        if q and q not in hay:
            continue
        active = int(c.get("id")) in self.worker.monitor_channel_ids
        row = ctk.CTkFrame(self.monitor_server_scroll, fg_color=PINK_DARK if active else CARD_2, corner_radius=14, border_color=PINK if active else PINK_DARK, border_width=2 if active else 1)
        row.pack(fill="x", padx=8, pady=6)
        icon = ctk.CTkLabel(row, text="#", width=42, height=42, font=("Segoe UI", 22, "bold"), text_color=PINK)
        icon.pack(side="left", padx=8, pady=8)
        txt = f"{g.get('name','Server')} / {c.get('name')}\nCategory: {c.get('category','No category')}\nID: {c.get('id')}"
        ctk.CTkLabel(row, text=txt, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        self.style_button(row, "Remove" if active else "Add", lambda cc=c: self.toggle_monitor_channel_card(cc), width=86).pack(side="right", padx=8, pady=8)
    for dm in self.dms:
        hay = f"{dm.get('display','')} {dm.get('name','')} {dm.get('id','')}".lower()
        if q and q not in hay:
            continue
        active = int(dm.get("id")) in self.worker.monitor_dm_user_ids
        row = ctk.CTkFrame(self.monitor_dm_scroll, fg_color=PINK_DARK if active else CARD_2, corner_radius=14, border_color=PINK if active else PINK_DARK, border_width=2 if active else 1)
        row.pack(fill="x", padx=8, pady=6)
        av = ctk.CTkLabel(row, text="👤", width=42, height=42, font=("Segoe UI", 20), text_color=PINK)
        av.pack(side="left", padx=8, pady=8)
        self.load_avatar_async(dm.get("avatar"), av, 42)
        txt = f"{dm.get('display') or dm.get('name')}\n{dm.get('name')}\nID: {dm.get('id')}"
        ctk.CTkLabel(row, text=txt, text_color=TEXT, justify="left", anchor="w").pack(side="left", fill="x", expand=True, padx=8)
        self.style_button(row, "Remove" if active else "Add", lambda dd=dm: self.toggle_monitor_dm_card(dd), width=86).pack(side="right", padx=8, pady=8)
    self.update_monitor_labels()


def _v26_toggle_monitor_channel_card(self, c):
    cid = int(c.get("id"))
    if cid in self.worker.monitor_channel_ids:
        self.worker.monitor_channel_ids.remove(cid)
    else:
        self.worker.monitor_channel_ids.add(cid)
    self.render_monitor_cards()


def _v26_toggle_monitor_dm_card(self, dm):
    uid = int(dm.get("id"))
    if uid in self.worker.monitor_dm_user_ids:
        self.worker.monitor_dm_user_ids.remove(uid)
    else:
        self.worker.monitor_dm_user_ids.add(uid)
    self.render_monitor_cards()


def _v26_update_monitor_labels(self):
    if hasattr(self, "monitor_label"):
        self.monitor_label.configure(text=f"Server targets: {'all channels' if not self.worker.monitor_channel_ids else str(len(self.worker.monitor_channel_ids)) + ' selected'}")
    if hasattr(self, "monitor_dm_label"):
        self.monitor_dm_label.configure(text=f"DM targets: {'all DMs' if not self.worker.monitor_dm_user_ids else str(len(self.worker.monitor_dm_user_ids)) + ' selected'}")


def _v26_build_monitor_tab(self, tab):
    self.page_title(tab, "Monitor / Auto", "Live monitor targets and automation rules. Server and DM targets are separated.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=2)
    outer.grid_columnconfigure(1, weight=2)
    outer.grid_columnconfigure(2, weight=3)
    outer.grid_rowconfigure(0, weight=1)
    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0,8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8,0), pady=8)
    for f in (left, mid, right):
        f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1); mid.grid_rowconfigure(3, weight=1); right.grid_rowconfigure(5, weight=1)

    ctk.CTkLabel(left, text="Server targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_server_switch = ctk.CTkSwitch(left, text="Enable server monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_server_switch.select(); self.monitor_server_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_target_search = self.style_entry(left, "Search channel, server, DM, or ID")
    self.monitor_target_search.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
    self.monitor_target_search.bind("<KeyRelease>", lambda _e: self.render_monitor_cards())
    self.monitor_label = ctk.CTkLabel(left, text="Server targets: all channels", text_color=MUTED, anchor="w")
    self.monitor_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_server_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.monitor_server_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))

    ctk.CTkLabel(mid, text="DM targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_dm_switch = ctk.CTkSwitch(mid, text="Enable DM monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_dm_switch.select(); self.monitor_dm_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_dm_label = ctk.CTkLabel(mid, text="DM targets: all DMs", text_color=MUTED, anchor="w")
    self.monitor_dm_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_dm_scroll = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.monitor_dm_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0,16))

    self.monitor_channel = self.style_option(right, ["No channels loaded"]); self.monitor_channel.grid_remove()
    self.monitor_dm_select = self.style_option(right, ["All DMs"]); self.monitor_dm_select.grid_remove()

    ctk.CTkLabel(right, text="Rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    rules = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    rules.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    rules.grid_columnconfigure(1, weight=1)
    self.monitor_only_keyword_switch = ctk.CTkSwitch(rules, text="Log only keyword matches", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_only_keyword_switch.grid(row=0, column=0, sticky="w", padx=12, pady=(12,6))
    self.monitor_keywords_entry = self.style_entry(rules, "Monitor keywords separated by comma")
    self.monitor_keywords_entry.grid(row=0, column=1, sticky="ew", padx=12, pady=(12,6))
    self.monitor_ignore_bots_switch = ctk.CTkSwitch(rules, text="Ignore bots", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_ignore_bots_switch.select(); self.monitor_ignore_bots_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.monitor_include_own_switch = ctk.CTkSwitch(rules, text="Include own messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_include_own_switch.grid(row=1, column=1, sticky="w", padx=12, pady=6)
    ctk.CTkFrame(rules, height=1, fg_color=PINK_DARK).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=8)
    self.reply_switch = ctk.CTkSwitch(rules, text="Auto Reply", progress_color=PINK_DARK, text_color=TEXT)
    self.reply_switch.grid(row=3, column=0, sticky="w", padx=12, pady=6)
    self.reply_keywords = self.style_entry(rules, "Reply keywords, empty = monitor keywords")
    self.reply_keywords.grid(row=3, column=1, sticky="ew", padx=12, pady=6)
    self.reply_text = self.style_entry(rules, "Reply text")
    self.reply_text.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
    self.reply_cooldown = self.style_option(rules, ["5", "10", "30", "60", "120", "300"])
    self.reply_cooldown.set("30")
    self.reply_cooldown.grid(row=5, column=0, sticky="ew", padx=12, pady=(6,12))
    self.react_switch = ctk.CTkSwitch(rules, text="Auto React", progress_color=PINK_DARK, text_color=TEXT)
    self.react_switch.grid(row=5, column=1, sticky="w", padx=12, pady=(6,12))
    reactrow = ctk.CTkFrame(right, fg_color="transparent")
    reactrow.grid(row=2, column=0, sticky="ew", padx=16, pady=(0,8))
    reactrow.grid_columnconfigure(0, weight=1)
    self.react_keywords = self.style_entry(reactrow, "React keywords, empty = monitor keywords")
    self.react_keywords.grid(row=0, column=0, sticky="ew", padx=(0,8))
    self.react_emoji = self.style_entry(reactrow, "Emoji")
    self.react_emoji.insert(0, "💖")
    self.react_emoji.grid(row=0, column=1, sticky="ew")
    quick = ctk.CTkFrame(right, fg_color="transparent")
    quick.grid(row=3, column=0, sticky="ew", padx=16, pady=4)
    for i, emoji in enumerate(["💖", "👍", "👀", "🔥", "✅", "❌", "😂", "😮"]):
        self.style_button(quick, emoji, lambda e=emoji: (self.react_emoji.delete(0, "end"), self.react_emoji.insert(0, e)), width=42).grid(row=0, column=i, padx=3, sticky="ew")
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=4, column=0, sticky="ew", padx=12, pady=8)
    actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(actions, "Apply", self.apply_auto_settings).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Clear target lists", self.clear_all_monitor_targets).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.monitor_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.monitor_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.monitor_output.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0,16))
    self.render_monitor_cards()


def _v26_apply_auto_settings(self):
    self.worker.monitor_servers = bool(self.monitor_server_switch.get())
    self.worker.monitor_dms = bool(self.monitor_dm_switch.get())
    self.worker.monitor_keywords = [x.strip() for x in self.monitor_keywords_entry.get().split(",") if x.strip()] if hasattr(self, "monitor_keywords_entry") else []
    self.worker.monitor_only_keywords = bool(self.monitor_only_keyword_switch.get()) if hasattr(self, "monitor_only_keyword_switch") else False
    self.worker.monitor_ignore_bots = bool(self.monitor_ignore_bots_switch.get()) if hasattr(self, "monitor_ignore_bots_switch") else True
    self.worker.monitor_include_own = bool(self.monitor_include_own_switch.get()) if hasattr(self, "monitor_include_own_switch") else False
    self.worker.auto_reply_enabled = bool(self.reply_switch.get())
    self.worker.auto_reply_keywords = [x.strip() for x in self.reply_keywords.get().split(",") if x.strip()]
    self.worker.auto_reply_text = self.reply_text.get().strip()
    self.worker.auto_reply_cooldown = _v26_int(self.reply_cooldown.get(), 30) or 30
    self.worker.auto_react_enabled = bool(self.react_switch.get())
    self.worker.auto_react_keywords = [x.strip() for x in self.react_keywords.get().split(",") if x.strip()]
    self.worker.auto_react_emoji = self.react_emoji.get().strip() or "💖"
    self.monitor_log("Settings applied.\n")
    self.update_monitor_labels()


def _v26_monitor_log(self, text):
    try:
        self.monitor_output.insert("end", text + ("" if text.endswith("\n") else "\n"))
        self.monitor_output.see("end")
    except Exception:
        pass


def _v26_clear_all_monitor_targets(self):
    self.worker.monitor_channel_ids.clear()
    self.worker.monitor_dm_user_ids.clear()
    self.render_monitor_cards()


App.build_monitor_tab = _v26_build_monitor_tab
App.render_monitor_cards = _v26_render_monitor_cards
App.toggle_monitor_channel_card = _v26_toggle_monitor_channel_card
App.toggle_monitor_dm_card = _v26_toggle_monitor_dm_card
App.update_monitor_labels = _v26_update_monitor_labels
App.apply_auto_settings = _v26_apply_auto_settings
App.monitor_log = _v26_monitor_log
App.clear_all_monitor_targets = _v26_clear_all_monitor_targets

_v26_old_update_lists = App.update_lists

def _v26_update_lists(self, *args, **kwargs):
    _v26_old_update_lists(self, *args, **kwargs)
    if hasattr(self, "render_monitor_cards"):
        self.render_monitor_cards()

App.update_lists = _v26_update_lists





async def _v27_delete_messages(self, target_type, target_id, limit=100, scope="Own messages", include_pinned=False, text_filter="", user_filter="", delete_limit="25"):
    self.clean_stop = False
    ch = await self._resolve_target_channel(target_type, target_id)
    is_dm = target_type == "DM User ID"
    if is_dm and scope != "Own messages":
        scope = "Own messages"

    admin_scopes = {"All messages (admin)", "Specific author (admin)", "Bot messages", "Matching text"}
    if not is_dm and scope in admin_scopes:
        guild = getattr(ch, "guild", None)
        me = guild.get_member(self.bot.user.id) if guild else None
        perms = getattr(me, "guild_permissions", None)
        channel_perms = ch.permissions_for(me) if me and hasattr(ch, "permissions_for") else perms
        if scope in {"All messages (admin)", "Specific author (admin)", "Bot messages", "Matching text"}:
            if not (getattr(channel_perms, "manage_messages", False) or getattr(channel_perms, "administrator", False)):
                raise RuntimeError("Manage Messages permission is required for this cleanup mode.")

    if scope == "Specific author (admin)" and not (user_filter or "").strip():
        raise RuntimeError("Specific author cleanup requires an Author ID.")
    if scope == "Matching text" and not (text_filter or "").strip():
        raise RuntimeError("Matching text cleanup requires a text filter.")

    scan_lim = _v26_int(limit, 100)
    max_delete = _v26_int(delete_limit, 25)
    text_filter = (text_filter or "").strip().lower()
    user_filter = (user_filter or "").strip()
    deleted = 0
    scanned = 0
    skipped = 0
    async for msg in ch.history(limit=scan_lim):
        if self.clean_stop:
            break
        scanned += 1
        if max_delete is not None and deleted >= max_delete:
            break
        if not include_pinned and getattr(msg, "pinned", False):
            skipped += 1
            continue
        if text_filter and text_filter not in (msg.content or "").lower():
            skipped += 1
            continue
        if user_filter and str(getattr(msg.author, "id", "")) != user_filter:
            skipped += 1
            continue

        allowed = False
        if is_dm or scope == "Own messages":
            allowed = msg.author.id == self.bot.user.id
        elif scope == "All messages (admin)":
            allowed = True
        elif scope == "Specific author (admin)":
            allowed = bool(user_filter) and str(getattr(msg.author, "id", "")) == user_filter
        elif scope == "Bot messages":
            allowed = bool(getattr(msg.author, "bot", False))
        elif scope == "Matching text":
            allowed = bool(text_filter)
        if not allowed:
            skipped += 1
            continue
        try:
            await msg.delete()
            deleted += 1
            await asyncio.sleep(0.45)
        except Exception as e:
            skipped += 1
            self.app.log(f"Cleaner delete failed: {e}")
    return {"deleted": deleted, "scanned": scanned, "skipped": skipped, "stopped": self.clean_stop, "scope": scope, "delete_limit": delete_limit}


DiscordWorker.delete_messages = _v27_delete_messages


def _v27_make_mode_card(app, parent, title, subtitle, command):
    row = ctk.CTkFrame(parent, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    row.pack(fill="x", padx=8, pady=5)
    txt = ctk.CTkLabel(row, text=title, text_color=TEXT, font=("Segoe UI", 13, "bold"), anchor="w")
    txt.pack(anchor="w", padx=12, pady=(9, 0))
    sub = ctk.CTkLabel(row, text=subtitle, text_color=MUTED, anchor="w", justify="left", wraplength=360)
    sub.pack(fill="x", padx=12, pady=(2, 9))
    for w in (row, txt, sub):
        w.bind("<Button-1>", lambda _e: command())
    return row


def _v27_copy_cleaner_output(self):
    try:
        text = self.clean_output.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.clean_output.insert("end", "\nCopied output to clipboard.\n")
    except Exception as e:
        self.clean_output.insert("end", f"\nCopy failed: {e}\n")


def _v27_build_cleaner_tab(self, tab):
    self.page_title(tab, "Cleaner", "Preview and delete messages with clear scopes. Server channels and DMs are separated.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=3)
    outer.grid_columnconfigure(1, weight=3)
    outer.grid_columnconfigure(2, weight=4)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8)
    for frame in (left, mid, right):
        frame.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(3, weight=1)
    mid.grid_rowconfigure(1, weight=1)
    right.grid_rowconfigure(5, weight=1)

    ctk.CTkLabel(left, text="1. Target", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    mode = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    mode.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    mode.grid_columnconfigure((0, 1), weight=1)
    self.clean_server_btn = self.style_button(mode, "Server channels", lambda: self.set_cleaner_mode("server"))
    self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=8, pady=10)
    self.clean_dm_btn = self.style_button(mode, "DM users", lambda: self.set_cleaner_mode("dm"))
    self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
    self.clean_search = self.style_entry(left, "Search target by name or ID")
    self.clean_search.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
    self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
    self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.clean_target_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))

    ctk.CTkLabel(mid, text="2. Cleanup mode", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.clean_action_list = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.clean_action_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    self.clean_scope = "Own messages"
    self.clean_scope_cards = {}
    scopes = [
        ("Own messages", "Delete only messages sent by your account. Works in DMs and server channels."),
        ("All messages (admin)", "Delete messages from any author in the selected server channel. Requires Manage Messages."),
        ("Specific author (admin)", "Delete messages from one user by Author ID in a server channel. Requires Manage Messages."),
        ("Bot messages", "Delete messages written by bots in the selected server channel."),
        ("Matching text", "Delete messages containing the text filter. In DMs this still only affects your own messages."),
    ]
    for name, sub in scopes:
        card = _v27_make_mode_card(self, self.clean_action_list, name, sub, lambda n=name: self.set_cleaner_scope(n))
        self.clean_scope_cards[name] = card

    opts = ctk.CTkFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
    opts.grid_columnconfigure(1, weight=1)
    opts.grid_columnconfigure(3, weight=1)
    self.small_label(opts, "Scan", width=90).grid(row=0, column=0, sticky="w", padx=(12, 4), pady=(12, 6))
    self.clean_limit = self.style_option(opts, ["25", "50", "100", "200", "500", "1000", "All"])
    self.clean_limit.set("100")
    self.clean_limit.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(12, 6))
    self.small_label(opts, "Delete max", width=90).grid(row=0, column=2, sticky="w", padx=(6, 4), pady=(12, 6))
    self.clean_delete_amount = self.style_option(opts, ["1", "5", "10", "25", "50", "100", "250", "500", "All"])
    self.clean_delete_amount.set("25")
    self.clean_delete_amount.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=(12, 6))
    helper = ctk.CTkLabel(opts, text="Scan = how many recent messages are checked. Delete max = how many matching messages will be deleted.", text_color=MUTED, anchor="w", justify="left", wraplength=480)
    helper.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 6))
    self.clean_include_pins = ctk.CTkSwitch(opts, text="Include pinned messages", progress_color=PINK_DARK, text_color=TEXT)
    self.clean_include_pins.grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=6)
    self.small_label(opts, "Text", width=90).grid(row=3, column=0, sticky="w", padx=(12, 4), pady=6)
    self.clean_text_filter = self.style_entry(opts, "Only messages containing this text")
    self.clean_text_filter.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(0, 12), pady=6)
    self.small_label(opts, "Author ID", width=90).grid(row=4, column=0, sticky="w", padx=(12, 4), pady=(6, 12))
    self.clean_author_filter = self.style_entry(opts, "For 'Specific author' or optional server filter")
    self.clean_author_filter.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(0, 12), pady=(6, 12))

    ctk.CTkLabel(right, text="3. Preview / Delete", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    detail.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=74, height=74, font=("Segoe UI", 34), text_color=PINK)
    self.clean_icon.pack(side="left", padx=12, pady=12)
    self.clean_detail = ctk.CTkLabel(detail, text="Select a target card first.", text_color=TEXT, justify="left", anchor="w", wraplength=520)
    self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)
    self.clean_dm_user = self.style_entry(right, "Manual DM user ID fallback")
    self.clean_dm_user.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
    actions.grid_columnconfigure((0,1,2,3), weight=1)
    self.style_button(actions, "Preview matches", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Delete matches", self.delete_cleaner_scope).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.clean_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.style_button(actions, "Copy output", lambda: _v27_copy_cleaner_output(self)).grid(row=0, column=3, sticky="ew", padx=4)
    hint = ctk.CTkLabel(right, text="Use Preview first. Admin cleanup is server-channel only and requires Manage Messages.", text_color=MUTED, anchor="w", wraplength=620)
    hint.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 6))
    self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.clean_output.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 16))

    self.clean_mode = "server"
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = ""
    self.clean_card_images = []
    self.set_cleaner_scope("Own messages")
    self.set_cleaner_mode("server")


def _v27_set_cleaner_scope(self, scope):
    self.clean_scope = scope
    for name, card in getattr(self, "clean_scope_cards", {}).items():
        card.configure(fg_color=PINK_DARK if name == scope else CARD_2, border_color=PINK if name == scope else PINK_DARK, border_width=2 if name == scope else 1)
    if hasattr(self, "clean_author_filter"):
        try:
            if scope == "Specific author (admin)":
                self.clean_author_filter.configure(placeholder_text="Required: author/user ID to delete")
            else:
                self.clean_author_filter.configure(placeholder_text="Optional author ID for server channels")
        except Exception:
            pass


def _v27_cleaner_common(self):
    t, target = self.cleaner_target()
    return {
        "target_type": t,
        "target_id": target,
        "limit": self.clean_limit.get(),
        "scope": getattr(self, "clean_scope", "Own messages"),
        "include_pinned": bool(self.clean_include_pins.get()) if hasattr(self, "clean_include_pins") else False,
        "text_filter": self.clean_text_filter.get().strip() if hasattr(self, "clean_text_filter") else "",
        "user_filter": self.clean_author_filter.get().strip() if hasattr(self, "clean_author_filter") else "",
        "delete_limit": self.clean_delete_amount.get() if hasattr(self, "clean_delete_amount") else "25",
    }


def _v27_delete_cleaner_scope(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
        return
    scope = args.get("scope")
    if args.get("target_type") == "DM User ID" and scope != "Own messages":
        scope = "Own messages"
        args["scope"] = scope
    if scope == "Specific author (admin)" and not args.get("user_filter"):
        self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Specific author cleanup needs an Author ID.")
        return
    msg = (
        f"Delete matching messages?\n\n"
        f"Scope: {args['scope']}\n"
        f"Scan: {args['limit']} recent messages\n"
        f"Delete max: {args['delete_limit']}\n\n"
        "Preview first when using admin cleanup."
    )
    if not messagebox.askyesno("Confirm cleaner", msg):
        return
    fut = self.worker.run_coro(self.worker.delete_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.clean_done(f)))


def _v27_clean_done(self, f):
    if f.exception():
        self.log(f"Cleaner error: {f.exception()}")
        self.clean_output.insert("end", f"\nError: {f.exception()}\n")
        return
    r = f.result()
    self.log(f"Cleaner done: scope={r.get('scope')} deleted={r['deleted']} scanned={r['scanned']} skipped={r.get('skipped', 0)} stopped={r['stopped']}")
    self.clean_output.insert("end", f"\nDone\nScope: {r.get('scope')}\nDelete max: {r.get('delete_limit')}\nDeleted: {r['deleted']}\nScanned: {r['scanned']}\nSkipped: {r.get('skipped', 0)}\nStopped: {r['stopped']}\n")


App.build_cleaner_tab = _v27_build_cleaner_tab
App.set_cleaner_scope = _v27_set_cleaner_scope
App.cleaner_common = _v27_cleaner_common
App.delete_cleaner_scope = _v27_delete_cleaner_scope
App.delete_own_messages = _v27_delete_cleaner_scope
App.clean_done = _v27_clean_done


def _v27_build_monitor_tab(self, tab):
    self.page_title(tab, "Monitor / Auto", "Watch server channels or DMs and optionally auto-reply or auto-react when keywords match.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=2)
    outer.grid_columnconfigure(1, weight=2)
    outer.grid_columnconfigure(2, weight=4)
    outer.grid_rowconfigure(0, weight=1)
    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0,8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8,0), pady=8)
    for f in (left, mid, right):
        f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1); mid.grid_rowconfigure(3, weight=1); right.grid_rowconfigure(4, weight=1)

    ctk.CTkLabel(left, text="Server targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_server_switch = ctk.CTkSwitch(left, text="Monitor server channels", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_server_switch.select(); self.monitor_server_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_target_search = self.style_entry(left, "Search channel, server, or ID")
    self.monitor_target_search.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
    self.monitor_target_search.bind("<KeyRelease>", lambda _e: self.render_monitor_cards())
    self.monitor_label = ctk.CTkLabel(left, text="Server targets: all channels", text_color=MUTED, anchor="w")
    self.monitor_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_server_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.monitor_server_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))

    ctk.CTkLabel(mid, text="DM targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_dm_switch = ctk.CTkSwitch(mid, text="Monitor DMs", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_dm_switch.select(); self.monitor_dm_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_dm_label = ctk.CTkLabel(mid, text="DM targets: all DMs", text_color=MUTED, anchor="w")
    self.monitor_dm_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_dm_scroll = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.monitor_dm_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0,16))

    self.monitor_channel = self.style_option(right, ["No channels loaded"]); self.monitor_channel.grid_remove()
    self.monitor_dm_select = self.style_option(right, ["All DMs"]); self.monitor_dm_select.grid_remove()

    ctk.CTkLabel(right, text="Rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    rules = ctk.CTkScrollableFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    rules.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    rules.grid_columnconfigure(0, weight=1)

    monitor_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    monitor_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,6))
    monitor_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(monitor_box, text="Logging filter", text_color=PINK_2, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.monitor_only_keyword_switch = ctk.CTkSwitch(monitor_box, text="Only log keyword matches", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_only_keyword_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.monitor_keywords_entry = self.style_entry(monitor_box, "keywords, e.g. help,error,ticket")
    self.monitor_keywords_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(monitor_box, text="Leave keywords empty to log every message in enabled targets.", text_color=MUTED, anchor="w", justify="left", wraplength=640).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,8))
    self.monitor_ignore_bots_switch = ctk.CTkSwitch(monitor_box, text="Ignore bots", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_ignore_bots_switch.select(); self.monitor_ignore_bots_switch.grid(row=3, column=0, sticky="w", padx=12, pady=(0,10))
    self.monitor_include_own_switch = ctk.CTkSwitch(monitor_box, text="Include own messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_include_own_switch.grid(row=3, column=1, sticky="w", padx=12, pady=(0,10))

    reply_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    reply_box.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
    reply_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(reply_box, text="Auto reply", text_color=PINK_2, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.reply_switch = ctk.CTkSwitch(reply_box, text="Enable auto reply", progress_color=PINK_DARK, text_color=TEXT)
    self.reply_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.reply_keywords = self.style_entry(reply_box, "reply keywords, e.g. help,support")
    self.reply_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.reply_text = self.style_entry(reply_box, "message to send as auto reply")
    self.reply_text.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(reply_box, text="Auto reply sends the reply text when a target message contains one of the reply keywords. If reply keywords are empty, it uses the logging keywords.", text_color=MUTED, anchor="w", justify="left", wraplength=720).grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,6))
    cool_frame = ctk.CTkFrame(reply_box, fg_color="transparent")
    cool_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,10))
    cool_frame.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(cool_frame, text="Cooldown seconds", text_color=TEXT).grid(row=0, column=0, sticky="w", padx=(0,10))
    self.reply_cooldown = self.style_option(cool_frame, ["5", "10", "30", "60", "120", "300"])
    self.reply_cooldown.set("30")
    self.reply_cooldown.grid(row=0, column=1, sticky="ew")
    ctk.CTkLabel(cool_frame, text="Minimum time before replying again to the same user in the same channel.", text_color=MUTED, anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4,0))

    react_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    react_box.grid(row=2, column=0, sticky="ew", padx=10, pady=(6,10))
    react_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(react_box, text="Auto react", text_color=PINK_2, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.react_switch = ctk.CTkSwitch(react_box, text="Enable auto react", progress_color=PINK_DARK, text_color=TEXT)
    self.react_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.react_keywords = self.style_entry(react_box, "react keywords, empty = logging keywords")
    self.react_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.react_emoji = self.style_entry(react_box, "emoji")
    self.react_emoji.insert(0, "💖")
    self.react_emoji.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
    quick = ctk.CTkFrame(react_box, fg_color="transparent")
    quick.grid(row=2, column=1, sticky="ew", padx=12, pady=6)
    for i, emoji in enumerate(["💖", "👍", "👀", "🔥", "✅", "❌", "😂", "😮"]):
        self.style_button(quick, emoji, lambda e=emoji: (self.react_emoji.delete(0, "end"), self.react_emoji.insert(0, e)), width=42).grid(row=0, column=i, padx=3, sticky="ew")

    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
    actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(actions, "Apply rules", self.apply_auto_settings).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Clear target lists", self.clear_all_monitor_targets).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.monitor_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.monitor_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.monitor_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))
    self.render_monitor_cards()


App.build_monitor_tab = _v27_build_monitor_tab


def _v28_limit_to_int(value, default=100):
    try:
        if str(value).strip().lower() == "all":
            return None
        return int(float(value))
    except Exception:
        return default

async def _v28_preview_cleaner_messages(self, target_type, target_id, limit=100, scope="Own messages", include_pinned=False, text_filter="", user_filter="", delete_limit="25"):
    ch = await self._resolve_target_channel(target_type, target_id)
    is_dm = target_type == "DM User ID"
    if is_dm and scope != "Own messages":
        scope = "Own messages"
    scan_lim = _v28_limit_to_int(limit, 100)
    max_show = _v28_limit_to_int(delete_limit, 25)
    text_filter = (text_filter or "").strip().lower()
    user_filter = (user_filter or "").strip()
    lines = []
    matched = 0
    scanned = 0
    skipped = 0
    async for msg in ch.history(limit=scan_lim):
        scanned += 1
        if max_show is not None and matched >= max_show:
            break
        if not include_pinned and getattr(msg, "pinned", False):
            skipped += 1; continue
        if text_filter and text_filter not in (msg.content or "").lower():
            skipped += 1; continue
        if user_filter and str(getattr(msg.author, "id", "")) != user_filter:
            skipped += 1; continue
        allowed = False
        if is_dm or scope == "Own messages":
            allowed = msg.author.id == self.bot.user.id
        elif scope == "All messages (admin)":
            allowed = True
        elif scope == "Specific author (admin)":
            allowed = bool(user_filter) and str(getattr(msg.author, "id", "")) == user_filter
        elif scope == "Bot messages":
            allowed = bool(getattr(msg.author, "bot", False))
        elif scope == "Matching text":
            allowed = bool(text_filter)
        if not allowed:
            skipped += 1; continue
        content = (msg.content or "").replace("\n", " ")
        if len(content) > 160:
            content = content[:157] + "..."
        stamp = msg.created_at.strftime("%d.%m %H:%M") if getattr(msg, "created_at", None) else ""
        lines.append(f"[{stamp}] {getattr(msg.author, 'display_name', getattr(msg.author, 'name', 'unknown'))} ({getattr(msg.author, 'id', '')}): {content}")
        matched += 1
    head = f"Preview only - nothing deleted\nScope: {scope}\nScan limit: {limit}\nDelete max: {delete_limit}\nMatches shown: {matched}\nScanned: {scanned}\nSkipped: {skipped}\n\n"
    return head + ("\n".join(lines) if lines else "No matching messages found.")

DiscordWorker.preview_cleaner_messages = _v28_preview_cleaner_messages


def _v28_set_status(self, label, text):
    try:
        label.configure(text=text)
    except Exception:
        pass


def _v28_build_cleaner_tab(self, tab):
    self.page_title(tab, "Cleaner", "Preview and delete messages with separate scan/delete limits. Server channels and DMs are separated.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=3)
    outer.grid_columnconfigure(1, weight=3)
    outer.grid_columnconfigure(2, weight=5)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8)
    for f in (left, mid, right):
        f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1); mid.grid_rowconfigure(1, weight=1); right.grid_rowconfigure(4, weight=1)

    ctk.CTkLabel(left, text="1. Select target", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    target_mode = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    target_mode.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    target_mode.grid_columnconfigure((0,1), weight=1)
    self.clean_server_btn = self.style_button(target_mode, "Server channels", lambda: self.set_cleaner_mode("server"))
    self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=8, pady=10)
    self.clean_dm_btn = self.style_button(target_mode, "DM users", lambda: self.set_cleaner_mode("dm"))
    self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
    self.clean_search = self.style_entry(left, "Search target by name or ID")
    self.clean_search.grid(row=2, column=0, sticky="ew", padx=16, pady=(4,8))
    self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
    self.clean_target_status = ctk.CTkLabel(left, text="Choose a server channel or DM.", text_color=MUTED, anchor="w", justify="left", wraplength=360)
    self.clean_target_status.grid(row=3, column=0, sticky="ew", padx=16, pady=(0,6))
    self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.clean_target_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))

    ctk.CTkLabel(mid, text="2. What should be deleted?", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.clean_action_list = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.clean_action_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    self.clean_scope = "Own messages"
    self.clean_scope_cards = {}
    scopes = [
        ("Own messages", "Deletes only messages sent by your account. Works in DMs and server channels."),
        ("All messages (admin)", "Server only. Deletes messages from everyone. Requires Manage Messages."),
        ("Specific author (admin)", "Server only. Deletes messages from one User ID. Requires Manage Messages."),
        ("Bot messages", "Server only. Deletes bot messages in the selected channel."),
        ("Matching text", "Deletes messages containing the text filter. In DMs it still only deletes your own messages."),
    ]
    for name, sub in scopes:
        self.clean_scope_cards[name] = _v27_make_mode_card(self, self.clean_action_list, name, sub, lambda n=name: self.set_cleaner_scope(n))

    options = ctk.CTkFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    options.grid(row=2, column=0, sticky="ew", padx=16, pady=(8,16))
    options.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(options, text="Limits", text_color=GOOD, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.small_label(options, "Scan recent", width=120).grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.clean_limit = self.style_option(options, ["25","50","100","200","500","1000","All"])
    self.clean_limit.set("100")
    self.clean_limit.grid(row=1, column=1, sticky="ew", padx=(0,12), pady=6)
    self.small_label(options, "Delete max", width=120).grid(row=2, column=0, sticky="w", padx=12, pady=6)
    self.clean_delete_amount = self.style_option(options, ["1","5","10","25","50","100","250","500","All"])
    self.clean_delete_amount.set("25")
    self.clean_delete_amount.grid(row=2, column=1, sticky="ew", padx=(0,12), pady=6)
    ctk.CTkLabel(options, text="Example: Scan 100 + Delete max 10 checks the latest 100 messages and deletes up to 10 matching ones.", text_color=MUTED, justify="left", anchor="w", wraplength=390).grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,8))
    self.clean_include_pins = ctk.CTkSwitch(options, text="Include pinned messages", progress_color=PINK_DARK, text_color=TEXT)
    self.clean_include_pins.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=6)
    self.small_label(options, "Text filter", width=120).grid(row=5, column=0, sticky="w", padx=12, pady=6)
    self.clean_text_filter = self.style_entry(options, "Only messages containing this text")
    self.clean_text_filter.grid(row=5, column=1, sticky="ew", padx=(0,12), pady=6)
    self.small_label(options, "Author/User ID", width=120).grid(row=6, column=0, sticky="w", padx=12, pady=(6,12))
    self.clean_author_filter = self.style_entry(options, "Required for 'Specific author' admin mode")
    self.clean_author_filter.grid(row=6, column=1, sticky="ew", padx=(0,12), pady=(6,12))

    ctk.CTkLabel(right, text="3. Preview and run", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    detail.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=74, height=74, font=("Segoe UI", 34), text_color=PINK)
    self.clean_icon.pack(side="left", padx=12, pady=12)
    self.clean_detail = ctk.CTkLabel(detail, text="Select a target card first.", text_color=TEXT, justify="left", anchor="w", wraplength=680)
    self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)
    self.clean_dm_user = self.style_entry(right, "Optional manual DM user ID")
    self.clean_dm_user.grid(row=2, column=0, sticky="ew", padx=16, pady=(0,8))
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
    actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(actions, "Preview matching messages", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Delete preview scope", self.delete_cleaner_scope).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.clean_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.clean_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(8,16))

    self.clean_mode = "server"; self.clean_selected_type = "Server Channel"; self.clean_selected_target = ""; self.clean_card_images = []
    self.set_cleaner_scope("Own messages")
    self.set_cleaner_mode("server")


def _v28_set_cleaner_scope(self, scope):
    self.clean_scope = scope
    for name, card in getattr(self, "clean_scope_cards", {}).items():
        card.configure(fg_color=PINK_DARK if name == scope else CARD_2, border_color=PINK if name == scope else PINK_DARK, border_width=2 if name == scope else 1)
    if hasattr(self, "clean_author_filter"):
        placeholder = "Required User ID for this mode" if scope == "Specific author (admin)" else "Optional User ID filter for server modes"
        try: self.clean_author_filter.configure(placeholder_text=placeholder)
        except Exception: pass


def _v28_preview_cleaner(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        self.clean_output.delete("1.0", "end"); self.clean_output.insert("end", "Select a target first.")
        return
    fut = self.worker.run_coro(self.worker.preview_cleaner_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.show_text(self.clean_output, f)))


def _v28_set_cleaner_mode(self, mode):
    self.clean_mode = mode
    self.clean_selected_target = ""
    self.clean_selected_type = "DM User ID" if mode == "dm" else "Server Channel"
    try:
        self.clean_server_btn.configure(fg_color=PINK_DARK if mode == "server" else CARD_2)
        self.clean_dm_btn.configure(fg_color=PINK_DARK if mode == "dm" else CARD_2)
        if mode == "server":
            self.clean_dm_user.grid_remove(); self.clean_dm_user.delete(0, "end")
            self.clean_target_status.configure(text="Server mode: choose a server channel. Admin modes work only here.")
        else:
            self.clean_dm_user.grid(); self.clean_target_status.configure(text="DM mode: choose a DM user. Only your own messages can be deleted in DMs.")
    except Exception: pass
    self.render_cleaner_targets()
    try:
        self.clean_detail.configure(text="Select a target card first.")
        self.clean_icon.configure(text="🏠" if mode == "server" else "👤", image=None)
    except Exception: pass


def _v28_cleaner_common(self):
    t, target = self.cleaner_target()
    return {
        "target_type": t,
        "target_id": target,
        "limit": self.clean_limit.get() if hasattr(self, "clean_limit") else "100",
        "scope": getattr(self, "clean_scope", "Own messages"),
        "include_pinned": bool(self.clean_include_pins.get()) if hasattr(self, "clean_include_pins") else False,
        "text_filter": self.clean_text_filter.get().strip() if hasattr(self, "clean_text_filter") else "",
        "user_filter": self.clean_author_filter.get().strip() if hasattr(self, "clean_author_filter") else "",
        "delete_limit": self.clean_delete_amount.get() if hasattr(self, "clean_delete_amount") else "25",
    }


def _v28_update_reply_slider_label(self, value=None):
    try:
        val = int(float(self.reply_cooldown.get() if value is None else value))
        self.reply_cooldown_label.configure(text=f"{val} seconds")
    except Exception:
        pass


def _v28_build_monitor_tab(self, tab):
    self.page_title(tab, "Monitor / Auto", "Monitor server channels or DMs, then apply clear logging, auto-reply and auto-react rules.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=3)
    outer.grid_columnconfigure(1, weight=3)
    outer.grid_columnconfigure(2, weight=6)
    outer.grid_rowconfigure(0, weight=1)
    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0,8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8,0), pady=8)
    for f in (left, mid, right): f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1); mid.grid_rowconfigure(3, weight=1); right.grid_rowconfigure(2, weight=1); right.grid_rowconfigure(4, weight=1)

    ctk.CTkLabel(left, text="Server targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_server_switch = ctk.CTkSwitch(left, text="Monitor server channels", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_server_switch.select(); self.monitor_server_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_target_search = self.style_entry(left, "Search channel, server, DM, or ID")
    self.monitor_target_search.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
    self.monitor_target_search.bind("<KeyRelease>", lambda _e: self.render_monitor_cards())
    self.monitor_label = ctk.CTkLabel(left, text="Server targets: all channels", text_color=MUTED, anchor="w")
    self.monitor_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_server_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.monitor_server_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))

    ctk.CTkLabel(mid, text="DM targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    self.monitor_dm_switch = ctk.CTkSwitch(mid, text="Monitor DMs", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_dm_switch.select(); self.monitor_dm_switch.grid(row=1, column=0, sticky="w", padx=16, pady=4)
    self.monitor_dm_label = ctk.CTkLabel(mid, text="DM targets: all DMs", text_color=MUTED, anchor="w")
    self.monitor_dm_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0,6))
    self.monitor_dm_scroll = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.monitor_dm_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0,16))

    self.monitor_channel = self.style_option(right, ["No channels loaded"]); self.monitor_channel.grid_remove()
    self.monitor_dm_select = self.style_option(right, ["All DMs"]); self.monitor_dm_select.grid_remove()

    ctk.CTkLabel(right, text="Rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14,4))
    rules = ctk.CTkScrollableFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    rules.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    rules.grid_columnconfigure(0, weight=1)

    log_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    log_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,8)); log_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(log_box, text="A. Logging filter", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.monitor_only_keyword_switch = ctk.CTkSwitch(log_box, text="Only log keyword matches", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_only_keyword_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.monitor_keywords_entry = self.style_entry(log_box, "help,error,ticket")
    self.monitor_keywords_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(log_box, text="Keywords are comma-separated. Leave empty to log all messages from enabled targets.", text_color=MUTED, justify="left", anchor="w", wraplength=760).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,6))
    self.monitor_ignore_bots_switch = ctk.CTkSwitch(log_box, text="Ignore bot messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_ignore_bots_switch.select(); self.monitor_ignore_bots_switch.grid(row=3, column=0, sticky="w", padx=12, pady=(0,10))
    self.monitor_include_own_switch = ctk.CTkSwitch(log_box, text="Include my own messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_include_own_switch.grid(row=3, column=1, sticky="w", padx=12, pady=(0,10))

    reply_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    reply_box.grid(row=1, column=0, sticky="ew", padx=10, pady=8); reply_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(reply_box, text="B. Auto reply", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.reply_switch = ctk.CTkSwitch(reply_box, text="Enable auto reply", progress_color=PINK_DARK, text_color=TEXT)
    self.reply_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.reply_keywords = self.style_entry(reply_box, "trigger words, e.g. help,support")
    self.reply_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.reply_text = self.style_entry(reply_box, "message that should be sent automatically")
    self.reply_text.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(reply_box, text="When Auto Reply is enabled, the tool sends the reply text if a monitored message contains one trigger word. If trigger words are empty, it uses the logging keywords above.", text_color=MUTED, justify="left", anchor="w", wraplength=760).grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,6))
    cool = ctk.CTkFrame(reply_box, fg_color="transparent"); cool.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(2,10)); cool.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(cool, text="Cooldown", text_color=TEXT).grid(row=0, column=0, sticky="w", padx=(0,12))
    self.reply_cooldown = ctk.CTkSlider(cool, from_=5, to=300, number_of_steps=59, progress_color=PINK_DARK, button_color=PINK)
    self.reply_cooldown.set(30); self.reply_cooldown.grid(row=0, column=1, sticky="ew")
    self.reply_cooldown_label = ctk.CTkLabel(cool, text="30 seconds", text_color=MUTED, width=90)
    self.reply_cooldown_label.grid(row=0, column=2, sticky="e", padx=(12,0))
    self.reply_cooldown.configure(command=lambda v: _v28_update_reply_slider_label(self, v))
    ctk.CTkLabel(cool, text="Cooldown prevents repeated replies to the same user in the same channel too quickly.", text_color=MUTED, anchor="w", justify="left").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4,0))

    react_box = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=12)
    react_box.grid(row=2, column=0, sticky="ew", padx=10, pady=(8,10)); react_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(react_box, text="C. Auto react", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.react_switch = ctk.CTkSwitch(react_box, text="Enable auto react", progress_color=PINK_DARK, text_color=TEXT)
    self.react_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.react_keywords = self.style_entry(react_box, "trigger words, empty = logging keywords")
    self.react_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.react_emoji = self.style_entry(react_box, "emoji")
    self.react_emoji.insert(0, "💖"); self.react_emoji.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
    quick = ctk.CTkFrame(react_box, fg_color="transparent"); quick.grid(row=2, column=1, sticky="ew", padx=12, pady=6)
    for i, emoji in enumerate(["💖", "👍", "👀", "🔥", "✅", "❌", "😂", "😮"]):
        self.style_button(quick, emoji, lambda e=emoji: (self.react_emoji.delete(0, "end"), self.react_emoji.insert(0, e)), width=42).grid(row=0, column=i, padx=3, sticky="ew")

    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=8); actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(actions, "Apply rules", self.apply_auto_settings).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Clear selected targets", self.clear_all_monitor_targets).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.monitor_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.monitor_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.monitor_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0,16))
    self.render_monitor_cards()


def _v28_apply_auto_settings(self):
    self.worker.monitor_servers = bool(self.monitor_server_switch.get())
    self.worker.monitor_dms = bool(self.monitor_dm_switch.get())
    self.worker.monitor_keywords = [x.strip() for x in self.monitor_keywords_entry.get().split(",") if x.strip()] if hasattr(self, "monitor_keywords_entry") else []
    self.worker.monitor_only_keywords = bool(self.monitor_only_keyword_switch.get()) if hasattr(self, "monitor_only_keyword_switch") else False
    self.worker.monitor_ignore_bots = bool(self.monitor_ignore_bots_switch.get()) if hasattr(self, "monitor_ignore_bots_switch") else True
    self.worker.monitor_include_own = bool(self.monitor_include_own_switch.get()) if hasattr(self, "monitor_include_own_switch") else False
    self.worker.auto_reply_enabled = bool(self.reply_switch.get())
    self.worker.auto_reply_keywords = [x.strip() for x in self.reply_keywords.get().split(",") if x.strip()]
    self.worker.auto_reply_text = self.reply_text.get().strip()
    try:
        self.worker.auto_reply_cooldown = int(float(self.reply_cooldown.get()))
    except Exception:
        self.worker.auto_reply_cooldown = 30
    self.worker.auto_react_enabled = bool(self.react_switch.get())
    self.worker.auto_react_keywords = [x.strip() for x in self.react_keywords.get().split(",") if x.strip()]
    self.worker.auto_react_emoji = self.react_emoji.get().strip() or "💖"
    self.monitor_log(f"Rules applied. Auto reply cooldown: {self.worker.auto_reply_cooldown}s.\n")
    self.update_monitor_labels()

App.build_cleaner_tab = _v28_build_cleaner_tab
App.set_cleaner_scope = _v28_set_cleaner_scope
App.set_cleaner_mode = _v28_set_cleaner_mode
App.cleaner_common = _v28_cleaner_common
App.preview_cleaner = _v28_preview_cleaner
App.build_monitor_tab = _v28_build_monitor_tab
App.apply_auto_settings = _v28_apply_auto_settings




def _v29_copy_textbox(self, box):
    try:
        txt = box.get("1.0", "end").strip()
        self.clipboard_clear(); self.clipboard_append(txt)
        self.log("Output copied to clipboard.")
    except Exception as e:
        self.log(f"Copy failed: {e}")


def _v29_make_mode_card(app, parent, title, subtitle, command):
    row = ctk.CTkFrame(parent, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    row.pack(fill="x", padx=8, pady=6)
    title_lbl = ctk.CTkLabel(row, text=title, text_color=TEXT, font=("Segoe UI", 14, "bold"), anchor="w")
    title_lbl.pack(fill="x", padx=12, pady=(10, 1))
    sub_lbl = ctk.CTkLabel(row, text=subtitle, text_color=MUTED, anchor="w", justify="left", wraplength=360)
    sub_lbl.pack(fill="x", padx=12, pady=(0, 10))
    for w in (row, title_lbl, sub_lbl):
        w.bind("<Button-1>", lambda _e: command())
    return row


def _v29_build_cleaner_tab(self, tab):
    self.page_title(tab, "Cleaner", "Choose exactly what to delete: target, scope, scan limit, delete amount and filters.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=10)
    outer.grid_columnconfigure(0, weight=3)
    outer.grid_columnconfigure(1, weight=3)
    outer.grid_columnconfigure(2, weight=5)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=6)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=6)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=6)
    for f in (left, mid, right):
        f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1)
    mid.grid_rowconfigure(2, weight=1)
    right.grid_rowconfigure(5, weight=1)

    ctk.CTkLabel(left, text="1. Target", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    mode = ctk.CTkFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    mode.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    mode.grid_columnconfigure((0,1), weight=1)
    self.clean_server_btn = self.style_button(mode, "Server channels", lambda: self.set_cleaner_mode("server"))
    self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=8, pady=10)
    self.clean_dm_btn = self.style_button(mode, "DM users", lambda: self.set_cleaner_mode("dm"))
    self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
    self.clean_search = self.style_entry(left, "Search target by server, channel, user or ID")
    self.clean_search.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
    self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
    self.clean_target_status = ctk.CTkLabel(left, text="Server mode: pick a channel. DM mode: pick a user.", text_color=MUTED, anchor="w", justify="left", wraplength=420)
    self.clean_target_status.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 6))
    self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.clean_target_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 16))

    ctk.CTkLabel(mid, text="2. Delete rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    self.clean_action_list = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.clean_action_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    self.clean_scope = "Own messages"
    self.clean_scope_cards = {}
    scopes = [
        ("Own messages", "Deletes only messages sent by your account. Works in DMs and server channels."),
        ("All messages (admin)", "Server channel only. Deletes messages from everyone. Requires Manage Messages."),
        ("Specific author (admin)", "Server channel only. Deletes messages from one User ID. Requires Manage Messages."),
        ("Bot messages", "Server channel only. Deletes bot messages in the selected channel."),
        ("Matching text", "Deletes messages containing the text filter. In DMs this only deletes your own messages."),
    ]
    for name, desc in scopes:
        self.clean_scope_cards[name] = _v29_make_mode_card(self, self.clean_action_list, name, desc, lambda n=name: self.set_cleaner_scope(n))

    opts = ctk.CTkFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
    opts.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(opts, text="Limits and filters", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
    self.small_label(opts, "Scan recent", width=120).grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.clean_limit = self.style_option(opts, ["25", "50", "100", "200", "500", "1000", "All"])
    self.clean_limit.set("100")
    self.clean_limit.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=6)
    self.small_label(opts, "Delete max", width=120).grid(row=2, column=0, sticky="w", padx=12, pady=6)
    self.clean_delete_amount = self.style_option(opts, ["1", "5", "10", "25", "50", "100", "250", "500", "All"])
    self.clean_delete_amount.set("25")
    self.clean_delete_amount.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=6)
    ctk.CTkLabel(opts, text="Scan recent = how many messages are checked. Delete max = maximum matching messages to delete.", text_color=MUTED, anchor="w", justify="left", wraplength=410).grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
    self.clean_include_pins = ctk.CTkSwitch(opts, text="Include pinned messages", progress_color=PINK_DARK, text_color=TEXT)
    self.clean_include_pins.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=6)
    self.small_label(opts, "Text filter", width=120).grid(row=5, column=0, sticky="w", padx=12, pady=6)
    self.clean_text_filter = self.style_entry(opts, "Optional: only messages containing this text")
    self.clean_text_filter.grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=6)
    self.small_label(opts, "Author/User ID", width=120).grid(row=6, column=0, sticky="w", padx=12, pady=(6, 12))
    self.clean_author_filter = self.style_entry(opts, "Required for Specific author mode")
    self.clean_author_filter.grid(row=6, column=1, sticky="ew", padx=(0, 12), pady=(6, 12))

    ctk.CTkLabel(right, text="3. Preview and delete", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    detail.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
    self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=74, height=74, font=("Segoe UI", 34), text_color=PINK)
    self.clean_icon.pack(side="left", padx=12, pady=12)
    self.clean_detail = ctk.CTkLabel(detail, text="Select a target card first.", text_color=TEXT, justify="left", anchor="w", wraplength=720)
    self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)
    self.clean_dm_user = self.style_entry(right, "Manual DM User ID fallback - only visible in DM mode")
    self.clean_dm_user.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
    self.clean_summary = ctk.CTkLabel(right, text="Current selection will appear here.", text_color=MUTED, anchor="w", justify="left", wraplength=760)
    self.clean_summary.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 6))
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=4, column=0, sticky="ew", padx=12, pady=6)
    actions.grid_columnconfigure((0,1,2,3), weight=1)
    self.style_button(actions, "Preview matches", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Delete matches", self.delete_cleaner_scope).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.clean_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.style_button(actions, "Copy output", lambda: _v29_copy_textbox(self, self.clean_output)).grid(row=0, column=3, sticky="ew", padx=4)
    self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.clean_output.grid(row=5, column=0, sticky="nsew", padx=16, pady=(8, 16))

    self.clean_mode = "server"
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = ""
    self.clean_card_images = []
    self.set_cleaner_scope("Own messages")
    self.set_cleaner_mode("server")


def _v29_update_cleaner_summary(self):
    try:
        target = getattr(self, "clean_selected_target", "") or "no target selected"
        mode = "DM user" if getattr(self, "clean_mode", "server") == "dm" else "server channel"
        txt = f"Mode: {mode} | Scope: {getattr(self,'clean_scope','Own messages')} | Scan: {self.clean_limit.get()} | Delete max: {self.clean_delete_amount.get()} | Target: {target}"
        self.clean_summary.configure(text=txt)
    except Exception:
        pass


def _v29_set_cleaner_scope(self, scope):
    if getattr(self, "clean_mode", "server") == "dm" and scope != "Own messages":
        scope = "Own messages"
    self.clean_scope = scope
    for name, card in getattr(self, "clean_scope_cards", {}).items():
        card.configure(fg_color=PINK_DARK if name == scope else CARD_2, border_color=PINK if name == scope else PINK_DARK, border_width=2 if name == scope else 1)
    if hasattr(self, "clean_author_filter"):
        try:
            self.clean_author_filter.configure(placeholder_text="Required User ID for Specific author mode" if scope == "Specific author (admin)" else "Optional User ID filter")
        except Exception:
            pass
    _v29_update_cleaner_summary(self)


def _v29_set_cleaner_mode(self, mode):
    self.clean_mode = mode
    self.clean_selected_target = ""
    self.clean_selected_type = "DM User ID" if mode == "dm" else "Server Channel"
    try:
        self.clean_server_btn.configure(fg_color=PINK_DARK if mode == "server" else CARD_2)
        self.clean_dm_btn.configure(fg_color=PINK_DARK if mode == "dm" else CARD_2)
        if mode == "server":
            self.clean_dm_user.grid_remove(); self.clean_dm_user.delete(0, "end")
            self.clean_target_status.configure(text="Server mode: choose a channel. Admin deletion modes are available here.")
        else:
            self.clean_dm_user.grid(); self.clean_target_status.configure(text="DM mode: choose a user. Only your own messages can be deleted in DMs.")
            self.set_cleaner_scope("Own messages")
        self.clean_detail.configure(text="Select a target card first.")
        self.clean_icon.configure(text="🏠" if mode == "server" else "👤", image=None)
    except Exception:
        pass
    self.render_cleaner_targets()
    _v29_update_cleaner_summary(self)


def _v29_select_cleaner_channel(self, guild, channel):
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = str(channel.get("id"))
    self.clean_detail.configure(text=f"Server: {guild.get('name')}\nChannel: #{channel.get('name')}\nCategory: {channel.get('category','No category')}\nChannel ID: {channel.get('id')}\nServer ID: {guild.get('id')}")
    self.clean_icon.configure(text="#", image=None)
    _v29_update_cleaner_summary(self)


def _v29_select_cleaner_dm(self, dm):
    self.clean_selected_type = "DM User ID"
    self.clean_selected_target = str(dm.get("id"))
    self.clean_dm_user.delete(0, "end")
    self.clean_dm_user.insert(0, self.clean_selected_target)
    self.clean_detail.configure(text=f"Display: {dm.get('display') or dm.get('name')}\nUsername: {dm.get('name')}\nUser ID: {dm.get('id')}")
    self.clean_icon.configure(text="👤", image=None)
    self.load_avatar_async(dm.get("avatar"), self.clean_icon, 74)
    self.set_cleaner_scope("Own messages")
    _v29_update_cleaner_summary(self)


def _v29_build_monitor_tab(self, tab):
    self.page_title(tab, "Monitor / Auto", "Monitor server channels or DMs. Auto Reply and Auto React have their own clear rule cards.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=10)
    outer.grid_columnconfigure(0, weight=4)
    outer.grid_columnconfigure(1, weight=6)
    outer.grid_rowconfigure(0, weight=1)

    targets = self.card(outer); targets.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=6)
    rules = self.card(outer); rules.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=6)
    targets.grid_columnconfigure(0, weight=1); targets.grid_columnconfigure(1, weight=1); targets.grid_rowconfigure(5, weight=1)
    rules.grid_columnconfigure(0, weight=1); rules.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(targets, text="1. Monitor targets", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
    search = self.style_entry(targets, "Search server channel, DM user or ID")
    search.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=8)
    self.monitor_target_search = search
    self.monitor_target_search.bind("<KeyRelease>", lambda _e: self.render_monitor_cards())

    server_box = ctk.CTkFrame(targets, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    server_box.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=8)
    server_box.grid_columnconfigure(0, weight=1); server_box.grid_rowconfigure(3, weight=1)
    ctk.CTkLabel(server_box, text="Server channels", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
    self.monitor_server_switch = ctk.CTkSwitch(server_box, text="Enable server monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_server_switch.select(); self.monitor_server_switch.grid(row=1, column=0, sticky="w", padx=12, pady=4)
    self.monitor_label = ctk.CTkLabel(server_box, text="Server targets: all channels", text_color=MUTED, anchor="w")
    self.monitor_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
    self.monitor_server_scroll = ctk.CTkScrollableFrame(server_box, fg_color=CARD, corner_radius=12)
    self.monitor_server_scroll.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))

    dm_box = ctk.CTkFrame(targets, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    dm_box.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=8)
    dm_box.grid_columnconfigure(0, weight=1); dm_box.grid_rowconfigure(3, weight=1)
    ctk.CTkLabel(dm_box, text="DM users", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
    self.monitor_dm_switch = ctk.CTkSwitch(dm_box, text="Enable DM monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_dm_switch.select(); self.monitor_dm_switch.grid(row=1, column=0, sticky="w", padx=12, pady=4)
    self.monitor_dm_label = ctk.CTkLabel(dm_box, text="DM targets: all DMs", text_color=MUTED, anchor="w")
    self.monitor_dm_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
    self.monitor_dm_scroll = ctk.CTkScrollableFrame(dm_box, fg_color=CARD, corner_radius=12)
    self.monitor_dm_scroll.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))

    self.monitor_channel = self.style_option(rules, ["No channels loaded"]); self.monitor_channel.grid_remove()
    self.monitor_dm_select = self.style_option(rules, ["All DMs"]); self.monitor_dm_select.grid_remove()

    ctk.CTkLabel(rules, text="2. Rules", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    main = ctk.CTkScrollableFrame(rules, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    main.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 8))
    main.grid_columnconfigure(0, weight=1)

    log_box = ctk.CTkFrame(main, fg_color=CARD_2, corner_radius=14)
    log_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8)); log_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(log_box, text="A. What should be logged?", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.monitor_only_keyword_switch = ctk.CTkSwitch(log_box, text="Only log messages with these words", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_only_keyword_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.monitor_keywords_entry = self.style_entry(log_box, "keywords for logging, e.g. help,error,ticket")
    self.monitor_keywords_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(log_box, text="Leave keywords empty to log every message from enabled targets.", text_color=MUTED, anchor="w", justify="left").grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,6))
    self.monitor_ignore_bots_switch = ctk.CTkSwitch(log_box, text="Ignore bot messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_ignore_bots_switch.select(); self.monitor_ignore_bots_switch.grid(row=3, column=0, sticky="w", padx=12, pady=(0,10))
    self.monitor_include_own_switch = ctk.CTkSwitch(log_box, text="Include my own messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_include_own_switch.grid(row=3, column=1, sticky="w", padx=12, pady=(0,10))

    reply_box = ctk.CTkFrame(main, fg_color=CARD_2, corner_radius=14)
    reply_box.grid(row=1, column=0, sticky="ew", padx=10, pady=8); reply_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(reply_box, text="B. Auto Reply", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.reply_switch = ctk.CTkSwitch(reply_box, text="Send an automatic reply", progress_color=PINK_DARK, text_color=TEXT)
    self.reply_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.reply_keywords = self.style_entry(reply_box, "reply trigger words, e.g. help,support")
    self.reply_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(reply_box, text="Reply triggers: when a monitored message contains one of these words, send the reply text below. Empty = use logging keywords.", text_color=MUTED, anchor="w", justify="left", wraplength=800).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0,6))
    self.reply_text = self.style_entry(reply_box, "reply message text")
    self.reply_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
    cool = ctk.CTkFrame(reply_box, fg_color="transparent"); cool.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 10)); cool.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(cool, text="Cooldown", text_color=TEXT, width=110, anchor="w").grid(row=0, column=0, sticky="w")
    self.reply_cooldown = ctk.CTkSlider(cool, from_=5, to=300, number_of_steps=59, progress_color=PINK_DARK, button_color=PINK)
    self.reply_cooldown.set(30); self.reply_cooldown.grid(row=0, column=1, sticky="ew", padx=8)
    self.reply_cooldown_label = ctk.CTkLabel(cool, text="30 seconds", text_color=PINK_2, width=110)
    self.reply_cooldown_label.grid(row=0, column=2, sticky="e")
    self.reply_cooldown.configure(command=lambda v: _v28_update_reply_slider_label(self, v))
    ctk.CTkLabel(cool, text="Example: 30 seconds means the same user in the same channel cannot trigger another auto reply for 30 seconds.", text_color=MUTED, anchor="w", justify="left", wraplength=850).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4,0))

    react_box = ctk.CTkFrame(main, fg_color=CARD_2, corner_radius=14)
    react_box.grid(row=2, column=0, sticky="ew", padx=10, pady=8); react_box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(react_box, text="C. Auto React", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10,2))
    self.react_switch = ctk.CTkSwitch(react_box, text="Add a reaction automatically", progress_color=PINK_DARK, text_color=TEXT)
    self.react_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.react_keywords = self.style_entry(react_box, "reaction trigger words, empty = logging keywords")
    self.react_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.react_emoji = self.style_entry(react_box, "emoji")
    self.react_emoji.insert(0, "💖"); self.react_emoji.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
    quick = ctk.CTkFrame(react_box, fg_color="transparent"); quick.grid(row=2, column=1, sticky="w", padx=12, pady=6)
    for i, emoji in enumerate(["💖", "👍", "👀", "🔥", "✅", "❌", "😂", "😮"]):
        self.style_button(quick, emoji, lambda e=emoji: (self.react_emoji.delete(0, "end"), self.react_emoji.insert(0, e)), width=42).grid(row=0, column=i, padx=3)

    actions = ctk.CTkFrame(rules, fg_color="transparent")
    actions.grid(row=2, column=0, sticky="ew", padx=12, pady=8); actions.grid_columnconfigure((0,1,2), weight=1)
    self.style_button(actions, "Apply rules", self.apply_auto_settings).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Clear selected targets", self.clear_all_monitor_targets).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.monitor_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.monitor_output = ctk.CTkTextbox(rules, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.monitor_output.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))
    rules.grid_rowconfigure(3, weight=1)
    self.render_monitor_cards()


def _v29_apply_auto_settings(self):
    self.worker.monitor_servers = bool(self.monitor_server_switch.get())
    self.worker.monitor_dms = bool(self.monitor_dm_switch.get())
    self.worker.monitor_keywords = [x.strip() for x in self.monitor_keywords_entry.get().split(",") if x.strip()] if hasattr(self, "monitor_keywords_entry") else []
    self.worker.monitor_only_keywords = bool(self.monitor_only_keyword_switch.get()) if hasattr(self, "monitor_only_keyword_switch") else False
    self.worker.monitor_ignore_bots = bool(self.monitor_ignore_bots_switch.get()) if hasattr(self, "monitor_ignore_bots_switch") else True
    self.worker.monitor_include_own = bool(self.monitor_include_own_switch.get()) if hasattr(self, "monitor_include_own_switch") else False
    self.worker.auto_reply_enabled = bool(self.reply_switch.get())
    self.worker.auto_reply_keywords = [x.strip() for x in self.reply_keywords.get().split(",") if x.strip()]
    self.worker.auto_reply_text = self.reply_text.get().strip()
    try:
        self.worker.auto_reply_cooldown = int(float(self.reply_cooldown.get()))
    except Exception:
        self.worker.auto_reply_cooldown = 30
    self.worker.auto_react_enabled = bool(self.react_switch.get())
    self.worker.auto_react_keywords = [x.strip() for x in self.react_keywords.get().split(",") if x.strip()]
    self.worker.auto_react_emoji = self.react_emoji.get().strip() or "💖"
    self.monitor_log(
        "Rules applied.\n"
        f"Logging keywords: {', '.join(self.worker.monitor_keywords) or 'none / log all'}\n"
        f"Auto reply: {'on' if self.worker.auto_reply_enabled else 'off'} | Cooldown: {self.worker.auto_reply_cooldown}s\n"
        f"Auto react: {'on' if self.worker.auto_react_enabled else 'off'} | Emoji: {self.worker.auto_react_emoji}\n\n"
    )
    self.update_monitor_labels()

App.build_cleaner_tab = _v29_build_cleaner_tab
App.set_cleaner_scope = _v29_set_cleaner_scope
App.set_cleaner_mode = _v29_set_cleaner_mode
App.select_cleaner_channel = _v29_select_cleaner_channel
App.select_cleaner_dm = _v29_select_cleaner_dm
App.build_monitor_tab = _v29_build_monitor_tab
App.apply_auto_settings = _v29_apply_auto_settings




V30_EMOJIS = ["💖", "👍", "👀", "🔥", "✅", "❌", "😂", "😮", "🙏", "⭐"]


def _v30_clear(parent):
    for child in parent.winfo_children():
        child.destroy()


def _v30_bind_card(widget, command):
    try:
        widget.bind("<Button-1>", lambda _e: command())
    except Exception:
        pass
    for child in widget.winfo_children():
        _v30_bind_card(child, command)


def _v30_copy_text(app, textbox):
    try:
        app.clipboard_clear()
        app.clipboard_append(textbox.get("1.0", "end").strip())
    except Exception:
        pass


def _v30_set_output(box, text):
    try:
        box.delete("1.0", "end")
        box.insert("end", text)
    except Exception:
        pass


def _v30_status_chip(parent, text, active=False):
    return ctk.CTkLabel(
        parent,
        text=text,
        text_color=TEXT,
        fg_color=PINK_DARK if active else CARD_2,
        corner_radius=12,
        padx=10,
        pady=6,
        font=("Segoe UI", 12, "bold" if active else "normal"),
    )


def _v30_make_target_card(app, parent, title, subtitle, icon_text, active, command, avatar_url=None):
    row = ctk.CTkFrame(parent, fg_color=PINK_DARK if active else CARD_2, corner_radius=16, border_color=PINK if active else PINK_DARK, border_width=2 if active else 1)
    row.pack(fill="x", padx=8, pady=6)
    icon = ctk.CTkLabel(row, text=icon_text, width=52, height=52, font=("Segoe UI", 22, "bold"), text_color=PINK)
    icon.pack(side="left", padx=10, pady=10)
    if avatar_url:
        app.load_avatar_async(avatar_url, icon, 52)
    text = ctk.CTkLabel(row, text=f"{title}\n{subtitle}", text_color=TEXT, justify="left", anchor="w", wraplength=330)
    text.pack(side="left", fill="x", expand=True, padx=(4, 8), pady=10)
    _v30_bind_card(row, command)
    return row


def _v30_make_scope_card(app, parent, key, title, subtitle, command):
    active = getattr(app, "clean_scope", "own") == key
    row = ctk.CTkFrame(parent, fg_color=PINK_DARK if active else CARD_2, corner_radius=16, border_color=PINK if active else PINK_DARK, border_width=2 if active else 1)
    row.pack(fill="x", padx=8, pady=6)
    ctk.CTkLabel(row, text=title, text_color=TEXT, font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 2))
    ctk.CTkLabel(row, text=subtitle, text_color=MUTED, justify="left", anchor="w", wraplength=330).pack(fill="x", padx=12, pady=(0, 10))
    _v30_bind_card(row, command)
    return row



def _v30_build_cleaner_tab(self, tab):
    self.page_title(tab, "Cleaner", "Preview and delete messages with clear controls. Server channels and DMs are separated.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=2)
    outer.grid_columnconfigure(1, weight=2)
    outer.grid_columnconfigure(2, weight=3)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
    mid = self.card(outer); mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
    right = self.card(outer); right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8)
    for frame in (left, mid, right):
        frame.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(4, weight=1)
    mid.grid_rowconfigure(2, weight=1)
    right.grid_rowconfigure(4, weight=1)

    ctk.CTkLabel(left, text="1. Target", text_color=PINK_2, font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    mode_row = ctk.CTkFrame(left, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    mode_row.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
    mode_row.grid_columnconfigure((0, 1), weight=1)
    self.clean_server_btn = self.style_button(mode_row, "Server channels", lambda: self.set_cleaner_mode("server"))
    self.clean_server_btn.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
    self.clean_dm_btn = self.style_button(mode_row, "DM users", lambda: self.set_cleaner_mode("dm"))
    self.clean_dm_btn.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
    self.clean_search = self.style_entry(left, "Search server, channel, DM user or ID")
    self.clean_search.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
    self.clean_search.bind("<KeyRelease>", lambda _e: self.render_cleaner_targets())
    self.clean_target_hint = ctk.CTkLabel(left, text="Choose a target card below.", text_color=MUTED, anchor="w")
    self.clean_target_hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
    self.clean_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.clean_target_scroll.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 16))

    ctk.CTkLabel(mid, text="2. What should be deleted?", text_color=PINK_2, font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    desc = ctk.CTkLabel(mid, text="Pick one cleanup scope. DMs are limited to your own messages. Admin scopes work only in server channels with Manage Messages.", text_color=MUTED, justify="left", anchor="w", wraplength=410)
    desc.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
    self.clean_scope_scroll = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14)
    self.clean_scope_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=6)
    self.clean_options = ctk.CTkFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    self.clean_options.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 16))
    self.clean_options.grid_columnconfigure(1, weight=1)
    self.clean_options.grid_columnconfigure(3, weight=1)

    ctk.CTkLabel(right, text="3. Preview and run", text_color=PINK_2, font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    detail = ctk.CTkFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    detail.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
    self.clean_icon = ctk.CTkLabel(detail, text="🧹", width=72, height=72, text_color=PINK, font=("Segoe UI", 32))
    self.clean_icon.pack(side="left", padx=12, pady=12)
    self.clean_detail = ctk.CTkLabel(detail, text="Select a target first.", text_color=TEXT, justify="left", anchor="w", wraplength=600)
    self.clean_detail.pack(side="left", fill="x", expand=True, padx=8, pady=12)
    self.clean_summary = ctk.CTkLabel(right, text="Preview uses the same filters as Delete.", text_color=MUTED, justify="left", anchor="w", wraplength=720)
    self.clean_summary.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 6))
    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
    actions.grid_columnconfigure((0, 1, 2, 3), weight=1)
    self.style_button(actions, "Preview matches", self.preview_cleaner).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Delete matches", self.delete_cleaner_scope).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.clean_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.style_button(actions, "Copy output", lambda: _v30_copy_text(self, self.clean_output)).grid(row=0, column=3, sticky="ew", padx=4)
    self.clean_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.clean_output.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 16))

    self.clean_mode = "server"
    self.clean_scope = "own"
    self.clean_selected_type = ""
    self.clean_selected_target = ""
    self.clean_selected_info = None
    self.clean_card_images = []
    self.set_cleaner_mode("server")
    self.set_cleaner_scope("own")


def _v30_set_cleaner_mode(self, mode):
    self.clean_mode = mode
    try:
        self.clean_server_btn.configure(fg_color=PINK_DARK if mode == "server" else CARD_2)
        self.clean_dm_btn.configure(fg_color=PINK_DARK if mode == "dm" else CARD_2)
    except Exception:
        pass
    if mode == "dm" and getattr(self, "clean_scope", "own") not in {"own", "text"}:
        self.clean_scope = "own"
    self.render_cleaner_targets()
    self.render_cleaner_scopes()
    self.render_cleaner_options()


def _v30_render_cleaner_scopes(self):
    if not hasattr(self, "clean_scope_scroll"):
        return
    _v30_clear(self.clean_scope_scroll)
    is_dm = getattr(self, "clean_mode", "server") == "dm"
    scopes = [("own", "Own messages", "Delete only messages sent by your account."),
              ("text", "Messages matching text", "Use the text filter below. In DMs this still only deletes your own messages.")]
    if not is_dm:
        scopes.extend([
            ("all", "All messages in channel", "Admin cleanup. Deletes messages from any author in the selected server channel."),
            ("author", "Messages from one user", "Admin cleanup. Enter a User/Author ID below."),
            ("bot", "Bot messages", "Admin cleanup. Deletes messages written by bot accounts in the selected channel."),
        ])
    self.clean_scope_cards = {}
    for key, title, sub in scopes:
        self.clean_scope_cards[key] = _v30_make_scope_card(self, self.clean_scope_scroll, key, title, sub, lambda k=key: self.set_cleaner_scope(k))


def _v30_set_cleaner_scope(self, scope):
    self.clean_scope = scope
    if getattr(self, "clean_mode", "server") == "dm" and scope not in {"own", "text"}:
        self.clean_scope = "own"
    self.render_cleaner_scopes()
    self.render_cleaner_options()
    self.update_cleaner_summary()


def _v30_render_cleaner_options(self):
    if not hasattr(self, "clean_options"):
        return
    _v30_clear(self.clean_options)
    opts = self.clean_options
    opts.grid_columnconfigure(1, weight=1); opts.grid_columnconfigure(3, weight=1)
    self.small_label(opts, "Scan recent", width=105).grid(row=0, column=0, sticky="w", padx=(12, 4), pady=(12, 6))
    self.clean_limit = self.style_option(opts, ["25", "50", "100", "200", "500", "1000", "All"])
    self.clean_limit.set(getattr(self, "_clean_limit_value", "100"))
    self.clean_limit.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(12, 6))
    self.small_label(opts, "Delete max", width=105).grid(row=0, column=2, sticky="w", padx=(6, 4), pady=(12, 6))
    self.clean_delete_amount = self.style_option(opts, ["1", "5", "10", "25", "50", "100", "250", "500", "All"])
    self.clean_delete_amount.set(getattr(self, "_clean_delete_value", "25"))
    self.clean_delete_amount.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=(12, 6))
    ctk.CTkLabel(opts, text="Scan recent = how far back to check. Delete max = maximum matching messages to delete after Preview.", text_color=MUTED, justify="left", anchor="w", wraplength=650).grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 8))
    self.clean_include_pins = ctk.CTkSwitch(opts, text="Include pinned messages", progress_color=PINK_DARK, text_color=TEXT)
    self.clean_include_pins.grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=6)
    self.small_label(opts, "Text filter", width=105).grid(row=3, column=0, sticky="w", padx=(12, 4), pady=6)
    self.clean_text_filter = self.style_entry(opts, "Only messages containing this text")
    self.clean_text_filter.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(0, 12), pady=6)
    self.small_label(opts, "Author/User ID", width=105).grid(row=4, column=0, sticky="w", padx=(12, 4), pady=(6, 12))
    placeholder = "Required for 'Messages from one user'" if getattr(self, "clean_scope", "own") == "author" else "Optional server author filter"
    self.clean_author_filter = self.style_entry(opts, placeholder)
    self.clean_author_filter.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(0, 12), pady=(6, 12))


def _v30_render_cleaner_targets(self):
    if not hasattr(self, "clean_target_scroll"):
        return
    _v30_clear(self.clean_target_scroll)
    q = self.clean_search.get().lower().strip() if hasattr(self, "clean_search") else ""
    if getattr(self, "clean_mode", "server") == "dm":
        self.clean_target_hint.configure(text="DM targets. Cleanup can only delete your own DM messages.")
        for dm in self.dms:
            hay = f"{dm.get('display','')} {dm.get('name','')} {dm.get('id','')}'.lower()"
            hay = f"{dm.get('display','')} {dm.get('name','')} {dm.get('id','')}".lower()
            if q and q not in hay:
                continue
            active = str(dm.get("id")) == str(getattr(self, "clean_selected_target", "")) and getattr(self, "clean_selected_type", "") == "DM User ID"
            title = dm.get("display") or dm.get("name") or "DM user"
            sub = f"{dm.get('name','')}\nID: {dm.get('id')}"
            _v30_make_target_card(self, self.clean_target_scroll, title, sub, "👤", active, lambda d=dm: self.select_cleaner_dm(d), dm.get("avatar"))
    else:
        self.clean_target_hint.configure(text="Server channels only. Select a channel card to clean messages from that channel.")
        guild_map = {str(g.get("id")): g for g in self.guilds}
        last_gid = None
        for c in self.channels:
            g = guild_map.get(str(c.get("guild_id")), {})
            hay = f"{g.get('name','')} {g.get('id','')} {c.get('name','')} {c.get('category','')} {c.get('id','')}".lower()
            if q and q not in hay:
                continue
            gid = str(c.get("guild_id"))
            if gid != last_gid:
                last_gid = gid
                header = ctk.CTkLabel(self.clean_target_scroll, text=f"● {g.get('name','Server')}  |  ID: {g.get('id','')}", text_color=GOOD, font=("Segoe UI", 13, "bold"), anchor="w")
                header.pack(fill="x", padx=10, pady=(10, 2))
            active = str(c.get("id")) == str(getattr(self, "clean_selected_target", "")) and getattr(self, "clean_selected_type", "") == "Server Channel"
            title = f"#{c.get('name','channel')}"
            sub = f"Category: {c.get('category') or 'No category'}\nID: {c.get('id')}"
            _v30_make_target_card(self, self.clean_target_scroll, title, sub, "#", active, lambda gg=g, cc=c: self.select_cleaner_channel(gg, cc))


def _v30_select_cleaner_channel(self, guild, channel):
    self.clean_selected_type = "Server Channel"
    self.clean_selected_target = str(channel.get("id"))
    self.clean_selected_info = (guild, channel)
    self.clean_detail.configure(text=f"Server: {guild.get('name')}\nChannel: #{channel.get('name')}\nCategory: {channel.get('category') or 'No category'}\nChannel ID: {channel.get('id')}")
    self.clean_icon.configure(text="#", image=None)
    self.render_cleaner_targets()
    self.update_cleaner_summary()


def _v30_select_cleaner_dm(self, dm):
    self.clean_selected_type = "DM User ID"
    self.clean_selected_target = str(dm.get("id"))
    self.clean_selected_info = dm
    self.clean_detail.configure(text=f"DM user: {dm.get('display') or dm.get('name')}\nUsername: {dm.get('name','')}\nUser ID: {dm.get('id')}\nOnly your own messages can be deleted here.")
    self.load_avatar_async(dm.get("avatar"), self.clean_icon, 72)
    self.render_cleaner_targets()
    self.update_cleaner_summary()


def _v30_cleaner_target(self):
    return getattr(self, "clean_selected_type", ""), getattr(self, "clean_selected_target", "")


def _v30_scope_to_worker(scope):
    return {
        "own": "Own messages",
        "all": "All messages (admin)",
        "author": "Specific author (admin)",
        "bot": "Bot messages",
        "text": "Matching text",
    }.get(scope, "Own messages")


def _v30_cleaner_common(self):
    try: self._clean_limit_value = self.clean_limit.get()
    except Exception: self._clean_limit_value = "100"
    try: self._clean_delete_value = self.clean_delete_amount.get()
    except Exception: self._clean_delete_value = "25"
    t, target = self.cleaner_target()
    return {
        "target_type": t,
        "target_id": target,
        "limit": self._clean_limit_value,
        "scope": _v30_scope_to_worker(getattr(self, "clean_scope", "own")),
        "include_pinned": bool(self.clean_include_pins.get()) if hasattr(self, "clean_include_pins") else False,
        "text_filter": self.clean_text_filter.get().strip() if hasattr(self, "clean_text_filter") else "",
        "user_filter": self.clean_author_filter.get().strip() if hasattr(self, "clean_author_filter") else "",
        "delete_limit": self._clean_delete_value,
    }


def _v30_update_cleaner_summary(self):
    if not hasattr(self, "clean_summary"):
        return
    args = self.cleaner_common() if hasattr(self, "clean_limit") else {"limit":"100", "delete_limit":"25", "scope":"Own messages", "text_filter":"", "user_filter":""}
    target = "No target selected" if not getattr(self, "clean_selected_target", "") else f"Target: {self.clean_selected_type} {self.clean_selected_target}"
    self.clean_summary.configure(text=f"{target}  •  Scope: {args['scope']}  •  Scan: {args['limit']}  •  Delete max: {args['delete_limit']}")


def _v30_preview_cleaner(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        _v30_set_output(self.clean_output, "Select a target card first.")
        return
    if args["scope"] == "Specific author (admin)" and not args["user_filter"]:
        _v30_set_output(self.clean_output, "Enter an Author/User ID for this cleanup mode.")
        return
    if args["scope"] == "Matching text" and not args["text_filter"]:
        _v30_set_output(self.clean_output, "Enter a Text filter for this cleanup mode.")
        return
    self.update_cleaner_summary()
    fut = self.worker.run_coro(self.worker.preview_cleaner_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: _v30_set_output(self.clean_output, f"Error: {f.exception()}" if f.exception() else f.result())))


def _v30_delete_cleaner_scope(self):
    args = self.cleaner_common()
    if not args["target_id"]:
        _v30_set_output(self.clean_output, "Select a target card first.")
        return
    if args["scope"] == "Specific author (admin)" and not args["user_filter"]:
        _v30_set_output(self.clean_output, "Enter an Author/User ID for this cleanup mode.")
        return
    if args["scope"] == "Matching text" and not args["text_filter"]:
        _v30_set_output(self.clean_output, "Enter a Text filter for this cleanup mode.")
        return
    msg = f"Delete matching messages?\n\nScope: {args['scope']}\nScan recent: {args['limit']}\nDelete max: {args['delete_limit']}\n\nUse Preview first."
    if not messagebox.askyesno("Confirm cleaner", msg):
        return
    self.update_cleaner_summary()
    fut = self.worker.run_coro(self.worker.delete_messages(**args))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.clean_done(f)))



def _v30_build_monitor_tab(self, tab):
    self.page_title(tab, "Monitor / Auto", "Watch selected server channels or DMs. Auto Reply and Auto React are optional and keyword based.")
    outer = ctk.CTkFrame(tab, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=24, pady=12)
    outer.grid_columnconfigure(0, weight=2)
    outer.grid_columnconfigure(1, weight=3)
    outer.grid_rowconfigure(0, weight=1)

    left = self.card(outer); left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
    right = self.card(outer); right.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=8)
    for f in (left, right):
        f.grid_columnconfigure(0, weight=1)
    left.grid_rowconfigure(5, weight=1)
    right.grid_rowconfigure(1, weight=1)
    right.grid_rowconfigure(3, weight=1)

    ctk.CTkLabel(left, text="1. Monitor targets", text_color=PINK_2, font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    mode = ctk.CTkFrame(left, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    mode.grid(row=1, column=0, sticky="ew", padx=16, pady=6); mode.grid_columnconfigure((0, 1), weight=1)
    self.monitor_server_switch = ctk.CTkSwitch(mode, text="Server monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_server_switch.select(); self.monitor_server_switch.grid(row=0, column=0, sticky="w", padx=12, pady=10)
    self.monitor_dm_switch = ctk.CTkSwitch(mode, text="DM monitor", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_dm_switch.select(); self.monitor_dm_switch.grid(row=0, column=1, sticky="w", padx=12, pady=10)
    self.monitor_target_search = self.style_entry(left, "Search target")
    self.monitor_target_search.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
    self.monitor_target_search.bind("<KeyRelease>", lambda _e: self.render_monitor_cards())
    stats = ctk.CTkFrame(left, fg_color="transparent")
    stats.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4)); stats.grid_columnconfigure((0, 1), weight=1)
    self.monitor_label = _v30_status_chip(stats, "Server: all", False); self.monitor_label.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    self.monitor_dm_label = _v30_status_chip(stats, "DM: all", False); self.monitor_dm_label.grid(row=0, column=1, sticky="ew", padx=(4, 0))
    self.monitor_target_tabs = ctk.CTkSegmentedButton(left, values=["Server channels", "DM users"], command=lambda _v: self.render_monitor_cards(), selected_color=PINK_DARK, selected_hover_color=PINK, unselected_color=CARD_2, unselected_hover_color=PINK_DARK)
    self.monitor_target_tabs.set("Server channels")
    self.monitor_target_tabs.grid(row=4, column=0, sticky="ew", padx=16, pady=8)
    self.monitor_target_scroll = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14)
    self.monitor_target_scroll.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 16))

    ctk.CTkLabel(right, text="2. Rules", text_color=PINK_2, font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    rules = ctk.CTkScrollableFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    rules.grid(row=1, column=0, sticky="nsew", padx=16, pady=6)
    rules.grid_columnconfigure(0, weight=1)
    self.monitor_rules_panel = rules

    logbox = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=14)
    logbox.pack(fill="x", padx=10, pady=(10, 8)); logbox.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(logbox, text="A. Logging filter", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
    self.monitor_only_keyword_switch = ctk.CTkSwitch(logbox, text="Only log messages containing keywords", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_only_keyword_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.monitor_keywords_entry = self.style_entry(logbox, "keywords: help,support,error")
    self.monitor_keywords_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.monitor_ignore_bots_switch = ctk.CTkSwitch(logbox, text="Ignore bot messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_ignore_bots_switch.select(); self.monitor_ignore_bots_switch.grid(row=2, column=0, sticky="w", padx=12, pady=(6, 12))
    self.monitor_include_own_switch = ctk.CTkSwitch(logbox, text="Include my own messages", progress_color=PINK_DARK, text_color=TEXT)
    self.monitor_include_own_switch.grid(row=2, column=1, sticky="w", padx=12, pady=(6, 12))

    replybox = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=14)
    replybox.pack(fill="x", padx=10, pady=8); replybox.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(replybox, text="B. Auto Reply", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
    self.reply_switch = ctk.CTkSwitch(replybox, text="Enable auto reply", progress_color=PINK_DARK, text_color=TEXT)
    self.reply_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.reply_keywords = self.style_entry(replybox, "reply trigger keywords, empty = logging keywords")
    self.reply_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    ctk.CTkLabel(replybox, text="When a monitored message contains a trigger keyword, the app sends the reply text below. Empty trigger field means it uses the logging keywords above.", text_color=MUTED, justify="left", anchor="w", wraplength=820).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
    self.reply_text = self.style_entry(replybox, "message that should be sent as the automatic reply")
    self.reply_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
    cool = ctk.CTkFrame(replybox, fg_color="transparent")
    cool.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 12)); cool.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(cool, text="Cooldown", text_color=TEXT, width=110, anchor="w").grid(row=0, column=0, sticky="w")
    self.reply_cooldown = ctk.CTkSlider(cool, from_=5, to=300, number_of_steps=59, progress_color=PINK_DARK, button_color=PINK)
    self.reply_cooldown.set(30); self.reply_cooldown.grid(row=0, column=1, sticky="ew", padx=8)
    self.reply_cooldown_label = ctk.CTkLabel(cool, text="30 seconds", text_color=PINK_2, width=120)
    self.reply_cooldown_label.grid(row=0, column=2, sticky="e")
    self.reply_cooldown.configure(command=lambda v: _v30_update_reply_slider_label(self, v))
    ctk.CTkLabel(cool, text="Example: with 30 seconds, the same user in the same channel cannot trigger another auto reply for 30 seconds.", text_color=MUTED, justify="left", anchor="w", wraplength=820).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

    reactbox = ctk.CTkFrame(rules, fg_color=CARD_2, corner_radius=14)
    reactbox.pack(fill="x", padx=10, pady=8); reactbox.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(reactbox, text="C. Auto React", text_color=GOOD, font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
    self.react_switch = ctk.CTkSwitch(reactbox, text="Enable auto react", progress_color=PINK_DARK, text_color=TEXT)
    self.react_switch.grid(row=1, column=0, sticky="w", padx=12, pady=6)
    self.react_keywords = self.style_entry(reactbox, "reaction trigger keywords, empty = logging keywords")
    self.react_keywords.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
    self.react_emoji = self.style_entry(reactbox, "emoji to react with")
    self.react_emoji.insert(0, "💖"); self.react_emoji.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
    quick = ctk.CTkFrame(reactbox, fg_color="transparent")
    quick.grid(row=2, column=1, sticky="w", padx=12, pady=6)
    for i, emoji in enumerate(V30_EMOJIS):
        self.style_button(quick, emoji, lambda e=emoji: (self.react_emoji.delete(0, "end"), self.react_emoji.insert(0, e)), width=42).grid(row=0, column=i, padx=3)

    actions = ctk.CTkFrame(right, fg_color="transparent")
    actions.grid(row=2, column=0, sticky="ew", padx=12, pady=6); actions.grid_columnconfigure((0, 1, 2), weight=1)
    self.style_button(actions, "Apply rules", self.apply_auto_settings).grid(row=0, column=0, sticky="ew", padx=4)
    self.style_button(actions, "Clear selected targets", self.clear_all_monitor_targets).grid(row=0, column=1, sticky="ew", padx=4)
    self.style_button(actions, "Clear output", lambda: self.monitor_output.delete("1.0", "end")).grid(row=0, column=2, sticky="ew", padx=4)
    self.monitor_output = ctk.CTkTextbox(right, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1)
    self.monitor_output.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))

    self.monitor_channel = self.style_option(right, ["No channels loaded"]); self.monitor_channel.grid_remove()
    self.monitor_dm_select = self.style_option(right, ["All DMs"]); self.monitor_dm_select.grid_remove()
    self.render_monitor_cards()


def _v30_update_reply_slider_label(self, value):
    try:
        self.reply_cooldown_label.configure(text=f"{int(float(value))} seconds")
    except Exception:
        pass


def _v30_render_monitor_cards(self):
    if not hasattr(self, "monitor_target_scroll"):
        return
    _v30_clear(self.monitor_target_scroll)
    q = self.monitor_target_search.get().lower().strip() if hasattr(self, "monitor_target_search") else ""
    tab = self.monitor_target_tabs.get() if hasattr(self, "monitor_target_tabs") else "Server channels"
    if tab == "DM users":
        for dm in self.dms:
            hay = f"{dm.get('display','')} {dm.get('name','')} {dm.get('id','')}".lower()
            if q and q not in hay:
                continue
            active = int(dm.get("id")) in self.worker.monitor_dm_user_ids
            title = dm.get("display") or dm.get("name") or "DM user"
            sub = f"{dm.get('name','')}\nID: {dm.get('id')}"
            _v30_make_target_card(self, self.monitor_target_scroll, title, sub, "👤", active, lambda d=dm: self.toggle_monitor_dm_card(d), dm.get("avatar"))
    else:
        guild_map = {str(g.get("id")): g for g in self.guilds}
        last_gid = None
        for c in self.channels:
            g = guild_map.get(str(c.get("guild_id")), {})
            hay = f"{g.get('name','')} {c.get('name','')} {c.get('category','')} {c.get('id','')}".lower()
            if q and q not in hay:
                continue
            gid = str(c.get("guild_id"))
            if gid != last_gid:
                last_gid = gid
                ctk.CTkLabel(self.monitor_target_scroll, text=f"● {g.get('name','Server')}", text_color=GOOD, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 2))
            active = int(c.get("id")) in self.worker.monitor_channel_ids
            _v30_make_target_card(self, self.monitor_target_scroll, f"#{c.get('name')}", f"Category: {c.get('category') or 'No category'}\nID: {c.get('id')}", "#", active, lambda cc=c: self.toggle_monitor_channel_card(cc))
    self.update_monitor_labels()


def _v30_update_monitor_labels(self):
    if hasattr(self, "monitor_label"):
        count = len(self.worker.monitor_channel_ids)
        self.monitor_label.configure(text=f"Server: {'all channels' if count == 0 else str(count) + ' selected'}")
    if hasattr(self, "monitor_dm_label"):
        count = len(self.worker.monitor_dm_user_ids)
        self.monitor_dm_label.configure(text=f"DM: {'all DMs' if count == 0 else str(count) + ' selected'}")


def _v30_clear_all_monitor_targets(self):
    self.worker.monitor_channel_ids.clear()
    self.worker.monitor_dm_user_ids.clear()
    self.render_monitor_cards()
    self.monitor_log("Selected monitor targets cleared. With empty target lists, enabled monitors watch all available server channels / DMs.\n")


def _v30_apply_auto_settings(self):
    self.worker.monitor_servers = bool(self.monitor_server_switch.get())
    self.worker.monitor_dms = bool(self.monitor_dm_switch.get())
    self.worker.monitor_keywords = [x.strip() for x in self.monitor_keywords_entry.get().split(",") if x.strip()]
    self.worker.monitor_only_keywords = bool(self.monitor_only_keyword_switch.get())
    self.worker.monitor_ignore_bots = bool(self.monitor_ignore_bots_switch.get())
    self.worker.monitor_include_own = bool(self.monitor_include_own_switch.get())
    self.worker.auto_reply_enabled = bool(self.reply_switch.get())
    self.worker.auto_reply_keywords = [x.strip() for x in self.reply_keywords.get().split(",") if x.strip()]
    self.worker.auto_reply_text = self.reply_text.get().strip()
    try:
        self.worker.auto_reply_cooldown = int(float(self.reply_cooldown.get()))
    except Exception:
        self.worker.auto_reply_cooldown = 30
    self.worker.auto_react_enabled = bool(self.react_switch.get())
    self.worker.auto_react_keywords = [x.strip() for x in self.react_keywords.get().split(",") if x.strip()]
    self.worker.auto_react_emoji = self.react_emoji.get().strip() or "💖"
    self.monitor_log(
        "Rules applied.\n"
        f"Server monitor: {'on' if self.worker.monitor_servers else 'off'} | DM monitor: {'on' if self.worker.monitor_dms else 'off'}\n"
        f"Logging keywords: {', '.join(self.worker.monitor_keywords) or 'none / log all'}\n"
        f"Auto Reply: {'on' if self.worker.auto_reply_enabled else 'off'} | Cooldown: {self.worker.auto_reply_cooldown}s | Reply keywords: {', '.join(self.worker.auto_reply_keywords) or 'logging keywords'}\n"
        f"Auto React: {'on' if self.worker.auto_react_enabled else 'off'} | Emoji: {self.worker.auto_react_emoji}\n\n"
    )
    self.update_monitor_labels()


App.build_cleaner_tab = _v30_build_cleaner_tab
App.set_cleaner_mode = _v30_set_cleaner_mode
App.render_cleaner_scopes = _v30_render_cleaner_scopes
App.set_cleaner_scope = _v30_set_cleaner_scope
App.render_cleaner_options = _v30_render_cleaner_options
App.render_cleaner_targets = _v30_render_cleaner_targets
App.select_cleaner_channel = _v30_select_cleaner_channel
App.select_cleaner_dm = _v30_select_cleaner_dm
App.cleaner_target = _v30_cleaner_target
App.cleaner_common = _v30_cleaner_common
App.update_cleaner_summary = _v30_update_cleaner_summary
App.preview_cleaner = _v30_preview_cleaner
App.delete_cleaner_scope = _v30_delete_cleaner_scope
App.delete_own_messages = _v30_delete_cleaner_scope

App.build_monitor_tab = _v30_build_monitor_tab
App.render_monitor_cards = _v30_render_monitor_cards
App.update_monitor_labels = _v30_update_monitor_labels
App.clear_all_monitor_targets = _v30_clear_all_monitor_targets
App.apply_auto_settings = _v30_apply_auto_settings


def _v33_entry_row(app, parent, row, label, widget, help_text=None):
    app.small_label(parent, label, width=145).grid(row=row, column=0, sticky="w", padx=14, pady=(8, 4))
    widget.grid(row=row, column=1, sticky="ew", padx=14, pady=(8, 4))
    if help_text:
        ctk.CTkLabel(parent, text=help_text, text_color=MUTED, anchor="w", justify="left", wraplength=650).grid(row=row+1, column=1, sticky="ew", padx=14, pady=(0, 6))
        return row + 2
    return row + 1


def _v33_build_profile_tab(self, tab):
    self.page_title(tab, "Profile", "Manage your avatar, presence, activity text, server nickname, and account details in one place.")
    wrap = ctk.CTkFrame(tab, fg_color="transparent")
    wrap.pack(fill="both", expand=True, padx=24, pady=10)
    wrap.grid_columnconfigure(0, weight=1)
    wrap.grid_columnconfigure(1, weight=1)
    wrap.grid_columnconfigure(2, weight=1)
    wrap.grid_rowconfigure(0, weight=1)

    left = self.card(wrap); left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=8)
    mid = self.card(wrap); mid.grid(row=0, column=1, sticky="nsew", padx=10, pady=8)
    right = self.card(wrap); right.grid(row=0, column=2, sticky="nsew", padx=(10, 0), pady=8)
    for col in (left, mid, right):
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(left, text="Avatar", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    avatar_body = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    avatar_body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    avatar_body.grid_columnconfigure((0, 1), weight=1)

    self.avatar_preview = ctk.CTkLabel(avatar_body, text="Preview", width=150, height=150, fg_color=CARD_2, text_color=MUTED, corner_radius=18)
    self.avatar_preview.grid(row=0, column=0, columnspan=2, pady=(18, 8))
    self.avatar_file_label = ctk.CTkLabel(avatar_body, text="No file selected", text_color=MUTED, wraplength=420)
    self.avatar_file_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))

    self.avatar_url = self.style_entry(avatar_body, "Paste image URL")
    self.avatar_url.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 6))
    self.style_button(avatar_body, "Preview link", self.preview_avatar_url).grid(row=3, column=0, sticky="ew", padx=(14, 5), pady=6)
    self.style_button(avatar_body, "Set link", self.set_avatar_url).grid(row=3, column=1, sticky="ew", padx=(5, 14), pady=6)
    self.style_button(avatar_body, "Choose image", self.choose_avatar_file).grid(row=4, column=0, sticky="ew", padx=(14, 5), pady=6)
    self.style_button(avatar_body, "Set selected file", self.set_avatar_file).grid(row=4, column=1, sticky="ew", padx=(5, 14), pady=6)
    self.style_button(avatar_body, "Load current avatar", self.profile_load_current_avatar).grid(row=5, column=0, sticky="ew", padx=(14, 5), pady=(12, 6))
    self.style_button(avatar_body, "Clear preview", self.profile_clear_avatar_preview).grid(row=5, column=1, sticky="ew", padx=(5, 14), pady=(12, 6))
    ctk.CTkLabel(avatar_body, text="Images are automatically cropped, resized, and converted for Discord. Avoid changing avatars too often.", text_color=MUTED, justify="left", wraplength=420).grid(row=6, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 18))

    ctk.CTkLabel(mid, text="Presence & activity", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    presence = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    presence.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    presence.grid_columnconfigure(1, weight=1)

    r = 0
    self.online_status = self.style_option(presence, ["online", "idle", "dnd", "invisible"])
    try:
        self.online_status.set("online")
    except Exception:
        pass
    status_card = ctk.CTkFrame(presence, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    status_card.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 10))
    status_card.grid_columnconfigure((0, 1, 2, 3), weight=1)
    ctk.CTkLabel(status_card, text="Online status", text_color=PINK_2, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))
    self.online_status_display = ctk.CTkLabel(status_card, text="Current: online", text_color=TEXT, anchor="w")
    self.online_status_display.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 8))
    for i, txt in enumerate(["online", "idle", "dnd", "invisible"]):
        self.style_button(status_card, txt, lambda t=txt: self.profile_set_online_status(t), width=80).grid(row=2, column=i, sticky="ew", padx=4, pady=(0, 12))
    r += 1
    self.status_type = self.style_option(presence, ["Playing", "Watching", "Listening", "Streaming", "Competing"])
    r = _v33_entry_row(self, presence, r, "Activity type", self.status_type)
    self.status_text = self.style_entry(presence, "Activity name / text")
    r = _v33_entry_row(self, presence, r, "Activity text", self.status_text, "Example: Playing Minecraft, Watching YouTube, Listening to music.")
    self.status_stream_url = self.style_entry(presence, "Streaming URL, optional")
    r = _v33_entry_row(self, presence, r, "Stream URL", self.status_stream_url, "Only relevant for Streaming. If empty, a default Twitch URL is used by the library.")
    self.status_image_note = self.style_entry(presence, "Rich Presence image asset note, optional")
    r = _v33_entry_row(self, presence, r, "Activity image", self.status_image_note, "Activity images are not available through normal account presence. This field is saved only as a note.")

    quick = ctk.CTkFrame(presence, fg_color="transparent")
    quick.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 4))
    quick.grid_columnconfigure((0, 1, 2), weight=1)
    for i, txt in enumerate(["Playing", "Watching", "Listening"]):
        self.style_button(quick, txt, lambda t=txt: self.profile_set_activity_type(t), width=90).grid(row=0, column=i, sticky="ew", padx=4, pady=4)

    btns = ctk.CTkFrame(presence, fg_color="transparent")
    btns.grid(row=r+1, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 14))
    btns.grid_columnconfigure((0, 1), weight=1)
    self.style_button(btns, "Apply presence", self.set_status).grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=4)
    self.style_button(btns, "Clear activity", self.profile_clear_activity).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=4)

    ctk.CTkLabel(right, text="Nickname & account", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    account = ctk.CTkScrollableFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    account.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    account.grid_columnconfigure(1, weight=1)

    info = ctk.CTkFrame(account, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    info.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(16, 10))
    info.grid_columnconfigure(1, weight=1)
    self.profile_avatar_small = ctk.CTkLabel(info, text="👤", width=64, height=64, text_color=PINK, fg_color="#050408", corner_radius=12)
    self.profile_avatar_small.grid(row=0, column=0, rowspan=2, padx=12, pady=12)
    self.profile_account_label = ctk.CTkLabel(info, text="Not connected yet. Connect first, then refresh cache.", text_color=TEXT, justify="left", anchor="w", wraplength=390)
    self.profile_account_label.grid(row=0, column=1, sticky="ew", padx=8, pady=(12, 4))
    self.style_button(info, "Refresh profile info", self.profile_refresh_info, width=150).grid(row=1, column=1, sticky="e", padx=8, pady=(0, 12))

    r = 1
    self.nick_guild = self.style_option(account, ["No servers loaded"])
    r = _v33_entry_row(self, account, r, "Server", self.nick_guild)
    self.nick_entry = self.style_entry(account, "New nickname")
    r = _v33_entry_row(self, account, r, "Nickname", self.nick_entry, "Leave empty and press Clear nickname to remove the nickname where possible.")
    nick_buttons = ctk.CTkFrame(account, fg_color="transparent")
    nick_buttons.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 10))
    nick_buttons.grid_columnconfigure((0, 1, 2), weight=1)
    self.style_button(nick_buttons, "Apply nickname", self.set_nick).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    self.style_button(nick_buttons, "Clear nickname", self.profile_clear_nickname).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    self.style_button(nick_buttons, "Server member info", self.profile_server_member_info).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

    copy_buttons = ctk.CTkFrame(account, fg_color="transparent")
    copy_buttons.grid(row=r+1, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 10))
    copy_buttons.grid_columnconfigure((0, 1, 2), weight=1)
    self.style_button(copy_buttons, "Copy user ID", self.profile_copy_user_id).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    self.style_button(copy_buttons, "Copy username", self.profile_copy_username).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    self.style_button(copy_buttons, "Refresh cache", self.refresh_lists).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

    ctk.CTkLabel(account, text="Profile output", text_color=PINK_2, font=("Segoe UI", 13, "bold")).grid(row=r+2, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))
    self.profile_output = ctk.CTkTextbox(account, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1, height=170)
    self.profile_output.grid(row=r+3, column=0, columnspan=2, sticky="nsew", padx=14, pady=(4, 18))
    self.profile_refresh_info()


def _v33_profile_log(self, text):
    try:
        self.profile_output.insert("end", text.rstrip() + "\n")
        self.profile_output.see("end")
    except Exception:
        self.log(text)


def _v33_profile_refresh_info(self):
    user = getattr(getattr(self, "worker", None), "bot", None)
    user = getattr(user, "user", None)
    if not user:
        if hasattr(self, "profile_account_label"):
            self.profile_account_label.configure(text="Not connected yet. Connect first, then refresh cache.")
        return
    name = getattr(user, "name", "-")
    display = getattr(user, "display_name", name)
    uid = getattr(user, "id", "-")
    text = f"Display: {display}\nUsername: {name}\nUser ID: {uid}\nServers: {len(getattr(self, 'guilds', []))} | DMs: {len(getattr(self, 'dms', []))}"
    if hasattr(self, "profile_account_label"):
        self.profile_account_label.configure(text=text)
    try:
        url = str(user.display_avatar.url)
        self.load_avatar_async(url, self.profile_avatar_small, 64)
    except Exception:
        pass


def _v33_profile_load_current_avatar(self):
    user = getattr(getattr(self, "worker", None), "bot", None)
    user = getattr(user, "user", None)
    if not user:
        return self.profile_log("Connect first, then load current avatar.")
    try:
        url = str(user.display_avatar.url)
        self.avatar_url.delete(0, "end")
        self.avatar_url.insert(0, url)
        self.preview_avatar_url()
        self.profile_log("Current avatar URL loaded into the avatar URL field.")
    except Exception as e:
        self.profile_log(f"Could not load current avatar: {e}")


def _v33_profile_clear_avatar_preview(self):
    try:
        self.avatar_preview.configure(image=None, text="Preview")
        self.avatar_file_label.configure(text="No file selected")
        self.avatar_url.delete(0, "end")
        self.selected_avatar_file = None
    except Exception:
        pass


def _v33_profile_set_activity_type(self, value):
    try:
        self.status_type.set(value)
    except Exception:
        pass


def _v33_profile_set_online_status(self, value):
    try:
        self.online_status.set(value)
    except Exception:
        pass
    try:
        self.online_status_display.configure(text=f"Current: {value}")
    except Exception:
        pass


def _v33_profile_clear_activity(self):
    try:
        self.status_text.delete(0, "end")
        if hasattr(self, "status_image_note"):
            self.status_image_note.delete(0, "end")
        if hasattr(self, "status_stream_url"):
            self.status_stream_url.delete(0, "end")
    except Exception:
        pass
    fut = self.worker.run_coro(self.worker.set_presence("", self.status_type.get(), self.online_status.get()))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.profile_log("Activity cleared." if not f.exception() else f"Clear activity error: {f.exception()}")))


def _v33_profile_clear_nickname(self):
    try:
        self.nick_entry.delete(0, "end")
    except Exception:
        pass
    gid = self.selected_value_id(self.nick_guild.get())
    fut = self.worker.run_coro(self.worker.set_nick(gid, ""))
    if fut:
        fut.add_done_callback(lambda f: self.after(0, lambda: self.profile_log("Nickname cleared." if not f.exception() else f"Clear nickname error: {f.exception()}")))


def _v33_profile_copy(self, text, label="value"):
    try:
        self.clipboard_clear()
        self.clipboard_append(str(text))
        self.profile_log(f"Copied {label}: {text}")
    except Exception as e:
        self.profile_log(f"Copy error: {e}")


def _v33_profile_copy_user_id(self):
    user = getattr(getattr(self, "worker", None), "bot", None)
    user = getattr(user, "user", None)
    self.profile_copy(getattr(user, "id", ""), "user ID")


def _v33_profile_copy_username(self):
    user = getattr(getattr(self, "worker", None), "bot", None)
    user = getattr(user, "user", None)
    self.profile_copy(getattr(user, "name", ""), "username")


def _v33_profile_server_member_info(self):
    gid = self.selected_value_id(self.nick_guild.get())
    if not gid or gid.startswith("No "):
        return self.profile_log("No server selected.")
    g = next((x for x in self.guilds if str(x.get("id")) == str(gid)), None)
    members = self.members_by_guild.get(str(gid), [])
    user_id = str(getattr(getattr(getattr(self, "worker", None), "bot", None), "user", None).id) if getattr(getattr(getattr(self, "worker", None), "bot", None), "user", None) else ""
    me = next((m for m in members if str(m.get("id")) == user_id), None)
    lines = []
    if g:
        lines.append(f"Server: {g.get('name')} ({g.get('id')})")
        lines.append(f"Members cached: {len(members)}")
    if me:
        lines.append(f"You as member: {me.get('display') or me.get('name')} ({me.get('name')})")
        lines.append(f"Bot account flag: {me.get('bot')}")
    else:
        lines.append("Your member object is not in the current cache. Try Refresh cache.")
    self.profile_log("\n".join(lines) + "\n")


App.build_profile_tab = _v33_build_profile_tab
App.profile_log = _v33_profile_log
App.profile_refresh_info = _v33_profile_refresh_info
App.profile_load_current_avatar = _v33_profile_load_current_avatar
App.profile_clear_avatar_preview = _v33_profile_clear_avatar_preview
App.profile_set_activity_type = _v33_profile_set_activity_type
App.profile_set_online_status = _v33_profile_set_online_status
App.profile_clear_activity = _v33_profile_clear_activity
App.profile_clear_nickname = _v33_profile_clear_nickname
App.profile_copy = _v33_profile_copy
App.profile_copy_user_id = _v33_profile_copy_user_id
App.profile_copy_username = _v33_profile_copy_username
App.profile_server_member_info = _v33_profile_server_member_info

def _v35_register_marquee(self, widget, text, min_len=16):
    """Register long UI text for lightweight marquee animation.

    v34 updated every registered widget on every tick, which could feel laggy.
    v35 updates only a small batch per tick, uses a wider visible slice, and
    stores the original text so short labels/buttons stay untouched.
    """
    try:
        if not hasattr(self, "_marquee_widgets"):
            self._marquee_widgets = []
            self._marquee_index = 0
        text = str(text or "")
        if len(text) >= min_len:
            for item in self._marquee_widgets:
                if item.get("widget") is widget:
                    item["text"] = text
                    return widget
            self._marquee_widgets.append({"widget": widget, "text": text, "offset": 0})
    except Exception:
        pass
    return widget


_original_style_button_v35 = App.style_button
def _v35_style_button(self, parent, text, cmd, width=120):
    btn = _original_style_button_v35(self, parent, text, cmd, width)
    try:
        btn.configure(anchor="center")
    except Exception:
        pass
    return _v35_register_marquee(self, btn, text, 18)


_original_small_label_v35 = App.small_label
def _v35_small_label(self, parent, text, width=115):
    lbl = _original_small_label_v35(self, parent, text, width)
    return _v35_register_marquee(self, lbl, text, 18)


def _v35_marquee_tick(self):
    try:
        widgets = getattr(self, "_marquee_widgets", [])
        if not widgets:
            self.after(420, self._marquee_tick)
            return

        alive = []
        for item in widgets:
            w = item.get("widget")
            try:
                if w is not None and w.winfo_exists():
                    alive.append(item)
            except Exception:
                pass
        self._marquee_widgets = alive
        if not alive:
            self.after(420, self._marquee_tick)
            return

        batch_size = 5
        start = int(getattr(self, "_marquee_index", 0)) % len(alive)
        for i in range(min(batch_size, len(alive))):
            item = alive[(start + i) % len(alive)]
            w = item.get("widget")
            text = item.get("text", "")
            if not text or len(text) < 18:
                continue
            spacer = "     "
            loop = text + spacer
            item["offset"] = (int(item.get("offset", 0)) + 2) % len(loop)
            off = item["offset"]

            visible_chars = 26
            try:
                width_px = max(80, int(w.winfo_width()))
                visible_chars = max(18, min(42, width_px // 9))
            except Exception:
                pass
            shown = (loop[off:] + loop[:off])[:visible_chars]
            w.configure(text=shown)

        self._marquee_index = (start + batch_size) % max(1, len(alive))
        self.after(260, self._marquee_tick)
    except Exception:
        try:
            self.after(500, self._marquee_tick)
        except Exception:
            pass


_original_build_ui_v35 = App._build_ui
def _v35_build_ui(self):
    _original_build_ui_v35(self)
    try:
        self.after(450, self._marquee_tick)
    except Exception:
        pass


App.style_button = _v35_style_button
App.small_label = _v35_small_label
App._v35_register_marquee = _v35_register_marquee
App._marquee_tick = _v35_marquee_tick
App._build_ui = _v35_build_ui



def _asb_format_age(dt):
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        days = max(0, (now - dt).days)
        years = days // 365
        months = (days % 365) // 30
        return f"{years}y {months}m ({days} days)"
    except Exception:
        return "unknown"


def _asb_flags_text(flags):
    try:
        names = []
        for name, enabled in flags:
            if enabled:
                names.append(str(name))
        return ", ".join(names) if names else "none visible"
    except Exception:
        try:
            return str(flags) if flags else "none visible"
        except Exception:
            return "none visible"


def _asb_build_profile_tab(self, tab):
    self.page_title(tab, "Profile", "Manage your avatar, presence, nickname and visible account details in one place.")
    wrap = ctk.CTkFrame(tab, fg_color="transparent")
    wrap.pack(fill="both", expand=True, padx=24, pady=10)
    wrap.grid_columnconfigure(0, weight=1)
    wrap.grid_columnconfigure(1, weight=1)
    wrap.grid_columnconfigure(2, weight=1)
    wrap.grid_rowconfigure(0, weight=1)

    left = self.card(wrap); left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=8)
    mid = self.card(wrap); mid.grid(row=0, column=1, sticky="nsew", padx=10, pady=8)
    right = self.card(wrap); right.grid(row=0, column=2, sticky="nsew", padx=(10, 0), pady=8)
    for col in (left, mid, right):
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(left, text="Avatar", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    avatar_body = ctk.CTkScrollableFrame(left, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    avatar_body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    avatar_body.grid_columnconfigure((0, 1), weight=1)

    self.avatar_preview = ctk.CTkLabel(avatar_body, text="Preview", width=150, height=150, fg_color=CARD_2, text_color=MUTED, corner_radius=18)
    self.avatar_preview.grid(row=0, column=0, columnspan=2, pady=(18, 8))
    self.avatar_file_label = ctk.CTkLabel(avatar_body, text="No file selected", text_color=MUTED, wraplength=420)
    self.avatar_file_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))

    self.avatar_url = self.style_entry(avatar_body, "Paste image URL")
    self.avatar_url.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 6))
    self.style_button(avatar_body, "Preview link", self.preview_avatar_url).grid(row=3, column=0, sticky="ew", padx=(14, 5), pady=6)
    self.style_button(avatar_body, "Set link", self.set_avatar_url).grid(row=3, column=1, sticky="ew", padx=(5, 14), pady=6)
    self.style_button(avatar_body, "Choose image", self.choose_avatar_file).grid(row=4, column=0, sticky="ew", padx=(14, 5), pady=6)
    self.style_button(avatar_body, "Set selected file", self.set_avatar_file).grid(row=4, column=1, sticky="ew", padx=(5, 14), pady=6)
    self.style_button(avatar_body, "Load current avatar", self.profile_load_current_avatar).grid(row=5, column=0, sticky="ew", padx=(14, 5), pady=(12, 6))
    self.style_button(avatar_body, "Clear preview", self.profile_clear_avatar_preview).grid(row=5, column=1, sticky="ew", padx=(5, 14), pady=(12, 6))
    ctk.CTkLabel(avatar_body, text="Images are cropped, resized and converted before upload. Avatar changes can be rate-limited.", text_color=MUTED, justify="left", wraplength=420).grid(row=6, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 18))

    ctk.CTkLabel(mid, text="Presence & activity", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    presence = ctk.CTkScrollableFrame(mid, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    presence.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    presence.grid_columnconfigure(1, weight=1)

    self.online_status = self.style_option(presence, ["online", "idle", "dnd", "invisible"])
    try:
        self.online_status.set("online")
    except Exception:
        pass
    status_card = ctk.CTkFrame(presence, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    status_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 10))
    status_card.grid_columnconfigure((0, 1, 2, 3), weight=1)
    ctk.CTkLabel(status_card, text="Online status", text_color=PINK_2, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))
    self.online_status_display = ctk.CTkLabel(status_card, text="Current: online", text_color=TEXT, anchor="w")
    self.online_status_display.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 8))
    for i, txt in enumerate(["online", "idle", "dnd", "invisible"]):
        self.style_button(status_card, txt, lambda t=txt: self.profile_set_online_status(t), width=80).grid(row=2, column=i, sticky="ew", padx=4, pady=(0, 12))

    r = 1
    self.status_type = self.style_option(presence, ["Playing", "Streaming", "Competing"])
    r = _v33_entry_row(self, presence, r, "Activity type", self.status_type)
    self.status_text = self.style_entry(presence, "Activity name / text")
    r = _v33_entry_row(self, presence, r, "Activity text", self.status_text, "Example: Playing Minecraft or Competing in ranked.")
    self.status_stream_url = self.style_entry(presence, "Streaming URL, optional")
    r = _v33_entry_row(self, presence, r, "Stream URL", self.status_stream_url, "Only needed for Streaming.")
    self.status_image_note = self.style_entry(presence, "Rich Presence image note")
    r = _v33_entry_row(self, presence, r, "Activity image", self.status_image_note, "Activity image assets cannot be set through normal account presence. This is only a note field.")

    btns = ctk.CTkFrame(presence, fg_color="transparent")
    btns.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 14))
    btns.grid_columnconfigure((0, 1), weight=1)
    self.style_button(btns, "Apply presence", self.set_status).grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=4)
    self.style_button(btns, "Clear activity", self.profile_clear_activity).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=4)

    ctk.CTkLabel(right, text="Account tools", text_color=PINK_2, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
    account = ctk.CTkScrollableFrame(right, fg_color="#050408", corner_radius=14, border_color=PINK_DARK, border_width=1)
    account.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
    account.grid_columnconfigure(1, weight=1)

    info = ctk.CTkFrame(account, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    info.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(16, 10))
    info.grid_columnconfigure(1, weight=1)
    self.profile_avatar_small = ctk.CTkLabel(info, text="👤", width=64, height=64, text_color=PINK, fg_color="#050408", corner_radius=12)
    self.profile_avatar_small.grid(row=0, column=0, rowspan=2, padx=12, pady=12)
    self.profile_account_label = ctk.CTkLabel(info, text="Not connected yet. Connect first, then refresh cache.", text_color=TEXT, justify="left", anchor="w", wraplength=390)
    self.profile_account_label.grid(row=0, column=1, sticky="ew", padx=8, pady=(12, 4))
    self.style_button(info, "Refresh profile info", self.profile_refresh_info, width=150).grid(row=1, column=1, sticky="e", padx=8, pady=(0, 12))

    r = 1
    self.nick_guild = self.style_option(account, ["No servers loaded"])
    r = _v33_entry_row(self, account, r, "Server", self.nick_guild)
    self.nick_entry = self.style_entry(account, "New nickname")
    r = _v33_entry_row(self, account, r, "Nickname", self.nick_entry, "Leave empty and press Clear nickname to remove it where possible.")
    nick_buttons = ctk.CTkFrame(account, fg_color="transparent")
    nick_buttons.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 10))
    nick_buttons.grid_columnconfigure((0, 1, 2), weight=1)
    self.style_button(nick_buttons, "Apply nickname", self.set_nick).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    self.style_button(nick_buttons, "Clear nickname", self.profile_clear_nickname).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    self.style_button(nick_buttons, "Member info", self.profile_server_member_info).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

    checker = ctk.CTkFrame(account, fg_color=CARD_2, corner_radius=14, border_color=PINK_DARK, border_width=1)
    checker.grid(row=r+1, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 10))
    checker.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(checker, text="User ID Checker", text_color=PINK_2, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 6))
    self.user_checker_avatar = ctk.CTkLabel(checker, text="👤", width=72, height=72, text_color=PINK, fg_color="#050408", corner_radius=12)
    self.user_checker_avatar.grid(row=1, column=0, rowspan=3, padx=(12, 10), pady=(4, 10))
    self.user_checker_id = self.style_entry(checker, "Enter user ID")
    self.user_checker_id.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=(4, 6))
    self.style_button(checker, "Check user", self.profile_check_user).grid(row=2, column=1, sticky="ew", padx=(0, 5), pady=4)
    self.style_button(checker, "Copy avatar URL", self.profile_copy_checked_avatar).grid(row=2, column=2, sticky="ew", padx=(5, 12), pady=4)
    ctk.CTkLabel(checker, text="Fetches everything visible to this account: name, avatar, created date, public flags, bot/system, banner/accent if exposed, and cached mutual servers.", text_color=MUTED, justify="left", wraplength=420).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=(2, 10))

    copy_buttons = ctk.CTkFrame(account, fg_color="transparent")
    copy_buttons.grid(row=r+2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 10))
    copy_buttons.grid_columnconfigure((0, 1, 2), weight=1)
    self.style_button(copy_buttons, "Copy user ID", self.profile_copy_user_id).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    self.style_button(copy_buttons, "Copy username", self.profile_copy_username).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    self.style_button(copy_buttons, "Refresh cache", self.refresh_lists).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

    ctk.CTkLabel(account, text="Profile output", text_color=PINK_2, font=("Segoe UI", 13, "bold")).grid(row=r+3, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))
    self.profile_output = ctk.CTkTextbox(account, fg_color="#050408", text_color=TEXT, border_color=PINK_DARK, border_width=1, height=220)
    self.profile_output.grid(row=r+4, column=0, columnspan=2, sticky="nsew", padx=14, pady=(4, 18))
    self.profile_refresh_info()


def _asb_user_mutual_servers(self, user_id):
    names = []
    try:
        for g in getattr(self, "guilds", []):
            gid = str(g.get("id"))
            for m in self.members_by_guild.get(gid, []):
                if str(m.get("id")) == str(user_id):
                    names.append(f"{g.get('name')} ({gid})")
                    break
    except Exception:
        pass
    return names


async def _asb_fetch_user_details(self, user_id):
    bot = self.worker.bot
    user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
    created = getattr(user, "created_at", None)
    avatar = ""
    try:
        avatar = str(user.display_avatar.url)
    except Exception:
        pass
    banner = ""
    try:
        banner_obj = getattr(user, "banner", None)
        if banner_obj:
            banner = str(banner_obj.url)
    except Exception:
        pass
    accent = getattr(user, "accent_color", None) or getattr(user, "accent_colour", None)
    flags = getattr(user, "public_flags", None) or getattr(user, "flags", None)
    mutual = _asb_user_mutual_servers(self, getattr(user, "id", user_id))
    lines = [
        "User ID Checker result",
        f"Username: {getattr(user, 'name', '-')}",
        f"Display name: {getattr(user, 'display_name', getattr(user, 'name', '-'))}",
        f"User ID: {getattr(user, 'id', user_id)}",
        f"Bot: {getattr(user, 'bot', False)}",
        f"System: {getattr(user, 'system', False)}",
        f"Created: {created}",
        f"Account age: {_asb_format_age(created) if created else 'unknown'}",
        f"Avatar URL: {avatar or 'none visible'}",
        f"Banner URL: {banner or 'none visible'}",
        f"Accent color: {accent or 'none visible'}",
        f"Public flags/badges: {_asb_flags_text(flags)}",
        "Nitro: not reliably visible through this API" + (" (animated avatar may indicate Nitro)" if str(avatar).endswith('.gif') else ""),
        f"Cached mutual servers: {len(mutual)}",
    ]
    if mutual:
        lines.extend(["", "Mutual servers from cache:"] + [f"- {x}" for x in mutual[:30]])
    return {"text": "\n".join(lines), "avatar": avatar}


def _asb_profile_check_user(self):
    uid = ""
    try:
        uid = self.user_checker_id.get().strip()
    except Exception:
        pass
    if not uid or not uid.isdigit():
        return self.profile_log("Enter a valid numeric user ID.")
    fut = self.worker.run_coro(_asb_fetch_user_details(self, uid))
    if not fut:
        return self.profile_log("Connect first, then check a user ID.")
    def done(f):
        try:
            result = f.result()
            self.checked_user_avatar_url = result.get("avatar", "")
            self.profile_log(result.get("text", "No data."))
            if self.checked_user_avatar_url:
                self.load_avatar_async(self.checked_user_avatar_url, self.user_checker_avatar, 72)
        except Exception as e:
            self.profile_log(f"User ID check failed: {e}")
    fut.add_done_callback(lambda f: self.after(0, lambda: done(f)))


def _asb_profile_copy_checked_avatar(self):
    url = getattr(self, "checked_user_avatar_url", "")
    if not url:
        return self.profile_log("No checked avatar URL to copy. Run Check user first.")
    self.profile_copy(url, "checked avatar URL")


App.build_profile_tab = _asb_build_profile_tab
App.profile_check_user = _asb_profile_check_user
App.profile_copy_checked_avatar = _asb_profile_copy_checked_avatar

if __name__ == "__main__":
    app = App()
    app.mainloop()
