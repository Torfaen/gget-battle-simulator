"""Local manual controls for the original battle process."""

from dataclasses import dataclass, replace
from collections import Counter
import json
from math import ceil, floor, isfinite
import os
from queue import Empty, Queue
import re
import subprocess
from struct import error as StructError
from tempfile import NamedTemporaryFile, SpooledTemporaryFile
import threading
import time
from pathlib import Path
from zipfile import BadZipFile

from kernel_runner import ARTIFACTS, STAGE, prepare_guest
from battle_camera import BattleCamera, PITCH_COS, PITCH_SIN
from battle_save import (BATTLE_SAVES, MAX_TIMELINE, read_save_entry, read_saved_formation,
                         read_saved_supporters, unpack_save, write_save)
from export_mumu_snapshot import DEFAULT_OUTPUT, export_snapshot
from formation import (LAST_FORMATION, current_catalog, exported_mech_assets, formation_from_snapshot, formation_names,
                       load_sortie, save_team)
from prepare_stage import extract_additional_stage, prepared_stage_ids, stage_prepared
from stage_catalog import EXPANSION_STAGES, LANGUAGES, ROAD_HARD, STAGE_NAMES, STORY_EXPANSION, StageChoice, load_catalog
from android_bridge import prepare_mumu, remove_session


def configure_tk_runtime():
    runtime = ARTIFACTS / 'tk_runtime'
    for variable, folder, startup in (
        ('TCL_LIBRARY', 'tcl8.6', 'init.tcl'),
        ('TK_LIBRARY', 'tk8.6', 'tk.tcl'),
    ):
        directory = runtime / folder
        if (directory / startup).is_file():
            os.environ.setdefault(variable, folder)
    return runtime


def create_root():
    # Resolve the bundled Tcl paths from their own directory during interpreter creation.
    if not (TK_RUNTIME / 'tcl8.6/init.tcl').is_file():
        return tk.Tk()
    working_directory = Path.cwd()
    try:
        os.chdir(TK_RUNTIME)
        return tk.Tk()
    finally:
        os.chdir(working_directory)


TK_RUNTIME = configure_tk_runtime()
_startup_directory = Path.cwd()
try:
    if TK_RUNTIME.is_dir():
        os.chdir(TK_RUNTIME)
    import tkinter as tk
    from tkinter import font, messagebox, ttk
finally:
    os.chdir(_startup_directory)

from formation_ui import FormationEditor
from save_library import SaveLibrary, capture_preview

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = ImageTk = None


BG = '#0a121d'
PANEL = '#142435'
TEXT = '#eef6ff'
MUTED = '#9db1c8'
GRID = '#3c526a'
OWN = '#75baff'
ENEMY = '#ff8f89'
ALLY = '#72d5aa'
HP_TRACK = '#0a1422'
HP_LOSS = '#fff0d9'
HP_GAIN = '#bdf7cc'
EN_FILL = '#ffbd36'
EN_LOSS = '#ff796e'
EN_DIGITS = '#fff0ac'
IDLE_DIR = ARTIFACTS / 'idle_koma'
UI_ASSETS = ARTIFACTS / 'battle_ui_assets'
SUPPORTER_SKILL = -2
FACING_IDLE = {1: 'right', 2: 'back', 3: 'left', 4: 'front'}
STAGE_TERRAIN_ATMOSPHERIC = 2
UNIT_STANDBY_FLOAT = 1
MAP_SLOT_COLORS = (('#995137', '#683c3c', '#ffc393'), ('#426982', '#354a68', '#a4d6ff'))
OBJECTIVE_MARKERS = {1: ('#72d5aa', 'defense'), 2: ('#75baff', 'destination')}
MAP_TEXTURE_CELL = {'webp': 72, 'png': 28}
MAP_DRAG_THRESHOLD = 5
KOMA_SINGLE_SCALE = 1.50
KOMA_LARGE_SCALE = 1.30
KOMA_LIFT = 0.16
SUPPORT_BADGE_SCALE = 0.25
SUPPORT_BADGE_GAP_SCALE = 0.035
SUPPORT_CELL_ICON_SCALE = 0.34
DANGER_MARKERS = {23: ('danger_normal', 0.864), 57: ('danger_map', 1.04)}
AURA_MARKERS = {1: 'aura_1', 2: 'aura_2', 3: 'aura_3', 4: 'aura_4', 6: 'aura_6'}
ROLES = {1: '攻击型', 2: '防御型', 3: '支援型'}
WEAPON_ATTRIBUTES = ('物理', '光束', '特殊', '万能')
ATTACK_ATTRIBUTES = ('射击', '格斗', '觉醒')
BATTLE_ACTIONS = {'attack': '攻击', 'support': '支援攻击', 'counter': '反击',
                  'counter_support': '支援反击', 'defense': '支援防御', 'attack_defense': '支援防御',
                  'evade': '回避', 'guard': '防御', 'none': '不参加', 'unavailable': '无法行动'}
WEAPON_ACTIONS = ('attack', 'support', 'counter', 'counter_support')
STARTUP_MARKERS = (
    ('REUSE loading ', 4, '加载原版战斗库'),
    ('REUSE init_enter', 5, '初始化 IL2CPP'),
    ('REUSE master_database_bytes ', 6, '读取游戏主表'),
    ('REUSE scene_created ', 7, '正在部署地图与双方编队'),
    ('REUSE original_battle_context_ready ', 8, '应用关卡、光环和回合特性'),
    ('REUSE original_no_presentation_view_ready', 9, '初始化战斗规则'),
    ('REUSE original_task_completed own_phase_start ', 10, '读取首回合战场'),
)
WEAPON_UNAVAILABLE = {'Out of Range': '超出射程', 'Not Enough EN': 'EN 不足',
                      'Not Enough MP': 'MP 不足', 'Not Enough Tension': '气势不足'}
DEFEAT_REASONS = {1: '我方全灭', 2: '指定单位被击破', 3: '回合上限', 4: '到达指定地点', 5: '击破数条件'}


