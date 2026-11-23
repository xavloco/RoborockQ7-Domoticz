"""
<plugin key="RoborockQ7" name="Roborock Q7 (local + cloud)" author="Xavier" version="3.0.0">
    <description>
        <h2>Roborock Q7 / Q7 Max</h2>
        Pilotage via python-roborock (connexion locale automatique quand le robot repond).<br/>
        Premiere connexion : ecrire "demander" dans "Code email" et Mettre a jour, puis remplacer par le code recu par email et Mettre a jour.<br/>
        Les dependances s'installent automatiquement dans un environnement dedie.
    </description>
    <params>
        <param field="Username" label="Email Roborock" width="250px" required="true"/>
        <param field="Mode4" label="Code email (premiere connexion)" width="100px" default=""/>
        <param field="Mode1" label="Intervalle statut (s)" width="60px" default="30"/>
        <param field="Mode2" label="Image carte (chemin)" width="400px" default="/home/pi/domoticz/www/roborock_map.png"/>
        <param field="Mode3" label="Nom du robot (optionnel)" width="250px" default=""/>
        <param field="Mode5" label="Carte en direct pendant le nettoyage (s, 0 = non)" width="60px" default="20"/>
    </params>
</plugin>
"""
# Ce fichier joue deux roles :
#  - plugin Domoticz (importe par Domoticz)
#  - processus "worker" (lance par le plugin avec l'argument --worker), qui parle a python-roborock.
# Le worker tourne dans un processus separe pour que l'arret de Domoticz soit propre.
import gc
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

try:
    import Domoticz
except ImportError:          # mode worker
    Domoticz = None

MIN_MAJOR = 4                # python-roborock >= 4 (API device_manager)

# ---------- Unites Domoticz ----------
U_ACTION, U_FAN, U_WATER = 1, 2, 3
U_BATT, U_STATUS = 4, 5
U_TIME, U_AREA, U_LAST = 6, 7, 8
U_CNT, U_TTIME, U_TAREA = 9, 10, 11
U_ROOMS, U_MAPBTN = 12, 13
U_MAIN, U_SIDE, U_FILTER, U_SENSOR = 14, 15, 16, 17
U_FLOOR = 18
U_AUTODUST = 19
U_CYCLES = 20
U_ALERT = 21
U_EVENT = 22
U_BILAN = 23

ACTION_NAMES = "Off|Demarrer|Pause|Stop|Retour base|Localiser|Vider bac|Stop vidange"
ACTIONS = {10: "start", 20: "pause", 30: "stop", 40: "home", 50: "find", 60: "dust", 70: "stopdust"}
FAN = {10: 101, 20: 102, 30: 103, 40: 104}          # silencieux, equilibre, turbo, max
WATER = {10: 200, 20: 201, 30: 202, 40: 203}        # coupee, faible, moyen, fort
LIFE_H = {"main": 300, "side": 200, "filter": 150, "sensor": 30}

STATES = {
    1: "Demarrage", 2: "Chargeur deconnecte", 3: "Inactif", 4: "Telecommande",
    5: "Nettoyage", 6: "Retour a la base", 7: "Mode manuel", 8: "En charge",
    9: "Probleme de charge", 10: "En pause", 11: "Nettoyage local", 12: "Erreur",
    13: "Arret", 14: "Mise a jour", 15: "Accostage", 16: "Vers la cible",
    17: "Nettoyage de zone", 18: "Nettoyage de piece", 22: "Vidage du bac",
    23: "Lavage vadrouille", 26: "Vers le lavage", 100: "Charge terminee",
    101: "Hors ligne",
}
ACTIVE = {5, 6, 11, 15, 16, 17, 18, 22, 23, 26}
DOCKED = {8, 100}


def to_m2(v):
    try:
        return round(float(v or 0) / 1e6, 1)
    except Exception:
        return 0.0


MAX_RSS_MB = 150             # le worker redemarre tout seul s'il depasse cette memoire
RECYCLE_S = 24 * 3600        # redemarrage preventif du worker (robot au repos), contre la derive memoire


def trim_memory():
    """Demande a la libc de rendre au systeme la memoire libre (Linux/glibc)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def rss_mb():
    """Memoire residente du processus courant (Mo), lue dans /proc."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def label(v):
    """Nom lisible d'un code d'erreur (enum python-roborock) ou 'code N'."""
    n = getattr(v, "name", None)
    return str(n).replace("_", " ") if n else "code %s" % code_of(v)


def code_of(v):
    try:
        return int(getattr(v, "value", v))
    except Exception:
        return -1


# =====================================================================
#  PARTIE WORKER (processus separe)
# =====================================================================
class FatalError(Exception):
    """Erreur de configuration: inutile de redemarrer automatiquement."""


