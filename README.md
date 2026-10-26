# Undo Bridge

**Give your AI hands.** Undo Bridge connects a chat window (DeepSeek) to a local agent called Undo that can actually touch your computer: read and edit files, run commands, capture windows, and send the result straight back into the chat.

No copy-paste loops, no plugins inside your IDE. You describe the task in the chat, the agent does the work, the answer comes back on its own.

---

## Why

Chat models can write code, but they cannot run it. Undo Bridge closes that gap:

- you ask for something in plain language;
- the model replies with a command in a simple text format;
- the browser extension picks the command up and hands it to the local agent;
- the agent executes it on your machine and puts the result back;
- the extension pastes the result into the chat as your next message.

You only watch. The whole round trip happens through the system clipboard, so nothing new is exposed to the network.

---

## What it can do

**Files and code**
- Write, overwrite, append to and edit files by exact text match
- Create folders, copy, move, rename, delete, compare, hash
- Search inside a project, read ranges, count matches
- Keep automatic backups before every destructive change and restore them

**System**
- Run shell commands in `cmd` or `powershell`
- Long tasks are not cut off: search, copy and build operations wait until they finish
- Get the real output back, including errors, instead of an empty "done"

**Screen**
- Capture the active window or a specific window by title
- Capture the full virtual desktop across multiple monitors
- The image is attached to the chat as a real picture, not a file path

**Context**
- Export project structure, recent files, open tabs and clipboard state
- Remember notes, reminders and task lists between sessions

**Interface**
- A compact panel floats over the chat page: status, log, pause, step counter
- A full side panel opens with settings, language, journal and controls
- Interface and agent support English, Russian and Chinese

---

## Install

1. Run `UndoBridge_Setup.exe`.
2. Pick the Chromium browsers you use — Chrome, Edge, Yandex, Opera, Opera GX, Brave, Vivaldi, Chromium.
3. The installer extracts the agent to `%LOCALAPPDATA%\UndoBridge\agent`, starts it in the background and opens the browser page where you load the unpacked extension.

The agent runs headless: no window, no console, no tray icon. Everything you interact with lives in the extension.

Nothing else is required — Python is bundled inside the agent executable.

---

## How it works

```
        you type a task
              |
              v
   +---------------------+        clipboard        +------------------+
   |  chat page (browser)|  ------------------->   |  Undo agent      |
   |  + extension        |                         |  (local, python) |
   |    content.js       |  <------------------    |                  |
   +---------------------+        result           +------------------+
              |
              v
      result pasted back into the chat
```

The agent polls the clipboard about every 700 ms. A message that starts with a known marker is treated as a command. Everything else is ignored.

### Command markers

| Marker | Purpose |
|---|---|
| `FILE: path` | create or overwrite a file |
| `EDIT: path` | replace an exact fragment |
| `APPEND: path` | add text to the end of a file |
| `DELETE: path` | remove a file |
| `MKDIR: path` | create a folder |
| `COPY:` / `MOVE:` / `RENAME:` | file operations |
| `CMD: <command>` | run through cmd.exe and wait for the output |
| `PS: <script>` | run through PowerShell and wait for the output |
| `RUN: path` | start a program or script |
| `READ:` / `HEAD:` / `TAIL:` | read a file or a part of it |
| `FIND:` / `SEARCH:` / `LIST:` | search by name or content |
| `TREE:` | project structure |
| `SHOT: full` or `SHOT: "window title"` | screenshot the desktop or a window |
| `LOG: N` | last N log lines |
| `CTX:` | context snapshot |
| `MANIFEST:` | full command protocol in the agent language |
| `LANG: ru\|en\|zh` | switch agent language |
| `CALC:`, `REGEX:`, `HASH:`, `DIFF:`, `COUNT:` | helpers |

### Several commands in one answer

A single model reply can contain many command blocks. The extension does not stop at the first or the last one: it keeps a queue and executes them in order, one after another, up to the configured step limit.

### Timeouts

| Layer | Default | Notes |
|---|---|---|
| Clipboard poll | 700 ms | agent side |
| Answer stability | 900 ms | waits until the model stops typing |
| Regular result wait | 90 s | short commands |
| `CMD` / `PS` / `RUN` | 30 min | long operations, configurable |
| Agent command limit | 30 min | `cmd_timeout` in `config.json`, `0` means no limit |

---

## Project layout

```
UndoBridge/
  extension/          browser extension (MV3)
    manifest.json     permissions, side panel, content scripts
    content.js        chat bridge: reads answers, queues commands, sends results
    content.css       floating panel styles
    i18n.js           en / ru / zh dictionary
    popup.html/.js    compact popup
    panel.html/.js    full side panel
    bg.js             background worker
  installer/
    Setup.cs          setup wizard (C#), bundles the agent executable
  build.ps1           builds extension zip and the installer
  dist/               build output
```

The agent itself (`Undo.py`) lives in the repository root: one file, standard library plus `Pillow`, `pyperclip` and `pywin32` for window capture.

---

## Build from source

Agent (standalone executable, no Python needed on the target machine):

```powershell
python -m PyInstaller --noconfirm --onefile --windowed --name UndoBridgeAgent `
  --distpath UndoBridge\dist\engine --workpath UndoBridge\build\pyi `
  --specpath UndoBridge\build\pyi repo\Undo.py
```

Installer:

```powershell
powershell -ExecutionPolicy Bypass -File UndoBridge\build.ps1
```

The script packs the extension into a zip, embeds it and the agent executable as resources, and compiles `UndoBridge_Setup.exe` with the C# compiler shipped with Windows.

---

## Security notes

- The agent executes commands on your machine. Treat it as a shell with your permissions.
- Everything travels through the local clipboard; no traffic leaves your computer for the bridge itself.
- The agent only reacts to messages that start with a known marker.
- Destructive file edits keep a backup in `Nepizdin/backups` and can be restored with `RESTORE:`.
- The setup wizard does not install a service and does not require administrator rights.

---

## Known limits

- Chromium browsers only. Firefox has a different extension model and needs a separate build.
- The unpacked extension must be loaded once by hand: Chromium does not allow silent installation of unpacked extensions.
- The agent executable is about 57 MB because it carries the whole Python runtime.
- `SHOT:` needs a composited desktop; some fullscreen games return a black frame.
