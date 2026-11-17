"""Single-screen editor for the original battle runner's two deployment forces."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import tkinter as tk
from tkinter import font, ttk
try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    ImageTk = None

from formation import (CAPABILITY_GRADE, ROLES, TEAM_SAVES, TERRAIN_SHORT, UNIT_RARITIES, catalog_for, current_catalog,
                       default_team, describe_error, exported_mech_assets, formation_names, load_team,
                       master_for_stage, read_player_database, save_team, team_preview, validation_errors)
from prepare_stage import ARTIFACTS
from save_library import SaveLibrary, capture_preview


SURFACE = '#101c2c'
CARD = '#192c42'
TEXT = '#eff7ff'
MUTED = '#a8bfd6'
BLUE = '#7ec8ff'
BOOST = '#92ffab'
LINE = '#365775'
INVALID = '#fa9a91'
ART = '#203b54'
ROLE_COLORS = {1: '#ffa38a', 2: '#82cfff', 3: '#f5d276'}
PILOT_ASSETS = ARTIFACTS / 'battle_ui_assets'
PICKER_TITLES = {'unit': '选择机体', 'pilot': '选择驾驶员', 'op': '装备 OP', 'supporter': '选择支援人员'}


def prepare_picture(kind, resource):
    from extract_battle_ui_assets import export_pilot_icon, export_pilot_portrait, export_supporter_portrait

    export = {'pilot': export_pilot_portrait, 'supporter': export_supporter_portrait, 'icon': export_pilot_icon}
    return export[kind](resource, PILOT_ASSETS)


class FormationEditor(tk.Frame):
    def __init__(self, parent, battle, on_close, *, team=None):
        super().__init__(parent, bg=SURFACE, padx=16, pady=9)
        self.battle = battle
        self.root = battle.root
        self.on_close = on_close
        self.font = battle.font
        self.card_title_fonts = {size: font.Font(root=self, family=self.font[0], size=size, weight='bold')
                                 for size in (9, 11)}
        self.card_subtitle_font = font.Font(root=self, family=self.font[0], size=9)
        self.catalog = current_catalog()
        self.default = default_team(self.catalog)
        self.team = deepcopy(self.default) if team is None else team
        original = read_player_database()
        self.supporter_stars = {row[2]: row[4] for row in original['user_supporter']}
        self.specialized_pilots = {row[1] for row in original['user_character'] if row[9]}
        self.selected = (0, 0)
        self.errors = []
        self.native_rejection = ()
        self.invalid_slots = set()
        self.sortie_forms = {}
        self.leader_bonus_slots = set()
        self.name_cache = {}
        self.image_sources = {}
        self.card_images = {}
        self.card_states = {}
        self.portrait_jobs = {}
        self.portrait_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='formation-portrait')
        self.portrait_poll = None
        assets = exported_mech_assets()
        self.portrait_directory = assets / 'portraits' if assets else None
        self.koma_directory = assets / 'koma' if assets else None
        self.path = None
        self.controls = {}
        self.cards = {}
        self.support_cards = {}
        self.support_images = {}
        self.chooser_kind = None
        self.chooser_apply = None
        self.chooser_options = ()
        self.chooser_visible = []
        self.chooser_current = None
        self.chooser_force = 0
        self.profile_key = None
        self.profile_icon_widgets = {}
        self.profile_details = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure((2, 3), weight=1, uniform='forces')

        toolbar = tk.Frame(self, bg=SURFACE)
        toolbar.grid(row=0, column=0, sticky='ew')
        toolbar.columnconfigure(1, weight=1)
        self.label(toolbar, '出击准备', size=17, bold=True).grid(row=0, column=0, sticky='w')
        self.terrain = self.label(toolbar, '', color=BLUE)
        self.terrain.grid(row=0, column=1, sticky='w', padx=12)
        for col, (title, command) in enumerate((('恢复默认', self.reset), ('读取队伍', self.open),
                                                ('保存队伍', self.save)), 2):
            ttk.Button(toolbar, text=title, command=command).grid(row=0, column=col, padx=(5, 0))
        self.return_button = ttk.Button(toolbar, text='返回战场', command=self.on_close)
        self.return_button.grid(row=0, column=5, padx=(5, 0))

        positions = tk.Frame(self, bg=SURFACE)
        positions.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        positions.columnconfigure(tuple(range(5)), weight=1, uniform='formation')
        for col, span, name in ((0, 3, '前方配置'), (3, 2, '后方配置')):
            self.label(positions, name, color=MUTED, size=9).grid(row=0, column=col, columnspan=span, sticky='w')
        for force in range(2):
            self.build_force(force)

        details = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=10, pady=6)
        details.grid(row=4, column=0, sticky='ew', pady=(6, 3))
        details.columnconfigure((1, 3, 5), weight=1, uniform='formation_fields')
        self.detail_title = self.label(details, '', bg=CARD, color=BLUE, bold=True)
        self.detail_title.grid(row=0, column=0, columnspan=6, sticky='ew', pady=(0, 3))
        self.add_picker(details, '机体', 'unit', 1, 0)
        self.add_picker(details, '驾驶员', 'pilot', 1, 2)
        self.add_picker(details, '装备 OP', 'op', 1, 4)
        self.add_level(details, '机体等级', 'level', 2, 0)
        self.add_level(details, '人物等级', 'pilot_level', 2, 2)
        self.add_level(details, '星级', 'stars', 2, 4)
        flags = tk.Frame(details, bg=CARD)
        flags.grid(row=3, column=0, columnspan=2, sticky='w')
        for name in ('sp', 'ssp'):
            state = tk.BooleanVar(master=self)
            widget = ttk.Checkbutton(flags, text=f'{name.upper()} 化', variable=state,
                                     command=lambda key=name, value=state: self.set_flag(key, value.get()))
            widget.pack(side='left', padx=(0, 10))
            self.controls[name] = widget, state
        self.add_level(details, 'SSP 等级', 'ssp_level', 3, 2)
        self.label(details, '武装等级', bg=CARD).grid(row=3, column=4, sticky='w', padx=(0, 6))
        weapons = tk.Frame(details, bg=CARD)
        weapons.grid(row=3, column=5, sticky='ew', pady=2)
        weapons.columnconfigure(0, weight=1)
        self.weapon_name = ttk.Combobox(weapons, width=13, state='readonly')
        self.weapon_name.grid(row=0, column=0, sticky='ew')
        self.weapon_name.bind('<<ComboboxSelected>>', lambda _: self.sync_weapon_level())
        self.weapon_level = ttk.Combobox(weapons, width=3, state='readonly')
        self.weapon_level.grid(row=0, column=1, sticky='e', padx=(4, 0))
        self.weapon_level.bind('<<ComboboxSelected>>', self.set_weapon_level)
        effect_area = tk.Frame(details, bg=CARD)
        effect_area.grid(row=4, column=0, columnspan=6, sticky='ew', pady=(3, 0))
        effect_area.columnconfigure(0, weight=1)
        self.equipped_effect = tk.Text(effect_area, bg=CARD, fg=MUTED, font=(self.font[0], 9),
                                       width=1, height=2, wrap='word', borderwidth=0,
                                       highlightthickness=0, state='disabled')
        effect_scroll = ttk.Scrollbar(effect_area, command=self.equipped_effect.yview)
        self.equipped_effect.grid(row=0, column=0, sticky='ew', padx=(0, effect_scroll.winfo_reqwidth()))

        def scroll_effects(first, last):
            effect_scroll.set(first, last)
            if float(first) > 0 or float(last) < 1:
                effect_scroll.place(relx=1, relheight=1, anchor='ne')
            else:
                effect_scroll.place_forget()

        self.equipped_effect.configure(yscrollcommand=scroll_effects)
        self.build_chooser()
        self.notice = self.label(self, '', color=MUTED, size=9)
        self.notice.grid(row=5, column=0, sticky='ew')
        self.team_saves = SaveLibrary(self, TEAM_SAVES, '.ggetteam', team_preview)
        self.refresh()

    def label(self, parent, title, *, bg=SURFACE, color=TEXT, size=10, bold=False):
        return tk.Label(parent, text=title, bg=bg, fg=color, anchor='w',
                        font=(self.font[0], size, 'bold' if bold else 'normal'))

    def build_force(self, force):
        section = tk.Frame(self, bg=SURFACE)
        section.grid(row=force + 2, column=0, sticky='nsew', pady=(3, 0))
        section.columnconfigure(tuple(range(5)), weight=1, uniform='formation')
        section.rowconfigure(1, weight=1)
        header = tk.Frame(section, bg=SURFACE)
        header.grid(row=0, column=0, columnspan=5, sticky='ew', pady=(0, 3))
        self.label(header, f'部队 {force + 1}', color=BLUE, size=11, bold=True).pack(side='left')
        supporter_label = self.label(header, '支援人员', color=MUTED, size=9)
        supporter_label.pack(side='left', padx=(16, 5))
        button = ttk.Button(header, width=29, compound='left', command=lambda: self.choose(
            'supporter', self.team[force].supporter, lambda identifier: self.set_supporter(force, identifier),
            force=force))
        button.pack(side='left')
        level = ttk.Combobox(header, width=4, state='readonly')
        self.label(header, 'Lv', color=MUTED, size=9).pack(side='left', padx=(7, 3))
        level.pack(side='left')
        level.bind('<<ComboboxSelected>>', lambda _: self.set_supporter_level(force, int(level.get())))
        self.support_cards[force] = button, level, supporter_label
        for slot in range(5):
            card = tk.Canvas(section, bg=CARD, highlightbackground=LINE, highlightthickness=1,
                             cursor='hand2', width=1, height=68)
            card.grid(row=1, column=slot, sticky='nsew', padx=(0, 5) if slot < 4 else 0)
            card.bind('<Button-1>', lambda _, f=force, s=slot: self.select(f, s))
            card.bind('<Configure>', lambda _, f=force, s=slot: self.draw_card(f, s))
            self.cards[force, slot] = card

    def add_picker(self, parent, title, kind, row, col):
        self.label(parent, title, bg=CARD).grid(row=row, column=col, sticky='w', padx=(0, 6))
        button = ttk.Button(parent, width=18, command=lambda: self.choose(
            kind, getattr(self.member, kind), lambda identifier: self.set_choice(kind, identifier)))
        button.grid(row=row, column=col + 1, sticky='ew', padx=(0, 8), pady=2)
        self.controls[kind] = button

    def picture(self, path, width, height):
        if ImageTk is None or path is None or min(width, height) < 8:
            return None
        if path not in self.image_sources:
            try:
                with Image.open(path) as source:
                    picture = source.convert('RGBA')
                    self.image_sources[path] = picture.crop(picture.getbbox())
            except OSError:
                return None
        img = ImageOps.contain(self.image_sources[path], (width, height), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img, master=self.root)

    def unit_picture(self, identifier):
        unit = self.catalog.units.get(identifier)
        if unit:
            for directory in (self.portrait_directory, PILOT_ASSETS / 'unit_portraits'):
                if directory is not None:
                    path = directory / f'ub_{unit[3]}.png'
                    if path.is_file():
                        return path
            path = ARTIFACTS / 'idle_koma' / unit[4] / 'front.png'
            if path.is_file():
                return path
            if self.koma_directory:
                return self.koma_directory / f'ogk_{unit[4]}.png'
        return None

    def pilot_picture(self, identifier, *, request=False):
        if resource := self.catalog.pilot_resource(identifier):
            target = PILOT_ASSETS / 'pilot_portraits' / f'{resource}.png'
            if target.is_file():
                return target
            if request:
                self.queue_picture('pilot', resource)
        return PILOT_ASSETS / 'pilots' / f'{identifier}.png'

    def queue_picture(self, kind, resource):
        key = kind, resource
        if resource and key not in self.portrait_jobs and ImageTk is not None:
            self.portrait_jobs[key] = self.portrait_pool.submit(prepare_picture, kind, resource)
            if self.portrait_poll is None:
                self.portrait_poll = self.after(100, self.poll_portraits)

    def supporter_picture(self, identifier):
        row = self.catalog.supporters.get(identifier)
        if row and row[7]:
            resource = row[7]
            path = PILOT_ASSETS / 'supporter_portraits' / f'{resource}.png'
            if path.is_file():
                return path
            self.queue_picture('supporter', resource)
        return None

    def update_supporter_picture(self, force):
        identifier = self.team[force].supporter
        path = self.supporter_picture(identifier)
        previous, previous_path, image = self.support_images.get(force, (None, None, None))
        if (identifier, path) != (previous, previous_path):
            image = self.picture(path, 106, 46)
            self.support_cards[force][0].configure(image=image or '')
            self.support_images[force] = identifier, path, image

    def skill_icon(self, resource):
        if not resource or resource == '0':
            return None
        path = PILOT_ASSETS / 'traits' / f'{resource}.png'
        if path.is_file():
            return self.picture(path, 26, 26)
        self.queue_picture('icon', resource)
        return None

    def poll_portraits(self):
        self.portrait_poll = None
        changed = False
        active = False
        for resource, job in self.portrait_jobs.items():
            if job is None:
                continue
            if job.done():
                try:
                    changed |= job.result() is not None
                except (OSError, ValueError, ImportError, KeyError):
                    pass
                self.portrait_jobs[resource] = None
            else:
                active = True
        if changed and self.winfo_ismapped():
            for force in self.support_cards:
                self.update_supporter_picture(force)
            for force, slot in self.cards:
                self.draw_card(force, slot)
            self.draw_chooser_preview()
            for widget, resource in self.profile_icon_widgets.items():
                if (icon := self.skill_icon(resource)) is not None:
                    widget.configure(image=icon)
                    widget.icon = icon
        if active:
            self.portrait_poll = self.after(100, self.poll_portraits)

    @staticmethod
    def fit_label(text, face, width):
        if face.measure(text) <= width:
            return text
        while text and face.measure(text + '…') > width:
            text = text[:-1]
        return text + '…'

    def draw_card(self, force, slot):
        position = force, slot
        canvas = self.cards[position]
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if min(width, height) < 12:
            return
        member = self.team[force].members[slot]
        form = self.sortie_forms.get(position)
        boost = position in self.leader_bonus_slots
        selected = self.selected == position
        invalid = position in self.invalid_slots
        border = INVALID if invalid else BLUE if selected else LINE
        footer = min(58, max(42, round(height * .29)))
        baseline = height - footer
        unit_path = self.unit_picture(form or member.unit)
        pilot_path = self.pilot_picture(member.pilot, request=baseline >= 66) if member.unit else None
        unit_state = f'Lv{member.level}  ★{member.stars}'
        if member.sp:
            unit_state += '  SP'
        if member.ssp:
            unit_state += f'  SSP {member.ssp_level}'
        state = (width, height, form, boost, invalid, unit_path, pilot_path,
                 member.unit, member.pilot, member.pilot_level, unit_state)
        if self.card_states.get(position) == state:
            if canvas.cget('highlightbackground') != border:
                canvas.configure(highlightbackground=border)
                canvas.itemconfigure('slot_number', fill=border)
            return

        canvas.configure(highlightbackground=border)
        canvas.delete('all')
        canvas.create_rectangle(1, 1, width - 1, baseline, fill=ART, outline='')
        canvas.create_polygon(0, baseline, width * .55, baseline, width - 2, 0, width * .65, 0,
                              fill='#274861', outline='')
        unit_image = self.picture(unit_path, round(width * .76), baseline - 4)
        pilot_image = self.picture(pilot_path, round(width * .39), round(baseline * .87))
        self.card_images[position] = unit_image, pilot_image
        if unit_image:
            canvas.create_image(width * .04, baseline - 2, image=unit_image, anchor='sw')
        if pilot_image:
            canvas.create_image(width - 3, baseline - 1, image=pilot_image, anchor='se')
        canvas.create_text(8, 7, text=str(slot + 1), anchor='nw', fill=border,
                           font=(self.font[0], 11, 'bold'), tags=('slot_number',))
        if form:
            canvas.create_text(width - (29 if boost else 8), 7, text='变形出击', anchor='ne',
                               fill=BLUE, font=(self.font[0], 9))
        if boost:
            self.draw_leader_badge(canvas, width)
        canvas.create_rectangle(1, baseline, width - 1, height - 1, fill=CARD, outline='')
        name = self.caption('unit', member.unit) if member.unit else '空位'
        font_size = 11 if width >= 210 else 9
        title_font = self.card_title_fonts[font_size]
        subtitle_font = self.card_subtitle_font
        canvas.create_text(7, baseline + 3, text=self.fit_label(name, title_font, width - 14), anchor='nw',
                           fill=INVALID if invalid else TEXT, font=title_font)
        if member.unit:
            available = width - 22 - subtitle_font.measure(unit_state)
            pilot_level = f' Lv{member.pilot_level}'
            pilot = self.fit_label(self.caption('pilot', member.pilot), subtitle_font,
                                   max(15, available - subtitle_font.measure(pilot_level)))
            canvas.create_text(7, height - 5, text=pilot + pilot_level, anchor='sw', fill=MUTED, font=subtitle_font)
            canvas.create_text(width - 6, height - 5, text=unit_state, anchor='se', fill=BLUE, font=subtitle_font)
        self.card_states[position] = state

    @staticmethod
    def draw_leader_badge(canvas, width):
        x = width - 15
        tags = ('leader_bonus',)
        canvas.create_rectangle(width - 28, 3, width - 4, 26, fill='#173b35', outline='#417f54', tags=tags)
        for y in (8, 15):
            canvas.create_line(x - 5, y + 5, x, y, x + 5, y + 5, fill=BOOST, width=3,
                               capstyle='round', joinstyle='round', tags=tags)

    def add_level(self, parent, title, name, row, col):
        self.label(parent, title, bg=CARD).grid(row=row, column=col, sticky='w', padx=(0, 6))
        widget = ttk.Combobox(parent, width=5, state='readonly')
        widget.grid(row=row, column=col + 1, sticky='w', pady=2)
        widget.bind('<<ComboboxSelected>>', lambda _: self.set_level(name, int(widget.get())))
        self.controls[name] = widget

    @property
    def member(self):
        force, slot = self.selected
        return self.team[force].members[slot]

    @property
    def stage(self):
        return self.battle.stage_choices.get(self.battle.stage.get())

    def caption(self, kind, item):
        if item == 0:
            return {'op': '未装备', 'unit': '不编入', 'supporter': '不编入', 'pilot': '未选择'}.get(kind, '0')
        return self.catalog.name(kind, item)

    def select(self, force, slot):
        self.close_chooser()
        position = force, slot
        previous = self.selected
        if position == previous:
            return
        self.selected = position
        self.draw_card(*previous)
        self.draw_card(*position)
        self.refresh_details()
        self.refresh_notice()

    def compatible(self, stage):
        return not validation_errors(self.team, catalog_for(master_for_stage(stage.stage_id)), stage.stage_id)

    def refresh(self):
        stage_id = self.stage.stage_id if self.stage else None
        catalog = catalog_for(master_for_stage(stage_id)) if stage_id else self.catalog
        self.errors = validation_errors(self.team, catalog, stage_id) if stage_id else [(0, 0, '请选择关卡')]
        self.terrain.configure(text=f'关卡地形  {catalog.terrain_label(stage_id)}')
        self.invalid_slots = {(force - 1, slot - 1) for force, slot, _ in self.errors}
        self.invalid_slots.update((force - 1, slot - 1) for force, slot in self.native_rejection)
        self.sortie_forms = {}
        self.leader_bonus_slots.clear()
        for force, slot in self.cards:
            team = self.team[force]
            member = team.members[slot]
            form = catalog.sortie_form(member, stage_id) if member.unit in catalog.units else None
            if form and form != member.unit:
                self.sortie_forms[force, slot] = form
            if catalog.receives_leader_bonus(form or member.unit, team.supporter,
                                             self.supporter_step(force, team.supporter)):
                self.leader_bonus_slots.add((force, slot))
            self.draw_card(force, slot)
        for force, (button, level, label) in self.support_cards.items():
            row = self.team[force]
            button.configure(text=self.caption('supporter', row.supporter))
            label.configure(fg=INVALID if (force, -1) in self.invalid_slots else MUTED)
            cap = self.catalog.supporter_max_level(row.supporter) if row.supporter in self.catalog.supporters else 100
            level.configure(values=tuple(range(1, cap + 1)) if row.supporter else ())
            level.set(row.supporter_level if row.supporter else '')
            level.state(['!disabled' if row.supporter else 'disabled'])
            self.update_supporter_picture(force)
        self.refresh_details()
        self.return_button.state(['!disabled' if self.battle.width else 'disabled'])
        self.refresh_notice()

    def refresh_notice(self):
        if self.errors:
            force, slot = self.selected
            current = next((error for error in self.errors if error[:2] == (force + 1, slot + 1)), self.errors[0])
            title = describe_error(current, self.team, self.catalog)
            if len(self.errors) > 1:
                title += f'   ·   共 {len(self.errors)} 处'
            self.notice.configure(text=title, fg=INVALID)
        elif self.native_rejection:
            title = '、'.join(f'部队 {force} 的 {slot} 号位' for force, slot in self.native_rejection)
            self.notice.configure(text=f'原版地图无法部署 {title}', fg=INVALID)
        else:
            self.notice.configure(text='编队可以出击' + (f'   ·   {self.path.stem}' if self.path else ''), fg=MUTED)

    def show_native_rejection(self, slots):
        self.native_rejection = tuple(slots)
        self.refresh()

    def edited(self):
        self.native_rejection = ()
        self.refresh()

    def refresh_details(self):
        member = self.member
        force, slot = self.selected
        unit = self.catalog.units.get(member.unit)
        terrain = ''
        if unit:
            values = self.catalog.capabilities(member)['capabilities'].values()
            terrain = '    地形适应性  ' + '   '.join(
                f'{label}{CAPABILITY_GRADE.get(value, "×")}' for label, value in zip(TERRAIN_SHORT, values))
        if form := self.sortie_forms.get(self.selected):
            terrain += f'    出击形态  {self.caption("unit", form)}'
        self.detail_title.configure(text=f'部队 {force + 1} · {slot + 1} 号位  '
                                         f'{self.caption("unit", member.unit)}{terrain}')
        for kind in ('unit', 'pilot', 'op'):
            self.controls[kind].configure(text=self.caption(kind, getattr(member, kind)))
            self.controls[kind].state(['!disabled' if kind == 'unit' or member.unit else 'disabled'])
        effects = self.catalog.part_effects(member.op) if member.unit else ()
        effect_text = 'OP 效果  ' + '  ·  '.join(effect.replace('\n', ' ') for effect in effects) if effects else ''
        if self.equipped_effect.get('1.0', 'end-1c') != effect_text:
            self.equipped_effect.configure(state='normal')
            self.equipped_effect.delete('1.0', 'end')
            self.equipped_effect.insert('1.0', effect_text)
            # Update wrapped line heights before resetting the scroll position.
            self.equipped_effect.count('1.0', 'end', 'update', 'displaylines')
            self.equipped_effect.yview_moveto(0)
            self.equipped_effect.configure(state='disabled')
        for name, start, cap in (
            ('level', 1, self.catalog.unit_max_level(member.unit, member.sp) if unit else 100),
            ('pilot_level', 1, self.catalog.pilot_max_level(member.pilot) if member.pilot in self.catalog.pilots else 100),
            ('stars', 0, self.catalog.max_stars if unit and unit[37] else 0),
            ('ssp_level', 1, max(self.catalog.ssp_exp, default=1)),
        ):
            widget = self.controls[name]
            widget.configure(values=tuple(range(start, cap + 1)))
            widget.set(getattr(member, name) if member.unit else '')
            enabled = member.unit and (name != 'ssp_level' or member.ssp)
            widget.state(['!disabled' if enabled else 'disabled'])
        for name, usable in (('sp', unit and self.catalog.can_sp(member.unit)),
                             ('ssp', member.unit in self.catalog.ssp_config)):
            widget, state = self.controls[name]
            state.set(getattr(member, name))
            widget.state(['!disabled' if usable else 'disabled'])
        old_weapon = self.weapon_name.get()
        self.weapon_choices = self.catalog.loadout_weapons(member) if unit else {}
        active_form = self.sortie_forms.get(self.selected, member.unit)
        active_weapons = (self.catalog.member_weapons(active_form, member.ssp, member.ssp_cores)
                          if active_form in self.catalog.units else {})
        self.weapon_choices = active_weapons | self.weapon_choices
        labels = [f'{index}. {name}' + ('（另一形态）' if key not in active_weapons else '')
                  for index, (key, (name, _, _)) in enumerate(self.weapon_choices.items(), 1)]
        self.weapon_name.configure(values=labels)
        self.weapon_name.state(['!disabled' if labels else 'disabled'])
        if labels:
            self.weapon_name.current(labels.index(old_weapon) if old_weapon in labels else 0)
        else:
            self.weapon_name.set('')
        self.sync_weapon_level()

    def sync_weapon_level(self):
        index = self.weapon_name.current()
        if index < 0:
            self.weapon_level.set('')
            self.weapon_level.state(['disabled'])
            return
        key = list(self.weapon_choices)[index]
        _, cap, fixed = self.weapon_choices[key]
        self.weapon_level.configure(values=(cap,) if fixed else tuple(range(1, cap + 1)))
        self.weapon_level.set(cap if fixed else self.member.weapons.get(key, 1))
        self.weapon_level.state(['disabled' if fixed else '!disabled'])

    def set_weapon_level(self, _):
        index = self.weapon_name.current()
        if index >= 0:
            key = list(self.weapon_choices)[index]
            if not self.weapon_choices[key][2]:
                self.member.weapons[key] = int(self.weapon_level.get())
                self.edited()

    def set_level(self, name, value):
        setattr(self.member, name, value)
        self.edited()

    def set_flag(self, name, value):
        member = self.member
        setattr(member, name, value)
        if name == 'sp':
            member.level = min(member.level, self.catalog.unit_max_level(member.unit, member.sp))
        else:
            if not value:
                member.ssp_cores = None
            member.weapons = {key: min(limit, member.weapons.get(key, 1))
                              for key, (_, limit, fixed) in self.catalog.loadout_weapons(member).items()
                              if not fixed}
        self.edited()

    def set_choice(self, kind, item):
        member = self.member
        if getattr(member, kind) == item:
            return
        if kind == 'unit' and item == 0:
            force, slot = self.selected
            self.team[force].members[slot] = replace(member, unit=0, level=1, stars=0, sp=False,
                                                     ssp=False, ssp_level=1, op=0, weapons={}, ssp_cores=None)
            self.edited()
            return
        setattr(member, kind, item)
        if kind == 'unit':
            member.sp = member.sp and self.catalog.can_sp(item)
            member.ssp = member.ssp and item in self.catalog.ssp_config
            member.ssp_cores = None
            member.level = min(member.level, self.catalog.unit_max_level(item, member.sp))
            member.stars = member.stars if self.catalog.units[item][37] else 0
            member.weapons = {key: 1 for key, (_, _, fixed) in self.catalog.loadout_weapons(member).items()
                              if not fixed}
            if linked := self.catalog.linked_pilot(item):
                member.pilot = linked
                member.pilot_specialized = None
                member.pilot_level = min(member.pilot_level, self.catalog.pilot_max_level(linked))
        elif kind == 'pilot':
            member.pilot_specialized = None
            member.pilot_level = min(member.pilot_level, self.catalog.pilot_max_level(item))
        self.edited()

    def set_supporter(self, force, identifier):
        row = self.team[force]
        row.supporter = identifier
        if identifier:
            row.supporter_level = min(row.supporter_level, self.catalog.supporter_max_level(identifier))
        self.edited()

    def set_supporter_level(self, force, level):
        self.team[force].supporter_level = level
        self.edited()

    def build_chooser(self):
        panel = self.chooser = tk.Frame(self, bg=CARD, padx=10, pady=7,
                                       highlightbackground=LINE, highlightthickness=1)
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(2, weight=2)
        panel.rowconfigure(2, weight=1)
        self.chooser_title = self.label(panel, '', bg=CARD, color=BLUE, bold=True)
        self.chooser_title.grid(row=0, column=0, sticky='w')
        ttk.Button(panel, text='返回编辑', command=self.close_chooser).grid(row=0, column=2, sticky='e')
        self.chooser_query = tk.StringVar(master=panel)
        self.chooser_search = ttk.Entry(panel, textvariable=self.chooser_query)
        self.chooser_search.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(3, 4))
        self.chooser_results = tk.Listbox(panel, bg=SURFACE, fg=TEXT, selectbackground='#2373ac',
                                          height=5, font=self.font, highlightthickness=0,
                                          activestyle='none', exportselection=False)
        self.chooser_results.grid(row=2, column=0, rowspan=2, sticky='nsew')
        self.chooser_scroll = ttk.Scrollbar(panel, command=self.chooser_results.yview)
        self.chooser_scroll.grid(row=2, column=1, rowspan=2, sticky='ns')
        self.chooser_results.configure(yscrollcommand=self.chooser_scroll.set)
        style = ttk.Style(panel)
        style.configure('FormationRole.Treeview', background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                        rowheight=26, font=self.font)
        style.map('FormationRole.Treeview', background=[('selected', '#2373ac')], foreground=[('selected', TEXT)])
        self.role_results = ttk.Treeview(panel, show='tree', style='FormationRole.Treeview', height=4,
                                         selectmode='browse', yscrollcommand=self.chooser_scroll.set)
        self.role_results.column('#0', width=220, minwidth=120)
        self.role_results.grid(row=2, column=0, rowspan=2, sticky='nsew')
        self.role_results.grid_remove()
        self.role_results.bind('<<TreeviewSelect>>', lambda _: self.draw_chooser_preview())
        for event in ('<Return>', '<Double-Button-1>'):
            self.role_results.bind(event, self.apply_choice)
        self.role_icons = {role: self.picture(PILOT_ASSETS / 'roles' / f'{role}.png', 20, 20)
                           for role in ROLES}
        self.chooser_effects = tk.Text(panel, bg=ART, fg=TEXT, font=self.font, width=25, height=4,
                                       wrap='word', relief='flat', padx=10, pady=6, highlightthickness=0,
                                       state='disabled')
        self.chooser_effects.grid(row=1, column=2, rowspan=2, sticky='nsew', padx=(10, 0), pady=(3, 4))
        self.chooser_effects.grid_remove()
        profile = self.chooser_profile = tk.Frame(panel, bg=ART)
        profile.grid(row=1, column=2, rowspan=2, sticky='nsew', padx=(10, 0), pady=(3, 4))
        profile.grid_remove()
        profile.columnconfigure(1, weight=1)
        profile.rowconfigure(0, weight=1)
        self.profile_art = tk.Canvas(profile, bg='#274861', width=154, height=90, highlightthickness=0)
        self.profile_art.grid(row=0, column=0, sticky='nsew')
        self.profile_art.bind('<Configure>', lambda _: self.draw_profile_art())
        self.profile_art.bind('<MouseWheel>', self.profile_wheel)
        self.profile_canvas = tk.Canvas(profile, bg=ART, height=90, width=192, highlightthickness=0)
        self.profile_canvas.configure(yscrollincrement=16)
        self.profile_canvas.grid(row=0, column=1, sticky='nsew')
        profile_scroll = ttk.Scrollbar(profile, command=self.profile_canvas.yview)
        profile_scroll.grid(row=0, column=2, sticky='ns')
        self.profile_canvas.configure(yscrollcommand=profile_scroll.set)
        self.profile_body = tk.Frame(self.profile_canvas, bg=ART, padx=8, pady=5)
        self.profile_window = self.profile_canvas.create_window(0, 0, window=self.profile_body, anchor='nw')
        self.profile_body.bind('<Configure>', lambda _: self.profile_canvas.configure(
            scrollregion=self.profile_canvas.bbox('all')))
        self.profile_canvas.bind('<Configure>', self.resize_profile)
        self.profile_canvas.bind('<MouseWheel>', self.profile_wheel)
        self.profile_body.bind('<MouseWheel>', self.profile_wheel)
        self.chooser_image = None
        self.chooser_confirm = ttk.Button(panel, text='确定', command=self.apply_choice)
        self.chooser_confirm.grid(row=3, column=2, sticky='e')
        self.chooser_query.trace_add('write', self.filter_choices)
        self.chooser_search.bind('<Return>', self.apply_choice)
        self.chooser_results.bind('<Return>', self.apply_choice)
        self.chooser_results.bind('<Double-Button-1>', self.apply_choice)
        self.chooser_results.bind('<<ListboxSelect>>', lambda _: self.draw_chooser_preview())

    def choose(self, kind, current, on_pick, *, force=None):
        if kind not in self.name_cache:
            names = [(item, self.caption(kind, item)) for item in self.catalog.playable(kind)]
            self.name_cache[kind] = sorted(names, key=lambda row: (row[1], row[0]))
        options = list(self.name_cache[kind])
        if kind == 'pilot' and self.member.unit in self.catalog.units:
            if linked := self.catalog.linked_pilot(self.member.unit):
                options = [(linked, self.caption('pilot', linked))]
        if current and current not in {item for item, _ in options}:
            options.insert(0, (current, self.caption(kind, current)))
        if kind in ('op', 'unit', 'supporter'):
            options.insert(0, (0, self.caption(kind, 0)))
        self.chooser_kind, self.chooser_current, self.chooser_apply = kind, current, on_pick
        self.chooser_force = self.selected[0] if force is None else force
        self.profile_key = None
        self.chooser_options = options
        self.chooser_title.configure(text=f'{PICKER_TITLES[kind]} · 搜索名称或编号')
        self.profile_art.configure(width=240 if kind == 'unit' else 154)
        self.chooser_profile.columnconfigure(0, weight=1 if kind == 'unit' else 0)
        self.chooser_profile.columnconfigure(1, weight=2 if kind == 'unit' else 1)
        has_roles = kind in ('unit', 'pilot')
        for widget, shown in ((self.role_results, has_roles), (self.chooser_results, not has_roles),
                              (self.chooser_effects, kind == 'op'), (self.chooser_profile, kind != 'op')):
            widget.grid() if shown else widget.grid_remove()
        self.chooser_scroll.configure(command=(self.role_results if has_roles else self.chooser_results).yview)
        self.chooser_query.set('')
        self.chooser.place(rely=1, relwidth=1, relheight=.74 if kind == 'unit' else .56, anchor='sw')
        self.chooser.lift()
        self.chooser_search.focus_set()

    def filter_choices(self, *_):
        kind = self.chooser_kind
        if not kind:
            return
        terms = self.chooser_query.get().casefold().split()
        results = self.chooser_results
        results.delete(0, 'end')
        role_results = self.role_results
        role_rows = role_results.get_children()
        if role_rows:
            role_results.delete(*role_rows)
        role_for = {'unit': self.catalog.unit_role, 'pilot': self.catalog.pilot_role}.get(kind)
        self.chooser_visible = []
        labels = []
        for item, title in self.chooser_options:
            effects = self.catalog.part_effects(item) if kind == 'op' else ()
            role = role_for(item) if role_for else 0
            prefix = ROLES.get(role, '')
            if kind == 'supporter':
                prefix = self.catalog.supporter_badges(item, self.supporter_step(self.chooser_force, item))
            searchable = ' '.join((title, str(item), prefix, *effects)).casefold()
            if not all(term in searchable for term in terms):
                continue
            self.chooser_visible.append((item, title))
            detail = effects[0].replace('\n', ' ') if effects else str(item) if item else ''
            label = f'{title}  ·  {detail}' if detail else title
            if role_for:
                role_results.insert('', 'end', iid=str(len(self.chooser_visible) - 1),
                                    image=self.role_icons.get(role) or '', text=f'{prefix}  {label}' if prefix else label)
            else:
                labels.append(f'{prefix}  {label}' if prefix else label)
        if labels:
            results.insert('end', *labels)
        if self.chooser_visible:
            index = next((i for i, (item, _) in enumerate(self.chooser_visible)
                          if item == self.chooser_current), 0)
            if role_for:
                role_results.selection_set(str(index))
                role_results.focus(str(index))
                role_results.see(str(index))
            else:
                results.selection_set(index)
                results.activate(index)
                results.see(index)
        self.draw_chooser_preview()

    def chooser_selection(self):
        selected = (self.role_results.selection() if self.chooser_kind in ('unit', 'pilot')
                    else self.chooser_results.curselection())
        if selected and (index := int(selected[0])) < len(self.chooser_visible):
            return self.chooser_visible[index]
        return None

    def supporter_step(self, force, item):
        baseline = self.default[force].supporter
        return self.catalog.supporter_step(item, baseline, self.supporter_stars.get(baseline, 0))

    def resize_profile(self, event):
        self.profile_canvas.itemconfigure(self.profile_window, width=event.width)
        for widget in self.profile_body.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(wraplength=max(50, event.width - 20))
        for widget in self.profile_details:
            widget.configure(wraplength=max(50, event.width - 36))

    def draw_profile_art(self):
        if self.chooser_kind not in ('unit', 'pilot', 'supporter'):
            return
        canvas = self.profile_art
        canvas.delete('all')
        if not (selection := self.chooser_selection()) or not selection[0]:
            self.chooser_image = None
            return
        identifier, _ = selection
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if min(width, height) < 12:
            return
        if self.chooser_kind == 'unit':
            path = self.unit_picture(identifier)
        elif self.chooser_kind == 'pilot':
            path = self.pilot_picture(identifier, request=True)
        else:
            path = self.supporter_picture(identifier)
        self.chooser_image = self.picture(path, width - 8, height - 14)
        if self.chooser_image:
            canvas.create_image(width / 2, height / 2, image=self.chooser_image)
        elif path is None and self.chooser_kind != 'unit':
            canvas.create_text(width / 2, height / 2, text='立绘加载中', fill=MUTED,
                               font=(self.font[0], 9))

    def profile_wheel(self, event):
        self.profile_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def profile_heading(self, text):
        heading = self.label(self.profile_body, text, bg=ART, color=BLUE, size=10, bold=True)
        heading.pack(fill='x', pady=(6, 2))
        heading.bind('<MouseWheel>', self.profile_wheel)

    def profile_label(self, text, *, color=TEXT, bold=False):
        widget = self.label(self.profile_body, text, bg=ART, color=color, size=9, bold=bold)
        widget.configure(justify='left', wraplength=max(80, self.profile_canvas.winfo_width() - 20))
        widget.pack(fill='x', pady=1)
        widget.bind('<MouseWheel>', self.profile_wheel)
        return widget

    def profile_numbers(self, numbers):
        grid = tk.Frame(self.profile_body, bg=SURFACE, padx=4, pady=3)
        grid.pack(fill='x', pady=(3, 4))
        cells = []
        for label, value, boosted in numbers:
            cell = self.label(grid, f'{label}  {value}', bg=SURFACE, color=BLUE if boosted else TEXT,
                              size=9, bold=True)
            cell.bind('<MouseWheel>', self.profile_wheel)
            cells.append(cell)
        cell_width = max((cell.winfo_reqwidth() + 8 for cell in cells), default=1)

        def layout(width):
            columns = min(len(cells) or 1, 4, max(1, (width - 8) // cell_width))
            grid.columnconfigure(tuple(range(4)), weight=0, uniform='')
            grid.columnconfigure(tuple(range(columns)), weight=1, uniform='stats')
            for index, cell in enumerate(cells):
                row, col = divmod(index, columns)
                cell.grid(row=row, column=col, sticky='w', padx=3, pady=1)

        layout(self.profile_canvas.winfo_width())
        grid.bind('<Configure>', lambda event: layout(event.width))

    def profile_entry(self, name, icon, details, *, locked=False):
        row = tk.Frame(self.profile_body, bg=CARD)
        row.pack(fill='x', pady=1)
        header = tk.Frame(row, bg=CARD, cursor='hand2', takefocus=True)
        header.pack(fill='x', padx=4, pady=2)
        artwork = self.label(header, '', bg=CARD)
        if icon and icon != '0':
            artwork.pack(side='left', padx=(1, 5))
            self.profile_icon_widgets[artwork] = icon
            if (image := self.skill_icon(icon)) is not None:
                artwork.configure(image=image)
                artwork.icon = image
        title = self.label(header, name, bg=CARD, color=MUTED if locked else TEXT, size=9, bold=True)
        arrow = self.label(header, '▸', bg=CARD, color=BLUE, bold=True)
        arrow.pack(side='right', padx=3)
        title.pack(side='left', fill='x', expand=True)
        title.configure(justify='left')
        title.bind('<Configure>', lambda event: title.configure(wraplength=max(50, event.width)), add='+')
        body = self.label(row, '\n\n'.join(details) or '暂无效果说明', bg=CARD, size=9)
        body.configure(justify='left', wraplength=max(75, self.profile_canvas.winfo_width() - 36))
        self.profile_details.append(body)

        def toggle(_):
            if body.winfo_manager():
                body.pack_forget()
                arrow.configure(text='▸')
            else:
                body.pack(fill='x', padx=8, pady=(2, 7))
                arrow.configure(text='▾')
                self.profile_body.update_idletasks()
                self.profile_canvas.configure(scrollregion=self.profile_canvas.bbox('all'))
                if body.winfo_y() + row.winfo_y() + body.winfo_height() > (
                    self.profile_canvas.canvasy(0) + self.profile_canvas.winfo_height()
                ):
                    total = self.profile_body.winfo_height()
                    self.profile_canvas.yview_moveto(max(0, row.winfo_y() - 2) / max(1, total))

        for widget in (header, artwork, title, arrow):
            widget.bind('<Button-1>', toggle)
            widget.bind('<MouseWheel>', self.profile_wheel)
        header.bind('<Return>', toggle)
        header.bind('<space>', toggle)
        body.bind('<MouseWheel>', self.profile_wheel)

    def draw_profile(self, selection):
        kind = self.chooser_kind
        if not selection:
            key = None
        else:
            item, _ = selection
            level = None
            if kind == 'pilot':
                cap = self.catalog.pilot_max_level(item) if item in self.catalog.pilots else 100
                level = max(1, min(self.member.pilot_level, cap))
            elif kind == 'supporter':
                cap = self.catalog.supporter_max_level(item) if item in self.catalog.supporters else 100
                level = max(1, min(self.team[self.chooser_force].supporter_level, cap))
            key = kind, item, level
        self.draw_profile_art()
        if key == self.profile_key:
            return
        self.profile_key = key
        for widget in self.profile_body.winfo_children():
            widget.destroy()
        self.profile_icon_widgets.clear()
        self.profile_details.clear()
        self.profile_canvas.yview_moveto(0)
        if key is None:
            return
        kind, item, level = key
        _, title = selection
        self.profile_label(title, bold=True)
        if not item:
            return
        if kind == 'unit':
            self.draw_unit_info(item)
        elif kind == 'pilot':
            role = self.catalog.pilot_role(item)
            self.profile_label('  ·  '.join(filter(None, (ROLES.get(role), f'Lv{level}'))),
                               color=ROLE_COLORS.get(role, MUTED))
            specialized = item in self.specialized_pilots
            numbers = self.catalog.pilot_numbers(item, level, specialized=specialized)
            if numbers:
                self.profile_numbers((label, f'{value:,}', value > base) for label, value, base in numbers)
            skills, abilities = self.catalog.pilot_entries(item, specialized=specialized)
            for label, entries in (('角色技能', skills), ('角色能力', abilities)):
                self.profile_heading(label)
                if not entries:
                    self.profile_label('无', color=MUTED)
                for entry in entries:
                    locked = level < entry.level
                    caption = f'{entry.name}  ·  Lv{entry.level}' if locked else entry.name
                    meta = '  ·  '.join((f'Lv{entry.level} 解锁', *entry.costs))
                    self.profile_entry(caption, entry.icon, (meta, *entry.effects), locked=locked)
        elif item in self.catalog.supporters:
            step = self.supporter_step(self.chooser_force, item)
            info = self.catalog.supporter_info(item, level, step)
            self.profile_label(f'Lv{level}  ·  ★{step}', color=BLUE)
            self.profile_numbers((('机体 HP', f'+{info.hp:,}', True), ('机体攻击', f'+{info.attack:,}', True)))
            if info.leaders:
                self.profile_heading('队长增益')
                for condition, description in info.leaders:
                    self.profile_label(condition or '部队内单位', color=BLUE, bold=True)
                    if description:
                        self.profile_label(description)
                if info.use_max and len(info.leaders) > 1:
                    self.profile_label('同时满足多档条件时，取较高加成。', color=MUTED)
            if info.active:
                self.profile_heading('支援技能')
                self.profile_entry(info.active.name, info.active.icon, info.active.effects)

    def draw_unit_info(self, item):
        self.profile_label(str(item), color=MUTED)
        if item not in self.catalog.units:
            return
        unit = self.catalog.units[item]
        role = self.catalog.unit_role(item)
        meta = (unit[10], UNIT_RARITIES.get(unit[14]), ROLES.get(role))
        if any(meta):
            self.profile_label('  ·  '.join(filter(None, meta)), color=ROLE_COLORS.get(role, MUTED))
        series = sorted(self.catalog.series_sets.get(unit[22], ()), key=lambda row: row[1])
        if series:
            self.profile_label(' / '.join(self.caption('series', key) for key, _ in series), color=MUTED)
        if numbers := self.catalog.unit_numbers(item):
            self.profile_heading('Lv1 基础数值')
            self.profile_numbers((label, f'{value:,}', False) for label, value in numbers)
        terrain = self.catalog.terrain.get(unit[18])
        if terrain:
            capability = '  '.join(f'{label}{CAPABILITY_GRADE.get(value, "—")}'
                                   for label, value in zip(TERRAIN_SHORT, terrain[1:]))
            self.profile_label(f'地形适应性  {capability}')
        if tags := self.catalog.unit_tags.get(item):
            self.profile_label('标签  ' + ' / '.join(self.caption('lineage', key) for (key,) in tags), color=MUTED)
        weapons, abilities, skills = self.catalog.unit_entries(item)
        for heading, entries in (('武装 · Lv1', weapons), ('机体能力', abilities), ('机体技能', skills)):
            if entries:
                self.profile_heading(heading)
                for entry in entries:
                    details = ('  ·  '.join(entry.costs),) if entry.costs else ()
                    self.profile_entry(entry.name, entry.icon, (*details, *entry.effects))
        if description := formation_names().get('m_unit', {}).get(str(unit[7])):
            self.profile_heading('机体简介')
            self.profile_label(description, color=MUTED)

    def draw_chooser_preview(self):
        if not self.chooser_kind:
            return
        selection = self.chooser_selection()
        self.chooser_confirm.state(['!disabled' if selection else 'disabled'])
        if self.chooser_kind == 'op':
            self.chooser_effects.configure(state='normal')
            self.chooser_effects.delete('1.0', 'end')
            if selection:
                item, title = selection
                effects = self.catalog.part_effects(item)
                self.chooser_effects.insert('end', '\n\n'.join((title, *effects)))
            self.chooser_effects.configure(state='disabled')
            return
        self.draw_profile(selection)

    def apply_choice(self, _=None):
        if self.chooser_kind and (selection := self.chooser_selection()):
            item, _ = selection
            apply = self.chooser_apply
            self.close_chooser()
            apply(item)

    def close_chooser(self):
        if not self.chooser_kind:
            return False
        self.chooser_kind = None
        self.profile_key = None
        self.chooser.place_forget()
        self.focus_set()
        return True

    def close(self):
        if self.portrait_poll is not None:
            self.after_cancel(self.portrait_poll)
        self.portrait_pool.shutdown(wait=False, cancel_futures=True)

    def reset(self):
        self.close_chooser()
        self.team = deepcopy(self.default)
        self.path = None
        self.edited()

    def save(self):
        self.close_chooser()
        preview = capture_preview(self)
        self.team_saves.show_save('保存队伍', self.save_to, self.path.stem if self.path else '我的队伍', preview)

    def save_to(self, path, preview):
        save_team(path, self.team, preview=preview)
        self.path = path
        self.refresh()
        self.battle.status.set(f'队伍已保存 · {path.stem}')

    def open(self):
        self.close_chooser()
        self.team_saves.show_load('读取队伍', self.load_from)

    def load_from(self, path):
        with path.open('rb') as source:
            team = load_team(source)
        self.team, self.path = team, path
        self.edited()
        self.battle.status.set(f'队伍已读取 · {path.stem}')

    def deployment(self):
        self.edited()
        if self.errors:
            force, slot, _ = self.errors[0]
            if force and slot:
                self.select(force - 1, slot - 1)
            self.battle.status.set('请修正编队中的出击限制')
            return None
        return deepcopy(self.team)
