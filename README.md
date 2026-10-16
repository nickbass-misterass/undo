markdown
# Undo — Command Station for AI

> A local agent that lets any AI assistant work with the files on your PC
> through the clipboard. No API keys. No network. No telemetry.

[![Latest release](https://img.shields.io/github/v/release/nickbass-misterass/undo)](https://github.com/nickbass-misterass/undo/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/nickbass-misterass/undo)

**Website:** https://undo-f2l.pages.dev

## What it is

Undo is a terminal-style desktop application. You run it locally. The AI writes a command. You copy it into the clipboard. Undo reads the clipboard, executes the command on disk and writes the result back. You paste the result into the chat. That is the whole protocol.

No server. No account. No internet needed. No API keys. No telemetry. Everything happens on your machine.

## Install

Download the installer:

**[Download Undo_Setup.exe](https://github.com/nickbass-misterass/undo/releases/download/v1.0/Undo_Setup.exe)** (39 MB)

Runs on Windows 10 / 11 (64-bit). No Python required.

Or build from source with Python 3.10+:
python Undo.py

## What it can do

- **Files** — `FILE`, `EDIT`, `APPEND`, `DELETE`, `RENAME`, `MKDIR`, `TOUCH`
- **Read & search** — `READ`, `HEAD`, `TAIL`, `STAT`, `FIND`, `SEARCH`, `REGEX`, `LIST`, `TREE`, `COUNT`, `HASH`
- **Backups** — automatic before every change; `BACKUPS`, `RESTORE`, `DIFF`, `CLEANUP`, `BACKUP_RULE`
- **Checks** — syntax check for Python, JSON, HTML, JS after every EDIT
- **Screenshots** — `SHOT:` takes an image and puts it in the clipboard
- **Terminal** — any command that doesn't match an Undo marker goes to `cmd.exe`
- **Project memory** — every project keeps a `.agent/` folder with description, errors, notes, tasks and a log; `CTX:` assembles it for the AI
- **18 code templates** — `SNIPPET:` inserts ready-to-use skeletons
- **Safe edits** — atomic, idempotent, line-ending safe
- **Self-edit** — edit Undo with Undo; it restarts itself in place
- **Three languages** — EN / RU / ZH

## How it works

1. AI writes a command in a ``` code block
2. You click the **Copy** button
3. Undo reads the clipboard every 700 ms
4. It runs the command, saves a backup first, and puts the result back into the clipboard
5. You paste the result into the chat — the AI continues

## Manifest for AI

Undo ships with a manifest that tells any AI assistant how to use it. Inside the program: **AI menu → Copy manifest for AI**. Paste it into any chat, and the AI instantly knows all the commands and rules.

Works with ChatGPT, Claude, DeepSeek, Gemini, and local LLM frontends.

## License

MIT — take it, fork it, ship it, sell it.

## Contact

NickBass@bk.ru
