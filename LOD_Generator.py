from __future__ import annotations
import concurrent.futures
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import traceback
import struct
import atexit
import platform
import webbrowser
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
    Image = None
    ImageTk = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES # pyright: ignore[reportMissingImports]
    DND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False

# =============================================================================
# OpenGL Constants - Unified for both Pyglet and PyOpenGL
# =============================================================================
OPENGL_AVAILABLE = False
GLUT_AVAILABLE = False
PYGLET_AVAILABLE = False
GLUT_INITIALIZED = False

if OPENGL_AVAILABLE:
    try:
        from OpenGL.GLUT import glutInit
        try:
            glutInit()
            GLUT_INITIALIZED = True
        except:
            pass
    except:
        pass

try:
    from OpenGL.GL import (
        glEnable, glDisable, glBegin, glEnd, glVertex3f, glVertex3fv, glNormal3fv,
        glTranslatef, glRotatef, glScalef, glClear, glMatrixMode, glLoadIdentity,
        glViewport, glLightfv, glColor3f, glNormal3f, glPushMatrix, glPopMatrix,
        glRasterPos2i, glGenLists, glNewList, glEndList, glCallList, glDeleteLists,
        GLfloat, GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, 
        GL_LIGHTING, GL_LIGHT0, GL_COLOR_MATERIAL, GL_POSITION, GL_DIFFUSE, 
        GL_AMBIENT, GL_PROJECTION, GL_MODELVIEW, GL_TRIANGLES, GL_QUADS, GL_COMPILE
    )
    OPENGL_AVAILABLE = True
except ImportError:
    pass

GLU_AVAILABLE = False
try:
    from OpenGL.GLU import gluPerspective, gluOrtho2D
    GLU_AVAILABLE = True
except ImportError:
    pass

try:
    import pyglet
    if not GLU_AVAILABLE:
        from pyglet.gl import gluPerspective, gluOrtho2D
    PYGLET_AVAILABLE = True
except ImportError:
    pass

try:
    if OPENGL_AVAILABLE:
        from OpenGL.GLUT import (
            glutInit, glutInitDisplayMode, glutInitWindowSize, glutCreateWindow,
            glutDisplayFunc, glutReshapeFunc, glutMouseFunc, glutMotionFunc,
            glutKeyboardFunc, glutSpecialFunc, glutMainLoop, glutMainLoopEvent, 
            glutPostRedisplay, glutSwapBuffers, glutDestroyWindow, glutCloseFunc, 
            glutSetWindowTitle, GLUT_RGBA, GLUT_DOUBLE, GLUT_DEPTH, GLUT_LEFT_BUTTON, 
            GLUT_DOWN, GLUT_KEY_LEFT, GLUT_KEY_RIGHT, GLUT_KEY_UP, GLUT_KEY_DOWN
        )
        try:
            from OpenGL.GLUT import glutIdleFunc
        except ImportError:
            glutIdleFunc = None
        try:
            from OpenGL.GLUT import glutBitmapString, GLUT_BITMAP_HELVETICA_12
        except ImportError:
            glutBitmapString = None
            GLUT_BITMAP_HELVETICA_12 = None
        GLUT_AVAILABLE = True
except ImportError:
    pass

APP_NAME = "Source (GMOD) props LOD Builder"
APP_VERSION = "1.13"

# =============================================================================
# INTERNATIONALISATION (EN / FR)
# =============================================================================
TRANSLATIONS: Dict[str, Dict[str, str]] = {}  # populated below after Dict import

def _T(key: str, lang: str = "en") -> str:
    """Return translated string for key in given lang, fallback to key itself."""
    return TRANSLATIONS.get(lang, {}).get(key, TRANSLATIONS.get("en", {}).get(key, key))

# Populated after class definitions to avoid forward-ref issues;
# defined here so _T() is available globally.

# =============================================================================
# TEMP DIRECTORIES
# =============================================================================
TEMP_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "LodTEMP"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

# Emplacement stable pour les données persistantes.
# Contrairement a TEMP_ROOT (fichiers de traitement jetables, ecrases en continu a chaque job),
# ce dossier n'est jamais vide automatiquement et survit aux nettoyages temporaires.
APPDATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "LodGenerator"
APPDATA_ROOT.mkdir(parents=True, exist_ok=True)

VPK_CACHE_DIR = TEMP_ROOT / "vpk_cache"
VPK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_EXTRACTED_MODELS: Dict[str, Path] = {}

KV_RE = re.compile(r'^"([^"]+)"\s+"([^"]*)"\s*$')


@dataclass
class PropEntry:
    original_model: str
    classname: str = ""
    usage_count: int = 1
    status: str = "pending"
    error: str = ""
    relative_subpath: str = ""
    resolved_source_path: str = ""
    output_path: str = ""
    workdir: str = ""
    qc_path: str = ""
    preview_dir: str = ""
    preview_frames: List[str] = field(default_factory=list)
    lod_count: int = 0
    original_qc_path: str = ""
    original_phy_path: str = ""
    metadata: Dict[str, List[str]] = field(default_factory=dict)
    lod_model_paths: List[str] = field(default_factory=list)
    file_size: int = 0  # Size in bytes of the .mdl file
    is_smd_only: bool = False  # v1.13 : prop = un .smd brut (mode SMD only, pas de decompilation/recompilation)


# =============================================================================
# VPK EXTRACTION SYSTEM
# =============================================================================

def find_steam_libraries() -> List[Path]:
    libraries = []
    # Try reading registry on Windows
    steam_path = None
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        except Exception:
            pass
            
    # Common default paths
    default_steam_paths = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"E:\Steam",
    ]
    if steam_path:
        default_steam_paths.insert(0, steam_path)
        
    for p_str in default_steam_paths:
        p = Path(p_str)
        if p.exists() and p.is_dir() and p not in libraries:
            libraries.append(p)
            
    # Parse libraryfolders.vdf
    for lib in list(libraries):
        vdf = lib / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                content = vdf.read_text(encoding="utf-8", errors="ignore")
                paths = re.findall(r'"path"\s*"([^"]+)"', content)
                for path in paths:
                    path_norm = Path(path.replace("\\\\", "\\"))
                    if path_norm.exists() and path_norm not in libraries:
                        libraries.append(path_norm)
            except Exception:
                pass
    return libraries

def normalize_slashes(path: str) -> str:
    return path.replace("\\", "/").strip()

def normalize_game_root(path: str) -> str:
    if not path:
        return ""
    p = Path(path.strip().strip('"'))
    if not p.exists():
        return str(p)
    if p.is_file():
        if p.name.lower() == "gameinfo.txt":
            return str(p.parent)
        p = p.parent
    
    # If the folder itself contains gameinfo.txt, it's already the correct game folder!
    if (p / "gameinfo.txt").exists():
        return str(p)
        
    # If a child subfolder named "garrysmod" contains gameinfo.txt, return that!
    if (p / "garrysmod" / "gameinfo.txt").exists():
        return str(p / "garrysmod")
        
    # Check other common subfolders containing gameinfo.txt
    for sub in ("hl2", "tf", "cstrike", "portal", "dod", "left4dead2", "left4dead", "portal2"):
        if (p / sub / "gameinfo.txt").exists():
            return str(p / sub)
            
    # Or scan any direct subdirectories for gameinfo.txt
    try:
        if p.is_dir():
            for child in p.iterdir():
                if child.is_dir() and (child / "gameinfo.txt").exists():
                    return str(child)
    except Exception:
        pass
        
    # Fallback to the original logic
    candidates = [p] + list(p.parents)
    for cand in candidates:
        if not cand.exists():
            continue
        if (cand / "gameinfo.txt").exists():
            return str(cand)
        if (cand / "garrysmod" / "gameinfo.txt").exists():
            return str(cand / "garrysmod")
        if (cand / "garrysmod").exists() or (cand / "sourceengine").exists() or (cand / "platform").exists():
            if (cand / "garrysmod").exists():
                return str(cand / "garrysmod")
            return str(cand)
    return str(p)

def discover_vpk_archives(game_root: str) -> List[Path]:
    root = Path(normalize_game_root(game_root))
    roots: List[Path] = []
    seen = set()
    for cand in (root, root / "garrysmod", root / "sourceengine", root / "platform"):
        if cand.exists() and cand not in seen:
            roots.append(cand)
            seen.add(cand)
    for cand in (root.parent, root.parent / "garrysmod", root.parent / "sourceengine", root.parent / "platform"):
        if cand.exists() and cand not in seen:
            roots.append(cand)
            seen.add(cand)
    archives: List[Path] = []
    seen_archives = set()
    for scan_root in roots:
        try:
            for archive in scan_root.rglob("*_dir.vpk"):
                key = str(archive).lower()
                if key not in seen_archives:
                    archives.append(archive)
                    seen_archives.add(key)
        except Exception:
            continue
    def priority(p: Path) -> tuple:
        low = str(p).lower().replace("\\", "/")
        if "/garrysmod/" in low: base = 0
        elif "/sourceengine/" in low: base = 1
        elif "/platform/" in low: base = 2
        else: base = 3
        return (base, low)
    archives.sort(key=priority)
    return archives

def find_all_vpk_files(gmod_root: str) -> List[Path]:
    """
    Découvre et retourne TOUS les fichiers VPK _dir présents dans le dossier du jeu.
    Cette fonction est SANS ÉTAT (pas de cache) : elle rescanne TOUJOURS, ce qui
    permet aux modèles VPK d'être trouvés à chaque appel, indépendamment des scans
    antérieurs. Le cache est SEULEMENT pour les modèles EXTRAITS (voir _EXTRACTED_MODELS),
    pas pour les archives VPK elles-mêmes.
    """
    return discover_vpk_archives(gmod_root)

def extract_model_from_vpk(vpk_path: Path, model_rel_path: str, output_dir: Path) -> Optional[Path]:
    model_rel_path = normalize_slashes(model_rel_path).strip('/')
    base_no_ext = model_rel_path.rsplit('.', 1)[0] if '.' in model_rel_path else model_rel_path
    extensions = ['.mdl', '.vvd', '.dx90.vtx', '.dx80.vtx', '.sw.vtx', '.vtx', '.phy', '.qc']
    try:
        with open(vpk_path, 'rb') as f:
            magic = struct.unpack('<I', f.read(4))[0]
            if magic != 0x55aa1234: return None
            version, dir_size = struct.unpack('<II', f.read(8))
            if version == 2: f.read(16)
            def read_vpk_str(file_obj):
                res = []
                while True:
                    b = file_obj.read(1)
                    if not b or b == b'\x00': break
                    res.append(b)
                return b"".join(res).decode('utf-8', errors='ignore')
            found_model = False
            extracted_path = None
            while True:
                ext = read_vpk_str(f)
                if not ext: break
                while True:
                    vpk_subpath = read_vpk_str(f)
                    if not vpk_subpath: break
                    while True:
                        fname = read_vpk_str(f)
                        if not fname: break
                        crc, preload, arch_idx, offset, length, term = struct.unpack('<IHHIIH', f.read(18))
                        preload_data = f.read(preload) if preload > 0 else b''
                        full_path_check = f"{vpk_subpath}/{fname}.{ext}".lower()
                        for target_ext in extensions:
                            target_rel = (base_no_ext + target_ext).lower()
                            target_rel_alt = f"models/{base_no_ext}{target_ext}".lower()
                            if full_path_check == target_rel or full_path_check == target_rel_alt:
                                out_file = output_dir / f"{fname}.{ext}"
                                out_file.parent.mkdir(parents=True, exist_ok=True)
                                data = preload_data
                                if length > 0:
                                    if arch_idx == 0x7fff:
                                        h_offset = 12 if version == 1 else 28
                                        with open(vpk_path, 'rb') as rf:
                                            rf.seek(h_offset + dir_size + offset)
                                            data += rf.read(length)
                                    else:
                                        prefix = vpk_path.name.replace('_dir.vpk', '')
                                        pack_name = f"{prefix}_{arch_idx:03d}.vpk"
                                        pack_path = vpk_path.parent / pack_name
                                        if pack_path.exists():
                                            with open(pack_path, 'rb') as pf:
                                                pf.seek(offset)
                                                data += pf.read(length)
                                out_file.write_bytes(data)
                                if target_ext == '.mdl':
                                    extracted_path = out_file
                                    found_model = True
            return extracted_path if found_model else None
    except Exception:
        return None

def extract_model_from_all_vpks(gmod_root: str, model_path: str) -> Optional[Path]:
    model_path = normalize_slashes(model_path).lstrip('/')
    if model_path in _EXTRACTED_MODELS and _EXTRACTED_MODELS[model_path].exists():
        return _EXTRACTED_MODELS[model_path]
    for vpk_path in find_all_vpk_files(gmod_root):
        result = extract_model_from_vpk(vpk_path, model_path, VPK_CACHE_DIR)
        if result:
            _EXTRACTED_MODELS[model_path] = result
            return result
    return None

def clear_vpk_cache():
    """
    Vide UNIQUEMENT le cache des MODÈLES EXTRAITS (cache disque + cache mémoire).
    N'affecte JAMAIS la découverte des VPKs eux-mêmes : find_all_vpk_files()
    continue toujours de retourner TOUS les VPKs disponibles.

    À NE PAS CONFONDRE avec un "scan forcé" : c'est juste du nettoyage des fichiers
    temporaires extraits. Les modèles seront re-extraits à la demande si nécessaire.
    """
    global _EXTRACTED_MODELS
    if VPK_CACHE_DIR.exists():
        for item in VPK_CACHE_DIR.rglob('*'):
            try:
                if item.is_file(): item.unlink()
            except: pass
    _EXTRACTED_MODELS.clear()

def cleanup_temp_on_exit():
    settings_file = TEMP_ROOT / "lod_builder_settings.json"
    for item in TEMP_ROOT.iterdir():
        if item == settings_file: continue
        try:
            if item.is_file(): item.unlink()
            elif item.is_dir(): shutil.rmtree(item, ignore_errors=True)
        except: pass

atexit.register(cleanup_temp_on_exit)


# =============================================================================
# SMD MODEL LOADER
# =============================================================================

