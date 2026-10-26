# -*- coding: utf-8 -*-
"""UNDO — Terminal 640x480"""
import os, sys, json, time, re, shutil, subprocess, threading, queue, traceback
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

if getattr(sys, "frozen", False):
    # onefile-exe: __file__ лежит во временной папке распаковки,
    # поэтому конфиг, логи, бэкапы и снимки держим рядом с самим exe
    AGENT_DIR = Path(sys.executable).parent.resolve()
else:
    AGENT_DIR = Path(__file__).parent.resolve()
DATA_DIR = AGENT_DIR / "Nepizdin"
CONFIG_FILE = DATA_DIR / "config.json"
LOGS_DIR = DATA_DIR / "logs"
BACKUPS_DIR = DATA_DIR / "backups"
SHOTS_DIR = DATA_DIR / "shots"
for p in [DATA_DIR, LOGS_DIR, BACKUPS_DIR, SHOTS_DIR]:
    try: p.mkdir(parents=True, exist_ok=True)
    except: pass

DEFAULT_CONFIG = {
    "font_family": "Consolas",
    "font_size": 11,
    "window_geometry": "640x480",
    "recent_projects": [],
    "current_project": None,
    "language": "en",
    "auto_ctx_min": 0,
    "cmd_timeout": 1800,
    "headless": False,
}
def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items(): cfg.setdefault(k, v)
        return cfg
    except: return dict(DEFAULT_CONFIG)
def save_config(cfg):
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except: pass
CFG = load_config()
try:
    if not CONFIG_FILE.exists():
        save_config(CFG)   # первый запуск: сразу создаём файл настроек
except Exception:
    pass

try:
    import pyperclip
    HAS_PC = True
except ImportError:
    HAS_PC = False

def clip_get():
    if HAS_PC:
        try: return pyperclip.paste() or ""
        except: pass
    try:
        r = tk.Tk(); r.withdraw()
        t = r.clipboard_get(); r.destroy(); return t
    except: return ""

def clip_set(text):
    if HAS_PC:
        try: pyperclip.copy(text); return True
        except: pass
    try:
        r = tk.Tk(); r.withdraw()
        r.clipboard_clear(); r.clipboard_append(text)
        r.update(); r.destroy(); return True
    except: return False

_CMD_RE = re.compile(
    r'^(?://|<!--|#|--)?\s*'
    r'(FILE|EDIT|APPEND|DELETE|CMD|MKDIR|COPY|MOVE|RENAME|TOUCH|'
    r'TREE|READ|FIND|SEARCH|LIST|HEAD|TAIL|STAT|DIFF|COUNT|HASH|'
    r'BACKUPS|RESTORE|CLEANUP|BACKUP_RULE|REMIND|CALC|CHECK|SNIPPET|'
    r'CTX|NOTE|TASK|SHOT|LOG|REGEX|LANG|MANIFEST|PS):'
    r'\s*(.*)$', re.I
)
def detect_mode(text):
    lines = text.splitlines()
    if not lines: return None, None, None
    m = _CMD_RE.match(lines[0])
    if not m: return None, None, None
    return m.group(1).lower(), (m.group(2) or "").strip(), "\n".join(lines[1:])

def parse_edit_blocks(payload):
    blocks = []
    pat = re.compile(
        r'<<<FIND>>>\s*\n(.*?)\n<<<REPLACE>>>\s*\n(.*?)(?=\n<<<FIND>>>|\n<<<END>>>|$)',
        re.DOTALL
    )
    for m in pat.finditer(payload):
        fnd = m.group(1).rstrip('\n'); rpl = m.group(2).rstrip('\n')
        if fnd.strip(): blocks.append((fnd, rpl))
    return blocks

def resolve_path(rel):
    p = Path(rel)
    if p.is_absolute(): return p
    proj = CFG.get("current_project")
    if proj: return Path(proj) / rel
    return AGENT_DIR / rel

def make_backup(p, tag=""):
    if not p.exists(): return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = BACKUPS_DIR / (p.name + ".bak_" + tag + ts)
    try: shutil.copy2(p, b); return b
    except: return None

def save_file(rel, content):
    target = resolve_path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    bak = make_backup(target, "file_")
    target.write_text(content, encoding="utf-8")
    return target, bak

def apply_edits(rel, blocks):
    target = resolve_path(rel)
    if not target.exists(): return False, "file not found: " + str(target)
    try: original = target.read_text(encoding="utf-8")
    except Exception as e: return False, "read error: " + str(e)

    # Validate blocks cumulatively: each FIND is checked against
    # the result of all previous replacements, not the original.
    preview = original
    missing = []
    for i, (fnd, rpl) in enumerate(blocks):
        if fnd in preview:
            preview = preview.replace(fnd, rpl)
            continue
        # CRLF/LF fallback
        fnd_n = fnd.replace("\r\n", "\n")
        prev_n = preview.replace("\r\n", "\n")
        if fnd_n in prev_n:
            preview = prev_n.replace(fnd_n, rpl.replace("\r\n", "\n"))
            continue
        missing.append(i+1)
    if missing:
        return False, "NOT FOUND blocks: " + str(missing)

    # All blocks validated — apply for real on the original
    bak = make_backup(target, "edit_")
    result = original; applied = 0
    for fnd, rpl in blocks:
        cnt = result.count(fnd)
        if cnt == 0:
            fnd_n = fnd.replace("\r\n", "\n"); res_n = result.replace("\r\n", "\n")
            if fnd_n in res_n:
                result = res_n.replace(fnd_n, rpl.replace("\r\n", "\n"))
                applied += 1
            else:
                continue
        else:
            result = result.replace(fnd, rpl); applied += 1
    try: target.write_text(result, encoding="utf-8")
    except Exception as e: return False, "write error: " + str(e)
    suffix = (" | bak: " + bak.name) if bak else ""
    return True, "applied " + str(applied) + "/" + str(len(blocks)) + suffix

def read_file_impl(arg, mode='read', n=None, rng=None):
    target = resolve_path(arg)
    if not target.exists(): return False, "no file: " + arg
    try: text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e: return False, "read err: " + str(e)
    lines = text.splitlines(); total = len(lines)
    if rng:
        a,b = rng; chunk = lines[max(0,a-1):min(total,b)]
        header = "[READ] " + arg + " lines " + str(max(1,a)) + "-" + str(min(total,b)) + " of " + str(total)
        start = max(1,a)
    elif mode == 'head':
        chunk = lines[:n]; header = "[HEAD] " + arg + " first " + str(n) + " of " + str(total); start = 1
    elif mode == 'tail':
        chunk = lines[-n:]; header = "[TAIL] " + arg + " last " + str(n) + " of " + str(total)
        start = total - min(total,n) + 1
    else:
        chunk = lines; header = "[READ] " + arg + " (" + str(total) + " lines)"; start = 1
    out = [header, "-" * 60]
    for i, ln in enumerate(chunk): out.append("{:5d} | {}".format(start+i, ln))
    out.append("-" * 60)
    return True, "\n".join(out)

def tree_impl():
    root = Path(CFG.get("current_project")) if CFG.get("current_project") else AGENT_DIR
    out = ["[TREE] " + str(root), "-" * 50]
    def walk(path, prefix="", depth=0):
        if depth > 2: return
        try: items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except: return
        for p in items:
            if p.name in (".git","__pycache__","node_modules"): continue
            if p.is_dir():
                try: cnt = sum(1 for _ in p.rglob("*") if _.is_file())
                except: cnt = 0
                out.append(prefix + "[D] " + p.name + "/  (" + str(cnt) + ")")
                walk(p, prefix + "   ", depth+1)
            else:
                try: sz = p.stat().st_size
                except: sz = 0
                szs = "{:.1f}K".format(sz/1024) if sz > 1024 else str(sz) + "B"
                out.append(prefix + "[F] " + p.name + "  (" + szs + ")")
    walk(root)
    return True, "\n".join(out)

def list_impl(arg):
    base = Path(CFG.get("current_project")) if CFG.get("current_project") else AGENT_DIR
    target = Path(arg) if Path(arg).is_absolute() else (base / arg)
    if not target.exists() or not target.is_dir(): return False, "no dir: " + str(target)
    out = ["[LIST] " + str(target), "-" * 50]
    try: items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except Exception as e: return False, "err: " + str(e)
    for p in items:
        if p.name in (".git","__pycache__"): continue
        if p.is_dir():
            try: cnt = sum(1 for _ in p.rglob("*") if _.is_file())
            except: cnt = 0
            out.append("[D] " + p.name + "/  (" + str(cnt) + ")")
        else:
            try: sz = p.stat().st_size
            except: sz = 0
            szs = "{:.1f}K".format(sz/1024) if sz > 1024 else str(sz) + "B"
            out.append("[F] " + p.name + "  (" + szs + ")")
    return True, "\n".join(out)

def find_in_file_impl(arg):
    if "|" not in arg: return False, "format: FIND: file | text"
    parts = arg.split("|", 1); fname = parts[0].strip(); needle = parts[1].strip()
    if not needle: return False, "empty search"
    target = resolve_path(fname)
    if not target.exists(): return False, "no file: " + fname
    try: text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e: return False, "err: " + str(e)
    lines = text.splitlines(); nl = needle.lower()
    hits = [i for i, ln in enumerate(lines) if nl in ln.lower()]
    if not hits: return True, "[FIND] '" + needle + "' not found in " + fname
    out = ["[FIND] '" + needle + "' in " + fname + " -> " + str(len(hits)), "-" * 50]
    for h in hits[:30]:
        a = max(0,h-3); b = min(len(lines), h+4)
        for i in range(a,b):
            mark = ">" if i == h else " "
            out.append(mark + " {:5d} | {}".format(i+1, lines[i]))
        out.append("." * 40)
    return True, "\n".join(out)

def stat_impl(arg):
    target = resolve_path(arg)
    if not target.exists(): return False, "no file: " + arg
    st = target.stat()
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.count("\n")+1; chars = len(text)
    except: lines = chars = "?"
    sz = st.st_size
    szs = "{:.1f}K".format(sz/1024) if sz > 1024 else str(sz) + "B"
    return True, ("[STAT] " + arg + "\n" + "-"*50 +
                  "\n  size:    " + szs +
                  "\n  lines:   " + str(lines) +
                  "\n  chars:   " + str(chars) +
                  "\n  changed: " + mtime)

def agent_dir_for(project_path):
    return Path(project_path) / ".agent"

def ensure_agent_meta(project_path):
    if not project_path: return None
    ad = agent_dir_for(project_path)
    ad.mkdir(parents=True, exist_ok=True)
    defaults = {
        "project.prj": json.dumps({"name": Path(project_path).name,
                                    "path": str(project_path),
                                    "created": datetime.now().isoformat()},
                                   ensure_ascii=False, indent=2),
        "desc.md": "# Project\n\n(no description)\n",
        "context.json": json.dumps({"errors":[],"solutions":[],"notes":[]},
                                    ensure_ascii=False, indent=2),
        "tasks.json": json.dumps({"todo":[],"done":[]},
                                  ensure_ascii=False, indent=2),
        "project.log": "",
    }
    for fname, content in defaults.items():
        f = ad / fname
        if not f.exists(): f.write_text(content, encoding="utf-8")
    return ad

def add_note(project_path, text):
    ad = ensure_agent_meta(project_path)
    if not ad: return False, "no project"
    ctx_file = ad / "context.json"
    try: ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
    except: ctx = {"errors":[],"solutions":[],"notes":[]}
    ctx.setdefault("notes",[]).append({"ts": datetime.now().isoformat(), "text": text})
    ctx_file.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, "note added"

def task_add(project_path, text):
    ad = ensure_agent_meta(project_path)
    if not ad: return False, "no project"
    tf = ad / "tasks.json"
    try: tasks = json.loads(tf.read_text(encoding="utf-8"))
    except: tasks = {"todo":[],"done":[]}
    new_id = len(tasks.get("todo",[])) + len(tasks.get("done",[])) + 1
    tasks.setdefault("todo",[]).append({"id": new_id, "text": text,
                                         "created": datetime.now().isoformat()})
    tf.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, "task #" + str(new_id) + " added"

def task_done(project_path, n):
    ad = ensure_agent_meta(project_path)
    if not ad: return False, "no project"
    tf = ad / "tasks.json"
    try: tasks = json.loads(tf.read_text(encoding="utf-8"))
    except: return False, "no tasks.json"
    todo = tasks.get("todo",[])
    for i, t in enumerate(todo):
        if t.get("id") == n:
            t["done_at"] = datetime.now().isoformat()
            tasks.setdefault("done",[]).append(t)
            todo.pop(i)
            tf.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
            return True, "task #" + str(n) + " done"
    return False, "no task #" + str(n)

def task_list(project_path):
    ad = ensure_agent_meta(project_path)
    if not ad: return False, "no project"
    tf = ad / "tasks.json"
    try: tasks = json.loads(tf.read_text(encoding="utf-8"))
    except: return False, "no tasks.json"
    out = ["[TASKS]", "-" * 50, "todo:"]
    if tasks.get("todo"):
        for t in tasks["todo"]: out.append("  [ ] #" + str(t.get("id","?")) + "  " + t.get("text",""))
    else: out.append("  (empty)")
    out.append(""); out.append("done:")
    if tasks.get("done"):
        for t in tasks["done"]: out.append("  [x] #" + str(t.get("id","?")) + "  " + t.get("text",""))
    else: out.append("  (empty)")
    return True, "\n".join(out)

def collect_context(project_path):
    if not project_path: return False, "no project"
    ad = ensure_agent_meta(project_path)
    if not ad: return False, "cannot create .agent/"
    parts = ["="*60, "PROJECT CONTEXT", "="*60, "path: " + str(project_path), ""]
    for fname, title in [("project.prj","META"),("desc.md","DESC"),
                          ("context.json","CONTEXT"),
                          ("tasks.json","TASKS"),("project.log","LOG")]:
        f = ad / fname
        if f.exists():
            parts.append("-"*60); parts.append("## " + title); parts.append("-"*60)
            parts.append(f.read_text(encoding="utf-8", errors="replace")); parts.append("")
    return True, "\n".join(parts)

def find_window(title):
    """Окно по части заголовка. Из нескольких подходящих берём самое крупное
    видимое и не свёрнутое — иначе можно поймать служебную панель."""
    try: import win32gui
    except ImportError: return None
    want = (title or "").strip().lower()
    hits = []
    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h): return True
            text = win32gui.GetWindowText(h) or ""
            if not text: return True
            if want and want not in text.lower(): return True
            if win32gui.IsIconic(h):
                # свёрнутое окно тоже подходит — перед снимком развернём
                hits.append((1, 0, h, text))
                return True
            l, t, r, b = win32gui.GetWindowRect(h)
            if r - l < 40 or b - t < 40: return True
            hits.append((0, (r - l) * (b - t), h, text))
        except Exception:
            pass
        return True
    try: win32gui.EnumWindows(cb, None)
    except Exception: return None
    if not hits: return None
    hits.sort(key=lambda x: (x[0], -x[1]))
    return hits[0][2]

_DPI_READY = False