def emit(kind, data):
    sys.stdout.write(json.dumps({"k": kind, "d": data}) + "\n")
    sys.stdout.flush()


def _xy(p):
    if isinstance(p, dict):
        return float(p["x"]), float(p["y"])
    return float(p.x), float(p.y)


def _affine(pts):
    """pts: 3 tuples (x, y, u) -> coefficients (a, b, c) tels que u = a*x + b*y + c."""
    (x1, y1, u1), (x2, y2, u2), (x3, y3, u3) = pts
    det = x1 * (y2 - y3) - y1 * (x2 - x3) + (x2 * y3 - x3 * y2)
    a = (u1 * (y2 - y3) - y1 * (u2 - u3) + (u2 * y3 - u3 * y2)) / det
    b = (x1 * (u2 - u3) - u1 * (x2 - x3) + (x2 * u3 - x3 * u2)) / det
    c = (x1 * (y2 * u3 - y3 * u2) - y1 * (x2 * u3 - x3 * u2) + u1 * (x2 * y3 - x3 * y2)) / det
    return a, b, c


def _flatten_path(items):
    """Le trajet peut etre une liste de points ou une liste de segments (listes de points)."""
    pts = []
    for e in items or []:
        if isinstance(e, (list, tuple)) and len(e) == 2 and all(isinstance(v, (int, float)) for v in e):
            pts.append((float(e[0]), float(e[1])))
        elif isinstance(e, (list, tuple)):
            pts.extend(_flatten_path(e))
        elif hasattr(e, "x") and hasattr(e, "y"):
            pts.append((float(e.x), float(e.y)))
        elif isinstance(e, dict) and "x" in e and "y" in e:
            pts.append((float(e["x"]), float(e["y"])))
    return pts