class SMDModel:
    def __init__(self):
        self.vertices = []
        self.normals = []
        self.faces = []
        self.texture_coords = []
        self.bbox_min = [float('inf'), float('inf'), float('inf')]
        self.bbox_max = [float('-inf'), float('-inf'), float('-inf')]
        self.display_list_id = 0
        self.triangle_count = 0
    
    def _create_placeholder(self):
        size = 1.0
        self.vertices = [
            [-size, -size, -size], [size, -size, -size], [size, size, -size], [-size, size, -size],
            [-size, -size, size], [size, -size, size], [size, size, size], [-size, size, size]
        ]
        self.normals = [
            [0, 0, -1], [0, 0, -1], [0, 0, -1], [0, 0, -1],
            [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
        ]
        self.faces = [
            (0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6),
            (0, 3, 7), (0, 7, 4), (1, 2, 6), (1, 6, 5)
        ]
        self.bbox_min = [-size, -size, -size]
        self.bbox_max = [size, size, size]
        self.triangle_count = 12
    
    def build_display_list(self) -> bool:
        try:
            if self.display_list_id:
                try: glDeleteLists(self.display_list_id, 1)
                except Exception: pass
                self.display_list_id = 0
            if not self.vertices or not self.faces: return False
            list_id = glGenLists(1)
            if not list_id: return False
            glNewList(list_id, GL_COMPILE)
            glBegin(GL_TRIANGLES)
            for face in self.faces:
                for vi in face:
                    if vi < len(self.vertices):
                        v = self.vertices[vi]
                        n = self.normals[vi] if vi < len(self.normals) else [0, 0, 1]
                        try:
                            glNormal3fv((GLfloat * 3)(*n))
                            glVertex3fv((GLfloat * 3)(*v))
                        except Exception:
                            glNormal3fv(n)
                            glVertex3fv(v)
            glEnd()
            glEndList()
            self.display_list_id = list_id
            return True
        except Exception:
            self.display_list_id = 0
            return False

    def load_from_smd(self, smd_path: Path) -> bool:
        try:
            with open(smd_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            in_triangles = False
            for line in lines:
                line = line.strip()
                if line == 'triangles': in_triangles = True; continue
                elif line == 'end': in_triangles = False; continue
                if in_triangles:
                    parts = line.split()
                    if len(parts) >= 9:
                        try:
                            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                            nx, ny, nz = float(parts[4]), float(parts[5]), float(parts[6])
                            u, v = float(parts[7]), float(parts[8])
                            self.vertices.append([x, y, z])
                            self.normals.append([nx, ny, nz])
                            self.texture_coords.append([u, v])
                            self.bbox_min = [min(self.bbox_min[i], [x,y,z][i]) for i in range(3)]
                            self.bbox_max = [max(self.bbox_max[i], [x,y,z][i]) for i in range(3)]
                        except (ValueError, IndexError): pass
            if not self.vertices:
                self._create_placeholder()
                return False
            for i in range(0, len(self.vertices) - 2, 3):
                self.faces.append((i, i+1, i+2))
            self._center_model()
            return True
        except Exception:
            self._create_placeholder()
            return False
    
    def _center_model(self):
        if not self.vertices: return
        center = [
            (self.bbox_min[0] + self.bbox_max[0]) / 2,
            (self.bbox_min[1] + self.bbox_max[1]) / 2,
            (self.bbox_min[2] + self.bbox_max[2]) / 2
        ]
        for v in self.vertices:
            v[0] -= center[0]
            v[1] -= center[1]
            v[2] -= center[2]
        self.bbox_min = [v - center[i] for i, v in enumerate(self.bbox_min)]
        self.bbox_max = [v - center[i] for i, v in enumerate(self.bbox_max)]
    
    def get_scale(self):
        size = max(b - a for a, b in zip(self.bbox_min, self.bbox_max))
        return 1.0 / size if size > 0 else 1.0


# =============================================================================
# 3D PREVIEW WINDOW (OpenGL / GLUT / Pyglet fallback)
# =============================================================================

class ModelPreviewWindow:
    def __init__(self, root: tk.Tk, model_paths: List[str], current_lod: int = 0):
        self.root = root
        self.model_paths = model_paths
        self.current_lod = max(0, min(current_lod, len(model_paths) - 1))
        self.models = []
        self.rotation = [0, 0]
        self.zoom = -5.0
        self.is_dragging = False
        self.last_mouse = (0, 0)
        self.window = None
        self.window_id = None
        self.closed = False
        self.backend = ""
        self.render_lists: List[int] = []
        self.model_scales: List[float] = []
        self._load_models()
        self._try_open_backend()
    
    def _load_models(self):
        for path in self.model_paths:
            model = SMDModel()
            if Path(path).exists(): model.load_from_smd(Path(path))
            else: model._create_placeholder()
            self.models.append(model)

    def _build_render_caches(self):
        self.render_lists = []
        self.model_scales = []
        if not OPENGL_AVAILABLE: return
        for model in self.models:
            self.model_scales.append(model.get_scale())
            list_id = 0
            try: list_id = glGenLists(1)
            except Exception: list_id = 0
            if not list_id:
                self.render_lists.append(0)
                continue
            try:
                glNewList(list_id, GL_COMPILE)
                glBegin(GL_TRIANGLES)
                if model.vertices and model.faces:
                    for face in model.faces:
                        for vi in face:
                            if vi < len(model.vertices):
                                v = model.vertices[vi]
                                n = model.normals[vi] if vi < len(model.normals) else [0, 0, 1]
                                try:
                                    glNormal3fv((GLfloat * 3)(*n))
                                    glVertex3fv((GLfloat * 3)(*v))
                                except Exception:
                                    glNormal3fv(n)
                                    glVertex3fv(v)
                else:
                    self._emit_cube_geometry()
                glEnd()
                glEndList()
                self.render_lists.append(list_id)
            except Exception:
                try: glEnd()
                except Exception: pass
                self.render_lists.append(0)

    def _emit_cube_geometry(self):
        faces = [
            ((0.8, 0.2, 0.2), (0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
            ((0.2, 0.8, 0.2), (0, 0, -1), [(-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)]),
            ((0.2, 0.2, 0.8), (-1, 0, 0), [(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)]),
            ((0.8, 0.8, 0.2), (1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
            ((0.2, 0.8, 0.8), (0, 1, 0), [(-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)]),
            ((0.8, 0.2, 0.8), (0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
        ]
        for color, normal, verts in faces:
            glColor3f(*color)
            glNormal3f(*normal)
            v0, v1, v2, v3 = verts
            glVertex3f(*v0); glVertex3f(*v1); glVertex3f(*v2)
            glVertex3f(*v0); glVertex3f(*v2); glVertex3f(*v3)

    def _try_open_backend(self):
        if PYGLET_AVAILABLE and OPENGL_AVAILABLE:
            if self._try_pyglet(): return
        if GLUT_AVAILABLE and OPENGL_AVAILABLE:
            if self._try_glut(): return
        messagebox.showerror(
            "Erreur Aperçu 3D",
            "Aucun backend 3D compatible disponible !\n\n"
            "Veuillez installer :\n  pip install pyglet PyOpenGL\n\n"
            "Ou mettez à jour vos pilotes graphiques.\n"
            "L'aperçu dynamique de l'image fonctionnera toujours."
        )
    
    def _try_pyglet(self) -> bool:
        try:
            pyglet.options['shadow_window'] = False
            configs = [
                pyglet.gl.Config(double_buffer=True, depth_size=24, sample_buffers=0, samples=0),
                pyglet.gl.Config(double_buffer=True, depth_size=16, sample_buffers=0, samples=0),
                pyglet.gl.Config(double_buffer=True, depth_size=24),
                pyglet.gl.Config(double_buffer=True, depth_size=16),
                pyglet.gl.Config(double_buffer=True),
                None
            ]
            window = None
            for i, config in enumerate(configs):
                try:
                    window = pyglet.window.Window(
                        800, 600, f"3D Preview - LOD {self.current_lod}",
                        config=config, vsync=False, resizable=False
                    )
                    break
                except Exception:
                    continue
            if window is None: return False
            self.window = window
            self.backend = "pyglet"
            self._build_render_caches()
            self.help_label = None
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_COLOR_MATERIAL)
            light_pos = (GLfloat * 4)(1.0, 1.0, 1.0, 0.0)
            glLightfv(GL_LIGHT0, GL_POSITION, light_pos)
            glLightfv(GL_LIGHT0, GL_DIFFUSE, (GLfloat * 4)(1.0, 1.0, 1.0, 1.0))
            glLightfv(GL_LIGHT0, GL_AMBIENT, (GLfloat * 4)(0.2, 0.2, 0.2, 1.0))
            self._build_render_caches()
            window.on_draw = self._draw
            window.on_mouse_press = self._mouse_press
            window.on_mouse_drag = self._mouse_drag
            window.on_mouse_scroll = self._mouse_scroll
            window.on_close = self._on_close
            window.on_key_press = self._key_press
            return True
        except Exception:
            return False
    
    def _try_glut(self) -> bool:
        global GLUT_INITIALIZED
        try:
            if not GLUT_INITIALIZED:
                try:
                    glutInit()
                    GLUT_INITIALIZED = True
                except TypeError:
                    try:
                        glutInit([])
                        GLUT_INITIALIZED = True
                    except Exception:
                        GLUT_INITIALIZED = True
            try:
                mode = int(GLUT_RGBA) | int(GLUT_DOUBLE) | int(GLUT_DEPTH)
                glutInitDisplayMode(mode)
                glutInitWindowSize(int(800), int(600))
            except Exception: pass
            import random
            window_name = f"3D Preview - LOD {self.current_lod} - {random.randint(1000, 9999)}"
            try:
                self.window_id = glutCreateWindow(window_name)
            except Exception:
                self.window_id = glutCreateWindow(window_name.encode('utf-8'))
            try:
                glutDisplayFunc(self._glut_draw)
                glutReshapeFunc(self._glut_reshape)
                glutMouseFunc(self._glut_mouse)
                glutMotionFunc(self._glut_motion)
                glutKeyboardFunc(self._glut_keyboard)
                try: glutSpecialFunc(self._glut_special)
                except Exception: pass
                glutCloseFunc(self._glut_close)
            except Exception: pass
            if glutIdleFunc is not None:
                try: glutIdleFunc(self._glut_idle)
                except Exception: pass
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_COLOR_MATERIAL)
            glLightfv(GL_LIGHT0, GL_POSITION, [1.0, 1.0, 1.0, 0.0])
            self._build_render_caches()
            self.backend = "glut"
            return True
        except Exception:
            return False

    def _glut_idle(self):
        if self.window_id and not self.closed:
            glutPostRedisplay()
    
    def _glut_close(self):
        self.closed = True
    
    def _draw(self):
        if self.closed or not self.models or not self.window: return
        try:
            if hasattr(self.window, 'switch_to'): self.window.switch_to()
            self.window.clear()
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            gluPerspective(45, self.window.width / self.window.height, 0.1, 100.0)
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glTranslatef(0.0, 0.0, self.zoom)
            glRotatef(self.rotation[0], 1, 0, 0)
            glRotatef(self.rotation[1], 0, 1, 0)
            self._draw_model(self.models[self.current_lod])
        except Exception:
            pass
    
    def _glut_draw(self):
        if self.closed or not self.models: return
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, 800/600, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glTranslatef(0, 0, self.zoom)
        glRotatef(self.rotation[0], 1, 0, 0)
        glRotatef(self.rotation[1], 0, 1, 0)
        self._draw_model(self.models[self.current_lod])
        glutSwapBuffers()
    
    def _draw_model(self, model: SMDModel):
        if not model.vertices:
            self._draw_cube()
            return
        idx = self.models.index(model) if model in self.models else -1
        scale = self.model_scales[idx] if 0 <= idx < len(self.model_scales) else model.get_scale()
        glPushMatrix()
        glScalef(scale, scale, scale)
        glColor3f(0.8, 0.8, 0.8)
        list_id = self.render_lists[idx] if 0 <= idx < len(self.render_lists) else 0
        if list_id:
            try:
                glCallList(list_id)
                glPopMatrix()
                return
            except Exception: pass
        glBegin(GL_TRIANGLES)
        if model.faces:
            for face in model.faces:
                for vi in face:
                    if vi < len(model.vertices):
                        v = model.vertices[vi]
                        n = model.normals[vi] if vi < len(model.normals) else [0, 0, 1]
                        try:
                            glNormal3fv((GLfloat * 3)(*n))
                            glVertex3fv((GLfloat * 3)(*v))
                        except Exception:
                            glNormal3fv(n)
                            glVertex3fv(v)
        glEnd()
        glPopMatrix()

    def _draw_cube(self):
        pass

    def _glut_reshape(self, w, h):
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, w/h, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
    
    def _mouse_press(self, x, y, button, modifiers):
        if button == pyglet.window.mouse.LEFT:
            self.is_dragging = True
            self.last_mouse = (x, y)
        return True
    
    def _glut_mouse(self, button, state, x, y):
        if button == GLUT_LEFT_BUTTON:
            self.is_dragging = (state == GLUT_DOWN)
            self.last_mouse = (x, y)
            if self.window_id: glutPostRedisplay()
        elif button in (3, 4):
            if state == GLUT_DOWN or state == 0:
                self.zoom += 0.45 if button == 3 else -0.45
                self.zoom = max(-20, min(-1, self.zoom))
                if self.window_id: glutPostRedisplay()
    
    def _mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.is_dragging and buttons & pyglet.window.mouse.LEFT:
            self.rotation[0] -= dy * 0.5
            self.rotation[1] += dx * 0.5
            self.last_mouse = (x, y)
            self.render_frame()
        return True
    
    def _glut_motion(self, x, y):
        if self.is_dragging:
            dx = x - self.last_mouse[0]
            dy = y - self.last_mouse[1]
            self.rotation[0] += dy * 0.5
            self.rotation[1] += dx * 0.5
            self.last_mouse = (x, y)
            if self.window_id: glutPostRedisplay()
    
    def _mouse_scroll(self, x, y, scroll_x, scroll_y):
        delta = scroll_y if scroll_y else scroll_x
        self.zoom += delta * 0.45
        self.zoom = max(-20, min(-1, self.zoom))
        self.render_frame()
        return True
    
    def _key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.Q or symbol == pyglet.window.key.ESCAPE:
            self.close()
        elif symbol in (pyglet.window.key.LEFT, pyglet.window.key.DOWN, ord('[')):
            self.prev_lod()
            self.render_frame()
        elif symbol in (pyglet.window.key.RIGHT, pyglet.window.key.UP, ord(']')):
            self.next_lod()
            self.render_frame()
        return True
    
    def _glut_keyboard(self, key, x, y):
        if key in (b'q', b'Q', 27):
            self.close()
        elif key == b'[':
            self.prev_lod()
            if self.window_id: glutPostRedisplay()
        elif key == b']':
            self.next_lod()
            if self.window_id: glutPostRedisplay()

    def _glut_special(self, key, x, y):
        if key in (GLUT_KEY_LEFT, GLUT_KEY_DOWN):
            self.prev_lod()
            if self.window_id: glutPostRedisplay()
        elif key in (GLUT_KEY_RIGHT, GLUT_KEY_UP):
            self.next_lod()
            if self.window_id: glutPostRedisplay()
    
    def _on_close(self):
        self.closed = True
        return True

    def render_frame(self):
        if self.closed: return
        try:
            if self.backend == "pyglet" and self.window:
                try: self.window.switch_to()
                except Exception: pass
                try: self.window.dispatch_events()
                except Exception: pass
                try:
                    self._draw()
                    self.window.flip()
                except Exception: pass
            elif self.backend == "glut" and self.window_id:
                try:
                    import OpenGL.GLUT as glut_mod
                    if hasattr(glut_mod, "glutMainLoopEvent"): glut_mod.glutMainLoopEvent()
                    glutPostRedisplay()
                except Exception: pass
        except Exception: pass
    
    def prev_lod(self):
        if self.current_lod > 0:
            self.current_lod -= 1
            if self.window: self.window.set_caption(f"3D Preview - LOD {self.current_lod}")
            elif self.window_id:
                try: glutSetWindowTitle(f"3D Preview - LOD {self.current_lod}")
                except: pass
            self.render_frame()
    
    def next_lod(self):
        if self.current_lod < len(self.models) - 1:
            self.current_lod += 1
            if self.window: self.window.set_caption(f"3D Preview - LOD {self.current_lod}")
            elif self.window_id:
                try: glutSetWindowTitle(f"3D Preview - LOD {self.current_lod}")
                except: pass
            self.render_frame()
    
    def close(self):
        self.closed = True
        try:
            for list_id in self.render_lists:
                if list_id:
                    try: glDeleteLists(list_id, 1)
                    except Exception: pass
        except Exception: pass
        self.render_lists = []
        self.model_scales = []
        if self.window:
            try: self.window.close()
            except: pass
            self.window = None
        if self.window_id:
            try: glutDestroyWindow(self.window_id)
            except: pass
            self.window_id = None


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def strip_models_prefix(model_path: str) -> str:
    s = normalize_slashes(model_path).lstrip("/")
    if s.lower().startswith("models/"): return s[7:]
    return s

def source_relative_subpath(model_path: str) -> str:
    return strip_models_prefix(model_path)

def resolve_source_model_path(game_root: str, model_path: str) -> str:
    root = Path(normalize_game_root(game_root))
    sub = source_relative_subpath(model_path)
    return str(root / "models" / Path(sub))


def resolve_source_model_path_multi(game_root: str, extra_dirs: List[str], model_path: str) -> str:
    """
    Comme resolve_source_model_path, mais recherche aussi dans une liste de dossiers modeles
    additionnels (par defaut, un seul dossier de recherche : le jeu Source/GMod choisi ci-dessus).
    Chaque dossier supplementaire peut etre soit un dossier "jeu" (contenant lui-meme "models/..."),
    soit directement un dossier "models" -> on teste les deux interpretations.
    Le premier chemin qui existe reellement sur le disque est retourne ; sinon, on retombe sur le
    chemin par defaut (meme si non existant, pour une extraction VPK ulterieure).
    """
    sub = Path(source_relative_subpath(model_path))
    default_path = resolve_source_model_path(game_root, model_path)
    if Path(default_path).exists():
        return default_path
    for extra in extra_dirs:
        extra = extra.strip()
        if not extra:
            continue
        extra_root = Path(extra)
        candidate_a = extra_root / "models" / sub   # dossier "jeu" fourni
        if candidate_a.exists():
            return str(candidate_a)
        candidate_b = extra_root / sub               # dossier "models" fourni directement
        if candidate_b.exists():
            return str(candidate_b)
    return default_path

def resolve_output_model_path(output_root: str, model_path: str) -> str:
    root = Path(output_root)
    sub = source_relative_subpath(model_path)
    return str(root / Path(sub))


def get_model_display_size(model_path: str) -> int:
    """
    Retourne la taille a AFFICHER pour un modele : celle du fichier .vvd (donnees de vertex)
    associe, plutot que celle du .mdl lui-meme (le .mdl seul ne reflete pas le poids reel
    du modele). Si le .vvd n'existe pas (rare),
    on retombe sur la taille du .mdl.
    """
    try:
        p = Path(model_path)
        vvd = p.with_suffix(".vvd")
        if vvd.exists():
            return vvd.stat().st_size
        return p.stat().st_size
    except Exception:
        return 0


def format_file_size(size_bytes: int) -> str:
    """Format file size in bytes to human-readable string (Ko/Mo)."""
    if size_bytes == 0:
        return "0 Ko"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f} Ko"
    mb = kb / 1024
    return f"{mb:.1f} Mo"


def extract_entity_blocks(vmf_text: str) -> List[str]:
    pattern = re.compile(r'\bentity\b\s*\{', re.IGNORECASE)
    blocks = []
    for match in pattern.finditer(vmf_text):
        start = vmf_text.find("{", match.start())
        if start == -1: continue
        depth = 1
        for i in range(start + 1, len(vmf_text)):
            if vmf_text[i] == "{": depth += 1
            elif vmf_text[i] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(vmf_text[start + 1:i])
                    break
    return blocks

def parse_keyvalues_from_block(block_text: str) -> Dict[str, str]:
    out = {}
    for line in block_text.splitlines():
        s = line.strip()
        m = KV_RE.match(s)
        if m: out[m.group(1)] = m.group(2)
    return out

def extract_models_from_folder(folder_path: str, max_workers: int = 4) -> List[PropEntry]:
    """
    Scans one or several directories (";"-separated in folder_path, to support multiple
    configured model source folders) recursively for .mdl files and returns one PropEntry
    per file. The model path is stored relative to each folder's root (models/<subpath>/<name>.mdl)
    using the same convention as Source Engine (forward slashes, lower-case).
    usage_count is set to 1 (no map reference to count placements).

    The disk stat() calls (file size) are parallelized across threads (multithread scan),
    since this is the part that scales with folder size / model count and can be slow on
    network drives or very large addon collections.
    """
    roots = [Path(p.strip()) for p in folder_path.split(";") if p.strip()]
    if not roots:
        return []

    all_mdls: List[Tuple[Path, Path]] = []  # (mdl_path, root)
    for root in roots:
        if not root.is_dir():
            continue
        for mdl in root.rglob("*.mdl"):
            all_mdls.append((mdl, root))
    all_mdls.sort(key=lambda pair: str(pair[0]).lower())

    def _stat_one(pair: Tuple[Path, Path]) -> PropEntry:
        mdl, root = pair
        try:
            rel = mdl.relative_to(root)
        except ValueError:
            rel = Path(mdl.name)
        rel_str = normalize_slashes(str(rel))
        # FIX "models/models/xxx.mdl" (v1.13) : si le dossier scanne CONTIENT deja un
        # segment "models" dans son arborescence (tres courant : sortie de compilation,
        # addon complet, ou simplement un sous-dossier nomme "models"), on ancrait
        # naivement "models/" + chemin-relatif PAR DESSUS -> chemin double qui ne
        # correspond a aucun fichier reel et fait echouer toute resolution ulterieure.
        # On prend maintenant le sous-chemin a PARTIR du segment "models" existant s'il y
        # en a un, et on ne prefixe "models/" que s'il n'y en a vraiment aucun.
        parts = rel_str.split("/")
        lower_parts = [p.lower() for p in parts]
        if "models" in lower_parts:
            idx = lower_parts.index("models")
            model_path = "/".join(parts[idx:])
        else:
            model_path = "models/" + rel_str
        try:
            size = get_model_display_size(str(mdl))
        except Exception:
            size = 0
        entry = PropEntry(original_model=model_path, classname="", usage_count=1, file_size=size)
        # FIX "il faut ajouter un chemin custom pour que ca marche" (v1.13) : le scan de
        # dossier connait DEJA l'emplacement EXACT du fichier sur le disque. On le stocke
        # directement plutot que de forcer le traitement a le re-deriver plus tard via le
        # Game Root / Source Game Folder, qui n'a aucune raison de contenir ce dossier
        # scanne (voir _process_single_job_attempt, qui privilegie desormais ce chemin).
        entry.resolved_source_path = str(mdl)
        return entry

    entries: List[PropEntry] = []
    if not all_mdls:
        return entries
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        entries = list(executor.map(_stat_one, all_mdls))
    return entries


def extract_models_from_vmf(vmf_path: str) -> List[PropEntry]:
    text = Path(vmf_path).read_text(encoding="utf-8", errors="ignore")
    blocks = extract_entity_blocks(text)
    counts = Counter()
    classnames = {}
    metadata = defaultdict(lambda: defaultdict(list))
    for block in blocks:
        kv = parse_keyvalues_from_block(block)
        model = kv.get("model", "").strip()
        classname = kv.get("classname", "").strip()
        if not model: continue
        model_norm = normalize_slashes(model)
        if not model_norm.lower().endswith(".mdl"): continue
        counts[model_norm] += 1
        if classname and model_norm not in classnames:
            classnames[model_norm] = classname
        if classname:
            metadata[model_norm]["classnames"].append(classname)
    entries = []
    for model_path, count in sorted(counts.items(), key=lambda x: x[0].lower()):
        entries.append(PropEntry(
            original_model=model_path,
            classname=classnames.get(model_path, ""),
            usage_count=count,
            metadata={k: list(v) for k, v in metadata[model_path].items()},
            file_size=0,
        ))
    return entries


def _skip_qc_blocks(qc_text: str, keywords: tuple) -> str:
    """
    Retire du texte QC tous les blocs (avec leurs accolades) qui commencent par
    l'un des mots-clés donnés. Utilisé pour exclure les sections qui ne sont pas
    des maillages de corps (séquences, animations, collisions, $lod déjà présents...)
    avant de chercher les vrais maillages de corps.
    """
    lower_kws = tuple(k.lower() for k in keywords)
    lines = qc_text.splitlines()
    result = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        low = raw.strip().lower()
        if any(_qc_keyword_matches(low, kw) for kw in lower_kws):
            depth = raw.count('{') - raw.count('}')
            i += 1
            if depth == 0:
                peek = i
                while peek < n and not lines[peek].strip():
                    peek += 1
                if peek < n and lines[peek].strip().startswith('{'):
                    depth += 1
                    i = peek + 1
            while i < n and depth > 0:
                depth += lines[i].count('{') - lines[i].count('}')
                i += 1
            continue
        result.append(raw)
        i += 1
    return "\n".join(result)


def _qc_keyword_matches(line: str, keyword: str) -> bool:
    if not line.startswith(keyword): return False
    rest = line[len(keyword):]
    return not rest or rest[0] in (' ', '\t', '"', '{', '\r', '\n')


def extract_body_mesh_names(qc_text: str) -> List[str]:
    """
    Ne retourne QUE les vrais maillages de corps, référencés via '$body "..." "x.smd"'
    ou via 'studio "x.smd"' dans un bloc $bodygroup. Exclut explicitement tout ce qui
    n'est PAS un maillage visuel remplaçable par un $lod : séquences d'animation,
    modèles de collision physique, blocs $lod déjà présents, attachements, etc.

    C'est le correctif du bug "Unknown replace model 'idle'" : l'ancienne extraction
    (extract_referenced_models) attrapait n'importe quel ".smd"/".dmx" dans tout le QC,
    y compris les fichiers d'animation référencés par $sequence (ex: "idle.smd"), et
    tentait de les remplacer via $lod alors que ce ne sont pas des maillages de corps.
    """
    body_only = _skip_qc_blocks(qc_text, (
        '$sequence', '$animation', '$collisionmodel', '$collisionjoints',
        '$lod', '$include', '$attachment', '$poseparameter', '$ikchain', '$weightlist',
    ))
    names: List[str] = []
    seen = set()
    smd_pattern = re.compile(r'"([^"]+\.(?:smd|dmx))"', re.IGNORECASE)
    for line in body_only.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith('$body') or low.startswith('studio'):
            m = smd_pattern.search(stripped)
            if m:
                name = m.group(1).replace("\\", "/").split("/")[-1]
                if name.lower() not in seen:
                    seen.add(name.lower())
                    names.append(name)
    return names


def _parse_smd_skeleton(smd_path: Path):
    """
    Parse un .smd et retourne (positions, root_name) :
      - positions : dict {nom_os: (x, y, z)} pour la frame "time 0" du bloc
        'skeleton'.
      - root_name : nom de l'os racine (celui dont le parent déclaré dans le
        bloc 'nodes' est -1), ou le nom de l'os d'index 0 en repli si aucun
        parent -1 explicite n'est trouvé.
    Retourne ({}, None) si le fichier est illisible/mal formé.
    """
    try:
        text = smd_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}, None
    lines = text.splitlines()
    nodes = {}
    parents = {}
    positions = {}
    root_name = None
    n = len(lines)
    i = 0
    while i < n and lines[i].strip().lower() != "nodes":
        i += 1
    if i < n:
        i += 1
        while i < n and lines[i].strip().lower() != "end":
            parts = lines[i].strip().split(None, 2)
            if len(parts) >= 2:
                try:
                    idx = int(parts[0])
                    nodes[idx] = parts[1].strip('"')
                    if len(parts) >= 3:
                        try:
                            parents[idx] = int(parts[2].split()[0])
                        except (ValueError, IndexError):
                            pass
                except ValueError:
                    pass
            i += 1
    for _idx, _name in nodes.items():
        if parents.get(_idx, -1) == -1:
            root_name = _name
            break
    if root_name is None and 0 in nodes:
        root_name = nodes[0]
    while i < n and lines[i].strip().lower() != "skeleton":
        i += 1
    if i < n:
        i += 1
        while i < n and not lines[i].strip().lower().startswith("time"):
            i += 1
        if i < n:
            i += 1
            while i < n:
                stripped = lines[i].strip()
                low = stripped.lower()
                if low == "end" or low.startswith("time"):
                    break
                parts = stripped.split()
                if len(parts) >= 4:
                    try:
                        idx = int(parts[0])
                        name = nodes.get(idx)
                        if name:
                            positions[name] = (float(parts[1]), float(parts[2]), float(parts[3]))
                    except ValueError:
                        pass
                i += 1
    return positions, root_name


def _shift_smd_positions(smd_path: Path, delta: Tuple[float, float, float]) -> bool:
    """
    Réécrit un .smd EN PLACE en décalant toutes les positions (bloc
    'skeleton', toutes les frames, et bloc 'triangles', tous les sommets)
    d'un vecteur constant `delta`. Ne touche ni aux rotations, ni aux
    normales, ni aux UV, ni aux poids de skinning : sert uniquement à
    recentrer l'ORIGINE d'un maillage (typiquement la collision physique)
    pour qu'elle coïncide avec celle d'un autre (le maillage visuel).
    """
    try:
        text = smd_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    lines = text.splitlines()
    dx, dy, dz = delta
    out = []
    n = len(lines)
    i = 0
    section = None
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        low = stripped.lower()
        if low == "skeleton":
            section = "skeleton"; out.append(raw); i += 1; continue
        if low == "triangles":
            section = "triangles"; out.append(raw); i += 1; continue
        if low == "end":
            section = None; out.append(raw); i += 1; continue
        if section == "skeleton" and not low.startswith("time") and low:
            parts = stripped.split()
            if len(parts) >= 7:
                try:
                    bone_id = int(parts[0])
                    x = float(parts[1]) + dx
                    y = float(parts[2]) + dy
                    z = float(parts[3]) + dz
                    rest = " ".join(parts[4:])
                    out.append(f"{bone_id}  {x:.6f} {y:.6f} {z:.6f}  {rest}")
                    i += 1; continue
                except ValueError:
                    pass
        elif section == "triangles" and low:
            parts = stripped.split()
            if len(parts) >= 9:
                try:
                    bone_id = int(parts[0])
                    x = float(parts[1]) + dx
                    y = float(parts[2]) + dy
                    z = float(parts[3]) + dz
                    rest = " ".join(parts[4:])
                    out.append(f"{bone_id} {x:.6f} {y:.6f} {z:.6f} {rest}")
                    i += 1; continue
                except ValueError:
                    pass
        out.append(raw)
        i += 1
    try:
        smd_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def smd_triangles_stats(smd_path: Path) -> Optional[dict]:
    """
    Parcourt le bloc 'triangles' d'un .smd et calcule des statistiques
    géométriques BRUTES (aucune rotation, aucune transformation : exactement
    ce que Crowbar/studiomdl considèrent comme les unités/axes natifs du
    modèle) :
      - 'diag'     : diagonale de la bbox (invariante par rotation -> détecte
                     un écart d'ÉCHELLE, cf. World scale de SourceIO)
      - 'extents'  : (dx, dy, dz) étendues par axe -> détecte une PERMUTATION
                     d'axes (ex: Y/Z inversés selon les modèles)
      - 'centroid' : (mx, my, mz) barycentre des sommets par rapport à
                     l'origine du modèle -> lève l'ambiguïté de signe entre
                     deux orientations qui donneraient les mêmes étendues
                     (ex: 180° autour d'un axe), la plupart des props Source
                     n'étant pas centrés sur leur origine.
    Sert de référence "vérité terrain" pour corriger, côté worker Blender,
    à la fois l'échelle ET l'orientation introduites par l'aller-retour
    import SourceIO / export SMD maison.
    """
    try:
        lines = smd_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    n = len(lines)
    i = 0
    while i < n and lines[i].strip().lower() != "triangles":
        i += 1
    if i >= n:
        return None
    i += 1
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    while i < n:
        stripped = lines[i].strip()
        if stripped.lower() == "end":
            break
        parts = stripped.split()
        # Ligne de sommet : <bone> <x> <y> <z> <nx> <ny> <nz> <u> <v> [nlinks ...]
        # (une ligne de nom de matériau n'a qu'un seul token et n'est pas parsable en floats)
        if len(parts) >= 9:
            try:
                xs.append(float(parts[1]))
                ys.append(float(parts[2]))
                zs.append(float(parts[3]))
            except ValueError:
                pass
        i += 1
    if not xs:
        return None
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    mz = sum(zs) / len(zs)
    return {
        "diag": math.sqrt(dx * dx + dy * dy + dz * dz),
        "extents": (dx, dy, dz),
        "centroid": (mx, my, mz),
    }


def diagnose_and_fix_physics_alignment(qc_text: str, decomp_root: Path, target_dir: Path, log_fn) -> None:
    """
    Compare la pose de référence de l'os racine du maillage de corps principal
    et du maillage de collision physique ($collisionmodel), TOUS DEUX tels que
    décompilés par Crowbar (jamais retouchés à ce stade). Si un décalage
    d'ORIGINE (translation pure) est détecté, il est corrigé AUTOMATIQUEMENT
    dans la copie du fichier physique présente dans le dossier de compilation
    (`target_dir`) -- jamais dans `decomp_root`/OG_QC, qui restent la
    référence d'origine intacte.
    """
    try:
        body_names = extract_body_mesh_names(qc_text)
        coll_match = re.search(r'\$collisionmodel\s+"([^"]+)"', qc_text, re.IGNORECASE)
        if not body_names or not coll_match:
            return
        body_smd = decomp_root / Path(body_names[0]).name
        coll_smd_name = Path(coll_match.group(1)).name
        coll_smd = decomp_root / coll_smd_name
        if not body_smd.exists() or not coll_smd.exists():
            return

        body_pos, _body_root = _parse_smd_skeleton(body_smd)
        coll_pos, coll_root = _parse_smd_skeleton(coll_smd)
        if not body_pos or not coll_pos:
            return

        common = set(body_pos) & set(coll_pos)
        if not common:
            log_fn(f"[PHYSICS DIAGNOSTIC] No common bone between {body_smd.name} and {coll_smd.name}, "
                   f"comparison impossible.")
            return

        mismatches = []
        for name in sorted(common):
            bx, by, bz = body_pos[name]
            cx, cy, cz = coll_pos[name]
            delta = ((bx - cx) ** 2 + (by - cy) ** 2 + (bz - cz) ** 2) ** 0.5
            if delta > 0.05:
                mismatches.append((name, delta))

        if not mismatches:
            log_fn(f"[PHYSICS DIAGNOSTIC] {body_smd.name} and {coll_smd.name} share the same reference "
                   f"pose: no offset detected at this level.")
            return

        # Os de référence pour le vecteur de correction : la racine si elle est
        # commune aux deux squelettes, sinon le premier os en commun (ordre stable).
        ref_bone = coll_root if (coll_root and coll_root in common) else sorted(common)[0]
        bx, by, bz = body_pos[ref_bone]
        cx, cy, cz = coll_pos[ref_bone]
        delta_vec = (bx - cx, by - cy, bz - cz)
        delta_mag = (delta_vec[0] ** 2 + delta_vec[1] ** 2 + delta_vec[2] ** 2) ** 0.5

        staged_coll = target_dir / coll_smd_name
        if delta_mag > 0.05 and staged_coll.exists():
            ok = _shift_smd_positions(staged_coll, delta_vec)
            if ok:
                log_fn(f"[PHYSICS FIX] Origin offset detected between {body_smd.name} and {coll_smd.name} "
                       f"(bone '{ref_bone}', delta={delta_mag:.3f}) -> automatic correction applied to "
                       f"the compile copy ({staged_coll.name}): translation = "
                       f"({delta_vec[0]:.3f}, {delta_vec[1]:.3f}, {delta_vec[2]:.3f}).")
            else:
                log_fn(f"[PHYSICS FIX] Offset detected (delta={delta_mag:.3f}) but failed to "
                       f"rewrite {staged_coll} -- correction not applied.")
        else:
            log_fn(f"[PHYSICS DIAGNOSTIC] Offset below the correction threshold (delta={delta_mag:.3f} "
                   f"on '{ref_bone}').")

        # Vérifie s'il reste un écart significatif sur d'AUTRES os une fois le
        # décalage de `ref_bone` neutralisé (indice d'un problème qui n'est pas
        # une simple translation globale).
        residual = []
        for name, _d in mismatches:
            if name == ref_bone:
                continue
            bx2, by2, bz2 = body_pos[name]
            cx2, cy2, cz2 = coll_pos[name]
            cx2 += delta_vec[0]; cy2 += delta_vec[1]; cz2 += delta_vec[2]
            d2 = ((bx2 - cx2) ** 2 + (by2 - cy2) ** 2 + (bz2 - cz2) ** 2) ** 0.5
            if d2 > 0.05:
                residual.append((name, d2))
        if residual:
            residual.sort(key=lambda x: -x[1])
            top = ", ".join(f"{n} (delta={d:.3f})" for n, d in residual[:5])
            log_fn(f"[PHYSICS DIAGNOSTIC] Residual offset after translation correction on "
                   f"{len(residual)} bone(s) (e.g. {top}): looks like a hierarchy/orientation difference, "
                   f"not just an origin offset -- check manually in Crowbar/HLMV for this specific model.")
    except Exception as e:
        log_fn(f"[PHYSICS DIAGNOSTIC] Error during diagnostic/correction: {e}")


def preserve_original_phy(source_model_path: str, model_stem: str, log_fn) -> str:
    """
    Sauvegarde de côté (dans TEMP_ROOT/OG_PHY), TEL QUEL et sans jamais le
    modifier, le fichier de collision physique .phy compilé par Valve/l'auteur
    d'origine à côté du .mdl source (source_model_path).

    Contrairement au maillage visuel, le .phy ne peut pas être "corrigé" après
    coup (le problème vient de la façon dont Source/Crowbar/studiomdl gèrent
    la recompilation de la collision, pas de ce script) : la seule solution
    fiable est de ne JAMAIS le faire passer par Crowbar -> Blender -> studiomdl,
    et de réinjecter la version d'origine à la toute fin du traitement (voir
    restore_original_phy). Retourne le chemin de la copie de sauvegarde, ou ""
    si le modèle n'a pas de .phy d'origine (prop sans collision physique).
    """
    try:
        original_phy = Path(source_model_path).with_suffix(".phy")
        if not original_phy.exists():
            return ""
        og_phy_dir = TEMP_ROOT / "OG_PHY"
        og_phy_dir.mkdir(parents=True, exist_ok=True)
        saved_path = og_phy_dir / f"{model_stem}_original.phy"
        shutil.copy2(original_phy, saved_path)
        log_fn(f"[OG_PHY] Original physics saved as-is: {saved_path}")
        return str(saved_path)
    except Exception as e:
        log_fn(f"[OG_PHY] Original .phy save error: {e}")
        return ""


def restore_original_phy(original_phy_path: str, final_dest: Path, model_stem: str, log_fn) -> None:
    """
    Réinjecte, à la toute fin du traitement d'un modèle, la copie du .phy
    d'origine (sauvegardée par preserve_original_phy, jamais modifiée) à la
    place de celui fraîchement recompilé par studiomdl dans le dossier de
    sortie `final_dest`. Le studiomdl-recompilé sert seulement à produire un
    .mdl/.vvd/.vtx valides avec les LODs ; sa collision recompilée, elle, est
    jetée -- on garde la physique EXACTEMENT identique à l'originale.

    IMPORTANT : on cible UNIQUEMENT le fichier `{model_stem}.phy`, jamais un
    glob("*.phy") sur tout `final_dest`. Plusieurs props partagent souvent le
    même dossier de sortie (ex: keithy/props/ballon.mdl ET
    keithy/props/carrousel.mdl) : un glob écraserait aussi les .phy des AUTRES
    props déjà traités et présents dans ce même dossier.
    """
    if not original_phy_path or not Path(original_phy_path).exists():
        log_fn("[PHY] No original physics saved for this model (no source .phy); "
               "the recompiled .phy (if any) is kept as-is.")
        return
    target = final_dest / f"{model_stem}.phy"
    try:
        shutil.copy2(original_phy_path, target)
        if target.exists():
            log_fn(f"[PHY] Original physics restored as-is: {target.name} "
                   f"(studiomdl-recompiled collision discarded).")
    except Exception as e:
        log_fn(f"[PHY] Error while restoring {target.name}: {e}")


def sanitize_body_name(name: str) -> str:
    """
    DOIT rester rigoureusement identique a sanitize_body_name() dans le script Blender
    embarque (_get_blender_worker_text) : les deux cotes doivent produire le meme
    identifiant pour le meme nom, sans quoi la correspondance mesh-original <-> fichier
    LOD par bodygroup echoue silencieusement.

    FIX (v1.16) : confirme par logs utilisateur -- SourceIO nomme les objets Blender
    importes avec le nom de fichier SMD D'ORIGINE COMPLET, EXTENSION INCLUSE
    (ex: object='Christmas_Part.smd', pas juste 'Christmas_Part'). Le script principal,
    lui, recevait le nom du maillage depuis le QC et retirait D'ABORD l'extension via
    Path(...).stem avant de sanitizer -- donc "christmas_part" (extension deja retiree
    en amont) ne correspondait JAMAIS a "christmas_part_smd" (nom d'objet Blender brut,
    ".smd" sanitize en "_smd"). Resultat : la correspondance echouait a 100% du temps,
    et TOUT retombait silencieusement sur le fichier fusionne -- exactement le
    comportement observe (aucun fichier lod{idx}_xxx.smd jamais charge par studiomdl).
    Cette fonction retire maintenant elle-meme une extension .smd/.dmx finale (le cas
    echeant) AVANT de normaliser, pour que le meme nom logique produise TOUJOURS le
    meme identifiant, qu'on lui passe un nom de fichier QC ("Christmas_Part.smd") ou un
    nom d'objet Blender brut ("Christmas_Part.smd" aussi, dans ce cas precis).
    """
    n = name or ""
    low = n.lower()
    if low.endswith(".smd") or low.endswith(".dmx"):
        n = n[:-4]
    out = []
    for ch in n.lower():
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out).strip("_")
    return s or "mesh"


def patch_original_qc(original: str, entry: PropEntry, rel_model: str, mat_dir: str,
                     lod_levels: List[Tuple[int, float]], mat_dirs: Optional[List[str]] = None,
                     object_manifest: Optional[List[str]] = None, log_fn: Optional[Callable[[str], None]] = None) -> str:
    """
    Patch original QC file to add LOD support WITHOUT removing existing properties.
    Only removes existing $lod sections and adds new ones. Preserves ALL other properties.
    """
    lines = original.splitlines()

    # 1. Update or add $modelname (keep all other properties)
    modelname_line = f'$modelname "{rel_model}"'
    has_modelname = False
    for i, line in enumerate(lines):
        if line.strip().lower().startswith('$modelname'):
            lines[i] = modelname_line
            has_modelname = True
            break
    if not has_modelname:
        lines.insert(0, modelname_line)

    # 2. Handle $cdmaterials - only add if not present and needed
    if mat_dirs:
        # Remove existing $cdmaterials
        lines = [line for line in lines if not line.strip().lower().startswith('$cdmaterials')]
        # Add new ones after $modelname
        insert_pos = 1
        for d in mat_dirs:
            lines.insert(insert_pos, f'$cdmaterials "{d.rstrip("/")}/"')
            insert_pos += 1
    elif mat_dir and mat_dir not in (".", "./", ""):
        if not any(line.strip().lower().startswith('$cdmaterials') for line in lines):
            lines.insert(1, f'$cdmaterials \"{mat_dir}/\"')

    # 3. Find the main mesh name from $bodygroup
    main_mesh = None
    in_bodygroup = False
    bodygroup_depth = 0
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith('$bodygroup'):
            in_bodygroup = True
            bodygroup_depth = stripped.count('{') - stripped.count('}')
            continue
        if in_bodygroup:
            bodygroup_depth += stripped.count('{') - stripped.count('}')
            if bodygroup_depth <= 0:
                in_bodygroup = False
                continue
            if stripped.lower().startswith('studio'):
                parts = stripped.split()
                if len(parts) >= 2:
                    mesh_name = parts[1].strip('"')
                    main_mesh = mesh_name
                    break

    # Fallback: extract from actual body mesh declarations (jamais depuis les
    # séquences/animations/collisions)
    if not main_mesh:
        mesh_names = extract_body_mesh_names(original)
        if mesh_names:
            # Prefer non-lod named meshes
            for name in mesh_names:
                lower_name = name.lower()
                if 'lod' not in lower_name and '_lod' not in lower_name:
                    main_mesh = name
                    break
            if not main_mesh:
                main_mesh = mesh_names[0]

    # Final fallback - use model name
    if not main_mesh:
        main_mesh = Path(rel_model).stem + ".smd"

    # 4. Remove ONLY existing $lod sections (keep everything else!)
    new_lines = []
    in_lod = False
    lod_depth = 0
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith('$lod') and not in_lod:
            in_lod = True
            lod_depth = stripped.count('{') - stripped.count('}')
            continue
        if in_lod:
            lod_depth += stripped.count('{') - stripped.count('}')
            if lod_depth <= 0:
                in_lod = False
                lod_depth = 0
            continue
        new_lines.append(line)
    lines = new_lines

    # 5. Add new LOD sections
    lod_sections = []
    mesh_names = extract_body_mesh_names(original)
    if not mesh_names:
        mesh_names = [main_mesh]

    # FIX bodygroups (v1.14) : si le manifeste des objets Blender est disponible, on associe
    # CHAQUE maillage d'origine du QC a SON PROPRE fichier LOD decime individuellement
    # (lod{idx}_{nom}.smd), au lieu de tout renvoyer vers un unique fichier fusionne partage
    # par tous les bodygroups (ancien comportement, qui rendait le changement de bodygroup
    # sans effet visuel a partir du LOD1). Correspondance par nom normalise (insensible a la
    # casse/ponctuation) entre le stem du maillage QC et le nom d'objet Blender ; si aucune
    # correspondance fiable n'est trouvee pour un maillage donne, on retombe sur le fichier
    # fusionne "lod{idx}.smd" pour CE maillage precis uniquement (comportement precedent,
    # jamais pire qu'avant).
    manifest_lookup: Dict[str, str] = {}
    if object_manifest:
        for obj_name in object_manifest:
            manifest_lookup[sanitize_body_name(obj_name)] = sanitize_body_name(obj_name)

    def _lod_file_for_mesh(mesh_filename: str, idx: int) -> str:
        stem_sanitized = sanitize_body_name(mesh_filename)
        if stem_sanitized in manifest_lookup:
            return f"lod{idx}_{manifest_lookup[stem_sanitized]}.smd"
        if log_fn:
            log_fn(f"[BODYGROUP MATCH] No Blender object match for QC mesh '{mesh_filename}' "
                   f"(looked for sanitized id '{stem_sanitized}' among: {sorted(manifest_lookup.keys())}) "
                   f"-> falling back to the merged lod{idx}.smd for this mesh.")
        return f"lod{idx}.smd"  # repli : fichier fusionne (comportement precedent)

    # Generate LOD replacements - chaque maillage pointe vers SON PROPRE fichier decime
    # quand la correspondance est possible, sinon vers le fichier fusionne en repli.
    matched_count = 0
    total_count = 0
    for idx, (distance, _) in enumerate(lod_levels[1:], start=1):
        lod_sections.append(f'$lod {distance}')
        lod_sections.append('{')
        for mesh in mesh_names:
            if 'lod' not in mesh.lower() and '_lod' not in mesh.lower():
                target_file = _lod_file_for_mesh(mesh, idx)
                total_count += 1
                if not target_file.startswith(f"lod{idx}.smd"):
                    matched_count += 1
                lod_sections.append(f'    replacemodel \"{mesh}\" \"{target_file}\"')
        lod_sections.append('}')
        lod_sections.append('')

    if log_fn and object_manifest:
        log_fn(f"[BODYGROUP MATCH] {matched_count}/{total_count} mesh-to-LOD-file matches resolved "
               f"individually (per bodygroup); the rest fall back to the shared merged file.")

    if lod_sections:
        lines.extend([''] + lod_sections)

    return '\n'.join(lines) + '\n'

def discover_existing_lod_smds(entry: PropEntry, game_root: str = "", output_root: str = "") -> List[str]:
    search_dirs: List[Path] = []
    seen_dirs: Set[str] = set()

    def add_dir(path: Optional[Path]):
        if not path: return
        try: rp = path.resolve()
        except Exception: rp = path
        key = str(rp).lower()
        if key not in seen_dirs and rp.exists() and rp.is_dir():
            seen_dirs.add(key)
            search_dirs.append(rp)

    rel = Path(source_relative_subpath(entry.original_model))
    stem = rel.stem

    if entry.resolved_source_path:
        src = Path(entry.resolved_source_path)
        add_dir(src.parent)
        add_dir(src.parent / "smd")
        add_dir(src.parent.parent / "smd")
        add_dir(src.parent / "lod")

    if entry.original_qc_path:
        qc_dir = Path(entry.original_qc_path).parent
        add_dir(qc_dir)
        add_dir(qc_dir / "smd")

    if game_root:
        gr = Path(normalize_game_root(game_root))
        add_dir(gr / "models" / rel.parent)
        add_dir(gr / "models" / rel.parent / "smd")
        add_dir(gr / "models")

    if output_root:
        oroot = Path(output_root)
        add_dir(oroot / rel.parent)
        add_dir(oroot / rel.parent / "smd")
        persistent_cache = oroot / ".lod_preview_cache" / re.sub(r'[^A-Za-z0-9._-]+', '_', source_relative_subpath(entry.original_model))
        add_dir(persistent_cache / "smd")

    candidate_names = [f"{stem}.smd"] + [f"{stem}_lod{i}.smd" for i in range(8)] + [f"lod{i}.smd" for i in range(8)]
    found: List[str] = []
    seen_files: Set[str] = set()

    for d in search_dirs:
        for name in candidate_names:
            for cand in (d / name, d / Path(name).name):
                try:
                    if cand.exists() and cand.is_file():
                        key = str(cand.resolve()).lower()
                        if key not in seen_files:
                            seen_files.add(key)
                            found.append(str(cand))
                except Exception:
                    continue
        for patt in (f"{stem}*.smd", "lod*.smd"):
            try:
                for cand in d.glob(patt):
                    if cand.is_file():
                        key = str(cand.resolve()).lower()
                        if key not in seen_files:
                            seen_files.add(key)
                            found.append(str(cand))
            except Exception: pass

    def lod_sort_key(path_str: str):
        name = Path(path_str).name.lower()
        m = re.search(r"lod(\d+)", name)
        if m: return (0, int(m.group(1)), name)
        if name == f"{stem}.smd": return (-1, 0, name)
        return (1, 9999, name)

    found.sort(key=lod_sort_key)
    return found


def discover_existing_previews(entry: PropEntry, output_root: str = "") -> List[str]:
    if not output_root:
        return []
    rel = source_relative_subpath(entry.original_model)
    persistent_img_cache = Path(output_root) / ".lod_preview_cache" / re.sub(r'[^A-Za-z0-9._-]+', '_', rel) / "previews"
    if not persistent_img_cache.exists():
        return []
    def _preview_sort_key(p: Path):
        m = re.search(r"preview_lod(\d+)\.png$", p.name, flags=re.I)
        return int(m.group(1)) if m else 10_000
    paths = sorted(persistent_img_cache.glob("preview_lod*.png"), key=_preview_sort_key)
    return [str(p) for p in paths]


# =============================================================================
# MAIN APPLICATION
# =============================================================================

# --- Translation tables (populated here, before SourceLODApp) ---
TRANSLATIONS["en"] = {
    # Header / status
    "ready": "Ready", "processing": "Processing", "done": "Done | LOD included",
    "error": "Error", "pending": "Pending",
    # Section labels
    "lbl_params": "Parameters", "lbl_tools": "Tools (Blender/SDK/Crowbar)",
    "lbl_lod": "LOD (Dist/Ratio)", "lbl_physics": "Physics",
    "lbl_props": "Props", "lbl_preview": "Preview", "lbl_info": "Info",
    "chk_generate_preview": "Generate preview during processing",
    "chk_show_preview": "Show previews of selected models",
    "lbl_log": "Log", "btn_export_log": "Export log", "msg_no_log_yet": "No log file yet.",
    # Path labels
    "lbl_vmf": "VMF File", "lbl_models_dir": "Models Folder (OPTIONAL - Loose .mdl scan)",
    "lbl_vmf_extra_paths": "Custom VMF models Paths (additional folders searched when resolving a VMF's props)",
    "btn_add_path": "+ Add", "btn_remove_path": "- Remove",
    "lbl_game_root": "Source/GMod Game Folder (contains gameinfo.txt)", "lbl_output": "Output Folder",
    "lbl_studiomdl": "studiomdl executable", "lbl_blender": "blender executable", "lbl_crowbar": "Crowbar CLI path", "lbl_crowbar_cli": "Crowbar CLI",
    # Buttons
    "btn_browse": "...", "btn_parse_vmf": "Analyse VMF", "btn_parse_folder": "Analyse Folder",
    "btn_scan_vpk": "Scan VPK", "btn_3d": "3D Preview", "btn_folder": "Open output folder",
    "btn_save": "Save Param", "btn_load": "Load Param", "btn_cache": "Clear VPK",
    "btn_smd_only": "Process SMD only...",
    "chk_include_smd": "Also scan for .smd files (SMD only mode)",
    "msg_smd_only_need_blender": "Set the blender.exe path in Tools first.",
    "msg_smd_only_need_lods": "At least 2 LOD levels are required (LOD0 + one decimated).",
    "msg_vpk_cleared": "VPK cache cleared.",
    "btn_all": "ALL PROPS", "btn_selected": "SELECTED", "btn_clear": "Clear",
    "btn_stop": "Stop",
    # Filters
    "lbl_search": "Search", "btn_clear_search": "X",
    "lbl_filter_status": "Status:", "lbl_filter_type": "Type:", "lbl_filter_count": "Count:",
    "lbl_count_min": "min", "lbl_count_max": "max", "lbl_sort": "Sort:",
    "lbl_filter_size": "Size (Ko):", "lbl_size_min": "min", "lbl_size_max": "max",
    "filter_all": "All", "filter_ready": "Ready", "filter_processing": "Processing",
    "filter_ok": "Done", "filter_error": "Error",
    "sort_none": "None", "sort_asc": "Asc", "sort_desc": "Desc",
    # Tree columns
    "col_model": "Model", "col_type": "Type", "col_qty": "Qty", "col_size": "Size", "col_status": "Status",
    # Physics
    "phys_rebuild": "Rebuild", "phys_keep": "Keep (recommended)",
    # Threads
    "lbl_threads": "Threads:",
    # Misc
    "no_selection": "No selection", "select_prop": "Select a prop",
    "lbl_lod_slider": "LOD:",
    # Messages
    "msg_analysis_running": "Analysing...", "msg_analysis_folder": "Scanning folder...",
    "msg_vmf_invalid": "Please select a valid .vmf file.",
    "msg_folder_invalid": "Please select a valid models folder.",
    "msg_paths_invalid": "Please check that all paths are valid.",
    "msg_queued": "{n} prop(s) added to the processing queue.",
    "msg_stop_requested": "Stop requested - active jobs will finish, pending jobs will be cancelled.",
    "msg_settings_saved": "Settings saved.", "msg_settings_loaded": "Settings loaded.",
    "msg_vpk_scanned": "{n} VPK files scanned!\nModels will be extracted on demand.",
    "msg_no_3d": "Select a model first.", "msg_no_lod_3d": "No LOD SMD found for this model. Compile it first.",
    "msg_no_backend": "No 3D backend available!\nInstall: pip install pyglet PyOpenGL",
    "msg_clear_vpk": "Clear VPK cache?", "msg_thread_max": "Max threads on your CPU: {max}\n({total} logical thread(s) detected, 1 reserved for system)\nValue corrected automatically.",
    "msg_detected": "{n} props detected", "msg_extracted": "Extraction complete: {n} unique Source models to optimise.",
    "msg_folder_done": "Folder scan complete: {n} .mdl models found.",
    "lang_label": "Language:",
    "menu_file": "File", "menu_open_vmf": "Open VMF...", "menu_open_folder": "Open Folder...",
    "menu_open_smd": "Open SMD (SMD only mode)...", "menu_quit": "Quit",
    "menu_view": "View", "menu_see_log": "See Log",
    "menu_help": "Help", "menu_instructions": "Instructions", "menu_github": "Open GitHub",
    "msg_instructions": "1. Set your paths (VMF or Models Folder, Game Folder, Output Folder).\n"
                        "2. Set your tools (studiomdl, blender, CrowbarCLI).\n"
                        "3. Configure the LOD grid (distance / decimation ratio).\n"
                        "4. Analyse VMF/Folder, or use File > Open SMD for SMD only mode.\n"
                        "5. Select props and process them (All Props / Selected).",
    "lbl_theme": "Theme:", "btn_apply_theme": "Apply",
    "help_3d_title": "3D Preview Controls",
    "help_3d_text": "Left-click + drag: Rotate\nLeft/Right arrows: LOD +1 / LOD -1\nScroll wheel: Scale\nESC: Quit (the preview window's X button cannot close it)",
    "msg_no_selection_generic": "Select a prop first.",
    "msg_no_lod_generic": "No LOD SMD found for this model. Process it first.",
    "msg_no_backend_generic": "No 3D backend available!\nInstall: pip install pyglet PyOpenGL",
    "no_selection_short": "No selection", "no_prop_selected": "No prop selected",
    "preview_unavailable": "Preview Unavailable\n(PIL module not installed or image not found)",
    "preview_loading": "Loading preview...",
    "preview_render_error": "Preview render error:\n{e}",
    "preview_launch_hint": "Run LOD generation to see\nthe interactive 3D preview of:\n{name}",
    "preview_no_lod": "Preview unavailable or LOD not included\n{name}",
}
TRANSLATIONS["fr"] = {
    "ready": "Pret", "processing": "Traitement", "done": "Termine | LOD Inclu",
    "error": "Erreur", "pending": "En attente",
    "lbl_params": "Parametres", "lbl_tools": "Outils (Blender/SDK/Crowbar)",
    "lbl_lod": "LOD (Dist/Ratio)", "lbl_physics": "Physique",
    "lbl_props": "Props", "lbl_preview": "Apercu", "lbl_info": "Info",
    "chk_generate_preview": "Générer la preview dans le traitement",
    "chk_show_preview": "Afficher les previews des models sélectionnés",
    "lbl_log": "Log", "btn_export_log": "Exporter le log", "msg_no_log_yet": "Aucun fichier de log pour le moment.",
    "lbl_vmf": "Fichier VMF", "lbl_models_dir": "Dossier Modèles (FACULTATIF - Scan de .mdl locaux)",
    "lbl_vmf_extra_paths": "Dossiers de modèles VMF personnalisés (dossiers additionnels recherchés pour résoudre les props d'un VMF)",
    "btn_add_path": "+ Ajouter", "btn_remove_path": "- Retirer",
    "lbl_game_root": "Dossier Jeu Source/GMod (contient gameinfo.txt)", "lbl_output": "Dossier de Sortie",
    "lbl_studiomdl": "Exécutable studiomdl", "lbl_blender": "Exécutable blender", "lbl_crowbar": "Chemin de Crowbar CLI", "lbl_crowbar_cli": "Crowbar CLI",
    "btn_browse": "...", "btn_parse_vmf": "Analyser VMF", "btn_parse_folder": "Analyser Dossier",
    "btn_scan_vpk": "Scanner VPK", "btn_3d": "Apercu 3D", "btn_folder": "Dossier de sortie",
    "btn_save": "Sauver Param", "btn_load": "Charger Param", "btn_cache": "Vider VPK",
    "btn_smd_only": "Traiter SMD only...",
    "chk_include_smd": "Scanner aussi les .smd (mode SMD only)",
    "msg_smd_only_need_blender": "Renseigne d'abord le chemin de blender.exe dans Outils.",
    "msg_smd_only_need_lods": "Il faut au moins 2 niveaux de LOD (LOD0 + un décimé).",
    "msg_vpk_cleared": "Cache VPK vidé.",
    "btn_all": "TOUS LES PROPS", "btn_selected": "SELECTIONNES", "btn_clear": "Vider",
    "btn_stop": "Arreter",
    "lbl_search": "Recherche", "btn_clear_search": "X",
    "lbl_filter_status": "Statut:", "lbl_filter_type": "Type:", "lbl_filter_count": "Qte:",
    "lbl_count_min": "min", "lbl_count_max": "max", "lbl_sort": "Tri:",
    "lbl_filter_size": "Taille (Ko):", "lbl_size_min": "min", "lbl_size_max": "max",
    "filter_all": "Tous", "filter_ready": "Pret", "filter_processing": "En cours",
    "filter_ok": "OK", "filter_error": "Erreur",
    "sort_none": "Aucun", "sort_asc": "Croissant", "sort_desc": "Decroissant",
    "col_model": "Modele", "col_type": "Type", "col_qty": "Qte", "col_size": "Taille", "col_status": "Statut",
    "phys_rebuild": "Recompiler", "phys_keep": "Conserver (recommande)",
    "lbl_threads": "Threads:",
    "no_selection": "Aucune selection", "select_prop": "Selectionnez un prop",
    "lbl_lod_slider": "LOD:",
    "msg_analysis_running": "Analyse en cours...", "msg_analysis_folder": "Scan du dossier...",
    "msg_vmf_invalid": "Selectionnez une carte .vmf valide.",
    "msg_folder_invalid": "Selectionnez un dossier de modeles valide.",
    "msg_paths_invalid": "Verifiez que tous les chemins sont valides.",
    "msg_queued": "{n} prop(s) ajoute(s) a la file de traitement.",
    "msg_stop_requested": "Arret demande - les jobs en cours se termineront, les suivants seront annules.",
    "msg_settings_saved": "Parametres sauvegardes.", "msg_settings_loaded": "Parametres charges.",
    "msg_vpk_scanned": "{n} fichiers VPK scannes!\nLes modeles seront extraits a la demande.",
    "msg_no_3d": "Selectionnez un modele d'abord.", "msg_no_lod_3d": "Aucun LOD SMD detecte. Compilez d'abord.",
    "msg_no_backend": "Backend 3D non disponible!\nInstallez: pip install pyglet PyOpenGL",
    "msg_clear_vpk": "Vider le cache VPK?", "msg_thread_max": "Nombre max de threads de votre CPU: {max}\n({total} thread(s) logique(s) detecte(s), 1 reserve au systeme)\nValeur corrigee automatiquement.",
    "msg_detected": "{n} props detectes", "msg_extracted": "Extraction terminee: {n} modeles Source uniques a optimiser.",
    "msg_folder_done": "Scan termine: {n} modeles .mdl trouves.",
    "lang_label": "Langue:",
    "menu_file": "Fichier", "menu_open_vmf": "Ouvrir VMF...", "menu_open_folder": "Ouvrir Dossier...",
    "menu_open_smd": "Ouvrir SMD (mode SMD only)...", "menu_quit": "Quitter",
    "menu_view": "Affichage", "menu_see_log": "Voir le Log",
    "menu_help": "Help", "menu_instructions": "Instructions", "menu_github": "Ouvrir le GitHub",
    "msg_instructions": "1. Renseigne les chemins (VMF ou Dossier Modèles, Dossier Jeu, Dossier de Sortie).\n"
                        "2. Renseigne les outils (studiomdl, blender, CrowbarCLI).\n"
                        "3. Configure la grille de LOD (distance / ratio de décimation).\n"
                        "4. Analyse VMF/Dossier, ou utilise Fichier > Ouvrir SMD pour le mode SMD only.\n"
                        "5. Sélectionne des props et lance le traitement (Tous / Sélectionnés).",
    "lbl_theme": "Thème :", "btn_apply_theme": "Appliquer",
    "help_3d_title": "Contrôles de l'aperçu 3D",
    "help_3d_text": "Clique gauche + glisser : Rotation\nFlèches droite/gauche : LOD +1 / LOD -1\nMolette : Scale\nECHAP : Quitter (le bouton X de la fenêtre de preview ne permet pas de quitter)",
    "msg_no_selection_generic": "Sélectionnez un prop d'abord.",
    "msg_no_lod_generic": "Aucun LOD SMD détecté pour ce modèle. Traitez-le d'abord.",
    "msg_no_backend_generic": "Aucun backend 3D disponible !\nInstallez : pip install pyglet PyOpenGL",
    "no_selection_short": "Aucune sélection", "no_prop_selected": "Aucun prop sélectionné",
    "preview_unavailable": "Aperçu Indisponible\n(Module PIL non installé ou image introuvable)",
    "preview_loading": "Chargement de l'aperçu...",
    "preview_render_error": "Erreur rendu aperçu :\n{e}",
    "preview_launch_hint": "Lancez la génération des LODs pour voir\nl'aperçu 3D interactif de :\n{name}",
    "preview_no_lod": "Preview indisponible ou LOD non inclus\n{name}",
}


def process_smd_only(smd_path: str, output_dir: str, lod_levels: List[Tuple[int, float]],
                     blender_path: str, log_fn: Optional[Callable[[str], None]] = None) -> List[str]:
    """
    Mode "SMD only" (v1.13) : decime un fichier .smd DIRECTEMENT (import Blender ->
    decimation -> export SMD), SANS decompilation (Crowbar) ni recompilation
    (studiomdl). Utile pour traiter un SMD de reference deja extrait/exporte par un
    autre outil, sans repasser par tout le pipeline .mdl complet.

    Fonction independante de toute UI (Tkinter ou autre) : peut etre appelee depuis
    n'importe quelle interface. blender_worker.py doit deja exister (genere par un
    traitement .mdl normal au moins une fois) puisqu'il contient les fonctions
    partagees (apply_decimate, bake_object_transforms, etc.) reutilisees ici.

    NOTE IMPORTANTE : cote Blender, l'import passe par l'addon "Blender Source
    Tools" (io_scene_valvesource), PAS SourceIO (qui ne lit que des .mdl compiles).
    Cet addon doit deja etre installe/active dans le Blender utilise -- contrairement
    a SourceIO, ce projet ne peut pas l'installer automatiquement. Fonctionnalite
    plus recente/moins eprouvee que le pipeline .mdl complet : a valider en conditions
    reelles.

    Retourne la liste des chemins de fichiers <stem>_lod{N}.smd generes (peut etre
    vide si aucun LOD n'a pu etre produit).
    """
    log = log_fn or (lambda msg: None)
    smd_src = Path(smd_path).resolve()
    if not smd_src.exists() or smd_src.suffix.lower() != ".smd":
        raise FileNotFoundError(f"Fichier .smd introuvable ou invalide : {smd_path}")

    stem = smd_src.stem
    workdir = TEMP_ROOT / f"smdonly_{stem}"
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    out_dir = Path(output_dir) if output_dir else smd_src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    blender_script = APPDATA_ROOT / "blender_worker.py"
    if not blender_script.exists():
        raise RuntimeError(
            "blender_worker.py introuvable dans %APPDATA%\\LodGenerator\\ -- lancez au "
            "moins un traitement .mdl complet une fois (il le genere automatiquement), "
            "puis relancez le mode SMD only.")

    lod_arg = ",".join(f"{d}:{r}" for d, r in lod_levels)
    cmd = [blender_path, "-b", "-P", str(blender_script), "--",
          "--input", str(smd_src), "--workdir", str(workdir),
          "--lod-count", str(len(lod_levels)), "--lod-levels", lod_arg, "--smd-only"]
    log(f"[SMD-ONLY] Launching Blender for {smd_src.name}...")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Blender a dépassé le délai imparti (10 min) en mode SMD only.")
    for line in (proc.stdout or "").splitlines():
        log(line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            log(f"[BLENDER ERR] {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"Blender a échoué (code {proc.returncode}) en mode SMD only.")

    smd_dir = workdir / "smd"
    generated: List[str] = []
    for idx in range(1, len(lod_levels)):
        src = smd_dir / f"lod{idx}.smd"
        if src.exists():
            dest = out_dir / f"{stem}_lod{idx}.smd"
            shutil.copy2(src, dest)
            generated.append(str(dest))
            log(f"[SMD-ONLY] Generated: {dest.name}")
    if not generated:
        log(f"[SMD-ONLY] No LOD file was produced for {smd_src.name} - check the Blender log above.")
    return generated


class SourceLODApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1280x720")
        self.root.minsize(1024, 600)

        self.lang: str = "en"  # default; overridden by load_settings
        self.theme_text_color = tk.StringVar(value="#2f6fed")  # couleur d'accent/titres (defaut: bleu)
        self.theme_bg_color = tk.StringVar(value="#ffffff")    # couleur de fond generale (defaut: blanc)
        # Options preview (v1.13) : 1) generer la preview 2D decimee pendant le traitement
        # (gain de temps sur un gros batch si desactive) ; 2) afficher le panneau de preview
        # dans l'UI (gain de place si desactive). Independantes l'une de l'autre.
        self.generate_preview_var = tk.BooleanVar(value=True)
        self.show_preview_var = tk.BooleanVar(value=True)
        # Mode SMD only (v1.13, v2) : inclure aussi les .smd trouves lors d'un scan de dossier
        self.include_smd_var = tk.BooleanVar(value=False)
        self._apply_modern_theme()

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.status_queue: queue.Queue[Tuple[str, str]] = queue.Queue()
        self.processing = False
        self.current_selection: Optional[str] = None   # dernier item cliqué (pour l'aperçu)
        self.current_selections: List[str] = []        # tous les items sélectionnés
        self.entries: Dict[str, PropEntry] = {}
        self._dynamic_labels: Dict[str, tk.Widget] = {}  # label widgets registered for translation refresh
        self._preview_pil_cache: Dict[str, "Image.Image"] = {}  # chemin -> image DEJA decodee (fix lag au clic)
        self._preview_loading: Set[str] = set()  # chemins en cours de decodage en arriere-plan
        self.preview_image_ref = None
        self.current_preview_paths: List[str] = []
        self.current_preview_lod_index = 0
        self.preview_3d_window: Optional[ModelPreviewWindow] = None

        # File d'attente de jobs et signal d'arrêt
        self._pending_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()

        # Chronométrage
        self.batch_start_time: Optional[float] = None
        self.current_job_start_time: Optional[float] = None
        self.job_times: List[Tuple[str, float]] = []  # [(model_name, elapsed_seconds), ...]
        self.total_batch_time: float = 0.0

        # Variables de filtrage et recherche
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.apply_filters())
        self.filter_status_var = tk.StringVar(value="Tous")
        self.filter_classname_var = tk.StringVar(value="Tous")
        # Filtre par quantité
        self.filter_count_min_var = tk.IntVar(value=0)
        self.filter_count_max_var = tk.IntVar(value=9999)
        self.filter_count_sort_var = tk.StringVar(value="—")  # "—", "Croissant", "Décroissant"
        self.filter_count_min_var.trace_add("write", lambda *args: self.apply_filters())
        self.filter_count_max_var.trace_add("write", lambda *args: self.apply_filters())
        # Filtre par taille (en Ko)
        self.filter_size_min_var = tk.IntVar(value=0)
        self.filter_size_max_var = tk.IntVar(value=999999)
        self.filter_size_sort_var = tk.StringVar(value="—")  # "—", "Croissant", "Décroissant"
        self.filter_size_min_var.trace_add("write", lambda *args: self.apply_filters())
        self.filter_size_max_var.trace_add("write", lambda *args: self.apply_filters())

        # Parallélisation : nb de props traités simultanément
        # On réserve 1 thread pour l'OS/UI, et on limite au max logique du CPU.
        self._cpu_count: int = os.cpu_count() or 2
        self._cpu_max_workers: int = max(1, self._cpu_count - 1)
        default_workers = max(1, min(2, self._cpu_max_workers))
        self.parallel_jobs_var = tk.IntVar(value=default_workers)
        # Validation : correction automatique si l'utilisateur saisit > max
        self._thread_warning_shown = False
        self.parallel_jobs_var.trace_add("write", self._validate_thread_count)

        self.vmf_var = tk.StringVar()
        self.models_dir_var = tk.StringVar()   # dossier models pour SCAN DIRECT (import direct de .mdl locaux)
        # Dossiers de recherche ADDITIONNELS pour la resolution des modeles reference par un VMF,
        # en plus du dossier de jeu par defaut (Source/GMod Game Folder). Section UI separee
        # ("Custom VMF models Paths"), sans lien avec models_dir_var ci-dessus.
        self.vmf_extra_paths: List[str] = []
        self.game_root_var = tk.StringVar()
        self.output_root_var = tk.StringVar()
        self.studiomdl_var = tk.StringVar(value=self._auto_find_studiomdl())
        self.blender_var = tk.StringVar(value=self._auto_find_blender())
        self.crowbar_var = tk.StringVar(value=self._auto_find_crowbar())
        self.status_var = tk.StringVar(value="Ready")
        self.preview_label_var = tk.StringVar(value="No selection")
        self.physics_mode_var = tk.StringVar(value="keep")


        self.lod_vars = []
        self._MAX_LOD_LEVELS = 8
        default_lods = [(0, 1.0), (50, 0.5), (150, 0.2), (350, 0.08)]
        for i in range(4):
            dv = tk.StringVar(value=str(default_lods[i][0]))
            rv = tk.StringVar(value=str(default_lods[i][1]))
            self.lod_vars.append((dv, rv))

        self._build_ui()
        self._bind_drop_support()
        self._poll_queues()
        self.load_settings()
        try:
            with open(APPDATA_ROOT / "log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n===== Session started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                       f"- {APP_NAME} v{APP_VERSION} =====\n")
        except Exception:
            pass
    
    @staticmethod
    def _hex_to_rgb(hex_color: str):
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        r = max(0, min(255, int(r))); g = max(0, min(255, int(g))); b = max(0, min(255, int(b)))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _adjust_color(self, hex_color: str, amount: int) -> str:
        """Eclaircit (amount>0) ou fonce (amount<0) une couleur hex de 'amount' par canal."""
        try:
            r, g, b = self._hex_to_rgb(hex_color)
        except Exception:
            r, g, b = (128, 128, 128)
        return self._rgb_to_hex(r + amount, g + amount, b + amount)

    def _is_dark(self, hex_color: str) -> bool:
        try:
            r, g, b = self._hex_to_rgb(hex_color)
        except Exception:
            return False
        # Luminance perceptuelle standard
        return (0.299 * r + 0.587 * g + 0.114 * b) < 140

    def _apply_modern_theme(self):
        """
        Palette derivee de DEUX couleurs choisies par l'utilisateur (theme_text_color = couleur
        d'accent/texte de titre, theme_bg_color = couleur de fond generale), plutot que des
        constantes figees. Toutes les autres teintes (panneaux, bordures, entetes, texte
        attenue...) sont calculees automatiquement a partir de ces deux couleurs pour rester
        coherentes et lisibles, que le fond choisi soit clair ou fonce.
        """
        BG   = self.theme_bg_color.get() if hasattr(self, "theme_bg_color") else "#ffffff"
        TEXT_ACCENT = self.theme_text_color.get() if hasattr(self, "theme_text_color") else "#2f6fed"

        dark_bg = self._is_dark(BG)
        # Texte general : toujours lisible sur BG (noir sur fond clair, blanc casse sur fond fonce)
        TEXT        = "#eef1f5" if dark_bg else "#1f2733"
        MUTED_TEXT  = self._adjust_color(TEXT, -70 if not dark_bg else 70)
        PANEL_BG    = self._adjust_color(BG, 10 if dark_bg else -6)
        BORDER      = self._adjust_color(BG, 45 if dark_bg else -35)
        HEADER_BG   = self._adjust_color(BG, 22 if dark_bg else -18)
        ACCENT      = TEXT_ACCENT                          # couleur choisie par l'utilisateur
        ACCENT_DARK = self._adjust_color(ACCENT, -30)
        ACCENT_TXT  = "#ffffff" if self._is_dark(ACCENT) else "#000000"  # contraste sur fond ACCENT (selection)

        style = ttk.Style()
        try: style.theme_use('clam')
        except Exception: pass

        self.root.configure(background=BG)

        style.configure(".", background=BG, foreground=TEXT, font=('Segoe UI', 9))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TLabelframe", background=BG, foreground=TEXT, bordercolor=BORDER, relief="solid")
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT_DARK, font=('Segoe UI', 9, 'bold'))

        style.configure("TButton", background=PANEL_BG, foreground=TEXT, bordercolor=BORDER,
                         focusthickness=1, focuscolor=ACCENT, padding=4, font=('Segoe UI', 8))
        style.map("TButton",
                  background=[("active", HEADER_BG), ("pressed", HEADER_BG)],
                  foreground=[("disabled", MUTED_TEXT)])

        style.configure("Stop.TButton", foreground="#c0392b")

        style.configure("TEntry", fieldbackground=PANEL_BG, bordercolor=BORDER, foreground=TEXT)
        style.configure("TCombobox", fieldbackground=PANEL_BG, background=PANEL_BG, foreground=TEXT)
        style.configure("TSpinbox", fieldbackground=PANEL_BG, foreground=TEXT)

        style.configure("TRadiobutton", background=BG, foreground=TEXT)
        style.configure("TCheckbutton", background=BG, foreground=TEXT)

        style.configure("Treeview", background=PANEL_BG, fieldbackground=PANEL_BG, foreground=TEXT,
                         bordercolor=BORDER, rowheight=20)
        style.configure("Treeview.Heading", background=HEADER_BG, foreground=TEXT,
                         font=('Segoe UI', 9, 'bold'), relief="flat")
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", ACCENT_TXT)])

        style.configure("TProgressbar", background=ACCENT, troughcolor=HEADER_BG, bordercolor=BORDER)
        style.configure("TScale", background=BG, troughcolor=HEADER_BG)
        style.configure("TSeparator", background=BORDER)
        style.configure("TScrollbar", background=HEADER_BG, troughcolor=BG, bordercolor=BORDER)

        # Couleurs de statut dans le Treeview (props) : coherentes avec la palette
        self._status_colors = {
            "done": "#1e8e3e", "error": "#c0392b", "processing": "#e08a00", "pending": MUTED_TEXT,
        }

    def t(self, key: str) -> str:
        """Shortcut for _T(key, self.lang)."""
        return _T(key, self.lang)

    def _validate_thread_count(self, *args):
        """Corrige la valeur du Spinbox si elle dépasse la limite CPU."""
        try:
            val = self.parallel_jobs_var.get()
        except Exception:
            return
        if val > self._cpu_max_workers:
            self.parallel_jobs_var.set(self._cpu_max_workers)
            if not self._thread_warning_shown:
                self._thread_warning_shown = True
                self.root.after(50, lambda: (
                    messagebox.showinfo(
                        APP_NAME,
                        self.t("msg_thread_max").format(max=self._cpu_max_workers, total=self._cpu_count)
                    ),
                    setattr(self, '_thread_warning_shown', False)
                ))
        elif val < 1:
            self.parallel_jobs_var.set(1)
    
    def _auto_find_blender(self) -> str:
        candidates = [shutil.which(n) for n in ("blender.exe", "blender") if shutil.which(n)]
        candidates.extend([
            r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender\blender.exe",
        ])
        try:
            libs = find_steam_libraries()
            for lib in libs:
                cand = lib / "steamapps" / "common" / "Blender" / "blender.exe"
                if cand.exists():
                    candidates.append(str(cand))
        except Exception:
            pass
        for c in candidates:
            if c and Path(c).exists(): return c
        return "blender"

    def _auto_find_studiomdl(self) -> str:
        candidates = [
            r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe",
            r"C:\Program Files\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe",
        ]
        try:
            libs = find_steam_libraries()
            for lib in libs:
                cand = lib / "steamapps" / "common" / "GarrysMod" / "bin" / "studiomdl.exe"
                if cand.exists():
                    candidates.append(str(cand))
                # Also check Source SDK Base 2013 Multiplayer
                cand_sdk = lib / "steamapps" / "common" / "Source SDK Base 2013 Multiplayer" / "bin" / "studiomdl.exe"
                if cand_sdk.exists():
                    candidates.append(str(cand_sdk))
        except Exception:
            pass
        for c in candidates:
            if Path(c).exists(): return c
        return shutil.which("studiomdl.exe") or shutil.which("studiomdl") or "studiomdl.exe"

    def _auto_find_crowbar(self) -> str:
        # Get the script directory
        script_dir = None
        if sys.argv and sys.argv[0]:
            try:
                script_dir = Path(sys.argv[0]).resolve().parent
            except Exception:
                pass
        cwd = Path.cwd().resolve()
        
        # Build search list
        dirs_to_check = []
        if script_dir:
            dirs_to_check.append(script_dir)
            dirs_to_check.append(script_dir / "tools")
            dirs_to_check.append(script_dir / "tool")
            dirs_to_check.append(script_dir.parent)
            dirs_to_check.append(script_dir.parent / "tools")
            dirs_to_check.append(script_dir.parent / "tool")
            
        dirs_to_check.append(cwd)
        dirs_to_check.append(cwd / "tools")
        dirs_to_check.append(cwd / "tool")
        dirs_to_check.append(cwd.parent)
        dirs_to_check.append(cwd.parent / "tools")
        dirs_to_check.append(cwd.parent / "tool")
        
        # Add system paths
        system_paths = [
            Path(r"C:\Program Files (x86)\Crowbar"),
            Path(r"C:\Program Files\Crowbar"),
        ]
        dirs_to_check.extend(system_paths)
        
        # Check all unique directories in priority order
        seen_dirs = set()
        for d in dirs_to_check:
            d_resolved = d.resolve() if d else None
            if d_resolved and d_resolved.exists() and d_resolved not in seen_dirs:
                seen_dirs.add(d_resolved)
                for name in ("CrowbarCLI.exe", "CrowbarCLI", "Crowbar.exe", "Crowbar"):
                    cand = d_resolved / name
                    if cand.is_file():
                        return str(cand)
                        
        # Check system PATH
        sh_path = shutil.which("CrowbarCLI.exe") or shutil.which("CrowbarCLI") or shutil.which("Crowbar.exe") or shutil.which("Crowbar")
        if sh_path:
            return sh_path
            
        return "CrowbarCLI.exe"

    def get_current_lod_levels(self) -> List[Tuple[int, float]]:
        levels = []
        defaults = [(0, 1.0), (50, 0.5), (150, 0.2), (350, 0.08)]
        for i, (dv, rv) in enumerate(self.lod_vars):
            try:
                levels.append((int(dv.get()), float(rv.get())))
            except Exception:
                levels.append(defaults[i] if i < len(defaults) else (i * 100, 0.5 / (2 ** i)))
        return levels

    def _rebuild_lod_grid(self):
        """Rebuilds the LOD grid widget from self.lod_vars (max 8, 4 per row)."""
        for w in self._lod_grid_container.winfo_children():
            w.destroy()
        for i, (dv, rv) in enumerate(self.lod_vars):
            row, col_base = divmod(i, 4)
            col_base *= 3
            ttk.Label(self._lod_grid_container, text=f"L{i}:", font=('Arial', 8)).grid(
                row=row, column=col_base, sticky="w", padx=(2, 1))
            dist_e = ttk.Entry(self._lod_grid_container, textvariable=dv, width=4)
            dist_e.grid(row=row, column=col_base + 1, sticky="w", padx=1)
            ratio_e = ttk.Entry(self._lod_grid_container, textvariable=rv, width=5)
            ratio_e.grid(row=row, column=col_base + 2, sticky="w", padx=(1, 4))
            if i == 0:
                dist_e.configure(state="disabled")
                ratio_e.configure(state="disabled")
        # Update button states
        n = len(self.lod_vars)
        if hasattr(self, "_lod_add_btn"):
            self._lod_add_btn.configure(state="normal" if n < self._MAX_LOD_LEVELS else "disabled")
            self._lod_remove_btn.configure(state="normal" if n > 2 else "disabled")

    def _add_lod_level(self):
        """Adds a new LOD level after the last one, up to _MAX_LOD_LEVELS."""
        if len(self.lod_vars) >= self._MAX_LOD_LEVELS:
            return
        i = len(self.lod_vars)
        prev_dist = int(self.lod_vars[-1][0].get() or 0)
        prev_ratio = float(self.lod_vars[-1][1].get() or 0.1)
        new_dist = prev_dist + 150
        new_ratio = round(max(prev_ratio * 0.5, 0.02), 3)
        self.lod_vars.append((tk.StringVar(value=str(new_dist)),
                               tk.StringVar(value=str(new_ratio))))
        self._rebuild_lod_grid()

    def _remove_lod_level(self):
        """Removes the last LOD level. Minimum is L0 + L1 = 2 levels."""
        if len(self.lod_vars) <= 2:
            return
        self.lod_vars.pop()
        self._rebuild_lod_grid()

    def _make_path_row(self, parent, label, var, button_text, browse_cmd, row, label_key=None):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        if label_key:
            self._dynamic_labels[label_key] = lbl
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        button = ttk.Button(parent, text=button_text, command=browse_cmd)
        button.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=3)
        parent.columnconfigure(1, weight=1)
        return entry

    def _build_menu_bar(self):
        """
        Barre de menu style WinForm (v1.13, v2) : "Fichier" (ouvrir VMF/dossier/SMD),
        "Affichage" (seul acces a la fenetre de log -- plus jamais visible par defaut
        dans la fenetre principale), "Help" (instructions/GitHub).
        """
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=self.t("menu_open_vmf"), command=self.browse_vmf)
        file_menu.add_command(label=self.t("menu_open_folder"), command=self.browse_models_dir)
        file_menu.add_command(label=self.t("menu_open_smd"), command=self.browse_smd_only)
        file_menu.add_separator()
        file_menu.add_command(label=self.t("menu_quit"), command=self.root.destroy)
        menubar.add_cascade(label=self.t("menu_file"), menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label=self.t("menu_see_log"), command=self.show_log_window)
        menubar.add_cascade(label=self.t("menu_view"), menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=self.t("menu_instructions"), command=self._on_show_instructions)
        help_menu.add_command(label=self.t("menu_github"),
                              command=lambda: webbrowser.open("https://github.com/Lumino-2-0/LOD-Generator-Source-SDK"))
        menubar.add_cascade(label=self.t("menu_help"), menu=help_menu)

        self.root.config(menu=menubar)
        self._menubar = menubar
        self._file_menu = file_menu
        self._view_menu = view_menu
        self._help_menu = help_menu

    def _on_show_instructions(self):
        messagebox.showinfo(self.t("menu_instructions"), self.t("msg_instructions"))

    def _build_ui(self):
        self._build_menu_bar()
        main = ttk.Frame(self.root, padding=5)
        main.pack(fill="both", expand=True)
        self._main_frame = main
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # --- Header ---
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 5))
        ttk.Label(header, text=f"{APP_NAME} v{APP_VERSION}", font=('Segoe UI', 12, 'bold')).pack(side="left")
        # Language selector
        lang_frame = ttk.Frame(header)
        lang_frame.pack(side="right")
        self._lang_label = ttk.Label(lang_frame, text=self.t("lang_label"), font=('Arial', 8))
        self._lang_label.pack(side="left", padx=(0, 3))
        self._lang_var = tk.StringVar(value="English" if self.lang == "en" else "Francais")
        lang_combo = ttk.Combobox(lang_frame, textvariable=self._lang_var,
                                   values=["English", "Francais"], state="readonly", width=9)
        lang_combo.pack(side="left")
        lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)
        ttk.Label(header, textvariable=self.status_var, font=('Segoe UI', 8)).pack(side="right", padx=(0, 8))

        # Theme color picker : deux "ronds" de couleur (texte/accent, fond) + bouton Appliquer.
        # Le choix ne prend effet (et n'est sauvegarde automatiquement dans le fichier de
        # parametres) qu'au clic sur "Appliquer", pour ne pas re-styliser toute l'UI a chaque
        # clic dans la palette de couleurs.
        theme_frame = ttk.Frame(header)
        theme_frame.pack(side="right", padx=(0, 10))
        self._theme_label = ttk.Label(theme_frame, text=self.t("lbl_theme"), font=('Arial', 8))
        self._theme_label.pack(side="left", padx=(0, 4))

        self._theme_text_swatch = tk.Canvas(theme_frame, width=18, height=18, highlightthickness=1,
                                             highlightbackground="#888888", cursor="hand2")
        self._theme_text_swatch.pack(side="left", padx=2)
        self._theme_text_oval = self._theme_text_swatch.create_oval(2, 2, 16, 16,
                                                                      fill=self.theme_text_color.get(), outline="")
        self._theme_text_swatch.bind("<Button-1>", lambda e: self._pick_theme_color("text"))

        self._theme_bg_swatch = tk.Canvas(theme_frame, width=18, height=18, highlightthickness=1,
                                           highlightbackground="#888888", cursor="hand2")
        self._theme_bg_swatch.pack(side="left", padx=2)
        self._theme_bg_oval = self._theme_bg_swatch.create_oval(2, 2, 16, 16,
                                                                  fill=self.theme_bg_color.get(), outline="")
        self._theme_bg_swatch.bind("<Button-1>", lambda e: self._pick_theme_color("bg"))

        self._btn_apply_theme = ttk.Button(theme_frame, text=self.t("btn_apply_theme"),
                                            command=self.apply_theme_colors, width=9)
        self._btn_apply_theme.pack(side="left", padx=(4, 0))

        # --- Parameters ---
        self._path_section = ttk.LabelFrame(main, text=self.t("lbl_params"), padding=4)
        self._path_section.pack(fill="x", pady=(0, 4))
        self._make_path_row(parent=self._path_section, label=self.t("lbl_vmf"),
                            var=self.vmf_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_vmf, row=0, label_key="lbl_vmf")
        self._make_path_row(parent=self._path_section, label=self.t("lbl_models_dir"),
                            var=self.models_dir_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_models_dir, row=1, label_key="lbl_models_dir")
        self._make_path_row(parent=self._path_section, label=self.t("lbl_game_root"),
                            var=self.game_root_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_game_root, row=2, label_key="lbl_game_root")
        self._make_path_row(parent=self._path_section, label=self.t("lbl_output"),
                            var=self.output_root_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_output_root, row=3, label_key="lbl_output")
        self._chk_include_smd = ttk.Checkbutton(self._path_section, text=self.t("chk_include_smd"),
                                                variable=self.include_smd_var)
        self._chk_include_smd.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # --- Custom VMF models Paths (dossiers de recherche additionnels pour la resolution
        #     des modeles reference par un VMF, en plus du dossier de jeu par defaut) ---
        self._vmf_paths_section = ttk.LabelFrame(main, text=self.t("lbl_vmf_extra_paths"), padding=4)
        self._vmf_paths_section.pack(fill="x", pady=(0, 4))
        vmf_paths_row = ttk.Frame(self._vmf_paths_section)
        vmf_paths_row.pack(fill="x")
        self.vmf_paths_listbox = tk.Listbox(vmf_paths_row, height=3, exportselection=False,
                                             selectmode="extended")
        self.vmf_paths_listbox.pack(side="left", fill="x", expand=True)
        vmf_paths_btns = ttk.Frame(vmf_paths_row)
        vmf_paths_btns.pack(side="left", padx=(6, 0), fill="y")
        self._btn_add_vmf_path = ttk.Button(vmf_paths_btns, text=self.t("btn_add_path"),
                                             command=self.add_vmf_extra_path, width=10)
        self._btn_add_vmf_path.pack(fill="x", pady=(0, 2))
        self._btn_remove_vmf_path = ttk.Button(vmf_paths_btns, text=self.t("btn_remove_path"),
                                                command=self.remove_vmf_extra_path, width=10)
        self._btn_remove_vmf_path.pack(fill="x")

        # --- Tools ---
        self._tools_section = ttk.LabelFrame(main, text=self.t("lbl_tools"), padding=4)
        self._tools_section.pack(fill="x", pady=(0, 4))
        self._make_path_row(parent=self._tools_section, label=self.t("lbl_studiomdl"),
                            var=self.studiomdl_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_studiomdl, row=0, label_key="lbl_studiomdl")
        self._make_path_row(parent=self._tools_section, label=self.t("lbl_blender"),
                            var=self.blender_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_blender, row=1, label_key="lbl_blender")
        self._make_path_row(parent=self._tools_section, label=self.t("lbl_crowbar"),
                            var=self.crowbar_var, button_text=self.t("btn_browse"),
                            browse_cmd=self.browse_crowbar, row=2, label_key="lbl_crowbar")

        # --- LOD + Physics ---
        config_row = ttk.Frame(main)
        config_row.pack(fill="x", pady=(0, 4))
        self._lod_frame = ttk.LabelFrame(config_row, text=self.t("lbl_lod"), padding=4)
        self._lod_frame.pack(side="left", fill="x", expand=True, padx=(0, 4))
        # Container for the dynamic grid
        self._lod_grid_container = ttk.Frame(self._lod_frame)
        self._lod_grid_container.pack(fill="x")
        # +/- buttons row
        lod_btn_row = ttk.Frame(self._lod_frame)
        lod_btn_row.pack(fill="x", pady=(2, 0))
        self._lod_add_btn = ttk.Button(lod_btn_row, text="+ LOD", width=7,
                                       command=self._add_lod_level)
        self._lod_add_btn.pack(side="left", padx=(2, 2))
        self._lod_remove_btn = ttk.Button(lod_btn_row, text="– LOD", width=7,
                                          command=self._remove_lod_level)
        self._lod_remove_btn.pack(side="left")
        self._rebuild_lod_grid()
        self._physics_frame = ttk.LabelFrame(config_row, text=self.t("lbl_physics"), padding=4)
        self._physics_frame.pack(side="left", fill="x")
        self._phys_rebuild_btn = ttk.Radiobutton(self._physics_frame, text=self.t("phys_rebuild"),
                                                   variable=self.physics_mode_var, value="rebuild")
        self._phys_rebuild_btn.pack(anchor="w", side="left", padx=2)
        self._phys_keep_btn = ttk.Radiobutton(self._physics_frame, text=self.t("phys_keep"),
                                               variable=self.physics_mode_var, value="keep")
        self._phys_keep_btn.pack(anchor="w", side="left", padx=2)

        # --- Action buttons ---
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(0, 4))
        self._btn_parse_vmf    = ttk.Button(actions, text=self.t("btn_parse_vmf"),    command=self.parse_vmf,            width=14)
        self._btn_parse_folder = ttk.Button(actions, text=self.t("btn_parse_folder"), command=self.parse_folder,         width=18)
        self._btn_scan_vpk     = ttk.Button(actions, text=self.t("btn_scan_vpk"),     command=self.scan_all_vpks,        width=13)
        self._btn_3d           = ttk.Button(actions, text=self.t("btn_3d"),           command=self.open_3d_viewer,       width=12)
        self._btn_open_folder  = ttk.Button(actions, text=self.t("btn_folder"),       command=self.open_output_folder,   width=21)
        self._btn_save         = ttk.Button(actions, text=self.t("btn_save"),         command=self.save_settings,        width=14)
        self._btn_load         = ttk.Button(actions, text=self.t("btn_load"),         command=self.load_settings,        width=15)
        self._btn_cache        = ttk.Button(actions, text=self.t("btn_cache"),        command=self.clear_vpk_cache_ui,   width=11)
        for btn in (self._btn_parse_vmf, self._btn_parse_folder, self._btn_scan_vpk,
                    self._btn_3d, self._btn_open_folder, self._btn_save, self._btn_load, self._btn_cache):
            btn.pack(side="left", padx=1)
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=6)
        self._threads_label = ttk.Label(actions, text=self.t("lbl_threads"), font=('Arial', 8))
        self._threads_label.pack(side="left", padx=(0, 2))
        self.parallel_jobs_spin = ttk.Spinbox(actions, from_=1, to=self._cpu_max_workers,
                                              textvariable=self.parallel_jobs_var, width=3)
        self.parallel_jobs_spin.pack(side="left")
        ttk.Label(actions, text=f"/ {self._cpu_max_workers}", font=('Arial', 7), foreground="gray").pack(side="left", padx=(2, 0))

        actions2 = ttk.Frame(main)
        actions2.pack(fill="x", pady=(0, 4))
        self._btn_smd_only = ttk.Button(actions2, text=self.t("btn_smd_only"),
                                        command=self.browse_smd_only, width=20)
        self._btn_smd_only.pack(side="left", padx=1)

        # --- Center pane (list | preview) ---
        center = ttk.Panedwindow(main, orient="horizontal")
        center.pack(fill="both", expand=True, pady=(2, 2))
        left = ttk.Frame(center)
        right = ttk.Frame(center)
        center.add(left, weight=1)
        center.add(right, weight=2)

        # LEFT: props list with search + filters
        props_header = ttk.Frame(left)
        props_header.pack(fill="x", pady=(0, 2))
        self._props_label = ttk.Label(props_header, text=self.t("lbl_props"), font=('Arial', 9, 'bold'))
        self._props_label.pack(side="left")

        search_frame = ttk.Frame(left)
        search_frame.pack(fill="x", pady=(0, 2))
        self._search_lbl = ttk.Label(search_frame, text=self.t("lbl_search") + ":", font=('Arial', 8))
        self._search_lbl.pack(side="left", padx=(0, 3))
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self._btn_clear_search = ttk.Button(search_frame, text=self.t("btn_clear_search"),
                                             width=3, command=lambda: self.search_var.set(""))
        self._btn_clear_search.pack(side="left", padx=(2, 0))

        filter_frame = ttk.Frame(left)
        filter_frame.pack(fill="x", pady=(0, 2))
        self._filter_status_lbl = ttk.Label(filter_frame, text=self.t("lbl_filter_status"), font=('Arial', 8))
        self._filter_status_lbl.pack(side="left", padx=(0, 2))
        _status_vals = [self.t("filter_all"), self.t("filter_ready"), self.t("filter_processing"),
                        self.t("filter_ok"), self.t("filter_error")]
        self.status_filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_status_var,
                                                 values=_status_vals, state="readonly", width=9)
        self.status_filter_combo.pack(side="left", padx=(0, 4))
        self.status_filter_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())
        self._filter_type_lbl = ttk.Label(filter_frame, text=self.t("lbl_filter_type"), font=('Arial', 8))
        self._filter_type_lbl.pack(side="left", padx=(0, 2))
        self.classname_filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_classname_var,
                                                    values=[self.t("filter_all")], state="readonly", width=12)
        self.classname_filter_combo.pack(side="left")
        self.classname_filter_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        filter_count_frame = ttk.Frame(left)
        filter_count_frame.pack(fill="x", pady=(0, 2))
        self._filter_count_lbl = ttk.Label(filter_count_frame, text=self.t("lbl_filter_count"), font=('Arial', 8))
        self._filter_count_lbl.pack(side="left", padx=(0, 2))
        ttk.Label(filter_count_frame, text=self.t("lbl_count_min"), font=('Arial', 7), foreground="gray").pack(side="left")
        ttk.Spinbox(filter_count_frame, from_=0, to=9999, textvariable=self.filter_count_min_var,
                    width=4, font=('Arial', 8)).pack(side="left", padx=(1, 3))
        ttk.Label(filter_count_frame, text=self.t("lbl_count_max"), font=('Arial', 7), foreground="gray").pack(side="left")
        ttk.Spinbox(filter_count_frame, from_=0, to=9999, textvariable=self.filter_count_max_var,
                    width=4, font=('Arial', 8)).pack(side="left", padx=(1, 4))
        self._sort_lbl = ttk.Label(filter_count_frame, text=self.t("lbl_sort"), font=('Arial', 8))
        self._sort_lbl.pack(side="left", padx=(0, 2))
        self.count_sort_combo = ttk.Combobox(filter_count_frame, textvariable=self.filter_count_sort_var,
                                              values=[self.t("sort_none"), self.t("sort_asc"), self.t("sort_desc")],
                                              state="readonly", width=10)
        self.count_sort_combo.pack(side="left")
        self.count_sort_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        filter_size_frame = ttk.Frame(left)
        filter_size_frame.pack(fill="x", pady=(0, 2))
        self._filter_size_lbl = ttk.Label(filter_size_frame, text=self.t("lbl_filter_size"), font=('Arial', 8))
        self._filter_size_lbl.pack(side="left", padx=(0, 2))
        ttk.Label(filter_size_frame, text=self.t("lbl_size_min"), font=('Arial', 7), foreground="gray").pack(side="left")
        ttk.Spinbox(filter_size_frame, from_=0, to=999999, textvariable=self.filter_size_min_var,
                    width=6, font=('Arial', 8)).pack(side="left", padx=(1, 3))
        ttk.Label(filter_size_frame, text=self.t("lbl_size_max"), font=('Arial', 7), foreground="gray").pack(side="left")
        ttk.Spinbox(filter_size_frame, from_=0, to=999999, textvariable=self.filter_size_max_var,
                    width=6, font=('Arial', 8)).pack(side="left", padx=(1, 4))
        self._size_sort_lbl = ttk.Label(filter_size_frame, text=self.t("lbl_sort"), font=('Arial', 8))
        self._size_sort_lbl.pack(side="left", padx=(0, 2))
        self.size_sort_combo = ttk.Combobox(filter_size_frame, textvariable=self.filter_size_sort_var,
                                            values=[self.t("sort_none"), self.t("sort_asc"), self.t("sort_desc")],
                                            state="readonly", width=10)
        self.size_sort_combo.pack(side="left")
        self.size_sort_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True, pady=(2, 0))
        self.tree = ttk.Treeview(tree_frame, columns=("model", "classname", "count", "size", "status"),
                                 show="headings", selectmode="extended", height=8)
        self.tree.heading("model",     text=self.t("col_model"))
        self.tree.heading("classname", text=self.t("col_type"))
        self.tree.heading("count",     text=self.t("col_qty"))
        self.tree.heading("size",      text=self.t("col_size"))
        self.tree.heading("status",    text=self.t("col_status"))
        self.tree.column("model",     width=180)
        self.tree.column("classname", width=70)
        self.tree.column("count",     width=35)
        self.tree.column("size",      width=60)
        self.tree.column("status",    width=55)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # RIGHT: preview + details
        preview_opts_row = ttk.Frame(right)
        preview_opts_row.pack(fill="x", pady=(0, 2))
        self._chk_generate_preview = ttk.Checkbutton(preview_opts_row, text=self.t("chk_generate_preview"),
                                                       variable=self.generate_preview_var)
        self._chk_generate_preview.pack(side="left")
        self._chk_show_preview = ttk.Checkbutton(preview_opts_row, text=self.t("chk_show_preview"),
                                                   variable=self.show_preview_var,
                                                   command=self._on_toggle_show_preview)
        self._chk_show_preview.pack(side="left", padx=(8, 0))

        self._preview_label_hdr = ttk.Label(right, text=self.t("lbl_preview"), font=('Arial', 9, 'bold'))
        self._preview_label_hdr.pack(anchor="w")
        self.preview_box = tk.Frame(right, bg='#222222', relief="solid", borderwidth=1)
        self.preview_box.pack(fill="both", expand=True, pady=(2, 2))
        self.preview_canvas = tk.Label(self.preview_box, text=self.t("select_prop"), relief="flat",
                                        anchor="center", justify="center", bg='#222222', fg='#cccccc',
                                        font=('Arial', 9))
        self.preview_canvas.pack(fill="both", expand=True)
        # FIX deformation UI (1920x1080 et resolutions similaires) : l'image de preview etait
        # generee a une taille FIXE (650x520 px), ce qui forcait la fenetre a exiger plus
        # d'espace vertical que ce que l'ecran pouvait offrir, ecrasant les boutons/le log en
        # dessous. On recalcule desormais la taille cible depuis l'espace REELLEMENT disponible
        # a chaque redimensionnement (avec un court debounce pour eviter de re-render en boucle).
        self._preview_resize_job = None
        self._last_preview_path: Optional[str] = None
        self.preview_box.bind("<Configure>", self._on_preview_box_resize)
        self.preview_filename_label = ttk.Label(right, textvariable=self.preview_label_var,
                                                 justify="center", font=('Arial', 7))
        self.preview_filename_label.pack(fill="x", pady=1)
        self._info_frame = ttk.LabelFrame(right, text=self.t("lbl_info"), padding=3)
        self._info_frame.pack(fill="x", pady=2)
        self.details_text = tk.Text(self._info_frame, height=3, wrap="word", font=('Consolas', 7))
        self.details_text.pack(fill="x", expand=False)
        self.details_text.configure(state="disabled")
        slider_row = ttk.Frame(right)
        slider_row.pack(fill="x", pady=2)
        self._lod_slider_lbl = ttk.Label(slider_row, text=self.t("lbl_lod_slider"), font=('Arial', 8))
        self._lod_slider_lbl.pack(side="left")
        self.lod_scale = ttk.Scale(slider_row, from_=0, to=0, orient="horizontal", command=self.on_lod_slider)
        self.lod_scale.pack(side="left", fill="x", expand=True, padx=4)
        self.lod_scale.state(["disabled"])
        self.lod_value_label = ttk.Label(slider_row, text="0", font=('Arial', 8, 'bold'), width=3)
        self.lod_value_label.pack(side="right")

        # --- Bottom: action buttons + progressbar ---
        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=3)
        self.lod_all_btn = ttk.Button(bottom, text=self.t("btn_all"),      command=self.lod_all,      state="disabled")
        self.lod_one_btn = ttk.Button(bottom, text=self.t("btn_selected"), command=self.lod_selected, state="disabled")
        self.lod_all_btn.pack(side="left")
        self.lod_one_btn.pack(side="left", padx=4)
        self._btn_clear_list = ttk.Button(bottom, text=self.t("btn_clear"), command=self.clear_list)
        self._btn_clear_list.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(bottom, text=self.t("btn_stop"), command=self._request_stop,
                                   state="disabled", style="Stop.TButton")
        self.stop_btn.pack(side="left", padx=(0, 8))
        _style = ttk.Style()
        _style.configure("Stop.TButton", foreground="red")
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(bottom, variable=self.progress_var, maximum=100,
                                             length=180, mode="determinate")
        self.progress_bar.pack(side="left", padx=(4, 4), fill="x", expand=True)
        self.progress_label = ttk.Label(bottom, text="", font=('Arial', 7), width=20)
        self.progress_label.pack(side="left")

        # --- Log ---
        # Log (v1.13, v2) : plus de panneau visible par defaut dans la fenetre
        # principale -- uniquement accessible via le menu "Affichage" -> "Voir le Log"
        # (fenetre externe dediee, avec son propre bouton d'export). Voir _build_log_window.
        self._build_log_window()

        # Colour tags
        self.tree.tag_configure("done",       foreground="green")
        self.tree.tag_configure("error",      foreground="red")
        self.tree.tag_configure("processing", foreground="orange")
        self.tree.tag_configure("pending",    foreground="gray")

    def _on_lang_change(self, _event=None):
        """Switch UI language and rebuild all translatable labels."""
        choice = self._lang_var.get()
        self.lang = "en" if choice == "English" else "fr"
        self._refresh_ui_labels()
        self.save_settings()

    def _refresh_ui_labels(self):
        """Update all translatable widget texts after a language switch."""
        L = self.lang
        self._build_menu_bar()
        self._lang_label.configure(text=_T("lang_label", L))
        self._theme_label.configure(text=_T("lbl_theme", L))
        self._chk_generate_preview.configure(text=_T("chk_generate_preview", L))
        self._chk_show_preview.configure(text=_T("chk_show_preview", L))
        self._btn_export_log.configure(text=_T("btn_export_log", L))
        self._btn_apply_theme.configure(text=_T("btn_apply_theme", L))
        for key, widget in self._dynamic_labels.items():
            try: widget.configure(text=_T(key, L))
            except Exception: pass
        self._vmf_paths_section.configure(text=_T("lbl_vmf_extra_paths", L))
        self._btn_add_vmf_path.configure(text=_T("btn_add_path", L))
        self._btn_remove_vmf_path.configure(text=_T("btn_remove_path", L))
        self._path_section.configure(text=_T("lbl_params", L))
        self._tools_section.configure(text=_T("lbl_tools", L))
        self._lod_frame.configure(text=_T("lbl_lod", L))
        self._physics_frame.configure(text=_T("lbl_physics", L))
        self._phys_rebuild_btn.configure(text=_T("phys_rebuild", L))
        self._phys_keep_btn.configure(text=_T("phys_keep", L))
        self._btn_parse_vmf.configure(text=_T("btn_parse_vmf", L))
        self._btn_parse_folder.configure(text=_T("btn_parse_folder", L))
        self._btn_scan_vpk.configure(text=_T("btn_scan_vpk", L))
        self._btn_3d.configure(text=_T("btn_3d", L))
        self._btn_open_folder.configure(text=_T("btn_folder", L))
        self._btn_save.configure(text=_T("btn_save", L))
        self._btn_load.configure(text=_T("btn_load", L))
        self._btn_cache.configure(text=_T("btn_cache", L))
        self._btn_smd_only.configure(text=_T("btn_smd_only", L))
        self._chk_include_smd.configure(text=_T("chk_include_smd", L))
        self._threads_label.configure(text=_T("lbl_threads", L))
        self._props_label.configure(text=_T("lbl_props", L))
        self._search_lbl.configure(text=_T("lbl_search", L) + ":")
        self._btn_clear_search.configure(text=_T("btn_clear_search", L))
        self._filter_status_lbl.configure(text=_T("lbl_filter_status", L))
        self._filter_type_lbl.configure(text=_T("lbl_filter_type", L))
        self._filter_count_lbl.configure(text=_T("lbl_filter_count", L))
        self._filter_size_lbl.configure(text=_T("lbl_filter_size", L))
        self._sort_lbl.configure(text=_T("lbl_sort", L))
        self._size_sort_lbl.configure(text=_T("lbl_sort", L))
        self._preview_label_hdr.configure(text=_T("lbl_preview", L))
        self._info_frame.configure(text=_T("lbl_info", L))
        self._lod_slider_lbl.configure(text=_T("lbl_lod_slider", L))
        self.lod_all_btn.configure(text=_T("btn_all", L))
        lod_one_text = _T("btn_selected", L)
        n = len(self.current_selections)
        if n > 1: lod_one_text += f" ({n})"
        self.lod_one_btn.configure(text=lod_one_text)
        self._btn_clear_list.configure(text=_T("btn_clear", L))
        self.stop_btn.configure(text=_T("btn_stop", L))
        # Update combobox values
        _status_vals = [_T("filter_all",L), _T("filter_ready",L), _T("filter_processing",L),
                        _T("filter_ok",L), _T("filter_error",L)]
        self.status_filter_combo.configure(values=_status_vals)
        self.count_sort_combo.configure(values=[_T("sort_none",L), _T("sort_asc",L), _T("sort_desc",L)])
        self.size_sort_combo.configure(values=[_T("sort_none",L), _T("sort_asc",L), _T("sort_desc",L)])
        # Tree headings
        self.tree.heading("model",     text=_T("col_model", L))
        self.tree.heading("classname", text=_T("col_type", L))
        self.tree.heading("count",     text=_T("col_qty", L))
        self.tree.heading("size",      text=_T("col_size", L))
        self.tree.heading("status",    text=_T("col_status", L))

    @staticmethod
    def _run_silent(cmd: list, **kwargs) -> subprocess.CompletedProcess:
        """
        Lance un subprocess sans ouvrir de fenêtre CMD visible ni voler le focus.
        Fonctionne sur Windows (CREATE_NO_WINDOW + STARTF_USESHOWWINDOW) et
        sur les autres plateformes (comportement identique à subprocess.run).
        """
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        flags = subprocess.CREATE_NO_WINDOW
        return subprocess.run(
            cmd,
            startupinfo=si,
            creationflags=flags,
            **kwargs
        )

    def _bind_drop_support(self):
        if not DND_AVAILABLE:
            self.log("[DND] tkinterdnd2 module not installed - drag & drop disabled. "
                     "Install with: pip install tkinterdnd2")
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
            # Certains builds de tkdnd ne remontent le <<Drop>> qu'au widget exact survole ;
            # on enregistre donc aussi la frame principale en plus du root pour plus de fiabilite.
            if hasattr(self, "_main_frame"):
                self._main_frame.drop_target_register(DND_FILES)
                self._main_frame.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as e:
            self.log(f"[DND] Failed to enable drag & drop: {e}")

    def _on_drop(self, event):
        paths = self.root.tk.splitlist(event.data or "")
        if not paths:
            return
        dropped = str(paths[0])
        p = Path(dropped)
        if dropped.lower().endswith(".vmf"):
            self.vmf_var.set(dropped)
            self.log(f"[DND] VMF file dropped: {dropped}")
        elif dropped.lower().endswith(".smd"):
            self.log(f"[DND] SMD file dropped: {dropped}")
            self.add_smd_entries_to_list([dropped])
        elif p.is_dir():
            smds = list(p.rglob("*.smd"))
            if smds:
                self.log(f"[DND] Folder with {len(smds)} .smd dropped")
                self.add_smd_entries_to_list([str(s) for s in smds])
            else:
                self.models_dir_var.set(dropped)
                self.log(f"[DND] Models folder dropped: {dropped}")
        else:
            self.log(f"[DND] Unsupported item dropped (expected a .vmf file, a folder, or a .smd): {dropped}")

    def add_smd_entries_to_list(self, smd_paths: List[str]):
        """
        Mode 'SMD only' (v1.13, v2) : ajoute un ou plusieurs .smd a LA MEME LISTE que
        les .mdl (au lieu de lancer un traitement immediat isole), pour pouvoir les
        selectionner et les traiter avec les MEMES boutons "TOUS LES PROPS" /
        "SELECTIONNES" que le reste -- exactement comme demande. Action additive :
        n'efface jamais la liste existante (contrairement a une Analyse VMF/Dossier).
        """
        added = 0
        for smd_path in smd_paths:
            p = Path(smd_path)
            if not p.exists() or p.suffix.lower() != ".smd":
                continue
            key = f"[SMD] {p.name}"
            if key in self.entries:
                self.log(f"[SMD-ONLY] Already in the list, skipped: {p.name}")
                continue
            try:
                size = get_model_display_size(str(p))
            except Exception:
                size = 0
            entry = PropEntry(original_model=key, classname="SMD", usage_count=1,
                             file_size=size, is_smd_only=True)
            entry.resolved_source_path = str(p)
            entry.status = "ready"
            self.entries[key] = entry
            self.tree.insert("", "end", iid=key,
                            values=(key, entry.classname, entry.usage_count,
                                    format_file_size(entry.file_size), self._status_label("ready")),
                            tags=("ready",))
            added += 1
        if added:
            self.log(f"[SMD-ONLY] {added} SMD file(s) added to the props list.")
            self.lod_all_btn.configure(state="normal")
            self._update_classname_filter_options()

    def browse_smd_only(self):
        """Menu Fichier > 'Ouvrir SMD (mode SMD only)...' : ajoute un ou plusieurs .smd
        choisis dans l'explorateur de fichiers a la liste des props (toujours possible,
        en plus du glisser-deposer et du scan de dossier avec 'Inclure les SMD')."""
        paths = filedialog.askopenfilenames(title="SMD file(s) (mode SMD only)",
                                            filetypes=[("Source Engine SMD", "*.smd")])
        if paths:
            self.add_smd_entries_to_list(list(paths))

    def browse_vmf(self):
        p = filedialog.askopenfilename(title="VMF Map File", filetypes=[("Source Engine VMF", "*.vmf")])
        if p: self.vmf_var.set(p)

    def browse_models_dir(self):
        p = filedialog.askdirectory(title="Models folder to process (e.g. .../garrysmod/models OR Custom)")
        if p: self.models_dir_var.set(p)

    def add_vmf_extra_path(self):
        """
        Section 'Custom VMF models Paths' : ajoute un dossier de recherche additionnel pour la
        resolution des modeles reference par un VMF (en plus du dossier de jeu par defaut,
        ex: C:/Program Files (x86)/Steam/steamapps/common/GarrysMod/garrysmod/models). Utile
        pour piocher des modeles qui vivent dans un emplacement different (autre addon/mod,
        dossier de contenu partage, etc).
        """
        p = filedialog.askdirectory(title=self.t("lbl_vmf_extra_paths"))
        if not p:
            return
        if p not in self.vmf_extra_paths:
            self.vmf_extra_paths.append(p)
            self.vmf_paths_listbox.insert("end", p)

    def remove_vmf_extra_path(self):
        sel = list(self.vmf_paths_listbox.curselection())
        for idx in reversed(sel):
            del self.vmf_extra_paths[idx]
            self.vmf_paths_listbox.delete(idx)

    def _extra_model_search_dirs(self) -> List[str]:
        return list(self.vmf_extra_paths)

    def browse_game_root(self):
        p = filedialog.askdirectory(title="Dossier de base du jeu (garrysmod, cstrike...)")
        if p: self.game_root_var.set(normalize_game_root(p))

    def browse_output_root(self):
        p = filedialog.askdirectory(title="Dossier pour exporter les MDL de Sortie")
        if p: self.output_root_var.set(p)

    def browse_studiomdl(self):
        p = filedialog.askopenfilename(title="Exec de compilation", filetypes=[("studiomdl.exe", "studiomdl.exe")])
        if p: self.studiomdl_var.set(p)

    def browse_blender(self):
        p = filedialog.askopenfilename(title="Blender Executable", filetypes=[("blender.exe", "blender.exe")])
        if p: self.blender_var.set(p)

    def browse_crowbar(self):
        p = filedialog.askopenfilename(title="Crowbar Executable", filetypes=[("CrowbarCLI.exe", "CrowbarCLI*.exe")])
        if p: self.crowbar_var.set(p)

    def settings_file(self) -> Path:
        return APPDATA_ROOT / "lod_builder_settings.json"

    def save_settings(self):
        data = {
            "vmf": self.vmf_var.get(), "models_dir": self.models_dir_var.get(),
            "vmf_extra_paths": list(self.vmf_extra_paths),
            "game_root": self.game_root_var.get(),
            "output_root": self.output_root_var.get(), "studiomdl": self.studiomdl_var.get(),
            "blender": self.blender_var.get(), "crowbar CLI": self.crowbar_var.get(),
            "lod_levels": [(v[0].get(), v[1].get()) for v in self.lod_vars],
            "lang": self.lang,
            "theme_text_color": self.theme_text_color.get(),
            "theme_bg_color": self.theme_bg_color.get(),
            "generate_preview": self.generate_preview_var.get(),
            "show_preview": self.show_preview_var.get(),
        }
        self.settings_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.log(self.t("msg_settings_saved"))

    def load_settings(self):
        p = self.settings_file()
        if not p.exists(): return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.vmf_var.set(data.get("vmf", ""))
            self.models_dir_var.set(data.get("models_dir", ""))
            self.vmf_extra_paths = list(data.get("vmf_extra_paths", []))
            self.vmf_paths_listbox.delete(0, "end")
            for path in self.vmf_extra_paths:
                self.vmf_paths_listbox.insert("end", path)
            self.game_root_var.set(normalize_game_root(data.get("game_root", "")))
            self.output_root_var.set(data.get("output_root", ""))
            self.studiomdl_var.set(data.get("studiomdl", self.studiomdl_var.get()))
            self.blender_var.set(data.get("blender", self.blender_var.get()))
            self.crowbar_var.set(data.get("crowbar CLI", self.crowbar_var.get()))
            self.generate_preview_var.set(data.get("generate_preview", True))
            self.show_preview_var.set(data.get("show_preview", True))
            self._on_toggle_show_preview()
            for i, lvl in enumerate(data.get("lod_levels", [])):
                if i < len(self.lod_vars):
                    self.lod_vars[i][0].set(lvl[0])
                    self.lod_vars[i][1].set(lvl[1])
                elif i < self._MAX_LOD_LEVELS:
                    # Saved settings have more LOD levels than currently exist -> add them
                    self.lod_vars.append((tk.StringVar(value=str(lvl[0])),
                                         tk.StringVar(value=str(lvl[1]))))
            # Remove surplus levels if saved settings have fewer than default
            saved_count = len(data.get("lod_levels", []))
            if 2 <= saved_count < len(self.lod_vars):
                self.lod_vars = self.lod_vars[:saved_count]
            self._rebuild_lod_grid()
            saved_lang = data.get("lang", "en")
            if saved_lang != self.lang:
                self.lang = saved_lang
                self._lang_var.set("English" if self.lang == "en" else "Francais")
                self._refresh_ui_labels()
            saved_text_color = data.get("theme_text_color", self.theme_text_color.get())
            saved_bg_color = data.get("theme_bg_color", self.theme_bg_color.get())
            if saved_text_color != self.theme_text_color.get() or saved_bg_color != self.theme_bg_color.get():
                self.theme_text_color.set(saved_text_color)
                self.theme_bg_color.set(saved_bg_color)
                self._theme_text_swatch.itemconfigure(self._theme_text_oval, fill=saved_text_color)
                self._theme_bg_swatch.itemconfigure(self._theme_bg_oval, fill=saved_bg_color)
                self._apply_modern_theme()
            self._refresh_existing_lods_from_disk()
            self.log(self.t("msg_settings_loaded"))
        except Exception as e:
            self.log(f"{self.t('msg_settings_loaded')}: {e}")

    def log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        # Sauvegarde persistante (v1.13) : chaque ligne est ajoutee A LA SUITE de
        # %APPDATA%\LodGenerator\log.txt (jamais efface automatiquement, comme les logs
        # de build VBSP/VVIS/VRAD), pour pouvoir retrouver/exporter l'historique complet
        # en cas de souci, meme apres avoir ferme l'appli.
        try:
            with open(APPDATA_ROOT / "log.txt", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def set_status(self, text: str):
        self.status_var.set(text)

    def _poll_queues(self):
        while True:
            try: self.log(self.log_queue.get_nowait())
            except queue.Empty: break
        while True:
            try:
                status, key = self.status_queue.get_nowait()
                if key in self.entries:
                    self.entries[key].status = status
                    self.refresh_tree_item(key)
                    if self.current_selection == key and status == "done":
                        self.update_preview_for_entry(self.entries[key])
            except queue.Empty: break
        
        if getattr(self, 'preview_3d_window', None):
            try: self.preview_3d_window.render_frame()
            except Exception: pass
        
        self.root.after(16, self._poll_queues)

    def clear_list(self):
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        self.current_selection = None
        self.current_selections = []
        self.preview_label_var.set(self.t("no_selection_short"))
        self._set_details_text("")
        self.current_preview_paths = []
        self.current_preview_lod_index = 0
        self.preview_canvas.configure(image="", text=self.t("no_prop_selected"))
        self.preview_image_ref = None
        self.lod_scale.state(["disabled"])
        self.lod_scale.configure(from_=0, to=0)
        self.lod_value_label.configure(text="0")
        self.lod_all_btn.configure(state="disabled")
        self.lod_one_btn.configure(state="disabled")
        if self.preview_3d_window:
            self.preview_3d_window.close()
            self.preview_3d_window = None

    def apply_filters(self):
        search_text = self.search_var.get().lower().strip()
        status_filter = self.filter_status_var.get()
        classname_filter = self.filter_classname_var.get()
        try:
            count_min = self.filter_count_min_var.get()
        except Exception:
            count_min = 0
        try:
            count_max = self.filter_count_max_var.get()
        except Exception:
            count_max = 9999
        try:
            size_min_kb = self.filter_size_min_var.get()
        except Exception:
            size_min_kb = 0
        try:
            size_max_kb = self.filter_size_max_var.get()
        except Exception:
            size_max_kb = 999999
        sort_mode = self.filter_count_sort_var.get()
        sort_size_mode = self.filter_size_sort_var.get()

        # Map translated filter labels back to internal status keys
        L = self.lang
        status_map = {
            _T("filter_all",        L): None,
            _T("filter_ready",      L): "ready",
            _T("filter_processing", L): "processing",
            _T("filter_ok",         L): "done",
            _T("filter_error",      L): "error",
        }
        required_status = status_map.get(status_filter)  # None means no filter

        for item in self.tree.get_children():
            self.tree.detach(item)

        matching: List[PropEntry] = []
        for key, entry in self.entries.items():
            if search_text and search_text not in entry.original_model.lower():
                continue
            if required_status is not None and entry.status != required_status:
                continue
            filter_all_label = _T("filter_all", L)
            if classname_filter != filter_all_label and entry.classname != classname_filter:
                continue
            if not (count_min <= entry.usage_count <= count_max):
                continue
            # Filter by file size (convert bytes to KB)
            size_kb = entry.file_size / 1024
            if not (size_min_kb <= size_kb <= size_max_kb):
                continue
            matching.append(entry)

        sort_asc  = _T("sort_asc",  L)
        sort_desc = _T("sort_desc", L)
        # Sort by count if specified
        if sort_mode == sort_asc:
            matching.sort(key=lambda e: e.usage_count)
        elif sort_mode == sort_desc:
            matching.sort(key=lambda e: e.usage_count, reverse=True)
        # Sort by size if specified (takes precedence)
        if sort_size_mode == sort_asc:
            matching.sort(key=lambda e: e.file_size)
        elif sort_size_mode == sort_desc:
            matching.sort(key=lambda e: e.file_size, reverse=True)

        for entry in matching:
            self.tree.reattach(entry.original_model, "", "end")

    def _update_classname_filter_options(self):
        """Met à jour la liste des types/classnames disponibles dans le filtre."""
        classnames = set()
        for entry in self.entries.values():
            if entry.classname:
                classnames.add(entry.classname)

        options = [self.t("filter_all")] + sorted(list(classnames))
        self.classname_filter_combo['values'] = options
        if self.filter_classname_var.get() not in options:
            self.filter_classname_var.set(self.t("filter_all"))

    def refresh_tree_item(self, model_key):
        entry = self.entries.get(model_key)
        if entry is None or not self.tree.exists(model_key):
            return
        self.tree.item(model_key, values=(
            entry.original_model,
            entry.classname or "",
            entry.usage_count,
            self._status_label(entry.status)
        ), tags=(entry.status,))

    def _prefetch_sizes_async(self):
        """
        FIX '0 Ko' avant traitement (mode scan VMF) : la taille d'un prop reference par un VMF
        n'etait connue qu'une fois le modele resolu sur disque OU extrait d'un VPK, ce qui
        n'arrivait qu'au moment du TRAITEMENT. On tente ici une resolution rapide (sans
        extraction VPK, trop lente pour un simple scan) en parallele sur plusieurs threads,
        contre le dossier de jeu ET les dossiers de "Custom VMF models Paths", afin d'afficher
        les tailles reelles des props deja presents sur disque immediatement apres l'analyse du
        VMF, et de pouvoir filtrer/trier par taille avant meme de lancer un traitement.
        Les modeles uniquement presents dans un VPK resteront a 0 Ko jusqu'au traitement
        (l'extraction elle-meme reste trop couteuse pour etre faite au simple scan).
        """
        game_root = self.game_root_var.get().strip()
        extra_dirs = list(self.vmf_extra_paths)
        entries_snapshot = [e for e in self.entries.values() if e.file_size == 0]
        if not entries_snapshot or not game_root:
            return

        def _resolve_one(entry):
            try:
                path = resolve_source_model_path_multi(game_root, extra_dirs, entry.original_model)
                if Path(path).exists():
                    return entry, get_model_display_size(path), path
            except Exception:
                pass
            return entry, 0, None

        def _worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self._cpu_max_workers)) as executor:
                results = list(executor.map(_resolve_one, entries_snapshot))
            self.root.after(0, lambda: self._apply_prefetched_sizes(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_prefetched_sizes(self, results):
        for entry, size, path in results:
            if size:
                entry.file_size = size
                if path:
                    entry.resolved_source_path = path
                if entry.original_model in self.entries:
                    self.refresh_tree_item(entry.original_model)

    def parse_vmf(self):
        vmf = self.vmf_var.get().strip()
        if not vmf or not Path(vmf).exists():
            messagebox.showerror(APP_NAME, self.t("msg_vmf_invalid"))
            return
        self.set_status(self.t("msg_analysis_running"))
        self.progress_label.configure(text=self.t("msg_analysis_running"))
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)
        self.lod_all_btn.configure(state="disabled")

        def _worker():
            try:
                parsed = extract_models_from_vmf(vmf)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
                self.root.after(0, _finish_error)
                return
            self.root.after(0, lambda: _populate(parsed))

        def _finish_error():
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_label.configure(text="")
            self.set_status(self.t("ready"))

        def _populate(parsed):
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_var.set(0)
            self.progress_label.configure(text="")
            self.clear_list()
            for entry in parsed:
                entry.status = "ready"
                self.entries[entry.original_model] = entry
                self.tree.insert("", "end", iid=entry.original_model,
                               values=(entry.original_model, entry.classname or "", entry.usage_count,
                                       format_file_size(entry.file_size), self._status_label("ready")),
                               tags=("ready",))
            self._refresh_existing_lods_from_disk()
            self._prefetch_sizes_async()
            self._update_classname_filter_options()
            self.set_status(self.t("msg_detected").format(n=len(parsed)))
            self.lod_all_btn.configure(state="normal" if parsed else "disabled")
            self.log(self.t("msg_extracted").format(n=len(parsed)))

        threading.Thread(target=_worker, daemon=True).start()

    def parse_folder(self):
        folder = self.models_dir_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror(APP_NAME, self.t("msg_folder_invalid"))
            return
        self.set_status(self.t("msg_analysis_folder"))
        self.progress_label.configure(text=self.t("msg_analysis_folder"))
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)
        self.lod_all_btn.configure(state="disabled")

        def _worker():
            try:
                parsed = extract_models_from_folder(folder)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
                self.root.after(0, _finish_error)
                return
            self.root.after(0, lambda: _populate(parsed))

        def _finish_error():
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_label.configure(text="")
            self.set_status(self.t("ready"))

        def _populate(parsed):
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_var.set(0)
            self.progress_label.configure(text="")
            self.clear_list()
            for entry in parsed:
                entry.status = "ready"
                if entry.original_model in self.entries:
                    self.log(f"[WARN] Duplicate model path found, keeping the first occurrence "
                             f"and skipping: {entry.original_model} ({entry.resolved_source_path})")
                    continue
                self.entries[entry.original_model] = entry
                self.tree.insert("", "end", iid=entry.original_model,
                               values=(entry.original_model, entry.classname or "",
                                       entry.usage_count, format_file_size(entry.file_size),
                                       self._status_label("ready")),
                               tags=("ready",))
            self._refresh_existing_lods_from_disk()
            self._update_classname_filter_options()
            n = len(parsed)
            self.set_status(self.t("msg_detected").format(n=n))
            self.lod_all_btn.configure(state="normal" if parsed else "disabled")
            self.log(self.t("msg_folder_done").format(n=n))

            # Mode SMD only (v1.13, v2) : si la case "Inclure les SMD" est cochee, on
            # scanne AUSSI ce meme dossier pour des .smd, ajoutes a la liste comme des
            # entrees SMD only (traitees avec les memes boutons TOUS/SELECTIONNES).
            if self.include_smd_var.get():
                smds = [str(p) for p in Path(folder).rglob("*.smd")]
                if smds:
                    self.add_smd_entries_to_list(smds)

        threading.Thread(target=_worker, daemon=True).start()

    def refresh_tree_item(self, key: str):
        if key not in self.entries or not self.tree.exists(key): return
        entry = self.entries[key]
        self.tree.item(key, values=(entry.original_model, entry.classname or "",
                                   entry.usage_count, format_file_size(entry.file_size),
                                   self._status_label(entry.status)))
        self.tree.item(key, tags=(entry.status,))

    def _status_label(self, status: str) -> str:
        return _T({"pending": "pending", "ready": "ready",
                   "processing": "processing", "done": "done",
                   "error": "error"}.get(status, status), self.lang)

    def _scan_existing_lods_for_entry(self, entry: PropEntry, game_root: str, output_root: str):
        """
        Partie PURE DISQUE (thread-safe, ne touche ni Tk ni self.entries) : recherche les LOD/
        previews deja generes pour ce prop. Separee de l'application du resultat pour pouvoir
        etre lancee en parallele sur plusieurs threads (voir _refresh_existing_lods_from_disk).
        """
        paths = discover_existing_lod_smds(entry, game_root, output_root)
        preview_paths = discover_existing_previews(entry, output_root)

        output_path = resolve_output_model_path(output_root, entry.original_model) if output_root else entry.output_path
        output_exists = False
        try:
            if output_path and Path(output_path).exists():
                output_exists = True
            else:
                rel = Path(source_relative_subpath(entry.original_model))
                out_dir = Path(output_root) / rel.parent if output_root else None
                if out_dir and out_dir.exists():
                    stem = rel.stem.lower()
                    for cand in out_dir.glob("*.mdl"):
                        nm = cand.name.lower()
                        if nm == f"{stem}.mdl" or nm.startswith(f"{stem}_lod"):
                            output_exists = True
                            break
        except Exception:
            output_exists = False
        return output_path, paths, preview_paths, output_exists

    def _apply_existing_lods_result(self, entry: PropEntry, result) -> List[str]:
        """Partie qui mute entry/le Treeview : DOIT s'executer sur le thread principal Tk."""
        output_path, paths, preview_paths, output_exists = result
        if output_path:
            entry.output_path = output_path
        if paths:
            entry.lod_model_paths = paths
            entry.lod_count = len(paths)
        if preview_paths:
            entry.preview_frames = preview_paths
            self._preload_previews_async(preview_paths)
        if paths or preview_paths or output_exists:
            if entry.status not in ("processing", "error"):
                entry.status = "done"
            if entry.original_model in self.entries:
                self.refresh_tree_item(entry.original_model)
        return paths

    def _populate_existing_lods_for_entry(self, entry: PropEntry) -> List[str]:
        """Version synchrone (1 seul prop) : utilisee par ex. a la selection d'un prop / avant
        d'ouvrir l'apercu 3D, la ou le multithreading n'apporterait rien (un seul disque a lire)."""
        game_root = self.game_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        result = self._scan_existing_lods_for_entry(entry, game_root, output_root)
        return self._apply_existing_lods_result(entry, result)

    def _refresh_existing_lods_from_disk(self):
        """
        Recherche sur disque les LOD/preview deja generes pour CHAQUE prop de la liste.
        FIX lenteur chargement VMF/dossier avec beaucoup de props : cette recherche etait
        executee sequentiellement (prop par prop) sur le thread principal. Le scan disque
        (glob/exists) est maintenant reparti sur plusieurs threads (ThreadPoolExecutor),
        la ou c'est justement l'operation qui domine le temps de chargement d'une grosse
        liste de props (105 detectes dans une map = 105 lectures disque sequentielles avant).
        """
        game_root = self.game_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        entries_snapshot = list(self.entries.values())
        if not entries_snapshot:
            return

        def _scan_one(entry):
            return entry, self._scan_existing_lods_for_entry(entry, game_root, output_root)

        def _worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self._cpu_max_workers)) as executor:
                results = list(executor.map(_scan_one, entries_snapshot))
            self.root.after(0, lambda: self._apply_existing_lods_results_bulk(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_existing_lods_results_bulk(self, results):
        for entry, result in results:
            self._apply_existing_lods_result(entry, result)
        # Precharge (decode) en arriere-plan toutes les previews maintenant connues, sans
        # bloquer l'UI (voir _preload_previews_async) : fix du lag ressenti au clic sur
        # chaque prop, tout en gardant l'appli utilisable pendant le chargement.
        all_preview_paths = [p for entry, _ in results for p in entry.preview_frames]
        self._preload_previews_async(all_preview_paths)

    def on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self.current_selection = None
            self.current_selections = []
            self.lod_one_btn.configure(state="disabled")
            return
        self.current_selections = list(sel)
        # Aperçu sur le dernier item de la sélection
        focused = self.tree.focus() or sel[-1]
        self.current_selection = focused
        entry = self.entries.get(focused)
        if entry:
            self._populate_existing_lods_for_entry(entry)
            self._show_entry_details(entry)
            self.update_preview_for_entry(entry)
        count = len(sel)
        label = f"CE PROP ({count})" if count > 1 else "CE PROP"
        self.lod_one_btn.configure(state="normal", text=label)

    def _show_entry_details(self, entry: PropEntry):
        text = [
            f"Model: {entry.original_model}",
            f"Type: {entry.classname or 'N/A'}",
            f"Count: {entry.usage_count}",
            f"Status: {entry.status}",
            f"Source: {entry.resolved_source_path or 'Not found'}",
            f"Output: {entry.output_path or 'Not set'}",
        ]
        if entry.error: text.extend(["", "=== ERROR ===", entry.error])
        self._set_details_text("\n".join(text))

    def _set_details_text(self, value: str):
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", value)
        self.details_text.configure(state="disabled")

    def _enable_lod_slider(self, count: int):
        if count <= 1:
            self.lod_scale.state(["disabled"])
            self.lod_scale.configure(from_=0, to=0)
            self.lod_value_label.configure(text="0")
        else:
            self.lod_scale.state(["!disabled"])
            self.lod_scale.configure(from_=0, to=count-1)
            self.lod_scale.set(0)
            self.lod_value_label.configure(text="0")

    def on_lod_slider(self, value):
        if not self.current_preview_paths: return
        try:
            idx = max(0, min(int(float(value)), len(self.current_preview_paths)-1))
            self.current_preview_lod_index = idx
            self.lod_value_label.configure(text=str(idx))
            if self.current_preview_paths:
                self.preview_label_var.set(f"{self.preview_label_var.get().split(' [LOD ')[0]} [LOD {idx}]")
                if self.current_selection and self.current_selection in self.entries:
                    entry = self.entries[self.current_selection]
                    if entry.preview_frames and idx < len(entry.preview_frames):
                        self.show_preview_image(entry.preview_frames[idx])
        except: pass

    def update_preview_for_entry(self, entry: PropEntry):
        if not entry.lod_model_paths and not entry.preview_frames:
            self._populate_existing_lods_for_entry(entry)
            
        if entry.preview_frames:
            self.current_preview_paths = entry.preview_frames
            self.current_preview_lod_index = max(0, min(self.current_preview_lod_index, len(entry.preview_frames) - 1))
            self._enable_lod_slider(len(entry.preview_frames))
            self.preview_label_var.set(f"{Path(entry.original_model).name} [LOD {self.current_preview_lod_index}]")
            self.show_preview_image(entry.preview_frames[self.current_preview_lod_index])
        else:
            self._last_preview_path = None
            self.preview_canvas.configure(image="", text=self._preview_placeholder_text(entry))
            self.preview_image_ref = None
            self.preview_label_var.set(Path(entry.original_model).name)
            self.lod_scale.state(["disabled"])
            if entry.lod_model_paths:
                self.current_preview_paths = entry.lod_model_paths

    def _preview_placeholder_text(self, entry: PropEntry) -> str:
        return self.t("preview_no_lod").format(name=Path(entry.original_model).name)

    def _on_preview_box_resize(self, _event=None):
        """Debounce : on ne re-render qu'une fois le redimensionnement stabilise (~120ms)."""
        if self._preview_resize_job is not None:
            try: self.root.after_cancel(self._preview_resize_job)
            except Exception: pass
        self._preview_resize_job = self.root.after(120, self._reflow_preview_image)

    def _reflow_preview_image(self):
        self._preview_resize_job = None
        if self._last_preview_path:
            self.show_preview_image(self._last_preview_path)

    def show_preview_image(self, path: str):
        """
        Affiche l'image de preview en la centrant et en la redimensionnant pour tenir dans
        l'espace reellement disponible (fix deformation UI 1080p, voir _on_preview_box_resize).

        FIX lag au clic : le DECODAGE disque (Image.open + convert, la partie couteuse) est
        mis en cache (self._preview_pil_cache) et rempli en arriere-plan par plusieurs threads
        (voir _preload_previews_async), donc au clic on ne fait plus que redimensionner une
        image DEJA en memoire (rapide). Si l'image n'est pas encore en cache (chargement pas
        termine), on affiche un texte "Chargement..." et on programme un rafraichissement
        automatique des que le decodage en arriere-plan aura fini pour CE prop precis.
        """
        self._last_preview_path = path
        if not path or not PIL_AVAILABLE:
            self.preview_canvas.configure(image="", text=self.t("preview_unavailable"))
            self.preview_image_ref = None
            return
        try:
            box_w = self.preview_box.winfo_width()
            box_h = self.preview_box.winfo_height()
            target_w = max(200, box_w - 8) if box_w > 20 else 480
            target_h = max(150, box_h - 8) if box_h > 20 else 360
            target_w = min(target_w, 1000)
            target_h = min(target_h, 800)

            img = self._preview_pil_cache.get(path)
            if img is None:
                if not Path(path).exists():
                    self.preview_canvas.configure(image="", text=self.t("preview_unavailable"))
                    self.preview_image_ref = None
                    return
                # Pas encore decode par le pool en arriere-plan : on l'affiche comme "en
                # chargement" et on le fait decoder tout de suite en tache de fond (thread
                # dedie a CE fichier), sans bloquer l'UI.
                self.preview_canvas.configure(image="", text=self.t("preview_loading"))
                self.preview_image_ref = None
                self._load_single_preview_async(path)
                return

            img_w, img_h = img.size
            scale = min(target_w / img_w, target_h / img_h)
            new_w = max(1, int(img_w * scale))
            new_h = max(1, int(img_h * scale))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            bg = Image.new('RGB', (target_w, target_h), color=(34, 34, 34))
            offset_x = (target_w - new_w) // 2
            offset_y = (target_h - new_h) // 2
            bg.paste(resized, (offset_x, offset_y))

            photo = ImageTk.PhotoImage(bg)
            self.preview_image_ref = photo
            self.preview_canvas.configure(image=photo, text="")
        except Exception as e:
            self.preview_canvas.configure(image="", text=self.t("preview_render_error").format(e=e))
            self.preview_image_ref = None

    def _load_single_preview_async(self, path: str):
        """Decode UN fichier de preview en arriere-plan (hors thread principal) et rafraichit
        l'affichage si l'utilisateur regarde toujours ce meme prop une fois pret."""
        if path in self._preview_loading:
            return
        self._preview_loading.add(path)

        def _worker():
            img = None
            try:
                img = Image.open(path).convert("RGB")
            except Exception:
                pass
            self.root.after(0, lambda: self._on_preview_decoded(path, img))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_preview_decoded(self, path: str, img):
        self._preview_loading.discard(path)
        if img is not None:
            self._preview_pil_cache[path] = img
        # Ne rafraichit que si l'utilisateur regarde toujours CE prop (evite d'ecraser
        # une selection plus recente avec un resultat de chargement en retard).
        if self._last_preview_path == path:
            self.show_preview_image(path)

    def _preload_previews_async(self, paths: List[str]):
        """
        Precharge (decode) EN ARRIERE-PLAN, sur plusieurs threads, toutes les previews connues
        - remplace l'ancien preload sequentiel qui gelait l'appli. L'utilisateur peut continuer
        a cliquer/selectionner des props pendant que ca charge : si un prop pas encore decode
        est selectionne, show_preview_image affiche "Chargement..." puis se met a jour des que
        ce fichier precis est pret (voir _load_single_preview_async / _on_preview_decoded).
        """
        if not PIL_AVAILABLE:
            return
        todo = [p for p in dict.fromkeys(paths) if p and p not in self._preview_pil_cache
                and p not in self._preview_loading]
        if not todo:
            return
        self._preview_loading.update(todo)

        def _decode_one(p):
            try:
                return p, Image.open(p).convert("RGB")
            except Exception:
                return p, None

        def _worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self._cpu_max_workers)) as executor:
                for p, img in executor.map(_decode_one, todo):
                    self.root.after(0, lambda p=p, img=img: self._on_preview_decoded(p, img))

        threading.Thread(target=_worker, daemon=True).start()

    def scan_all_vpks(self):
        gmod = self.game_root_var.get().strip()
        if not gmod or not Path(gmod).exists():
            messagebox.showerror(APP_NAME, self.t("msg_paths_invalid"))
            return
        clear_vpk_cache()
        vpk_files = find_all_vpk_files(gmod)
        self.set_status(self.t("ready"))
        messagebox.showinfo(APP_NAME, self.t("msg_vpk_scanned").format(n=len(vpk_files)))

    def _pick_theme_color(self, which: str):
        """Ouvre le selecteur de couleur systeme pour le rond 'texte/accent' ou 'fond'.
        Met a jour uniquement l'apercu du rond : la palette n'est reappliquee (et sauvegardee)
        qu'au clic sur 'Appliquer' (voir apply_theme_colors)."""
        var = self.theme_text_color if which == "text" else self.theme_bg_color
        _, hex_color = colorchooser.askcolor(color=var.get(), title=self.t("lbl_theme"))
        if not hex_color:
            return
        var.set(hex_color)
        oval = self._theme_text_oval if which == "text" else self._theme_bg_oval
        swatch = self._theme_text_swatch if which == "text" else self._theme_bg_swatch
        swatch.itemconfigure(oval, fill=hex_color)

    def apply_theme_colors(self):
        """Reapplique le theme avec les couleurs actuellement choisies dans les ronds, et
        sauvegarde automatiquement dans le fichier de parametres (%APPDATA%\\LodGenerator\\)."""
        self._apply_modern_theme()
        self.save_settings()

    def _build_log_window(self):
        """
        Log (v1.13, v2) : plus jamais visible dans la fenetre principale par defaut --
        uniquement une fenetre EXTERNE (creee ici, cachee au demarrage), affichee
        seulement via le menu Affichage -> Voir le Log. Le bouton d'export vit
        UNIQUEMENT dans cette fenetre (plus dans la fenetre principale).
        """
        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - Log")
        win.geometry("760x380")
        win.withdraw()  # cachee par defaut, jamais visible au demarrage
        win.protocol("WM_DELETE_WINDOW", win.withdraw)  # "fermer" = juste masquer

        top_row = ttk.Frame(win)
        top_row.pack(fill="x", padx=4, pady=(4, 0))
        self._btn_export_log = ttk.Button(top_row, text=self.t("btn_export_log"),
                                          command=self._on_export_log, width=16)
        self._btn_export_log.pack(side="right")

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=4, pady=4)
        self.log_text = tk.Text(body, wrap="word", font=('Consolas', 9), state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(body, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self._log_window = win

    def show_log_window(self):
        """Menu Affichage/Outils/Options -> 'Voir le Log' : seul moyen d'afficher la
        fenetre de log, qui n'apparait plus jamais dans la fenetre principale."""
        self._log_window.deiconify()
        self._log_window.lift()

    def _on_export_log(self):
        """Bouton 'Exporter le log' (v1.13) : copie directement %APPDATA%\\LodGenerator\\log.txt
        (l'historique complet, toutes sessions confondues) vers l'endroit choisi par
        l'utilisateur -- pratique pour me le transmettre en cas de souci."""
        src = APPDATA_ROOT / "log.txt"
        if not src.exists():
            messagebox.showinfo(APP_NAME, self.t("msg_no_log_yet"))
            return
        dest = filedialog.asksaveasfilename(defaultextension=".txt",
                                            initialfile="log_export.txt",
                                            filetypes=[("Text files", "*.txt")])
        if dest:
            try:
                shutil.copy2(src, dest)
                self.log(f"[LOG] Exported to: {dest}")
            except Exception as e:
                messagebox.showerror(APP_NAME, str(e))

    def _on_toggle_show_preview(self):
        """Option 'Afficher les previews des models selectionnes' (v1.13) : masque/affiche
        le panneau d'apercu 2D pour gagner de la place quand on n'en a pas besoin. N'affecte
        pas la generation des previews elle-meme (voir generate_preview_var / _chk_generate_preview)."""
        if self.show_preview_var.get():
            self._preview_label_hdr.pack(anchor="w")
            self.preview_box.pack(fill="both", expand=True, pady=(2, 2))
            self.preview_filename_label.pack(fill="x", pady=1)
        else:
            self._preview_label_hdr.pack_forget()
            self.preview_box.pack_forget()
            self.preview_filename_label.pack_forget()

    def clear_vpk_cache_ui(self):
        if messagebox.askyesno(APP_NAME, self.t("msg_clear_vpk")):
            clear_vpk_cache()
            self.log(self.t("msg_vpk_cleared"))

    def open_3d_viewer(self):
        if not self.current_selection or self.current_selection not in self.entries:
            messagebox.showinfo(APP_NAME, self.t("msg_no_selection_generic"))
            return
        entry = self.entries[self.current_selection]
        self._populate_existing_lods_for_entry(entry)
        if not entry.lod_model_paths:
            messagebox.showinfo(APP_NAME, self.t("msg_no_lod_generic"))
            return
        if not (PYGLET_AVAILABLE or GLUT_AVAILABLE):
            messagebox.showerror(APP_NAME, self.t("msg_no_backend_generic"))
            return
        if self.preview_3d_window:
            self.preview_3d_window.close()
        messagebox.showinfo(self.t("help_3d_title"), self.t("help_3d_text"))
        self.preview_3d_window = ModelPreviewWindow(self.root, entry.lod_model_paths, self.current_preview_lod_index)

    def open_output_folder(self):
        out = self.output_root_var.get().strip()
        if out and Path(out).exists():
            if platform.system() == "Windows": os.startfile(out)
            else:
                opener = "open" if platform.system() == "Darwin" else "xdg-open"
                subprocess.run([opener, out])

    def lod_all(self):
        """Process all props currently visible in the list, in the displayed order."""
        keys = self.tree.get_children()
        if not keys:
            return
        jobs = [self.entries[k] for k in keys if k in self.entries]
        if jobs:
            self._start_processing(jobs)

    def lod_selected(self):
        """
        Traite tous les props sélectionnés.
        - Si aucun batch n'est actif : démarre un nouveau batch avec la sélection.
        - Si un batch est déjà actif : enfile les props sélectionnés dans
          `_pending_queue` pour qu'ils soient traités par le pool en cours.
        """
        keys = [k for k in self.current_selections if k in self.entries]
        if not keys:
            return
        jobs = [self.entries[k] for k in keys]
        if self.processing:
            # Ajouter à la file d'attente du batch actif
            queued = 0
            for entry in jobs:
                if entry.status not in ("processing",):
                    self._pending_queue.put(entry)
                    queued += 1
            if queued:
                self.log(self.t("msg_queued").format(n=queued))
        else:
            self._start_processing(jobs)

    def _start_processing(self, jobs: List[PropEntry]):
        game_root = normalize_game_root(self.game_root_var.get().strip())
        output_root = self.output_root_var.get().strip()
        vmf = self.vmf_var.get().strip()
        models_dir = self.models_dir_var.get().strip()
        blender = self.blender_var.get().strip()
        studiomdl = self.studiomdl_var.get().strip()
        crowbar = self.crowbar_var.get().strip()
        physics_mode = self.physics_mode_var.get().strip() or "keep"

        # Mode SMD only (v1.13, v2) : un lot entierement compose d'entrees SMD only n'a
        # besoin ni du Game Root, ni de Crowbar/studiomdl (pas de decompilation/recompilation) --
        # seulement de blender.exe et d'un dossier de sortie.
        all_smd_only = jobs and all(e.is_smd_only for e in jobs)
        if all_smd_only:
            if not all([output_root, blender]):
                messagebox.showerror(APP_NAME, self.t("msg_paths_invalid"))
                return
        else:
            # VMF is optional when using folder-mode; at least one source must be present.
            source_ok = (vmf and Path(vmf).exists()) or (models_dir and Path(models_dir).is_dir())
            if not all([source_ok, game_root, Path(game_root).exists(), output_root, blender, studiomdl, crowbar]):
                messagebox.showerror(APP_NAME, self.t("msg_paths_invalid"))
                return

        Path(output_root).mkdir(parents=True, exist_ok=True)
        blender_script = APPDATA_ROOT / "blender_worker.py"
        blender_script.write_text(self._get_blender_worker_text(), encoding="utf-8")

        # Réinitialiser la file d'attente et l'événement stop
        self._pending_queue = queue.Queue()
        self._stop_event.clear()
        for entry in jobs:
            self._pending_queue.put(entry)

        self.processing = True
        self.stop_btn.configure(state="normal")
        self._set_controls_state(False)
        current_lods = self.get_current_lod_levels()
        threading.Thread(
            target=self._process_jobs_thread,
            args=(game_root, output_root, blender, studiomdl, crowbar, current_lods, physics_mode),
            daemon=True
        ).start()

    def _set_controls_state(self, enabled: bool):
        s = "normal" if enabled else "disabled"
        self.lod_all_btn.configure(state=s if self.entries else "disabled")
        has_sel = bool(self.current_selections)
        self.lod_one_btn.configure(state=s if has_sel else "disabled")

    def _request_stop(self):
        if self.processing:
            self._stop_event.set()
            self.stop_btn.configure(state="disabled")
            self.log(self.t("msg_stop_requested"))

    def _process_jobs_thread(self, game_root: str, output_root: str,
                           blender_path: str, studiomdl_path: str, crowbar_path: str, lod_levels: List[Tuple[int, float]],
                           physics_mode: str = "keep"):
        lock = threading.Lock()
        done_count = 0
        total_submitted = 0

        self.root.after(0, lambda: self.progress_var.set(0))
        self.root.after(0, lambda: self.progress_bar.configure(mode="determinate", maximum=1))
        self.root.after(0, lambda: self.progress_label.configure(text="0 / ?"))

        try:
            self.batch_start_time = time.time()
            self.job_times.clear()
            self.log_queue.put(f"[TIMER] Starting batch (dynamic queue)")

            n_workers = max(1, min(self.parallel_jobs_var.get(), self._cpu_max_workers))
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=n_workers)
            active_futures: set = set()

            try:
                while True:
                    # Stop demandé ?
                    if self._stop_event.is_set():
                        self.log_queue.put("[STOP] Stop requested - cancelling remaining jobs.")
                        # Annuler les futures en attente (pas encore démarrés)
                        for f in list(active_futures):
                            f.cancel()
                        break

                    # Dépiler un job si disponible
                    try:
                        entry = self._pending_queue.get_nowait()
                    except queue.Empty:
                        # Attendre que des futures en cours terminent ou qu'un nouveau job arrive
                        if not active_futures:
                            break  # Plus rien à faire
                        done, active_futures = concurrent.futures.wait(
                            active_futures,
                            timeout=0.2,
                            return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        for f in done:
                            try: f.result()
                            except Exception as exc:
                                self.log_queue.put(f"[UNEXPECTED ERROR] {exc}")
                        continue

                    total_submitted += 1
                    self.root.after(0, lambda t=total_submitted: (
                        self.progress_bar.configure(maximum=max(t, done_count + 1)),
                    ))

                    def _run_one(e=entry):
                        nonlocal done_count
                        self._process_single_job(e, game_root, output_root, blender_path,
                                                 studiomdl_path, crowbar_path, lod_levels, physics_mode)
                        with lock:
                            done_count += 1
                            dc = done_count
                        if self.job_times:
                            avg_time = sum(t for _, t in self.job_times) / len(self.job_times)
                            remaining = avg_time * max(0, self._pending_queue.qsize() + len(active_futures) - 1)
                            eta = f" ~{remaining:.0f}s" if remaining > 0 else ""
                            lbl = f"{dc} / {total_submitted}{eta}"
                        else:
                            lbl = f"{dc} / {total_submitted}"
                        self.root.after(0, lambda v=dc, lb=lbl: (
                            self.progress_var.set(v),
                            self.progress_label.configure(text=lb)
                        ))

                    fut = executor.submit(_run_one)
                    active_futures.add(fut)

                    # Nettoyer les futures terminées sans bloquer
                    done_now = {f for f in active_futures if f.done()}
                    for f in done_now:
                        active_futures.discard(f)
                        try: f.result()
                        except Exception as exc:
                            self.log_queue.put(f"[UNEXPECTED ERROR] {exc}")

                # Attendre la fin de tous les futures encore actifs
                if active_futures:
                    for f in concurrent.futures.as_completed(active_futures):
                        try: f.result()
                        except Exception as exc:
                            self.log_queue.put(f"[UNEXPECTED ERROR] {exc}")
            finally:
                executor.shutdown(wait=False)

        finally:
            self.processing = False
            self.root.after(0, lambda: self.stop_btn.configure(state="disabled"))
            self.root.after(0, lambda: self._set_controls_state(True))
            self.root.after(0, lambda: self.progress_label.configure(text=""))

            if self.batch_start_time:
                self.total_batch_time = time.time() - self.batch_start_time
                if self.job_times:
                    avg = self.total_batch_time / len(self.job_times)
                    stopped = " (stopped)" if self._stop_event.is_set() else ""
                    self.log_queue.put(f"[TIMER] DONE{stopped} in {self.total_batch_time:.1f}s "
                                       f"(avg {avg:.1f}s/prop, {n_workers} thread(s))")
                else:
                    self.log_queue.put(f"[TIMER] Processing finished in {self.total_batch_time:.1f}s")
            else:
                self.log_queue.put("Processing finished")

    def _process_single_job(self, entry: PropEntry, game_root: str, output_root: str,
                           blender_path: str, studiomdl_path: str, crowbar_path: str, lod_levels: List[Tuple[int, float]],
                           physics_mode: str = "keep"):
        """
        Point d'entree public pour le traitement d'un prop : execute _process_single_job_attempt
        avec jusqu'a 3 TENTATIVES (soit 2 retries) en cas d'echec. Certaines erreurs (Blender/
        Crowbar/studiomdl qui plantent ponctuellement, verrou de fichier temporaire, extraction
        VPK qui echoue une fois sur un gros paquet...) sont transitoires et disparaissent en
        recommençant simplement le job, d'ou l'interet d'un retry automatique plutot que de
        marquer le prop en erreur des le premier echec.

        FIX mode SMD only (v1.13, v2) : integre desormais dans la MEME liste/traitement par
        lot que les .mdl, au lieu d'un traitement immediat isole hors de la liste -- les
        entrees marquees is_smd_only sont routees vers _process_smd_only_job (pas de
        decompilation/recompilation), le reste suit le pipeline complet habituel.
        """
        if entry.is_smd_only:
            self._process_smd_only_job(entry, output_root, blender_path, lod_levels)
            return
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                self._process_single_job_attempt(
                    entry, game_root, output_root, blender_path, studiomdl_path,
                    crowbar_path, lod_levels, physics_mode, attempt, max_attempts
                )
                return  # succes, pas besoin de retry
            except Exception:
                if attempt < max_attempts:
                    continue  # deja loggue par _process_single_job_attempt, on retente
                return  # dernier echec deja loggue et marque "error" par _process_single_job_attempt

    def _process_smd_only_job(self, entry: PropEntry, output_root: str, blender_path: str,
                             lod_levels: List[Tuple[int, float]]):
        """Traitement d'un prop en mode SMD only, integre a la liste/au statut comme les .mdl."""
        key = entry.original_model
        job_start = time.time()
        self.status_queue.put(("processing", key))
        self.log_queue.put(f"[SMD-ONLY] {entry.original_model}")
        try:
            source_path = entry.resolved_source_path or entry.original_model
            generated = process_smd_only(source_path, output_root, lod_levels, blender_path,
                                         log_fn=self.log_queue.put)
            entry.lod_model_paths = generated
            entry.lod_count = len(generated)
            job_elapsed = time.time() - job_start
            self.job_times.append((entry.original_model, job_elapsed))
            if generated:
                self.status_queue.put(("done", key))
                self.log_queue.put(f"[SMD-ONLY] {Path(source_path).name}: {len(generated)} "
                                   f"LOD file(s) generated in {job_elapsed:.1f}s.")
            else:
                entry.error = "No LOD file was produced (see log above)."
                self.status_queue.put(("error", key))
        except Exception as e:
            entry.error = str(e)
            job_elapsed = time.time() - job_start
            self.job_times.append((entry.original_model, job_elapsed))
            self.status_queue.put(("error", key))
            self.log_queue.put(f"[SMD-ONLY][ERROR] {entry.original_model}: {e}")

    def _process_single_job_attempt(self, entry: PropEntry, game_root: str, output_root: str,
                           blender_path: str, studiomdl_path: str, crowbar_path: str, lod_levels: List[Tuple[int, float]],
                           physics_mode: str, attempt: int, max_attempts: int):
        key = entry.original_model
        job_start = time.time()
        try:
            self.current_job_start_time = job_start
            self.status_queue.put(("processing", key))
            if attempt == 1:
                self.log_queue.put(f"[PROCESSING] {entry.original_model}")
            else:
                self.log_queue.put(f"[RETRY] Attempt {attempt}/{max_attempts} for {entry.original_model}")

            extra_model_dirs = self._extra_model_search_dirs()
            # FIX (v1.13) : si on connait deja le chemin EXACT du fichier sur le disque
            # (scan de dossier local, ou tentative precedente reussie), on lui fait
            # confiance directement plutot que de le re-deriver via le Game Root / Source
            # Game Folder -> corrige le besoin de configurer un chemin "Custom VMF models
            # Paths" pour un simple scan de dossier local, et le Game Root redevient
            # optionnel dans ce mode (il ne sert plus qu'a la resolution VMF/VPK).
            if entry.resolved_source_path and Path(entry.resolved_source_path).exists():
                source_model_path = entry.resolved_source_path
            else:
                source_model_path = resolve_source_model_path_multi(game_root, extra_model_dirs, entry.original_model)
                entry.resolved_source_path = source_model_path

                if not Path(source_model_path).exists():
                    self.log_queue.put(f"[VPK] Extracting {entry.original_model}...")
                    extracted = extract_model_from_all_vpks(game_root, entry.original_model)
                    if extracted:
                        source_model_path = str(extracted)
                        entry.resolved_source_path = source_model_path
                        self.log_queue.put(f"[VPK] Extracted to: {source_model_path}")
                    else:
                        raise FileNotFoundError(f"Model not found: {entry.original_model}")

            # FIX taille "0 Ko" : le fichier n'est connu (donc mesurable) qu'a partir d'ici,
            # une fois resolu sur disque ou extrait d'un VPK. On met a jour la taille reelle
            # et on rafraichit immediatement la ligne du tableau pour que la colonne "Taille"
            # ne reste plus jamais bloquee a 0.
            try:
                entry.file_size = get_model_display_size(source_model_path)
                self.status_queue.put(("processing", key))
            except Exception:
                pass

            # === Sauvegarde de la physique D'ORIGINE (jamais modifiée) ===
            # Fait AVANT toute décompilation Crowbar : le fichier .phy source
            # (celui compilé par l'auteur d'origine) est copié de côté tel
            # quel. Le problème de décalage/orientation de la collision vient
            # de la façon dont studiomdl recompile une physique décompilée
            # (limitation côté Source/Crowbar, pas de ce script) : la seule
            # solution fiable est de ne jamais faire passer ce fichier par le
            # pipeline Crowbar -> Blender -> studiomdl, et de le réinjecter à
            # l'identique à la toute fin du traitement (cf. restore_original_phy).
            model_stem_for_phy = Path(entry.original_model).stem
            entry.original_phy_path = preserve_original_phy(source_model_path, model_stem_for_phy, self.log_queue.put)

            out_rel = source_relative_subpath(entry.original_model)
            out_rel_dir = str(Path(out_rel).parent)
            safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', out_rel)
            workdir = TEMP_ROOT / safe_name
            if workdir.exists(): shutil.rmtree(workdir, ignore_errors=True)
            workdir.mkdir(parents=True, exist_ok=True)

            entry.output_path = resolve_output_model_path(output_root, entry.original_model)
            entry.workdir = str(workdir)
            entry.preview_dir = str(workdir / "previews")

            staging_game = workdir / "staging_game"
            staging_models = staging_game / "models"
            # Dossier final DANS le staging, qui reflète l'arborescence models/<sous-dossier>/
            # du prop d'origine. On le crée TOUT DE SUITE : c'est ce qui manquait avant et
            # provoquait le crash "No such file or directory" dès la copie de la sortie Crowbar.
            target_dir = staging_models / out_rel_dir if out_rel_dir not in ("", ".") else staging_models
            target_dir.mkdir(parents=True, exist_ok=True)

            game_root_esc = game_root.replace(chr(92), "/")
            (staging_game / "gameinfo.txt").write_text(
                '\n'.join(['"GameInfo"', '{', f'\tgame\t"Staging Game"', '\ttitle\t"Staging Game"',
                         '\tFileSystem', '\t{', '\t\tSearchPaths', '\t\t{',
                         '\t\t\tGame\t"|gameinfo_path|."', f'\t\t\tGame\t"{game_root_esc}"', '\t\t}', '\t}', '}']),
                encoding="utf-8"
            )

            # === CROWBAR : DÉCOMPILATION DU MODÈLE COMPILÉ POUR RÉCUPÉRER SON QC D'ORIGINE ===
            # C'est l'UNIQUE source du QC d'origine : on ne génère jamais un QC "from scratch",
            # on reprend celui de Crowbar et on le CORRIGE seulement (ajout des LODs).
            decomp_dir = workdir / "decompiled"
            decomp_dir.mkdir(parents=True, exist_ok=True)
            self.log_queue.put(f"[CROWBAR] Decompiling...")

            exe = Path(crowbar_path)
            cli_exe = exe.parent / "CrowbarCLI.exe"
            cmd_exe = cli_exe if cli_exe.exists() else exe

            cmd = [str(cmd_exe), source_model_path, str(decomp_dir)] # CrowbarCLI V0.74 from Fozkais (wich do not use -o -p flags)
            self.log_queue.put(f"[CROWBAR] {cmd}")
            c_proc = self._run_silent(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

            # Le .qc peut se retrouver directement dans decomp_dir ou dans un sous-dossier
            # selon la configuration de Crowbar : on cherche donc dans les deux cas.
            qc_files = list(decomp_dir.glob("*.qc")) or list(decomp_dir.rglob("*.qc"))
            if not qc_files:
                raise RuntimeError(f"Crowbar decompilation failed (no .qc produced).\nCommand: {' '.join(cmd)}\nOut: {c_proc.stdout}")

            decompiled_qc = qc_files[0]
            # decomp_root = le dossier qui contient réellement le .qc ET tous ses fichiers
            # associés (smd, physique, sous-dossier d'animations, etc.) à copier ensemble.
            decomp_root = decompiled_qc.parent
            entry.original_qc_path = str(decompiled_qc)
            self.log_queue.put(f"[CROWBAR] Decompiled QC: {decompiled_qc.name}")
            qc_text = decompiled_qc.read_text(encoding="utf-8", errors="replace")

            # === Sauvegarde du QC D'ORIGINE (non modifié) pour référence / sécurité ===
            og_qc_dir = TEMP_ROOT / "OG_QC"
            og_qc_dir.mkdir(parents=True, exist_ok=True)
            try:
                model_stem = Path(entry.original_model).stem
                saved_qc_path = og_qc_dir / f"{model_stem}_original.qc"
                shutil.copy2(decompiled_qc, saved_qc_path)
                self.log_queue.put(f"[OG_QC] Original QC saved: {saved_qc_path}")
            except Exception as e:
                self.log_queue.put(f"[OG_QC] Save error: {e}")

            # Copie de TOUTE la sortie Crowbar (qc, smd, physique, sous-dossiers d'animations...)
            # vers le staging, en conservant l'arborescence relative. C'était le bug principal :
            # le sous-dossier models/<...>/ n'existait pas encore lors de cette copie, et les
            # fichiers dans des sous-dossiers (ex: animations) n'étaient de toute façon jamais copiés.
            for item in decomp_root.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(decomp_root)
                    dest = target_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)

            # Si le maillage de collision physique a une origine décalée par rapport au maillage
            # visuel principal (tous deux tels que décompilés par Crowbar), le
            # décalage est corrigé automatiquement sur la copie de compilation.
            diagnose_and_fix_physics_alignment(qc_text, decomp_root, target_dir, self.log_queue.put)

            # === Référence "vérité terrain" (échelle + orientation) ===
            # On calcule les stats géométriques brutes du maillage de corps
            # PRINCIPAL tel que décompilé par Crowbar (jamais touché par
            # Blender) :
            #  - diagonale de bbox -> détecte l'écart d'ÉCHELLE : l'import MDL
            #    via SourceIO applique par défaut une "World scale" de confort
            #    qui n'est PAS 1:1 avec les unités natives Source (voir la doc
            #    SourceIO : "World scale -> Use value 1 for exporting back to
            #    source engine").
            #  - étendues par axe + barycentre -> détectent une erreur
            #    d'ORIENTATION (permutation/inversion d'axes) : la rotation de
            #    conversion Blender -> SMD utilisée jusqu'ici était une valeur
            #    FIXE, qui ne correspond pas à l'orientation réelle de tous les
            #    modèles (d'où des LOD 1/2/3 "retournés" seulement sur certains
            #    props).
            # On transmet cette référence au worker Blender, qui mesure
            # lui-même l'écart après import et applique la correction
            # nécessaire (échelle ET orientation) à toutes les coordonnées
            # exportées, de façon spécifique à CE modèle.
            ref_stats = None
            try:
                body_names = extract_body_mesh_names(qc_text)
                ref_smd_name = None
                for name in body_names:
                    if 'lod' not in name.lower() and '_lod' not in name.lower():
                        ref_smd_name = name
                        break
                if not ref_smd_name and body_names:
                    ref_smd_name = body_names[0]
                if ref_smd_name:
                    ref_candidates = list(decomp_root.rglob(Path(ref_smd_name).name))
                    if ref_candidates:
                        ref_stats = smd_triangles_stats(ref_candidates[0])
                        if ref_stats:
                            self.log_queue.put(
                                f"[SCALE] Crowbar reference: diag={ref_stats['diag']:.3f}, "
                                f"extents={tuple(round(v, 3) for v in ref_stats['extents'])}, "
                                f"centroid={tuple(round(v, 3) for v in ref_stats['centroid'])}")
                        else:
                            self.log_queue.put("[SCALE] Unable to measure reference (unreadable SMD).")
            except Exception as e:
                self.log_queue.put(f"[SCALE] Reference calculation error: {e}")

            lod_arg = ",".join(f"{d}:{r}" for d, r in lod_levels)
            cmd = [blender_path, "-b", "-P", str(APPDATA_ROOT / "blender_worker.py"), "--",
                   "--input", source_model_path, "--workdir", str(workdir),
                   "--lod-count", str(len(lod_levels)), "--lod-levels", lod_arg]
            # Option "Générer la preview dans le traitement" (v1.13) : gain de temps sur un
            # gros batch quand on n'a pas besoin des apercus 2D decimes.
            if self.generate_preview_var.get():
                cmd.append("--preview")
            if ref_stats:
                # IMPORTANT : on utilise la forme "--option=valeur" (un seul token
                # argv) et non "--option", "valeur" (deux tokens séparés). Les
                # centroïdes peuvent être négatifs (ex: "-0.000000,-4.078000,...")
                # et un token séparé commençant par "-" est interprété par argparse
                # comme un NOUVEAU flag plutôt que comme la valeur de l'option
                # précédente ("expected one argument"), puisqu'il ne matche pas son
                # pattern interne de "nombre négatif simple" (il contient des
                # virgules). La forme "=" élimine complètement cette ambiguïté.
                cmd += [f"--ref-diag={ref_stats['diag']:.6f}",
                        "--ref-extents=" + ",".join(f"{v:.6f}" for v in ref_stats["extents"]),
                        "--ref-centroid=" + ",".join(f"{v:.6f}" for v in ref_stats["centroid"]),
                        f"--ref-mesh-name={ref_smd_name}"]

            self.log_queue.put(f"[BLENDER] Launching (MDL import via SourceIO)...")
            proc = self._run_silent(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.stdout: self.log_queue.put(proc.stdout.strip())
            if proc.stderr: self.log_queue.put(f"[BLENDER ERR] {proc.stderr.strip()}")
            if proc.returncode != 0: raise RuntimeError(f"Blender failed (code {proc.returncode})")

            # On AJOUTE seulement les blocs $lod. Tout le reste
            # (hitboxes, séquences, $modelname, $cdmaterials, collisions, etc.) reste identique,
            # et le nom du modèle ($modelname) n'est jamais changé. ===
            rel_model = source_relative_subpath(entry.original_model)
            mat_dir = normalize_slashes(str(Path(rel_model).parent))

            # FIX bodygroups (v1.14) : on lit le manifeste ecrit par le worker Blender pour
            # savoir quel fichier LOD correspond a QUEL objet/bodygroup exporte separement,
            # afin que patch_original_qc() puisse faire correspondre chaque maillage
            # d'origine du QC a SON PROPRE fichier decime plutot qu'a un fichier fusionne
            # partage par tous les bodygroups.
            object_manifest: Optional[List[str]] = None
            manifest_path = workdir / "smd" / "lod_bodies_manifest.json"
            if manifest_path.exists():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    object_manifest = [str(e.get("object_name", "")) for e in manifest_data if e.get("object_name")]
                except Exception as e:
                    self.log_queue.put(f"[BODYGROUP MANIFEST] Failed to read manifest: {e}")

            patched_qc_text = patch_original_qc(qc_text, entry, rel_model, mat_dir, lod_levels, None,
                                                object_manifest=object_manifest,
                                                log_fn=self.log_queue.put)

            entry.qc_path = str(workdir / decompiled_qc.name)
            Path(entry.qc_path).write_text(patched_qc_text, encoding="utf-8")
            # On écrase la copie non corrigée déjà présente dans le staging par la version patchée.
            (target_dir / decompiled_qc.name).write_text(patched_qc_text, encoding="utf-8")

            smd_dir = workdir / "smd"
            if smd_dir.exists():
                entry.lod_model_paths = [str(f) for f in sorted(smd_dir.glob("*.smd"))]
                self.log_queue.put(f"[LOD] {len(entry.lod_model_paths)} LOD files generated")
                try:
                    persistent_cache = Path(output_root) / ".lod_preview_cache" / re.sub(r'[^A-Za-z0-9._-]+', '_', source_relative_subpath(entry.original_model)) / "smd"
                    persistent_cache.mkdir(parents=True, exist_ok=True)
                    for f in smd_dir.glob("*.smd"): shutil.copy2(f, persistent_cache / f.name)
                except Exception: pass

                # Copie des LODs générés (lod0.smd, lod1.smd, ...) dans le staging, à côté des
                # fichiers d'origine décompilés par Crowbar.
                for f in smd_dir.iterdir():
                    if f.is_file(): shutil.copy2(f, target_dir / f.name)

            preview_dir = workdir / "previews"
            if preview_dir.exists():
                def _preview_sort_key(p: Path):
                    m = re.search(r"preview_lod(\d+)\.png$", p.name, flags=re.I)
                    return int(m.group(1)) if m else 10_000
                preview_paths = sorted(preview_dir.glob("preview_lod*.png"), key=_preview_sort_key)
                entry.preview_frames = [str(p) for p in preview_paths]
                try:
                    persistent_img_cache = Path(output_root) / ".lod_preview_cache" / re.sub(r'[^A-Za-z0-9._-]+', '_', source_relative_subpath(entry.original_model)) / "previews"
                    persistent_img_cache.mkdir(parents=True, exist_ok=True)
                    for f in preview_paths: shutil.copy2(f, persistent_img_cache / f.name)
                except Exception: pass

            stage_qc = target_dir / decompiled_qc.name
            cmd = [studiomdl_path, "-game", str(staging_game), str(stage_qc)]
            self.log_queue.put("[STUDIOMDL] Compiling with LODs...")
            proc2 = self._run_silent(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc2.stdout: self.log_queue.put(proc2.stdout.strip())
            if proc2.returncode != 0: raise RuntimeError(f"studiomdl failed (code {proc2.returncode})")

            final_dest = Path(output_root) / Path(out_rel_dir)
            final_dest.mkdir(parents=True, exist_ok=True)

            for item in target_dir.iterdir():
                if item.is_file() and item.suffix.lower() not in ('.qc', '.smd', '.dmx', '.vta', '.txt'):
                    shutil.copy2(item, final_dest / item.name)

            # === Restauration de la physique D'ORIGINE ===
            # Le .phy fraîchement recompilé par studiomdl (copié ci-dessus) est
            # écarté au profit de la sauvegarde intacte faite en tout début de
            # traitement (preserve_original_phy), SAUF si l'utilisateur a
            # explicitement choisi "Recompiler le PHY" dans l'UI.
            if physics_mode == "keep":
                restore_original_phy(entry.original_phy_path, final_dest, model_stem_for_phy, self.log_queue.put)
            else:
                self.log_queue.put("[PHY] 'Recompile PHY' mode: keeping the collision mesh "
                                    "recompiled by studiomdl as-is.")

            self.log_queue.put(f"[SUCCESS] Files copied to: {final_dest}")
            self.status_queue.put(("done", key))

            # Enregistrer le temps écoulé pour ce prop
            job_elapsed = time.time() - job_start
            self.job_times.append((entry.original_model, job_elapsed))
            self.log_queue.put(f"[TIMER] {Path(entry.original_model).name} completed in {job_elapsed:.1f}s")

        except Exception as e:
            job_elapsed = time.time() - job_start
            entry.error = str(e)
            if attempt < max_attempts:
                self.log_queue.put(f"[RETRY] Attempt {attempt}/{max_attempts} failed for "
                                   f"{entry.original_model}: {e} (elapsed: {job_elapsed:.1f}s) - retrying...")
                raise
            self.job_times.append((entry.original_model, job_elapsed))
            self.status_queue.put(("error", key))
            self.log_queue.put(f"[ERROR] {entry.original_model}: {e} (elapsed: {job_elapsed:.1f}s, "
                               f"{max_attempts} attempts exhausted)")
            raise

    def _get_blender_worker_text(self) -> str:
        return r'''"""
Blender Worker Script for GMod LOD Builder & Optimizer
"""
import argparse
import itertools
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Optional
import bpy
import bmesh
import mathutils

def log(msg): print(msg, flush=True)

def sanitize_body_name(name: str) -> str:
    """
    Normalise un nom d'objet/maillage en un identifiant de fichier stable :
    minuscules, uniquement [a-z0-9_]. Utilisee pour nommer les fichiers LOD
    PAR BODYGROUP (lod{idx}_{nom}.smd) ET, cote script principal (Python hors
    Blender), pour faire correspondre les noms de maillages du QC original a
    ces memes fichiers -- DOIT rester rigoureusement identique des deux cotes
    (voir sanitize_body_name() duplique dans patch_original_qc()).

    FIX (v1.16) : retire d'abord une extension .smd/.dmx finale (le nom d'objet
    Blender importe par SourceIO EST le nom de fichier SMD d'origine complet,
    extension incluse, ex: 'Christmas_Part.smd') pour matcher exactement la
    normalisation faite cote QC.
    """
    n = name or ""
    low = n.lower()
    if low.endswith(".smd") or low.endswith(".dmx"):
        n = n[:-4]
    out = []
    for ch in n.lower():
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out).strip("_")
    return s or "mesh"

# ÉCHELLE (2/2) : facteur de correction numérique appliqué à TOUTES
# les positions écrites par write_smd() (sommets ET os). Calculé une seule
# fois dans main(), au premier import, en comparant la diagonale de bbox du
# maillage importé dans Blender à celle du maillage d'origine décompilé par
# Crowbar (--ref-diag, transmis par le script principal). Reste à 1.0 (donc
# sans effet) si aucune référence n'a pu être calculée ou si l'écart mesuré
# est négligeable.
SCALE_FIX = 1.0
# FIX RETRECISSEMENT LOD PAR BODYGROUP (v1.16) : maintenant que chaque bodygroup est
# decime et exporte INDIVIDUELLEMENT (voir sanitize_body_name / manifeste), une
# correction d'echelle UNIQUE partagee par LOD n'a plus de sens : chaque bodygroup a sa
# propre geometrie et peut se contracter differemment sous DECIMATE (mode COLLAPSE).
# LOD_SCALE_CORR_PER_OBJECT stocke donc un facteur de correction PAR OBJET (cle =
# sanitize_body_name(obj.name)), recalcule apres decimation de CET OBJET precis, en
# comparant sa propre diagonale de bbox (raw_bbox_diagonal, meme methode que SCALE_FIX)
# a celle de CE MEME OBJET au LOD0 (non decime). SCALE_FIX reste un facteur global
# (ecart import Blender vs reference Crowbar, mesure une fois) ; les deux sont
# multiplies ensemble dans write_smd(), par objet.
LOD_SCALE_CORR_PER_OBJECT = {}
LOD0_RAW_DIAG_PER_OBJECT = {}  # sanitize_body_name(obj.name) -> diagonale bbox de CET OBJET au LOD0

# ORIENTATION : rotation de conversion Blender -> SMD/Source utilisée
# par write_smd() pour les sommets et pour l'os racine. La valeur ci-dessous
# (-90° autour de X) est la valeur PAR DÉFAUT historique, qui ne correspond en
# réalité qu'à l'un des 24 axes/orientations possibles pour un objet Blender
# importé -- ce qui explique pourquoi certains modèles ressortaient "retournés"
# sur plusieurs axes et d'autres non. Elle est recalculée empiriquement une
# seule fois dans main() (au premier import), en comparant la forme réelle
# (étendues par axe + barycentre) du maillage d'origine Crowbar à celle importée
# dans Blender, et en choisissant, parmi les 24 rotations propres du cube,
# celle qui les fait correspondre le mieux. Si aucune référence n'est fournie
# (--ref-extents absent), elle reste à sa valeur par défaut historique.
AXIS_FIX = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')

# Index du LOD en cours de traitement (0=LOD0, 1=LOD1, etc.). Utilisé par write_smd()
# pour désactiver AXIS_FIX sur les LOD décimés (propper++ fix).
CURRENT_LOD_INDEX = 0

def generate_cube_rotations():
    """
    Génère les 24 rotations propres (déterminant +1) du groupe de symétrie du
    cube : toutes les façons de réassigner les axes X/Y/Z avec un signe, tout
    en conservant un repère direct. Sert à tester quelle réorientation exacte
    (permutation + inversions d'axes) fait correspondre le maillage importé
    dans Blender à la forme réelle du maillage d'origine (Crowbar).
    """
    mats = []
    axes = [0, 1, 2]
    for perm in itertools.permutations(axes):
        for signs in itertools.product([1, -1], repeat=3):
            # La colonne `dst_axis` de la matrice reçoit +/-1 à la ligne
            # `src_axis`, avec dst_axis = perm[src_axis].
            rows = [[0.0, 0.0, 0.0] for _ in range(3)]
            for src_axis in range(3):
                dst_axis = perm[src_axis]
                rows[dst_axis][src_axis] = float(signs[src_axis])
            m3 = mathutils.Matrix(rows)
            if abs(m3.determinant() - 1.0) < 1e-6:
                m4 = m3.to_4x4()
                mats.append(m4)
    return mats

def parse_args():
    argv = sys.argv
    if "--" not in argv: return None
    idx = argv.index("--")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--lod-count", type=int, required=True)
    parser.add_argument("--lod-levels", required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--ref-diag", type=float, default=None)
    parser.add_argument("--ref-extents", default=None)
    parser.add_argument("--ref-centroid", default=None)
    parser.add_argument("--ref-mesh-name", default=None,
                        help="Nom du maillage de reference cote QC (v1.13) : permet de "
                             "mesurer SCALE_FIX/AXIS_FIX sur le MEME objet Blender que la "
                             "reference Crowbar, plutot que sur tous les bodygroups combines.")
    parser.add_argument("--smd-only", action="store_true",
                        help="Mode SMD only (v1.13) : decime un .smd de reference brut, "
                             "sans decompilation/recompilation (pas de Crowbar/studiomdl).")
    return parser.parse_args(argv[idx + 1:])

def bootstrap_sourceio():
    try:
        import addon_utils
        for name in ("SourceIO", "io_scene_sourceio"):
            try: addon_utils.enable(name, default_set=False, persistent=False)
            except Exception: pass
    except Exception: pass

def reset_scene():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        try: bpy.ops.object.mode_set(mode='OBJECT')
        except: pass
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for col in list(bpy.data.collections):
        try: bpy.data.collections.remove(col)
        except: pass
    for db in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for item in list(db):
            try:
                if item.users == 0: db.remove(item)
            except: pass

def unexclude_all_collections():
    """
    FIX POTENTIEL bodygroups vides (v1.15) : SourceIO organise les objets par
    bodygroup dans des COLLECTIONS separees (bodygroup_grouping=True). Il est
    courant que les alternatives NON actives par defaut (option "blank" en position
    0 dans le QC) soient placees dans une collection EXCLUE du View Layer courant,
    pour ne montrer QUE le choix par defaut a l'ecran. Un objet dans une collection
    exclue reste liste par bpy.context.scene.objects, MAIS le depsgraph du View
    Layer courant NE L'EVALUE PAS -- eval_obj.to_mesh() peut alors renvoyer un mesh
    VIDE meme si obj.data (les donnees de base, non-evaluees) contient bien la
    geometrie reelle. Ca correspond exactement au symptome observe : le maillage
    existe (visible en LOD0, qui vient du SMD Crowbar original, jamais touche par
    Blender), mais les fichiers LOD generes par CE script pour ces bodygroups
    precis sont vides. On force donc TOUTES les collections a etre incluses avant
    tout traitement, pour garantir une evaluation complete de chaque objet, quel
    que soit son bodygroup d'origine ou son etat "actif par defaut".
    """
    def _walk(layer_coll):
        try:
            layer_coll.exclude = False
        except Exception:
            pass
        for child in layer_coll.children:
            _walk(child)
    try:
        _walk(bpy.context.view_layer.layer_collection)
    except Exception as e:
        log(f"[DIAG BODYGROUP] Failed to un-exclude collections: {e}")

def import_model(filepath):
    bootstrap_sourceio()
    try:
        from SourceIO.blender_bindings.models import import_model as _imp
        from SourceIO.blender_bindings.models.common import put_into_collections
        from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
        from SourceIO.library.shared.content_manager import ContentManager
        from SourceIO.library.utils import FileBuffer
        from SourceIO.library.utils.tiny_path import TinyPath
    except Exception as e:
        log(f"[ERROR] SourceIO unavailable: {e}")
        return False
    mdl = TinyPath(filepath)
    cm = ContentManager()
    cm.first_import = mdl.parent
    try: cm.scan_for_content(mdl.parent)
    except: pass
    opts = ModelOptions.default()
    opts.write_qc = False; opts.import_physics = False; opts.load_refpose = False
    opts.import_animations = False; opts.create_flex_drivers = False
    opts.bodygroup_grouping = True; opts.import_textures = True
    # ÉCHELLE (1/2) : SourceIO applique par défaut une "World scale"
    # de confort (documentation officielle : "World scale -> Scale factor.
    # Use value 1 for exporting back to source engine, leave as is for more
    # or less human scale") qui n'est PAS 1:1 avec les unités natives Source.
    # On force donc explicitement 1.0 sur tout attribut d'échelle existant,
    # quel que soit son nom exact selon la version de SourceIO installée.
    # Voir aussi la correction empirique (SCALE_FIX) dans main()/write_smd,
    # qui rattrape le cas où ce nom d'attribut ne serait pas trouvé ci-dessous.
    for _scale_attr in ("scale", "world_scale", "import_scale", "global_scale", "unit_scale"):
        if hasattr(opts, _scale_attr):
            try:
                setattr(opts, _scale_attr, 1.0)
                log(f"[SCALE FIX] opts.{_scale_attr} forced to 1.0")
            except Exception:
                pass
    try:
        with FileBuffer(mdl) as f:
            container = _imp(mdl, f, cm, opts, None)
    except Exception as e:
        return False
    if container is None: return False
    try: put_into_collections(container, mdl.stem, bodygroup_grouping=opts.bodygroup_grouping)
    except Exception: return False
    cm.first_import = None
    return True

def compute_uv_winding_sign(obj) -> Optional[float]:
    """
    Calcule le signe moyen (pondéré par aire 3D) de l'aire signée UV de tous
    les triangles du maillage évalué de `obj`. Sert de "signature" d'orientation
    UV : si ce signe s'inverse entre le mesh LOD0 (référence) et un LOD décimé,
    cela signifie que le mapping UV a subi un effet miroir (Mirror Y/X) --
    typiquement causé par une inversion de winding de face pendant la
    décimation (COLLAPSE) sur certains sous-maillages.

    Utilise la formule du lacet (shoelace) en espace UV :
        aire_signée = 0.5 * ((u1*(v2-v3) + u2*(v3-v1) + u3*(v1-v2)))
    Le signe (positif/négatif) indique le sens de rotation (winding) du
    triangle en espace UV. Une majorité de triangles positifs vs négatifs
    donne l'orientation globale du mapping.
    """
    if obj.type != 'MESH': return None
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if not uv_layer or len(mesh.polygons) == 0: return None

    total_weighted_sign = 0.0
    total_weight = 0.0
    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        if len(loops) < 3: continue
        # Triangulation en éventail pour polygones à N côtés
        for i in range(1, len(loops) - 1):
            l0, l1, l2 = loops[0], loops[i], loops[i + 1]
            uv0 = uv_layer.data[l0].uv
            uv1 = uv_layer.data[l1].uv
            uv2 = uv_layer.data[l2].uv
            signed_area = 0.5 * ((uv1.x - uv0.x) * (uv2.y - uv0.y) - (uv2.x - uv0.x) * (uv1.y - uv0.y))
            weight = abs(signed_area)
            if weight < 1e-12: continue
            total_weighted_sign += (1.0 if signed_area > 0 else -1.0) * weight
            total_weight += weight

    if total_weight < 1e-9: return None
    return total_weighted_sign / total_weight  # entre -1.0 et 1.0

def apply_uv_flip_y(obj):
    """Applique un Mirror Y sur le UV layer actif (v = 1.0 - v)."""
    if obj.type != 'MESH': return
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if not uv_layer: return
    for loop_uv in uv_layer.data:
        loop_uv.uv.y = 1.0 - loop_uv.uv.y

def apply_decimate(obj, ratio):
    """
    Décimation via modifier DECIMATE, appliquée SANS bpy.ops.object.modifier_apply().

    CORRECTIF CRITIQUE (v3) : bpy.ops.object.modifier_apply({'object': obj}, ...)
    utilise l'ancien système de "context override" par dictionnaire, SUPPRIMÉ
    depuis Blender 4.0 (remplacé par bpy.context.temp_override()). Sur Blender
    5.1.x (confirmé dans les logs utilisateur), cet appel lève une exception
    silencieusement catchée par le try/except -- le modifier N'ÉTAIT JAMAIS
    RÉELLEMENT APPLIQUÉ. Conséquence en cascade :
      - Le mesh restait NON décimé (obj.data inchangé).
      - compute_uv_winding_sign() comparait donc le même mesh avant/après
        décimation -> aucun flip ne pouvait jamais être détecté.
      - bake_object_transforms() (qui utilise aussi bpy.ops.object.transform_apply
        sans contexte explicite) pouvait échouer pour la même raison sur
        certaines versions/configs -> rotation jamais cuite.
    Ceci explique pourquoi les patchs précédents n'avaient AUCUN effet visible.

    Solution : ne plus utiliser AUCUN bpy.ops.object.* pour appliquer le
    modifier. On récupère le mesh évalué (depsgraph, méthode 100% API data,
    pas d'opérateur) et on l'assigne directement à obj.data.
    """
    if obj.type != 'MESH': return
    face_count = len(obj.data.polygons)
    if face_count == 0:
        log(f"[DECIMATE SKIP] {obj.name}: 0 polygons already at start (ratio={ratio}) - "
            f"geometry was already empty BEFORE decimation was attempted.")
        return
    if ratio >= 1.0: return
    target = int(face_count * ratio)
    if target < 4:
        log(f"[DECIMATE SKIP] {obj.name}: face_count={face_count} too low for ratio={ratio} "
            f"(target={target} < 4) - decimation skipped, ORIGINAL geometry kept unchanged.")
        return

    try:
        mod = obj.modifiers.new('LOD_Decimate', 'DECIMATE')
        mod.decimate_type = 'COLLAPSE'
        mod.ratio = max(0.01, min(1.0, float(ratio)))
        mod.use_collapse_triangulate = True

        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        try:
            baked = eval_obj.to_mesh()
            new_mesh = baked.copy()
        finally:
            try: eval_obj.to_mesh_clear()
            except Exception: pass

        obj.modifiers.remove(mod)
        old_mesh = obj.data
        new_mesh.name = old_mesh.name + "_lod"

        new_poly_count = len(new_mesh.polygons)
        obj.data = new_mesh
        if old_mesh.users == 0:
            try: bpy.data.meshes.remove(old_mesh)
            except Exception: pass

        log(f"[DECIMATE] {obj.name}: {face_count} -> {new_poly_count} faces "
            f"(ratio={ratio}, via evaluated-mesh swap, no bpy.ops)")
    except Exception as e:
        log(f"[DECIMATE] Decimation error for {obj.name}: {e}")

def clean_mat_name(name):
    stem = name.replace("\\", "/").split("/")[-1]
    if "." in stem: stem = stem.rsplit(".", 1)[0]
    import re
    stem = re.sub(r'\.\d+$', '', stem)
    return stem if stem else "default"

def write_smd(smd_filepath, mesh_objects):
    armatures = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    arm_obj = armatures[0] if armatures else None
    lines = ["version 1", "", "nodes"]
    bone_to_idx = {}
    if arm_obj:
        for idx, bone in enumerate(arm_obj.data.bones):
            bone_to_idx[bone.name] = idx
            p_idx = bone_to_idx[bone.parent.name] if bone.parent else -1
            lines.append(f'{idx} "{bone.name}" {p_idx}')
    else:
        bone_to_idx["root"] = 0
        lines.append('0 "root" -1')
    lines.append("end"); lines.append(""); lines.append("skeleton"); lines.append("time 0")
    # R = AXIS_FIX : rotation de conversion Blender -> SMD/Source, déterminée
    # empiriquement une seule fois par modèle dans main() (voir AXIS_FIX en
    # tête de fichier). Remplace l'ancienne valeur fixe (-90° X) qui ne
    # correspondait pas à l'orientation réelle de tous les modèles.
    # FIX v1.13 (v3) : l'ancien "correctif universel" forçait une rotation FIXE
    # de -90° Z sur TOUS les LOD décimés (idx>0), en écrasant l'AXIS_FIX pourtant
    # calculé empiriquement pour CE modèle précis. C'était un reliquat de l'ancienne
    # implémentation de la décimation (basée sur bpy.ops.object.modifier_apply, qui
    # induisait une vraie rotation résiduelle de +90° Z après application). La
    # décimation actuelle (evaluated-mesh swap, sans bpy.ops) n'introduit plus cette
    # rotation résiduelle : ce correctif était donc devenu une MAUVAISE rotation
    # appliquée à tous les LOD décimés. Sur une forme non cubique/asymétrique, une
    # mauvaise rotation présente une silhouette différente (donc apparemment plus
    # petite) à une caméra fixe (HLMV) SANS changer la diagonale de la bounding box
    # -> invisible aux corrections d'échelle précédentes, qui mesuraient la bbox.
    # On réutilise donc simplement AXIS_FIX (LOD0) pour tous les LOD.
    R = AXIS_FIX
    R3 = R.to_3x3()
    if arm_obj:
        for idx, bone in enumerate(arm_obj.data.bones):
            if bone.parent:
                mat_rel = bone.parent.matrix_local.inverted() @ bone.matrix_local
                pos = mat_rel.to_translation(); rot = mat_rel.to_euler('XYZ')
            else:
                mat_g_se = R @ bone.matrix_local
                pos = mat_g_se.to_translation(); rot = mat_g_se.to_euler('XYZ')
            # SCALE_FIX : les translations (positions) doivent être corrigées,
            # jamais les rotations (une rotation n'a pas d'"échelle").
            lines.append(f"{idx}  {pos.x*SCALE_FIX:.6f} {pos.y*SCALE_FIX:.6f} {pos.z*SCALE_FIX:.6f}  {rot.x:.6f} {rot.y:.6f} {rot.z:.6f}")
    else:
        lines.append("0  0.000000 0.000000 0.000000  0.000000 0.000000 0.000000")
    lines.append("end"); lines.append(""); lines.append("triangles")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total_tris = 0
    for obj in mesh_objects:
        if obj.type != 'MESH': continue
        try: eval_obj = obj.evaluated_get(depsgraph); mesh = eval_obj.to_mesh()
        except: continue
        if not mesh or len(mesh.polygons) == 0: continue
        bm = bmesh.new(); bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.to_mesh(mesh); bm.free()
        mat_names = [clean_mat_name(s.material.name if s.material else "default") for s in obj.material_slots] or ["default"]
        uv_layer = mesh.uv_layers.active
        mw_to_arm = arm_obj.matrix_world.inverted() @ obj.matrix_world if arm_obj else obj.matrix_world
        mw_to_arm_3x3 = mw_to_arm.to_3x3().normalized()
        # Correction de retrecissement PROPRE A CET OBJET (voir declaration de
        # LOD_SCALE_CORR_PER_OBJECT plus haut) : 1.0 si pas encore mesuree pour cet objet
        # (ex: LOD0, ou objet nouveau sans reference).
        obj_scale_corr = LOD_SCALE_CORR_PER_OBJECT.get(sanitize_body_name(obj.name), 1.0)
        for poly in mesh.polygons:
            loops = list(poly.loop_indices)
            if len(loops) != 3: continue
            mat_idx = min(poly.material_index, len(mat_names) - 1)
            lines.append(mat_names[mat_idx])
            for li in loops:
                loop = mesh.loops[li]; vert = mesh.vertices[loop.vertex_index]
                pos_se = R @ (mw_to_arm @ vert.co); n_se = R3 @ (mw_to_arm_3x3 @ loop.normal)
                if n_se.length_squared > 1e-9: n_se.normalize()
                else: n_se = mathutils.Vector((0, 0, 1))
                # SCALE_FIX : correction globale (import vs reference Crowbar).
                # obj_scale_corr : correction du retrecissement introduit par DECIMATE sur
                # CET OBJET/bodygroup precis (voir LOD_SCALE_CORR_PER_OBJECT dans main()).
                # Uniquement sur les positions de sommets (pas sur les os : le squelette
                # reste celui du LOD0). Les normales ne sont pas affectees (directions
                # unitaires, pas des grandeurs d'echelle).
                pos_se = pos_se * SCALE_FIX * obj_scale_corr
                u, v_c = (uv_layer.data[li].uv.x, 1.0 - uv_layer.data[li].uv.y) if uv_layer else (0.0, 0.0)
                weights = []
                for g in vert.groups:
                    if g.group < len(obj.vertex_groups):
                        gname = obj.vertex_groups[g.group].name
                        if gname in bone_to_idx and g.weight > 0.001: weights.append((bone_to_idx[gname], g.weight))
                tw = sum(w[1] for w in weights)
                weights = [(idx, w / tw) for idx, w in weights] if tw > 0 else [(0, 1.0)]
                weights.sort(key=lambda x: x[1], reverse=True)
                w_str = f"{len(weights)}" + "".join(f" {idx} {w:.6f}" for idx, w in weights)
                lines.append(f"{weights[0][0]}  {pos_se.x:.6f} {pos_se.y:.6f} {pos_se.z:.6f}  {n_se.x:.6f} {n_se.y:.6f} {n_se.z:.6f}  {u:.6f} {v_c:.6f}  {w_str}")
            total_tris += 1
        try: eval_obj.to_mesh_clear()
        except: pass
    if total_tris == 0:
        lines.append("default")
        lines.append("0  0.000 0.000 0.000  0.0 0.0 1.0  0.0 0.0  1 0 1.0")
        lines.append("0  0.010 0.000 0.000  0.0 0.0 1.0  0.0 1.0  1 0 1.0")
        lines.append("0  0.000 0.010 0.000  0.0 0.0 1.0  1.0 0.0  1 0 1.0")
        total_tris = 1
    lines.append("end"); lines.append("")
    Path(smd_filepath).parent.mkdir(parents=True, exist_ok=True)
    Path(smd_filepath).write_text("\n".join(lines), encoding="utf-8")

def export_material_metadata(mesh_objects, workdir):
    mat_dirs = set()
    mesh_names = []
    for obj in mesh_objects:
        if obj.type == 'MESH':
            mesh_names.append(obj.name)
            for slot in obj.material_slots:
                if not slot.material: continue
                name = slot.material.name.replace("\\", "/")
                parts = name.split("/")
                if len(parts) > 1: mat_dirs.add("/".join(parts[:-1]))
    data = {"material_dirs": sorted(mat_dirs), "mesh_names": sorted(list(set(mesh_names)))}
    (Path(workdir) / "metadata.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

def bbox_of(objs):
    xs, ys, zs = [], [], []
    for obj in objs:
        if not hasattr(obj, "bound_box"): continue
        for c in obj.bound_box:
            w = obj.matrix_world @ mathutils.Vector(c)
            xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs: return None
    return ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2, max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)))

def raw_bbox_diagonal(objs):
    """
    Diagonale de la bbox monde (Blender) des objets donnés. Invariante par
    rotation : comparée à smd_triangles_stats() côté script principal
    (même invariant, calculé sur le .smd d'origine), elle permet de détecter
    un pur facteur d'échelle introduit par l'import SourceIO.
    """
    xs, ys, zs = [], [], []
    for obj in objs:
        if not hasattr(obj, "bound_box"): continue
        for c in obj.bound_box:
            w = obj.matrix_world @ mathutils.Vector(c)
            xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs: return None
    dx = max(xs) - min(xs); dy = max(ys) - min(ys); dz = max(zs) - min(zs)
    return math.sqrt(dx*dx + dy*dy + dz*dz)

def compute_pre_axis_stats(mesh_objects, arm_obj):
    """
    Étendues par axe + barycentre de tous les sommets de mesh_objects, dans le
    même espace "pré-rotation, pré-échelle" que celui utilisé par write_smd()
    (mw_to_arm @ vert.co, AVANT application de R/AXIS_FIX et de SCALE_FIX).
    Comparé à smd_triangles_stats() côté script principal (mêmes grandeurs,
    calculées sur les coordonnées BRUTES du .smd d'origine Crowbar), ceci sert
    à déterminer empiriquement quelle rotation de correction (AXIS_FIX) fait
    correspondre l'import Blender à la forme réelle du modèle d'origine.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    xs, ys, zs = [], [], []
    for obj in mesh_objects:
        if obj.type != 'MESH': continue
        try:
            eval_obj = obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
        except Exception:
            continue
        if not mesh: continue
        mw_to_arm = arm_obj.matrix_world.inverted() @ obj.matrix_world if arm_obj else obj.matrix_world
        for vert in mesh.vertices:
            w = mw_to_arm @ vert.co
            xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs:
        return None
    dx = max(xs) - min(xs); dy = max(ys) - min(ys); dz = max(zs) - min(zs)
    mx = sum(xs) / len(xs); my = sum(ys) / len(ys); mz = sum(zs) / len(zs)
    return {"extents": (dx, dy, dz), "centroid": (mx, my, mz)}

def describe_axis_matrix(m4):
    """Description lisible d'une matrice de correction d'axes, ex: 'X->X, Y->Z, Z->-Y'."""
    m3 = m4.to_3x3()
    axis_names = ['X', 'Y', 'Z']
    parts = []
    for j in range(3):
        col = m3.col[j]
        for i in range(3):
            if abs(col[i]) > 0.5:
                sign = '' if col[i] > 0 else '-'
                parts.append(f"{axis_names[j]}->{sign}{axis_names[i]}")
                break
    return ", ".join(parts)

def setup_camera(objs):
    # FIX preview hors champ pour gros/petits modeles (v1.13, v2) : deux problemes
    # distincts corriges ici :
    # 1) Le calcul precedent n'utilisait QUE le champ de vision HORIZONTAL. Le rendu
    #    est en 800x600 (format "paysage", plus large que haut) : avec le mode "AUTO"
    #    de Blender, le FOV VERTICAL est plus ETROIT que l'horizontal pour ce format.
    #    Un objet pouvait donc tenir horizontalement mais avoir le haut/bas coupes.
    #    On utilise maintenant le FOV le plus restrictif des deux (toujours le
    #    vertical pour un rendu paysage).
    # 2) Pour un GROS objet, la distance de camera necessaire peut depasser le plan
    #    de clipping "far" par defaut de Blender (clip_end=1000 unites) -- au-dela,
    #    Blender ne rend RIEN (image vide/noire), meme si le cadrage lui-meme est
    #    correct. On ajuste maintenant clip_end (et clip_start pour les tres petits
    #    objets tres proches) en fonction de la distance reellement calculee.
    xs, ys, zs = [], [], []
    for obj in objs:
        if not hasattr(obj, "bound_box"): continue
        for c in obj.bound_box:
            w = obj.matrix_world @ mathutils.Vector(c)
            xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs: return
    cx, cy, cz = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2
    dx, dy, dz = max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)
    # Rayon de la sphere englobante (moitie de la diagonale 3D) : majore TOUJOURS
    # l'etendue reelle de l'objet, contrairement au max d'un seul axe.
    radius = 0.5 * math.sqrt(dx * dx + dy * dy + dz * dz)
    radius = max(radius, 0.01)  # plancher degeneree uniquement (objet quasi ponctuel)

    for obj in list(bpy.context.scene.objects):
        if obj.type in {'CAMERA', 'LIGHT'}:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.object.camera_add()
    cam = bpy.context.active_object
    lens_mm = 50.0
    cam.data.lens = lens_mm
    sensor_mm = cam.data.sensor_width if getattr(cam.data, "sensor_width", 0) else 36.0

    res_x = bpy.context.scene.render.resolution_x or 800
    res_y = bpy.context.scene.render.resolution_y or 600
    horizontal_half_fov = math.atan((sensor_mm / 2.0) / lens_mm)
    vertical_sensor_mm = sensor_mm * (res_y / max(res_x, 1))
    vertical_half_fov = math.atan((vertical_sensor_mm / 2.0) / lens_mm)
    half_fov = min(horizontal_half_fov, vertical_half_fov)  # le plus restrictif des deux

    margin = 1.25  # 25% de marge de securite tout autour du modele
    distance = (radius * margin) / max(math.sin(half_fov), 1e-6)

    bpy.context.scene.camera = cam
    direction_unit = mathutils.Vector((1.8, -1.8, 1.1)).normalized()
    cam.location = mathutils.Vector((cx, cy, cz)) + direction_unit * distance
    direction = mathutils.Vector((cx, cy, cz)) - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

    # FIX plans de clipping (v1.13, v2) : sans cela, un gros objet necessitant une
    # camera eloignee tombait au-dela du plan "far" par defaut (1000 unites) et ne
    # rendait rien du tout. On calcule des plans confortablement plus larges que la
    # distance reellement utilisee, dans les deux sens (tres petit ET tres grand objet).
    cam.data.clip_start = max(0.001, distance * 0.001)
    cam.data.clip_end = max(distance * 3.0, 2000.0)

    bpy.ops.object.light_add(type='SUN', location=(cx, cy, cz + radius * 4.0))
    bpy.context.active_object.data.energy = 3.0

def render_preview(out_path):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.render.image_settings.file_format = 'PNG'
    sc.render.resolution_x = 800
    sc.render.resolution_y = 600
    sc.render.filepath = out_path
    try:
        bpy.ops.render.render(write_still=True)
        return True
    except Exception: return False

def bake_object_transforms(meshes):
    """
    "Cuit" (applique) la rotation et l'échelle de chaque objet mesh dans ses
    données de maillage (matrix_basis -> identité pour rotation/scale).

    CORRECTIF CRITIQUE : la version précédente utilisait
    bpy.ops.object.transform_apply(...), un OPÉRATEUR nécessitant un contexte
    Blender valide (fenêtre/zone 3D). En arrière-plan (blender -b), sans
    override de contexte explicite (bpy.context.temp_override), cet appel
    peut échouer silencieusement (exception catchée) -- la transform n'était
    alors JAMAIS réellement appliquée, ce qui invalidait la détection du flip
    UV et laissait la rotation résiduelle intacte.

    Solution : appliquer la transform manuellement via l'API data (aucun
    opérateur), en multipliant chaque vertex par la matrice
    rotation+scale de l'objet, puis en remettant rotation_euler/scale à neutre.
    Cette méthode fonctionne de façon identique en mode background et est
    indépendante de tout contexte de fenêtre.

    Pourquoi : sur les props multi-objets (typiquement générés par
    propper++), certains sous-objets peuvent conserver une rotation/échelle
    au niveau de l'OBJET (matrix_basis) plutôt que dans le maillage lui-même.
    On neutralise cette transform pour TOUS les LOD (y compris LOD0), avant
    tout calcul de référence (AXIS_FIX, UV winding, etc.), afin que toute la
    pipeline travaille sur une géométrie déjà cohérente.

    Ne touche jamais aux vertex groups (poids d'os par sommet) ni au modifier
    Armature : seul obj.matrix_basis (rotation_euler/scale de l'OBJET) est cuit,
    ce qui laisse obj.matrix_world mathematiquement inchange et donc le skinning
    intact -- voir commentaire detaille dans la boucle ci-dessous (fix v1.17).
    """
    applied = []
    for obj in meshes:
        # Diagnostic : log l'orientation de CHAQUE objet AVANT bake. Utile
        # pour identifier, sur un prop multi-objets (propper++), un sous-objet
        # dont la rotation initiale diffère des autres (indice de la cause
        # d'un LOD "tourné" par rapport au reste du prop).
        try:
            rot_deg = tuple(round(math.degrees(a), 2) for a in obj.rotation_euler)
            scale_v = tuple(round(s, 4) for s in obj.scale)
            if rot_deg != (0.0, 0.0, 0.0) or scale_v != (1.0, 1.0, 1.0):
                log(f"[DIAG ROTATION] {obj.name}: rotation_euler={rot_deg} deg, scale={scale_v} "
                    f"(non-neutral transform detected before bake)")
        except Exception:
            pass

        # FIX PROPPER++ 1-MESH (v1.17) : cette fonction ignorait AVANT tout objet
        # parente a une armature ("le skinning depend de la transform relative,
        # elle ne doit pas etre alteree"). Or bake_object_transforms ne touche QUE
        # matrix_basis (rotation_euler/scale de l'OBJET), jamais les vertex groups
        # (poids d'os par sommet) ni le modifier Armature lui-meme -- ces deux
        # derniers sont totalement INDEPENDANTS de matrix_basis. Cuire
        # matrix_basis dans les donnees de mesh puis le remettre a l'identite ne
        # change PAS obj.matrix_world (parent.matrix_world @ matrix_parent_inverse
        # @ matrix_basis reste mathematiquement identique avant/apres), donc ne
        # casse pas le skinning. Beaucoup de props propper++ (meme a 1 seul mesh)
        # ont une armature triviale (juste pour le positionnement), et c'etait
        # justement CES props que ce garde-fou empechait de corriger -- d'ou le
        # bug "propper++ a 1 mesh n'ont pas la rotation" qui persistait malgre le
        # fix precedent cense s'appliquer a tous les LOD.
        if obj.parent and obj.parent.type == 'ARMATURE':
            log(f"[DIAG ROTATION] {obj.name}: parente a une armature ('{obj.parent.name}') "
                f"-- transform de l'objet cuite quand meme (ne touche pas le skinning/poids "
                f"d'os, voir commentaire ci-dessus).")

        try:
            rot_mat = obj.rotation_euler.to_matrix()
            scale_vec = obj.scale.copy()
            if (rot_mat == mathutils.Matrix.Identity(3) and
                    abs(scale_vec.x - 1.0) < 1e-9 and abs(scale_vec.y - 1.0) < 1e-9 and abs(scale_vec.z - 1.0) < 1e-9):
                continue  # rien à cuire

            bake_mat = mathutils.Matrix.Diagonal(scale_vec).to_4x4()
            bake_mat = rot_mat.to_4x4() @ bake_mat
            normal_mat = rot_mat  # scale uniforme supposé pour les normales; sinon inverse-transpose serait requis

            mesh = obj.data
            mesh.transform(bake_mat)
            mesh.update()

            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
            applied.append(obj.name)
        except Exception as e:
            log(f"[TRANSFORM] bake skipped for {obj.name}: {e}")
    if applied:
        log(f"[TRANSFORM] Rotation/scale applied (baked, no bpy.ops) for: {', '.join(applied)}")

def import_smd_reference(filepath: str) -> bool:
    """
    Mode SMD ONLY (v1.13) : importe un fichier .smd de reference BRUT (pas un .mdl
    compile) via l'addon Blender "Source Tools" (io_scene_valvesource) -- PAS via
    SourceIO, qui ne sait lire que des .mdl compiles. Contrairement a SourceIO, ce
    script ne peut pas "bootstrap"/installer Source Tools automatiquement (addon
    distinct, non embarque dans ce projet) : il doit deja etre installe et active
    dans le Blender utilise. Voir https://github.com/befzz/blender_source_tools
    """
    try:
        if not hasattr(bpy.ops.import_scene, "smd"):
            try:
                bpy.ops.preferences.addon_enable(module="io_scene_valvesource")
            except Exception:
                pass
        if not hasattr(bpy.ops.import_scene, "smd"):
            log("[ERROR] Blender Source Tools (io_scene_valvesource) introuvable ou non "
                "active dans ce Blender. Le mode SMD only en a besoin pour importer un "
                ".smd brut (SourceIO ne lit que des .mdl compiles).")
            return False
        bpy.ops.import_scene.smd(filepath=filepath)
        return True
    except Exception as e:
        log(f"[ERROR] SMD import failed: {e}")
        return False


def export_smd_reference(filepath: str, mesh_objects) -> bool:
    """
    Mode SMD ONLY (v1.13) : exporte via l'operateur NATIF de Source Tools
    (export_scene.smd) plutot que via write_smd() -- write_smd() applique
    AXIS_FIX/SCALE_FIX, calibres empiriquement pour compenser les particularites
    de l'import SourceIO (MDL compiles) ; ils n'ont pas de raison de s'appliquer a
    une geometrie importee par un addon totalement different. Source Tools gere sa
    propre conversion d'axes/echelle de facon coherente entre son import et son
    export, ce qui evite d'avoir a re-deriver cette logique pour ce cas precis.
    """
    try:
        if not hasattr(bpy.ops.export_scene, "smd"):
            log("[ERROR] Blender Source Tools export operator (export_scene.smd) unavailable.")
            return False
        bpy.ops.object.select_all(action='DESELECT')
        for obj in mesh_objects:
            obj.select_set(True)
        if mesh_objects:
            bpy.context.view_layer.objects.active = mesh_objects[0]
        bpy.ops.export_scene.smd(filepath=filepath, use_selection=True)
        return True
    except Exception as e:
        log(f"[ERROR] SMD export failed: {e}")
        return False


def main_smd_only(args):
    """
    Mode SMD ONLY (v1.13) : pipeline simplifie qui decime un .smd de reference brut
    et exporte un fichier lod{N}.smd par niveau de LOD (idx>=1 ; le LOD0 EST le
    fichier d'entree, pas de re-export necessaire). Aucune decompilation (Crowbar)
    ni recompilation (studiomdl) -- reutilise apply_decimate() et
    bake_object_transforms() (generiques, independants de la source d'import), mais
    PAS write_smd()/AXIS_FIX/SCALE_FIX (calibres pour SourceIO specifiquement, voir
    export_smd_reference ci-dessus).
    """
    workdir = Path(args.workdir)
    smd_dir = workdir / "smd"
    smd_dir.mkdir(parents=True, exist_ok=True)

    lod_levels = []
    for item in args.lod_levels.split(","):
        dist_s, ratio_s = item.split(":", 1)
        lod_levels.append((int(dist_s), float(ratio_s)))

    for idx, (distance, ratio) in enumerate(lod_levels[:args.lod_count]):
        if idx == 0:
            continue  # LOD0 = le fichier d'entree lui-meme, rien a generer
        reset_scene()
        if not import_smd_reference(args.input):
            raise RuntimeError(f"SMD import failed for LOD {idx}")
        bpy.context.view_layer.update()
        unexclude_all_collections()
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        if not meshes:
            raise RuntimeError(f"No mesh found after SMD import for LOD {idx}")
        for _o in meshes:
            try:
                _o.hide_viewport = False; _o.hide_set(False); _o.hide_render = False
            except Exception:
                pass

        bake_object_transforms(meshes)
        bpy.context.view_layer.update()

        for obj in meshes:
            apply_decimate(obj, ratio)
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']

        out_path = smd_dir / f"lod{idx}.smd"
        if export_smd_reference(str(out_path), meshes):
            total_polys = sum(len(o.data.polygons) for o in meshes)
            log(f"[SMD-ONLY] LOD{idx} exported: {out_path.name} ({total_polys} polygons total)")
        else:
            log(f"[SMD-ONLY] Export failed for LOD{idx}")

    log("[OK] SMD-only worker completed")

def main():
    args = parse_args()
    if args is None: raise RuntimeError("Args parse failed")
    workdir = Path(args.workdir)
    smd_dir = workdir / "smd"
    preview_dir = workdir / "previews"
    for d in (workdir, smd_dir, preview_dir): d.mkdir(parents=True, exist_ok=True)
    lod_levels = []
    for item in args.lod_levels.split(","):
        dist_s, ratio_s = item.split(":", 1)
        lod_levels.append((int(dist_s), float(ratio_s)))
    metadata_written = False
    for idx, (dist, ratio) in enumerate(lod_levels[:args.lod_count]):
        global CURRENT_LOD_INDEX
        CURRENT_LOD_INDEX = idx
        reset_scene()
        if not import_model(args.input): raise RuntimeError(f"Import failed for LOD {idx}")
        bpy.context.view_layer.update()
        unexclude_all_collections()
        bpy.context.view_layer.update()
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        for _o in meshes:
            try:
                _o.hide_viewport = False
                _o.hide_set(False)
                _o.hide_render = False
            except Exception:
                pass
        if not meshes: raise RuntimeError(f"Aucun maillage pour le LOD {idx}")
        if len(meshes) > 1:
            log(f"[DIAG ROTATION] LOD {idx}: {len(meshes)} sub-objects detected (multi-mesh prop, "
                f"typical propper++). Checking orientation consistency...")
        # DIAGNOSTIC BODYGROUPS (v1.15) : log la geometrie de CHAQUE objet des l'import,
        # AVANT toute decimation/bake -- necessaire pour determiner si un objet de
        # bodygroup arrive DEJA vide de SourceIO (import), ou s'il devient vide plus tard
        # dans le pipeline (decimation/ecriture). Un seul log par LOD suffit (les objets
        # sont les memes a chaque reimport frais).
        for _o in meshes:
            log(f"[DIAG BODYGROUP] LOD{idx} import: object='{_o.name}' "
                f"polygons={len(_o.data.polygons)} vertices={len(_o.data.vertices)} "
                f"hide_viewport={_o.hide_viewport} hide_get={_o.hide_get()} "
                f"modifiers={[m.type for m in _o.modifiers]}")
        bake_object_transforms(meshes)
        bpy.context.view_layer.update()

        if idx == 0:
            global LOD0_RAW_DIAG_PER_OBJECT
            for _o in meshes:
                LOD0_RAW_DIAG_PER_OBJECT[sanitize_body_name(_o.name)] = raw_bbox_diagonal([_o])

        if idx == 0 and not metadata_written:
            try:
                export_material_metadata(meshes, workdir)
                metadata_written = True
            except: pass
        if idx == 0 and args.ref_diag:
            global SCALE_FIX
            # FIX SCALE BODYGROUPS (v1.13) : la reference Crowbar (--ref-diag) ne
            # decrit QU'UN SEUL maillage (le bodygroup "principal" du QC, ex:
            # Generator_Part.smd), mais l'ancienne mesure cote Blender
            # (raw_bbox_diagonal(meshes)) combinait TOUS les objets/bodygroups en
            # une seule bbox -- beaucoup plus grande des que le modele a des
            # bodygroups annexes (decorations, pieces optionnelles...). Comparer
            # une reference "1 maillage" a une mesure "tous maillages combines"
            # produisait un SCALE_FIX enorme et faux (ex: 0.58, soit -42%),
            # applique ensuite UNIFORMEMENT a TOUS les bodygroups. On isole donc
            # maintenant le MEME objet que la reference (via --ref-mesh-name) pour
            # une comparaison coherente ; a defaut de correspondance (modele a un
            # seul maillage, cas le plus courant), on retombe sur la mesure
            # combinee, identique a l'ancien comportement dans ce cas precis.
            scale_ref_meshes = meshes
            if args.ref_mesh_name:
                target_key = sanitize_body_name(args.ref_mesh_name)
                matched = [o for o in meshes if sanitize_body_name(o.name) == target_key]
                if matched:
                    scale_ref_meshes = matched
                else:
                    log(f"[SCALE FIX] No Blender object matches reference mesh "
                        f"'{args.ref_mesh_name}' (sanitized '{target_key}') -- falling back "
                        f"to all meshes combined for the scale comparison.")
            blender_diag = raw_bbox_diagonal(scale_ref_meshes)
            if blender_diag and blender_diag > 1e-6:
                ratio_scale = args.ref_diag / blender_diag
                if abs(ratio_scale - 1.0) > 0.02:
                    SCALE_FIX = ratio_scale
                    log(f"[SCALE FIX] Scale mismatch detected between the Blender import and the "
                        f"original Crowbar SMD (ref diag={args.ref_diag:.3f}, "
                        f"Blender diag={blender_diag:.3f}) -> correction factor "
                        f"applied to generated LODs: {SCALE_FIX:.6f}")
                else:
                    log(f"[SCALE FIX] Consistent scale (mismatch {abs(ratio_scale-1.0)*100:.2f}%), "
                        f"no correction needed.")
            else:
                log("[SCALE FIX] Unable to measure the Blender bbox, scale correction skipped.")

        if idx == 0 and args.ref_extents and args.ref_centroid:
            global AXIS_FIX
            try:
                ref_ext = tuple(float(v) for v in args.ref_extents.split(","))
                ref_cen = tuple(float(v) for v in args.ref_centroid.split(","))
            except Exception:
                ref_ext = None; ref_cen = None
            if ref_ext and ref_cen:
                armatures_probe = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
                arm_obj_probe = armatures_probe[0] if armatures_probe else None
                stats = compute_pre_axis_stats(meshes, arm_obj_probe)
                if stats:
                    bdx, bdy, bdz = stats["extents"]
                    bcx, bcy, bcz = stats["centroid"]
                    raw_vec = mathutils.Vector((bdx, bdy, bdz))
                    cen_vec_raw = mathutils.Vector((bcx, bcy, bcz))
                    # FIX ORIENTATION (v1.13, propper++) : ref_ext restait ordonnee X/Y/Z
                    # d'origine (PAS triee) -- voir le detail du bug juste en dessous.
                    best_mat = None
                    best_score = None
                    best_match_type = None

                    for cand in generate_cube_rotations():
                        c3 = cand.to_3x3()
                        ext_t = c3 @ raw_vec
                        cen_t = (c3 @ cen_vec_raw) * SCALE_FIX

                        # FIX BUG ORIENTATION (v1.13) : la version precedente TRIAIT les
                        # etendues (ext_sorted = sorted(...)) ET la reference (ref_ext =
                        # sorted(...)) avant de les comparer. Or trier une PERMUTATION de 3
                        # valeurs donne TOUJOURS le meme resultat trie -- donc les 24
                        # candidats de rotation (qui sont TOUS des permutations/reflexions
                        # de X/Y/Z) produisaient un ext_err QUASI IDENTIQUE entre eux, meme
                        # si le commentaire d'origine disait "etendues prioritaires (elles
                        # definissent la forme)". En pratique, le terme etendues ne
                        # discriminait RIEN, et le choix de rotation reposait UNIQUEMENT
                        # sur le centroide -- pas fiable pour un objet dont le centroide
                        # est proche d'une position symetrique/ambigue (frequent sur des
                        # pieces structurelles/propper++). On compare maintenant les
                        # etendues DANS LEUR ORDRE D'AXE D'ORIGINE (sans tri), ce qui rend
                        # CHAQUE permutation vraiment distincte et corrige la selection.
                        ext_err = sum((abs(ext_t[i]) - ref_ext[i]) ** 2 for i in range(3))

                        # Distance du CENTROÏDE (ordre-sensible) : tie-breaker
                        cen_err = sum((cen_t[i] - ref_cen[i]) ** 2 for i in range(3))

                        # Score combiné : étendues prioritaires (elles définissent la forme),
                        # centroïde comme tie-breaker (pour résoudre les ambiguïtés de symétrie)
                        score = ext_err * 10.0 + cen_err

                        if best_score is None or score < best_score:
                            best_score = score
                            best_mat = cand
                            best_match_type = "extents-centroid"

                    if best_mat is not None:
                        AXIS_FIX = best_mat
                        log(f"[AXIS FIX] Orientation determined empirically for this model: "
                            f"{describe_axis_matrix(best_mat)} ({best_match_type}, "
                            f"residual error={best_score:.6f}). "
                            f"Applied to all generated LODs.")
                    else:
                        log("[AXIS FIX] No candidate rotation found (unexpected case), "
                            "keeping default value (-90 X).")
                else:
                    log("[AXIS FIX] Unable to measure Blender geometry, "
                        "keeping default value (-90 X).")
            else:
                log("[AXIS FIX] Unreadable orientation reference, keeping default value (-90 X).")

        if ratio < 1.0:
            for obj in meshes: apply_decimate(obj, ratio)
            bpy.context.view_layer.update()
            meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']

            # CORRECTION UNIVERSELLE : Applique le Mirror Y sur les UV de TOUS les LOD
            # décimés (LOD1, LOD2, LOD3), sans condition de détection. Cette transformation
            # corrige une déformation systématique des textures présente sur tous les props
            # générés, indépendamment de la géométrie ou du résultat de la décimation.
            # Le LOD0 (ratio=1.0) n'est jamais affecté par ce correctif.
            if idx > 0:  # idx=0 est le LOD0, idx>=1 sont les LOD décimés
                for obj in meshes:
                    apply_uv_flip_y(obj)
                log(f"[UV FLIP] Mirror Y systematically applied to all meshes of LOD{idx} "
                    f"(universal fix for distorted textures).")

        # FIX RETRECISSEMENT LOD PAR BODYGROUP (v1.16) : le modifier DECIMATE (mode COLLAPSE)
        # contracte naturellement le maillage vers son centre en fusionnant les aretes
        # (comportement documente de Blender, independant de ce script). Chaque bodygroup
        # etant decime INDIVIDUELLEMENT (voir sanitize_body_name / export par objet), on
        # recalcule ici la correction SEPAREMENT POUR CHAQUE OBJET de ce LOD (comparaison de
        # diagonale de bbox, raw_bbox_diagonal, meme methode que SCALE_FIX) contre la
        # diagonale de CE MEME OBJET au LOD0 non-decime -> corrige le retrecissement quel
        # que soit le ratio de decimation ET quelle que soit la geometrie propre a chaque
        # bodygroup (au lieu d'un facteur unique partage qui sur/sous-corrigeait selon les
        # objets).
        global LOD_SCALE_CORR_PER_OBJECT
        LOD_SCALE_CORR_PER_OBJECT = {}
        if idx > 0:
            for _o in meshes:
                key = sanitize_body_name(_o.name)
                ref_diag = LOD0_RAW_DIAG_PER_OBJECT.get(key)
                if not ref_diag or ref_diag <= 1e-6:
                    continue
                current_diag = raw_bbox_diagonal([_o])
                if not current_diag or current_diag <= 1e-6:
                    log(f"[DECIMATE SCALE FIX] LOD{idx} '{_o.name}': unable to measure "
                        f"post-decimate bbox, correction skipped for this object.")
                    continue
                corr = ref_diag / current_diag
                if abs(corr - 1.0) > 0.005:  # >0.5% d'ecart, sinon inutile de corriger
                    LOD_SCALE_CORR_PER_OBJECT[key] = corr
                    log(f"[DECIMATE SCALE FIX] LOD{idx} '{_o.name}': bounding-box shrink from "
                        f"COLLAPSE decimation corrected (diag {current_diag:.3f} -> "
                        f"{ref_diag:.3f}, factor={corr:.4f})")

        # FIX BODYGROUPS QUI "NE MARCHENT PLUS" AU LOD1+ (v1.14) : la version precedente
        # fusionnait TOUS les objets mesh (donc tous les bodygroups) dans UN SEUL fichier
        # lod{idx}.smd, et le QC associait CHAQUE option de bodygroup a ce MEME fichier
        # fusionne -> a partir du LOD1, changer de bodygroup en jeu n'affichait plus aucune
        # difference visuelle (tous les choix pointaient vers le meme blob), ce qui donnait
        # l'impression que "les bodygroups ne marchent plus". On exporte maintenant EN PLUS
        # un fichier SEPARE par objet/bodygroup (lod{idx}_{nom}.smd), avec un manifeste
        # (lod_bodies_manifest.json) que le script principal utilise pour faire correspondre
        # chaque maillage d'origine du QC a SON PROPRE fichier decime a ce LOD -- le fichier
        # fusionne lod{idx}.smd est conserve comme repli si la correspondance echoue.
        write_smd(str(smd_dir / f"lod{idx}.smd"), meshes)
        if idx > 0:
            manifest_names = []
            for obj in meshes:
                safe = sanitize_body_name(obj.name)
                poly_n = len(obj.data.polygons)
                log(f"[DIAG BODYGROUP] LOD{idx} export: object='{obj.name}' -> "
                    f"lod{idx}_{safe}.smd (polygons={poly_n})")
                write_smd(str(smd_dir / f"lod{idx}_{safe}.smd"), [obj])
                manifest_names.append({"object_name": obj.name, "sanitized": safe})
            try:
                (smd_dir / "lod_bodies_manifest.json").write_text(
                    json.dumps(manifest_names, indent=2), encoding="utf-8")
            except Exception as e:
                log(f"[BODYGROUP MANIFEST] Failed to write manifest: {e}")
        if args.preview:
            try:
                setup_camera(meshes)
                render_preview(str(preview_dir / f"preview_lod{idx}.png"))
            except Exception: pass
    log("[OK] Blender worker completed")

try:
    _smdonly_args = parse_args()
    if getattr(_smdonly_args, "smd_only", False):
        main_smd_only(_smdonly_args)
    else:
        main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
'''


# =============================================================================
# MAIN
# =============================================================================

def main():
    if not (PYGLET_AVAILABLE or GLUT_AVAILABLE):
        print("Warning: No 3D backend. Install: pip install pyglet PyOpenGL")
    if DND_AVAILABLE and TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = SourceLODApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: [cleanup_temp_on_exit(), root.destroy()])
    root.mainloop()


if __name__ == "__main__":
    main()