class BattleProcess:
    def __init__(self, stage, trace=False, *, invasion_level=0, use_mumu=False, timeline_ui=False, resume=None,
                 formation=None, opening=None, native_checkpoint=None, saved_supporters=None):
        self.events = Queue()
        self.lock = threading.Lock()
        self.stopped = False
        self.process = None
        self.log = None
        self.commands = Queue()
        self.use_mumu = use_mumu or native_checkpoint is not None or saved_supporters is not None
        self.formation = formation
        self.opening = opening
        self.native_checkpoint = native_checkpoint
        self.saved_supporters = saved_supporters
        self.save_destination = None
        self.save_preview = None
        self.save_stream = None
        self.thread = threading.Thread(target=self.run, args=(stage, trace, invasion_level, timeline_ui, resume),
                                       daemon=True)
        self.thread.start()

    def run(self, stage, trace, invasion_level, timeline_ui, resume):
        mumu_session = None
        def progress(step, label):
            self.events.put(('startup', (step, label)))

        try:
            progress(0, '检查所选关卡的原版资源')
            if stage != STAGE and not stage_prepared(stage):
                progress(0, '正在提取缺少的原版关卡资源')
                extract_additional_stage(stage, fetch=True, dependencies=False)
            if self.use_mumu:
                command, self.log, adb, session = prepare_mumu(
                    stage, trace, invasion_level=invasion_level, progress=progress, timeline_ui=timeline_ui,
                    resume=resume, formation=self.formation, native_checkpoint=self.native_checkpoint,
                    saved_supporters=self.saved_supporters)
                mumu_session = adb, session
            else:
                progress(1, '校验原版运行文件并准备启动环境')
                command, self.log, _ = prepare_guest('interactive', stage, trace_nuls=trace,
                                                      invasion_level=invasion_level, progress=progress,
                                                      timeline_ui=timeline_ui, resume=resume, formation=self.formation)
            progress(3, '启动 MuMu 内的独立战斗进程' if self.use_mumu else '启动独立 ARM64 战斗进程')
            with self.lock:
                if self.stopped:
                    return
                self.process = subprocess.Popen(command, cwd=self.log.parent, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self.write_commands, args=(self.process,), daemon=True).start()
            startup = True
            opening_lines = []
            with self.process as process, self.log.open('wb', buffering=0) as log:
                for line in process.stdout:
                    message = line.decode('utf-8', errors='replace').rstrip('\r\n')
                    if message.startswith('UI\tSAVE_'):
                        self.receive_save(message)
                        continue
                    log.write(line)
                    if message == 'UI\tREADY':
                        startup = False
                    if startup:
                        for prefix, step, label in STARTUP_MARKERS:
                            if message.startswith(prefix):
                                progress(step, label)
                                break
                    if self.opening is not None and message.startswith('UI\t'):
                        opening_lines.append(message)
                        if message == 'UI\tREADY':
                            try:
                                verify_imported_opening(self.opening, opening_lines)
                            except ValueError as exc:
                                process.terminate()
                                self.events.put(('error', f'{exc} · 日志 {self.log}'))
                                return
                            for buffered in opening_lines:
                                self.events.put(('line', buffered))
                            opening_lines.clear()
                            self.opening = None
                        continue
                    self.events.put(('line', message))
                self.events.put(('exit', process.wait()))
        except (OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
            self.events.put(('error', str(exc)))
        finally:
            if resume is not None:
                resume.unlink(missing_ok=True)
            if self.save_destination is not None:
                self.finish_save('战斗进程已退出，存档未写入')
            self.commands.put(None)
            if mumu_session:
                try:
                    remove_session(*mumu_session)
                except (OSError, subprocess.SubprocessError):
                    pass

    def send(self, command):
        with self.lock:
            if self.stopped or self.process is None or self.process.poll() is not None:
                return False
            self.commands.put((command + '\n').encode('ascii'))
            return True

    def save_to(self, path, *, preview=None):
        with self.lock:
            if self.stopped or self.process is None or self.process.poll() is not None or self.save_destination:
                return False
            self.save_destination = path
            self.save_preview = preview
            self.commands.put(b'save\n')
            return True

    def receive_save(self, message):
        if not self.save_destination:
            return
        try:
            _, event, *values = message.split('\t', 2)
            if event == 'SAVE_BEGIN':
                self.save_stream = SpooledTemporaryFile(max_size=2 * 1024 * 1024)
            elif event == 'SAVE_CHUNK' and self.save_stream is not None:
                chunk = bytes.fromhex(values[0])
                if self.save_stream.tell() + len(chunk) > MAX_TIMELINE:
                    raise ValueError('存档超出大小限制')
                self.save_stream.write(chunk)
            elif event == 'SAVE_END' and self.save_stream is not None:
                write_save(self.save_destination, self.save_stream, self.formation, preview=self.save_preview,
                           saved_supporters=self.saved_supporters)
                self.finish_save()
            elif event == 'SAVE_FAILED':
                self.finish_save('原版战斗状态保存失败')
        except (OSError, ValueError) as exc:
            self.finish_save(str(exc))

    def finish_save(self, error=None):
        with self.lock:
            path, self.save_destination = self.save_destination, None
            self.save_preview = None
        if self.save_stream is not None:
            self.save_stream.close()
            self.save_stream = None
        self.events.put(('saved', (path, error)))

    def write_commands(self, process):
        try:
            while (payload := self.commands.get()) is not None:
                if self.use_mumu:
                    process.stdin.write(payload)
                    process.stdin.flush()
                    continue
                # Leave room in the guest UART FIFO when several choices share a command.
                for offset in range(0, len(payload), 8):
                    if offset:
                        time.sleep(0.02)
                    process.stdin.write(payload[offset:offset + 8])
                    process.stdin.flush()
        except (OSError, ValueError):
            if not self.stopped:
                self.events.put(('error', '无法向原版战斗进程发送操作'))

    def stop(self, wait=False):
        with self.lock:
            self.stopped = True
            self.commands.put(None)
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
        if wait and self.process is not None:
            self.thread.join(timeout=2)


ALIVE_ON_MAP = (0, 1)  # UnitExistenceType.AliveStepReady and AliveStepEnd.


def verify_imported_opening(snapshot, lines):
    expected = {
        row['id']: (row['side'], row['x'], row['z'], int(row['master_unit_id']), row['direction'])
        for row in snapshot['units'] if row['existence_type'] in ALIVE_ON_MAP and 'x' in row and 'z' in row
    }
    units, directions, pilots = {}, {}, {}
    stage_id = turn = None
    for line in lines:
        _, event, *values = line.split('\t')
        if event == 'STATE':
            stage_id, turn = map(int, values[:2])
        elif event == 'UNIT':
            number, side, x, z = map(int, values[:4])
            units[number] = side, x, z, int(values[9])
        elif event == 'FOOTPRINT':
            number, direction = map(int, values[:2])
            directions[number] = direction
        elif event == 'UNIT_DETAIL':
            number, pilot = map(int, values[:2])
            pilots[number] = pilot
    if (stage_id, turn) != (snapshot['stage_id'], 1):
        raise ValueError('本地后端未能重建导入关卡的第 1 回合')
    actual = {number: (*row, directions.get(number)) for number, row in units.items()}
    if actual != expected:
        raise ValueError('本地后端生成的单位或站位与模拟器检查点不同')
    if any(pilots.get(row['id']) != int(row['character_id'])
           for row in snapshot['units'] if row['id'] in expected and 'character_id' in row):
        raise ValueError('本地后端生成的驾驶员与模拟器检查点不同')


@dataclass(frozen=True)
class UnitEffect:
    kind: int
    icon: str
    label: str
    detail: str


@dataclass(frozen=True)
class Unit:
    number: int
    side: int
    x: int
    z: int
    ready: int
    hp: int
    max_hp: int
    en: int
    mp: int
    master_id: int
    name: str
    direction: int = 0
    footprint: frozenset[tuple[int, int]] = frozenset()
    character: int = 0
    role: int = 0
    sp: int = 0
    support_attack: int = 0
    support_defense: int = 0
    barrier: int = 0
    post_attack_move: bool = False
    attack: int = 0
    max_en: int = 0
    manual: bool = False
    effects: tuple[UnitEffect, ...] = ()
    aura_effects: tuple[UnitEffect, ...] = ()
    sfs: str = ''
    sfs_floating: bool = False

    @property
    def cells(self):
        return self.footprint or frozenset(((self.x, self.z),))


def draw_resource_bar(canvas, box, current, predicted, maximum, color, *, loss_color=HP_LOSS, tags=()):
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    maximum = max(1, maximum)
    current_end, predicted_end = (
        left + round((right - left) * min(max(hp, 0), maximum) / maximum)
        for hp in (current, predicted)
    )
    canvas.create_rectangle(left - 1, top - 1, right + 1, bottom + 1, fill=HP_TRACK, width=0, tags=tags)
    remaining_end = min(current_end, predicted_end)
    if remaining_end > left:
        canvas.create_rectangle(left, top, remaining_end, bottom, fill=color, width=0, tags=tags)
    if current_end != predicted_end:
        canvas.create_rectangle(remaining_end, top, max(current_end, predicted_end), bottom,
                                fill=loss_color if predicted_end < current_end else HP_GAIN, width=0, tags=tags)


def draw_hp_bar(canvas, box, unit, color, forecast=None, *, tags=()):
    current, predicted = forecast[1:3] if forecast is not None else (unit.hp, unit.hp)
    draw_resource_bar(canvas, box, current, predicted, unit.max_hp, color, tags=tags)


def draw_bar_text(canvas, box, label, face, color=TEXT, *, tags=()):
    left, top, right, bottom = box
    x, y = (left + right) // 2, (top + bottom) // 2
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        canvas.create_text(x + dx, y + dy, text=label, fill=BG, font=face, tags=tags)
    canvas.create_text(x, y, text=label, fill=color, font=face, tags=tags)


def draw_en_bar(canvas, box, current, maximum, cost, face, *, tags=()):
    predicted = current if cost is None else current - cost
    draw_resource_bar(canvas, box, current, predicted, maximum or current, EN_FILL, loss_color=EN_LOSS, tags=tags)
    if cost is not None:
        draw_bar_text(canvas, box, f'−{cost:,} EN' if cost else '0 EN', face, EN_DIGITS, tags=tags)


class UnitCard(tk.Frame):
    def __init__(self, parent, color, family):
        super().__init__(parent, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.color = color
        self.columnconfigure(1, weight=1)
        portrait_box = tk.Frame(self, bg=BG, width=54, height=54)
        portrait_box.grid(row=0, column=0, rowspan=4, sticky='n', padx=(7, 8), pady=7)
        portrait_box.pack_propagate(False)
        self.portrait = tk.Label(portrait_box, bg=BG, borderwidth=0)
        self.portrait.pack(fill='both', expand=True)
        self.name = tk.Label(self, bg=PANEL, fg=color, anchor='w', font=(family, 11, 'bold'))
        self.name.grid(row=0, column=1, sticky='ew', padx=(0, 7), pady=(5, 0))
        self.pilot = tk.Label(self, bg=PANEL, fg=MUTED, anchor='w', font=(family, 8))
        self.pilot.grid(row=1, column=1, sticky='ew', padx=(0, 7))
        self.effects = tk.Text(self, width=24, height=1, bg=PANEL, fg=TEXT, relief='flat',
                               borderwidth=0, highlightthickness=0, wrap='word', font=(family, 8), cursor='arrow')
        self.effects.grid(row=2, column=1, sticky='ew', padx=(0, 5))
        self.effects_scroll = ttk.Scrollbar(self, command=self.effects.yview)
        self.effects_scroll.grid(row=2, column=2, sticky='ns', padx=(0, 7))
        self.effects.configure(yscrollcommand=self.effects_scroll.set)
        self.effects.configure(state='disabled')
        self.effects.tag_configure('buff', foreground='#8de7ef')
        self.effects.tag_configure('debuff', foreground='#ffad9c')
        self.effect_rows = ()
        self.effects.grid_remove()
        self.effects_scroll.grid_remove()
        self.vitals = tk.Label(self, bg=PANEL, fg=TEXT, anchor='w', justify='left', font=(family, 9, 'bold'))
        self.vitals.grid(row=3, column=1, columnspan=2, sticky='ew', padx=(0, 7))
        self.gauge = tk.Canvas(self, height=15, bg=HP_TRACK, highlightthickness=1, highlightbackground=GRID)
        self.gauge.grid(row=4, column=1, columnspan=2, sticky='ew', padx=(0, 8), pady=(2, 7))
        self.weapons_button = ttk.Button(self, text='查看武装')
        self.weapons_button.grid(row=5, column=1, sticky='w', padx=(0, 7), pady=(0, 6))
        self.gauge_state = None
        self.gauge_font = (family, 9, 'bold')
        self.gauge.bind('<Configure>', lambda _: self.draw_gauge())

    def draw_gauge(self):
        self.gauge.delete('all')
        if self.gauge_state is None:
            return
        unit, forecast, delta, energy, en_cost = self.gauge_state
        width, height = self.gauge.winfo_width(), self.gauge.winfo_height()
        split = round(width * 0.65)
        hp_box = (1, 1, split - 3, height - 1)
        en_box = (split + 2, 1, width - 1, height - 1)
        draw_hp_bar(self.gauge, hp_box, unit, self.color, forecast, tags=('hp_gauge',))
        if delta:
            draw_bar_text(self.gauge, hp_box, delta, self.gauge_font, tags=('hp_gauge',))
        draw_en_bar(self.gauge, en_box, *energy, en_cost, self.gauge_font, tags=('en_gauge',))

    def update_unit(self, unit, pilot, portrait, fallback, state, forecast=None, received=None, *, energy, en_cost):
        self.portrait.configure(image=portrait or fallback or '')
        self.name.configure(text=f'#{unit.number}  {unit.name}')
        self.pilot.configure(text='  ·  '.join(filter(None, (pilot, state))))
        effects = (*unit.aura_effects, *unit.effects)
        if effects != self.effect_rows:
            self.effect_rows = effects
            self.effects.configure(state='normal')
            self.effects.delete('1.0', 'end')
            for i, effect in enumerate(effects):
                kind = 'buff' if effect.kind == 1 else 'debuff'
                self.effects.insert('end', ('\n' if i else '') + ('▲ ' if effect.kind == 1 else '▼ ') + effect.label, kind)
                if effect.detail:
                    self.effects.insert('end', f'  {effect.detail}')
            self.effects.configure(state='disabled', height=max(1, min(5, 3 * len(unit.aura_effects) +
                                                                     2 * len(unit.effects))))
        for widget in (self.effects, self.effects_scroll):
            widget.grid() if effects else widget.grid_remove()
        barrier = f'  护罩 {unit.barrier:,}' if unit.barrier else ''
        en, max_en = energy
        en_text = f'{en:,}/{max_en:,}' if max_en else f'{en:,}'
        resources = f'EN {en_text}  ·  MP {unit.mp}  ·  SP {unit.sp}'
        health = f'HP {unit.hp:,}/{unit.max_hp:,}{barrier}'
        delta = ''
        if forecast is not None:
            _, current, predicted, loss, _ = forecast
            damage = (received[0] if received else 0) or loss
            health = f'HP {current:,} → {predicted:,}'
            if predicted > current:
                delta = f'+{predicted - current:,}'
            elif damage:
                delta = f'−{damage:,}'
        self.vitals.configure(text=f'{health}\n{resources}')
        self.gauge_state = unit, forecast, delta, energy, en_cost
        self.draw_gauge()


class BattleWindow:
    def __init__(self, root, trace=False):
        self.root = root
        self.trace = trace
        self.backend = None
        self.formation_to_remember = None
        self.import_results = Queue()
        self.importing_mumu = False
        self.imported_opening = False
        self.turn = 0
        self.width = self.height = 0
        self.units = {}
        self.previous_units = {}
        self.moves = set()
        self.support_cells = set()
        self.danger_cells = {kind: set() for kind in DANGER_MARKERS}
        self.aura_types = {}
        self.aura_cells = {}
        self.aura_preview_unit = 0
        self.weapons = {}
        self.selectable_weapons = {}
        self.selectable_weapon_ranges = {}
        self.reading_weapon = None
        self.weapon_estimates = {}
        self.weapon_info = {}
        self.weapon_effects = {}
        self.weapon_catalog = {}
        self.weapon_catalog_owner = None
        self.weapon_catalog_panel = None
        self.weapon_power = {}
        self.confirming_attack = False
        self.forecast = None
        self.forecast_damage = {}
        self.battle_received = {}
        self.battle_order = []
        self.battle_actions = {}
        self.support_candidates = {}
        self.support_plan_active = False
        self.support_editor = None
        self.support_actions = {}
        self.support_power = {}
        self.support_action_unit = None
        self.support_action_weapon = None
        self.attack_defense_candidates = {}
        self.attack_defense_selection = None
        self.attack_defense_hp = None
        self.defense_candidates = {}
        self.defense_actions = None
        self.counter_weapons = []
        self.counter_support = {}
        self.counter_support_weapons = {}
        self.defense_battle_actions = {}
        self.defense_damage = {}
        self.defense_received = {}
        self.defense_outline = {}
        self.defense_energy = {}
        self.defense_pending = False
        self.defense_latest = None
        self.defense_requested = None
        self.defense_render = None
        self.map_weapons = {}
        self.map_weapon = None
        self.map_preview = None
        self.map_effect = set()
        self.map_overlap = set()
        self.map_taps = set()
        self.map_targets = set()
        self.map_hp_forecast = {}
        self.map_points = set()
        self.map_edit_slot = None
        self.map_slots = {}
        self.map_slot_effects = {}
        self.incoming = None
        self.pilot_skills = {}
        self.unit_skill = None
        self.supporter_skill = None
        self.skill_selection = None
        self.skill_manual_target = False
        self.skill_point = None
        self.skill_candidates = set()
        self.skill_targets = set()
        self.skill_range = set()
        self.small_images = {}
        self.board_badge_images = {}
        self.selected = self.target = 0
        self.moved_unit = 0
        self.selecting_weapon = False
        self.pending_command = ''
        self.pending_move = None
        self.after_read = None
        self.queued_review_back = False
        self.last_action_error = ''
        self.auto_respond = False
        self.auto_phase_label = ''
        self.estimated_targets = set()
        self.estimates_cache = {}
        self.option_cache = {}
        self.pending_state = False
        self.ready = self.undo_available = False
        self.battle_running = False
        self.undo_count = 0
        self.rewind_at_settlement = False
        self.focus_loaded_board = False
        self.pending_result = self.settlement = None
        self.outcome = None
        self.initial_enemies = set()
        self.layout = (0, 0, 1)
        self.map_camera = None
        self.map_focus = None
        self.map_area = (0, 0, 0, 0)
        self.map_objectives = {}
        self.map_pins = set()
        self.zoom = 1.0
        self.map_offset = (0, 0)
        self.fit_board = False
        self.pan_start = None
        self.pan_dragging = False
        self.idle_size = 0
        self.idle_images = {}
        self.board_idle_images = {}
        self.mounted_sources = {}
        self.mounted_images = []
        try:
            self.idle_index = json.loads((IDLE_DIR / 'unit_index.json').read_bytes())
        except (OSError, ValueError):
            self.idle_index = {}
        try:
            self.sfs_geometry = json.loads((IDLE_DIR / 'sfs_geometry.json').read_bytes())
        except (OSError, ValueError):
            self.sfs_geometry = {}
        self.status = tk.StringVar(value='选择关卡后开始战斗')
        self.startup_detail = tk.StringVar()
        self.startup_step = tk.StringVar()
        self.turn_label = tk.StringVar()
        self.mode_label = tk.StringVar(value='关卡选择')
        self.objective_label = tk.StringVar()
        self.action_label = tk.StringVar()
        self.map_background = None
        self.map_background_key = None
        self.map_background_image = None
        self.map_texture_cell = 0
        self.map_camera_zoom = None
        self.map_reference_height = None
        self.map_fixed_backdrop = False
        self.map_asset = ''
        self.map_origin = (0, 0)
        self.stage_category = tk.StringVar()
        self.stage_group = tk.StringVar()
        self.stage = tk.StringVar()
        self.use_mumu = tk.BooleanVar(value=True)
        self.weapon_label = tk.StringVar()
        self.forecast_label = tk.StringVar(value='选择武装后查看预判')
        self.skill_label = tk.StringVar()
        self.unit_skill_label = tk.StringVar()
        self.supporter_skill_label = tk.StringVar()
        self.skill_preview_label = tk.StringVar()
        self.map_weapon_label = tk.StringVar()
        self.map_status = tk.StringVar()
        self.map_slot_label = tk.StringVar()
        try:
            names = json.loads(LANGUAGES.read_bytes())
        except (OSError, ValueError):
            names = {}
        try:
            cached_names = json.loads((UI_ASSETS / 'master_names.json').read_bytes())
        except (OSError, ValueError):
            cached_names = {}
        for table, entries in cached_names.items():
            names[table] = entries | names.get(table, {})
        try:
            self.stage_catalog = load_catalog(names)
        except (OSError, ValueError, KeyError):
            self.stage_catalog = {}
            self.status.set('完整关卡目录读取失败，请检查原版关卡表')
        new_stages = {row.stage_id for groups in self.stage_catalog.values() for choices in groups.values()
                      for row in choices}
        hard_stages = [StageChoice(f'{STAGE_NAMES.get(key, "永恒之路")}  ·  {key}', key)
                       for key in prepared_stage_ids() if key not in new_stages and key not in EXPANSION_STAGES]
        if hard_stages:
            self.stage_catalog.setdefault(ROAD_HARD, {}).setdefault('', []).extend(hard_stages)
        self.stage_catalog[STORY_EXPANSION] = {
            '': [StageChoice(label, stage_id) for stage_id, label in EXPANSION_STAGES.items()]
        }
        self.unit_names = names.get('m_unit', {})
        self.weapon_names = names.get('m_weapon', {})
        self.weapon_trait_text = names.get('m_weapon_trait', {})
        try:
            effects = json.loads((UI_ASSETS / 'unit_effect_text.json').read_bytes())
        except (OSError, ValueError):
            effects = {}
        self.trait_type_names = effects.pop('trait_type_names', {})
        self.trait_percent_types = set(effects.pop('trait_percent_types', []))
        self.unit_skill_effects = effects.pop('unit_skill_effects', {})
        self.unit_effect_text = effects
        self.character_names = names.get('m_character', {})
        self.unit_skill_text = names.get('m_unit_skill', {})
        try:
            self.character_roles = json.loads((UI_ASSETS / 'character_roles.json').read_bytes())
        except (OSError, ValueError):
            self.character_roles = {}
        try:
            self.skill_names = json.loads((UI_ASSETS / 'skill_names.json').read_bytes())
        except (OSError, ValueError):
            self.skill_names = {}
        self.build_window()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(40, self.poll)

    def change_stage_category(self, _=None):
        groups = self.stage_catalog.get(self.stage_category.get(), {})
        group_names = tuple(name for name in groups if name)
        self.stage_group_picker.configure(values=group_names)
        self.stage_group.set(next(iter(groups), ''))
        if group_names:
            self.stage_group_picker.grid()
            self.stage_picker.configure(width=12)
        else:
            self.stage_group_picker.grid_remove()
            self.stage_picker.configure(width=32)
        self.change_stage_group()

    def change_stage_group(self, _=None):
        groups = self.stage_catalog.get(self.stage_category.get(), {})
        self.stage_choices = {row.label: row for row in groups.get(self.stage_group.get(), ())}
        self.stage_ids = {label: choice.stage_id for label, choice in self.stage_choices.items()}
        self.stage_picker.configure(values=tuple(self.stage_choices))
        default = next((label for label, stage_id in self.stage_ids.items() if stage_id == STAGE),
                       next(iter(self.stage_choices), ''))
        self.stage.set(default)

    def find_stage(self, stage_id, level):
        for category, groups in self.stage_catalog.items():
            for group, choices in groups.items():
                for choice in choices:
                    if (choice.stage_id, choice.invasion_level) == (stage_id, level):
                        return category, group, choice

    def select_stage(self, stage_id, level):
        if location := self.find_stage(stage_id, level):
            category, group, choice = location
            self.stage_category.set(category)
            self.change_stage_category()
            self.stage_group.set(group)
            self.change_stage_group()
            self.stage.set(choice.label)
            return choice

    def build_window(self):
        self.root.title('SD 高达 G 世代 永恒 · 战斗模拟')
        width = min(1334, max(920, self.root.winfo_screenwidth() - 60))
        height = min(805, max(620, self.root.winfo_screenheight() - 100))
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(920, 590)
        self.root.configure(bg=BG)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        installed = font.families(self.root)
        family = 'Inter' if 'Inter' in installed else 'Microsoft YaHei UI'
        self.font = (family, 10)
        heading = (family, 13, 'bold')
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Battle.Horizontal.TProgressbar', background='#3b9df4', troughcolor='#0c1825',
                        bordercolor=GRID, lightcolor='#3b9df4', darkcolor='#3b9df4')
        style.configure('TButton', padding=(8, 5), background='#1b344e', foreground=TEXT, font=self.font)
        style.map('TButton', background=[('active', '#285984'), ('disabled', PANEL)], foreground=[('disabled', '#8193aa')])
        style.configure('Primary.TButton', padding=(12, 8), background='#1168b5', foreground='white',
                        font=(family, 12, 'bold'))
        style.map('Primary.TButton', background=[('active', '#177fda'), ('disabled', '#192c42')])
        style.configure('BattleStart.TButton', padding=(12, 8), background='#ac4918', foreground='white',
                        font=(family, 12, 'bold'))
        style.map('BattleStart.TButton', background=[('active', '#c96121'), ('disabled', '#4c3a35')])
        style.configure('SelectedWeapon.TButton', padding=(8, 5), background='#1168b5',
                        foreground='white', font=self.font)
        style.map('SelectedWeapon.TButton', background=[('active', '#177fda'), ('disabled', '#192c42')])
        style.configure('UnavailableWeapon.TButton', padding=(8, 5), background=PANEL, foreground=MUTED,
                        font=self.font)
        style.map('UnavailableWeapon.TButton', background=[('active', '#22384c')])
        style.configure('SelectedUnavailableWeapon.TButton', padding=(8, 5), background='#33506a',
                        foreground=TEXT, font=self.font)
        style.map('SelectedUnavailableWeapon.TButton', background=[('active', '#3d607e')])
        style.configure('TCombobox', fieldbackground=PANEL, background=PANEL, foreground=TEXT, font=self.font)
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', TEXT)])

        header = tk.Frame(self.root, bg=BG)
        header.grid(row=0, column=0, sticky='ew', padx=12, pady=8)
        header.columnconfigure(8, weight=1)
        self.stage_picker = ttk.Combobox(header, textvariable=self.stage, state='readonly', width=32, font=self.font)
        categories = ttk.Combobox(header, textvariable=self.stage_category, state='readonly', width=16,
                                  values=tuple(self.stage_catalog), font=self.font)
        categories.grid(row=0, column=0, padx=(0, 8))
        categories.bind('<<ComboboxSelected>>', self.change_stage_category)
        self.stage_group_picker = ttk.Combobox(header, textvariable=self.stage_group, state='readonly',
                                               width=22, font=self.font)
        self.stage_group_picker.grid(row=0, column=1, padx=(0, 8))
        self.stage_group_picker.bind('<<ComboboxSelected>>', self.change_stage_group)
        self.stage_picker.grid(row=0, column=2, padx=(0, 8))
        self.stage_category.set(ROAD_HARD if ROAD_HARD in self.stage_catalog else next(iter(self.stage_catalog), ''))
        self.change_stage_category()
        last_team = last_stage = None
        try:
            with LAST_FORMATION.open('rb') as source:
                last_team, last_stage = load_sortie(source)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.status.set('无法读取上次出击记录，已恢复默认编队')
        restored_stage = self.select_stage(*last_stage) if last_stage else None
        self.start_button = ttk.Button(header, style='BattleStart.TButton', text='出击', width=5, command=self.start)
        self.start_button.grid(row=0, column=3)
        ttk.Checkbutton(header, text='使用 MuMu 加速', variable=self.use_mumu).grid(row=0, column=4, padx=(8, 0))
        self.save_button = ttk.Button(header, text='存档', width=4, command=self.save_battle)
        self.save_button.grid(row=0, column=5, padx=(8, 0))
        self.load_button = ttk.Button(header, text='读档', width=4, command=self.load_battle)
        self.load_button.grid(row=0, column=6, padx=(4, 0))
        self.formation_button = ttk.Button(header, text='编队', width=4, command=self.show_formation)
        self.formation_button.grid(row=0, column=7, padx=(4, 0))
        self.import_mumu_button = ttk.Button(header, text='从 MuMu 导入战斗', command=self.import_mumu)
        self.import_mumu_button.grid(row=1, column=0, columnspan=3, sticky='w', pady=(5, 0))
        if not self.stage_choices:
            self.start_button.state(['disabled'])
            self.status.set('请先按 README 准备原版关卡资源')
        body = tk.Frame(self.root, bg=BG)
        body.grid(row=1, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg='#111d29', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.startup_panel = tk.Frame(body, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.label(self.startup_panel, textvariable=self.startup_detail, bg=PANEL,
                   wraplength=385).pack(fill='x', padx=14, pady=(12, 5))
        self.startup_bar = ttk.Progressbar(self.startup_panel, maximum=11, mode='determinate',
                                          style='Battle.Horizontal.TProgressbar')
        self.startup_bar.pack(fill='x', padx=14)
        self.label(self.startup_panel, textvariable=self.startup_step, bg=PANEL, fg=MUTED).pack(
            fill='x', padx=14, pady=(5, 10))
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.canvas.bind('<MouseWheel>', self.zoom_map)
        for button in (1, 2):
            self.canvas.bind(f'<ButtonPress-{button}>', self.begin_pan)
            self.canvas.bind(f'<B{button}-Motion>', self.pan_map)
            self.canvas.bind(f'<ButtonRelease-{button}>', self.end_pan)
        phase = tk.Frame(body, bg=BG, highlightbackground=OWN, highlightthickness=1)
        self.phase = phase
        phase.place(x=12, y=10, anchor='nw')
        self.label(phase, textvariable=self.mode_label, font=heading).grid(row=0, column=0, columnspan=2,
                                                                        sticky='w', padx=9, pady=(4, 0))
        self.label(phase, textvariable=self.turn_label, bg=OWN, fg=BG, font=(family, 9, 'bold')).grid(
            row=1, column=0, sticky='w', padx=(9, 5), pady=(3, 6))
        self.label(phase, textvariable=self.objective_label, fg=MUTED).grid(row=1, column=1, sticky='w', padx=(0, 9), pady=(3, 6))
        self.phase_tools = tk.Frame(body, bg=BG)
        self.pass_button = ttk.Button(self.phase_tools, text='回合结束', command=self.end_turn)
        self.pass_button.pack(side='left')
        self.undo_button = ttk.Button(self.phase_tools, text='行动回撤', command=self.rewind)
        self.undo_button.pack(side='left', padx=(4, 0))
        self.target_card = UnitCard(body, ENEMY, family)
        self.actor_card = UnitCard(body, OWN, family)
        self.unit_list_panel = tk.Frame(body, bg=PANEL, highlightbackground=OWN, highlightthickness=1)
        self.unit_switcher = tk.Frame(body, bg=BG, highlightbackground=GRID, highlightthickness=1)
        for label, offset in (('上一机体', -1), ('下一机体', 1)):
            ttk.Button(self.unit_switcher, text=label, width=8,
                       command=lambda d=offset: self.switch_unit(d)).pack(side='left', padx=2, pady=2)
        self.portrait = self.actor_card.portrait
        self.target_portrait = self.target_card.portrait

        panel = tk.Frame(body, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.command_panel = panel
        panel.columnconfigure(0, weight=1)
        skills = tk.Frame(panel, bg=PANEL)
        self.skills = skills
        skills.grid(row=2, column=0, sticky='ew', padx=8, pady=(6, 0))
        skills.columnconfigure(0, weight=1)
        self.skill_picker = ttk.Combobox(skills, textvariable=self.skill_label, width=11, font=self.font)
        self.skill_picker.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.skill_picker.bind('<<ComboboxSelected>>', lambda _: self.refresh_controls())
        self.skill_button = ttk.Button(skills, text='人物技能', command=self.use_character_skill)
        self.skill_button.grid(row=0, column=1)
        self.unit_skill_button = ttk.Button(skills, textvariable=self.unit_skill_label, command=self.use_unit_skill)
        self.unit_skill_button.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.supporter_skill_button = ttk.Button(skills, textvariable=self.supporter_skill_label,
                                                 command=self.use_supporter_skill)
        self.supporter_skill_button.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.skill_preview_panel = tk.Frame(skills, bg=PANEL)
        self.skill_preview_panel.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.label(self.skill_preview_panel, textvariable=self.skill_preview_label, bg=PANEL,
                   justify='left', wraplength=225).pack(fill='x')
        self.skill_confirm = ttk.Button(self.skill_preview_panel, text='确认技能', command=self.confirm_skill)
        self.skill_confirm.pack(side='left')
        self.skill_cancel = ttk.Button(self.skill_preview_panel, text='取消', command=self.cancel_skill)
        self.skill_cancel.pack(side='left', padx=(4, 0))
        weapon_row = tk.Frame(body, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.weapon_row = weapon_row
        self.weapon_picker = ttk.Combobox(weapon_row, textvariable=self.weapon_label, state='disabled', font=self.font,
                                         width=18)
        self.weapon_picker.bind('<<ComboboxSelected>>', lambda _: self.weapon_changed())
        self.weapon_cards = tk.Frame(weapon_row, bg=PANEL)
        self.weapon_cards.pack(fill='x', padx=4, pady=4)
        self.weapon_card_buttons = []
        self.weapon_card_values = ()
        self.weapon_detail = tk.Frame(panel, bg=PANEL)
        self.weapon_detail.grid(row=0, column=0, sticky='ew', padx=8, pady=(6, 4))
        self.weapon_detail_title = self.label(self.weapon_detail, bg=PANEL, fg=OWN, font=heading,
                                              justify='left', wraplength=300)
        self.weapon_detail_title.pack(anchor='w')
        self.weapon_detail_stats = self.label(self.weapon_detail, bg=PANEL, fg=MUTED, justify='left', wraplength=300)
        self.weapon_detail_stats.pack(anchor='w')
        self.weapon_detail_effects = self.label(self.weapon_detail, bg=PANEL, justify='left', wraplength=300)
        self.weapon_detail_effects.pack(anchor='w')
        forecast_panel = tk.Frame(panel, bg=PANEL)
        self.forecast_panel = forecast_panel
        forecast_panel.grid(row=1, column=0, sticky='ew', padx=8, pady=(3, 6))
        forecast_panel.columnconfigure(0, weight=1)
        self.label(forecast_panel, textvariable=self.forecast_label, bg=PANEL, wraplength=302,
                   justify='left').grid(row=0, column=0, sticky='w')
        self.forecast_rows = tk.Listbox(forecast_panel, bg=BG, fg=TEXT, selectbackground=GRID,
                                        relief='flat', highlightthickness=0, font=self.font, height=4, width=21)
        self.forecast_rows.grid(row=1, column=0, sticky='ew')
        self.forecast_scrollbar = ttk.Scrollbar(forecast_panel, command=self.forecast_rows.yview)
        self.forecast_scrollbar.grid(row=1, column=1, sticky='ns')
        self.forecast_rows.configure(yscrollcommand=self.forecast_scrollbar.set)
        self.order_bar = tk.Frame(body, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.order_canvas = tk.Canvas(self.order_bar, bg=PANEL, height=98, highlightthickness=0)
        self.order_canvas.pack(fill='x')
        self.order_scroll = ttk.Scrollbar(self.order_bar, orient='horizontal', command=self.order_canvas.xview)
        self.order_canvas.configure(xscrollcommand=self.order_scroll.set)
        self.support_slots = tk.Frame(self.order_canvas, bg=PANEL)
        self.order_canvas.create_window(0, 0, anchor='nw', window=self.support_slots)
        self.order_canvas.bind('<Configure>', self.resize_battle_order)
        self.support_slots.bind('<Configure>', self.resize_battle_order)
        self.order_signature = ()
        self.support_menu = tk.Frame(body, bg=BG, highlightbackground=OWN, highlightthickness=1)
        self.root.bind('<Escape>', self.on_back_key, add='+')
        self.defense_panel = tk.Frame(body, bg=PANEL, highlightbackground=OWN, highlightthickness=1)
        actions = tk.Frame(body, bg=BG, highlightbackground=GRID, highlightthickness=1)
        self.actions = actions
        actions.place(relx=1, rely=1, x=-12, y=-10, anchor='se')
        actions.columnconfigure((0, 1, 2), weight=1)
        self.attack_defense_button = ttk.Button(actions, text='反击援防', command=self.choose_attack_defense)
        self.attack_defense_button.grid(row=0, column=1, sticky='ew', padx=3, pady=(3, 0))
        self.attack_button = ttk.Button(actions, textvariable=self.action_label, style='Primary.TButton',
                                        width=10, command=self.primary_action)
        self.attack_button.grid(row=0, column=2, rowspan=2, sticky='nsew', padx=3, pady=3)
        self.next_target_button = ttk.Button(actions, text='切换目标', command=self.next_target)
        self.next_target_button.grid(row=0, column=0, sticky='ew', padx=3, pady=(3, 0))
        self.back_button = ttk.Button(actions, text='返回', command=self.back_to_unit)
        self.back_button.grid(row=1, column=0, sticky='ew', padx=3, pady=3)
        self.standby_button = ttk.Button(actions, text='待机', command=lambda: self.send(f's {self.selected}'))
        self.standby_button.grid(row=1, column=1, sticky='ew', padx=3, pady=3)
        map_controls = tk.Frame(panel, bg=PANEL)
        self.map_controls = map_controls
        map_controls.grid(row=3, column=0, sticky='ew', padx=8, pady=(6, 7))
        map_controls.columnconfigure(0, weight=1)
        self.map_picker = ttk.Combobox(map_controls, textvariable=self.map_weapon_label, width=14, font=self.font)
        self.map_picker.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.map_picker.bind('<<ComboboxSelected>>', lambda _: self.open_map_weapon())
        self.map_button = ttk.Button(map_controls, text='地图武装', command=self.open_map_weapon)
        self.map_button.grid(row=0, column=1, sticky='ew')
        self.map_tools = tk.Frame(map_controls, bg=PANEL)
        self.map_tools.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.map_tools.columnconfigure((0, 1, 2, 3), weight=1, uniform='map_controls')
        self.direction_buttons = []
        for column, (symbol, direction) in enumerate((('↑', 2), ('↓', 4), ('←', 3), ('→', 1))):
            button = ttk.Button(self.map_tools, text=symbol, width=2, command=lambda d=direction: self.map_command('r', d))
            button.grid(row=0, column=column, sticky='ew', padx=(0, 4) if column < 3 else 0)
            self.direction_buttons.append(button)
        self.label(self.map_tools, textvariable=self.map_status, bg=PANEL, fg=MUTED).grid(
            row=1, column=0, columnspan=4, sticky='w')
        self.map_slot_picker = ttk.Combobox(self.map_tools, textvariable=self.map_slot_label, width=12, state='readonly')
        self.map_slot_picker.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(3, 0))
        self.map_slot_picker.bind('<<ComboboxSelected>>', lambda _: self.map_command('s', self.map_slot_picker.current()))
        self.map_back_button = ttk.Button(self.map_tools, text='返回', width=0, command=self.cancel_map_weapon)
        self.map_back_button.grid(row=3, column=0, columnspan=2, sticky='ew', padx=(0, 4), pady=(4, 0))
        self.map_fire_button = ttk.Button(self.map_tools, text='发射', width=0, command=lambda: self.map_command('f'))
        self.map_fire_button.grid(row=3, column=2, columnspan=2, sticky='ew', pady=(4, 0))
        tools = tk.Frame(body, bg=BG, highlightbackground=GRID, highlightthickness=1)
        tools.place(x=12, rely=1, y=-10, anchor='sw')
        ttk.Button(tools, text='战斗记录', command=self.toggle_history).grid(row=0, column=0)
        ttk.Button(tools, text='复位视野', command=self.reset_view).grid(row=0, column=1, padx=(4, 0))
        self.label(tools, text='滚轮缩放 / 左键或中键拖动', fg=MUTED, font=(family, 8)).grid(row=0, column=2, padx=8)
        self.unit_list_button = ttk.Button(body, text='单位列表', command=self.show_unit_list)
        self.history_panel = tk.Frame(body, bg=PANEL, highlightbackground=GRID, highlightthickness=1)
        self.history = tk.Listbox(self.history_panel, bg=BG, fg=TEXT, selectbackground=GRID, relief='flat',
                                  highlightthickness=0, font=self.font, height=6, width=38, activestyle='none')
        self.history.pack(fill='both', expand=True, padx=6, pady=6)
        status_label = self.label(header, textvariable=self.status, fg=MUTED)
        status_label.grid(row=1, column=3, columnspan=6, sticky='e', padx=(10, 0), pady=(5, 0))
        self.battle_saves = SaveLibrary(body, BATTLE_SAVES, '.ggetsave', self.battle_save_entry, stage_column=True)
        self.formation_editor = None
        try:
            self.formation_editor = FormationEditor(body, self, self.hide_formation, team=last_team)
        except (OSError, ValueError, KeyError) as exc:
            self.status.set(f'无法读取原版编队资源 · {exc}')
            self.formation_button.state(['disabled'])
        if self.formation_editor:
            if restored_stage is None and self.formation_editor.errors:
                compatible = next((label for label, choice in self.stage_choices.items()
                                   if self.formation_editor.compatible(choice)), None)
                if compatible is not None:
                    self.stage.set(compatible)
            self.stage.trace_add('write', self.refresh_formation)
            self.show_formation()
        self.refresh_controls()

    def toggle_history(self):
        if self.history_panel.winfo_manager():
            self.history_panel.place_forget()
        else:
            self.history_panel.place(x=12, rely=1, y=-49, anchor='sw')
            self.history_panel.lift()

    def reset_view(self):
        self.cancel_pan()
        self.zoom = 1.0
        self.map_offset = (0, 0)
        self.map_focus = None
        self.fit_board = True
        self.draw()

    def focus_unit(self, unit, *, footprint=None):
        xs, zs = zip(*(footprint or unit.cells))
        center_x = (min(xs) + max(xs) + 1) / 2
        center_z = (min(zs) + max(zs) + 1) / 2
        if self.map_camera_zoom is not None:
            self.map_focus = center_x, center_z
            return
        cell = self.map_cell_size()
        left, bottom, right, top = self.active_map_area()
        self.map_offset = (round(((left + right) / 2 - center_x) * cell),
                           round((center_z - (bottom + top) / 2) * cell))

    def focus_enemy(self, number):
        unit = self.units.get(number)
        if unit and unit.side in (2, 3, 5, 6):
            self.cancel_pan()
            footprint = self.defense_outline.get(number, (0, ()))[1] if self.incoming else None
            self.focus_unit(unit, footprint=footprint)

    def active_map_area(self):
        left, bottom, right, top = self.map_area
        return self.map_area if left < right and bottom < top else (0, 0, self.width, self.height)

    def read_unit_effect(self, values):
        number, kind, key, source, trait_type, duration, remaining, value, aura = map(int, values[:9])
        unit = self.units[number]
        icon, name, description = values[9:]
        unit_skill_key = self.unit_skill_effects.get(f'{source}:{icon}')
        local_name, local_detail, local_icon = self.unit_effect_text.get(str(key or unit_skill_key), ('', '', ''))
        if not (local_name or local_detail) and source:
            row = self.unit_effect_text.get(str(source))
            if row and (not row[2] or row[2] == icon):
                local_name, local_detail, local_icon = row
        icon = icon or local_icon
        label = local_name or name or self.trait_type_names.get(str(trait_type), '增益' if kind == 1 else '减益')
        unit_label = '%' if trait_type in self.trait_percent_types else ''
        detail = local_detail.replace('{0}', str(abs(value))) or description or (f'{value:+d}{unit_label}' if value else '')
        remaining_text = ''
        if remaining >= 0 and duration in (1, 2):
            remaining_text = f"剩余 {remaining} {'回合' if duration == 1 else '次'}"
        effect = UnitEffect(kind, icon, label, ' · '.join(filter(None, (detail, remaining_text, '光环' if aura else ''))))
        if effect not in unit.effects:
            self.units[number] = replace(unit, effects=(*unit.effects, effect))

    def read_aura_effect(self, values):
        number, kind, aura_type, trait_id, trait_type, value, timing, minimum, maximum = map(int, values[:9])
        targets, name, description = values[9:]
        unit = self.units[number]
        local_name, local_detail, _ = self.unit_effect_text.get(str(trait_id), ('', '', ''))
        resource = {3: 'HP', 4: 'HP', 5: 'EN', 6: 'EN'}.get(trait_type)
        label = local_name or name or self.trait_type_names.get(str(trait_type), resource or '效果')
        suffix = '%' if trait_type in self.trait_percent_types or trait_type in (3, 5, 7, 9) else ''
        detail = (local_detail or description).replace('{0}', str(abs(value)))
        if aura_type in (1, 2) and resource:
            label = f'{resource}恢复'
            when = '所属阵营回合开始时，' if timing == 2 else ''
            detail = f'{when}恢复最大 {resource} 的 {value}%' if suffix else f'{when}恢复 {value} {resource}'
        elif value:
            detail = f'{value:+d}{suffix}'
        reach = str(minimum) if minimum == maximum else f'{minimum}–{maximum}'
        target = '同阵营或友好单位' if targets == 'AllyOrFriendly' else '范围内单位'
        label = f'光环 · {label}'
        effect = UnitEffect(2 if kind == 2 or value < 0 else 1, '', label,
                            ' · '.join(filter(None, (detail, f'{reach} 格范围', target))))
        if effect not in unit.aura_effects:
            self.units[number] = replace(unit, aura_effects=(*unit.aura_effects, effect))

    def own_units(self):
        return [unit for unit in self.units.values() if unit.manual]

    def switch_unit(self, offset):
        candidates = [unit.number for unit in self.own_units() if unit.ready or unit.post_attack_move]
        if self.ready and not self.moved_unit and candidates:
            if self.selected not in candidates:
                self.select_unit(candidates[0 if offset > 0 else -1])
            elif len(candidates) > 1:
                self.select_unit(candidates[(candidates.index(self.selected) + offset) % len(candidates)])

    def show_unit_list(self):
        if self.unit_list_panel.winfo_manager():
            self.unit_list_panel.place_forget()
            return
        panel = self.unit_list_panel
        for widget in panel.winfo_children():
            widget.destroy()
        heading = tk.Frame(panel, bg=PANEL)
        heading.pack(fill='x', padx=8, pady=6)
        self.label(heading, text='单位列表', bg=PANEL, fg=OWN).pack(side='left')
        ttk.Button(heading, text='返回', width=5, command=panel.place_forget).pack(side='right')
        units = self.own_units()
        listing = tk.Listbox(panel, bg=BG, fg=TEXT, font=self.font, height=min(len(units), 12), width=32)
        listing.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        for unit in units:
            state = '可行动' if unit.ready else '已行动'
            faction = '  ·  客军' if unit.side == 1 else ''
            listing.insert('end', f'{self.unit_name(unit.number)}{faction}  ·  {state}')

        def select(_event=None):
            selection = listing.curselection()
            if selection:
                panel.place_forget()
                self.select_unit(units[selection[0]].number)

        listing.bind('<Double-Button-1>', select)
        listing.bind('<Return>', select)
        ttk.Button(panel, text='选择', command=select).pack(anchor='e', padx=8, pady=(0, 6))
        panel.place(relx=1, rely=1, x=-12, y=-55, anchor='se')
        panel.lift()
        if units:
            listing.selection_set(0)
            listing.focus_set()

    def load_map_background(self, stage):
        self.map_background = self.map_background_image = self.map_background_key = None
        self.map_texture_cell = 0
        self.map_camera_zoom = None
        self.map_reference_height = None
        self.map_fixed_backdrop = False
        self.map_asset = ''
        self.map_origin = (0, 0)
        if ImageTk is None:
            return
        directory = UI_ASSETS / 'maps'
        frame = {}
        try:
            frame = json.loads((directory / f'{stage}.json').read_bytes())
            self.map_asset = frame.get('asset', '')
            self.map_origin = tuple(map(int, frame.get('background_origin', (0, 0))))
            zoom, reference_height = float(frame['camera_zoom']), float(frame['reference_height'])
            if isfinite(zoom) and zoom > 0 and isfinite(reference_height) and reference_height < 0:
                with Image.open(directory / f'{stage}-camera.webp') as source:
                    self.map_background = source.convert('RGB')
                self.map_camera_zoom = zoom
                self.map_reference_height = reference_height
                self.map_fixed_backdrop = bool(frame.get('fixed_backdrop'))
                return
        except (OSError, KeyError, TypeError, ValueError):
            pass
        for extension, pixels in MAP_TEXTURE_CELL.items():
            try:
                with Image.open(directory / f'{stage}.{extension}') as source:
                    self.map_background = source.convert('RGB')
                self.map_texture_cell = pixels
                return
            except OSError:
                continue

    def draw_camera_background(self, camera):
        source = self.map_background
        if source is None:
            return False
        width, height = camera.width, camera.height
        cover = max(width / source.width, height / source.height)
        if self.map_fixed_backdrop:
            scale = cover
            offset_x = offset_y = 0
        else:
            scale = max(cover, height * camera.zoom / (source.height * self.map_camera_zoom))
            # Translate the view at the planet's depth, where a board cell spans only a few pixels.
            origin_x, origin_z = self.map_origin
            x, y = camera.backdrop_offset(self.width / 2 + origin_x, self.height / 2 + origin_z,
                                          self.map_camera_zoom, self.map_reference_height)
            max_x, max_y = (source.width - width / scale) / 2, (source.height - height / scale) / 2
            offset_x = max(-max_x, min(max_x, round(x * source.height)))
            offset_y = max(-max_y, min(max_y, round(y * source.height)))
        key = width, height, scale, offset_x, offset_y
        if key != self.map_background_key:
            source_width = width / scale
            source_height = height / scale
            left = (source.width - source_width) / 2 + offset_x
            top = (source.height - source_height) / 2 + offset_y
            cropped = source.transform((width, height), Image.Transform.AFFINE,
                                       (1 / scale, 0, left, 0, 1 / scale, top),
                                       resample=Image.Resampling.BICUBIC, fillcolor=(20, 24, 43))
            self.map_background_image = ImageTk.PhotoImage(cropped, master=self.root)
            self.map_background_key = key
        self.canvas.create_image(0, 0, anchor='nw', image=self.map_background_image)
        return True

    def draw_map_background(self, left, top, cell):
        if self.map_background is None:
            return False
        extent = (self.width * cell, self.height * cell)
        # Only resize the visible part at high zoom.
        x0, y0 = max(0, -left), max(0, -top)
        x1 = min(extent[0], self.canvas.winfo_width() - left)
        y1 = min(extent[1], self.canvas.winfo_height() - top)
        if x1 <= x0 or y1 <= y0:
            return False
        key = extent, (x0, y0, x1, y1)
        if key != self.map_background_key:
            source = self.map_background
            pixels = self.map_texture_cell
            board_width, board_height = self.width * pixels, self.height * pixels
            if pixels and source.width >= board_width and source.height >= board_height:
                scale = pixels / cell
                origin = source.height - board_height
                box = (x0 * scale, origin + y0 * scale, x1 * scale, origin + y1 * scale)
            else:
                box = (x0 * source.width / extent[0], y0 * source.height / extent[1],
                       x1 * source.width / extent[0], y1 * source.height / extent[1])
            resampling = Image.Resampling.LANCZOS if extent[0] < source.width else Image.Resampling.BICUBIC
            resized = source.resize((x1 - x0, y1 - y0), resampling, box=box)
            self.map_background_image = ImageTk.PhotoImage(resized, master=self.root)
            self.map_background_key = key
        self.canvas.create_image(left + x0, top + y0, anchor='nw', image=self.map_background_image)
        return True

    def label(self, parent, **options):
        return tk.Label(parent, **({'bg': BG, 'fg': TEXT, 'font': self.font, 'anchor': 'w'} | options))

    def start(self):
        stage = self.stage_choices.get(self.stage.get())
        if not stage or self.importing_mumu or self.start_button.instate(['disabled']):
            return
        if not self.formation_editor:
            self.start_battle(stage)
            return
        self.show_formation()
        formation = self.formation_editor.deployment()
        if formation is not None:
            self.start_battle(stage, formation=formation)

    def refresh_formation(self, *_):
        if self.formation_editor and self.formation_editor.winfo_manager():
            self.formation_editor.edited()

    def show_formation(self):
        if not self.formation_editor or not self.can_load_battle():
            return
        self.battle_saves.close()
        self.formation_editor.place(relwidth=1, relheight=1)
        self.formation_editor.lift()
        self.formation_editor.refresh()

    def hide_formation(self):
        if self.formation_editor:
            self.formation_editor.team_saves.close()
            self.formation_editor.close_chooser()
            self.formation_editor.place_forget()

    def save_battle(self):
        if not self.ready or self.outcome or not self.backend:
            return
        self.battle_saves.close()
        self.hide_formation()
        preview = capture_preview(self.canvas.master)
        self.battle_saves.show_save('保存战斗', self.save_battle_to,
                                    f'{self.active_stage.stage_id}-T{self.turn}', preview)

    def save_battle_to(self, path, preview):
        if not self.ready or self.outcome or not self.backend or not self.backend.save_to(path, preview=preview):
            raise ValueError('当前无法保存战斗')
        self.ready = False
        self.pending_command = 'save'
        self.status.set('正在保存战斗和回撤历史')
        self.refresh_controls()

    def load_battle(self):
        if not self.can_load_battle():
            return
        self.battle_saves.show_load('读取战斗存档', self.load_battle_from)

    def import_mumu(self):
        if not self.formation_editor or not self.can_load_battle():
            return
        if self.battle_running and not messagebox.askyesno(
            '从 MuMu 导入战斗', '导入模拟器最近保存的战斗进度会替换当前前端对局。继续吗？',
            parent=self.root,
        ):
            return
        self.battle_saves.close()
        self.importing_mumu = True
        self.status.set('正在从 MuMu 读取战斗检查点')
        self.refresh_controls()
        local_team = tuple(self.formation_editor.team)

        def read():
            try:
                folder, _, snapshot = export_snapshot(DEFAULT_OUTPUT, read_progress=True)
                if error := snapshot.get('decode_error'):
                    raise ValueError(f'{error}\n原版检查点已保存到 {folder}')
                if snapshot.get('progress', {}).get('side', 0) != 0:
                    raise ValueError('检查点在敌方阶段，请等游戏进入我方阶段后再导入。')
                team = formation_from_snapshot(snapshot, local_team)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                self.import_results.put((str(exc), None, None, None))
            else:
                self.import_results.put((None, snapshot, team, folder))

        threading.Thread(target=read, name='mumu-battle-import', daemon=True).start()

    def finish_mumu_import(self, error, snapshot, team, folder):
        self.importing_mumu = False
        if error:
            self.status.set('MuMu 战斗导入失败')
            self.refresh_controls()
            messagebox.showerror('MuMu 战斗导入失败', error, parent=self.root)
            return
        stage_id = snapshot['stage_id']
        stage = self.select_stage(stage_id, 0) or StageChoice(snapshot.get('stage_name', str(stage_id)), stage_id)
        if snapshot['can_resume_native_battle']:
            supporters = {row['force']: (row['instance_id'], row['exp'], row['stars'])
                          for row in snapshot['supporters']}
            self.start_battle(stage, formation=team, native_checkpoint=folder / snapshot['source']['checkpoint_file'],
                              saved_supporters=supporters)
        else:
            self.start_battle(stage, formation=team, opening=snapshot)

    def battle_save_entry(self, path):
        preview, stage_id, level = read_save_entry(path)
        if location := self.find_stage(stage_id, level):
            category, group, choice = location
            return preview, ' · '.join(filter(None, (category, group, choice.label)))
        label = f'关卡 {stage_id}'
        if level:
            label += f' · 第 {level:02d} 层'
        elif level is None:
            label += ' · 楼层未知'
        return preview, label

    def load_battle_from(self, path):
        if not self.can_load_battle():
            raise ValueError('当前无法读取战斗存档')
        resume = None
        try:
            with NamedTemporaryFile(dir=ARTIFACTS, suffix='.timeline', delete=False) as output:
                resume = Path(output.name)
                stage_id, level = unpack_save(path, output)
            formation = read_saved_formation(path)
            supporters = read_saved_supporters(path)
            if supporters is not None and formation is None:
                raise ValueError('MuMu 存档缺少出击编队')
        except (OSError, ValueError, KeyError, StructError, BadZipFile, RuntimeError) as exc:
            if resume is not None:
                resume.unlink(missing_ok=True)
            raise ValueError(str(exc)) from exc
        stage = self.select_stage(stage_id, level) or StageChoice(str(stage_id), stage_id, level)
        self.start_battle(stage, resume=resume, formation=formation, saved_supporters=supporters)

    def can_load_battle(self):
        return not self.importing_mumu and bool(self.ready or self.outcome or not self.battle_running)

    def start_battle(self, stage, *, resume=None, formation=None, opening=None, native_checkpoint=None,
                     saved_supporters=None):
        self.battle_saves.close()
        self.hide_formation()
        self.close_weapon_catalog()
        if self.backend:
            self.backend.stop()
        self.defense_panel.place_forget()
        self.unit_list_panel.place_forget()
        self.incoming = None
        self.defense_pending = False
        self.defense_render = None
        self.auto_respond = False
        self.auto_phase_label = ''
        self.start_button.state(['disabled'])
        self.active_stage = stage
        self.battle_running = True
        self.imported_opening = opening is not None
        self.formation_to_remember = formation if resume is None and opening is None and native_checkpoint is None else None
        self.backend = BattleProcess(stage.stage_id, self.trace, invasion_level=stage.invasion_level,
                                     use_mumu=self.use_mumu.get(), timeline_ui=True, resume=resume,
                                     formation=formation, opening=opening, native_checkpoint=native_checkpoint,
                                     saved_supporters=saved_supporters)
        self.load_map_background(stage.stage_id)
        self.units = {}
        self.previous_units = {}
        self.initial_enemies.clear()
        self.history_panel.place_forget()
        self.turn = self.width = self.height = 0
        self.zoom = 1.0
        self.map_offset = (0, 0)
        self.map_camera = None
        self.map_focus = None
        self.map_area = (0, 0, 0, 0)
        self.map_objectives.clear()
        self.map_pins.clear()
        self.fit_board = False
        self.cancel_pan()
        self.selected = self.target = 0
        self.moved_unit = 0
        self.selecting_weapon = False
        self.pending_command = ''
        self.pending_move = None
        self.after_read = None
        self.queued_review_back = False
        self.last_action_error = ''
        self.sortie_rejected = []
        self.pending_result = self.settlement = self.outcome = None
        self.ready = self.undo_available = self.pending_state = False
        self.undo_count = 0
        self.rewind_at_settlement = False
        self.focus_loaded_board = False
        self.save_status = ''
        self.history.delete(0, 'end')
        self.clear_options()
        self.option_cache.clear()
        self.estimates_cache.clear()
        self.turn_label.set('')
        self.show_startup(0, '检查所选关卡的原版资源')
        self.refresh_controls()
        self.draw()

    def show_startup(self, step, label):
        self.status.set(label)
        self.startup_detail.set(label)
        self.startup_bar.configure(value=step + 1)
        self.startup_step.set(f'启动步骤 {step + 1} / 11')
        self.startup_panel.place(relx=0.5, rely=0.48, anchor='center', width=414)
        self.startup_panel.lift()

    def clear_options(self):
        self.moves.clear()
        self.support_cells.clear()
        self.aura_preview_unit = 0
        for cells in self.danger_cells.values():
            cells.clear()
        self.weapons.clear()
        self.selectable_weapons.clear()
        self.selectable_weapon_ranges.clear()
        self.reading_weapon = None
        self.weapon_estimates.clear()
        self.estimated_targets.clear()
        self.weapon_info.clear()
        self.weapon_effects.clear()
        self.weapon_power.clear()
        self.pilot_skills.clear()
        self.unit_skill = None
        self.supporter_skill = None
        self.clear_skill_preview()
        self.map_weapons.clear()
        self.clear_map_preview()
        self.clear_prediction()

    def clear_prediction(self):
        self.support_menu.place_forget()
        self.confirming_attack = False
        self.forecast = None
        self.forecast_damage.clear()
        self.battle_received.clear()
        self.battle_order.clear()
        self.battle_actions.clear()
        self.support_candidates.clear()
        self.support_plan_active = False
        self.support_editor = None
        self.support_actions.clear()
        self.support_power.clear()
        self.support_action_unit = None
        self.support_action_weapon = None
        self.attack_defense_candidates.clear()
        self.attack_defense_selection = None
        self.attack_defense_hp = None

    def close(self):
        if self.backend:
            self.backend.stop(wait=True)
        if self.formation_editor:
            self.formation_editor.close()
        self.root.destroy()

    def end_turn(self):
        self.auto_respond = False
        self.auto_phase_label = '正在推进回合'
        self.send('pass', self.auto_phase_label)

    def rewind(self):
        if self.outcome and self.rewind_at_settlement and self.backend.send('undo'):
            self.rewind_at_settlement = False
            self.pending_command = 'undo'
            self.status.set('正在恢复原版战斗状态')
            self.refresh_controls()
        elif self.ready:
            self.send('undo', '正在恢复原版战斗状态')

    def send(self, command, label='正在读取原版战斗状态'):
        if not self.ready or not self.backend:
            return False
        if not command.startswith('weapons '):
            self.close_weapon_catalog()
        self.ready = False
        sent = self.backend.send(command)
        if sent:
            self.pending_command = command
            self.last_action_error = ''
            self.status.set(label)
        else:
            self.status.set('战斗进程已退出')
        self.refresh_controls()
        return sent

    @property
    def browsing_weapons(self):
        return self.ready or self.pending_command.startswith(('e ', 'estimates '))

    @property
    def inspecting_move(self):
        return self.pending_command.startswith('inspect ') and bool(self.moved_unit)

    def poll(self):
        try:
            imported = self.import_results.get_nowait()
        except Empty:
            pass
        else:
            self.finish_mumu_import(*imported)
        if self.backend:
            while True:
                try:
                    kind, data = self.backend.events.get_nowait()
                except Empty:
                    break
                if kind == 'line':
                    self.handle_line(data)
                elif kind == 'startup':
                    self.show_startup(*data)
                elif kind == 'status':
                    self.status.set(data)
                elif kind == 'saved':
                    path, error = data
                    self.save_status = f'存档失败 · {error}' if error else f'已存档 · {path.name}'
                elif kind == 'error':
                    self.battle_running = False
                    self.startup_panel.place_forget()
                    self.defense_panel.place_forget()
                    self.unit_list_panel.place_forget()
                    self.ready = False
                    self.start_button.state(['!disabled'])
                    self.status.set(data)
                    self.refresh_controls()
                    self.draw()
                    if not self.width:
                        self.show_formation()
                elif kind == 'exit':
                    self.battle_running = False
                    self.startup_panel.place_forget()
                    self.defense_panel.place_forget()
                    self.unit_list_panel.place_forget()
                    self.ready = False
                    self.rewind_at_settlement = False
                    self.start_button.state(['!disabled'])
                    if self.backend.use_mumu and data == 0:
                        self.show_completed_battle()
                    if not self.outcome:
                        self.status.set(self.last_action_error or f'战斗进程结束。日志位于 {self.backend.log}')
                    self.refresh_controls()
                    self.draw()
                    if not self.width:
                        self.show_formation()
                        if self.sortie_rejected and self.formation_editor:
                            self.formation_editor.show_native_rejection(self.sortie_rejected)
        self.root.after(16 if self.pending_command or self.defense_pending else 40, self.poll)

    def handle_line(self, line):
        if line.startswith('UI\t'):
            _, event, *values = line.split('\t')
            if event == 'SORTIE_INVALID':
                self.sortie_rejected.append(tuple(map(int, values)))
                self.last_action_error = '原版部署检查拒绝出击'
            elif event in ('STATE', 'BOARD'):
                _, turn, self.width, self.height, undo, moved = map(int, values)
                self.map_objectives.clear()
                self.map_pins.clear()
                self.aura_types.clear()
                self.aura_cells.clear()
                self.previous_units = self.units
                self.units = {}
                if event == 'STATE':
                    self.close_weapon_catalog()
                    if self.outcome:
                        self.outcome = self.pending_result = self.settlement = None
                        self.rewind_at_settlement = False
                    self.incoming = None
                    self.auto_respond = False
                    self.defense_pending = False
                    self.defense_render = None
                    if turn != self.turn:
                        self.add_history(f'第 {turn} 回合 · 我方阶段')
                    self.turn = turn
                    self.undo_available = bool(undo)
                    self.undo_count = undo
                    self.moved_unit = moved
                    if moved:
                        self.selected = moved
                    self.clear_options()
                    self.option_cache.clear()
                    self.estimates_cache.clear()
                    self.target = 0
                    self.selecting_weapon = bool(self.moved_unit and self.selected == self.moved_unit)
                    self.pending_state = True
            elif event == 'BOARD_READY':
                if values:
                    self.focus_enemy(int(values[0]))
                self.record_hp_changes()
                self.refresh_controls()
                self.draw()
            elif event == 'SETTLED':
                self.undo_count = int(values[0])
                self.rewind_at_settlement = self.undo_available = self.undo_count > 0
                self.start_button.state(['!disabled'])
                self.show_completed_battle()
            elif event == 'LOADED':
                if self.backend.native_checkpoint is not None:
                    self.last_action_error = f'已从 MuMu 续战 · 第 {values[0]} 回合'
                    self.add_history('续战随机数由后端生成')
                else:
                    self.last_action_error = f'已读取存档 · 第 {values[0]} 回合'
                self.add_history(self.last_action_error)
                self.focus_loaded_board = True
            elif event == 'OPENING_ENEMY':
                self.initial_enemies.add(int(values[0]))
            elif event == 'LOAD_FAILED':
                self.last_action_error = '原版战斗进程无法恢复该存档'
                self.status.set(self.last_action_error)
            elif event == 'MAP_AREA':
                left, bottom, width, height = map(int, values[:4])
                self.map_area = (max(0, left), max(0, bottom),
                                 min(self.width, left + width), min(self.height, bottom + height))
                if self.map_asset and values[4] and values[4] != self.map_asset:
                    self.add_history(f'当前地图资源为 {values[4]}，对应背景尚未导出')
                    self.map_background = self.map_background_image = self.map_background_key = None
                    self.map_camera_zoom = None
                    self.map_asset = ''
            elif event == 'MAP_OBJECTIVE':
                kind, x, z = map(int, values)
                if kind in OBJECTIVE_MARKERS:
                    self.map_objectives[x, z] = kind
            elif event == 'MAP_PIN':
                kind, x, z = int(values[0]), float(values[1]), float(values[2])
                if kind in OBJECTIVE_MARKERS:
                    self.map_pins.add((kind, x, z))
            elif event == 'UNIT':
                key = int(values[9])
                name = self.unit_names.get(f'11010000{key:010d}') or values[10] or f'机体 {key}'
                unit = Unit(*map(int, values[:10]), name, manual=values[1] == '0')
                self.units[unit.number] = unit
            elif event == 'UNIT_MAX_EN':
                number, maximum = map(int, values)
                self.units[number] = replace(self.units[number], max_en=maximum)
            elif event == 'UNIT_MANUAL':
                number, manual = map(int, values)
                self.units[number] = replace(self.units[number], manual=bool(manual))
            elif event == 'UNIT_SFS':
                number, resource, standby, terrain = values
                number = int(number)
                self.units[number] = replace(self.units[number], sfs=resource,
                                             sfs_floating=int(standby) == UNIT_STANDBY_FLOAT and
                                             int(terrain) != STAGE_TERRAIN_ATMOSPHERIC)
            elif event == 'FOOTPRINT':
                number, direction, *coords = map(int, values)
                unit = self.units[number]
                self.units[number] = replace(unit, direction=direction,
                                             footprint=frozenset(zip(coords[::2], coords[1::2])))
            elif event == 'DEFENSE_FOOTPRINT':
                number, direction, *coords = map(int, values)
                self.defense_outline[number] = direction, frozenset(zip(coords[::2], coords[1::2]))
            elif event == 'DEFENSE_EN':
                number, en, maximum = map(int, values)
                self.defense_energy[number] = en, maximum
            elif event == 'UNIT_DETAIL':
                number, pilot, role, sp, attack, defense = map(int, values)
                self.units[number] = replace(self.units[number], character=pilot, role=role, sp=sp,
                                             support_attack=attack, support_defense=defense)
            elif event == 'SUPPORT_REMAINING':
                number, attack, defense = map(int, values)
                if number in self.units:
                    unit = self.units[number]
                    if (unit.support_attack, unit.support_defense) != (attack, defense):
                        self.units[number] = replace(unit, support_attack=attack, support_defense=defense)
                        self.draw()
            elif event == 'UNIT_TACTICAL':
                number, barrier, post_move, attack = map(int, values)
                self.units[number] = replace(self.units[number], barrier=barrier, post_attack_move=bool(post_move),
                                             attack=attack)
            elif event == 'UNIT_EFFECT':
                self.read_unit_effect(values)
            elif event == 'AURA_EFFECT':
                self.read_aura_effect(values)
            elif event == 'AURA_TYPE':
                number, kind = map(int, values)
                self.aura_types[number] = kind
            elif event == 'AURA_CELL':
                number, x, z = map(int, values)
                self.aura_cells.setdefault(number, set()).add((x, z))
            elif event == 'OPTIONS':
                self.clear_options()
            elif event == 'MOVE':
                point = tuple(map(int, values))
                actor = self.units.get(self.selected)
                if not actor or point != (actor.x, actor.z):
                    self.moves.add(point)
            elif event == 'SUPPORT_CELL':
                self.support_cells.add(tuple(map(int, values)))
            elif event == 'DANGER':
                kind, x, z = map(int, values)
                if kind in self.danger_cells:
                    self.danger_cells[kind].add((x, z))
            elif event == 'MOVE_READY':
                if self.pending_command.startswith('inspect '):
                    self.draw()
            elif event == 'WEAPON':
                target, index, key = map(int, values[:3])
                name = self.weapon_names.get(f'120100{key:012d}') or values[3] or f'武器 {key}'
                self.weapons.setdefault(target, {})[index] = (key, name)
                self.selectable_weapons.setdefault(key, name)
            elif event == 'SELECT_WEAPON':
                actor, key = map(int, values[:2])
                self.reading_weapon = key if actor == self.selected else None
                if self.reading_weapon is not None:
                    name = self.weapon_names.get(f'120100{key:012d}') or values[2] or f'武装 {key}'
                    self.selectable_weapons[key] = name
                    self.selectable_weapon_ranges[key] = set()
            elif event == 'SELECT_WEAPON_RANGE':
                if self.reading_weapon is not None:
                    self.selectable_weapon_ranges[self.reading_weapon].add(tuple(map(int, values)))
            elif event == 'WEAPON_INFO':
                actor, key, *info = map(int, values)
                self.weapon_info[actor, key] = tuple(info)
                self.weapon_effects[actor, key] = []
            elif event == 'UNIT_WEAPON':
                actor, key, is_map = map(int, values[:3])
                if actor == self.weapon_catalog_owner:
                    name = self.weapon_names.get(f'120100{key:012d}') or values[3] or f'武装 {key}'
                    self.weapon_catalog[key] = name, bool(is_map)
            elif event == 'WEAPON_EFFECT':
                actor, key, trait = map(int, values[:3])
                description = self.weapon_trait_text.get(f'140800{trait:012d}') or values[3]
                if description:
                    self.weapon_effects.setdefault((actor, key), []).append(description)
            elif event == 'WEAPON_TEXT':
                actor, key = map(int, values[:2])
                self.weapon_effects.setdefault((actor, key), []).append(values[2])
            elif event == 'MAP_WEAPON':
                key, kind, allowed, slots, ammo = map(int, values[:5])
                name = self.weapon_names.get(f'120100{key:012d}') or values[5] or f'武装 {key}'
                self.map_weapons[key] = kind, bool(allowed), slots, ammo, name
            elif event == 'MAP_PREVIEW':
                self.clear_map_range()
                self.map_preview = tuple(map(int, values))
            elif event == 'MAP_EFFECT':
                self.map_effect.add(tuple(map(int, values)))
            elif event == 'MAP_OVERLAP':
                self.map_overlap.add(tuple(map(int, values)))
            elif event == 'MAP_TAP':
                self.map_taps.add(tuple(map(int, values)))
            elif event == 'MAP_TARGET':
                self.map_targets.add(int(values[0]))
            elif event == 'MAP_HP':
                number, current, predicted = map(int, values)
                self.map_hp_forecast[number] = (0, current, predicted, max(0, current - predicted), 0)
            elif event == 'MAP_POINT':
                self.map_points.add(tuple(map(int, values)))
            elif event == 'MAP_EDIT':
                self.map_edit_slot = int(values[0])
            elif event == 'MAP_SLOT':
                slot, x, z = map(int, values)
                self.map_slots[slot] = (x, z)
            elif event == 'MAP_SLOT_EFFECT':
                slot, x, z = map(int, values)
                self.map_slot_effects.setdefault(slot, set()).add((x, z))
            elif event == 'MAP_USED':
                actor, _ = map(int, values)
                self.add_history(f'{self.unit_name(actor)} 使用地图武装')
            elif event == 'WEAPON_ESTIMATE':
                actor, target, weapon, damage, barrier, hit_rate = map(int, values)
                self.weapon_estimates[actor, target, weapon] = damage, barrier, hit_rate
                if (actor, target, weapon) == self.attack_selection():
                    self.refresh_controls()
                    self.draw()
            elif event == 'WEAPON_POWER':
                actor, target, weapon, power, en = map(int, values)
                self.weapon_power[actor, target, weapon] = power, en
                if (actor, target, weapon) == self.attack_selection():
                    self.refresh_controls()
            elif event == 'PREVIEW':
                self.forecast_damage.clear()
                self.battle_received.clear()
                self.battle_order.clear()
                self.battle_actions.clear()
                self.support_candidates.clear()
                self.attack_defense_candidates.clear()
                self.attack_defense_hp = None
                self.forecast = tuple(map(int, values))
            elif event == 'DAMAGE_DETAIL':
                number, role, current, predicted, damage, shield = map(int, values)
                self.forecast_damage[number] = role, current, predicted, damage, shield
            elif event in ('BATTLE_RECEIVED', 'DEFENSE_RECEIVED'):
                number, damage, barrier = map(int, values)
                received = self.battle_received if event == 'BATTLE_RECEIVED' else self.defense_received
                received[number] = damage, barrier
            elif event == 'BATTLE_ORDER':
                number, role, hit, en = values
                self.battle_order.append((int(number), role, int(hit), int(en)))
            elif event in ('BATTLE_ACTION', 'DEFENSE_BATTLE_ACTION'):
                number, role, key, hp, barrier, hit, en, name = values
                key = int(key)
                name = (self.weapon_names.get(f'120100{key:012d}') or name) if key else ''
                actions = self.battle_actions if event == 'BATTLE_ACTION' else self.defense_battle_actions
                actions[int(number)] = (role, key, name, int(hp), int(barrier), int(hit), int(en))
            elif event == 'SUPPORT_ATTACK':
                number, enabled, weapon, damage = map(int, values)
                self.support_candidates[number] = (bool(enabled), weapon, damage)
            elif event == 'SUPPORT_ACTION':
                number, key, available = map(int, values[:3])
                if self.support_editor == number:
                    name = self.weapon_names.get(f'120100{key:012d}') or values[3] or f'武装 {key}'
                    self.support_actions[key] = (bool(available), name, values[4])
            elif event == 'SUPPORT_POWER':
                number, key, power, en = map(int, values)
                if self.support_editor == number:
                    self.support_power[key] = power, en
            elif event == 'SUPPORT_CHANGED':
                self.support_plan_active = True
            elif event == 'ATTACK_DEFENSE':
                number, enabled = map(int, values)
                self.attack_defense_candidates[number] = bool(enabled)
            elif event == 'ATTACK_DEFENSE_HP':
                self.attack_defense_hp = tuple(map(int, values))
            elif event == 'ATTACK_DEFENSE_RESET':
                self.attack_defense_selection = None
                self.add_history('反击援防候选已更新')
            elif event == 'CHAR_SKILL':
                index, available = map(int, values[:2])
                key, name = values[2:]
                label = self.skill_names.get('m_character_skill', {}).get(key) or name or f'技能 {key}'
                self.pilot_skills[index] = (bool(available), label)
            elif event == 'UNIT_SKILL':
                enabled, remaining = map(int, values[:2])
                key, name = values[2:]
                label = (self.skill_names.get('m_unit_skill', {}).get(key) or
                         self.unit_skill_text.get(f'141300{int(key):012d}') or name or f'技能 {key}')
                description = self.unit_skill_text.get(f'141400{int(key):012d}', '')
                self.unit_skill = (bool(enabled), remaining, label, description)
            elif event == 'SUPPORTER_SKILL':
                enabled, remaining, supporter, key = map(int, values[:4])
                name, label, description = values[4:]
                names = formation_names()
                name = names.get('m_supporter', {}).get(f'160100{supporter:012d}') or name or f'支援人员 {supporter}'
                skill_text = names.get('m_supporter_active_skill', {})
                label = skill_text.get(f'160400{key:012d}') or label or f'技能 {key}'
                description = skill_text.get(f'160500{key:012d}') or description
                self.supporter_skill = (bool(enabled), remaining, name, label, description)
            elif event == 'SKILL_USED':
                number, kind = map(int, values)
                if kind == SUPPORTER_SKILL and self.supporter_skill:
                    _, _, name, label, _ = self.supporter_skill
                    self.add_history(f'{name} 使用支援技能「{label}」')
                else:
                    self.add_history(f'单位 {number} 使用' + ('机体技能' if kind == -1 else '人物技能'))
            elif event == 'STANDBY':
                self.add_history(f'{self.unit_name(int(values[0]))} 待机')
            elif event == 'MOVE_CANCELLED':
                self.add_history(f'{self.unit_name(int(values[0]))} 返回移动前位置')
            elif event == 'SKILL_PREVIEW':
                self.clear_skill_range()
                self.skill_manual_target = bool(int(values[0]))
            elif event == 'SKILL_CANDIDATE':
                self.skill_candidates.add(int(values[0]))
            elif event == 'SKILL_TARGET':
                self.skill_targets.add(int(values[0]))
            elif event == 'SKILL_RANGE':
                self.skill_range.add(tuple(map(int, values)))
            elif event == 'SKILL_POINT':
                self.skill_point = tuple(map(int, values))
            elif event == 'DEFENSE':
                self.incoming = tuple(map(int, values))
                self.defense_outline.clear()
                self.defense_energy.clear()
                self.defense_battle_actions.clear()
                self.defense_damage.clear()
                self.defense_received.clear()
                self.defense_pending = False
                self.defense_latest = self.defense_requested = self.defense_render = None
                self.defense_candidates.clear()
                self.defense_actions = None
                self.counter_weapons.clear()
                self.counter_support.clear()
                self.counter_support_weapons.clear()
            elif event == 'DEFENSE_ACTIONS':
                self.defense_actions = tuple(map(int, values))
            elif event == 'COUNTER_WEAPON':
                self.counter_weapons.append(int(values[0]))
            elif event == 'COUNTER_SUPPORT':
                number, enabled, weapon, damage = map(int, values)
                self.counter_support[number] = bool(enabled), weapon, damage
            elif event == 'COUNTER_SUPPORT_WEAPON':
                number, key, en = map(int, values[:3])
                name = self.weapon_names.get(f'120100{key:012d}') or values[3] or f'武装 {key}'
                self.counter_support_weapons.setdefault(number, {})[key] = (name, en)
            elif event == 'SUPPORT_DEFENSE':
                number, enabled = map(int, values)
                self.defense_candidates[number] = bool(enabled)
            elif event == 'DEFENSE_READY':
                self.focus_enemy(self.incoming[0])
                self.choose_defense_support()
            elif event == 'DEFENSE_FORECAST':
                self.defense_battle_actions.clear()
                self.defense_damage.clear()
                self.defense_received.clear()
            elif event == 'DEFENSE_DAMAGE':
                number, role, current, predicted, damage, shield = map(int, values)
                self.defense_damage[number] = role, current, predicted, damage, shield
            elif event in ('DEFENSE_FORECAST_READY', 'DEFENSE_FORECAST_FAILED'):
                self.finish_defense_forecast(event == 'DEFENSE_FORECAST_READY')
            elif event == 'DEFENSE_REJECTED':
                self.auto_respond = False
                self.status.set('原版拒绝了此应对，请重新选择')
                if self.defense_render:
                    self.defense_panel.place(relx=1, x=-12, y=self.actor_card_bottom(), anchor='ne')
                    self.defense_panel.lift()
                    self.request_defense_forecast(self.defense_latest)
                else:
                    self.choose_defense_support()
            elif event == 'DEFENSE_ACCEPTED':
                self.incoming = None
                self.defense_render = None
                if self.auto_respond:
                    self.status.set(f'{self.auto_phase_label} · 本回合自动应对')
                self.refresh_controls()
                self.draw()
            elif event == 'REJECTED':
                self.add_history('原版拒绝了该操作')
                self.last_action_error = '该操作在当前战场状态不可用'
                self.support_editor = None
                self.pending_move = None
                if self.pending_command.startswith('estimates '):
                    self.estimated_targets.discard(tuple(map(int, self.pending_command.split()[1:3])))
                    if self.confirming_attack:
                        self.clear_prediction()
                    self.after_read = None
                if self.pending_command.startswith('p ') and self.forecast is None:
                    self.clear_prediction()
                if self.pending_command == f'v {self.selected} {SUPPORTER_SKILL}':
                    self.clear_skill_preview()
            elif event == 'READY':
                self.startup_panel.place_forget()
                completed = self.pending_command
                self.pending_command = ''
                self.auto_phase_label = ''
                self.pending_move = None
                self.ready = True
                self.start_button.state(['!disabled'])
                opening = 'MuMu 开局 · 支援人员沿用本地编队' if self.imported_opening else ''
                if self.imported_opening and not self.previous_units and self.pending_state:
                    self.add_history(opening)
                self.status.set((self.save_status if completed == 'save' else '') or
                                self.last_action_error or opening or '我方阶段')
                if self.formation_to_remember is not None:
                    formation, self.formation_to_remember = self.formation_to_remember, None
                    try:
                        save_team(LAST_FORMATION, formation, stage=self.active_stage)
                    except OSError as exc:
                        message = f'未能自动保存出击记录 · {exc}'
                        self.status.set(message)
                        self.add_history(message)
                self.turn_label.set(f'第 {self.turn} 回合')
                if self.queued_review_back:
                    self.queued_review_back = False
                    self.clear_prediction()
                    self.send('review back', '正在退出交战预判')
                    self.draw()
                    return
                if self.pending_state:
                    if not self.initial_enemies:
                        self.initial_enemies.update(number for number, unit in self.units.items()
                                                    if unit.side in (2, 3, 5, 6))
                    if self.focus_loaded_board or not self.previous_units:
                        self.focus_loaded_board = False
                        opening = next(iter(self.own_units()), None)
                        if opening:
                            self.focus_unit(opening)
                    if self.selected not in self.units:
                        self.selected = 0
                    if completed != 'undo':
                        self.record_hp_changes()
                self.refresh_controls()
                if not completed.startswith(('e ', 'estimates ')) or self.selecting_weapon:
                    self.draw()
                if self.support_editor:
                    self.show_support_actions()
                if self.pending_state:
                    self.pending_state = False
                    selected = self.units.get(self.selected)
                    if selected and selected.manual:
                        self.send(f'inspect {self.selected}')
                elif completed.startswith('inspect '):
                    self.cache_selected_options(completed)
                    action, self.after_read = self.after_read, None
                    if action:
                        action()
                    elif self.selecting_weapon and not self.target:
                        self.open_weapon_selection()
                elif completed.startswith('weapons '):
                    self.show_weapon_catalog()
                elif completed.startswith(('e ', 'estimates ')):
                    if not self.last_action_error:
                        actor, target = map(int, completed.split()[1:3])
                        keys = [key for key in self.weapon_estimates if key[:2] == (actor, target)]
                        self.estimates_cache[actor, target] = (
                            {key: self.weapon_estimates[key] for key in keys},
                            {key: self.weapon_power[key] for key in keys if key in self.weapon_power},
                        )
                    action, self.after_read = self.after_read, None
                    if action:
                        action()
                    elif self.attack_selection() and self.attack_selection()[:2] != tuple(
                        map(int, completed.split()[1:3])
                    ):
                        self.request_weapon_estimate()
                elif completed == 'review back':
                    self.request_weapon_estimate()
            return

        if line.startswith('REUSE original_stage_outcome '):
            self.incoming = None
            parts = line.split()
            self.pending_result = (parts[2], int(parts[4]), int(parts[8]))
        elif line.startswith('REUSE original_stage_settlement '):
            self.settlement = line
        elif line == 'REUSE kernel_guest_exit 0':
            self.show_completed_battle()
        elif line.startswith('REUSE manual_move '):
            number = line.split()[5]
            self.add_history(f'单位 {number} 移动')
        elif line.startswith('REUSE manual_attack '):
            _, _, _, _, _, actor, _, target, *_ = line.split()
            self.add_history(f'单位 {actor} 攻击单位 {target}')
        elif line.startswith('REUSE manual_undo '):
            self.add_history(f'已回撤至第 {line.split()[3]} 回合')
        elif line.startswith('REUSE original_other_side_phase_enter '):
            side = int(line.split()[-1])
            self.auto_phase_label = '友军行动中' if side == 4 else '敌方行动中'
            self.status.set(f'{self.auto_phase_label} · 本回合自动应对' if self.auto_respond else self.auto_phase_label)
            self.refresh_controls()
        elif line.startswith('REUSE original_phase_step '):
            m = re.fullmatch(r'REUSE original_phase_step turn \d+ side ([23456]) step \d+ unit (\d+) target (\d+) .*', line)
            if m:
                side, actor, target = m.groups()
                side = '友军' if side == '4' else '敌方'
                action = f'攻击单位 {target}' if target != '0' else '行动'
                self.add_history(f'{side}单位 {actor} {action}')

    def add_history(self, text):
        self.history.insert('end', text)
        self.history.see('end')

    def show_completed_battle(self):
        if self.outcome or not self.pending_result or not self.settlement:
            return
        self.outcome, turn, reason = self.pending_result
        result = '胜利' if self.outcome == 'victory' else f'失败 · {DEFEAT_REASONS.get(reason, "原版判定")}'
        self.status.set(f'第 {turn} 回合 · {result} · 已完成本地结算')
        self.add_history(f'{result} · 原版已结算')
        self.ready = False
        self.pending_command = ''
        self.refresh_controls()

    def record_hp_changes(self):
        for number, before in self.previous_units.items():
            after = self.units.get(number)
            if after and after.hp != before.hp:
                self.add_history(f'#{number} {before.name}  HP {after.hp - before.hp:+,}')
            elif after is None:
                self.add_history(f'#{number} {before.name} 离场')

    def active_hp_forecast(self):
        if self.incoming:
            return {} if self.defense_pending else self.defense_damage
        if self.map_weapon is not None:
            return self.map_hp_forecast
        selection = self.attack_selection()
        if self.confirming_attack:
            return self.forecast_damage if self.forecast and self.forecast[:3] == selection else {}
        if selection and self.selecting_weapon and self.skill_selection is None:
            target = self.units.get(self.target)
            estimate = self.weapon_estimates.get(selection)
            if target and estimate and estimate[0]:
                damage, barrier, _ = estimate
                return {target.number: (0, target.hp, max(0, target.hp - damage), damage, barrier)}
        return {}

    def active_en_costs(self):
        if self.incoming:
            actions = {} if self.defense_pending else self.defense_battle_actions
        elif self.confirming_attack:
            actions = self.battle_actions if self.forecast and self.forecast[:3] == self.attack_selection() else {}
        else:
            if self.selecting_weapon and self.map_weapon is None and self.skill_selection is None:
                power = self.weapon_power.get(self.attack_selection())
                if power:
                    return {self.selected: power[1]}
            return {}
        return {number: en for number, (role, key, _, _, _, _, en) in actions.items() if key and role in WEAPON_ACTIONS}

    def unit_energy(self, unit):
        if self.incoming and unit.number in self.defense_energy:
            return self.defense_energy[unit.number]
        return unit.en, unit.max_en

    def actor_card_bottom(self):
        return 10 + self.actor_card.winfo_reqheight() + 8

    def refresh_controls(self):
        actor = self.units.get(self.selected)
        target = self.units.get(self.target)
        incoming_actor, incoming_target = self.incoming[:2] if self.incoming else (0, 0)
        support_actor = self.units.get(self.support_action_unit)
        card_actor = self.units.get(incoming_target) if self.incoming else support_actor or actor
        card_target = self.units.get(incoming_actor) if self.incoming else self.units.get(self.aura_preview_unit) or target
        if self.weapon_catalog_panel is not None:
            owner = card_actor if self.weapon_catalog_panel.master is self.actor_card else card_target
            if owner is None or owner.number != self.weapon_catalog_owner:
                self.close_weapon_catalog()
        predicted = self.active_hp_forecast()
        en_costs = self.active_en_costs()
        received = self.defense_received if self.incoming else self.battle_received if self.confirming_attack else {}
        for card, unit, x, y, relx, anchor in (
            (self.actor_card, card_actor, -12, 10, 1, 'ne'),
            (self.target_card, card_target, 12, 71, 0, 'nw'),
        ):
            if unit:
                state = ''
                if unit.manual:
                    state = '攻击后再移动' if unit.post_attack_move else '可行动' if unit.ready else '已行动'
                    if unit.side == 1:
                        state = f'客军 · {state}'
                elif unit.side == 4:
                    state = '友军 · 自动行动'
                card.update_unit(unit, self.pilot_caption(unit), self.small_image(f'pilots/{unit.character}.png', 54),
                                 self.idle_image(unit, (48, 48), 'front'), state, predicted.get(unit.number),
                                 received.get(unit.number), energy=self.unit_energy(unit), en_cost=en_costs.get(unit.number))
                if self.ready and not self.confirming_attack and self.map_weapon is None and self.skill_selection is None:
                    opened = self.weapon_catalog_panel is not None and self.weapon_catalog_panel.master is card
                    card.weapons_button.configure(text='收起武装' if opened else '查看武装',
                                                  command=lambda number=unit.number: self.inspect_weapons(number))
                    card.weapons_button.grid()
                else:
                    card.weapons_button.grid_remove()
                card.place(x=x, y=y, relx=relx, anchor=anchor, width=324)
            else:
                card.place_forget()
        choices = self.weapon_choices()
        if self.outcome:
            mode = '战斗结算'
        elif not self.width:
            mode = '准备出击' if self.backend else '关卡选择'
        elif self.incoming:
            mode = '战斗准备 -应战-'
        elif self.auto_phase_label:
            mode = self.auto_phase_label
        elif self.skill_selection is not None:
            mode = '选择技能目标' if self.skill_manual_target else '技能确认'
        elif self.map_weapon is not None:
            mode = '地图武装'
        elif self.confirming_attack:
            mode = '战斗准备 -攻击-'
        elif self.selecting_weapon:
            mode = '选择武装'
        elif target:
            mode = '查看敌机' if target.side in (2, 3, 5, 6) else '查看单位'
        else:
            mode = '单位移动' if actor else '我军回合'
        self.mode_label.set(mode)
        remaining = len(self.initial_enemies & self.units.keys())
        self.objective_label.set(f'初始敌军剩余 {remaining}/{len(self.initial_enemies)}' if self.initial_enemies else '')
        normal_commands = self.map_weapon is None and self.skill_selection is None and not self.incoming
        show_weapons = bool(self.selecting_weapon and normal_commands)
        show_skills = bool(actor and (self.skill_selection is not None or
                                     (normal_commands and not self.confirming_attack and
                                      (self.pilot_skills or self.unit_skill or self.supporter_skill))))
        show_map = bool(actor and (self.map_weapon is not None or
                                  (normal_commands and not self.confirming_attack and self.map_weapons)))
        for widget, shown in ((self.forecast_panel, show_weapons), (self.skills, show_skills),
                              (self.map_controls, show_map)):
            widget.grid() if shown else widget.grid_remove()
        if show_weapons:
            self.command_panel.place(in_=self.actor_card, relx=1, rely=1, x=0, y=8,
                                     anchor='ne', width=324, bordermode='outside')
            self.command_panel.lift()
        elif show_skills or show_map:
            self.command_panel.place(in_=self.actor_card.master, relx=1, rely=1, x=-12, y=-108,
                                     anchor='se', width=324)
        else:
            self.command_panel.place_forget()
        labels = list(self.weapon_choice_labels(choices).values())
        self.weapon_picker.configure(values=labels, state='readonly' if self.browsing_weapons and labels else 'disabled')
        if self.weapon_label.get() not in labels:
            self.weapon_label.set(labels[0] if labels else '')
        self.refresh_weapon_detail(choices)
        self.refresh_weapon_cards(choices, labels if show_weapons and not self.confirming_attack else [])
        selection = self.attack_selection()
        estimate = self.weapon_estimates.get(selection)
        forecast_ready = (self.confirming_attack and self.forecast is not None and self.forecast[:3] == selection)
        self.forecast_rows.delete(0, 'end')
        if forecast_ready and self.forecast_damage:
            rows = self.damage_rows()
            if not self.ready and self.pending_command.startswith(('p ', 'b ')) and not self.pending_command.endswith(' -1'):
                self.forecast_label.set('正在更新交战预判')
            else:
                self.forecast_label.set('原版预判 · 出手且命中时的 HP' if rows else '原版预判没有 HP 损失')
            for label, detail in rows:
                self.forecast_rows.insert('end', label, detail)
            self.forecast_rows.grid()
            self.forecast_scrollbar.grid()
        elif forecast_ready:
            self.forecast_rows.grid_remove()
            self.forecast_scrollbar.grid_remove()
            _, _, _, damage, counter, barrier, counter_barrier, actor_hp, target_hp = self.forecast
            outgoing = f'{damage:,} HP' + (f' + {barrier:,} 屏障' if barrier else '')
            incoming = f'{counter:,} HP' + (f' + {counter_barrier:,} 屏障' if counter_barrier else '')
            projected = f'预判剩余 HP  目标 {target_hp:,} / 我方 {actor_hp:,}' if min(actor_hp, target_hp) >= 0 else ''
            defender = next((number for number, enabled in self.attack_defense_candidates.items() if enabled), None)
            defense = f'{self.unit_name(defender)} 代挡反击' if defender else ''
            if defense and self.attack_defense_hp and self.attack_defense_hp[0] == defender:
                defense += f'\n预计剩余 {self.attack_defense_hp[1]:,} HP'
            self.forecast_label.set('\n'.join(filter(None, (f'攻击命中 {outgoing}', f'反击命中 {incoming}',
                                                           projected, defense))))
        else:
            self.forecast_rows.grid_remove()
            self.forecast_scrollbar.grid_remove()
            if estimate is not None:
                damage, barrier, hit_rate = estimate
                outgoing = f'{damage:,} HP' + (f' + {barrier:,} 屏障' if barrier else '')
                caption = f'武装预估 {outgoing} · 命中 {hit_rate}%'
            else:
                caption = ''
            if self.confirming_attack:
                state = '正在计算交战预判' if not self.ready else '交战预判不可用'
                caption = f'{caption}\n{state}' if caption else state
            elif not caption and self.selecting_weapon and (key := self.active_weapon()) is not None:
                caption = self.weapon_unavailable(key)
                if not caption and key not in self.map_weapons and not self.ready:
                    caption = '正在计算武装伤害'
            self.forecast_label.set(caption)
        self.action_label.set('开始战斗' if self.confirming_attack else '攻击' if self.selecting_weapon else '选择武装')
        if self.support_action_unit is not None:
            self.action_label.set('行动选择')
            can_attack = self.ready and self.support_actions.get(self.support_action_weapon, (False,))[0]
        elif self.confirming_attack:
            self.action_label.set('开始战斗')
            can_attack = forecast_ready
        else:
            key = self.active_weapon()
            if self.selecting_weapon and key in self.map_weapons:
                self.action_label.set('地图武装')
                can_attack = self.ready and self.map_weapons[key][1]
            else:
                self.action_label.set('攻击' if self.selecting_weapon else '选择武装')
                can_attack = selection is not None if self.selecting_weapon else bool(choices)
        self.attack_button.configure(style='BattleStart.TButton' if self.confirming_attack and not support_actor
                                     else 'Primary.TButton',
                                     state='normal' if self.browsing_weapons and can_attack else 'disabled')
        if show_weapons and not self.confirming_attack and len(self.attackable_targets()) > 1:
            self.next_target_button.grid()
            self.next_target_button.configure(state='normal' if self.browsing_weapons else 'disabled')
        else:
            self.next_target_button.grid_remove()
        self.refresh_support_slots()
        self.attack_defense_button.configure(
            state='normal' if self.ready and forecast_ready and self.attack_defense_candidates and not support_actor
            else 'disabled')
        if target and self.confirming_attack and not support_actor:
            self.attack_defense_button.grid()
        else:
            self.attack_defense_button.grid_remove()
        skill_values = [name if enabled else f'{name}（不可用）' for enabled, name in self.pilot_skills.values()]
        self.skill_picker.configure(values=skill_values, state='readonly' if self.ready and skill_values else 'disabled')
        if self.skill_label.get() not in skill_values:
            self.skill_label.set(skill_values[0] if skill_values else '')
        skill_index = self.skill_picker.current()
        skill_enabled = skill_index >= 0 and list(self.pilot_skills.values())[skill_index][0]
        self.skill_button.configure(state='normal' if self.ready and skill_enabled else 'disabled')
        if self.unit_skill:
            available, remaining, _, _ = self.unit_skill
            self.unit_skill_label.set(f'机体技能 · 剩余 {remaining} 次')
        else:
            available = False
            self.unit_skill_label.set('机体技能')
        self.unit_skill_button.configure(state='normal' if self.ready and available else 'disabled')
        if self.supporter_skill:
            available, remaining, _, label, _ = self.supporter_skill
            self.supporter_skill_label.set(f'支援技能 · {label} · 剩余 {remaining} 次')
            self.supporter_skill_button.configure(state='normal' if self.ready and available else 'disabled')
            self.supporter_skill_button.grid()
        else:
            self.supporter_skill_button.grid_remove()
        self.save_button.configure(state='normal' if self.ready and not self.outcome else 'disabled')
        self.load_button.configure(state='normal' if self.can_load_battle() else 'disabled')
        self.import_mumu_button.configure(
            state='normal' if self.formation_editor and self.can_load_battle() else 'disabled')
        self.formation_button.configure(state='normal' if self.formation_editor and self.can_load_battle() else 'disabled')
        undo_text = f'行动回撤 · {self.undo_count}' if self.undo_count else '行动回撤'
        self.undo_button.configure(text=undo_text,
                                   state='normal' if (self.ready and self.undo_available) or
                                   self.rewind_at_settlement else 'disabled')
        self.pass_button.configure(state='normal' if self.ready and not self.outcome else 'disabled')
        show_phase_tools = normal_commands and (not self.selecting_weapon or
                                                (self.moved_unit and not self.confirming_attack))
        if self.width and (self.rewind_at_settlement or (not self.outcome and show_phase_tools)):
            self.phase_tools.place(x=12, y=79, anchor='nw')
        else:
            self.phase_tools.place_forget()
        if actor and not self.incoming:
            self.actions.place(relx=1, rely=1, x=-12, y=-10, anchor='se')
            self.actions.lift()
        else:
            self.actions.place_forget()
        if not actor and self.ready and self.own_units():
            self.unit_list_button.place(relx=1, rely=1, x=-12, y=-10, anchor='se', width=142)
        else:
            self.unit_list_button.place_forget()
        if actor and self.ready and normal_commands and not self.selecting_weapon and not self.moved_unit:
            self.unit_switcher.place(x=12, rely=1, y=-46, anchor='sw')
            self.unit_switcher.lift()
        else:
            self.unit_switcher.place_forget()
        self.back_button.grid() if actor else self.back_button.grid_remove()
        may_cancel_review = self.pending_command.startswith(('p ', 'b '))
        self.back_button.configure(state='normal' if self.browsing_weapons or self.inspecting_move or may_cancel_review
                                   else 'disabled')
        standby = actor and actor.manual and (actor.ready or actor.post_attack_move) and self.map_weapon is None
        self.standby_button.configure(state='normal' if self.ready and standby else 'disabled')
        self.standby_button.grid_remove() if self.confirming_attack else self.standby_button.grid()
        map_labels = [f'{name} · {ammo} 发' if allowed else f'{name}（不可用）'
                      for _, allowed, _, ammo, name in self.map_weapons.values()]
        self.map_picker.configure(values=map_labels, state='readonly' if self.ready and map_labels else 'disabled')
        if self.map_weapon_label.get() not in map_labels:
            self.map_weapon_label.set(map_labels[0] if map_labels else '')
        index = self.map_picker.current()
        key = list(self.map_weapons)[index] if 0 <= index < len(self.map_weapons) else None
        self.map_button.configure(state='normal' if self.ready and key and self.map_weapons[key][1] else 'disabled')
        if self.map_weapon is None:
            self.map_tools.grid_remove()
        else:
            self.map_tools.grid()
            for button in (self.attack_button, self.attack_defense_button,
                           self.undo_button, self.standby_button, self.pass_button):
                button.configure(state='disabled')
            kind, direction, count, can_fire = self.map_preview or (0, 0, 0, 0)
            rotatable = self.ready and kind in (3, 4)
            for button in self.direction_buttons:
                button.configure(state='normal' if rotatable else 'disabled')
            self.map_back_button.configure(state='normal' if self.ready else 'disabled')
            self.map_fire_button.configure(state='normal' if self.ready and can_fire else 'disabled')
            slots = self.map_weapons[self.map_weapon][2]
            if kind == 2 and slots > 1:
                self.map_slot_picker.grid()
                labels = [f'落点 {i + 1} · {self.map_slots.get(i, "未选")}' for i in range(slots)]
                self.map_slot_picker.configure(values=labels,
                                               state='readonly' if self.ready else 'disabled')
                if self.map_edit_slot is not None and 0 <= self.map_edit_slot < slots:
                    self.map_slot_picker.current(self.map_edit_slot)
                current_range = self.map_slot_effects.get(self.map_edit_slot, ())
                self.map_status.set(f'已选 {count}/{slots}  ·  当前范围 {len(current_range)} 格')
            else:
                self.map_slot_picker.grid_remove()
                facing = dict(zip(range(1, 5), '右上左下')).get(direction, '—')
                self.map_status.set(f'朝向 {facing}  ·  范围 {len(self.map_effect)} 格')
        if self.skill_selection is None:
            self.skill_preview_panel.grid_remove()
        else:
            self.skill_preview_panel.grid()
            target_names = '、'.join(self.unit_name(number) for number in sorted(self.skill_targets))
            instructions = '点选范围内的队友' if self.skill_manual_target else ''
            details = self.unit_skill[2:] if self.skill_selection == -1 and self.unit_skill else ()
            if self.skill_selection == SUPPORTER_SKILL and self.supporter_skill:
                _, _, name, label, description = self.supporter_skill
                details = (f'{name} · {label}', description)
                instructions = '点击同队机体选择施放位置' if self.skill_manual_target else '全地图'
                if not target_names and self.ready:
                    target_names = '当前没有可受益的目标'
            self.skill_preview_label.set('\n'.join(filter(None, (*details, instructions, target_names))))
            self.skill_confirm.configure(state='normal' if self.ready and self.skill_targets else 'disabled')
            self.skill_cancel.configure(state='normal' if self.ready else 'disabled')
            for button in (self.attack_button, self.attack_defense_button,
                           self.map_button, self.skill_button, self.unit_skill_button, self.supporter_skill_button,
                           self.undo_button,
                           self.standby_button, self.pass_button):
                button.configure(state='disabled')
            self.map_picker.configure(state='disabled')

    def refresh_weapon_detail(self, choices):
        selection = self.attack_selection()
        owner = self.selected
        if self.support_action_unit is not None and self.support_action_weapon == 0:
            self.weapon_detail.grid()
            self.weapon_detail_title.configure(text='不参加')
            self.weapon_detail_stats.configure(text='')
            self.weapon_detail_effects.pack_forget()
            return
        if self.map_weapon in self.map_weapons:
            key = self.map_weapon
            name = self.map_weapons[key][4]
            ammo = self.map_weapons[key][3]
        elif self.support_action_unit is not None:
            owner = self.support_action_unit
            key = self.support_action_weapon
            _, name, _ = self.support_actions[key]
            ammo = selection = None
        elif self.selecting_weapon and self.skill_selection is None and (key := self.active_weapon()) in choices:
            name = choices[key]
            ammo = self.map_weapons[key][3] if key in self.map_weapons else None
        else:
            self.weapon_detail.grid_remove()
            return
        self.weapon_detail.grid()
        info = self.weapon_info.get((owner, key))
        if info is None:
            self.weapon_detail_title.configure(text=name)
            self.weapon_detail_stats.configure(text='')
        else:
            effective = (self.support_power.get(key) if self.support_action_unit is not None
                         else self.weapon_power.get(selection))
            power, stats = self.weapon_stat_lines(info, effective=effective, ammo=ammo)
            self.weapon_detail_title.configure(text=f'{name}\n{power}')
            self.weapon_detail_stats.configure(text=stats)
        effects = self.weapon_effects.get((owner, key), ())
        if effects:
            self.weapon_detail_effects.configure(text='\n'.join(dict.fromkeys(effects)))
            self.weapon_detail_effects.pack(anchor='w', pady=(3, 0))
        else:
            self.weapon_detail_effects.pack_forget()

    def refresh_weapon_cards(self, choices, labels):
        cards = []
        for key, label in zip(choices, labels):
            info = self.weapon_info.get((self.selected, key))
            cost = f'剩余 {self.map_weapons[key][3]} 发' if key in self.map_weapons else f'EN {info[1]}' if info else ''
            reason = self.weapon_unavailable(key)
            detail = '' if reason == '请选择目标' else reason
            cards.append(('\n'.join(filter(None, (label, cost, detail))), bool(reason)))
        cards = tuple(cards)
        if cards != self.weapon_card_values:
            for button in self.weapon_card_buttons:
                button.destroy()
            self.weapon_card_buttons = []
            self.weapon_card_values = cards
            for index, (label, _) in enumerate(cards):
                button = ttk.Button(self.weapon_cards, text=label, width=11,
                                    command=lambda i=index: self.select_weapon_card(i))
                row, col = divmod(index, 5)
                button.grid(row=row, column=col, sticky='nsew', padx=2, pady=2)
                self.weapon_card_buttons.append(button)
        if not cards:
            self.weapon_row.place_forget()
            return
        self.weapon_row.place(relx=0.5, rely=1, y=-89, anchor='s')
        for index, (button, (_, unavailable)) in enumerate(zip(self.weapon_card_buttons, cards)):
            if index == self.weapon_picker.current():
                style = 'SelectedUnavailableWeapon.TButton' if unavailable else 'SelectedWeapon.TButton'
            else:
                style = 'UnavailableWeapon.TButton' if unavailable else 'TButton'
            button.configure(style=style, state='normal' if self.browsing_weapons else 'disabled')

    def select_weapon_card(self, index):
        if self.browsing_weapons and index != self.weapon_picker.current():
            self.weapon_picker.current(index)
            self.weapon_changed()

    def attackable_targets(self):
        return [number for number in self.weapons if number in self.units]

    def weapon_choices(self):
        return self.selectable_weapons | {key: info[4] for key, info in self.map_weapons.items()}

    @staticmethod
    def weapon_choice_labels(choices):
        names = Counter(choices.values())
        return {key: f'{name} · {key}' if names[name] > 1 else name for key, name in choices.items()}

    def active_weapon(self):
        index = self.weapon_picker.current()
        keys = list(self.weapon_choices())
        return keys[index] if 0 <= index < len(keys) else None

    def weapon_unavailable(self, key):
        if key in self.map_weapons:
            _, allowed, _, ammo, _ = self.map_weapons[key]
            return '' if allowed else '弹药耗尽' if not ammo else '当前不可用'
        choices = self.weapons.get(self.target, {})
        if any(weapon == key for weapon, _ in choices.values()):
            return ''
        if not self.target:
            return '请选择目标'
        target = self.units.get(self.target)
        weapon_range = self.selectable_weapon_ranges.get(key)
        if target and weapon_range and not (weapon_range & target.cells):
            return '超出射程'
        return '无法攻击目标'

    def primary_action(self):
        if self.support_action_unit is not None:
            number, weapon = self.support_action_unit, self.support_action_weapon
            enabled, previous, _ = self.support_candidates[number]
            self.dismiss_support_menu()
            if weapon != (previous if enabled else 0):
                self.support_command(number, weapon)
        elif self.confirming_attack:
            self.attack()
        elif self.selecting_weapon:
            key = self.active_weapon()
            if key in self.map_weapons:
                self.open_map_weapon(key=key)
            else:
                self.request_preview()
        else:
            self.open_weapon_selection()

    def open_weapon_selection(self):
        candidates = self.attackable_targets()
        if candidates:
            actor = self.units[self.selected]
            nearest = min(candidates, key=lambda n: min(
                abs(x - actor.x) + abs(z - actor.z) for x, z in self.units[n].cells))
            self.select_target(self.target if self.target in candidates else nearest, opening=True)
        elif self.weapon_choices():
            self.select_target(0, opening=True)

    def next_target(self):
        candidates = self.attackable_targets()
        if self.browsing_weapons and not self.confirming_attack and candidates:
            index = candidates.index(self.target) if self.target in candidates else -1
            self.select_target(candidates[(index + 1) % len(candidates)])

    def on_back_key(self, _event):
        if self.battle_saves.close():
            return 'break'
        if self.formation_editor and self.formation_editor.winfo_manager():
            if not self.formation_editor.team_saves.close():
                self.formation_editor.close_chooser()
            return 'break'
        if self.root.focus_get() and self.root.focus_get().winfo_toplevel() == self.root:
            self.back_to_unit()
            return 'break'

    def back_to_unit(self):
        if self.weapon_catalog_owner is not None:
            self.close_weapon_catalog()
            self.refresh_controls()
            return
        if self.unit_list_panel.winfo_manager():
            self.unit_list_panel.place_forget()
            return
        if self.aura_preview_unit:
            self.aura_preview_unit = 0
            self.refresh_controls()
            self.draw()
            return
        if self.dismiss_support_menu():
            return
        if self.inspecting_move:
            self.after_read = self.back_to_unit
            return
        if self.pending_command.startswith(('p ', 'b ')):
            self.queued_review_back = True
            self.clear_prediction()
            self.status.set('正在退出交战预判')
            self.refresh_controls()
            self.draw()
            return
        if not self.browsing_weapons:
            return
        if self.skill_selection is not None:
            self.cancel_skill()
            return
        if self.map_weapon is not None:
            self.cancel_map_weapon()
            return
        if self.confirming_attack:
            self.clear_prediction()
            if self.ready:
                self.send('review back', '正在退出交战预判')
            else:
                self.after_read = None
                self.status.set('正在计算武装伤害')
                self.refresh_controls()
            self.draw()
        elif self.moved_unit and self.moved_unit == self.selected:
            self.target = 0
            self.selecting_weapon = False
            self.clear_prediction()
            if not self.ready:
                self.after_read = self.cancel_staged_move
                self.refresh_controls()
            else:
                self.cancel_staged_move()
        elif self.target or self.selecting_weapon:
            self.select_target(0)
        else:
            self.selected = 0
            self.clear_options()
            self.refresh_controls()
            self.draw()

    def cancel_staged_move(self):
        self.send(f'back {self.moved_unit}', '正在返回移动前位置')

    def select_target(self, number, *, opening=False):
        self.aura_preview_unit = 0
        previous = self.active_weapon() if self.selecting_weapon and not opening else None
        choices = self.weapons.get(number, {})
        self.target = number
        self.selecting_weapon = bool(choices or opening or (number and self.selecting_weapon))
        self.after_read = None
        preferred = previous if previous is not None else next((key for key, _ in choices.values()), None)
        self.weapon_label.set(self.weapon_choice_labels(self.weapon_choices()).get(preferred, ''))
        self.clear_prediction()
        self.refresh_controls()
        self.draw()
        self.request_weapon_estimate()

    def select_unit(self, number):
        self.unit_list_panel.place_forget()
        if self.moved_unit and self.moved_unit != number:
            self.cancel_staged_move()
            return
        if self.selected == number and not self.selecting_weapon and not self.target:
            return
        self.selected, self.target = number, 0
        self.selecting_weapon = False
        self.after_read = None
        self.clear_options()
        if number in self.option_cache:
            (moves, support, danger, weapons, selectable, selectable_ranges, info, effects, skills,
             unit_skill, supporter_skill, map_weapons) = self.option_cache[number]
            self.moves.update(moves)
            self.support_cells.update(support)
            for kind, cells in danger.items():
                self.danger_cells[kind].update(cells)
            self.weapons.update(weapons)
            self.selectable_weapons.update(selectable)
            self.selectable_weapon_ranges.update(selectable_ranges)
            self.weapon_info.update(info)
            self.weapon_effects.update({key: list(values) for key, values in effects.items()})
            self.pilot_skills.update(skills)
            self.unit_skill = unit_skill
            self.supporter_skill = supporter_skill
            self.map_weapons.update(map_weapons)
            for key, (estimates, power) in self.estimates_cache.items():
                if key[0] == number:
                    self.estimated_targets.add(key)
                    self.weapon_estimates.update(estimates)
                    self.weapon_power.update(power)
            self.refresh_controls()
            self.draw()
            return
        self.refresh_controls()
        self.draw()
        self.send(f'inspect {number}')

    def cache_selected_options(self, command):
        number = int(command.split()[1])
        if self.selected == number:
            self.option_cache[number] = (
                self.moves.copy(), self.support_cells.copy(),
                {kind: cells.copy() for kind, cells in self.danger_cells.items()},
                self.weapons.copy(), self.selectable_weapons.copy(), self.selectable_weapon_ranges.copy(),
                self.weapon_info.copy(), self.weapon_effects.copy(),
                self.pilot_skills.copy(), self.unit_skill, self.supporter_skill, self.map_weapons.copy(),
            )

    def close_weapon_catalog(self):
        if self.weapon_catalog_panel is not None:
            self.weapon_catalog_panel.destroy()
            self.weapon_catalog_panel = None
        self.weapon_catalog.clear()
        self.weapon_catalog_owner = None

    def inspect_weapons(self, number):
        was_open = self.weapon_catalog_panel is not None and self.weapon_catalog_owner == number
        self.close_weapon_catalog()
        if was_open:
            self.refresh_controls()
        elif self.send(f'weapons {number}', '正在读取原版武装资料'):
            self.weapon_catalog_owner = number

    def show_weapon_catalog(self):
        owner = self.weapon_catalog_owner
        if owner not in self.units:
            return
        card = self.actor_card if owner == self.selected else self.target_card
        panel = tk.Frame(card, bg=BG)
        panel.grid(row=6, column=0, columnspan=3, sticky='ew', padx=7, pady=(0, 7))
        self.weapon_catalog_panel = panel
        self.refresh_controls()
        if not self.weapon_catalog:
            self.label(panel, text='没有可查看的武装', bg=BG).pack(anchor='w', padx=6, pady=6)
            return
        labels = {key: f'{name} · MAP' if is_map else name for key, (name, is_map) in self.weapon_catalog.items()}
        counts = Counter(labels.values())
        picker = ttk.Combobox(panel, state='readonly', width=24,
                              values=[f'{label} · {key}' if counts[label] > 1 else label for key, label in labels.items()])
        picker.pack(fill='x', padx=6, pady=(6, 4))
        details = tk.Frame(panel, bg=BG)
        details.pack(fill='x', padx=6, pady=(0, 6))
        detail = tk.Text(details, bg=BG, fg=TEXT, font=self.font, relief='flat', wrap='word', width=24, height=5)
        scrollbar = ttk.Scrollbar(details, command=detail.yview)
        detail.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        detail.pack(side='left', fill='both', expand=True)

        def show(_=None):
            key = tuple(self.weapon_catalog)[picker.current()]
            lines = self.weapon_stat_lines(self.weapon_info.get((owner, key)))
            effects = self.weapon_effects.get((owner, key), ())
            lines.extend(dict.fromkeys(effects or ('无附加效果',)))
            detail.configure(state='normal')
            detail.delete('1.0', 'end')
            detail.insert('1.0', '\n'.join(lines))
            detail.configure(state='disabled')

        picker.current(0)
        picker.bind('<<ComboboxSelected>>', show)
        show()

    @staticmethod
    def weapon_stat_lines(info, *, effective=None, ammo=None):
        if info is None:
            return []
        power, en, level, minimum, maximum, attributes, attack_types = info
        effective_power, en = effective or (power, en)
        bonus = f'   {effective_power - power:+,}' if effective_power != power else ''
        attribute = '／'.join(label for values, mask in ((WEAPON_ATTRIBUTES, attributes),
                                                        (ATTACK_ATTRIBUTES, attack_types))
                              for i, label in enumerate(values, 1) if mask & (1 << i))
        reach = (f'射程 {minimum}' if minimum == maximum else f'射程 {minimum}–{maximum}') if maximum else ''
        cost = f'剩余 {ammo} 发' if ammo is not None else f'EN {en}'
        return [f'POWER  {effective_power:,}{bonus}', '  ·  '.join(filter(None, (attribute, f'LV{level}', reach, cost)))]

    def pilot_caption(self, unit):
        name = self.character_names.get(f'10010000{unit.character:010d}', '')
        role = ROLES.get(self.character_roles.get(str(unit.character)), '')
        return '  '.join(filter(None, (name, role)))

    def damage_rows(self, forecast=None):
        rows = []
        changes = self.forecast_damage if forecast is None else forecast
        received = self.battle_received if forecast is None else self.defense_received
        for number, (role, current, predicted, damage, shield) in changes.items():
            hit_hp, hit_shield = received.get(number, (damage, shield))
            if forecast is None and not role and not hit_hp and not hit_shield and current == predicted:
                continue
            unit = self.units.get(number)
            side = '我方' if unit and unit.manual else '敌方' if unit and unit.side in (2, 3, 5, 6) else ''
            label = ' '.join(filter(None, (side, self.unit_name(number)))) + (' · 代挡' if role else '')
            text = f'{current:,} → {predicted:,} HP'
            if hit_hp:
                text += f' · 受伤 {hit_hp:,}'
            if hit_shield:
                text += f' · 屏障 {hit_shield:+,}' if hit_shield < 0 else f' · 屏障损失 {hit_shield:,}'
            rows.append((label, text))
        return rows

    def attack_selection(self):
        key = self.active_weapon()
        for index, (weapon, _) in self.weapons.get(self.target, {}).items():
            if weapon == key:
                return self.selected, self.target, index
        return None

    def attack_command(self, command):
        selection = self.attack_selection()
        if not selection:
            return None
        numbers = list(selection)
        if self.support_plan_active:
            numbers.extend((-2, 0, 0))
        if self.attack_defense_selection is not None:
            if not self.support_plan_active:
                numbers.extend((-1, 0, 0))
            numbers.append(self.attack_defense_selection)
        return command + ' ' + ' '.join(map(str, numbers))

    def request_preview(self):
        if self.confirming_attack and not self.ready:
            return
        command = self.attack_command('p')
        if not command:
            return
        if self.ready:
            self.confirming_attack = True
            self.forecast = None
            self.send(command, '正在计算交战预判')
            self.draw()
        elif self.browsing_weapons:
            self.confirming_attack = True
            self.forecast = None
            self.after_read = self.request_preview
            self.status.set('正在计算交战预判')
            self.refresh_controls()
            self.draw()

    def request_weapon_estimate(self):
        selection = self.attack_selection()
        if self.selecting_weapon and selection and selection[:2] not in self.estimated_targets and self.ready:
            actor, target, weapon = selection
            if self.send(f'estimates {actor} {target} {weapon}', '正在计算武装伤害'):
                self.estimated_targets.add(selection[:2])

    def weapon_changed(self):
        self.after_read = None
        self.clear_prediction()
        self.refresh_controls()
        self.draw()
        self.request_weapon_estimate()

    def attack(self):
        command = self.attack_command('a')
        if command:
            self.support_menu.place_forget()
            self.send(command, '原版正在判定攻击')

    def use_character_skill(self):
        index = self.skill_picker.current()
        keys = list(self.pilot_skills)
        if 0 <= index < len(keys):
            key = keys[index]
            if self.pilot_skills[key][0]:
                self.open_skill_preview(key)

    def use_unit_skill(self):
        if self.unit_skill and self.unit_skill[0]:
            self.open_skill_preview(-1)

    def use_supporter_skill(self):
        if self.supporter_skill and self.supporter_skill[0]:
            self.open_skill_preview(SUPPORTER_SKILL)

    def open_skill_preview(self, index):
        if not self.ready:
            return
        self.clear_map_preview()
        self.clear_skill_range()
        self.skill_selection = index
        self.send(f'v {self.selected} {index}', '正在读取原版技能目标')

    def clear_skill_preview(self):
        self.skill_selection = None
        self.clear_skill_range()

    def clear_skill_range(self):
        self.skill_manual_target = False
        self.skill_point = None
        self.skill_candidates.clear()
        self.skill_targets.clear()
        self.skill_range.clear()

    def confirm_skill(self):
        if self.skill_selection is not None and self.skill_targets:
            index = self.skill_selection
            if index == SUPPORTER_SKILL:
                command = f'h {self.selected}'
            else:
                command = f'u {self.selected}' if index == -1 else f'c {self.selected} {index}'
            self.send(command, '原版正在执行技能')

    def cancel_skill(self):
        if not self.ready:
            return
        self.clear_skill_preview()
        self.send('v cancel')

    def clear_map_preview(self):
        self.map_weapon = None
        self.map_preview = None
        self.clear_map_range()

    def clear_map_range(self):
        self.map_effect.clear()
        self.map_overlap.clear()
        self.map_taps.clear()
        self.map_targets.clear()
        self.map_hp_forecast.clear()
        self.map_points.clear()
        self.map_edit_slot = None
        self.map_slots.clear()
        self.map_slot_effects.clear()

    def open_map_weapon(self, *, key=None):
        if key is None:
            index = self.map_picker.current()
            if not 0 <= index < len(self.map_weapons):
                return
            key = list(self.map_weapons)[index]
        if not self.ready or not self.map_weapons[key][1]:
            return
        self.clear_map_preview()
        self.map_weapon = key
        self.map_command('o')

    def map_command(self, command, *args):
        if self.map_weapon is not None:
            parts = (self.selected, self.map_weapon, command, *args)
            self.send('w ' + ' '.join(map(str, parts)), '原版正在处理地图武装')

    def cancel_map_weapon(self):
        if not self.ready:
            return
        self.clear_map_preview()
        self.refresh_controls()
        self.draw()

    def small_image(self, filename, size, *, upscale=False):
        if ImageTk is None:
            return ''
        key = filename, size
        cache = self.board_badge_images if upscale else self.small_images
        if key not in cache:
            try:
                with Image.open(UI_ASSETS / filename) as source:
                    image = source.convert('RGBA')
                if upscale:
                    image = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
                else:
                    image.thumbnail((size, size), Image.Resampling.LANCZOS)
                cache[key] = ImageTk.PhotoImage(image, master=self.root)
            except OSError:
                cache[key] = ''
        return cache[key]

    def unit_name(self, number):
        unit = self.units.get(number)
        return f'#{number} {unit.name}' if unit else f'单位 {number}'

    def refresh_support_slots(self):
        if not self.confirming_attack or self.support_action_unit is not None:
            self.order_bar.place_forget()
            return
        order = list(self.battle_order)
        listed = {number for number, role, _, _ in order if role == 'support'}
        extra = [(number, 'support', -1, 0) for number in self.support_candidates if number not in listed]
        main = next((i for i, (_, role, _, _) in enumerate(order) if role == 'attack'), 0)
        order[main:main] = extra
        if not order:
            self.order_bar.place_forget()
            return
        self.order_bar.place(x=12, rely=1, y=-47, anchor='sw', relwidth=1, width=-398)
        self.order_bar.lift()
        signature = tuple(order), tuple(self.support_candidates.items()), tuple(self.forecast_damage.items()), self.ready
        if signature == self.order_signature:
            return
        self.order_signature = signature
        for widget in self.support_slots.winfo_children():
            widget.destroy()
        sequence = 0
        for column, (number, role, hit, en) in enumerate(order):
            unit = self.units.get(number)
            if not unit:
                continue
            support = role == 'support' and number in self.support_candidates
            enabled = self.support_candidates[number][0] if support else role != 'none'
            action = BATTLE_ACTIONS[role] if enabled else '不参加'
            shows_energy = enabled and role in WEAPON_ACTIONS and (hit >= 0 or en > 0)
            if shows_energy:
                sequence += 1
                rate = f'{sequence}  ·  {hit}%'
            else:
                rate = ''
            damage = self.battle_received.get(number, (self.forecast_damage.get(number, (0, 0, 0, 0, 0))[3], 0))[0]
            cost = f'\n−{damage:,} HP' if role in ('defense', 'attack_defense') and damage else ''
            color = BG if not enabled else '#7a3d25' if unit.side in (2, 3, 5, 6) else '#244d74'
            face = self.small_image(f'pilots/{unit.character}.png', 35) or self.idle_image(unit, (35, 35), 'front')
            tile = tk.Frame(self.support_slots, bg=color, width=91, height=90)
            tile.grid(row=0, column=column, padx=(3, 0), pady=3)
            tile.pack_propagate(False)
            button = tk.Button(tile, text=f'{rate or f"#{number}"}\n{action}{cost}',
                               image=face or '', compound='top', bg=color, fg=TEXT, borderwidth=0,
                               activebackground=GRID, activeforeground=TEXT, disabledforeground=MUTED,
                               cursor='hand2' if support else '', font=(self.font[0], 9))
            if support:
                button.configure(command=lambda n=number: self.open_support_actions(n),
                                 state='normal' if self.ready else 'disabled')
            else:
                button.configure(activebackground=color)
            if shows_energy:
                gauge = tk.Canvas(tile, height=13, bg=color, highlightthickness=0)
                gauge.pack(side='bottom', fill='x', padx=5, pady=(0, 3))
                draw_en_bar(gauge, (1, 1, 79, 12), unit.en, unit.max_en, en, (self.font[0], 8, 'bold'),
                            tags=(f'order-en-{number}',))
                if support:
                    gauge.bind('<Button-1>', lambda _, control=button: control.invoke())
            button.pack(fill='both', expand=True)

    def resize_battle_order(self, _event=None):
        self.order_canvas.configure(scrollregion=self.order_canvas.bbox('all'))
        if self.support_slots.winfo_reqwidth() > self.order_canvas.winfo_width():
            self.order_scroll.pack(fill='x')
        else:
            self.order_scroll.pack_forget()
            self.order_canvas.xview_moveto(0)

    def support_command(self, number, weapon):
        selection = self.attack_selection()
        if not self.ready or not selection or not self.confirming_attack or number not in self.support_candidates:
            return
        fields = (*selection, number, weapon)
        if self.attack_defense_selection is not None:
            fields += (self.attack_defense_selection,)
        self.support_menu.place_forget()
        self.send('b ' + ' '.join(map(str, fields)),
                  '正在读取支援武装' if weapon < 0 else '正在更新交战预判')

    def open_support_actions(self, number):
        if not self.ready or not self.forecast:
            return
        self.support_actions.clear()
        self.support_power.clear()
        self.support_editor = number
        self.support_command(number, -1)

    def dismiss_support_menu(self, _event=None):
        if self.support_menu.winfo_manager():
            self.support_menu.place_forget()
            self.support_action_unit = self.support_action_weapon = None
            self.refresh_controls()
            self.draw()
            return 'break'

    def show_support_actions(self):
        number = self.support_editor
        self.support_editor = None
        if not self.support_actions:
            return
        enabled, selected, _ = self.support_candidates[number]
        self.support_action_unit = number
        current = selected if enabled else 0
        self.support_action_weapon = current if current in self.support_actions else next(iter(self.support_actions))
        for widget in self.support_menu.winfo_children():
            widget.destroy()
        self.support_action_buttons = {}
        for key, (available, name, reason) in self.support_actions.items():
            label = name if key else '不参加'
            info = self.weapon_info.get((number, key))
            if info:
                _, en = self.support_power.get(key, info[:2])
                label += f'\nEN {en}'
            if not available:
                label += f'\n{WEAPON_UNAVAILABLE.get(reason, reason) or "不可用"}'
            button = ttk.Button(self.support_menu, text=label, width=12, state='normal' if available else 'disabled',
                                command=lambda k=key: self.highlight_support_action(k))
            button.pack(side='left', padx=3, pady=3)
            self.support_action_buttons[key] = button
        self.support_menu.place(relx=0.5, rely=1, y=-89, anchor='s')
        self.highlight_support_action(self.support_action_weapon)
        self.support_menu.lift()

    def highlight_support_action(self, weapon):
        self.support_action_weapon = weapon
        for key, button in self.support_action_buttons.items():
            button.configure(style='SelectedWeapon.TButton' if key == weapon else 'TButton')
        self.refresh_controls()
        self.draw()

    def defense_picker(self, dialog, candidates):
        original = next((number for number, enabled in candidates.items() if enabled), 0)
        chosen = tk.IntVar(dialog, value=original)
        ttk.Radiobutton(dialog, text='不支援', variable=chosen, value=0).pack(anchor='w')
        for number in candidates:
            ttk.Radiobutton(dialog, text=self.unit_name(number), variable=chosen, value=number).pack(anchor='w')
        return chosen

    def choose_attack_defense(self):
        if not self.ready or not self.attack_defense_candidates:
            return
        for widget in self.support_menu.winfo_children():
            widget.destroy()
        self.label(self.support_menu, text='选择代挡反击的机体', fg=OWN).pack(anchor='w', padx=7, pady=4)
        original = next((number for number, enabled in self.attack_defense_candidates.items() if enabled), 0)

        def select(number):
            self.support_menu.place_forget()
            if number != original:
                self.attack_defense_selection = number
                self.request_preview()

        for number in (0, *self.attack_defense_candidates):
            label = self.unit_name(number) if number else '不参加'
            ttk.Button(self.support_menu, text=('✓  ' if number == original else '') + label,
                       command=lambda n=number: select(n)).pack(fill='x', padx=6, pady=2)
        ttk.Button(self.support_menu, text='返回', command=self.dismiss_support_menu).pack(anchor='e', padx=6, pady=4)
        self.support_menu.place(relx=1, rely=1, x=-12, y=-95, anchor='se')
        self.support_menu.lift()

    def choose_defense_support(self):
        attacker, target, weapon = self.incoming
        self.refresh_controls()
        panel = self.defense_panel
        for widget in panel.winfo_children():
            widget.destroy()
        panel.configure(padx=12, pady=10)
        weapon_name = self.weapon_names.get(f'120100{weapon:012d}', f'武装 {weapon}')
        self.status.set('等待选择受击应对')
        self.label(panel, text=f'{self.unit_name(attacker)} 使用 {weapon_name}\n目标 {self.unit_name(target)}',
                   bg=PANEL, wraplength=350,
                   justify='left').pack(anchor='w', pady=(0, 7))
        if self.defense_candidates:
            self.label(panel, text='支援防御', bg=PANEL, fg=OWN).pack(anchor='w', pady=(3, 0))
            chosen = self.defense_picker(panel, self.defense_candidates)
        else:
            chosen = tk.IntVar(panel, value=0)
        default_action, default_weapon, can_defend, can_evade = self.defense_actions or (0, 0, 0, 0)
        options = [(default_action, default_weapon, '原版自动应对')]
        options.extend((1, key, f'反击 · {self.weapon_names.get(f"120100{key:012d}", f"武装 {key}")}')
                       for key in self.counter_weapons)
        if can_defend:
            options.append((3, 0, '防御'))
        if can_evade:
            options.append((2, 0, '回避'))
        response = ttk.Combobox(panel, state='readonly', values=[entry[2] for entry in options], width=37)
        response.current(0)
        response.pack(fill='x', pady=(7, 3))
        supporters = {}
        original_support = {}
        forecasts = {}
        if self.counter_support:
            self.label(panel, text='支援反击', bg=PANEL, fg=OWN).pack(anchor='w', pady=(7, 0))
            for number, (enabled, key, _) in self.counter_support.items():
                row = tk.Frame(panel, bg=PANEL)
                row.pack(fill='x', pady=(4, 0))
                self.label(row, text=self.unit_name(number), bg=PANEL).pack(anchor='w')
                weapons = self.counter_support_weapons.get(number, {})
                default_name, default_en = weapons.get(key, (
                    self.weapon_names.get(f'120100{key:012d}', f'武装 {key}'), -1))
                cost = f' · EN {default_en}' if default_en >= 0 else ''
                labels = ['不参加', f'{default_name}{cost}（推荐）']
                keys = [None, 0]
                for weapon_id, (name, en) in weapons.items():
                    if weapon_id != key:
                        labels.append(f'{name} · EN {en}' if en >= 0 else name)
                        keys.append(weapon_id)
                picker = ttk.Combobox(row, state='readonly', values=labels, width=37)
                picker.current(1 if enabled else 0)
                picker.pack(fill='x')
                supporters[number] = picker, keys
                estimate = self.label(row, text='', bg=PANEL, fg=MUTED)
                estimate.pack(side='left')
                gauge = tk.Canvas(row, width=80, height=13, bg=PANEL, highlightthickness=0)
                gauge.pack(side='right', padx=(5, 0))
                forecasts[number] = estimate, gauge
                if enabled:
                    original_support[number] = 0

        def read_selection():
            action, key, _ = options[response.current()]
            picked = {number: keys[picker.current()] for number, (picker, keys) in supporters.items()
                      if picker.current() > 0}
            if len(picked) > 3:
                notice.configure(text='最多选择三台支援机体')
                return
            if chosen.get() in picked:
                notice.configure(text='同一台机体只能选择支援防御或支援反击')
                return
            if response.current() and action == 3 and chosen.get():
                notice.configure(text='选择防御时，请取消队友代挡')
                return
            parts = [chosen.get(), action if response.current() else -1, key]
            if picked != original_support:
                padding = [0] * (3 - len(picked))
                parts.extend([*picked, *padding, *picked.values(), *padding])
            notice.configure(text='')
            return ' '.join(map(str, parts))

        def render_forecast(status):
            current = status == 'ready'
            for number, (label, gauge) in forecasts.items():
                picker, _ = supporters[number]
                result = self.defense_battle_actions.get(number)
                unit = self.units.get(number)
                text = ''
                cost = None
                if picker.current() > 0:
                    if current and result and result[0] == 'counter_support' and result[5] >= 0:
                        _, _, _, hp, barrier, hit, cost = result
                        shield = f' · 屏障 {barrier:,}' if barrier else ''
                        text = f'预计伤害 {hp:,} HP{shield} · 命中 {hit}%'
                    elif current:
                        text = '本次交战未发动支援反击'
                    elif status == 'pending':
                        text = '正在计算'
                label.configure(text=text)
                gauge.delete('all')
                if unit and cost is not None:
                    draw_en_bar(gauge, (1, 1, 79, 12), *self.unit_energy(unit), cost, (self.font[0], 8, 'bold'),
                                tags=(f'defense-en-{number}',))
            lines = [f'{name}  {amount}' for name, amount in self.damage_rows(self.defense_damage)] if current else []
            loss.configure(text='\n'.join(lines) or
                           ('正在计算双方预计 HP' if status == 'pending' else '预判不可用，请调整应对'))
            self.defense_confirm.configure(state='normal' if current else 'disabled')

        def preview(_event=None):
            self.request_defense_forecast(read_selection())

        def confirm():
            if fields := read_selection():
                self.send_defense_response(f'd {fields}')

        self.label(panel, text='出手且命中时的 HP', bg=PANEL, fg=OWN).pack(anchor='w', pady=(8, 0))
        loss = self.label(panel, text='', bg=PANEL, wraplength=370, justify='left')
        loss.pack(fill='x', pady=(2, 0))

        notice = self.label(panel, text='', bg=PANEL, fg=ENEMY)
        notice.pack(fill='x', pady=(4, 0))
        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='自动应对', command=lambda: self.send_defense_response('d auto')).pack(side='left')
        self.defense_auto_turn = ttk.Button(buttons, text='本回合自动', command=self.auto_defend_turn)
        self.defense_auto_turn.pack(side='left', padx=4)
        self.defense_confirm = ttk.Button(buttons, text='确认应对', style='Primary.TButton', command=confirm)
        self.defense_confirm.pack(side='right')
        panel.place(relx=1, x=-12, y=self.actor_card_bottom(), anchor='ne')
        panel.lift()
        self.defense_render = render_forecast
        response.bind('<<ComboboxSelected>>', preview)
        for picker, _ in supporters.values():
            picker.bind('<<ComboboxSelected>>', preview)
        chosen.trace_add('write', lambda *_: preview())
        preview()

    def request_defense_forecast(self, selection):
        self.defense_latest = selection
        self.defense_damage.clear()
        self.defense_received.clear()
        self.defense_battle_actions.clear()
        self.defense_render('pending' if selection else 'invalid')
        if not self.defense_pending and selection:
            self.defense_pending = self.backend.send(f'f {selection}')
            self.defense_requested = selection if self.defense_pending else None
            if not self.defense_pending:
                self.defense_render('failed')
        self.refresh_controls()
        self.draw()

    def finish_defense_forecast(self, success):
        if not self.defense_render or not self.defense_pending:
            return
        self.defense_pending = False
        if self.defense_latest != self.defense_requested:
            self.request_defense_forecast(self.defense_latest)
            return
        if not success:
            self.defense_damage.clear()
            self.defense_received.clear()
            self.defense_battle_actions.clear()
        self.defense_render('ready' if success else 'failed')
        self.refresh_controls()
        self.draw()

    def auto_defend_turn(self):
        self.send_defense_response('d auto_turn')

    def send_defense_response(self, command):
        if not self.incoming or not self.defense_panel.winfo_manager():
            return
        if self.backend.send(command):
            self.auto_respond = command == 'd auto_turn'
            self.defense_panel.place_forget()
            self.defense_pending = False
            self.status.set('正在启用本回合自动应对' if self.auto_respond else self.auto_phase_label)
        else:
            self.status.set('战斗进程已退出')
        self.refresh_controls()

    def click_map(self, event):
        if not self.browsing_weapons or not self.width:
            return
        if self.map_camera:
            x, z = map(floor, self.map_camera.ground_at(event.x, event.y))
        else:
            left, top, cell = self.layout
            x = (event.x - left) // cell
            z = self.height - 1 - (event.y - top) // cell
        left, bottom, right, top = self.active_map_area()
        if not (left <= x < right and bottom <= z < top):
            return
        if self.map_weapon is not None:
            if self.map_preview and self.map_preview[0] == 2 and (x, z) in self.map_taps:
                self.map_command('t', x, z)
            return
        clicked = next((unit for unit in self.units.values() if (x, z) in unit.cells), None)
        if self.skill_selection is not None:
            if self.skill_manual_target and clicked and clicked.number in self.skill_candidates:
                command = f'v {self.selected} {self.skill_selection} {clicked.number}'
                if self.skill_selection == SUPPORTER_SKILL:
                    command += f' {x} {z}'
                self.send(command, '正在读取原版技能目标')
            return
        if self.confirming_attack:
            if self.support_action_unit is None and clicked and clicked.number in self.support_candidates:
                self.open_support_actions(clicked.number)
            return
        if self.selecting_weapon:
            if clicked and (clicked.number in self.weapons or clicked.side in (2, 3, 5, 6)):
                self.select_target(clicked.number)
            elif clicked and clicked.aura_effects:
                self.aura_preview_unit = 0 if self.aura_preview_unit == clicked.number else clicked.number
                self.refresh_controls()
                self.draw()
            elif not clicked and self.moved_unit != self.selected and (x, z) in self.moves and self.ready:
                self.move_selected(x, z)
            return
        if self.ready and self.selected and (x, z) in self.moves and (clicked is None or clicked.number == self.selected):
            self.move_selected(x, z)
        elif clicked and clicked.manual:
            if self.ready:
                self.select_unit(clicked.number)
        elif clicked:
            self.select_target(clicked.number)

    def move_selected(self, x, z):
        unit = self.units[self.selected]
        if self.send(f'm {self.selected} {x} {z}', '原版正在执行移动'):
            dx, dz = x - unit.x, z - unit.z
            self.pending_move = replace(unit, x=x, z=z,
                                        footprint=frozenset((a + dx, b + dz) for a, b in unit.cells))
            self.draw()

    def map_cell_size(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        left, bottom, right, top = self.active_map_area()
        horizontal = (w - 32) // (right - left)
        vertical = (h - 32) // (top - bottom)
        fit = min(horizontal, vertical) if self.fit_board else min(horizontal, round(vertical * 1.55))
        return max(1, round(fit * self.zoom))

    def camera_for_canvas(self, width, height):
        left, bottom, right, top = self.active_map_area()
        center_x, center_z = self.map_focus or ((left + right) / 2, (bottom + top) / 2)
        camera = BattleCamera(center_x, center_z, width, height, self.zoom)
        if self.fit_board:
            # At the original pitch, the near row occupies more horizontal space.
            _, far_y = camera.project(left, 0, top)
            near_right, near_y = camera.project(right, 0, bottom)
            near_left, _ = camera.project(left, 0, bottom)
            ratio = min((width / 2 - 16) / max(width / 2 - near_left, near_right - width / 2, 1),
                        (height / 2 - 16) / max(height / 2 - far_y, near_y - height / 2, 1))
            camera = replace(camera, zoom=max(0.05, min(self.zoom, ratio * self.zoom)))
        return camera

    def pan_camera_to(self, world, x, y, camera):
        dx, dz = camera.ground_offset(x, y)
        left, bottom, right, top = self.active_map_area()
        self.map_focus = (max(left + 0.5, min(right - 0.5, world[0] - dx)),
                          max(bottom + 0.5, min(top - 0.5, world[1] - dz)))
        self.draw()

    def place_map(self, left, top, cell):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        min_x, min_z, max_x, max_z = self.active_map_area()
        centered_left = (w - (max_x - min_x) * cell) // 2 - min_x * cell
        centered_top = (h - (max_z - min_z) * cell) // 2 - (self.height - max_z) * cell
        self.map_offset = left - centered_left, top - centered_top
        self.draw()

    def zoom_map(self, event):
        if self.width and event.delta:
            if self.map_camera:
                world = self.map_camera.ground_at(event.x, event.y)
                self.zoom = max(0.20, min(2.6, self.map_camera.zoom * 1.18 ** (event.delta / 120)))
                self.fit_board = False
                self.pan_camera_to(world, event.x, event.y, replace(self.map_camera, zoom=self.zoom))
                return 'break'
            left, top, old_cell = self.layout
            self.zoom = max(0.65, min(3.5, self.zoom * 1.18 ** (event.delta / 120)))
            cell = self.map_cell_size()
            ratio = cell / old_cell
            self.place_map(round(event.x - (event.x - left) * ratio),
                           round(event.y - (event.y - top) * ratio), cell)
        return 'break'

    def begin_pan(self, event):
        if self.width and (self.pan_start is None or self.pan_start[0] == event.num):
            self.cancel_pan()
            self.pan_start = event.num, event.x, event.y, event.num == 1 and self.browsing_weapons
        return 'break'

    def pan_map(self, event):
        if self.pan_start is None:
            return 'break'
        button, x, y, can_click = self.pan_start
        dx, dy = event.x - x, event.y - y
        if dx == dy == 0:
            return 'break'
        if button == 1 and not self.pan_dragging and max(abs(dx), abs(dy)) < MAP_DRAG_THRESHOLD:
            return 'break'
        if not self.pan_dragging:
            self.pan_dragging = True
            self.canvas.configure(cursor='fleur')
        left, top, cell = self.layout
        self.pan_start = button, event.x, event.y, can_click
        if self.map_camera:
            self.pan_camera_to(self.map_camera.ground_at(x, y), event.x, event.y, self.map_camera)
        else:
            self.place_map(left + dx, top + dy, cell)
        return 'break'

    def end_pan(self, event):
        if self.pan_start is None or self.pan_start[0] != event.num:
            return 'break'
        self.pan_map(event)
        click = self.pan_start[3] and not self.pan_dragging
        self.cancel_pan()
        if click and 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height():
            self.click_map(event)
        return 'break'

    def cancel_pan(self):
        self.pan_start = None
        if self.pan_dragging:
            self.pan_dragging = False
            self.canvas.configure(cursor='')

    def idle_image(self, unit, size, facing, *, board=False):
        koma_id = self.idle_index.get(str(unit.master_id))
        if ImageTk is None:
            return None
        assets = exported_mech_assets()
        if not koma_id and assets:
            try:
                koma_id = current_catalog().mecha_koma(unit.master_id)
            except (OSError, ValueError, KeyError):
                pass
        if not koma_id:
            return None
        acted = unit.manual and not unit.ready
        key = (koma_id, size, facing, acted)
        cache = self.board_idle_images if board else self.idle_images
        if key not in cache:
            try:
                path = IDLE_DIR / koma_id / f'{facing}.png'
                if facing != 'front' and not path.is_file():
                    path = path.with_name('front.png')
                if not path.is_file() and assets:
                    path = assets / 'koma' / f'ogk_{koma_id}.png'
                with Image.open(path) as source:
                    image = source.convert('RGBA')
                if board:
                    visible = image.getchannel('A').getbbox()
                    if visible:
                        image = image.crop(visible)
                    image = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
                else:
                    image.thumbnail(size, Image.Resampling.LANCZOS)
                if acted:
                    image.putalpha(image.getchannel('A').point(lambda alpha: alpha * 2 // 3))
                cache[key] = ImageTk.PhotoImage(image, master=self.root)
            except OSError:
                cache[key] = None
        return cache[key]

    def draw_mounted_idle(self, unit, facing, camera, center, cell):
        if ImageTk is None:
            return None
        rider_id = self.idle_index.get(str(unit.master_id))
        acted = unit.manual and not unit.ready
        # KomaHolder places the carrier 0.25 units behind the rider on local Z.
        lift = 0.25 + 0.30 * unit.sfs_floating
        layers = []
        for koma_id, offset, tag in ((unit.sfs, -0.25, 'sfs_idle'), (rider_id, 0, 'idle_koma')):
            corners = self.sfs_geometry.get(koma_id, {}).get(facing)
            if corners is None:
                return None
            key = (koma_id, facing, acted)
            if key not in self.mounted_sources:
                try:
                    with Image.open(IDLE_DIR / koma_id / f'{facing}.png') as source:
                        image = source.convert('RGBA')
                    if acted:
                        image.putalpha(image.getchannel('A').point(lambda alpha: alpha * 2 // 3))
                    self.mounted_sources[key] = image
                except OSError:
                    return None
            layers.append((self.mounted_sources[key], corners, lift + offset, tag))

        top = float('inf')
        for source, corners, z_offset, tag in layers:
            vertices = []
            for x, y, z in corners:
                z += z_offset
                if camera:
                    sx, sy = camera.project(unit.x + x, y, unit.z + z)
                    depth = camera.view_point(unit.x + x, y, unit.z + z)[2]
                else:
                    sx = center[0] + (x - 0.5) * cell
                    sy = center[1] - (PITCH_COS * y + PITCH_SIN * (z - 0.5)) * cell
                    depth = 1.0
                vertices.append((sx * depth, sy * depth, depth))
            origin, right, down = vertices
            opposite = tuple(r + d - o for o, r, d in zip(origin, right, down))
            screen = [(x / depth, y / depth) for x, y, depth in (*vertices, opposite)]
            top = min(top, min(y for _, y in screen))
            left = max(0, floor(min(x for x, _ in screen)))
            upper = max(0, floor(min(y for _, y in screen)))
            width = min(self.canvas.winfo_width(), ceil(max(x for x, _ in screen))) - left
            height = min(self.canvas.winfo_height(), ceil(max(y for _, y in screen))) - upper
            if width <= 0 or height <= 0:
                continue

            # Invert the projected sprite plane for Pillow's destination-to-source transform.
            o, r, d = [(x - left * depth, y - upper * depth, depth) for x, y, depth in vertices]
            u = tuple(a - b for a, b in zip(r, o))
            v = tuple(a - b for a, b in zip(d, o))

            def cross(a, b):
                return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

            w = cross(u, v)
            coefficients = (*[value * source.width / w[2] for value in cross(v, o)],
                            *[value * source.height / w[2] for value in cross(o, u)], w[0] / w[2], w[1] / w[2])
            raster = source.transform((width, height), Image.Transform.PERSPECTIVE, coefficients,
                                      Image.Resampling.BICUBIC)
            image = ImageTk.PhotoImage(raster, master=self.root)
            self.mounted_images.append(image)
            self.canvas.create_image(left, upper, anchor='nw', image=image, tags=(tag, f'unit-{unit.number}'))
        return top

    def draw_footprint(self, tiles, border, line_width):
        canvas = self.canvas
        for (x, z), (a, b, c, d) in tiles.items():
            left = (x - 1, z) not in tiles
            top = (x, z + 1) not in tiles
            right = (x + 1, z) not in tiles
            bottom = (x, z - 1) not in tiles
            if self.map_camera:
                insets = tuple(0.035 * edge for edge in (left, top, right, bottom))
                tl, tr, br, bl = self.map_camera.cell_polygon(x, z, insets)
            else:
                a, b, c, d = a + 2 * left, b + 2 * top, c - 2 * right, d - 2 * bottom
                tl, tr, br, bl = (a, b), (c, b), (c, d), (a, d)
            for exterior, edge in ((left, (*tl, *bl)), (top, (*tl, *tr)),
                                   (right, (*tr, *br)), (bottom, (*bl, *br))):
                if exterior:
                    canvas.create_line(*edge, fill=border, width=line_width)

    def place_forecast_label(self, box, occupied, reserved, icon_size):
        a, b, c, d = box
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        left = max(4 + icon_size, min(a, width - 4 - (c - a)))
        top = max(4, b)

        def free(x, y):
            if x - icon_size < 3 or x + c - a > width - 3 or y < 3 or y + d - b > height - 3:
                return False
            return all(x + c - a + 4 <= q[0] or x - icon_size - 4 >= q[2] or
                       y + d - b + 2 <= q[1] or y - 2 >= q[3] for q in (*occupied, *reserved))

        for y in (top, max(4, top - (d - b + 4)), max(4, top - (d - b + 4) * 2)):
            adjacent = next((q[0] - (c - a) - 6 for q in reserved
                             if y < q[3] and y + d - b > q[1] and left < q[2] and left + c - a > q[0]), left)
            for x in (left, adjacent, left - (c - a) - icon_size * 2):
                if free(x, y):
                    return x - a, y - b
        return left - a, top - b

    def draw_battle_markers(self, units, bounds, cell):
        if self.incoming:
            attacker, target, weapon = self.incoming
            actions = self.defense_battle_actions.copy()
            if not actions.get(attacker, ('attack', 0))[1]:
                name = self.weapon_names.get(f'120100{weapon:012d}', '')
                actions[attacker] = ('attack', weapon, name, 0, 0, -1, 0)
            damage, received = self.defense_damage, self.defense_received
        elif self.confirming_attack:
            attacker, target = self.selected, self.target
            actions = self.battle_actions.copy()
            if (selection := self.attack_selection()) and not actions.get(attacker, ('attack', 0))[1]:
                key, name = self.weapons[target][selection[2]]
                actions[attacker] = ('attack', key, name, 0, 0, -1, 0)
            damage, received = self.forecast_damage, self.battle_received
        else:
            return

        canvas = self.canvas
        font_size = max(8, min(12, cell // 4))
        icon_size = max(12, min(24, round(cell * 0.34)))
        energy_costs = self.active_en_costs()
        occupied_labels = []
        ordered_units = sorted(units, key=lambda unit: unit.number not in (attacker, target))
        widgets = (self.actor_card, self.target_card, self.defense_panel, self.command_panel, self.order_bar)
        label_limits = []
        for widget in widgets:
            if widget.winfo_ismapped():
                left = widget.winfo_rootx() - canvas.winfo_rootx()
                top = widget.winfo_rooty() - canvas.winfo_rooty()
                label_limits.append((left, top, left + widget.winfo_width(), top + widget.winfo_height()))
        for unit in ordered_units:
            number = unit.number
            if number not in actions and number not in (attacker, target):
                continue
            role, key, name, _, _, hit, _ = actions.get(number, ('none', 0, '', 0, 0, -1, 0))
            role_label = '武装' if number == attacker else '目标' if number == target else ''
            if not role_label and not key and role not in ('defense', 'attack_defense'):
                continue
            if key:
                caption = name or f'武装 {key}'
                if hit >= 0:
                    caption += f'  {hit}%'
            elif role in ('evade', 'guard', 'defense', 'attack_defense'):
                caption = BATTLE_ACTIONS[role]
            else:
                caption = ''
            if damage.get(number, (0,))[0] and '支援防御' not in caption:
                caption = '  ·  '.join(filter(None, (caption, '援防代挡')))
            if (loss := received.get(number, (0, 0))[0]) and (role_label or damage.get(number, (0,))[0]):
                caption = '  '.join(filter(None, (caption, f'−{loss:,} HP')))
            label = '  '.join(filter(None, (role_label, caption)))
            tiles = [bounds(x, z) for x, z in unit.cells]
            x = (min(t[0] for t in tiles) + max(t[2] for t in tiles)) / 2
            y = min(t[1] for t in tiles) - max(5, cell // 7)
            if not (-cell <= x <= canvas.winfo_width() + cell and -cell <= y <= canvas.winfo_height()):
                continue
            side = 'own' if unit.side == 0 else 'enemy' if unit.side in (2, 3, 5, 6) else 'other'
            color = OWN if side == 'own' else ENEMY if side == 'enemy' else ALLY
            tags = ('battle_marker', f'battle-marker-{number}')
            if role_label:
                # Use a separate outline so the target stays identifiable even when it evades or is covered.
                self.draw_footprint({pos: bounds(*pos) for pos in unit.cells}, color, max(2, cell // 15))
            which = 'target' if number == target else side
            sprite = self.small_image(f'forecast/{which}.png', icon_size, upscale=True)
            icon_x = x - icon_size // 2
            text = canvas.create_text(icon_x + 4, y - 1, text=label, fill=color if role_label else TEXT,
                                      font=(self.font[0], font_size, 'bold'), anchor='sw', tags=tags)
            box = canvas.bbox(text)
            cost = energy_costs.get(number)
            bar_width = 64 if cost is not None else 0
            a, b, c, d = box
            dx, dy = self.place_forecast_label((a, b, c + bar_width, d), occupied_labels, label_limits, icon_size)
            canvas.move(text, dx, dy)
            a, b, c, d = canvas.bbox(text)
            occupied_labels.append((a - icon_size, b, c + bar_width, d))
            if dx or dy:
                line = canvas.create_line(x, y + 4, max(a, min(x, c)), d, fill=color, tags=tags)
                canvas.tag_lower(line, text)
            panel = canvas.create_rectangle(a - 3, b - 1, c + 3, d + 1, fill=BG, outline='', tags=tags)
            canvas.tag_lower(panel, text)
            if cost is not None:
                draw_en_bar(canvas, (c + 4, b + 2, c + bar_width - 2, d - 2), *self.unit_energy(unit), cost,
                            (self.font[0], 8, 'bold'), tags=(*tags, f'marker-en-{number}'))
            if sprite:
                canvas.create_image(a - 3, d, anchor='se', image=sprite, tags=tags)
            else:
                radius = icon_size / 3
                canvas.create_oval(a - 3 - 2 * radius, d - 2 * radius, a - 3, d,
                                   fill=BG, outline=color, width=2, tags=tags)

    def draw(self):
        canvas = self.canvas
        canvas.delete('all')
        self.mounted_images.clear()
        if not self.width:
            w, h = canvas.winfo_width(), canvas.winfo_height()
            if not self.startup_panel.winfo_manager():
                canvas.create_text(w / 2, h / 2, text=self.status.get(), fill=MUTED, font=(self.font[0], 13))
            return
        w, h = canvas.winfo_width(), canvas.winfo_height()
        camera = self.camera_for_canvas(w, h) if self.map_camera_zoom is not None else None
        self.map_camera = camera
        cell = max(1, round(camera.cell_size)) if camera else self.map_cell_size()
        if self.idle_size != cell:
            self.idle_size = cell
            self.board_idle_images.clear()
            self.board_badge_images.clear()

        min_x, min_z, max_x, max_z = self.active_map_area()

        def offset(viewport, shift, first, last):
            start = (viewport - (last - first) * cell) // 2 - first * cell + round(shift)
            # Let the outermost cells reach the center, clear of the battle HUD.
            return max(round(viewport / 2 - (last - 0.5) * cell),
                       min(round(viewport / 2 - (first + 0.5) * cell), start))

        left = offset(w, self.map_offset[0], min_x, max_x)
        top = offset(h, self.map_offset[1], self.height - max_z, self.height - min_z)
        self.layout = (left, top, cell)
        has_background = self.draw_camera_background(camera) if camera else self.draw_map_background(left, top, cell)

        def bounds(x, z):
            if camera:
                corners = camera.cell_polygon(x, z)
                xs, ys = zip(*corners)
                return min(xs), min(ys), max(xs), max(ys)
            px, py = left + x * cell, top + (self.height - 1 - z) * cell
            return px, py, px + cell, py + cell

        map_fills = {}
        for slot, points in self.map_slot_effects.items():
            active, inactive, _ = MAP_SLOT_COLORS[slot % len(MAP_SLOT_COLORS)]
            for point in points:
                if slot == self.map_edit_slot or point not in map_fills:
                    map_fills[point] = active if slot == self.map_edit_slot else inactive
        attack_cells = set()
        if self.target and self.map_weapon is None and self.skill_selection is None:
            for number in self.attackable_targets():
                attack_cells.update(self.units[number].cells)
        weapon_range = self.selectable_weapon_ranges.get(self.active_weapon(), ()) if (
            self.selecting_weapon and not self.confirming_attack and self.map_weapon is None and
            self.skill_selection is None
        ) else ()
        show_move_markers = (self.selected and not self.target and not self.selecting_weapon and not self.confirming_attack
                             and self.map_weapon is None and self.skill_selection is None and not self.incoming
                             and not self.auto_phase_label and not self.outcome)
        aura_owner = self.aura_preview_unit or self.target or self.selected
        aura = AURA_MARKERS.get(self.aura_types.get(aura_owner))
        aura_cells = self.aura_cells.get(aura_owner, ()) if not self.incoming and not self.auto_phase_label else ()
        if camera:
            visible_x, visible_z = zip(*(camera.ground_at(x, y) for x in (0, w) for y in (0, h)))
            first_x, last_x = max(0, floor(min(visible_x))), min(self.width, floor(max(visible_x)) + 1)
            first_z, last_z = max(0, floor(min(visible_z))), min(self.height, floor(max(visible_z)) + 1)
        else:
            first_x = max(0, -left // cell)
            last_x = min(self.width, (w - left + cell - 1) // cell)
            first_z = self.height - min(self.height, (h - top + cell - 1) // cell)
            last_z = self.height - max(0, -top // cell)
        for z in range(max(first_z, min_z), min(last_z, max_z)):
            for x in range(max(first_x, min_x), min(last_x, max_x)):
                point = (x, z)
                if point in map_fills:
                    fill = map_fills[point]
                elif point in self.map_points:
                    fill = '#95542d'
                elif point in self.map_effect:
                    fill = '#683c3c'
                elif point in self.map_taps:
                    fill = '#5a4e28'
                elif point == self.skill_point:
                    fill = '#47879c'
                elif point in self.skill_range:
                    fill = '#254a62'
                elif point in attack_cells:
                    fill = '#b54b50'
                elif point in weapon_range:
                    fill = '#763747'
                elif not self.target and self.map_weapon is None and self.skill_selection is None and point in self.moves:
                    fill = '#254e73'
                else:
                    objective = self.map_objectives.get(point)
                    fill = OBJECTIVE_MARKERS[objective][0] if objective else (
                        '' if has_background else '#192735' if (x + z) % 2 else '#1b2a39')
                stipple = 'gray50' if has_background and fill else ''
                outline = '#d3ad69' if point in self.map_overlap else '#8497a6' if camera else GRID
                if camera:
                    canvas.create_polygon(*(c for v in camera.cell_polygon(x, z) for c in v),
                                          fill=fill, stipple=stipple, outline=outline, tags=('map_tile',))
                else:
                    canvas.create_rectangle(*bounds(x, z), fill=fill, stipple=stipple,
                                            outline=outline, tags=('map_tile',))
                supported = show_move_markers and point in self.support_cells
                danger = show_move_markers and any(point in cells for cells in self.danger_cells.values())
                if (aura and point in aura_cells) or danger or supported:
                    a, b, c, d = bounds(x, z)
                    center = camera.project(x + 0.5, 0, z + 0.5) if camera else ((a + c) / 2, (b + d) / 2)
                    if aura and point in aura_cells:
                        tile = self.small_image(f'map_markers/{aura}.png', max(1, round(c - a)), upscale=True)
                        if tile:
                            canvas.create_image(*center, image=tile, tags=('aura_range',))
                    if danger:
                        for kind, (name, scale) in DANGER_MARKERS.items():
                            if point in self.danger_cells[kind]:
                                icon = self.small_image(f'map_markers/{name}.png',
                                                        max(1, round(min(c - a, d - b) * scale)), upscale=True)
                                if icon:
                                    canvas.create_image(*center, image=icon, tags=('danger_cell', name))
                    if supported:
                        badge_size = max(1, round((c - a) * SUPPORT_CELL_ICON_SCALE))
                        badge = self.small_image('map_markers/support_defense.png', badge_size, upscale=True)
                        if badge:
                            sx, sy = camera.project(x + 0.83, 0, z + 0.17) if camera else (
                                a + (c - a) * 0.83, d - (d - b) * 0.17)
                            canvas.create_image(sx, sy, image=badge, tags=('support_cell',))
        for kind, x, z in sorted(self.map_pins):
            if not (min_x <= x < max_x and min_z <= z < max_z):
                continue
            color, name = OBJECTIVE_MARKERS[kind]
            stamp = self.small_image(f'map_markers/{name}.png', max(13, round(cell * 0.8)), upscale=True)
            sx, sy = camera.project(x, 0, z) if camera else (left + x * cell, top + (self.height - z) * cell)
            if stamp:
                canvas.create_image(sx, sy, anchor='s', image=stamp, tags=('map_objective_pin',))
            else:
                canvas.create_polygon(sx, sy, sx - 5, sy - 8, sx, sy - 15, sx + 5, sy - 8,
                                      fill=color, outline=TEXT, tags=('map_objective_pin',))
        # Larger sprites overlap the next row. Paint the units closest to the viewer last.
        units = [self.pending_move if self.pending_move and unit.number == self.pending_move.number else unit
                 for unit in self.units.values()]
        if self.incoming:
            units = [replace(unit, direction=self.defense_outline[unit.number][0],
                             footprint=self.defense_outline[unit.number][1])
                     if self.defense_outline.get(unit.number, (0, ()))[1] else unit for unit in units]
        hp_forecast = self.active_hp_forecast()
        bar_height = max(3, min(12, round(cell * 0.15)))
        bar_inset = min(3, cell // 4)
        for unit in sorted(units, key=lambda unit: min(z for _, z in unit.cells), reverse=True):
            tiles = {position: bounds(*position) for position in unit.cells}
            a = min(box[0] for box in tiles.values())
            b = min(box[1] for box in tiles.values())
            c = max(box[2] for box in tiles.values())
            d = max(box[3] for box in tiles.values())
            color = OWN if unit.side == 0 else ENEMY if unit.side in (2, 3, 5, 6) else ALLY
            focused = unit.number in (self.selected, self.target)
            attackable = unit.number in (self.map_targets if self.map_weapon is not None else self.weapons)
            border = color if focused else ENEMY if attackable else GRID
            skill_target = unit.number in self.skill_targets
            skill_candidate = self.skill_manual_target and unit.number in self.skill_candidates
            if self.skill_selection is not None:
                border = '#8ce6ff' if skill_target else '#67a2cf' if skill_candidate else TEXT if focused else GRID
            support = self.support_candidates.get(unit.number) if self.confirming_attack else None
            if support:
                border = '#ffd17d' if support[0] else '#b49a6f'
            self.draw_footprint(tiles, border, 2 if focused or attackable or skill_target or skill_candidate or support else 1)
            facing = FACING_IDLE.get(unit.direction, 'front') if unit.sfs or len(tiles) > 1 else 'front'
            scale = KOMA_LARGE_SCALE if len(tiles) > 1 else KOMA_SINGLE_SCALE
            if camera:
                xa, za = min(x for x, _ in tiles), min(z for _, z in tiles)
                xb, zb = max(x for x, _ in tiles) + 1, max(z for _, z in tiles) + 1
                local_cell = camera.focal_length / camera.view_point(0, 0, (za + zb) / 2)[2]
                size = max(1, round((xb - xa) * local_cell * scale)), max(1, round((zb - za) * local_cell * scale))
                center_x, center_y = camera.project((xa + xb) / 2, 0, (za + zb) / 2)
            else:
                size = max(1, round((c - a) * scale)), max(1, round((d - b) * scale))
                local_cell = cell
                center_x, center_y = (a + c) / 2, (b + d) / 2
            mounted_top = self.draw_mounted_idle(unit, facing, camera, (center_x, center_y), local_cell) if unit.sfs else None
            if mounted_top is None:
                image = self.idle_image(unit, size, facing, board=True)
                if image is not None:
                    canvas.create_image(center_x, center_y - local_cell * KOMA_LIFT, image=image,
                                        tags=('idle_koma', f'unit-{unit.number}'))
                else:
                    canvas.create_text((a + c) / 2, (b + d) / 2 - 1, text=str(unit.number),
                                       fill=MUTED if unit.manual and not unit.ready else color,
                                       font=(self.font[0], max(7, min(11, cell // 2)), 'bold'))
            bottom_row = [box for box in tiles.values() if box[3] == d]
            hp_left = min(box[0] for box in bottom_row) + bar_inset
            hp_right = max(box[2] for box in bottom_row) - bar_inset
            bar_top = d - 3 - bar_height
            hp_box = (hp_left, bar_top, hp_right, d - 3)
            hp_change = hp_forecast.get(unit.number)
            hp_tags = ('unit_overlay', f'unit-hp-{unit.number}')
            draw_hp_bar(canvas, hp_box, unit, color, hp_change, tags=hp_tags)
            if self.map_weapon is not None and hp_change and (
                unit.number in self.map_targets or hp_change[1] != hp_change[2]
            ):
                draw_bar_text(canvas, hp_box, f'{max(0, hp_change[2]):,}',
                              (self.font[0], max(7, min(10, cell // 5)), 'bold'), tags=hp_tags)
            if focused and cell >= 18:
                center = (a + c) / 2
                canvas.create_polygon(center - 4, b - 5, center + 4, b - 5, center, b + 1, fill=border, outline='',
                                      tags=('unit_overlay',))
            if unit.effects:
                icons = tuple(dict.fromkeys((effect.kind, effect.icon) for effect in unit.effects))
                badge_size = max(7, round(local_cell * 0.35))
                spacing = badge_size + 2
                first_x = center_x - (len(icons) - 1) * spacing / 2
                bottom = min(b - 6, center_y - local_cell * (KOMA_LIFT + scale / 2))
                if mounted_top is not None:
                    bottom = min(bottom, mounted_top - 2)
                tags = ('unit_overlay', 'unit_effect_icon', f'unit-effect-{unit.number}')
                for index, (kind, icon) in enumerate(icons):
                    x = first_x + index * spacing
                    color = '#65dcea' if kind == 1 else '#ff8d7c'
                    canvas.create_rectangle(x - badge_size / 2, bottom - badge_size, x + badge_size / 2, bottom,
                                            fill=BG, outline=color, tags=tags)
                    glyph = (self.small_image(f'traits/{icon}.png', badge_size - 2, upscale=True)
                             if re.fullmatch(r'trait_\d+', icon) else None)
                    if glyph:
                        canvas.create_image(x, bottom - badge_size / 2, image=glyph, tags=tags)
                    else:
                        canvas.create_text(x, bottom - badge_size / 2, text='▲' if kind == 1 else '▼',
                                           fill=color, font=(self.font[0], max(6, badge_size - 5)), tags=tags)
            if unit.number == self.selected:
                for position in self.moves & tiles.keys():
                    x, y, _, _ = tiles[position]
                    canvas.create_rectangle(x + 3, y + 3, x + 7, y + 7, fill=OWN, outline='')
            roles = [3] * unit.support_attack + [2] * unit.support_defense
            if roles:
                badge_x, badge_z = max(tiles, key=lambda point: (-point[1], point[0]))
                tile_left, tile_top, tile_right, tile_bottom = tiles[badge_x, badge_z]
                tile_size = min(tile_right - tile_left, tile_bottom - tile_top)
                if camera:
                    tile_right = camera.cell_polygon(badge_x, badge_z)[2][0]
                count = min(len(roles), 6)
                rows = min(3, count)
                gap = max(1, round(tile_size * SUPPORT_BADGE_GAP_SCALE))
                fit = floor((bar_top - tile_top) / rows) - gap
                badge_size = max(1, min(round(tile_size * SUPPORT_BADGE_SCALE), fit))
                spacing = badge_size + gap
                for index, role in enumerate(roles[:count]):
                    column, row = divmod(index, 3)
                    right = tile_right - gap - ((count > 3) - column) * spacing
                    bottom = bar_top - gap - row * spacing
                    icon = self.small_image(f'roles/{role}.png', badge_size, upscale=True)
                    if icon:
                        canvas.create_image(right, bottom, anchor='se', image=icon,
                                            tags=('unit_overlay', 'support_badge', f'support-{unit.number}'))
                if len(roles) > count:
                    canvas.create_text(right, bottom - badge_size, text=f'+{len(roles) - count}', anchor='se',
                                       fill=TEXT, font=(self.font[0], -max(6, badge_size), 'bold'),
                                       tags=('unit_overlay',))
        # Original sorting layers put the cell shield below the Koma and warnings above it.
        canvas.tag_raise('danger_cell')
        canvas.tag_raise('unit_overlay')
        self.draw_battle_markers(units, bounds, cell)
        for slot, point in self.map_slots.items():
            _, _, color = MAP_SLOT_COLORS[slot % len(MAP_SLOT_COLORS)]
            _, y, right, _ = bounds(*point)
            size = max(9, min(16, cell // 2))
            canvas.create_rectangle(right - size, y, right, y + size, fill=BG, outline=color,
                                    width=2 if slot == self.map_edit_slot else 1, tags=('map_slot',))
            canvas.create_text(right - size / 2, y + size / 2, text=str(slot + 1), fill=color,
                               font=(self.font[0], max(7, size - 6), 'bold'), tags=('map_slot',))


def main():
    root = create_root()
    BattleWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
