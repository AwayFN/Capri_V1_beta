#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAPRI // NEURAL LAUNCHER
========================
Lanceur d'applications au style cyberpunk (Tkinter, aucune dépendance externe).
 
Commandes
    /open app <nom>   ouvre n'importe quelle application installée sur le PC
    /list [filtre]    liste les applications détectées
    /refresh          relance l'indexation des applications   (F5)
    /clear            efface le terminal                      (Ctrl+L)
    /help             affiche l'aide
    /exit             quitte Capri
 
Comment /open app retrouve une application (dans cet ordre)
    1. chemin complet, URL ou protocole donné tel quel (steam://..., ms-settings:)
    2. index : menu Démarrer (programmes classiques ET apps du Microsoft Store),
       bureau, barre des tâches, raccourcis Steam...
    3. outils système (calc, notepad...), PATH et clés « App Paths » du registre
    4. recherche approfondie des .exe dans Program Files, AppData et dossiers de jeux
Les noms tolèrent majuscules, accents, fautes de frappe et noms partiels.
 
Lancer :  python capri.py        (Python 3.8+ ; pensé pour Windows)
          Astuce : renomme le fichier en capri.pyw pour ne pas voir la console.
"""
 
import base64
import json
import os
import queue
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
 
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VERSION = "1.0"
LOGO = "C A P R I"
 
# ═════════════════════════════ Palette néon ═════════════════════════════
BG = "#07070d"       # fond général
PANEL = "#0b0b15"    # intérieur des panneaux
GRID = "#10101c"     # quadrillage et scanlines
YELLOW = "#fcee0a"
CYAN = "#00f0ff"
PINK = "#ff2a6d"
PURPLE = "#a06bff"
MINT = "#00ff9c"
TEXT = "#d8e2ff"
DIM = "#6b7194"
 
SCALE = 1.0  # ajusté au démarrage selon le DPI de l'écran
 
 
# ═════════════════════════════ Utilitaires ═════════════════════════════
def px(value):
    """Adapte une taille en pixels au zoom de l'écran (écrans HiDPI)."""
    return max(1, int(round(value * SCALE)))
 
 
def mix(c1, c2, t):
    """Mélange deux couleurs #rrggbb : t=0 -> c1, t=1 -> c2 (sert aux effets de lueur)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))
 
 
def normalize(text):
    """Minuscules, sans accents ni ponctuation : « Paramètres-PC » -> « parametres pc »."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", text.lower()).strip()
 
 
def bevel(x0, y0, x1, y1, cut):
    """Rectangle aux coins biseautés en haut à gauche et en bas à droite (silhouette « HUD »)."""
    return [x0 + cut, y0, x1, y0, x1, y1 - cut, x1 - cut, y1, x0, y1, x0, y0 + cut]
 
 
