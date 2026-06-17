# Alpha Self Bot

Alpha Self Bot is a Windows-focused Python UI tool for managing one account.

## Main areas

### Connect
Connect with a hidden token field and refresh cached servers, channels, members and DMs.

### Profile
Manage avatar, presence/activity, online status and server nickname. The Profile page also includes a User ID Checker:

- Enter a numeric Discord user ID.
- Press **Check user**.
- The tool shows every account detail that is visible to the logged-in account, such as username, display name, ID, avatar URL, creation date, account age, visible public flags/badges, bot/system flags, banner/accent color when exposed, and cached mutual servers.
- The checked avatar is displayed in the UI and the avatar URL can be copied.

Nitro status is not reliably exposed by the public Discord API, so the checker does not fake that value.

### DM Center
Read DM history, send messages, attach files and insert emoji from quick buttons.

### Servers
Browse servers and channels with cards, inspect server/channel data, read history, send messages and attach files.

### Admin
Server management actions for servers where your account has permissions. Includes channel, role, member, invite, thread and message actions with command-specific pages.

### Cleaner
Preview and delete messages with clear scopes:

- Own messages
- All messages in a server channel when Manage Messages is available
- Specific author by User ID when Manage Messages is available
- Text filter
- Scan recent amount
- Delete max amount

### Monitor
Monitor server channels and DMs, log keyword matches, optionally auto-reply and auto-react with cooldown controls.

## Setup

1. Extract the ZIP.
2. Open `.env.example`, copy it to `.env`.
3. Put your token into `.env`:

```env
DISCORD_TOKEN=your_token_here
```

4. Start with `start.bat`.

The batch file creates `.venv`, installs requirements and runs `main.py`.