def draw_overlay(png, map_data, diag):
    """Dessine le trajet et la position du robot sur l'image de la carte."""
    import io
    from PIL import Image, ImageDraw
    cal = map_data.calibration()
    triples = []
    for p in cal[:3]:
        g = p["vacuum"] if isinstance(p, dict) else p.vacuum
        m = p["map"] if isinstance(p, dict) else p.map
        triples.append((_xy(g), _xy(m)))
    cx = _affine([(v[0], v[1], m[0]) for v, m in triples])
    cy = _affine([(v[0], v[1], m[1]) for v, m in triples])

    def to_img(x, y):
        return cx[0] * x + cx[1] * y + cx[2], cy[0] * x + cy[1] * y + cy[2]

    path = getattr(map_data, "path", None)
    raw = getattr(path, "path", None) or []
    pts = _flatten_path(raw)
    pos = getattr(map_data, "vacuum_position", None)
    img =Image.open(io.BytesIO(png)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w = max(2, img.width // 300)
    if len(pts) >= 2:
        line = [to_img(p[0], p[1]) for p in pts]
        draw.line(line, fill=(230, 40, 40, 255), width=w)
    if pos is not None:
        x, y = to_img(float(pos.x), float(pos.y))
        r = w * 4
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(40, 200, 80, 255), outline=(255, 255, 255, 255), width=w)
    out = io.BytesIO()
    img.save(out, format="PNG")
    img.close()
    return out.getvalue()


class Worker:
    def __init__(self, cfg, cmds, stop_evt):
        self.cfg, self.cmds, self.stop = cfg, cmds, stop_evt
        self.v1 = None
        self.manager = None
        self.prev_code = -1
        self.prev_err = 0
        self.fail = 0
        self.diag_done = False
        self.map_logged = False

    async def get_user_data(self):
        from roborock.web_api import RoborockApiClient
        try:
            from roborock.data import UserData
        except ImportError:
            from roborock.containers import UserData
        cache, idfile = self.cfg["cache"], self.cfg["idfile"]
        if os.path.exists(cache):
            try:
                with open(cache) as f:
                    return UserData.from_dict(json.load(f)), True
            except Exception:
                pass
        api = RoborockApiClient(username=self.cfg["user"])
        code = self.cfg["code"].strip()
        if not code:
            raise FatalError("Aucune session enregistree. Ecrire 'demander' dans le champ "
                             "'Code email' du materiel, puis cliquer sur Mettre a jour.")
        if code.lower() == "demander":
            try:
                await api.request_code()
            except Exception as e:
                raise FatalError("Demande de code impossible: %s" % e)
            ident = getattr(api, "_device_identifier", None)
            if ident:
                with open(idfile, "w") as f:
                    f.write(str(ident))
            else:
                emit("err", "Identifiant d'appareil introuvable dans python-roborock: la validation du code risque d'echouer")
            raise FatalError("Code envoye par email a %s. Attendre l'email, remplacer 'demander' par "
                             "le code recu dans le champ 'Code email', puis cliquer sur Mettre a jour." % self.cfg["user"])
        # Le code est lie a l'identifiant d'appareil du client: on reprend celui de la demande.
        try:
            with open(idfile) as f:
                saved = f.read().strip()
            if saved and hasattr(api, "_device_identifier"):
                api._device_identifier = saved
        except Exception:
            emit("err", "Aucune demande de code enregistree: ecrire 'demander' d'abord")
        try:
            user_data = await api.code_login(code)
        except Exception as e:
            raise FatalError("Code refuse (%s). Ecrire 'demander' pour en recevoir un nouveau." % e)
        try:
            with open(cache, "w") as f:
                json.dump(user_data.as_dict(), f)
        except Exception as e:
            emit("err", "Session non enregistree (%s): un nouveau code sera demande" % e)
        return user_data, False

    async def connect(self):
        from roborock.devices.device_manager import create_device_manager, UserParams
        user_data, _ = await self.get_user_data()
        try:
            self.manager = await create_device_manager(
                UserParams(username=self.cfg["user"], user_data=user_data))
            devices = await self.manager.get_devices()
        except Exception as e:
            raise Exception("%s (si la session a expire, supprimer roborock_user.json "
                            "puis ecrire 'demander' dans Code email)" % e)
        vacs = [d for d in devices if getattr(d, "v1_properties", None)]
        if not vacs:
            raise FatalError("Aucun robot compatible sur ce compte")
        dev = vacs[0]
        wanted = self.cfg["name"].strip().lower()
        for d in vacs:
            if wanted and d.name.lower() == wanted:
                dev = d
        self.v1 = dev.v1_properties
        emit("log", "Connecte a %s (local: %s)" % (dev.name, getattr(dev, "is_local_connected", "?")))

    async def guard(self, fn, *a):
        try:
            await fn(*a)
        except Exception as e:
            emit("err", "%s: %s" % (fn.__name__, e))

    def events(self, state, err, err_label, s):
        """Detecte les changements d'etat / d'erreur et emet des messages lisibles."""
        if self.prev_code != -1 and state != self.prev_code:
            emit("log", "Etat: %s -> %s (code %d -> %d)" % (
                STATES.get(self.prev_code, self.prev_code), STATES.get(state, state), self.prev_code, state))
            if state == 6 and self.prev_code in (5, 11, 17, 18):
                emit("event", "Nettoyage termine, retour a la station")
            elif state in DOCKED and self.prev_code in ACTIVE:
                emit("event", "Arrive a la station, en charge" if state == 8 else "Charge terminee")
            elif state == 10:
                emit("event", "Nettoyage en pause")
            elif state == 5 and self.prev_code in DOCKED:
                emit("event", "Nettoyage demarre")
            elif state == 22:
                emit("event", "Vidage du bac en cours")
        if err > 0 and err != self.prev_err:
            emit("log", "Erreur: %s (code %d)" % (err_label, err))
            emit("event", "Erreur: %s" % err_label)
        elif err == 0 and self.prev_err > 0:
            emit("event", "Erreur resolue")
        self.prev_err = err

    async def fast_refresh(self):
        await self.v1.status.refresh()
        s = self.v1.status
        state = code_of(s.state)
        err = code_of(s.error_code)
        emit("status", {
            "state": STATES.get(state, "Etat %s" % state),
            "battery": int(s.battery or 0),
            "time": int((s.clean_time or 0) // 60),
            "area": to_m2(s.clean_area),
            "error": label(s.error_code) if err > 0 else "",
            "dock_error": label(s.dock_error_status) if code_of(getattr(s, "dock_error_status", None)) > 0 else "",
            "code": state,
            "fan": code_of(s.fan_power),
            "water": code_of(s.water_box_mode),
            "floor": (code_of(s.map_status) >> 2) & 3 if s.map_status is not None else -1,
            "autodust": code_of(getattr(s, "auto_dust_collection", None)),
        })
        self.events(state, err, label(s.error_code), s)
        if self.prev_code in ACTIVE and state in DOCKED:
            emit("finished", {"m2": to_m2(s.clean_area), "min": int((s.clean_time or 0) // 60)})
            await self.guard(self.slow_refresh)
            await self.guard(self.do_map)
        self.prev_code = state

    async def step(self, name, fn, tries=3, quiet=False):
        """Execute une etape avec quelques essais. Un timeout (robot en veille, Wi-Fi) n'est
        journalise qu'en information (rien du tout si quiet); les autres etapes continuent."""
        import asyncio
        last = None
        for i in range(tries):
            try:
                await fn()
                return True
            except Exception as e:
                last = e
                if i < tries - 1:
                    await asyncio.sleep(3)
        msg = str(last).lower()
        if any(s in msg for s in ("timed out", "not connected", "could not publish")):
            if not quiet:
                emit("log", "%s: robot injoignable (%s), nouvel essai au prochain cycle" % (name, last))
        else:
            emit("err", "%s: %s" % (name, last))
        return False

    async def fast_poll(self):
        """Lecture du statut. Un timeout isole est ignore; apres 5 echecs de suite (environ 3 min),
        le robot est declare injoignable (message unique), puis de nouveau joignable."""
        if await self.step("statut", self.fast_refresh, tries=2, quiet=True):
            if self.fail >= 5:
                emit("event", "Robot de nouveau joignable")
            self.fail = 0
        else:
            self.fail += 1
            if self.fail == 5:
                emit("event", "Robot injoignable (pas de reponse depuis plusieurs minutes)")
            elif self.fail == 10:
                emit("log", "Toujours pas de reponse du robot, reconnexion complete")

    async def slow_refresh(self):
        from datetime import datetime
        v1 = self.v1

        async def summary():
            await v1.clean_summary.refresh()
            cs = v1.clean_summary
            emit("summary", {
                "count": int(cs.clean_count or 0),
                "time_h": round((cs.clean_time or 0) / 3600, 1),
                "area": to_m2(cs.clean_area),
            })
            r = getattr(cs, "last_clean_record", None)
            if r is not None:
                begin = getattr(r, "begin", 0) or 0
                emit("last", "%s - %d min - %s m2 - %s" % (
                    datetime.fromtimestamp(begin).strftime("%d/%m %H:%M"),
                    int((getattr(r, "duration", 0) or 0) // 60),
                    to_m2(getattr(r, "area", 0)),
                    "termine" if getattr(r, "complete", 1) else "interrompu"))

        async def consumables():
            await v1.consumables.refresh()
            c = v1.consumables

            def left(attr, key):
                used = (getattr(c, attr, 0) or 0) / 3600.0
                return max(0, round(100 - used / LIFE_H[key] * 100))
            emit("cons", {
                "main": left("main_brush_work_time", "main"),
                "side": left("side_brush_work_time", "side"),
                "filter": left("filter_work_time", "filter"),
                "sensor": left("sensor_dirty_time", "sensor"),
            })

        async def rooms():
            await v1.rooms.refresh()
            out = []
            for m in (v1.rooms.rooms or []):
                name = getattr(m, "raw_name", None) or getattr(m, "name", None) or "Piece %s" % m.segment_id
                out.append((int(m.segment_id), str(name)))
            emit("rooms", out)

        async def floors():
            await v1.maps.refresh()
            emit("floors", [(int(i.map_flag), str(i.name).strip()) for i in (v1.maps.map_info or [])])

        await self.step("historique", summary)
        await self.step("consommables", consumables)
        await self.step("pieces", rooms)
        await self.step("etages", floors)

    async def do_map(self):
        await self.v1.map_content.refresh()
        data = self.v1.map_content.image_content
        if not data:
            emit("err", "Carte vide")
            return
        try:
            data = draw_overlay(data, self.v1.map_content.map_data, self.diag_done)
            self.diag_done = True
        except Exception as e:
            emit("err", "Trajet non dessine: %s" % e)
        path = self.cfg["map"]
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        del data
        gc.collect()
        trim_memory()

    async def handle(self, item):
        import asyncio
        t = item.get("t")
        if t == "cmd":
            try:
                await self.v1.command.send(item["name"], item.get("params"))
            except Exception as e:
                emit("err", "Commande %s refusee par le robot: %s" % (item["name"], e))
                await self.fast_poll()
                return
            await asyncio.sleep(2)
            await self.fast_poll()
        elif t == "map":
            await self.guard(self.do_map)
        elif t == "floor":
            try:
                await self.v1.maps.set_current_map(item["flag"])
            except Exception as e:
                emit("err", "Changement d'etage refuse par le robot (%s). Il doit etre arrete, "
                            "sur sa base et localise sur sa carte." % e)
                await self.fast_poll()     # remet le selecteur sur l'etage reel
                return
            await asyncio.sleep(4)
            await self.guard(self.slow_refresh)
            await self.fast_poll()
            await self.guard(self.do_map)

    async def main(self):
        import asyncio
        await self.connect()
        await self.guard(self.slow_refresh)
        await self.fast_poll()
        if not os.path.exists(self.cfg["map"]):
            await self.guard(self.do_map)
        last_fast = last_slow = last_map = started = time.time()
        while not self.stop.is_set():
            try:
                item = self.cmds.get_nowait()
            except queue.Empty:
                item = None
            if item:
                try:
                    await self.handle(item)
                except Exception as e:
                    emit("err", "Commande: %s" % e)
            now = time.time()
            if now - last_fast >= self.cfg["interval"]:
                await self.fast_poll()
                last_fast = now
            if now - last_slow >= 900:
                await self.guard(self.slow_refresh)
                last_slow = now
            if self.fail >= 10:          # connexion perdue et non retablie: on repart de zero
                emit("recycle", None)
                return
            late = now - started - RECYCLE_S
            if late >= 0 and (self.prev_code not in ACTIVE or late >= 3 * 3600):
                emit("log", "Redemarrage preventif du processus Roborock (etat %s)" % STATES.get(self.prev_code, self.prev_code))
                emit("recycle", None)
                return
            if rss_mb() > MAX_RSS_MB and self.prev_code not in ACTIVE:
                emit("recycle", None)
                return
            live = self.cfg["map_live"]
            if live > 0 and self.prev_code in ACTIVE and time.time() - last_map >= live:
                await self.guard(self.do_map)
                last_map = time.time()
            await asyncio.sleep(1)


def run_worker():
    import asyncio
    cfg = json.loads(sys.stdin.readline())
    cmds, stop = queue.Queue(), threading.Event()

    def stdin_reader():
        for line in sys.stdin:
            try:
                cmds.put(json.loads(line))
            except Exception:
                pass
        stop.set()          # le plugin a ferme le tube: on s'arrete
    threading.Thread(target=stdin_reader, daemon=True).start()

    code = 0
    try:
        asyncio.run(Worker(cfg, cmds, stop).main())
    except FatalError as e:
        emit("fatal", str(e))
    except Exception as e:
        emit("err", "Worker arrete: %s" % e)
        code = 1
    sys.stdout.flush()
    os._exit(code)


# =====================================================================
#  PARTIE PLUGIN DOMOTICZ
# =====================================================================
def run_cmd(cmd, holder, timeout=1800):
    """Lance une commande; holder[0] recoit le Popen pour pouvoir l'arreter."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    holder[:] = [p]
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    return p.returncode, out or ""


def lib_major(exe):
    try:
        r = subprocess.run([exe, "-c", "from importlib.metadata import version;"
                            "print(version('python-roborock').split('.')[0])"],
                           capture_output=True, text=True, timeout=60)
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0


def prepare(home, out_q, holder):
    """Prepare l'environnement Python dedie (thread separe, plusieurs minutes la premiere fois)."""
    try:
        py = shutil.which("python3")
        if not py:
            raise RuntimeError("python3 introuvable")
        ver = subprocess.run([py, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True).stdout.strip()
        if tuple(int(x) for x in ver.split(".")) < (3, 11):
            raise RuntimeError("Python %s detecte: python-roborock demande 3.11 ou plus" % ver)

        venv = os.path.join(home, "venv")
        exe = os.path.join(venv, "bin", "python")
        system = False
        if not os.path.exists(exe):
            out_q.put(("log", "Creation de l'environnement Python dedie..."))
            rc, out = run_cmd([py, "-m", "venv", venv], holder, 600)
            if rc != 0:
                out_q.put(("err", "venv impossible (%s), utilisation de python3 systeme" % out[-200:]))
                exe, system = py, True
        elif not os.path.exists(exe):
            exe, system = py, True

        if lib_major(exe) < MIN_MAJOR:
            out_q.put(("log", "Installation de python-roborock (plusieurs minutes sur RPi)..."))
            base = [exe, "-m", "pip", "install", "--upgrade", "python-roborock"]
            ok, last = False, ""
            for extra in ((["--break-system-packages"], []) if system else ([],)):
                rc, out = run_cmd(base + extra, holder)
                if rc == 0:
                    ok = True
                    break
                last = out[-400:]
            if not ok:
                raise RuntimeError("Echec pip: %s" % last)
        out_q.put(("ready", exe))
    except Exception as e:
        out_q.put(("err", str(e)))
        out_q.put(("ready", None))


def reader(proc, out_q):
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                out_q.put((m["k"], m["d"]))
            except Exception:
                out_q.put(("log", "worker: " + line[:200]))
    except Exception:
        pass
    out_q.put(("exit", None))


class BasePlugin:
    def __init__(self):
        self.out_q = queue.Queue()
        self.holder = []
        self.proc = None
        self.exe = None
        self.threads = []
        self.cfg = {}
        self.stopping = False
        self.fatal = False
        self.recycling = False
        self.restart_at = 0
        self.rooms = []
        self.floors = []
        self.room_key = ""
        self.floor_key = ""
        self.err_txt = self.dock_txt = self.cons_txt = self.last_evt = ""

    # --- helpers ---
    def upd(self, unit, nvalue, svalue):
        if unit in Devices and (Devices[unit].nValue != nvalue or Devices[unit].sValue != str(svalue)):
            Devices[unit].Update(nValue=nvalue, sValue=str(svalue))

    def sel(self, unit, level):
        self.upd(unit, 1 if level else 0, level)

    def make(self, unit, name, **kw):
        if unit not in Devices:
            Domoticz.Device(Name=name, Unit=unit, Used=1, **kw).Create()

    def selector(self, unit, name, names):
        opts = {"LevelActions": "|" * (len(names.split("|")) - 1),
                "LevelNames": names, "LevelOffHidden": "true", "SelectorStyle": "0"}
        self.make(unit, name, TypeName="Selector Switch", Switchtype=18, Options=opts)

    def custom(self, unit, name, label):
        self.make(unit, name, TypeName="Custom", Options={"Custom": "1;" + label})

    def refresh_alert(self):
        """Alerte Domoticz: erreur robot > erreur base > consommable use > dernier evenement."""
        if self.err_txt:
            level, txt = 4, "Erreur: " + self.err_txt
        elif self.dock_txt:
            level, txt = 3, "Base: " + self.dock_txt
        elif self.cons_txt:
            level, txt = 2, self.cons_txt
        elif self.last_evt:
            level, txt = 1, self.last_evt
        else:
            level, txt = 1, "OK"
        if U_ALERT in Devices and (Devices[U_ALERT].nValue != level or Devices[U_ALERT].sValue != txt):
            Devices[U_ALERT].Update(nValue=level, sValue=txt)

    def cycles(self):
        try:
            return max(1, min(3, int(Devices[U_CYCLES].sValue) // 10))
        except Exception:
            return 1

    def send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            Domoticz.Error("Commande non envoyee: %s" % e)

    # --- cycle de vie ---
    def onStart(self):
        self.selector(U_ACTION, "Roborock Action", ACTION_NAMES)
        if U_ACTION in Devices and Devices[U_ACTION].Options.get("LevelNames") != ACTION_NAMES:
            Devices[U_ACTION].Update(nValue=0, sValue="0", Options={
                "LevelActions": "|" * (len(ACTION_NAMES.split("|")) - 1),
                "LevelNames": ACTION_NAMES, "LevelOffHidden": "true", "SelectorStyle": "0"})
        self.make(U_AUTODUST, "Roborock Vidange auto", TypeName="Switch", Switchtype=0)
        self.make(U_ALERT, "Roborock Alerte", TypeName="Alert")
        self.make(U_EVENT, "Roborock Dernier message", TypeName="Text")
        self.make(U_BILAN, "Roborock Bilan nettoyage", TypeName="Text")
        self.selector(U_CYCLES, "Roborock Cycles", "Off|1 cycle|2 cycles|3 cycles")
        if U_CYCLES in Devices and Devices[U_CYCLES].sValue in ("", "0"):
            Devices[U_CYCLES].Update(nValue=1, sValue="10")
        self.selector(U_FAN, "Roborock Aspiration", "Off|Silencieux|Equilibre|Turbo|Max")
        self.selector(U_WATER, "Roborock Eau", "Off|Coupee|Faible|Moyen|Fort")
        self.selector(U_ROOMS, "Roborock Pieces", "Off|Aucune")
        self.selector(U_FLOOR, "Roborock Etage", "Off|Aucun")
        self.make(U_BATT, "Roborock Batterie", TypeName="Percentage")
        self.make(U_STATUS, "Roborock Statut", TypeName="Text")
        self.custom(U_TIME, "Roborock Duree", "min")
        self.custom(U_AREA, "Roborock Surface", "m2")
        self.make(U_LAST, "Roborock Dernier nettoyage", TypeName="Text")
        self.custom(U_CNT, "Roborock Nb nettoyages", "")
        self.custom(U_TTIME, "Roborock Temps total", "h")
        self.custom(U_TAREA, "Roborock Surface totale", "m2")
        self.make(U_MAPBTN, "Roborock Maj carte", TypeName="Switch", Switchtype=9)
        self.custom(U_MAIN, "Roborock Brosse principale", "%")
        self.custom(U_SIDE, "Roborock Brosse laterale", "%")
        self.custom(U_FILTER, "Roborock Filtre", "%")
        self.custom(U_SENSOR, "Roborock Capteurs", "%")

        home = Parameters["HomeFolder"]
        try:
            interval = max(10, int(Parameters["Mode1"]))
        except Exception:
            interval = 30
        try:
            map_live = max(0, int(Parameters["Mode5"]))
            if 0 < map_live < 10:
                map_live = 10
        except Exception:
            map_live = 20
        self.cfg = {"user": Parameters["Username"], "interval": interval, "map_live": map_live,
                    "map": Parameters["Mode2"], "name": Parameters["Mode3"],
                    "code": Parameters["Mode4"],
                    "cache": os.path.join(home, "roborock_user.json"),
                    "idfile": os.path.join(home, "roborock_login_id.txt")}
        self.write_map_page()
        Domoticz.Heartbeat(10)
        t = threading.Thread(target=prepare, args=(home, self.out_q, self.holder), daemon=True)
        t.start()
        self.threads.append(t)

    def write_map_page(self):
        """Page personnalisee Domoticz affichant la carte (menu Personnalise)."""
        try:
            www = os.path.dirname(self.cfg["map"])
            if os.path.isdir(www):
                with open(os.path.join(www, "roborock.html"), "w") as f:
                    f.write('<!doctype html><meta charset="utf-8">'
                            '<meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<title>Roborock</title>'
                            '<body style="margin:0">'
                            '<img id="m" src="%(img)s" style="width:100%%;height:auto;display:block">'
                            '<script>setInterval(function(){document.getElementById("m").src="%(img)s?t="+Date.now()},10000)</script>'
                            '</body>\n' % {"img": os.path.basename(self.cfg["map"])})
            tpl = os.path.join(www, "templates")
            if os.path.isdir(tpl):
                with open(os.path.join(tpl, "Roborock.html"), "w") as f:
                    f.write('<iframe src="roborock.html" style="width:100%;aspect-ratio:1/1;border:0"></iframe>\n')
        except Exception as e:
            Domoticz.Log("Page carte non creee: %s" % e)

    def start_worker(self):
        home = Parameters["HomeFolder"]
        script = os.path.join(home, "plugin.py")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        errlog = open(os.path.join(home, "roborock_worker.log"), "w")
        self.proc = subprocess.Popen([self.exe, script, "--worker"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=errlog, text=True,
                                     bufsize=1, env=env)
        self.proc.stdin.write(json.dumps(self.cfg) + "\n")
        self.proc.stdin.flush()
        t = threading.Thread(target=reader, args=(self.proc, self.out_q), daemon=True)
        t.start()
        self.threads.append(t)

    def onStop(self):
        self.stopping = True
        for p in self.holder:
            try:
                p.kill()
            except Exception:
                pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        for t in self.threads:
            t.join(3)

    def onHeartbeat(self):
        if self.restart_at and time.time() >= self.restart_at and not self.stopping:
            self.restart_at = 0
            Domoticz.Log("Redemarrage du processus Roborock")
            self.start_worker()
        while True:
            try:
                kind, d = self.out_q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                Domoticz.Log(d)
            elif kind == "err":
                Domoticz.Error(d)
            elif kind == "fatal":
                self.fatal = True
                Domoticz.Error(d)
            elif kind == "ready":
                if d:
                    self.exe = d
                    self.start_worker()
                else:
                    Domoticz.Error("Installation impossible, voir les erreurs ci-dessus. Redemarrer Domoticz apres correction.")
            elif kind == "recycle":
                self.recycling = True
            elif kind == "exit":
                if not self.stopping and not self.fatal:
                    if self.recycling:
                        self.recycling = False
                        self.restart_at = time.time() + 3
                    else:
                        Domoticz.Error("Processus Roborock arrete, nouvel essai dans 60 s (voir roborock_worker.log)")
                        self.restart_at = time.time() + 60
            elif kind == "status":
                txt = d["state"]
                if d["error"]:
                    txt += " - ERREUR: " + d["error"]
                if U_STATUS in Devices and (Devices[U_STATUS].sValue != txt
                                            or getattr(Devices[U_STATUS], "BatteryLevel", None) != d["battery"]):
                    Devices[U_STATUS].Update(nValue=0, sValue=txt, BatteryLevel=d["battery"])
                self.upd(U_BATT, 0, d["battery"])
                self.err_txt, self.dock_txt = d["error"], d["dock_error"]
                self.refresh_alert()
                self.upd(U_TIME, 0, d["time"])
                self.upd(U_AREA, 0, d["area"])
                if d["autodust"] in (0, 1):
                    self.upd(U_AUTODUST, d["autodust"], "On" if d["autodust"] else "Off")
                self.sel(U_FAN, {v: k for k, v in FAN.items()}.get(d["fan"], 0))
                self.sel(U_WATER, {v: k for k, v in WATER.items()}.get(d["water"], 0))
                for i, (flag, _) in enumerate(self.floors):
                    if flag == d["floor"]:
                        self.sel(U_FLOOR, (i + 1) * 10)
            elif kind == "summary":
                self.upd(U_CNT, 0, d["count"])
                self.upd(U_TTIME, 0, d["time_h"])
                self.upd(U_TAREA, 0, d["area"])
            elif kind == "event":
                txt = time.strftime("%H:%M") + " " + d
                Domoticz.Log("Evenement: " + d)
                self.upd(U_EVENT, 0, txt)
                self.last_evt = d
                self.refresh_alert()
            elif kind == "finished":
                self.upd(U_BILAN, 0, "%s m2, %s min" % (d["m2"], d["min"]))
            elif kind == "last":
                self.upd(U_LAST, 0, d)
            elif kind == "cons":
                self.upd(U_MAIN, 0, d["main"])
                self.upd(U_SIDE, 0, d["side"])
                self.upd(U_FILTER, 0, d["filter"])
                self.upd(U_SENSOR, 0, d["sensor"])
                names = {"main": "brosse principale", "side": "brosse laterale",
                         "filter": "filtre", "sensor": "capteurs"}
                low = ["%s %d%%" % (names[k], v) for k, v in d.items() if v <= 10]
                self.cons_txt = ("A remplacer: " + ", ".join(low)) if low else ""
                self.refresh_alert()
            elif kind == "rooms":
                self.rooms = d
                self.set_names(U_ROOMS, "room_key", [n for _, n in d], "Aucune")
            elif kind == "floors":
                self.floors = d
                self.set_names(U_FLOOR, "floor_key", [n for _, n in d], "Aucun")

    def set_names(self, unit, key_attr, names, empty):
        key = "|".join(names)
        if key == getattr(self, key_attr) or unit not in Devices:
            return
        setattr(self, key_attr, key)
        lvl = "Off|" + (key if key else empty)
        opts = {"LevelActions": "|" * (len(lvl.split("|")) - 1), "LevelNames": lvl,
                "LevelOffHidden": "true", "SelectorStyle": "0"}
        Devices[unit].Update(nValue=0, sValue="0", Options=opts)

    # --- commandes ---
    def onCommand(self, Unit, Command, Level, Color):
        if not self.proc or self.proc.poll() is not None:
            Domoticz.Error("Robot non connecte, commande ignoree")
            return
        if Unit == U_ACTION and ACTIONS.get(Level) == "start" and self.cycles() > 1 and self.rooms:
            # nettoyage complet avec plusieurs cycles: toutes les pieces de l'etage, avec repetition
            self.send({"t": "cmd", "name": "app_segment_clean",
                       "params": [{"segments": [s for s, _ in self.rooms], "repeat": self.cycles()}]})
            self.sel(U_ACTION, 0)
        elif Unit == U_CYCLES:
            self.sel(U_CYCLES, Level)
        elif Unit == U_ACTION and Level in ACTIONS:
            name = {"start": "app_start", "pause": "app_pause", "stop": "app_stop",
                    "home": "app_charge", "find": "find_me",
                    "dust": "app_start_collect_dust",
                    "stopdust": "app_stop_collect_dust"}[ACTIONS[Level]]
            self.send({"t": "cmd", "name": name, "params": None})
            self.sel(U_ACTION, 0)
        elif Unit == U_AUTODUST:
            on = 1 if Command == "On" else 0
            self.send({"t": "cmd", "name": "set_dust_collection_switch_status", "params": {"status": on}})
            self.upd(U_AUTODUST, on, "On" if on else "Off")
        elif Unit == U_FAN and Level in FAN:
            self.send({"t": "cmd", "name": "set_custom_mode", "params": [FAN[Level]]})
            self.sel(U_FAN, Level)
        elif Unit == U_WATER and Level in WATER:
            self.send({"t": "cmd", "name": "set_water_box_custom_mode", "params": [WATER[Level]]})
            self.sel(U_WATER, Level)
        elif Unit == U_ROOMS:
            idx = Level // 10
            if 1 <= idx <= len(self.rooms):
                self.send({"t": "cmd", "name": "app_segment_clean",
                           "params": [{"segments": [self.rooms[idx - 1][0]], "repeat": self.cycles()}]})
            self.sel(U_ROOMS, 0)
        elif Unit == U_FLOOR:
            idx = Level // 10
            if 1 <= idx <= len(self.floors):
                self.send({"t": "floor", "flag": self.floors[idx - 1][0]})
                self.sel(U_FLOOR, Level)
        elif Unit == U_MAPBTN:
            self.send({"t": "map"})


_plugin = BasePlugin()


def onStart(): _plugin.onStart()
def onStop(): _plugin.onStop()
def onHeartbeat(): _plugin.onHeartbeat()
def onCommand(Unit, Command, Level, Color): _plugin.onCommand(Unit, Command, Level, Color)


if __name__ == "__main__" and "--worker" in sys.argv:
    run_worker()