def _ensure_dpi_aware():
    """Без этого координаты окна приходят в логических пикселях, а снимок —
    в физических. На мониторах с разным масштабом окно снимается со сдвигом
    или чёрным кадром. Ставим per-monitor DPI awareness (и на поток тоже —
    если процесс уже создал окно, установка на процесс не сработает)."""
    global _DPI_READY
    if _DPI_READY: return
    _DPI_READY = True
    try:
        import ctypes
        u = ctypes.windll.user32
        for name in ("SetProcessDpiAwarenessContext", "SetThreadDpiAwarenessContext"):
            try:
                fn = getattr(u, name, None)
                if fn: fn(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
            except Exception:
                pass
        try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception: pass
        try: u.SetProcessDPIAware()
        except Exception: pass
    except Exception:
        pass

def _virtual_origin():
    """Левый верхний угол виртуального экрана. У второго монитора он бывает
    отрицательным — без этой поправки область снимка уезжает."""
    try:
        import win32api
        return (win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77))
    except Exception:
        return (0, 0)

def _grab_region(l, t, r, b):
    """Кусок виртуального экрана по любой области, включая второй монитор."""
    try:
        from PIL import ImageGrab
        full = ImageGrab.grab(all_screens=True)
        if full is None: return None
        vx, vy = _virtual_origin()
        x1, y1, x2, y2 = l - vx, t - vy, r - vx, b - vy
        x1 = max(0, min(full.width - 1, int(x1)))
        x2 = max(0, min(full.width, int(x2)))
        y1 = max(0, min(full.height - 1, int(y1)))
        y2 = max(0, min(full.height, int(y2)))
        if x2 - x1 < 4 or y2 - y1 < 4: return None
        return full.crop((x1, y1, x2, y2))
    except Exception:
        return None

def _looks_blank(img):
    """Полностью одноцветный кадр — признак того, что область взята не та."""
    try:
        return img.getbbox() is None
    except Exception:
        return False

def _is_covered(hwnd, l, t, r, b):
    """Перекрыто ли окно: если в контрольных точках сверху оказывается другое
    окно, снимать с экрана нельзя — в кадр попадёт чужое содержимое
    (частая беда, когда окно лежит на другом мониторе под браузером)."""
    try:
        import win32gui
        pts = (((l + r) // 2, (t + b) // 2),
               (l + 8, t + 8), (r - 8, b - 8))
        for x, y in pts:
            h = win32gui.WindowFromPoint((x, y))
            if not h:
                continue
            try: root = win32gui.GetAncestor(h, 2)   # GA_ROOT
            except Exception: root = h
            if root != hwnd:
                return True
        return False
    except Exception:
        return False

def _print_window(hwnd, client=True):
    """Снимок самого окна через PrintWindow: работает, даже если окно
    на другом мониторе, перекрыто или частично за краем экрана."""
    try:
        import ctypes
        import win32gui, win32ui
        from PIL import Image
        l, t, r, b = win32gui.GetClientRect(hwnd) if client else win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w < 8 or h < 8: return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        # PW_RENDERFULLCONTENT (2) — иначе аппаратно ускоренные окна выходят чёрными
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bits, "raw", "BGRX", 0, 1)
        try: win32gui.DeleteObject(bmp.GetHandle())
        except Exception: pass
        try: save_dc.DeleteDC(); mfc_dc.DeleteDC()
        except Exception: pass
        try: win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception: pass
        return img
    except Exception:
        return None