def pick_font(root, candidates, fallback):
    """Premier nom de police réellement installé parmi `candidates`."""
    available = {name.lower() for name in tkfont.families(root)}
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback
 
 
def colorref(hex_color):
    """#rrggbb -> entier COLORREF (0x00BBGGRR) attendu par l'API Windows."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r
 
 
def parse_open_arg(rest):
    """'app discord' -> 'discord' ; 'discord' -> 'discord' (le mot « app » est facultatif)."""
    rest = re.sub(r"(?i)^\s*app(?:\s+|$)", "", rest.strip())
    return rest.strip().strip("\"'").strip()
 
 
URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]{2,}:", re.I)
 
 
def resolve_direct_target(name):
    """URL, protocole (ms-settings:, steam://...) ou chemin existant -> cible telle quelle, sinon None."""
    if URL_RE.match(name):
        return name
    path = os.path.expandvars(os.path.expanduser(name))
    if os.path.exists(path) and (os.path.isabs(path) or os.sep in path or "/" in path):
        return path
    return None
 
 
# ═════════════════════════ Index des applications ═════════════════════════
JUNK_NAME = re.compile(
    r"\b(unins\w*|desinstall\w*|readme|release notes|licen[cs]e|documentation|user guide)\b")
JUNK_EXE = re.compile(
    r"^(unins|uninstall|setup|install|update|crash|vc_?redist|dxsetup|dotnet|ndp\d|"
    r"notification_?helper|elevation)|(helper|updater|crashpad\w*|crashreporter)$", re.I)
SKIP_DIRS = {
    "temp", "tmp", "cache", "code cache", "gpucache", "user data", "node_modules", "packages",
    "crashdumps", "windowsapps", "$recycle.bin", "system volume information", "installer",
    "winsxs", ".git", "__pycache__", "site-packages", "logs", "redist", "_commonredist",
    "directx", "common files", "windows nt", "windows defender", "reference assemblies",
    "msbuild", "dotnet",
}
SHORTCUT_EXT = (".lnk", ".url", ".appref-ms")
LINUX_APP_DIRS = [
    "/usr/share/applications", "/usr/local/share/applications",
    os.path.expanduser("~/.local/share/applications"),
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
    "/var/lib/snapd/desktop/applications",
]
MAC_APP_DIRS = [
    "/Applications", "/Applications/Utilities", "/System/Applications",
    "/System/Applications/Utilities", os.path.expanduser("~/Applications"),
]
# Outils système (noms FR/EN) -> commande acceptée par ShellExecute
SYSTEM_ALIASES = {
    "calculator": "calc", "calculatrice": "calc",
    "notepad": "notepad", "bloc notes": "notepad", "blocnotes": "notepad",
    "paint": "mspaint",
    "task manager": "taskmgr", "gestionnaire des taches": "taskmgr",
    "control panel": "control", "panneau de configuration": "control",
    "settings": "ms-settings:", "parametres": "ms-settings:", "reglages": "ms-settings:",
    "explorer": "explorer", "explorateur": "explorer", "explorateur de fichiers": "explorer",
    "cmd": "cmd", "invite de commandes": "cmd", "command prompt": "cmd",
    "powershell": "powershell", "regedit": "regedit", "registre": "regedit",
    "snipping tool": "snippingtool", "outil capture": "snippingtool",
    "device manager": "devmgmt.msc", "gestionnaire de peripheriques": "devmgmt.msc",
    "services": "services.msc", "winver": "winver", "loupe": "magnify", "magnifier": "magnify",
}
 
 
@dataclass
class AppEntry:
    name: str
    target: str
    source: str = ""
    key: str = field(init=False, default="")
    words: list = field(init=False, default_factory=list)
    acronym: str = field(init=False, default="")
    shorts: set = field(init=False, default_factory=set)
 
    def __post_init__(self):
        self.key = normalize(self.name)
        self.words = self.key.split()
        self.acronym = "".join(w[0] for w in self.words)
        # formes abrégées : « vsc » et « vscode » pour « Visual Studio Code »
        if len(self.words) > 1:
            self.shorts.add(self.acronym)
            for i in range(1, len(self.words)):
                head = "".join(w[0] for w in self.words[:i])
                self.shorts.add(head + "".join(self.words[i:]))
 
 
def walk_files(root, max_depth):
    """Génère (nom, chemin) de chaque fichier de `root`, jusqu'à `max_depth` sous-dossiers."""
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                stack.append((entry.path, depth + 1))
                        else:
                            yield entry.name, entry.path
                    except OSError:
                        continue
        except OSError:
            continue
 
 
def windows_desktop_dir():
    """Dossier Bureau réel (gère la redirection OneDrive)."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")
 
 
def scan_windows_shortcuts():
    """Raccourcis du menu Démarrer, du bureau et de la barre des tâches."""
    env = os.environ
    roots = []
    for var in ("ProgramData", "APPDATA"):
        if env.get(var):
            roots.append((os.path.join(env[var], "Microsoft", "Windows", "Start Menu", "Programs"), 6))
    roots.append((windows_desktop_dir(), 0))
    roots.append((os.path.join(env.get("PUBLIC", r"C:\Users\Public"), "Desktop"), 0))
    if env.get("APPDATA"):
        roots.append((os.path.join(env["APPDATA"], "Microsoft", "Internet Explorer",
                                   "Quick Launch", "User Pinned", "TaskBar"), 0))
    found = []
    for root, depth in roots:
        if not os.path.isdir(root):
            continue
        for fname, path in walk_files(root, depth):
            stem, ext = os.path.splitext(fname)
            if ext.lower() in SHORTCUT_EXT:
                found.append(AppEntry(stem, path, "shortcut"))
    return found
 
 
def scan_windows_start_apps():
    """Toutes les apps du menu Démarrer (Win32 + Microsoft Store) via PowerShell Get-StartApps."""
    ps = shutil.which("powershell") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    script = ("$a = @(Get-StartApps | Select-Object Name, AppID); "
              "$j = ConvertTo-Json -InputObject $a -Compress; "
              "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($j))")
    out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, timeout=30, creationflags=NO_WINDOW)
    raw = out.stdout.strip()
    if out.returncode != 0 or not raw:
        return []
    data = json.loads(base64.b64decode(raw).decode("utf-8"))
    if isinstance(data, dict):
        data = [data]
    found = []
    for item in data:
        name, app_id = item.get("Name"), item.get("AppID")
        if not name or not app_id:
            continue
        target = app_id if re.match(r"(?i)^https?://", app_id) else "shell:AppsFolder\\" + app_id
        found.append(AppEntry(name, target, "startapps"))
    return found
 
 
def scan_macos_apps():
    found = []
    for folder in MAC_APP_DIRS:
        try:
            for item in os.listdir(folder):
                if item.endswith(".app"):
                    found.append(AppEntry(item[:-4], os.path.join(folder, item), "app"))
        except OSError:
            continue
    return found
 
 
def scan_linux_desktop_files():
    found = []
    for folder in LINUX_APP_DIRS:
        if not os.path.isdir(folder):
            continue
        for fname, path in walk_files(folder, 1):
            if not fname.endswith(".desktop"):
                continue
            name = exec_line = None
            hidden = terminal = in_entry = False
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("["):
                            in_entry = line == "[Desktop Entry]"
                            continue
                        if not in_entry or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        if k == "Name" and name is None:
                            name = v
                        elif k == "Exec":
                            exec_line = v
                        elif k in ("NoDisplay", "Hidden") and v.lower() == "true":
                            hidden = True
                        elif k == "Terminal" and v.lower() == "true":
                            terminal = True
            except OSError:
                continue
            if name and exec_line and not hidden and not terminal:
                command = re.sub(r"%[a-zA-Z]", "", exec_line).strip()
                found.append(AppEntry(name, "exec:" + command, "desktop"))
    return found
 
 
def app_paths_lookup(name):
    """Cherche <name>.exe dans les clés « App Paths » du registre Windows."""
    if not IS_WIN:
        return None
    import winreg
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as k:
                    value = os.path.expandvars(winreg.QueryValueEx(k, "")[0].strip('"'))
                    if os.path.exists(value):
                        return value
            except OSError:
                continue
    return None
 
 
def install_roots():
    """Dossiers où les programmes s'installent, avec la profondeur maximale de recherche."""
    env = os.environ
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if env.get(var):
            roots.append((env[var], 4))
    for var in ("LOCALAPPDATA", "APPDATA"):
        if env.get(var):
            roots.append((env[var], 3))
    for letter in "CDEFGH":
        for sub in ("Program Files", "Program Files (x86)", "Games", "Jeux", "XboxGames",
                    "Epic Games", "GOG Games", "SteamLibrary", "Steam", "Riot Games"):
            roots.append((f"{letter}:\\{sub}", 4))
    seen, result = set(), []
    for path, depth in roots:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isdir(path):
            seen.add(key)
            result.append((path, depth))
    return result
 
 
class AppIndex:
    """Index des applications installées + recherche floue + lancement."""
 
    def __init__(self):
        self.entries = []
        self.ready = False
        self._scanning = False
 
    # ───── indexation (thread d'arrière-plan) ─────
    def rescan_async(self, notify):
        """Lance l'indexation sans bloquer l'interface. notify(stage, count) : 'partial' puis 'ready'."""
        if self._scanning:
            return False
        self._scanning = True
        self.ready = False
        threading.Thread(target=self._scan, args=(notify,), daemon=True).start()
        return True
 
    def _scan(self, notify):
        found = {}
 
        def merge(entries):
            for e in entries:
                if e.key and not JUNK_NAME.search(e.key):
                    found.setdefault(e.key, e)  # le premier arrivé gagne (raccourci > StartApps)
 
        def publish(stage):
            self.entries = sorted(found.values(), key=lambda e: e.key)
            notify(stage, len(self.entries))
 
        if IS_WIN:
            steps = [(scan_windows_shortcuts, "partial"), (scan_windows_start_apps, None)]
        elif IS_MAC:
            steps = [(scan_macos_apps, None)]
        else:
            steps = [(scan_linux_desktop_files, None)]
        for scanner, stage in steps:
            try:
                merge(scanner())
            except Exception:
                pass
            if stage:
                publish(stage)
        self._scanning = False
        self.ready = True
        publish("ready")
 
    # ───── recherche ─────
    @staticmethod
    def _score(q, qc, e):
        k = e.key
        if k == q:
            return 100
        kc = k.replace(" ", "")
        if kc == qc:
            return 98
        if all(w in e.words for w in q.split()):
            return 92
        if k.startswith(q):
            return 88
        if re.search(r"\b" + re.escape(q), k):
            return 82
        if len(qc) >= 2 and qc in e.shorts:
            return 80
        if q in k:
            return 74
        if len(qc) >= 3 and qc in kc:
            return 70
        if len(qc) >= 2 and e.acronym.startswith(qc):
            return 66
        if len(qc) >= 3:
            ratio = max(SequenceMatcher(None, qc, c).ratio()
                        for c in [kc] + [w for w in e.words if len(w) >= 3])
            if ratio >= 0.55:
                return int(ratio * 75)
        return 0
 
    def search(self, query, limit=8, min_score=55):
        """[(score, AppEntry)] triés du meilleur au moins bon."""
        q = normalize(query)
        if not q:
            return []
        qc = q.replace(" ", "")
        hits = []
        for e in self.entries:
            s = self._score(q, qc, e)
            if s >= min_score:
                hits.append((s, e))
        hits.sort(key=lambda h: (-h[0], len(h[1].key), h[1].key))
        return hits[:limit]
 
    @staticmethod
    def find_executable(name):
        """PATH puis clés « App Paths » (Windows uniquement)."""
        if not IS_WIN:
            return None
        return shutil.which(name) or app_paths_lookup(name)
 
    def deep_search(self, query, budget=20.0):
        """Dernier recours : cherche un .exe dans les dossiers d'installation (à lancer dans un thread)."""
        q = normalize(query)
        qc = q.replace(" ", "")
        if len(qc) < 2 or not IS_WIN:
            return None
        deadline = time.monotonic() + budget
        best = None  # (score, -profondeur, taille, chemin)
        for root, max_depth in install_roots():
            if time.monotonic() > deadline:
                break
            base = root.rstrip("\\/").count(os.sep)
            for folder, dirs, files in os.walk(root):
                if time.monotonic() > deadline:
                    break
                depth = folder.count(os.sep) - base
                if depth >= max_depth:
                    dirs[:] = []
                else:
                    dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
                folder_key = normalize(os.path.basename(folder)).replace(" ", "")
                folder_hit = bool(folder_key) and (
                    folder_key == qc or (len(qc) >= 3 and qc in folder_key))
                for fname in files:
                    stem, ext = os.path.splitext(fname)
                    if ext.lower() != ".exe" or JUNK_EXE.search(stem):
                        continue
                    sc = normalize(stem).replace(" ", "")
                    if not sc:
                        continue
                    if sc == qc:
                        score = 100
                    elif len(qc) >= 3 and sc.startswith(qc):
                        score = 85
                    elif len(qc) >= 3 and qc in sc:
                        score = 72
                    elif folder_hit:
                        score = 60  # .exe d'un dossier portant le nom de l'appli : le plus gros gagne
                    else:
                        continue
                    path = os.path.join(folder, fname)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    cand = (score, -depth, size, path)
                    if best is None or cand[:3] > best[:3]:
                        best = cand
        return best[3] if best else None
 
    # ───── lancement ─────
    @staticmethod
    def launch(target):
        """Lance `target` sans jamais bloquer l'interface."""
        if IS_WIN:
            try:
                if target.lower().endswith(".exe") and os.path.isabs(target):
                    try:
                        os.startfile(target, cwd=os.path.dirname(target))
                        return
                    except TypeError:  # Python < 3.10 : pas de paramètre cwd
                        pass
                os.startfile(target)
            except OSError:
                if not target.lower().startswith("shell:"):
                    raise
                subprocess.Popen(["explorer.exe", target], creationflags=NO_WINDOW)
        elif IS_MAC:
            subprocess.Popen(["open", target])
        elif target.startswith("exec:"):
            subprocess.Popen(shlex.split(target[5:]), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", target], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
 
 
# ═════════════════════════ Composants graphiques ═════════════════════════
class NeonPanel(tk.Canvas):
    """Panneau HUD à coins coupés avec lueur néon. Les widgets se placent dans `.body`."""
 
    def __init__(self, master, color, title, font, **canvas_options):
        super().__init__(master, bg=BG, highlightthickness=0, bd=0, **canvas_options)
        self.base_color = self.color = color
        self.title_text = title
        self.title_font = font
        self.cut = px(16)
        self.body = tk.Frame(self, bg=PANEL)
        self._win = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._draw)
 
    def set_title(self, title):
        if title != self.title_text:
            self.title_text = title
            self._draw()
 
    def set_color(self, color):
        self.color = color
        self._draw()
 
    def flash(self, color, ms=450):
        self.set_color(color)
        self.after(ms, lambda: self.set_color(self.base_color))
 
    def _draw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < px(60) or h < px(40):
            return
        self.delete("deco")
        m, c = px(6), self.cut
        x0, x1, y1 = m, w - m, h - m
        y0 = px(11) if self.title_text else m
        pts = bevel(x0, y0, x1, y1, c)
        for width, t in ((9, .07), (6, .15), (3, .30)):  # lueur : traits larges et sombres dessous
            self.create_polygon(pts, outline=mix(BG, self.color, t), fill="", width=px(width),
                                joinstyle="miter", tags="deco")
        self.create_polygon(pts, outline=self.color, fill=PANEL, width=2, joinstyle="miter", tags="deco")
        # accents décoratifs
        self.create_line(x1 - c - px(64), y1, x1 - c, y1, fill=self.color, width=px(4), tags="deco")
        self.create_line(x0, y1 - px(46), x0, y1, fill=self.color, width=px(4), tags="deco")
        for i in range(3):
            sx = x1 - c - px(12) - i * px(12)
            self.create_rectangle(sx, y0 - px(3), sx + px(6), y0 + px(3), fill=self.color,
                                  outline="", tags="deco")
        if self.title_text:
            txt = self.create_text(x0 + c + px(22), y0, text=self.title_text, anchor="w",
                                   font=self.title_font, fill=self.color, tags="deco")
            bx = self.bbox(txt)
            gap = self.create_rectangle(bx[0] - px(8), y0 - px(5), bx[2] + px(8), y0 + px(5),
                                        fill=BG, outline="", tags="deco")
            self.tag_lower(gap, txt)
        inset = px(10)
        top = y0 + px(10)
        self.coords(self._win, x0 + inset, top)
        self.itemconfigure(self._win, width=max(px(20), x1 - x0 - 2 * inset),
                           height=max(px(20), y1 - top - inset))
 
 
class NeonButton(tk.Canvas):
    """Bouton à coins coupés : survol = remplissage néon, clic = éclaircissement."""
 
    def __init__(self, master, label, command, color, font, width, height):
        super().__init__(master, width=width, height=height, bg=BG, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._label, self._cmd, self._color, self._font = label, command, color, font
        self._mode = "idle"
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_mode("hover"))
        self.bind("<Leave>", lambda _e: self._set_mode("idle"))
        self.bind("<ButtonPress-1>", lambda _e: self._set_mode("down"))
        self.bind("<ButtonRelease-1>", self._on_release)
 
    def _set_mode(self, mode):
        self._mode = mode
        self._draw()
 
    def _on_release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._set_mode("hover" if inside else "idle")
        if inside:
            self._cmd()
 
    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < px(40) or h < px(30):
            return
        self.delete("all")
        m, c = px(6), px(14)
        pts = bevel(m, m, w - m, h - m, c)
        active = self._mode != "idle"
        fill = {"idle": PANEL, "hover": self._color,
                "down": mix(self._color, "#ffffff", 0.35)}[self._mode]
        for width, t in ((9, .08), (6, .17), (3, .32)):
            glow = mix(BG, self._color, min(1.0, t * (1.7 if active else 1.0)))
            self.create_polygon(pts, outline=glow, fill="", width=px(width), joinstyle="miter")
        self.create_polygon(pts, outline=self._color, fill=fill, width=2, joinstyle="miter")
        self.create_text(w // 2, h // 2, text=self._label, font=self._font,
                         fill=BG if active else self._color)
 
 
class Backdrop(tk.Canvas):
    """Fond de fenêtre : quadrillage discret + repères d'angle."""
 
    def __init__(self, master):
        super().__init__(master, bg=BG, highlightthickness=0, bd=0)
        self.bind("<Configure>", self._draw)
 
    def _draw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        step = px(36)
        for x in range(0, w, step):
            self.create_line(x, 0, x, h, fill=GRID)
        for y in range(0, h, step):
            self.create_line(0, y, w, y, fill=GRID)
        arm, m = px(34), px(5)
        for cx, cy, sx, sy, color in ((m, m, 1, 1, YELLOW), (w - m, m, -1, 1, CYAN),
                                      (m, h - m, 1, -1, CYAN), (w - m, h - m, -1, -1, PINK)):
            self.create_line(cx, cy + sy * arm, cx, cy, cx + sx * arm, cy, fill=color, width=2)
 
 
class Header(tk.Canvas):
    """Bandeau du haut : logo à aberration chromatique (glitch), horloge, bande hazard, balayage."""
 
    def __init__(self, master, fonts):
        super().__init__(master, height=px(96), bg=BG, highlightthickness=0, bd=0)
        self.fonts = fonts
        self._tx = self._ty = 0
        self._sweep_x = 0
        self.bind("<Configure>", self._draw)
        self.after(1000, self._tick_clock)
        self.after(40, self._tick_sweep)
        self.after(2200, self._glitch)
 
    def _hazard(self, x0, y0, x1, y1):
        """Bande de chantier jaune/noire, clippée entre x0 et x1."""
        stripe, hgt = px(10), y1 - y0
        x = x0 - hgt
        while x < x1:
            self.create_polygon(x, y1, x + stripe, y1, x + stripe + hgt, y0, x + hgt, y0,
                                fill=YELLOW, outline="")
            x += stripe * 2
        self.create_rectangle(x0 - hgt - 2 * stripe, y0 - 1, x0, y1 + 1, fill=BG, outline="")
        self.create_rectangle(x1, y0 - 1, x1 + hgt + 2 * stripe, y1 + 1, fill=BG, outline="")
 
    def _draw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < px(100):
            return
        self.delete("all")
        f = self.fonts
        for y in range(0, h, px(4)):  # scanlines
            self.create_line(0, y, w, y, fill=GRID)
        self.create_rectangle(px(4), px(16), px(10), h - px(24), fill=YELLOW, outline="")
        self._tx, self._ty = px(30), h // 2 - px(6)
        for tag, color, dx in (("t_pink", PINK, -3), ("t_cyan", CYAN, 3), ("t_main", YELLOW, 0)):
            self.create_text(self._tx + px(dx), self._ty, text=LOGO, anchor="w",
                             font=f.title, fill=color, tags=tag)
        x2 = self.bbox("t_main")[2] + px(26)
        self.create_text(x2, self._ty - px(12), text="NEURAL LAUNCHER", anchor="w",
                         font=f.label, fill=CYAN)
        self.create_text(x2, self._ty + px(8), text=f"SYS.LINK // v{VERSION} // EDGERUNNER PROTOCOL",
                         anchor="w", font=f.small, fill=DIM)
        now = datetime.now()
        self.create_text(w - px(26), px(28), text=now.strftime("%H:%M:%S"), anchor="e",
                         font=f.clock, fill=CYAN, tags="clock")
        self.create_text(w - px(26), px(50), text=now.strftime("%d.%m.%Y"), anchor="e",
                         font=f.small, fill=DIM, tags="date")
        self._hazard(w - px(236), px(60), w - px(26), px(69))
        y = h - px(5)
        self.create_line(0, y, w, y, fill=mix(BG, YELLOW, .30), width=2)
        self.create_rectangle(self._sweep_x, y - px(2), self._sweep_x + px(160), y + px(2),
                              fill=CYAN, outline="", tags="sweep")
 
    def _tick_clock(self):
        try:
            now = datetime.now()
            self.itemconfigure("clock", text=now.strftime("%H:%M:%S"))
            self.itemconfigure("date", text=now.strftime("%d.%m.%Y"))
            self.after(1000, self._tick_clock)
        except tk.TclError:
            pass
 
    def _tick_sweep(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
            length = px(160)
            self._sweep_x += px(9)
            if self._sweep_x > w:
                self._sweep_x = -length
            y = h - px(5)
            self.coords("sweep", self._sweep_x, y - px(2), self._sweep_x + length, y + px(2))
            self.after(33, self._tick_sweep)
        except tk.TclError:
            pass
 
    def _glitch(self):
        self._glitch_frame(random.randint(4, 8))
 
    def _glitch_frame(self, remaining):
        try:
            if remaining <= 0:
                self.coords("t_pink", self._tx - px(3), self._ty)
                self.coords("t_cyan", self._tx + px(3), self._ty)
                self.coords("t_main", self._tx, self._ty)
                self.itemconfigure("t_main", text=LOGO, fill=YELLOW)
                self.after(random.randint(2200, 5200), self._glitch)
                return
            dx, dy = random.randint(-9, 9), random.randint(-3, 3)
            self.coords("t_pink", self._tx - px(3) - px(dx), self._ty + px(dy))
            self.coords("t_cyan", self._tx + px(3) + px(dx), self._ty - px(dy))
            self.coords("t_main", self._tx + px(random.randint(-2, 2)), self._ty)
            chars = list(LOGO)
            if random.random() < 0.4:
                chars[random.choice((0, 2, 4, 6, 8))] = random.choice("#@%&$01/")
            self.itemconfigure("t_main", text="".join(chars),
                               fill=random.choice((YELLOW, YELLOW, "#ffffff")))
            self.after(45, self._glitch_frame, remaining - 1)
        except tk.TclError:
            pass
 
 
class StatusBar(tk.Canvas):
    """Barre d'état : voyant ONLINE clignotant, état de l'index, rappel des raccourcis."""
 
    def __init__(self, master, fonts):
        super().__init__(master, height=px(28), bg=BG, highlightthickness=0, bd=0)
        self.fonts = fonts
        self.status_text = "INDEX: …"
        self._lit = True
        self.bind("<Configure>", self._draw)
        self.after(650, self._blink)
 
    def set_text(self, text):
        self.status_text = text
        self._draw()
 
    def _draw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < px(100):
            return
        self.delete("all")
        cy = h // 2
        self.create_line(0, px(2), w, px(2), fill=mix(BG, CYAN, .30))
        self.create_oval(px(12), cy - px(4), px(20), cy + px(4),
                         fill=MINT if self._lit else mix(BG, MINT, .25), outline="", tags="dot")
        self.create_text(px(30), cy, text="ONLINE", anchor="w", font=self.fonts.small_b, fill=MINT)
        self.create_text(px(96), cy, text=self.status_text, anchor="w",
                         font=self.fonts.small_b, fill=CYAN)
        self.create_text(w - px(12), cy, anchor="e", font=self.fonts.small, fill=DIM,
                         text="TAB complétion · ↑↓ historique · CTRL+L effacer · F5 réindexer")
 
    def _blink(self):
        try:
            self._lit = not self._lit
            self.itemconfigure("dot", fill=MINT if self._lit else mix(BG, MINT, .25))
            self.after(650, self._blink)
        except tk.TclError:
            pass
 
 
# ═════════════════════════════ Application ═════════════════════════════
PREFIX = {"sys": "[SYS ]", "info": "[INFO]", "ok": "[ OK ]", "err": "[FAIL]",
          "warn": "[WARN]", "echo": "›", "text": "", "plain": ""}
 
 
class CapriApp:
    COMMANDS = ["/open app ", "/list", "/refresh", "/clear", "/help", "/exit"]
 
    def __init__(self, root):
        self.root = root
        self.index = AppIndex()
        self.events = queue.Queue()          # messages venant des threads d'arrière-plan
        self.history, self.hist_pos = [], 0
        self.out, self.printing, self.gen = deque(), False, 0   # file d'affichage « machine à écrire »
        self.pending_open = None             # /open reçu pendant l'indexation
        self.deep_running = False
        self.shown = []                      # entrées affichées dans la liste de droite
        self._tab = None
        self._no_filter = False
        self._announced = False
 
        self._make_fonts()
        self._setup_window()
        self._setup_style()
        self._build_ui()
        self._bind_keys()
        self.root.after(80, self._style_titlebar)
        self.root.after(100, self._pump_events)
        self._boot()
 
    # ───────────────────────── mise en place ─────────────────────────
    def _make_fonts(self):
        mono = pick_font(self.root, ("Cascadia Mono", "Cascadia Code", "Consolas", "Lucida Console",
                                     "DejaVu Sans Mono", "Courier New"), "Courier")
        display = pick_font(self.root, ("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Black",
                                        "Impact", "Arial Black", "DejaVu Sans"), "Helvetica")
        self.f = SimpleNamespace(
            mono=(mono, 11), mono_b=(mono, 11, "bold"), items=(mono, 10), entry=(mono, 14),
            label=(mono, 9, "bold"), small=(mono, 8), small_b=(mono, 8, "bold"),
            clock=(mono, 17, "bold"), title=(display, 34, "bold"), button=(display, 12, "bold"))
 
    def _setup_window(self):
        r = self.root
        r.title("CAPRI // NEURAL LAUNCHER")
        r.configure(bg=BG)
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(px(1000), sw - 40), min(px(660), sh - 80)
        r.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        r.minsize(min(px(800), sw), min(px(540), sh))
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)
 
    def _setup_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        name = "Neon.Vertical.TScrollbar"
        st.layout(name, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        thumb = mix(BG, CYAN, .45)
        st.configure(name, background=thumb, troughcolor=PANEL, bordercolor=PANEL,
                     lightcolor=thumb, darkcolor=thumb, relief="flat", width=px(10), gripcount=0)
        st.map(name, background=[("pressed", YELLOW), ("active", CYAN)],
               lightcolor=[("pressed", YELLOW), ("active", CYAN)],
               darkcolor=[("pressed", YELLOW), ("active", CYAN)])
 
    def _build_ui(self):
        r, f = self.root, self.f
        self.backdrop = Backdrop(r)
        self.backdrop.place(x=0, y=0, relwidth=1, relheight=1)
 
        self.header = Header(r, f)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=px(14), pady=(px(14), 0))
 
        # — terminal —
        self.term_panel = NeonPanel(r, CYAN, "// TERMINAL", f.label, width=px(200), height=px(200))
        self.term_panel.grid(row=1, column=0, sticky="nsew", padx=(px(14), px(2)), pady=(px(4), 0))
        body = self.term_panel.body
        sb = ttk.Scrollbar(body, orient="vertical", style="Neon.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.term = tk.Text(body, bg=PANEL, fg=TEXT, font=f.mono, relief="flat", bd=0,
                            highlightthickness=0, wrap="word", padx=px(8), pady=px(4),
                            cursor="arrow", state="disabled", insertwidth=0, spacing1=px(2),
                            spacing3=px(2), selectbackground=mix(BG, CYAN, .35),
                            yscrollcommand=sb.set)
        sb.configure(command=self.term.yview)
        self.term.pack(side="left", fill="both", expand=True)
        self._config_tags()
 
        # — saisie de commande —
        row = tk.Frame(r, bg=BG)
        row.grid(row=2, column=0, sticky="ew", padx=(px(14), px(2)), pady=(px(2), 0))
        row.columnconfigure(0, weight=1)
        self.input_panel = NeonPanel(row, YELLOW, "// COMMANDE", f.label, width=px(200), height=px(78))
        self.input_panel.grid(row=0, column=0, sticky="nsew")
        self.btn = NeonButton(row, "EXÉCUTER", self._submit, YELLOW, f.button, px(150), px(78))
        self.btn.grid(row=0, column=1, sticky="ns", padx=(px(2), 0))
        ib = self.input_panel.body
        tk.Label(ib, text="›_", font=f.entry, bg=PANEL, fg=YELLOW).pack(side="left", padx=(px(4), px(8)))
        self.var = tk.StringVar()
        self.entry = tk.Entry(ib, textvariable=self.var, font=f.entry, bg=PANEL, fg=TEXT,
                              insertbackground=YELLOW, insertwidth=px(2), relief="flat", bd=0,
                              highlightthickness=0, selectbackground=PINK, selectforeground=BG)
        self.entry.pack(side="left", fill="x", expand=True)
        self.hint = tk.Label(ib, text="/open app <nom de l'application>", font=f.mono, bg=PANEL, fg=DIM)
        self.hint.place(in_=self.entry, x=px(2), rely=0.5, anchor="w")
        self.hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.var.trace_add("write", self._on_var_change)
 
        # — liste des applications —
        self.apps_panel = NeonPanel(r, PINK, "// APPLICATIONS", f.label, width=px(310), height=px(200))
        self.apps_panel.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(px(2), px(14)),
                             pady=(px(4), 0))
        ab = self.apps_panel.body
        tk.Label(ab, text="clic : remplir  ·  double-clic : lancer", font=f.small, bg=PANEL,
                 fg=DIM).pack(side="bottom", fill="x", pady=(px(4), 0))
        sb2 = ttk.Scrollbar(ab, orient="vertical", style="Neon.Vertical.TScrollbar")
        sb2.pack(side="right", fill="y")
        self.listbox = tk.Listbox(ab, bg=PANEL, fg=TEXT, font=f.items, relief="flat", bd=0,
                                  highlightthickness=0, activestyle="none", exportselection=False,
                                  selectbackground=PINK, selectforeground=BG, yscrollcommand=sb2.set)
        sb2.configure(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.listbox.bind("<Double-Button-1>", self._on_list_double)
        self.listbox.bind("<Return>", self._on_list_double)
 
        # — barre d'état —
        self.status = StatusBar(r, f)
        self.status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=px(14), pady=(px(2), px(12)))
        # Canvas.lower() agit sur les éléments dessinés, pas sur le widget : on passe par Misc
        tk.Misc.lower(self.backdrop)
 
    def _config_tags(self):
        t, f = self.term, self.f
        t.tag_configure("echo_p", foreground=YELLOW, font=f.mono_b)
        t.tag_configure("echo", foreground="#ffffff", font=f.mono_b)
        for kind, color, light in (("sys", PURPLE, "#aab0d6"), ("info", CYAN, TEXT),
                                   ("ok", MINT, "#b9ffe0"), ("err", PINK, "#ffb3c9"),
                                   ("warn", YELLOW, "#fff7a8")):
            t.tag_configure(kind + "_p", foreground=color, font=f.mono_b)
            t.tag_configure(kind, foreground=light)
        t.tag_configure("text", foreground=TEXT)
        t.tag_configure("plain", foreground=DIM)
        t.tag_configure("cmd", foreground=CYAN, font=f.mono_b)
 
    def _bind_keys(self):
        e, r = self.entry, self.root
        e.bind("<Return>", self._submit)
        e.bind("<KP_Enter>", self._submit)
        e.bind("<Up>", self._hist_up)
        e.bind("<Down>", self._hist_down)
        e.bind("<Tab>", self._on_tab)
        e.bind("<Escape>", lambda _e: self._set_entry(""))
        for seq in ("<Control-l>", "<Control-L>"):
            r.bind(seq, lambda _e: self.cmd_clear())
        r.bind("<F5>", lambda _e: self.cmd_refresh())
        # taper n'importe où renvoie la frappe vers la ligne de commande
        self.term.bind("<Key>", self._forward_key)
        self.listbox.bind("<Key>", self._forward_key, add="+")
 
    def _style_titlebar(self):
        """Barre de titre sombre aux couleurs néon (Windows 10 2004+ / Windows 11)."""
        if not IS_WIN:
            return
        try:
            import ctypes
            user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                  ctypes.c_void_p, ctypes.c_uint]
            self.root.update_idletasks()
            hwnd = user32.GetParent(self.root.winfo_id())
 
            def set_attr(attr, value):
                val = ctypes.c_int(value)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
 
            set_attr(20, 1)                    # mode sombre
            set_attr(19, 1)                    # idem, anciennes builds
            set_attr(35, colorref(BG))         # couleur de la barre de titre (Windows 11)
            set_attr(36, colorref(YELLOW))     # couleur du texte du titre
            set_attr(34, colorref(CYAN))       # couleur de la bordure
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)  # redessine le cadre
        except Exception:
            pass
 
    # ───────────────────────── affichage du terminal ─────────────────────────
    def say(self, text, kind="sys", typed=True):
        prefix = PREFIX.get(kind, "")
        segs = [(prefix + " ", kind + "_p")] if prefix else []
        segs.append((text, kind))
        self._enqueue(segs, typed)
 
    def say_rich(self, segments, typed=False):
        """segments : [(texte, tag), ...] affichés sur une même ligne."""
        self._enqueue(list(segments), typed)
 
    def _enqueue(self, segs, typed):
        self.out.append((segs, typed))
        if not self.printing:
            self.printing = True
            self.root.after(0, self._print_next, self.gen)
 
    def _print_next(self, gen):
        if gen != self.gen:
            return
        if not self.out:
            self.printing = False
            return
        segs, typed = self.out.popleft()
        for text, tag in segs[:-1]:
            self._write(text, tag)
        last_text, last_tag = segs[-1]
        if typed and last_text:
            self._type(last_text, last_tag, 0, gen)
        else:
            self._write(last_text + "\n", last_tag)
            self.root.after(4, self._print_next, gen)
 
    def _type(self, text, tag, i, gen):
        """Effet machine à écrire : 3 caractères toutes les 7 ms."""
        if gen != self.gen:
            return
        self._write(text[i:i + 3], tag)
        i += 3
        if i < len(text):
            self.root.after(7, self._type, text, tag, i, gen)
        else:
            self._write("\n", tag)
            self.root.after(40, self._print_next, gen)
 
    def _write(self, text, tag):
        t = self.term
        t.configure(state="normal")
        t.insert("end", text, tag)
        if int(t.index("end-1c").split(".")[0]) > 1500:
            t.delete("1.0", "300.0")
        t.see("end")
        t.configure(state="disabled")
 
    # ───────────────────────── saisie ─────────────────────────
    def _set_entry(self, text, refresh=True):
        self._no_filter = not refresh
        self.var.set(text)
        self._no_filter = False
        self.entry.icursor("end")
 
    def _on_var_change(self, *_):
        if self.var.get():
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, x=px(2), rely=0.5, anchor="w")
        if not self._no_filter:
            self._refresh_list()
 
    def _submit(self, _event=None):
        raw = self.var.get().strip()
        self._set_entry("")
        if raw:
            self.run_command(raw)
        return "break"
 
    def _hist_up(self, _event):
        if self.history and self.hist_pos > 0:
            self.hist_pos -= 1
            self._set_entry(self.history[self.hist_pos])
        return "break"
 
    def _hist_down(self, _event):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self._set_entry(self.history[self.hist_pos])
        else:
            self.hist_pos = len(self.history)
            self._set_entry("")
        return "break"
 
    def _on_tab(self, _event):
        """Complétion : commandes, puis noms d'applications (Tab répété = suggestion suivante)."""
        text = self.var.get()
        cyc = self._tab
        if cyc and text == cyc["last"]:
            cyc["i"] = (cyc["i"] + 1) % len(cyc["opts"])
            cyc["last"] = cyc["opts"][cyc["i"]]
            self._set_entry(cyc["last"])
            return "break"
        self._tab = None
        opts = []
        if re.fullmatch(r"(?i)\s*/open\s+app", text):
            opts = ["/open app "]
        else:
            m = re.match(r"(?is)^(/open\s+(?:app\s+)?)(.*)$", text)
            if m and m.group(2).strip():
                opts = [m.group(1) + e.name
                        for _, e in self.index.search(m.group(2), limit=8, min_score=40)]
            elif text.startswith("/") and not re.search(r"\s", text):
                opts = [c for c in self.COMMANDS if c.startswith(text.lower())]
        if opts:
            self._tab = {"opts": opts, "i": 0, "last": opts[0]}
            self._set_entry(opts[0])
        return "break"
 
    def _forward_key(self, event):
        if event.char and event.char.isprintable() and not (event.state & 0x4):
            self.entry.focus_set()
            self.entry.insert("end", event.char)
            return "break"
        return None
 
    # ───────────────────────── liste des applications ─────────────────────────
    def _refresh_list(self):
        query = ""
        m = re.match(r"(?is)^/(open|list)\s+(.*)$", self.var.get())
        if m:
            query = parse_open_arg(m.group(2)) if m.group(1).lower() == "open" else m.group(2).strip()
        if query:
            self.shown = [e for _, e in self.index.search(query, limit=300, min_score=45)]
        else:
            self.shown = list(self.index.entries)
        self._fill_list()
 
    def _fill_list(self):
        """Remplit la liste de droite avec `self.shown` et met à jour le compteur du panneau."""
        self.listbox.delete(0, "end")
        if self.shown:
            self.listbox.insert("end", *[" " + e.name for e in self.shown])
        self.apps_panel.set_title(f"// APPLICATIONS [{len(self.shown)}]")
 
    def _selected_entry(self):
        sel = self.listbox.curselection()
        return self.shown[sel[0]] if sel and sel[0] < len(self.shown) else None
 
    def _on_list_select(self, _event=None):
        """Clic : remplit la ligne de commande (sans refiltrer la liste)."""
        entry = self._selected_entry()
        if entry:
            self._set_entry("/open app " + entry.name, refresh=False)
 
    def _on_list_double(self, _event=None):
        """Double-clic ou Entrée : lance directement l'application sélectionnée."""
        entry = self._selected_entry()
        if entry:
            self.say(f"/open app {entry.name}", "echo", typed=False)
            self._launch(entry.target, entry.name)
        return "break"
 
    # ───────────────────────── démarrage et threads ─────────────────────────
    def _boot(self):
        system = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
        self.say(f"CAPRI NEURAL LAUNCHER v{VERSION} — noyau initialisé", "sys")
        self.say(f"Plateforme : {system} · Python {sys.version.split()[0]}", "info")
        if not IS_WIN:
            self.say("Capri est pensé pour Windows : outils système et recherche approfondie "
                     "désactivés.", "warn")
        self.say("Indexation des applications en arrière-plan…", "sys")
        self.say("Tape /help pour la liste des commandes · exemple : /open app discord", "plain")
        self._start_scan()
        self.entry.focus_set()
 
    def _start_scan(self):
        """Lance l'indexation ; le thread parle à l'interface uniquement via la file `events`."""
        started = self.index.rescan_async(
            lambda stage, count: self.events.put(("index", stage, count)))
        if started:
            self._announced = False
            self.status.set_text("INDEX: SCAN…")
        return started
 
    def _idle_status(self):
        """Texte de la barre d'état quand aucune action n'est en cours."""
        count = len(self.index.entries)
        if self.deep_running:
            self.status.set_text("SCAN DISQUE…")
        elif self.index.ready:
            self.status.set_text(f"INDEX: {count} APPS")
        else:
            self.status.set_text(f"INDEX: {count} APPS · SCAN…")
 
    def _pump_events(self):
        """Toutes les 100 ms : traite, côté interface, les messages des threads d'arrière-plan."""
        try:
            while True:
                kind, *args = self.events.get_nowait()
                try:
                    if kind == "index":
                        self._on_index(*args)
                    elif kind == "deep":
                        self._on_deep(*args)
                except Exception as exc:  # une erreur ne doit jamais arrêter la boucle
                    self.say(f"Erreur interne : {exc}", "err")
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._pump_events)
        except tk.TclError:  # fenêtre fermée
            pass
 
    def _on_index(self, stage, count):
        self._refresh_list()
        self._idle_status()
        if stage == "partial":
            # un /open en attente peut déjà être satisfait par les raccourcis indexés
            if self.pending_open and self.index.search(self.pending_open, limit=1, min_score=80):
                name, self.pending_open = self.pending_open, None
                self.open_app(name)
            return
        if not self._announced:
            self._announced = True
            if count:
                self.say(f"Index prêt : {count} applications détectées.", "ok")
            else:
                hint = " (PowerShell bloqué ?)" if IS_WIN else ""
                self.say(f"Aucune application détectée à l'indexation{hint}. Les chemins complets "
                         "et les outils système restent utilisables.", "warn")
        if self.pending_open:
            name, self.pending_open = self.pending_open, None
            self.open_app(name)
 
    def _on_deep(self, name, path):
        self.deep_running = False
        self._idle_status()
        if path:
            self.say(f"Trouvé : {path}", "info")
            self._launch(path, name)
        else:
            self._not_found(name)
 
    # ───────────────────────── commandes ─────────────────────────
    CMD_ALIASES = {"/quit": "/exit", "/cls": "/clear", "/ls": "/list", "/?": "/help",
                   "/reload": "/refresh"}
 
    def run_command(self, raw):
        if not self.history or self.history[-1] != raw:
            self.history.append(raw)
            del self.history[:-200]
        self.hist_pos = len(self.history)
        self.say(raw, "echo", typed=False)
        parts = raw.split(None, 1)
        word = parts[0].lower()
        word = self.CMD_ALIASES.get(word, word)
        rest = parts[1] if len(parts) > 1 else ""
        if word == "/open":
            self.cmd_open(rest)
        elif word == "/list":
            self.cmd_list(rest)
        elif word == "/refresh":
            self.cmd_refresh()
        elif word == "/clear":
            self.cmd_clear()
        elif word == "/help":
            self.cmd_help()
        elif word == "/exit":
            self.cmd_exit()
        else:
            self._unknown(raw, word)
 
    def _unknown(self, raw, word):
        self.term_panel.flash(PINK)
        if not word.startswith("/"):
            self.say(f"« {raw} » n'est pas une commande : elles commencent toutes par « / ».", "warn")
            self.say(f"Pour lancer une application : /open app {raw}", "plain")
            return
        names = [c.split()[0] for c in self.COMMANDS]
        close = get_close_matches(word, names, n=1, cutoff=0.5)
        self.say(f"Commande inconnue : {word}", "err")
        self.say(f"Tu voulais dire {close[0]} ?" if close
                 else "Tape /help pour la liste des commandes.", "info")
 
    def cmd_open(self, arg):
        name = parse_open_arg(arg)
        if not name:
            self.say("Usage : /open app <nom de l'application>", "warn")
            return
        self.open_app(name)
 
    def cmd_list(self, arg):
        query = arg.strip()
        entries = self.index.entries
        if not entries:
            self.say("L'index est encore vide : patiente un instant, ou lance /refresh.", "warn")
            return
        if query:
            entries = [e for _, e in self.index.search(query, limit=500, min_score=45)]
            if not entries:
                self.say(f"Aucune application ne correspond à « {query} ».", "warn")
                return
        self.shown = list(entries)   # la liste de droite montre la même sélection
        self._fill_list()
        suffix = f" pour « {query} »" if query else ""
        self.say(f"{len(entries)} application(s){suffix}", "info")
        limit = 40
        for i, e in enumerate(entries[:limit], 1):
            self.say_rich([(f"  {i:>3}  ", "plain"), (e.name, "text")])
        if len(entries) > limit:
            self.say(f"… et {len(entries) - limit} autres : affine avec /list <filtre> "
                     "ou parcours la liste de droite.", "plain")
 
    def cmd_refresh(self):
        if self._start_scan():
            self.say("Réindexation des applications en cours…", "sys")
        else:
            self.say("Une indexation est déjà en cours.", "warn")
 
    def cmd_clear(self):
        """Efface le terminal et annule l'affichage « machine à écrire » en cours."""
        self.gen += 1            # invalide les callbacks _print_next/_type déjà programmés
        self.out.clear()
        self.printing = False
        self.term.configure(state="normal")
        self.term.delete("1.0", "end")
        self.term.configure(state="disabled")
 
    def cmd_help(self):
        rows = (("/open app <nom>", "ouvre une application installée (nom, chemin, URL…)"),
                ("/list [filtre]", "liste les applications détectées"),
                ("/refresh", "relance l'indexation (F5)"),
                ("/clear", "efface le terminal (Ctrl+L)"),
                ("/help", "affiche cette aide"),
                ("/exit", "quitte Capri"))
        self.say("Commandes disponibles :", "info")
        for command, text in rows:
            self.say_rich([("   " + command.ljust(18), "cmd"), (text, "text")])
        self.say("Le nom tolère majuscules, accents, fautes de frappe et abréviations "
                 "(vsc, vscode…).", "plain")
        self.say("Exemples : /open app discord · /open app steam://rungameid/730 · "
                 "/open app ms-settings:", "plain")
 
    def cmd_exit(self):
        self.say("Liaison neuronale coupée. À bientôt, edgerunner.", "sys")
        self.root.after(900, self.root.destroy)
 
    # ───────────────────────── /open app ─────────────────────────
    def open_app(self, name):
        """Retrouve et lance une application, dans l'ordre : cible directe, index,
        outils système / PATH / « App Paths », puis recherche approfondie sur le disque."""
        # 1. chemin, URL ou protocole donné tel quel
        direct = resolve_direct_target(name)
        if direct:
            self._launch(direct, name)
            return
        if re.match(r"(?i)^(?:[a-z]:[\\/]|\\\\|[.~]?[\\/])", name):
            self.say(f"Chemin introuvable : {name}", "err")
            self.term_panel.flash(PINK)
            return
        if not normalize(name):
            self.say(f"Nom d'application invalide : « {name} »", "warn")
            return
        # 2. index (menu Démarrer, bureau, barre des tâches...)
        hits = self.index.search(name, limit=6)
        if hits and (self.index.ready or hits[0][0] >= 80):
            self._open_hit(hits)
            return
        if not self.index.ready:   # pas de bonne piste pour l'instant : on attend la fin du scan
            self.pending_open = name
            self.say(f"Indexation en cours : « {name} » sera lancé dès que l'index sera prêt.", "warn")
            return
        # 3. outils système, PATH, clés « App Paths » du registre
        target = self._find_system_target(name)
        if target:
            self._launch(target, name)
            return
        # 4. recherche approfondie des .exe (thread séparé : l'interface reste fluide)
        self._start_deep_search(name)
 
    def _open_hit(self, hits):
        score, entry = hits[0]
        self._launch(entry.target, entry.name)
        if score < 98:   # correspondance approximative : on montre les autres candidats proches
            others = [e.name for s, e in hits[1:4] if s >= score - 10]
            if others:
                self.say("Autres correspondances : " + " · ".join(others), "plain")
 
    def _find_system_target(self, name):
        if not IS_WIN:
            return None
        key = normalize(name)
        if key in SYSTEM_ALIASES:
            return SYSTEM_ALIASES[key]
        compact = key.replace(" ", "")
        for variant in dict.fromkeys((name, compact, key.replace(" ", "-"))):
            found = self.index.find_executable(variant) if variant else None
            if found:
                return found
        return None
 
    def _start_deep_search(self, name):
        if not IS_WIN:
            self._not_found(name)
            return
        if self.deep_running:
            self.say("Une recherche approfondie est déjà en cours, patiente quelques secondes.", "warn")
            return
        self.deep_running = True
        self.status.set_text("SCAN DISQUE…")
        self.say(f"« {name} » absent de l'index : recherche approfondie des .exe (20 s max)…", "info")
 
        def worker():
            path = None
            try:
                path = self.index.deep_search(name)
            except Exception:
                pass
            self.events.put(("deep", name, path))
 
        threading.Thread(target=worker, daemon=True).start()
 
    def _launch(self, target, label):
        """Lance `target` et l'annonce dans le terminal. Renvoie True si le lancement a réussi."""
        try:
            AppIndex.launch(target)
        except Exception as exc:
            self.say(f"Impossible de lancer « {label} » : {exc}", "err")
            self.term_panel.flash(PINK)
            return False
        self.say_rich([("[ OK ] ", "ok_p"), ("Lancement : ", "ok"), (label, "echo")], typed=True)
        self.term_panel.flash(MINT)
        return True
 
    def _not_found(self, name):
        self.say(f"Introuvable : « {name} »", "err")
        self.term_panel.flash(PINK)
        names = [e.name for _, e in self.index.search(name, limit=3, min_score=40)]
        if not names:
            by_key = {e.key: e.name for e in self.index.entries}
            names = [by_key[k] for k in get_close_matches(normalize(name), list(by_key),
                                                          n=3, cutoff=0.5)]
        if names:
            self.say("Tu voulais dire : " + " · ".join(names) + " ?", "info")
        self.say("Astuce : /list <filtre> pour parcourir l'index, ou donne le chemin complet du .exe.",
                 "plain")
 
 
# ═════════════════════════════ Point d'entrée ═════════════════════════════
def enable_dpi_awareness():
    """Écrans HiDPI : évite le rendu flou de Tk sous Windows."""
    if not IS_WIN:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 1 = DPI du système
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()        # Windows 7 / 8.0
    except Exception:
        pass
 
 
def main():
    global SCALE
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        SCALE = min(3.0, max(1.0, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        SCALE = 1.0
    CapriApp(root)
    root.mainloop()
 
 
if __name__ == "__main__":
    main()