def shot_screen(mode="window", title=None, save_path=None, delay=0, client=True):
    """Снимок окна (по умолчанию — клиентская область без рамки и заголовка)
    либо всего экрана при mode="full".
    Возвращает (ok, path_or_error, (width, height))."""
    _ensure_dpi_aware()
    if delay and delay > 0: time.sleep(delay)
    try: from PIL import ImageGrab
    except ImportError: return False, "PIL not installed", (0, 0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(save_path) if save_path else (SHOTS_DIR / ("shot_" + ts + ".png"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = None
    if mode == "window":
        try:
            import win32gui
            hwnd = find_window(title) if title else None
            if not hwnd: hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                try:
                    if win32gui.IsIconic(hwnd): win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                except Exception:
                    pass
                try: win32gui.SetForegroundWindow(hwnd)
                except Exception: pass
                time.sleep(0.3)
                if client:
                    try:
                        import win32api
                        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
                        l, t = win32api.ClientToScreen(hwnd, (cl, ct))
                        r, b = win32api.ClientToScreen(hwnd, (cr, cb))
                    except Exception:
                        l, t, r, b = win32gui.GetWindowRect(hwnd)
                else:
                    l, t, r, b = win32gui.GetWindowRect(hwnd)
                if r - l >= 20 and b - t >= 20:
                    if _is_covered(hwnd, l, t, r, b):
                        # окно лежит под другими — снимаем само окно, а не область
                        # экрана, иначе в кадр попадёт чужое содержимое
                        img = _print_window(hwnd, client)
                    if img is None or _looks_blank(img):
                        # с экрана: точные пиксели на любом мониторе, включая
                        # отрицательные координаты второго экрана
                        img = _grab_region(l, t, r, b)
                    if img is None or _looks_blank(img):
                        alt = _print_window(hwnd, client)
                        if alt is not None and not _looks_blank(alt):
                            img = alt
        except Exception:
            img = None
    if img is None: img = ImageGrab.grab(all_screens=True)
    try: img.save(str(out_path))
    except Exception as e: return False, "save err: " + str(e), (0, 0)
    return True, str(out_path), img.size

def shot_to_clipboard(path, text=None):
    """Кладём скриншот в буфер как картинку (CF_DIB) и, если задан text,
    рядом — текстовую метку с путём. Text нужен мосту: он читает буфер
    как строку, чтобы понять, что пришёл результат, и вставить картинку в чат."""
    try:
        from PIL import Image
        import win32clipboard
        import io
        img = Image.open(path)
        output = io.BytesIO()
        img.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]
        output.close()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            if text:
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


SNIPPETS = {
    "python_class": '''class MyClass:
    """docstring"""
    def __init__(self, value=0):
        self.value = value

    def __repr__(self):
        return f"MyClass({self.value})"
''',

    "python_cli": '''import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="input file")
    parser.add_argument("-o", "--output", default="out.txt")
    args = parser.parse_args()
    print("input:", args.input, "output:", args.output)

if __name__ == "__main__":
    main()
''',

    "python_http": '''import requests

def get(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()
''',

    "python_file_io": '''from pathlib import Path

def read_text(path):
    return Path(path).read_text(encoding="utf-8")

def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")
''',

    "python_sqlite": '''import sqlite3

conn = sqlite3.connect("data.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
)""")
conn.commit()
''',

    "html_basic": '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Page</title>
<style>
  body { font-family: sans-serif; margin: 20px; }
</style>
</head>
<body>
<h1>Hello</h1>
</body>
</html>
''',

    "html_canvas": '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Canvas</title></head>
<body>
<canvas id="c" width="800" height="600"></canvas>
<script>
const ctx = document.getElementById("c").getContext("2d");
ctx.fillStyle = "#222";
ctx.fillRect(0, 0, 800, 600);
ctx.fillStyle = "#0f0";
ctx.beginPath();
ctx.arc(400, 300, 50, 0, Math.PI * 2);
ctx.fill();
</script>
</body>
</html>
''',

    "js_class": '''class MyClass {
  constructor(value = 0) {
    this.value = value;
  }
  toString() {
    return `MyClass(${this.value})`;
  }
}
''',

    "js_fetch": '''async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status);
  return await r.json();
}
''',

    "c_main": '''#include <stdio.h>

int main(void) {
    printf("Hello, world!\\n");
    return 0;
}
''',

    "cpp_class": '''#include <iostream>
#include <string>

class MyClass {
public:
    MyClass(int v) : value(v) {}
    void print() const { std::cout << value << "\\n"; }
private:
    int value;
};

int main() {
    MyClass x(42);
    x.print();
    return 0;
}
''',

    "cs_class": '''using System;

class MyClass {
    public int Value { get; set; }
    public MyClass(int value = 0) { Value = value; }
    public override string ToString() => $"MyClass({Value})";
}
''',

    "powershell_admin": '''# PowerShell admin snippet
$ErrorActionPreference = "Stop"
Get-Process | Where-Object { $_.CPU -gt 100 } | Select-Object Name, CPU
''',

    "bash_script": '''#!/usr/bin/env bash
set -euo pipefail

echo "hello"
''',

    "python_excel_read": '''# -*- coding: utf-8 -*-
# pip install openpyxl
import openpyxl

def read_xlsx(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        print("=== sheet:", sheet, "===")
        for row in ws.iter_rows(values_only=True):
            print(row)

if __name__ == "__main__":
    read_xlsx("data.xlsx")
''',

    "python_excel_write": '''# -*- coding: utf-8 -*-
# pip install openpyxl
import openpyxl

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Sheet1"
ws.append(["ID", "Name", "Value"])
ws.append([1, "Item A", 100])
ws.append([2, "Item B", 200])
wb.save("output.xlsx")
print("saved output.xlsx")
''',

    "python_1c_com": '''# -*- coding: utf-8 -*-
# Требует установленной 1С:Предприятие на этой машине.
# pip install pywin32
import win32com.client

# Подключение к 1С через COM
connector = win32com.client.Dispatch("V83.COMConnector")
# Параметры подключения зависят от версии и типа базы (файловая/серверная)
conn = connector.Connect('File="C:\\\\Base1C"; Usr="Admin"; Pwd="";')

# Пример запроса
query = conn.NewObject("Query")
query.Text = "SELECT FIRST 10 Наименование FROM Справочник.Номенклатура"
result = query.Execute().Unload()
for row in result:
    print(row[0])
''',

    "python_1c_http": '''# -*- coding: utf-8 -*-
# Запрос к 1С через HTTP-сервис (публикация базы на веб-сервере).
# pip install requests
import requests

URL = "http://server/base/hs/myapi/items"
HEADERS = {"Content-Type": "application/json"}

def get_items():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def post_item(data):
    r = requests.post(URL, json=data, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    print(get_items())
''',
}

def check_file(path):
    """Проверка синтаксиса по типу файла."""
    p = Path(path)
    if not p.exists(): return False, "file not found: " + str(p)
    ext = p.suffix.lower()

    if ext == ".py":
        try:
            import py_compile
            # compile to a temp file, so we don't touch the project's __pycache__
            import tempfile, os as _os
            try:
                fd, tmp = tempfile.mkstemp(suffix=".pyc")
                _os.close(fd)
                py_compile.compile(str(p), doraise=True, cfile=tmp)
                return True, "python OK"
            finally:
                try: _os.unlink(tmp)
                except Exception: pass
        except py_compile.PyCompileError as e:
            return False, "python SYNTAX ERROR:\n" + str(e)
        except Exception as e:
            return False, "python check err: " + str(e)

    if ext == ".json":
        try:
            json.loads(p.read_text(encoding="utf-8"))
            return True, "json OK"
        except Exception as e:
            return False, "json ERROR: " + str(e)

    if ext in (".html", ".htm"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            problems = []
            if "</html>" not in text.lower(): problems.append("no </html>")
            if text.count("<script") != text.count("</script"): problems.append("script mismatch")
            if problems: return False, "html WARN: " + ", ".join(problems)
            return True, "html OK"
        except Exception as e:
            return False, "html ERROR: " + str(e)

    if ext in (".js", ".mjs"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if text.count("{") != text.count("}"): return False, "js WARN: braces"
            if text.count("(") != text.count(")"): return False, "js WARN: parens"
            return True, "js OK (basic)"
        except Exception as e:
            return False, "js ERROR: " + str(e)

    return True, "no checker for " + ext


# ============================================================
#   TERMINAL
# ============================================================
BG = "#000000"
FG = "#00ff00"
DIM = "#008800"
WARN = "#ffff00"
ERR = "#ff4444"
BUF = "#00ddff"

MANIFEST = """# UNDO — манифест для ИИ

## Кто я
Локальный агент на компьютере пользователя. Работает в терминальном
окне (чёрный фон, зелёные буквы). Принимаю команды через буфер обмена,
выполняю их на диске, возвращаю результат в буфер.

Это единственный инструмент, через который ИИ может работать с файлами.
Никаких прямых доступов к диску у ИИ нет. Всё — через команды в буфере.

## Как выглядит окно
- Сверху меню: File / Edit / Project / Tools / Settings / Help / AI
- Основное поле — лог в реальном времени:
    clip(N) > ...       что прочитано из буфера (первые 70 символов)
    > COMMAND: ...      какая команда выполняется
    результат           вывод команды
    buffer < ...        что положено обратно в буфер
- Снизу строка ввода ">" — можно вводить команды вручную.
- Меню Tools → скриншоты и открытие папок (data, project, backups, shots).
- Меню Edit → Copy log / Clear log / Show clipboard / Paste to input.
- Меню Settings → размер шрифта (11/13/16) и язык (RU / EN / ZH).
- Меню AI → Show manifest / Copy manifest.
- Меню Help → Commands (список) и About.
- Смена языка — Настройки → Язык. Ундо сам перезапустится.

## Кнопки копирования
- В окнах манифеста, команд, задач и контекста есть кнопка "📋 Copy".
- В меню Edit → Copy log — кладёт весь лог в буфер.
- В меню Edit → Show clipboard — показывает что сейчас в буфере.
- Всякий результат команды Undo кладёт в буфер автоматически.

## Как работает буфер (главный механизм)
1. ИИ пишет пользователю команду в блоке ```...```.
2. У блока есть кнопка Copy в чате.
3. Пользователь нажимает Copy — команда в буфере.
4. Undo читает буфер каждые 700 мс.
5. Если первая строка — маркер (FILE:, EDIT:, ...) — Undo выполняет.
6. Результат кладётся обратно в буфер.
7. Пользователь вставляет результат в чат (Ctrl+V).
8. ИИ видит результат и идёт дальше.

Ничего не подтверждается вручную. Только копирование и вставка.
Если первая строка не маркер — Undo пишет "(no command marker)"
и ничего не делает.

## Структура проекта
Каждый проект хранит свою мету в папке .agent/:
  project.prj    — мета (имя, путь, дата)
  desc.md        — текстовое описание идеи
  context.json   — ошибки, решения, заметки
  tasks.json     — задачи (todo / done)
  project.log    — лог проекта

## Команды
Первая строка — маркер. Остальные строки — тело.
Путь можно давать абсолютным (C:\\folder\\file.py)
или относительным (src/main.py) — тогда ищется в проекте.

== Файлы ==
  FILE: path             создать или перезаписать файл
                         (тело — содержимое)
  EDIT: path             правка блоками:
                           <<<FIND>>>
                           что найти
                           <<<REPLACE>>>
                           на что заменить
                           <<<END>>>
                         можно несколько блоков в одном EDIT
  APPEND: path           дописать в конец (тело — что дописать)
  DELETE: path           удалить (с бэкапом)
  RENAME: old -> new     переименовать
  TOUCH: path            создать пустой файл
  MKDIR: path            создать папку (включая вложенные)

== Чтение и поиск ==
  READ: path             прочитать с номерами строк
  READ: path | 50-120    только строки с 50 по 120
  HEAD: path | N         первые N строк
  TAIL: path | N         последние N строк
  STAT: path             размер, дата, число строк, символов
  FIND: path | text      поиск в одном файле (регистр не важен)
  SEARCH: text           поиск по всему проекту (до 100 совпадений)
  LIST: path             список файлов в папке
  TREE:                  дерево проекта на 3 уровня
  COUNT: path            строки, слова, символы
  HASH: path             md5 и sha1 файла

== Бэкапы ==
  BACKUPS: [path]        список бэкапов (все или для одного файла)
  RESTORE: path | N      откат к N-му бэкапу (1 = самый свежий)
                         перед откатом делается снимок текущего
  DIFF: path             unified diff с последним бэкапом
  CLEANUP: keep=N        удалить старые (оставить N свежих на файл)

== Скриншоты (SHOT) ==
  SHOT:                  активное окно -> картинка в буфер + метка IMAGE: путь WxH
  SHOT: "Имя окна"       нужное окно по части заголовка (SHOT: "Donkey Kong")
  SHOT: full             весь экран
  SHOT: wait=N           снимок через N секунд (SHOT: wait=3)
  SHOT: save=path.png    сохранить по указанному пути
  Снимается клиентская область окна — без рамки и заголовка.
  Файлы сохраняются в shots/ с меткой времени.
  Результат: строка IMAGE: <путь> <ширина>x<высота>; картинка одновременно
  кладётся в буфер обмена, поэтому её можно вставить в чат по Ctrl+V.
  Правило: SHOT возвращает картинку, без авто-логов.
  Если ИИ нужен лог — запрашивает отдельно: LOG: N

== Язык и протокол (LANG / MANIFEST) ==
  LANG:                  какой язык сейчас у Undo -> ответ "lang: ru"
  LANG: ru | en | zh     переключить язык Undo (ru, en, zh)
  MANIFEST:              полный текст этого протокола на языке Undo
  Нужно, чтобы браузерное дополнение и ИИ говорили на одном языке:
  дополнение спрашивает LANG: и подстраивает свой интерфейс.

== Шаблоны кода (SNIPPET) ==
  SNIPPET:               без имени — список всех шаблонов
  SNIPPET: name          положить шаблон в буфер
  Python:  python_class, python_cli, python_http,
           python_file_io, python_sqlite
  Web:     html_basic, html_canvas, js_class, js_fetch
  C/C++:   c_main, cpp_class
  C#:      cs_class
  Shell:   powershell_admin, bash_script
  Шаблон — исходник целиком. Вставляется в буфер, пользователь
  копирует и вставляет в новый файл.

== Инструменты ==
  CHECK: path            проверка синтаксиса (py / json / html / js)
  CALC: expression       калькулятор (2+2*3, 10/3, 2**8)
  CMD: run ...           запустить программу
  CMD: open ...          открыть файл
  CMD: folder            открыть папку агента
  CMD: dir/ls/...        произвольная команда ОС
  CMD: status            кратко: текущий проект и версия Python
  PS: <скрипт>           PowerShell с ожиданием: дождаться конца и вернуть вывод
                         в ответ. Для долгих задач (поиск, копирование, сборка)
                         используй PS:, а не CMD: run/start powershell —
                         они не ждут завершения и ответ будет пустым.
                         Лимит времени: cmd_timeout в config.json (по умолчанию
                         30 минут, 0 = без лимита).
  LOG: N                 последние N строк лога (по умолчанию 200)
                         log читается из logs/undo.log, файл живёт
                         между перезапусками

== Контекст, заметки, задачи ==
  CTX:                   собрать контекст проекта в буфер
                         (meta + desc + context + tasks + log)
  NOTE: text             добавить заметку в context.json проекта
  TASK: add text         добавить задачу
  TASK: done N           отметить задачу N выполненной
  TASK: list             показать задачи

## Правила для ИИ
- Каждую команду оборачивай в блок с тройными кавычками — у пользователя
  появится кнопка Copy.
- Одна команда — один блок.
- Несколько правок — несколько отдельных блоков.
- Работай только через команды Undo. Не проси пользователя делать
  что-то руками.
- Если не хватает контекста — запроси: CTX:
- После каждой EDIT Undo автоматически проверяет синтаксис:
  Python — py_compile, JSON — parse, HTML/JS — базовые проверки.

## Правила бэкапов
- Перед каждым EDIT / FILE / DELETE / RENAME Undo делает бэкап.
- Бэкапы складываются в папку backups/ с меткой времени.
- Формат имени: <файл>.bak_<тег>_<YYYYMMDD_HHMMSS>.
- Список: BACKUPS: [path]
- Откат:   RESTORE: path | N   (N = 1 — самый свежий)
- Очистка: CLEANUP: keep=N    (оставить N свежих на каждый файл)
- Откат не удаляет бэкапы. Перед откатом делается ещё один
  снимок текущего — с тегом before_restore_*.

## Пример диалога
ИИ даёт:
  FILE: src/hello.py
  print("hello")

Пользователь копирует блок в буфер.
Undo сам выполняет, кладёт в буфер "saved: src/hello.py (0.1K)".
Пользователь вставляет результат в чат.
ИИ видит, что файл создан, идёт дальше.

Если правка не прошла — Undo возвращает:
  err: NOT FOUND blocks: [1]
ИИ сначала даёт READ: path | 1-50, потом новый EDIT с точным фрагментом.

## Формат ответа ИИ
- Короткий текст, без воды.
- Команда — всегда в блоке с тройными кавычками.
- Не хватает контекста — сначала CTX:, потом решение.
- Что-то не работает — сначала LOG: 50, потом гипотеза.
- Не спрашивать "как дела" и т.п.
- Не пересказывать то, что видно из логов.

## Скины и язык
- Undo работает в терминальном скине (чёрный фон, зелёные буквы).
- Язык интерфейса: RU / EN / ZH.
- Смена языка — Настройки → Язык (Undo сам перезапустится).
- Настройки сохраняются в config.json.

## Терминальная строка внизу
Это не просто поле ввода. Если команда без маркера Undo —
она отправляется в cmd.exe и результат показывается в логе.
- dir, cd, echo, type — команды Windows
- python, pip, powershell — работают
- cd path — меняет рабочую папку для shell-команд
- clear / cls — очистить лог
- help — список команд Undo
- exit — закрыть Undo
- стрелки вверх/вниз — история команд

## Работа с буфером и клавиатурой
- Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+A — работают в любой раскладке
  (по физическому keycode, а не по букве).
- Shift+Insert — вставка, Ctrl+Insert — копирование.
- Правый клик в логе → Copy selection / Copy whole log /
  Clear log / Show clipboard / Paste to input.
- Правый клик в строке ввода → Cut / Copy / Paste /
  Select all / Clear.
- Правый клик в меню Tools → Copy context / log / manifest.

## Инструменты в меню Tools
- Screenshot (window) — активное окно
- Screenshot (full screen) — весь экран
- 📋 Copy context (CTX) — контекст проекта в буфер
- 📋 Copy log
- 📋 Copy manifest
- Show clipboard — показать текущий буфер
- Open data / project / backups / shots

## Шрифты
- VT323 — пиксельный, стиль старого терминала
- Consolas — стандартный моноширинный
- Courier New — печатная машинка
- Settings → Font: ...
- Размер: 11 / 13 / 16

## Умный поиск — REGEX
- REGEX: file | pattern
- pattern — регулярное выражение Python.
- Например: REGEX: src.py | def\s+(\w+)\(
- Возвращает до 100 совпадений с номерами строк.

## Напоминания о контексте
- Каждые N правок Undo напоминает про CTX.
- Настройка: REMIND: ctx=N | off | reset | show
- При напоминании Undo автоматически копирует
  CTX-AUTO: <контекст> в буфер — можно сразу вставить в чат с ИИ.
- По умолчанию N = 50.
- Оптимально: ctx=30 (на 30 правок — раз в сессию).

## Готовые шаблоны (18 штук)
Python:
  python_class   — класс с __init__ и __repr__
  python_cli     — argparse + main()
  python_http    — requests GET
  python_file_io — чтение/запись текста
  python_sqlite  — SQLite + CREATE TABLE
  python_excel_read   — чтение .xlsx через openpyxl
  python_excel_write  — запись .xlsx
  python_1c_com       — подключение к 1С через COM
  python_1c_http      — запрос к 1С HTTP-сервису
Web:
  html_basic     — простая страница
  html_canvas    — canvas + 2D контекст
  js_class       — ES6 класс
  js_fetch       — async fetch JSON
C/C++:
  c_main         — hello world
  cpp_class      — класс с конструктором
C#:
  cs_class       — класс с ToString()
Shell:
  powershell_admin — типовой скрипт
  bash_script      — bash-скрипт
- SNIPPET:            — список всех
- SNIPPET: name       — конкретный шаблон в буфер

## Что ИИ должен помнить всегда
1. У тебя нет прямого доступа к файлам ПК — только через Undo.
2. Пользователь — посредник. Он копирует и вставляет блоки.
3. Одна команда = один блок ```. Не смешивай.
4. Если EDIT не нашёл блок — сначала READ, потом новый EDIT.
5. Не выдумывай пути — спроси у Undo: TREE: или LIST:.
6. Проверяй результат — Undo всегда кладёт ответ в буфер.
7. Для длинных команд (CTX, TREE) Undo возвращает
   компактно: N строк в буфере.
"""

MANIFEST_EN = """# UNDO — Manifest for AI

## Who I am
Local agent on the user's PC. Terminal window (black background, green
text). I take commands via clipboard, execute them on disk, return
results to clipboard.

This is the only tool through which AI can work with files. No direct
disk access. Everything goes through clipboard commands.

## Window layout
- Top menu: File / Edit / Project / Tools / Settings / Help / AI
- Main area — live log:
    clip(N) > ...       what was read from clipboard (first 70 chars)
    > COMMAND: ...      what is being executed
    result              command output
    buffer < ...        what was put back to clipboard
- Bottom input with ">" — manual command entry.
- Tools menu -> screenshots and folder shortcuts (data/project/backups/shots).
- Edit menu -> Copy log / Clear log / Show clipboard / Paste to input.
- Settings menu -> font size (11/13/16) and language (RU / EN / ZH).
- AI menu -> Show manifest / Copy manifest.
- Help menu -> Commands (list) and About.
- Change language via Settings. Undo restarts itself.

## Copy buttons
- In chat: every ``` block has a Copy button.
- In Undo windows (manifest, tasks, commands, context): "Copy" button.
- Edit menu -> Copy log — puts the whole log into clipboard.
- Edit menu -> Show clipboard — shows current clipboard.
- Every successful Undo command puts its result back to clipboard.

## How the clipboard works (main mechanism)
1. AI writes a command to the user in a ``` block.
2. The block has a Copy button in chat.
3. User clicks Copy — command is in clipboard.
4. Undo reads clipboard every 700 ms.
5. If the first line is a marker (FILE:, EDIT:, ...) — Undo executes.
6. Result goes back to clipboard.
7. User pastes result into chat (Ctrl+V).
8. AI sees the result and continues.

Nothing is confirmed manually. Only copy and paste.
If the first line is not a marker, Undo writes "(no command marker)"
and does nothing.

## Commands
First line is a marker. Rest is the body.
Paths may be absolute (C:\\folder\\file.py) or relative (src/main.py),
relative ones are resolved inside the current project.

== Files ==
  FILE: path             create or overwrite file (body = content)
  EDIT: path             block edit:
                           <<<FIND>>>
                           text to find
                           <<<REPLACE>>>
                           text to replace
                           <<<END>>>
                         multiple blocks per EDIT allowed
  APPEND: path           append to end (body = text)
  DELETE: path           delete (with backup)
  RENAME: old -> new     rename
  TOUCH: path            create empty file
  MKDIR: path            create folder (recursive)

== Read and search ==
  READ: path             read with line numbers
  READ: path | 50-120    only lines 50..120
  HEAD: path | N         first N lines
  TAIL: path | N         last N lines
  STAT: path             size, date, line count, char count
  FIND: path | text      search in one file (case-insensitive)
  SEARCH: text           search across the whole project (100 hits max)
  LIST: path             list files in a folder
  TREE:                  project tree, 3 levels deep
  COUNT: path            lines, words, chars
  HASH: path             md5 and sha1

== Backups ==
  BACKUPS: [path]        list backups (all or for one file)
  RESTORE: path | N      restore N-th backup (1 = newest)
                         a snapshot of current file is made first
  DIFF: path             unified diff against last backup
  CLEANUP: keep=N        delete old backups (keep N newest per file)

== Screenshots (SHOT) ==
  SHOT:                  active window -> image to clipboard + label IMAGE: path WxH
  SHOT: "Window Name"    specific window by title part (SHOT: "Donkey Kong")
  SHOT: full             full screen
  SHOT: wait=N           shot after N seconds
  SHOT: save=path.png    save to a specific path
  Captures the client area of the window — no frame, no title bar.
  Files are stored in shots/ with a timestamp.
  Result: a line IMAGE: <path> <width>x<height>; the image itself is also placed
  on the clipboard, so it can be pasted into the chat with Ctrl+V.
  Rule: SHOT returns the image, no auto-logs.
  If AI needs a log — it requests separately: LOG: N

== Language and protocol (LANG / MANIFEST) ==
  LANG:                  current Undo language -> reply "lang: en"
  LANG: ru | en | zh     switch Undo language (ru, en, zh)
  MANIFEST:              full text of this protocol in the Undo language
  Keeps the browser extension and the AI on the same language:
  the extension asks LANG: and adjusts its own interface.

== Code snippets (SNIPPET) ==
  SNIPPET:               without a name — list all snippets
  SNIPPET: name          put the snippet into clipboard
  Available (18 total):
    Python:  python_class, python_cli, python_http,
             python_file_io, python_sqlite,
             python_excel_read, python_excel_write,
             python_1c_com, python_1c_http
    Web:     html_basic, html_canvas, js_class, js_fetch
    C/C++:   c_main, cpp_class
    C#:      cs_class
    Shell:   powershell_admin, bash_script
  Snippet is a complete source file. Copied to clipboard.
  User pastes it into a new file and starts editing from there.

## Typical scenarios (recipes)
Use these patterns when working with Undo.

1) Create a new file from scratch
   FILE: path/to/file.ext
   <content>

2) Edit existing file safely
   READ: path | 1-30            (see the exact text)
   EDIT: path
   <<<FIND>>>
   exact text
   <<<REPLACE>>>
   new text
   <<<END>>>

3) If EDIT fails with NOT FOUND
   READ: path | 1-80            (find real text)
   give a new EDIT with the exact fragment

4) Make a quick check after edit
   CHECK: path
   (Python -> py_compile, JSON -> parse, HTML/JS -> brace/tag check)

5) Roll back a bad edit
   BACKUPS: path                (list)
   DIFF: path                   (see what changed)
   RESTORE: path | 2            (roll back to 2nd newest)

6) Look at a fragment of a big file
   READ: path | 200-260

7) Find text across the project
   SEARCH: def main

8) Take a screenshot of a specific window
   SHOT: "Chrome"
   (image lands in clipboard, no auto-logs)

9) Reuse a template
   SNIPPET: python_cli
   (template lands in clipboard)

10) Collect project context before a big task
    CTX:
    (everything in clipboard: meta + desc + tasks + log)

11) Add a note / task while working
    NOTE: file X uses UTC, not local time
    TASK: add rewrite login screen

12) Long chains
    One command = one block. Don't combine FILE + EDIT + CHECK
    in a single clipboard payload. Copy them one after another.

## Automated checks in detail
After every EDIT Undo runs a syntax check automatically:

  .py   py_compile.compile() — full Python syntax compile
  .json json.loads() — JSON parse
  .html </html> present, <script> and </script> balanced
  .js   braces {} and parens () balanced (basic)
  other no checker, silently passes

If the check fails, the response says "CHECK FAILED: <reason>".
The file is NOT rolled back automatically — the user decides.

Manual CHECK: path always available for any file.

== Tools ==
  CHECK: path            syntax check (py / json / html / js)
  CALC: expression       calculator (2+2*3, 10/3, 2**8)
  CMD: run ...           launch a program
  CMD: open ...          open a file
  CMD: folder            open agent folder
  CMD: dir/ls/...        arbitrary OS command
  CMD: status            brief: current project and Python version
  PS: <script>           PowerShell with waiting: run it, wait for the end and
                         return the output. For long jobs (search, copying,
                         builds) use PS:, not CMD: run/start powershell — those
                         do not wait and the answer comes back empty.
                         Time limit: cmd_timeout in config.json (30 min by
                         default, 0 = no limit).
  LOG: N                 last N log lines (default 200)
                         log lives in logs/undo.log across restarts

== Context, notes, tasks ==
  CTX:                   collect project context into clipboard
                         (meta + desc + context + tasks + log)
  NOTE: text             add note to project's context.json
  TASK: add text         add task
  TASK: done N           mark task N as done
  TASK: list             list tasks

## Projects and .agent
Every project folder has its own .agent/ with:
  project.prj    — meta (name, path, created)
  desc.md        — description of the idea
  context.json   — errors, solutions, notes
  tasks.json     — todo / done
  project.log    — project log
Switch project: File -> Open Folder. Recent list under File -> Recent.

## Auto check
After every EDIT Undo checks syntax:
  Python — py_compile, JSON — parse, HTML/JS — basic brace/tag checks.
If the check fails, the response contains "CHECK FAILED".

## Example dialogue
AI:
  FILE: src/hello.py
  print("hello")

User copies the block into clipboard.
Undo executes, puts "saved: src/hello.py (0.1K)" into clipboard.
User pastes the result into chat.
AI sees the file was created and continues.

If the edit fails — Undo returns:
  err: NOT FOUND blocks: [1]
AI first runs READ: path | 1-50, then gives a new EDIT with exact text.

## AI response style
- Short text, no fluff.
- Every command wrapped in triple backticks.
- Missing context — first CTX:, then act.
- Something broken — first LOG: 50, then a hypothesis.
- Do not ask "how are you" etc.
- Do not retell what is already visible in logs.

## Skins and language
- Undo works in terminal skin (black bg, green text).
- UI language: RU / EN / ZH.
- Change via Settings -> Language.
- Settings stored in config.json.

## Terminal line at the bottom
Not just an input field. If a command has no Undo marker,
it goes to cmd.exe and the output is shown in the log.
- dir, cd, echo, type — Windows commands
- python, pip, powershell — work
- cd path — change cwd for shell commands
- clear / cls — clear log
- help — list of Undo commands
- exit — close Undo
- up/down arrows — command history

## Clipboard and keyboard
- Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+A work in any layout
  (by physical keycode).
- Shift+Insert — paste, Ctrl+Insert — copy.
- Right-click in log -> Copy selection / Copy whole log /
  Clear log / Show clipboard / Paste to input.
- Right-click in input -> Cut / Copy / Paste / Select all / Clear.

## Tools menu
- Screenshot (window) / (full screen)
- Copy context (CTX) / Copy log / Copy manifest
- Show clipboard
- Open data / project / backups / shots

## Fonts
- VT323 — pixel, terminal look
- Consolas — standard monospace
- Courier New — typewriter
- Size: 11 / 13 / 16

## Backup rules
- Before EDIT / FILE / DELETE / RENAME — backup made.
- Stored in backups/, name: file.bak_TAG_YYYYMMDD_HHMMSS
- Auto-cleanup on start: removes older than N days and keeps
  only N latest per file (default 30/30).
- Configure: BACKUP_RULE: show | keep=N | age=N | run
- Fresh backups (<1 hour) are not touched.
- List / rollback / diff:
    BACKUPS: [path]
    RESTORE: path | N
    DIFF: path
    CLEANUP: keep=N

## Smart search — REGEX
- REGEX: file | pattern   (Python regex)
- Example: REGEX: src.py | def\s+(\w+)\(

## Context reminders
- Every N changes Undo reminds about CTX.
- Configure: REMIND: ctx=N | off | reset | show
- When reminding, Undo automatically copies CTX-AUTO into clipboard.
- Default 50, recommended 30.

## Built-in snippets (18)
Python:
  python_class, python_cli, python_http,
  python_file_io, python_sqlite,
  python_excel_read, python_excel_write,
  python_1c_com, python_1c_http
Web:     html_basic, html_canvas, js_class, js_fetch
C/C++:   c_main, cpp_class
C#:      cs_class
Shell:   powershell_admin, bash_script
- SNIPPET:            — list all
- SNIPPET: name       — specific snippet into clipboard

## What AI must always remember
1. No direct file access — only through Undo.
2. The user is the mediator. They copy/paste blocks.
3. One command = one ``` block. Don't mix.
4. If EDIT fails — READ first, then a new EDIT.
5. Don't invent paths — ask Undo: TREE: or LIST:.
6. Always check the result — Undo puts it back to clipboard.
7. For long commands Undo returns a compact summary: N lines.
"""

MANIFEST_ZH = """# UNDO — AI 清单

## 我是谁
用户 PC 上的本地代理。终端窗口(黑底,绿字)。
通过剪贴板接收命令,在磁盘上执行,把结果放回剪贴板。

这是 AI 处理文件的唯一工具。没有直接磁盘访问。
一切都通过剪贴板命令。

## 窗口结构
- 顶部菜单: File / Edit / Project / Tools / Settings / Help / AI
- 主区域 — 实时日志:
    clip(N) > ...       从剪贴板读取的内容
    > COMMAND: ...      正在执行的命令
    result              命令输出
    buffer < ...        放回剪贴板的内容
- 底部输入框 ">" — 手动输入命令。
- Tools 菜单 -> 截图和文件夹快捷方式。
- Edit 菜单 -> Copy log / Clear log / Show clipboard / Paste to input。
- Settings 菜单 -> 字号 (11/13/16) 和语言 (RU / EN / ZH)。
- AI 菜单 -> Show manifest / Copy manifest。
- Help 菜单 -> Commands 和 About。
- 切换语言: Settings -> Language。Undo 会自行重启。

## 复制按钮
- 在聊天中: 每个 ``` 块都有复制按钮。
- 在 Undo 窗口 (清单, 任务, 命令, 上下文): "Copy" 按钮。
- Edit 菜单 -> Copy log — 把整个日志放入剪贴板。
- Edit 菜单 -> Show clipboard — 显示当前剪贴板。
- 每次命令成功后,结果自动放入剪贴板。

## 剪贴板如何工作 (主要机制)
1. AI 在 ``` 块中向用户写命令。
2. 该块在聊天中有复制按钮。
3. 用户点击 Copy — 命令进入剪贴板。
4. Undo 每 700 毫秒读取一次剪贴板。
5. 如果第一行是标记 (FILE:, EDIT:, ...) — Undo 执行。
6. 结果放回剪贴板。
7. 用户把结果粘贴到聊天 (Ctrl+V)。
8. AI 看到结果,继续。

不需要手动确认。只有复制和粘贴。
如果第一行不是标记,Undo 写入 "(no command marker)" 并且什么都不做。

## 命令
第一行是标记,其余是主体。
路径可以绝对 (C:\\folder\\file.py) 或相对 (src/main.py),
相对路径在当前项目中解析。

== 文件 ==
  FILE: path             创建或覆盖文件 (主体 = 内容)
  EDIT: path             块编辑:
                           <<<FIND>>>
                           要查找的文本
                           <<<REPLACE>>>
                           替换成的文本
                           <<<END>>>
                         一次 EDIT 可以包含多个块
  APPEND: path           追加到末尾 (主体 = 文本)
  DELETE: path           删除 (带备份)
  RENAME: old -> new     重命名
  TOUCH: path            创建空文件
  MKDIR: path            创建文件夹 (递归)

== 读取和搜索 ==
  READ: path             带行号读取
  READ: path | 50-120    仅 50..120 行
  HEAD: path | N         前 N 行
  TAIL: path | N         最后 N 行
  STAT: path             大小, 日期, 行数, 字符数
  FIND: path | text      在单个文件中搜索 (忽略大小写)
  SEARCH: text           整个项目中搜索 (最多 100 个匹配)
  LIST: path             列出文件夹中的文件
  TREE:                  项目树, 3 层深
  COUNT: path            行, 词, 字符
  HASH: path             md5 和 sha1

== 备份 ==
  BACKUPS: [path]        列出备份 (全部或某文件)
  RESTORE: path | N      恢复第 N 个备份 (1 = 最新)
                         恢复前会为当前文件创建快照
  DIFF: path             与最后一个备份的 unified diff
  CLEANUP: keep=N        删除旧备份 (每个文件保留 N 个最新)

== 命令 (CMD / PS) ==
  CMD: <команда>         执行系统命令并返回输出
  PS: <script>           PowerShell 并等待结束, 返回输出。
                         长时间任务 (搜索, 复制, 构建) 用 PS:, 不要用
                         CMD: run / start powershell — 它们不等待, 结果为空。
                         时间限制: config.json 的 cmd_timeout (默认 30 分钟, 0 = 无限)。

== 截图 (SHOT) ==
  SHOT:                  活动窗口 -> 图像入剪贴板
  SHOT: full             全屏 -> 图像入剪贴板
  SHOT: "Window Name"    按名称窗口 (例如 SHOT: "Chrome")
  SHOT: wait=N           N 秒后截图
  SHOT: save=path.png    保存到指定路径
  文件保存在 shots/ 目录, 带时间戳。
  规则: SHOT 只返回图像, 不带自动日志。
  如果 AI 需要日志, 单独请求: LOG: N

== 语言和协议 (LANG / MANIFEST) ==
  LANG:                  当前 Undo 语言 -> 回复 "lang: zh"
  LANG: ru | en | zh     切换 Undo 语言 (ru, en, zh)
  MANIFEST:              当前语言的完整协议文本
  让浏览器扩展和 AI 使用同一种语言:
  扩展会请求 LANG: 并同步自己的界面。

== 代码模板 (SNIPPET) ==
  SNIPPET:               不带名称 — 列出所有模板
  SNIPPET: name          把模板放入剪贴板
  可用 (共 18 个):
    Python:  python_class, python_cli, python_http,
             python_file_io, python_sqlite,
             python_excel_read, python_excel_write,
             python_1c_com, python_1c_http
    Web:     html_basic, html_canvas, js_class, js_fetch
    C/C++:   c_main, cpp_class
    C#:      cs_class
    Shell:   powershell_admin, bash_script
  模板是完整源码。复制到剪贴板, 用户粘贴到新文件。

## 典型场景 (recipes)
1) 从零创建文件
   FILE: path/to/file.ext
   <content>

2) 安全地编辑已有文件
   READ: path | 1-30            (查看确切文本)
   EDIT: path
   <<<FIND>>>
   确切文本
   <<<REPLACE>>>
   新文本
   <<<END>>>

3) 如果 EDIT 返回 NOT FOUND
   READ: path | 1-80            (找到真正的文本)
   用确切片段给出新的 EDIT

4) 编辑后快速检查
   CHECK: path
   (Python -> py_compile, JSON -> parse, HTML/JS -> 括号/标签检查)

5) 回滚坏掉的编辑
   BACKUPS: path                (列表)
   DIFF: path                   (查看变化)
   RESTORE: path | 2            (回滚到第 2 新的)

6) 查看大文件的片段
   READ: path | 200-260

7) 在项目中查找文本
   SEARCH: def main

8) 截取特定窗口
   SHOT: "Chrome"
   (图像进入剪贴板, 无自动日志)

9) 复用模板
   SNIPPET: python_cli
   (模板进入剪贴板)

10) 大任务前收集项目上下文
    CTX:
    (全部在剪贴板: meta + desc + tasks + log)

11) 工作时添加注释/任务
    NOTE: file X uses UTC, not local time
    TASK: add rewrite login screen

12) 长链
    一个命令 = 一个块。不要把 FILE + EDIT + CHECK
    放在同一个剪贴板负载中。一个接一个复制。

## 自动检查详细
每次 EDIT 后 Undo 自动运行语法检查:
  .py   py_compile.compile() — 完整 Python 语法编译
  .json json.loads() — JSON 解析
  .html </html> 存在, <script> 与 </script> 平衡
  .js   花括号 {} 和圆括号 () 平衡 (基础)
  其他  无检查, 静默通过

如果失败, 响应包含 "CHECK FAILED: <原因>"。
文件不会自动回滚 — 用户决定。

手动 CHECK: path 对任何文件都可用。

## 项目与 .agent
每个项目文件夹有自己的 .agent/:
  project.prj    — 元数据 (名称, 路径, 创建时间)
  desc.md        — 想法描述
  context.json   — 错误, 解决方案, 注释
  tasks.json     — 待办 / 完成
  project.log    — 项目日志
切换项目: File -> Open Folder。最近列表在 File -> Recent。

## 备份规则
- 每次 EDIT / FILE / DELETE / RENAME 前都会做备份。
- 备份存放在 backups/ 中, 带时间戳。
- 文件名: file.bak_TAG_YYYYMMDD_HHMMSS
- 列表:     BACKUPS: [path]
- 回滚:     RESTORE: path | N   (N = 1 — 最新)
- 清理:     CLEANUP: keep=N      (每个文件保留 N 个最新)
- 恢复不会删除备份。恢复前会创建新快照, 标签 before_restore_*。

## 示例对话
AI:
  FILE: src/hello.py
  print("hello")

用户复制块到剪贴板。
Undo 执行, 把 "saved: src/hello.py (0.1K)" 放入剪贴板。
用户把结果粘贴到聊天。
AI 看到文件已创建, 继续。

如果编辑失败 — Undo 返回:
  err: NOT FOUND blocks: [1]
AI 先运行 READ: path | 1-50, 然后用确切文本给出新 EDIT。

## AI 响应风格
- 短文本, 无废话。
- 每个命令用三重反引号包裹。
- 缺上下文 — 先 CTX:, 再行动。
- 出问题 — 先 LOG: 50, 再假设。
- 不要问 "你好吗" 之类。
- 不要复述日志中已有的内容。

## 皮肤和语言
- Undo 使用终端皮肤 (黑底, 绿字)。
- UI 语言: RU / EN / ZH。
- 切换: Settings -> Language (Undo 会自行重启)。
- 设置保存在 config.json。

## 底部终端行
不只是输入框。没有 Undo 标记的命令会发送到 cmd.exe,
输出显示在日志中。
- dir, cd, echo, type — Windows 命令
- python, pip, powershell — 可用
- cd path — 更改 shell 命令的工作目录
- clear / cls — 清除日志
- help — Undo 命令列表
- exit — 关闭 Undo
- 上下箭头 — 命令历史

## 剪贴板和键盘
- Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+A 在任何输入法下都可用
  (按物理键码)。
- Shift+Insert — 粘贴, Ctrl+Insert — 复制。
- 日志右键 -> 复制选中 / 复制全部日志 /
  清除日志 / 显示剪贴板 / 粘贴到输入。
- 输入框右键 -> 剪切 / 复制 / 粘贴 / 全选 / 清除。

## Tools 菜单
- 截图(窗口)/(全屏)
- 复制上下文 (CTX) / 复制日志 / 复制清单
- 显示剪贴板
- 打开 data / project / backups / shots

## 字体
- VT323 — 像素风格, 终端外观
- Consolas — 标准等宽
- Courier New — 打字机
- 大小: 11 / 13 / 16

## 备份规则
- 每次 EDIT / FILE / DELETE / RENAME 前都会创建备份。
- 存放在 backups/, 名称: file.bak_TAG_YYYYMMDD_HHMMSS
- 启动时自动清理: 删除超过 N 天的, 每个文件保留 N 个最新
  (默认 30 天 / 30 个)。
- 配置: BACKUP_RULE: show | keep=N | age=N | run
- 新备份 (<1 小时) 不动。
- 列表 / 回滚 / diff:
    BACKUPS: [path]
    RESTORE: path | N
    DIFF: path
    CLEANUP: keep=N

## 智能搜索 — REGEX
- REGEX: file | pattern   (Python 正则)
- 示例: REGEX: src.py | def\s+(\w+)\(

## 上下文提醒
- 每 N 次更改 Undo 提醒 CTX。
- 配置: REMIND: ctx=N | off | reset | show
- 提醒时 Undo 自动把 CTX-AUTO 复制到剪贴板。
- 默认 50, 推荐 30。

## 内置模板 (18 个)
Python:
  python_class, python_cli, python_http,
  python_file_io, python_sqlite,
  python_excel_read, python_excel_write,
  python_1c_com, python_1c_http
Web:     html_basic, html_canvas, js_class, js_fetch
C/C++:   c_main, cpp_class
C#:      cs_class
Shell:   powershell_admin, bash_script
- SNIPPET:            — 列出所有
- SNIPPET: name       — 特定模板到剪贴板

## AI 必须始终记住
1. 没有直接文件访问 — 只能通过 Undo。
2. 用户是中介。他们复制/粘贴块。
3. 一个命令 = 一个 ``` 块。不要混合。
4. 如果 EDIT 失败 — 先 READ, 再新 EDIT。
5. 不要编造路径 — 问 Undo: TREE: 或 LIST:。
6. 总是检查结果 — Undo 放回剪贴板。
7. 长命令 Undo 返回紧凑摘要: N 行。
"""

MANIFESTS = {"ru": MANIFEST, "en": MANIFEST_EN, "zh": MANIFEST_ZH}

LANG_RU = {
    "file": "Файл", "edit": "Правка", "project": "Проект",
    "tools": "Инструменты", "ai": "AI", "settings": "Настройки",
    "help": "Помощь", "exit": "Выход", "manifest": "Показать манифест",
    "copy_manifest": "📋 Копировать манифест",
    "open_folder": "Открыть папку...", "save_ctx": "Сохранить контекст",
    "edit_desc": "Описание проекта...", "recent": "Недавние",
    "paste_input": "Вставить в строку", "copy_log": "Копировать лог",
    "clear_log": "Очистить лог", "screenshot_win": "Скриншот (окно)",
    "screenshot_full": "Скриншот (весь экран)",
    "open_data": "Открыть данные", "open_project": "Открыть проект",
    "open_backups": "Открыть бэкапы", "open_shots": "Открыть скриншоты",
    "font_11": "Шрифт 11", "font_13": "Шрифт 13", "font_16": "Шрифт 16",
    "lang_ru": "Русский", "lang_en": "English", "lang_zh": "中文",
    "commands": "Команды", "about": "О программе",
    "language": "Язык",
}
LANG_EN = {
    "file": "File", "edit": "Edit", "project": "Project",
    "tools": "Tools", "ai": "AI", "settings": "Settings",
    "help": "Help", "exit": "Exit", "manifest": "Show manifest",
    "copy_manifest": "📋 Copy manifest",
    "open_folder": "Open Folder...", "save_ctx": "Save Context",
    "edit_desc": "Edit Description...", "recent": "Recent",
    "paste_input": "Paste to input", "copy_log": "Copy log",
    "clear_log": "Clear log", "screenshot_win": "Screenshot (window)",
    "screenshot_full": "Screenshot (full screen)",
    "open_data": "Open data", "open_project": "Open project",
    "open_backups": "Open backups", "open_shots": "Open shots",
    "font_11": "Font 11", "font_13": "Font 13", "font_16": "Font 16",
    "lang_ru": "Русский", "lang_en": "English", "lang_zh": "中文",
    "commands": "Commands", "about": "About",
    "settings": "Settings", "language": "Language",
}
LANG_ZH = {
    "file": "文件", "edit": "编辑", "project": "项目",
    "tools": "工具", "ai": "AI", "settings": "设置",
    "help": "帮助", "exit": "退出", "manifest": "显示清单",
    "copy_manifest": "📋 复制清单",
    "open_folder": "打开文件夹...", "save_ctx": "保存上下文",
    "edit_desc": "编辑描述...", "recent": "最近",
    "paste_input": "粘贴到输入", "copy_log": "复制日志",
    "clear_log": "清除日志", "screenshot_win": "截图(窗口)",
    "screenshot_full": "截图(全屏)",
    "open_data": "打开数据", "open_project": "打开项目",
    "open_backups": "打开备份", "open_shots": "打开截图",
    "font_11": "字体 11", "font_13": "字体 13", "font_16": "字体 16",
    "lang_ru": "Русский", "lang_en": "English", "lang_zh": "中文",
    "commands": "命令", "about": "关于",
}
LANGS = {"ru": LANG_RU, "en": LANG_EN, "zh": LANG_ZH}

def L():
    lang = CFG.get("language", "en")
    return LANGS.get(lang, LANGS["en"])

def _load_pixel_font():
    """Загрузить VT323 в текущий процесс Tkinter."""
    try:
        from tkinter import font as tkfont
        fpath = DATA_DIR / "fonts" / "VT323-Regular.ttf"
        if not fpath.exists():
            return None
        # Tk умеет грузить шрифты из файла только через private API:
        try:
            # Windows + Tk 8.6: регистрируем шрифт в системе сессии
            import ctypes
            gdi32 = ctypes.windll.gdi32
            FR_PRIVATE = 0x10
            # 64-bit Python needs explicit types for Windows API
            try:
                gdi32.AddFontResourceExW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p
                ]
                gdi32.AddFontResourceExW.restype = ctypes.c_int
            except Exception:
                pass
            gdi32.AddFontResourceExW(str(fpath), FR_PRIVATE, 0)
        except Exception:
            pass
        # Проверим, что Tk его видит
        families = tkfont.families()
        for name in families:
            if "vt323" in name.lower():
                return name
        return "VT323"
    except Exception:
        return None


class Undo(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Undo — copy the manifest for AI")
        try:
            ico = AGENT_DIR / "undo.ico"
            if not ico.exists():
                ico = Path(r"C:\4\undo.ico")
            if ico.exists():
                self.iconbitmap(str(ico))
        except Exception:
            pass
        self.geometry(CFG.get("window_geometry", "420x300"))
        self.minsize(320, 200)
        self.configure(bg=BG)
        # Загрузка пиксельного шрифта (если есть)
        pixel = _load_pixel_font()
        if pixel and not CFG.get("_font_loaded_once", False):
            CFG["_font_loaded_once"] = True
            # Не меняем font_family насильно, но запоминаем имя
            CFG["_pixel_family"] = pixel
            save_config(CFG)
        self.busy = False
        self.auto = True
        self.last_clip = ""
        self.q = queue.Queue()
        self._build_menubar()
        self._build_ui()
        self.after(80, self._drain)
        self.after(700, self._poll)
        self._print("[Undo] ready", "ok")
        self._print("", "")
        self._print("─── START HERE ───", "warn")
        self._print("  1. AI menu → Copy manifest for AI", "info")
        self._print("  2. Paste it into any AI chat", "info")
        self._print("  3. AI will know how to work with you", "info")
        self._print("─────────────────", "warn")
        self._print("", "")
        if CFG.get("current_project"):
            self._print("project: " + CFG["current_project"], "info")
            ensure_agent_meta(CFG["current_project"])
        else:
            self._print("no project — File → Open Folder", "warn")
        # Авто-очистка бэкапов при старте
        self.after(1500, self._auto_cleanup_backups)

    def _auto_cleanup_backups(self):
        """Умная очистка бэкапов:
        - не трогает файлы моложе 1 часа;
        - хранит не более N последних на файл (по умолчанию 30);
        - удаляет всё старше 30 дней."""
        try:
            keep = int(CFG.get("backup_keep", 30))
            max_age_days = int(CFG.get("backup_max_age_days", 30))
        except:
            keep, max_age_days = 30, 30

        from collections import defaultdict
        import time as _t
        now = _t.time()
        cutoff_old = now - max_age_days * 86400
        cutoff_fresh = now - 3600   # не трогаем свежие 1 час

        groups = defaultdict(list)
        try:
            for f in BACKUPS_DIR.glob("*.bak_*"):
                base = f.name.split(".bak_", 1)[0]
                groups[base].append(f)
        except: return

        removed_old = 0
        removed_extra = 0
        for base, files in groups.items():
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            # 1) удалить старше N дней
            fresh = []
            for f in files:
                mt = f.stat().st_mtime
                if mt < cutoff_old:
                    try: f.unlink(); removed_old += 1
                    except: pass
                else:
                    fresh.append(f)
            # 2) оставить не более keep свежих
            for f in fresh[keep:]:
                try:
                    # не трогаем свежие (моложе часа)
                    if f.stat().st_mtime > cutoff_fresh: continue
                    f.unlink(); removed_extra += 1
                except: pass

        if removed_old or removed_extra:
            self._print("backup auto-cleanup: -" + str(removed_old) +
                        " old, -" + str(removed_extra) + " extra", "info")
            self._print("  (keep=" + str(keep) + ", max_age=" +
                        str(max_age_days) + "d)", "dim")

    def _build_menubar(self):
        Lc = L()
        mfnt = ("Consolas", 10)
        def mk(parent):
            return tk.Menu(parent, tearoff=0, bg="#000", fg=FG,
                            activebackground=FG, activeforeground="#000",
                            bd=0, font=mfnt)

        mb = mk(self); self.configure(menu=mb)

        fm = mk(mb)
        fm.add_command(label=Lc["open_folder"], command=self._pick_folder)
        fm.add_command(label=Lc["save_ctx"], command=self._save_context)
        fm.add_command(label=Lc["edit_desc"], command=self._edit_desc)
        fm.add_separator()
        rm = mk(fm)
        fm.add_cascade(label=Lc["recent"], menu=rm)
        self.recent_menu = rm
        self._refresh_recent()
        fm.add_separator()
        fm.add_command(label=Lc["exit"], command=self._on_exit)
        mb.add_cascade(label=Lc["file"], menu=fm)

        em = mk(mb)
        em.add_command(label=Lc["paste_input"], command=self._paste_input)
        em.add_command(label="Show clipboard", command=self._show_clip)
        em.add_command(label=Lc["copy_log"], command=self._copy_log)
        em.add_command(label=Lc["clear_log"], command=self._clear_log)
        mb.add_cascade(label=Lc["edit"], menu=em)

        tm = mk(mb)
        tm.add_command(label=Lc["screenshot_win"], command=lambda: self._exec_cmd("SHOT:"))
        tm.add_command(label=Lc["screenshot_full"], command=lambda: self._exec_cmd("SHOT: full"))
        tm.add_separator()
        tm.add_command(label="📋 Copy context (CTX)", command=self._save_context)
        tm.add_command(label="📋 Copy log", command=self._copy_log)
        tm.add_command(label="📋 Copy manifest", command=self._copy_manifest)
        tm.add_separator()
        tm.add_command(label="Show clipboard", command=self._show_clip)
        tm.add_separator()
        tm.add_command(label=Lc["open_data"], command=lambda: self._open(DATA_DIR))
        tm.add_command(label=Lc["open_project"], command=self._open_project)
        tm.add_command(label=Lc["open_backups"], command=lambda: self._open(BACKUPS_DIR))
        tm.add_command(label=Lc["open_shots"], command=lambda: self._open(SHOTS_DIR))
        mb.add_cascade(label=Lc["tools"], menu=tm)

        sm = mk(mb)
        sm.add_command(label=Lc["font_11"], command=lambda: self._set_size(11))
        sm.add_command(label=Lc["font_13"], command=lambda: self._set_size(13))
        sm.add_command(label=Lc["font_16"], command=lambda: self._set_size(16))
        sm.add_separator()
        sm.add_command(label="Font: VT323 (pixel)", command=lambda: self._set_family("VT323"))
        sm.add_command(label="Font: Consolas", command=lambda: self._set_family("Consolas"))
        sm.add_command(label="Font: Courier New", command=lambda: self._set_family("Courier New"))
        sm.add_separator()
        lm = mk(sm)
        lm.add_command(label=Lc["lang_ru"], command=lambda: self._set_lang("ru"))
        lm.add_command(label=Lc["lang_en"], command=lambda: self._set_lang("en"))
        lm.add_command(label=Lc["lang_zh"], command=lambda: self._set_lang("zh"))
        sm.add_cascade(label=Lc["settings"], menu=lm)
        mb.add_cascade(label=Lc["settings"], menu=sm)

        hm = mk(mb)
        hm.add_command(label=Lc["commands"], command=self._show_cmds)
        hm.add_command(label=Lc["about"], command=self._about)
        mb.add_cascade(label=Lc["help"], menu=hm)

        am = mk(mb)
        am.add_command(label="📋 Copy manifest for AI  ← start here",
                       command=self._copy_manifest)
        am.add_separator()
        am.add_command(label=Lc["manifest"], command=self._show_manifest)
        am.add_command(label=Lc["copy_manifest"], command=self._copy_manifest)
        mb.add_cascade(label=Lc["ai"], menu=am)

    def _build_ui(self):
        fnt = (CFG["font_family"], CFG["font_size"])
        self.log = tk.Text(self, bg=BG, fg=FG, font=fnt,
                           relief="flat", borderwidth=0, wrap="word",
                           padx=8, pady=6, insertbackground=FG,
                           highlightthickness=0,
                           selectbackground="#00aa00",
                           selectforeground="#000000")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="normal")
        # Разрешаем выделение мышью, но блокируем редактирование
        self.log.bind("<Key>", self._on_log_key)
        self.log.bind("<Control-c>", self._on_log_copy)
        self.log.bind("<Control-Insert>", self._on_log_copy)
        self.log.bind("<Button-3>", self._on_log_right_click)
        self.log.tag_configure("ok", foreground=FG)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("info", foreground=DIM)
        self.log.tag_configure("buf", foreground=BUF)
        self.log.tag_configure("dim", foreground=DIM)

        bottom = tk.Frame(self, bg=BG, height=26)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        tk.Label(bottom, text=">", bg=BG, fg=FG, font=fnt, padx=6
                 ).pack(side="left")
        self.entry = tk.Entry(bottom, bg=BG, fg=FG, font=fnt,
                              relief="flat", borderwidth=0,
                              insertbackground=FG, highlightthickness=0,
                              selectbackground="#00aa00",
                              selectforeground="#000000")
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda e: self._on_enter())
        self.entry.bind("<Control-KeyPress>", self._entry_ctrl)
        self.entry.bind("<Control-Insert>", lambda e: self._entry_copy())
        self.entry.bind("<Shift-Insert>", self._entry_paste)
        self.entry.bind("<Button-3>", self._entry_right_click)
        self.entry.bind("<Up>", self._entry_history)
        self.entry.bind("<Down>", self._entry_history)
        self.entry.focus()

    def _entry_ctrl(self, event):
        """Ctrl-комбинации по физическому коду (работает в любой раскладке)."""
        kc = event.keycode
        if kc == 67:      # C — copy
            self._entry_copy(); return "break"
        if kc == 86:      # V — paste
            self._entry_paste(); return "break"
        if kc == 88:      # X — cut
            self._entry_cut(); return "break"
        if kc == 65:      # A — select all
            self.entry.select_range(0, "end")
            self.entry.icursor("end"); return "break"
        return None

    def _entry_copy(self):
        try:
            if self.entry.selection_present():
                s = self.entry.selection_get()
                clip_set(s)
                self._print("copied " + str(len(s)) + " chars", "ok")
        except Exception:
            pass
        return "break"

    def _entry_cut(self):
        try:
            if self.entry.selection_present():
                s = self.entry.selection_get()
                clip_set(s)
                self.entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        return "break"

    def _entry_paste(self, event=None):
        """Универсальная вставка в поле ввода."""
        try:
            txt = clip_get()
            if txt:
                # заменяем выделение, если есть
                try:
                    if self.entry.selection_present():
                        self.entry.delete("sel.first", "sel.last")
                except Exception:
                    pass
                self.entry.insert("insert", txt.split("\n")[0])
                return "break"
        except Exception as e:
            self._print("paste err: " + str(e), "err")
        return "break"

    def _entry_select_all(self, event=None):
        self.entry.select_range(0, "end")
        self.entry.icursor("end")
        return "break"

    def _entry_right_click(self, event):
        menu = tk.Menu(self, tearoff=0, bg="#000", fg=FG,
                        activebackground=FG, activeforeground="#000", bd=0,
                        font=("Consolas", 10))
        def copy_sel():
            try:
                if self.entry.selection_present():
                    s = self.entry.selection_get()
                    clip_set(s)
                    self._print("copied " + str(len(s)) + " chars", "ok")
            except Exception:
                pass
        def cut_sel():
            try:
                if self.entry.selection_present():
                    s = self.entry.selection_get()
                    clip_set(s)
                    self.entry.delete("sel.first", "sel.last")
            except Exception:
                pass
        menu.add_command(label="Cut", command=cut_sel)
        menu.add_command(label="Copy", command=copy_sel)
        menu.add_command(label="Paste", command=self._entry_paste)
        menu.add_separator()
        menu.add_command(label="Select all", command=self._entry_select_all)
        menu.add_command(label="Clear", command=lambda: self.entry.delete(0, "end"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _print(self, msg, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        self.q.put((ts, str(msg), tag))
        try:
            log_path = LOGS_DIR / "undo.log"
            # Rotate: if the log is bigger than 5 MB, rename to .old
            try:
                if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
                    old = LOGS_DIR / "undo.log.old"
                    try:
                        if old.exists(): old.unlink()
                    except Exception: pass
                    try: log_path.rename(old)
                    except Exception: pass
            except Exception: pass
            with log_path.open("a", encoding="utf-8") as f:
                f.write("[" + ts + "] " + str(msg) + "\n")
        except: pass

    def _on_log_key(self, event):
        """Блокируем редактирование, но разрешаем навигацию и копирование."""
        # Ctrl+C, Ctrl+A, Ctrl+Insert
        if (event.state & 0x4) and event.keysym.lower() in ("c", "a"):
            return None
        # Разрешаем стрелки, home, end, pageup, pagedown
        allowed = ("Up","Down","Left","Right","Home","End","Prior","Next",
                    "Next","Control_L","Control_R","Shift_L","Shift_R")
        if event.keysym in allowed:
            return None
        # Всё остальное — блокируем (чтобы нельзя было печатать в лог)
        return "break"

    def _on_log_copy(self, event=None):
        try:
            sel = self.log.get("sel.first", "sel.last")
            if sel:
                clip_set(sel)
                self._print("log selection copied (" + str(len(sel)) + " chars)", "ok")
        except tk.TclError:
            # ничего не выделено — копируем всё
            txt = self.log.get("1.0", "end-1c")
            if clip_set(txt):
                self._print("whole log copied (" + str(len(txt)) + " chars)", "ok")
        return "break"

    def _on_log_right_click(self, event):
        menu = tk.Menu(self, tearoff=0, bg="#000", fg=FG,
                        activebackground=FG, activeforeground="#000", bd=0,
                        font=("Consolas", 10))
        try:
            sel = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            sel = ""
        if sel:
            menu.add_command(label="Copy selection",
                              command=lambda: (clip_set(sel),
                                                self._print("selection copied", "ok")))
        menu.add_command(label="Copy whole log",
                          command=lambda: (clip_set(self.log.get("1.0", "end-1c")),
                                            self._print("whole log copied", "ok")))
        menu.add_separator()
        menu.add_command(label="Clear log", command=self._clear_log)
        menu.add_command(label="Show clipboard", command=self._show_clip)
        menu.add_command(label="Paste to input",
                          command=self._paste_input)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _drain(self):
        try:
            while True:
                ts, msg, tag = self.q.get_nowait()
                # Запоминаем позицию курсора выделения
                try:
                    sel_start = self.log.index("sel.first")
                    sel_end = self.log.index("sel.last")
                    has_sel = True
                except tk.TclError:
                    has_sel = False
                self.log.insert("end", "[" + ts + "] ", "dim")
                self.log.insert("end", msg + "\n", tag or "")
                self.log.see("end")
                # Восстанавливаем выделение если было
                if has_sel:
                    try:
                        self.log.tag_add("sel", sel_start, sel_end)
                    except tk.TclError:
                        pass
        except queue.Empty: pass
        self.after(80, self._drain)

    def _poll(self):
        self._maybe_auto_ctx()
        if self.auto and not self.busy:
            try: txt = clip_get()
            except Exception as e:
                self._print("clipboard ERR: " + str(e), "err")
                txt = ""
            if txt and txt != self.last_clip:
                self.last_clip = txt
                self._print("clip(" + str(len(txt)) + ") > " + txt[:70].replace("\n", "\\n"), "buf")
                extra = len(txt.splitlines()) - 1
                if extra > 0:
                    self._print("       + " + str(extra) + " more lines", "dim")
                mode, arg, payload = detect_mode(txt)
                if mode:
                    self._exec(mode, arg, payload)
                else:
                    self._print("       (no command marker)", "dim")
        self.after(700, self._poll)

    def _maybe_auto_ctx(self):
        """Автосохранение контекста проекта раз в auto_ctx_min минут (0 = выкл)."""
        try:
            mins = int(CFG.get("auto_ctx_min", 0) or 0)
        except Exception:
            mins = 0
        if mins <= 0:
            return
        now = time.time()
        if now - getattr(self, "_last_auto_ctx", 0) < mins * 60:
            return
        self._last_auto_ctx = now
        try:
            self._save_context()
            self._print("auto-ctx: saved", "dim")
        except Exception as e:
            self._print("auto-ctx err: " + str(e), "err")

    def _show_clip(self):
        txt = clip_get()
        if not txt:
            self._print("clipboard empty", "warn")
        else:
            self._print("clip(" + str(len(txt)) + "): " + txt[:200], "buf")

    def _on_enter(self):
        cmd = self.entry.get().strip()
        if not cmd: return
        self.entry.delete(0, "end")
        # История команд
        if not hasattr(self, "_cmd_history"):
            self._cmd_history = []
            self._cmd_hist_idx = -1
        if not self._cmd_history or self._cmd_history[-1] != cmd:
            self._cmd_history.append(cmd)
        self._cmd_hist_idx = len(self._cmd_history)

        # Встроенные команды терминала
        low = cmd.lower()
        if low in ("clear", "cls"):
            self._clear_log(); return
        if low == "exit":
            self._on_exit(); return
        if low.startswith("cd ") or low == "cd":
            self._shell_cd(cmd[2:].strip() if len(cmd) > 2 else "")
            return
        if low == "help":
            self._print("[help] FILE EDIT APPEND DELETE READ HEAD TAIL", "info")
            self._print("[help] STAT FIND SEARCH REGEX LIST TREE COUNT HASH", "info")
            self._print("[help] MKDIR RENAME TOUCH SNIPPET CALC CHECK", "info")
            self._print("[help] BACKUPS RESTORE DIFF CLEANUP BACKUP_RULE", "info")
            self._print("[help] CTX NOTE TASK REMIND LOG CMD SHOT", "info")
            self._print("[help] shell: dir cd echo type python powershell ...", "info")
            self._print("[help] full manifest: AI → Show manifest", "info")
            return

        # Если есть маркер Undo — обрабатываем как команду Undo
        mode, arg, payload = detect_mode(cmd)
        if mode:
            self._print("> " + cmd[:80], "buf")
            self._exec(mode, arg, payload)
            return

        # Иначе — прокидываем в shell
        self._print("$ " + cmd, "buf")
        self._shell_run(cmd)

    def _shell_cd(self, path):
        """Смена cwd для shell-команд."""
        try:
            if not path:
                cur = getattr(self, "_shell_cwd", AGENT_DIR)
                self._print(str(cur), "ok")
                return
            target = Path(path)
            if not target.is_absolute():
                base = getattr(self, "_shell_cwd", AGENT_DIR)
                target = (base / path).resolve()
            if not target.is_dir():
                self._print("cd: no such directory: " + str(target), "err")
                return
            self._shell_cwd = target
            self._print("cwd: " + str(target), "ok")
        except Exception as e:
            self._print("cd err: " + str(e), "err")

    def _shell_run(self, cmd):
        """Выполнить команду в cmd.exe и вывести результат в лог."""
        cwd = str(getattr(self, "_shell_cwd", AGENT_DIR))
        try:
            tmo = int(CFG.get("cmd_timeout", 1800) or 0)
        except Exception:
            tmo = 1800
        def _work():
            try:
                out = subprocess.check_output(
                    cmd, shell=True, cwd=cwd,
                    stderr=subprocess.STDOUT,
                    timeout=(tmo if tmo > 0 else None)
                ).decode("cp866", errors="replace")
                if not out.strip():
                    self._print("(no output)", "dim")
                else:
                    for line in out.splitlines():
                        self._print("  " + line, "ok")
            except subprocess.CalledProcessError as e:
                try:
                    err = e.output.decode("cp866", errors="replace")
                except Exception:
                    err = str(e)
                self._print("exit " + str(e.returncode), "err")
                for line in err.splitlines():
                    self._print("  " + line, "err")
            except subprocess.TimeoutExpired:
                self._print("timeout (" + str(tmo) + "s): увеличьте cmd_timeout в config.json или поставьте 0", "err")
            except Exception as e:
                self._print("shell err: " + str(e), "err")
        threading.Thread(target=_work, daemon=True).start()

    def _entry_history(self, event):
        """Стрелки вверх/вниз — история."""
        if not hasattr(self, "_cmd_history") or not self._cmd_history:
            return "break"
        if event.keysym == "Up":
            if self._cmd_hist_idx > 0:
                self._cmd_hist_idx -= 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self._cmd_history[self._cmd_hist_idx])
        elif event.keysym == "Down":
            if self._cmd_hist_idx < len(self._cmd_history) - 1:
                self._cmd_hist_idx += 1
                self.entry.delete(0, "end")
                self.entry.insert(0, self._cmd_history[self._cmd_hist_idx])
            else:
                self._cmd_hist_idx = len(self._cmd_history)
                self.entry.delete(0, "end")
        return "break"

    def _exec_cmd(self, cmd):
        mode, arg, payload = detect_mode(cmd)
        if not mode:
            cmd = "CMD: " + cmd
            mode, arg, payload = detect_mode(cmd)
        self._print("> " + cmd[:80], "buf")
        self._exec(mode, arg, payload)

    def _exec(self, mode, arg, payload):
        if self.busy:
            self._print("busy...", "warn")
            return
        self.busy = True
        threading.Thread(target=self._work, args=(mode, arg, payload), daemon=True).start()

    def _work(self, mode, arg, payload):
        try: ok, result = self._dispatch(mode, arg, payload)
        except: ok, result = False, traceback.format_exc()
        tag = "ok" if ok else "err"
        self._print(result, tag)
        # Счётчик изменений для напоминания про контекст
        if ok and mode in ("file", "edit", "append", "delete", "rename"):
            self._change_counter = getattr(self, "_change_counter", 0) + 1
            every = int(CFG.get("remind_ctx_every", 50))
            if every > 0 and self._change_counter >= every:
                self._change_counter = 0
                self._print("─── reminder: " + str(every) +
                            " changes done. Update project context? ───", "warn")
                self._print("  run CTX: and paste into AI chat", "dim")
                # тихо копируем контекст в буфер
                try:
                    proj = CFG.get("current_project")
                    if proj:
                        ok2, ctx = collect_context(proj)
                        if ok2:
                            clip_set("CTX-AUTO:\n" + ctx)
                            self._print("  (CTX-AUTO copied to clipboard)", "dim")
                except: pass
        if mode == "shot" and ok:
            # Image is already in the clipboard (placed there by _dispatch).
            # Do not overwrite it with the text result.
            pass
        else:
            try:
                clip_set(result)
                # Remember what we just put into the clipboard, so _poll does
                # not mistake our own result for the next user command.
                self.last_clip = result
            except: pass
        self.busy = False
        # Показать что легло в буфер
        if ok and mode != "shot":
            preview = str(result).split("\n", 1)[0][:60]
            self._print("buffer < " + preview, "ok")
        elif mode == "shot":
            self._print("buffer < [image]", "ok")
        # Автоперезапуск если правили свой файл
        if mode == "edit" and ok:
            try:
                target = resolve_path(arg)
                if target.resolve() == Path(__file__).resolve():
                    self.after(800, self._restart_self)
            except: pass

    def _restart_self(self):
        self._print("self-edit — restarting...", "warn")
        try:
            CFG["window_geometry"] = self.geometry()
            save_config(CFG)
        except: pass
        self.destroy()
        try:
            cf = 0x00000010 if os.name == "nt" else 0
            if getattr(sys, "frozen", False):
                # собранный exe перезапускает сам себя с теми же аргументами
                subprocess.Popen([sys.executable] + list(sys.argv[1:]),
                                 cwd=str(Path(sys.executable).parent),
                                 creationflags=cf)
            else:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                                 cwd=str(Path(__file__).parent.resolve()),
                                 creationflags=cf)
        except: pass

    def _dispatch(self, mode, arg, payload):
        # LANG: -> "lang: en"; LANG: ru -> переключить язык и ответить.
        # Нужно дополнению в браузере: его интерфейс идёт за языком Undo.
        if mode == "lang":
            want = (arg or "").strip().lower()
            if want in ("ru", "en", "zh") and want != CFG.get("language"):
                CFG["language"] = want
                save_config(CFG)
                self._print("language: " + want, "ok")
                self.after(400, self._restart_self)
            return True, "lang: " + str(CFG.get("language", "en"))
        # MANIFEST: -> полный протокол на текущем языке (для вставки в чат).
        if mode == "manifest":
            cur = CFG.get("language", "en")
            return True, MANIFESTS.get(cur, MANIFESTS["en"])
        if mode == "file":
            target, bak = save_file(arg, payload)
            kb = len(payload.encode("utf-8"))/1024
            extra = "\nbak: " + bak.name if bak else ""
            return True, "saved: " + arg + " ({:.1f}K)".format(kb) + extra
        if mode == "edit":
            blocks = parse_edit_blocks(payload)
            if not blocks: return False, "no blocks"
            ok, msg = apply_edits(arg, blocks)
            if ok:
                try:
                    chk_ok, chk_msg = check_file(resolve_path(arg))
                    if not chk_ok:
                        return False, ("ok: " if ok else "err: ") + msg + "\n  CHECK FAILED: " + chk_msg
                    msg += "\n  check: " + chk_msg
                except Exception:
                    pass
            return ok, ("ok: " if ok else "err: ") + msg
        if mode == "append":
            target = resolve_path(arg); make_backup(target, "app_")
            with target.open("a", encoding="utf-8") as f: f.write(payload)
            return True, "appended: " + arg
        if mode == "delete":
            target = resolve_path(arg)
            if not target.exists(): return False, "no file"
            make_backup(target, "del_"); target.unlink()
            return True, "deleted: " + arg
        if mode == "mkdir":
            resolve_path(arg).mkdir(parents=True, exist_ok=True)
            return True, "mkdir: " + arg
        if mode in ("read","grep"):
            rng = None; fname = arg
            if "|" in arg:
                a, b = arg.split("|", 1); fname = a.strip()
                m2 = re.match(r"^(\d+)\s*-\s*(\d+)$", b.strip())
                if m2: rng = (int(m2.group(1)), int(m2.group(2)))
            return read_file_impl(fname, "read", rng=rng)
        if mode == "head":
            n, fname = 40, arg
            if "|" in arg:
                a, b = arg.split("|",1); fname = a.strip()
                try: n = int(b.strip())
                except: pass
            return read_file_impl(fname, "head", n=n)
        if mode == "tail":
            n, fname = 40, arg
            if "|" in arg:
                a, b = arg.split("|",1); fname = a.strip()
                try: n = int(b.strip())
                except: pass
            return read_file_impl(fname, "tail", n=n)
        if mode == "remind":
            raw = (arg or "").strip().lower()
            if not raw or raw == "show":
                n = CFG.get("remind_ctx_every", 50)
                return True, "[REMIND] show: ctx_every = " + str(n) + " changes"
            if raw.startswith("ctx="):
                try:
                    v = int(raw[4:].strip())
                    CFG["remind_ctx_every"] = v
                    save_config(CFG)
                    return True, "remind_ctx_every = " + str(v)
                except: return False, "must be integer"
            if raw == "reset":
                self._change_counter = 0
                return True, "change counter reset"
            if raw == "off":
                CFG["remind_ctx_every"] = 0
                save_config(CFG)
                return True, "reminder off"
            return False, "format: REMIND: show | ctx=N | reset | off"

        if mode == "backup_rule":
            raw = (arg or "").strip().lower()
            if not raw or raw == "show":
                keep = CFG.get("backup_keep", 30)
                age = CFG.get("backup_max_age_days", 30)
                return True, ("[BACKUP_RULE]\n  keep:       " + str(keep) +
                              "\n  max_age:    " + str(age) + " days")
            if raw.startswith("keep="):
                try:
                    CFG["backup_keep"] = int(raw[5:].strip())
                    save_config(CFG)
                    return True, "backup_keep = " + str(CFG["backup_keep"])
                except: return False, "keep must be integer"
            if raw.startswith("age="):
                try:
                    CFG["backup_max_age_days"] = int(raw[4:].strip())
                    save_config(CFG)
                    return True, "backup_max_age_days = " + str(CFG["backup_max_age_days"])
                except: return False, "age must be integer"
            if raw == "run":
                self._auto_cleanup_backups()
                return True, "cleanup triggered"
            return False, "format: BACKUP_RULE: show | keep=N | age=N | run"

        if mode == "regex":
            # REGEX: file | pattern
            if "|" not in arg: return False, "format: REGEX: file | pattern"
            parts = arg.split("|", 1)
            fname = parts[0].strip()
            pattern = parts[1].strip()
            if not pattern: return False, "empty pattern"
            target = resolve_path(fname)
            if not target.exists(): return False, "no file: " + fname
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception as e: return False, "read err: " + str(e)
            try:
                rx = re.compile(pattern)
            except re.error as e:
                return False, "regex error: " + str(e)
            lines = text.splitlines()
            hits = []
            for i, line in enumerate(lines, 1):
                for m in rx.finditer(line):
                    hits.append((i, m.start(), m.group(0), line))
                    if len(hits) >= 100: break
                if len(hits) >= 100: break
            if not hits: return True, "[REGEX] no match: " + pattern
            out = ["[REGEX] '" + pattern + "' in " + fname + " -> " + str(len(hits)), "-"*60]
            for ln_no, col, m, line in hits[:50]:
                out.append("  {:4d}:{:2d} | {}".format(ln_no, col, line[:120]))
            if len(hits) > 50:
                out.append("  ... truncated (50 of " + str(len(hits)) + ")")
            return True, "\n".join(out)

        if mode == "backups":
            # список бэкапов проекта или файла
            try:
                files = sorted(BACKUPS_DIR.glob("*.bak_*"), key=lambda p: p.stat().st_mtime, reverse=True)
                if arg:
                    files = [f for f in files if f.name.startswith(Path(arg).name)]
                if not files:
                    return True, "[BACKUPS] (empty)"
                out = ["[BACKUPS] " + str(len(files)) + " files", "-"*60]
                for i, f in enumerate(files[:50]):
                    mt = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    sz = f.stat().st_size
                    out.append("  {:3d}. [{}] {} ({:.1f}K)".format(i+1, mt, f.name, sz/1024))
                return True, "\n".join(out)
            except Exception as e: return False, "err: " + str(e)

        if mode == "restore":
            # RESTORE: file | N   — откат к N-му бэкапу
            if "|" not in arg: return False, "format: RESTORE: file | N"
            parts = arg.split("|", 1)
            fname = parts[0].strip()
            try: n = int(parts[1].strip())
            except: return False, "N must be integer"
            files = sorted(BACKUPS_DIR.glob(Path(fname).name + ".bak_*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if not files: return False, "no backups for " + fname
            if n < 1 or n > len(files): return False, "N out of range (1.." + str(len(files)) + ")"
            src = files[n-1]
            target = resolve_path(fname)
            make_backup(target, "before_restore_")
            shutil.copy2(src, target)
            return True, "restored " + fname + " from " + src.name

        if mode == "diff":
            # DIFF: file   — текущий vs последний бэкап
            target = resolve_path(arg)
            if not target.exists(): return False, "no file: " + arg
            files = sorted(BACKUPS_DIR.glob(target.name + ".bak_*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if not files: return False, "no backups"
            old_lines = files[0].read_text(encoding="utf-8", errors="replace").splitlines()
            new_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            import difflib
            diff = list(difflib.unified_diff(old_lines, new_lines,
                        fromfile="backup/" + files[0].name, tofile="current/" + target.name,
                        lineterm="", n=2))
            if not diff: return True, "[DIFF] no changes"
            if len(diff) > 200: diff = diff[:200] + ["... truncated"]
            return True, "[DIFF] " + arg + "\n" + "\n".join(diff)

        if mode == "cleanup":
            # CLEANUP: keep=N  (по умолчанию 20) — оставить N свежих на файл
            keep = 20
            if arg.lower().startswith("keep="):
                try: keep = int(arg[5:].strip())
                except: pass
            from collections import defaultdict
            groups = defaultdict(list)
            for f in BACKUPS_DIR.glob("*.bak_*"):
                base = f.name.split(".bak_", 1)[0]
                groups[base].append(f)
            removed = 0
            for base, files in groups.items():
                files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[keep:]:
                    try: old.unlink(); removed += 1
                    except: pass
            return True, "cleanup: removed " + str(removed) + " old backups (keep=" + str(keep) + ")"

        if mode == "rename":
            if "->" not in arg: return False, "format: RENAME: old -> new"
            a, b = [x.strip() for x in arg.split("->", 1)]
            src = resolve_path(a); dst = resolve_path(b)
            if not src.exists(): return False, "no file: " + a
            make_backup(src, "rename_")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return True, "renamed: " + a + " -> " + b

        if mode == "touch":
            target = resolve_path(arg)
            if target.exists(): return True, "already exists: " + arg
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
            return True, "created: " + arg

        if mode == "search":
            # SEARCH: text — поиск по всему проекту
            if not arg: return False, "format: SEARCH: text"
            root = Path(CFG.get("current_project")) if CFG.get("current_project") else AGENT_DIR
            needle = arg.lower()
            hits = []
            max_hits = 100
            for f in root.rglob("*"):
                if not f.is_file(): continue
                if any(p in f.parts for p in (".git", "__pycache__", ".agent", "node_modules")): continue
                if f.stat().st_size > 2_000_000: continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except: continue
                for i, line in enumerate(text.splitlines(), 1):
                    if needle in line.lower():
                        hits.append((f.relative_to(root), i, line[:100]))
                        if len(hits) >= max_hits: break
                if len(hits) >= max_hits: break
            if not hits: return True, "[SEARCH] '" + arg + "' not found"
            out = ["[SEARCH] '" + arg + "' — " + str(len(hits)) + " hits", "-"*60]
            for rel, i, ln in hits:
                out.append("  " + str(rel) + ":" + str(i) + " | " + ln)
            return True, "\n".join(out)

        if mode == "count":
            target = resolve_path(arg)
            if not target.exists(): return False, "no file: " + arg
            text = target.read_text(encoding="utf-8", errors="replace")
            lines = text.count("\n") + (1 if text else 0)
            words = len(text.split())
            chars = len(text)
            chars_no_sp = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
            return True, ("[COUNT] " + arg +
                          "\n  lines:          " + str(lines) +
                          "\n  words:          " + str(words) +
                          "\n  chars:          " + str(chars) +
                          "\n  chars (no ws):  " + str(chars_no_sp))

        if mode == "hash":
            target = resolve_path(arg)
            if not target.exists(): return False, "no file: " + arg
            import hashlib
            data = target.read_bytes()
            md5 = hashlib.md5(data).hexdigest()
            sha1 = hashlib.sha1(data).hexdigest()
            return True, ("[HASH] " + arg +
                          "\n  md5:  " + md5 +
                          "\n  sha1: " + sha1 +
                          "\n  size: " + str(len(data)) + " bytes")

        if mode == "calc":
            # CALC: 2+2*3
            import ast, operator as op
            expr = arg.strip()
            if not expr: return False, "format: CALC: 2+2*3"
            allowed = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
                        ast.Div: op.truediv, ast.Pow: op.pow, ast.Mod: op.mod,
                        ast.USub: op.neg, ast.UAdd: op.pos, ast.FloorDiv: op.floordiv}
            def _eval(node):
                if isinstance(node, ast.Num): return node.n
                if isinstance(node, ast.Constant): return node.value
                if isinstance(node, ast.BinOp) and type(node.op) in allowed:
                    return allowed[type(node.op)](_eval(node.left), _eval(node.right))
                if isinstance(node, ast.UnaryOp) and type(node.op) in allowed:
                    return allowed[type(node.op)](_eval(node.operand))
                raise ValueError("no")
            try:
                result = _eval(ast.parse(expr, mode="eval").body)
                return True, "[CALC] " + expr + " = " + str(result)
            except Exception as e:
                return False, "calc err: " + str(e)

        if mode == "check":
            if not arg: return False, "format: CHECK: file"
            ok, msg = check_file(resolve_path(arg))
            return ok, "[CHECK] " + arg + "\n  " + msg

        if mode == "snippet":
            name = arg.strip()
            if not name:
                keys = sorted(SNIPPETS.keys())
                out = ["[SNIPPETS] " + str(len(keys)) + " available:", "-"*60]
                for k in keys:
                    out.append("  " + k)
                return True, "\n".join(out)
            if name not in SNIPPETS:
                keys = sorted(SNIPPETS.keys())
                return False, ("unknown snippet: " + name +
                                "\n  available: " + ", ".join(keys))
            return True, SNIPPETS[name]

        if mode == "find": return find_in_file_impl(arg)
        if mode == "stat": return stat_impl(arg)
        if mode == "list": return list_impl(arg)
        if mode == "tree": return tree_impl()
        if mode == "shot":
            raw = (arg or "").strip()
            mode2 = "window"; title = None; delay = 0; save_path = None
            # Имя окна в кавычках имеет приоритет: SHOT: "Donkey Kong"
            rest = raw
            qm = re.search(r'"([^"]+)"', raw)
            if qm:
                title = qm.group(1).strip()
                rest = (raw[:qm.start()] + " " + raw[qm.end():]).strip()
            free = []
            for p in rest.split():
                p_low = p.lower()
                if p_low == "full": mode2 = "full"
                elif p_low == "window": mode2 = "window"
                elif p_low.startswith("wait="):
                    try: delay = int(p_low[5:])
                    except Exception: pass
                elif p_low.startswith("save="):
                    save_path = rest.split("save=", 1)[1].strip().strip('"')
                else: free.append(p)
            if not title and free:
                title = " ".join(free)
            ok, path, size = shot_screen(mode=mode2, title=title,
                                         save_path=save_path, delay=delay)
            if not ok: return False, "err: " + path
            # Метка рядом с картинкой: без неё мост не понимает, что пришёл результат
            label = "IMAGE: " + path + " " + str(size[0]) + "x" + str(size[1])
            clip_ok = shot_to_clipboard(path, label)
            if not clip_ok:
                return False, ("IMAGE: " + path + " " + str(size[0]) + "x" + str(size[1]) +
                               "\n  WARN: clipboard copy failed (install pywin32)")
            return True, label
        if mode == "log":
            try: n = int((arg or "200").strip())
            except: n = 200
            return True, "\n".join([l for l in open(str(LOGS_DIR / "undo.log"), encoding="utf-8", errors="replace").read().splitlines()[-n:]])
        if mode == "ctx": return collect_context(CFG.get("current_project"))
        if mode == "note": return add_note(CFG.get("current_project"), payload.strip() or arg)
        if mode == "task":
            raw = (arg or payload).strip()
            if raw.lower().startswith("add "): return task_add(CFG.get("current_project"), raw[4:].strip())
            if raw.lower().startswith("done "):
                try: n = int(raw[5:].strip())
                except: return False, "format: TASK: done N"
                return task_done(CFG.get("current_project"), n)
            return task_list(CFG.get("current_project"))
        # PowerShell с ожиданием: дожидаемся конца скрипта и отдаём его вывод.
        # В отличие от CMD: run, окно не остаётся висеть — результат сразу уходит в ответ.
        if mode == "ps":
            raw = (arg or payload).strip()
            if not raw:
                return False, "format: PS: <команда PowerShell>"
            try:
                tmo = int(CFG.get("cmd_timeout", 1800) or 0)
            except Exception:
                tmo = 1800
            script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + raw
            try:
                pr = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    cwd=str(AGENT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=(tmo if tmo > 0 else None))
                out = (pr.stdout or b"").decode("utf-8", errors="replace").strip()
                if pr.returncode != 0:
                    return False, "err: exit " + str(pr.returncode) + ("\n" + out if out else "")
                return True, out if out else "done"
            except subprocess.TimeoutExpired:
                return False, ("err: timeout " + str(tmo) + "s — увеличьте \"cmd_timeout\" "
                               "в config.json или поставьте 0 (без лимита)")
            except Exception as e:
                return False, "err: " + str(e)
        if mode == "cmd":
            raw = (arg or payload).strip(); c = raw.lower()
            if c in ("status","info"):
                return True, ("project: " + str(CFG.get("current_project") or "(none)") +
                              "\npython: " + sys.version.split()[0])
            if c == "tree": return tree_impl()
            if c in ("reset","clear"): self.last_clip = ""; return True, "buffer reset"
            # Модель часто просит «CMD: start powershell -NoExit -Command ...».
            # Такой запуск не ждёт конца и ломает кавычки в cmd.exe — ответ теряется.
            # Перехватываем и выполняем скрипт сами, дожидаясь завершения.
            mps = re.match(r'^(?:start\s+)?powershell[^\n]*?(?:-command|-c)\s+(.+)$', raw, re.I | re.S)
            if mps:
                script = mps.group(1).strip()
                if len(script) >= 2 and script[0] == script[-1] and script[0] in "\"'":
                    script = script[1:-1]
                try:
                    ptmo = int(CFG.get("cmd_timeout", 1800) or 0)
                except Exception:
                    ptmo = 1800
                try:
                    pr = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script],
                        cwd=str(AGENT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        timeout=(ptmo if ptmo > 0 else None))
                    out = (pr.stdout or b"").decode("utf-8", errors="replace").strip()
                    if pr.returncode != 0:
                        return False, "err: exit " + str(pr.returncode) + ("\n" + out if out else "")
                    return True, out if out else "done"
                except subprocess.TimeoutExpired:
                    return False, ("err: timeout " + str(ptmo) + "s\n" +
                                   "увеличьте \"cmd_timeout\" в config.json или поставьте 0")
                except Exception as e:
                    return False, "err: " + str(e)
            if c.startswith("run "):
                cmd = raw[4:].strip()
                try:
                    cf = 0x00000010 if os.name == "nt" else 0
                    subprocess.Popen(cmd, shell=True, cwd=str(AGENT_DIR), creationflags=cf)
                    return True, "run: " + cmd
                except Exception as e: return False, "err: " + str(e)
            if c.startswith("open "):
                fname = raw[5:].strip(); target = resolve_path(fname)
                if not target.exists(): return False, "no file"
                try: os.startfile(str(target)); return True, "opened: " + fname
                except Exception as e: return False, "err: " + str(e)
            if c in ("folder","dir","explorer"):
                try: os.startfile(str(AGENT_DIR)); return True, "folder opened"
                except Exception as e: return False, "err: " + str(e)
            # Долгие операции (поиск и копирование файлов) не обрываем:
            # лимит из настроек, 0 = ждать до завершения.
            try:
                tmo = int(CFG.get("cmd_timeout", 1800) or 0)
            except Exception:
                tmo = 1800
            try:
                out = subprocess.check_output(raw, shell=True, cwd=str(AGENT_DIR),
                                              stderr=subprocess.STDOUT,
                                              timeout=(tmo if tmo > 0 else None)
                                              ).decode("cp866", errors="replace")
                return True, out if out.strip() else "done"
            except subprocess.TimeoutExpired:
                return False, ("err: timeout " + str(tmo) + "s\n" +
                               "увеличьте \"cmd_timeout\" в config.json или поставьте 0 (без лимита)")
            except subprocess.CalledProcessError as e:
                txt = ""
                try: txt = e.output.decode("cp866", errors="replace")
                except Exception: pass
                return False, "err: exit " + str(e.returncode) + (("\n" + txt) if txt.strip() else "")
            except Exception as e: return False, "err: " + str(e)
        return False, "unknown: " + mode

    # ---------------- МЕНЮ ----------------
    def _refresh_recent(self):
        self.recent_menu.delete(0, "end")
        recents = CFG.get("recent_projects", [])
        if not recents: self.recent_menu.add_command(label="(empty)", state="disabled")
        else:
            for path in recents[:20]:
                self.recent_menu.add_command(label=path,
                    command=lambda p=path: self._load_project(p))

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="Project folder")
        if folder: self._load_project(folder)

    def _load_project(self, path):
        CFG["current_project"] = path
        recents = CFG.get("recent_projects", [])
        if path in recents: recents.remove(path)
        recents.insert(0, path)
        CFG["recent_projects"] = recents[:20]
        save_config(CFG)
        ensure_agent_meta(path)
        self._refresh_recent()
        self._print("project: " + path, "ok")

    def _save_context(self):
        if not CFG.get("current_project"):
            self._print("no project", "warn"); return
        ok, ctx = collect_context(CFG["current_project"])
        if ok and clip_set(ctx):
            self._print("context → clipboard", "ok")

    def _edit_desc(self):
        if not CFG.get("current_project"):
            self._print("no project", "warn"); return
        ad = ensure_agent_meta(CFG["current_project"])
        if not ad: return
        desc = ad / "desc.md"
        w = tk.Toplevel(self); w.title("Description")
        w.geometry("600x400"); w.configure(bg=BG)
        txt = tk.Text(w, bg=BG, fg=FG, font=(CFG["font_family"], CFG["font_size"]),
                       relief="flat", padx=10, pady=10, wrap="word",
                       insertbackground=FG)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        if desc.exists(): txt.insert("1.0", desc.read_text(encoding="utf-8"))
        def _save():
            (ad / "desc.md").write_text(txt.get("1.0", "end-1c"), encoding="utf-8")
            self._print("desc saved", "ok")
            w.destroy()
        tk.Button(w, text="Save", command=_save, bg=BG, fg=FG,
                   relief="flat", font=(CFG["font_family"], CFG["font_size"])
                   ).pack(side="right", padx=10, pady=6)

    def _paste_input(self):
        t = clip_get()
        if t:
            self.entry.delete(0, "end")
            self.entry.insert(0, t.split("\n")[0])

    def _copy_log(self):
        txt = self.log.get("1.0", "end-1c")
        if clip_set(txt): self._print("log copied", "ok")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open(self, p):
        try: os.startfile(str(p))
        except: pass

    def _open_project(self):
        if CFG.get("current_project"):
            try: os.startfile(CFG["current_project"])
            except: pass

    def _set_size(self, n):
        CFG["font_size"] = n; save_config(CFG)
        self._print("font size: " + str(n) + " — restarting...", "warn")
        self.after(500, self._restart_self)

    def _set_family(self, name):
        CFG["font_family"] = name; save_config(CFG)
        self._print("font family: " + name + " — restarting...", "warn")
        self.after(500, self._restart_self)

    def _show_cmds(self):
        cmds = (
            "== Files ==\n"
            "FILE: path            create / overwrite\n"
            "EDIT: path            FIND/REPLACE blocks\n"
            "APPEND: path          append to end\n"
            "DELETE: path          delete (with backup)\n"
            "RENAME: old -> new    rename\n"
            "TOUCH: path           create empty file\n"
            "MKDIR: path           create folder (recursive)\n"
            "\n"
            "== Read and search ==\n"
            "READ: path            read with line numbers\n"
            "READ: path | 50-120   read a range of lines\n"
            "HEAD/TAIL: path | N   first / last N lines\n"
            "STAT: path            size, date, lines, chars\n"
            "FIND: path | text     search inside one file\n"
            "SEARCH: text          search across the project\n"
            "REGEX: path | pattern regular expression search\n"
            "LIST: path            list files in a folder\n"
            "TREE:                 project tree\n"
            "COUNT: path           lines, words, chars\n"
            "HASH: path            md5 and sha1\n"
            "\n"
            "== Backups ==\n"
            "BACKUPS: [path]       list backups\n"
            "RESTORE: path | N     restore N-th backup\n"
            "DIFF: path            unified diff vs last backup\n"
            "CLEANUP: keep=N       delete old backups\n"
            "BACKUP_RULE:          keep=N | age=N | run | show\n"
            "\n"
            "== Tools ==\n"
            "CHECK: path           syntax check (py / json / html / js)\n"
            "CALC: expression      safe calculator\n"
            "SNIPPET: [name]       list or insert a code template\n"
            "CMD: run/open/folder  system commands\n"
            "CMD: status           current project and Python version\n"
            "\n"
            "== Screenshots ==\n"
            "SHOT:                 active window -> clipboard\n"
            "SHOT: full            whole screen -> clipboard\n"
            "SHOT: \"Name\"          window by name\n"
            "SHOT: wait=N          shot after N seconds\n"
            "SHOT: save=path.png   save to a specific path\n"
            "\n"
            "== Context, notes, tasks ==\n"
            "CTX:                  assemble project context\n"
            "NOTE: text            add a note\n"
            "TASK: add/list/done   tasks\n"
            "REMIND: ctx=N         reminder every N edits\n"
            "REMIND: off|reset     disable | reset counter\n"
            "LOG: N                last N log lines"
        )
        w = tk.Toplevel(self); w.title("Commands"); w.configure(bg=BG)
        w.geometry("640x480")
        t = tk.Text(w, bg=BG, fg=FG, font=(CFG["font_family"], CFG["font_size"]),
                     relief="flat", padx=10, pady=10)
        t.pack(fill="both", expand=True)
        t.insert("1.0", cmds)
        t.configure(state="disabled")

    def _about(self):
        text = (
            "UNDO — a command station for AI\n\n"
            "Love each other.\n"
            "Be patient. Respect one another. Value one another. Think about life.\n\n"
            "There is nothing more important than that.\n"
            "Not success, not money, not even this program.\n"
            "All of it ends. Only what we give to others stays.\n\n"
            "I built Undo so you could build something of your own —\n"
            "a small idea, a small step, a small kindness turned into code.\n"
            "If you're reading this, you already know what to do next.\n\n"
            "If you want to say hello, share a thought, or show me what you made —\n"
            "write to me. I read every message.\n\n"
            "✉  NickBass@bk.ru"
        )
        # Показать в отдельном окне с Copy — потому что messagebox не даёт копировать
        w = tk.Toplevel(self)
        w.title("About Undo")
        w.geometry("560x480")
        w.configure(bg=BG)
        t = tk.Text(w, bg=BG, fg=FG,
                    font=(CFG.get("font_family","Consolas"), CFG.get("font_size",11)),
                    relief="flat", padx=16, pady=16, wrap="word",
                    insertbackground=FG, highlightthickness=0)
        t.pack(fill="both", expand=True, padx=8, pady=8)
        t.insert("1.0", text)
        t.configure(state="disabled")
        btn = tk.Frame(w, bg=BG); btn.pack(fill="x", padx=8, pady=(0,8))
        tk.Button(btn, text="📋 Copy", bg=BG, fg=FG, relief="flat",
                  font=(CFG.get("font_family","Consolas"), CFG.get("font_size",11)),
                  command=lambda: clip_set(text)).pack(side="right")
        tk.Button(btn, text="Close", bg=BG, fg=FG, relief="flat",
                  font=(CFG.get("font_family","Consolas"), CFG.get("font_size",11)),
                  command=w.destroy).pack(side="right", padx=6)

    def _current_manifest(self):
        lang = CFG.get("language", "en")
        return MANIFESTS.get(lang, MANIFESTS["en"])

    def _show_manifest(self):
        w = tk.Toplevel(self); w.title("AI Manifest"); w.configure(bg=BG)
        w.geometry("720x520")

        top = tk.Frame(w, bg=BG); top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="Lang:", bg=BG, fg=FG,
                 font=(CFG["font_family"], CFG["font_size"]-1)).pack(side="left")

        lang_var = tk.StringVar(value=CFG.get("language", "en"))
        txt = tk.Text(w, bg=BG, fg=FG, font=(CFG["font_family"], CFG["font_size"]-1),
                       relief="flat", padx=10, pady=10, wrap="word",
                       insertbackground=FG)

        def _refresh():
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", MANIFESTS.get(lang_var.get(), MANIFESTS["en"]))
            txt.configure(state="disabled")

        for code, label in [("ru","RU"),("en","EN"),("zh","ZH")]:
            tk.Radiobutton(top, text=label, variable=lang_var, value=code,
                            command=_refresh, bg=BG, fg=FG,
                            selectcolor=BG, activebackground=BG, activeforeground=FG,
                            font=(CFG["font_family"], CFG["font_size"]-1)
                            ).pack(side="left", padx=4)

        def _copy():
            if clip_set(MANIFESTS.get(lang_var.get(), MANIFESTS["ru"])):
                self._print("manifest (" + lang_var.get() + ") copied", "ok")
            w.destroy()
        tk.Button(top, text="📋 Copy", command=_copy, bg=BG, fg=FG,
                   relief="flat", font=(CFG["font_family"], CFG["font_size"])
                   ).pack(side="right", padx=6)

        txt.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        _refresh()

    def _copy_manifest(self):
        if clip_set(self._current_manifest()):
            self._print("manifest copied", "ok")

    def _set_lang(self, code):
        CFG["language"] = code
        save_config(CFG)
        # маркер для дополнения в браузере: оно подхватит язык и перерисует панель
        try: clip_set("UNDO_LANG: " + code)
        except Exception: pass
        self._print("language: " + code + " — restarting...", "warn")
        self.after(600, self._restart_self)

    def _on_exit(self):
        CFG["window_geometry"] = self.geometry()
        save_config(CFG)
        self.destroy()


if __name__ == "__main__":
    # --headless: окна нет, движок живёт в фоне и общается только через буфер.
    # Так выглядит установка, где весь интерфейс — в браузерном дополнении.
    headless = ("--headless" in sys.argv) or bool(CFG.get("headless", False))
    try:
        app = Undo()
        app.protocol("WM_DELETE_WINDOW", app._on_exit)
        if headless:
            app.withdraw()
            app._print("headless: окно скрыто, работает только обмен через буфер", "warn")
        app.mainloop()
    except Exception as e:
        import traceback
        print("ERROR:", e); traceback.print_exc(); input("Enter...")