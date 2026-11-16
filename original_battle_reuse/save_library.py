"""In-window browser and naming form for local saves."""

from datetime import datetime
from io import BytesIO
import os
from pathlib import PureWindowsPath
import re
import tkinter as tk
from tkinter import ttk
from zipfile import BadZipFile

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    Image = ImageGrab = ImageTk = None


def capture_preview(widget):
    if ImageGrab is None:
        return None
    widget.update_idletasks()
    if not widget.winfo_viewable():
        return None
    try:
        try:
            image = ImageGrab.grab(window=widget.winfo_id())
        except TypeError:
            # Pillow before 11.2 has no window argument, but its Windows capture accepts an HWND.
            _, size, pixels = Image.core.grabscreen_win32(False, -1, widget.winfo_id())
            image = Image.frombytes('RGB', size, pixels, 'raw', 'BGR', (size[0] * 3 + 3) & -4, -1)
        image.thumbnail((320, 180), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.convert('RGB').save(output, 'JPEG', quality=78, optimize=True)
        return output.getvalue()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class SaveLibrary(tk.Frame):
    def __init__(self, parent, directory, suffix, read_entry, *, stage_column=False):
        super().__init__(parent, bg='#101c2c', padx=24, pady=18)
        self.directory = directory
        self.suffix = suffix
        self.read_entry = read_entry
        self.on_save = self.on_load = None
        self.preview = None
        self.overwrite = None
        self.entries = {}
        self.images = []
        self.name = tk.StringVar(master=self)
        self.message = tk.StringVar(master=self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=self['bg'])
        header.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.title = self.label(header, '', title=True)
        self.title.pack(side='left')
        ttk.Button(header, text='返回', command=self.close).pack(side='right')

        self.form = tk.Frame(self, bg='#192c42', padx=12, pady=8)
        self.form.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        self.draft = self.label(self.form, '', bg=self.form['bg'])
        self.draft.pack(side='left', padx=(0, 12))
        self.label(self.form, '存档名称', bg=self.form['bg']).pack(side='left', padx=(0, 6))
        self.name_entry = ttk.Entry(self.form, textvariable=self.name, width=28)
        self.name_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.name_entry.bind('<Return>', self.submit)
        self.confirm = ttk.Button(self.form, text='保存', command=self.submit)
        self.confirm.pack(side='left')
        self.name.trace_add('write', self.name_changed)

        rows = tk.Frame(self, bg=self['bg'])
        rows.grid(row=2, column=0, sticky='nsew')
        rows.columnconfigure(0, weight=1)
        rows.rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure('Save.Treeview', rowheight=94, background='#192c42', fieldbackground='#192c42',
                        foreground='#eff7ff')
        style.map('Save.Treeview', background=[('selected', '#285984')])
        columns = ('stage', 'date') if stage_column else ('date',)
        self.list = ttk.Treeview(rows, columns=columns, show='tree headings', selectmode='browse',
                                 style='Save.Treeview')
        self.list.heading('#0', text='名称', anchor='w')
        self.list.column('#0', width=370, minwidth=190)
        if stage_column:
            self.list.heading('stage', text='关卡 / 楼层', anchor='w')
            self.list.column('stage', width=330, minwidth=190)
        self.list.heading('date', text='保存时间', anchor='w')
        self.list.column('date', width=140, minwidth=135, stretch=False)
        self.list.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(rows, orient='vertical', command=self.list.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind('<ButtonRelease-1>', self.activate)
        self.list.bind('<Return>', self.activate)
        self.blank = tk.PhotoImage(master=self, width=152, height=86)
        self.blank.put('#101c2c', to=(0, 0, 152, 86))

        self.label(self, '', textvariable=self.message).grid(row=3, column=0, sticky='ew', pady=(7, 0))
        self.label(self, str(directory), fg='#a8bfd6').grid(row=4, column=0, sticky='ew', pady=(4, 0))

    def label(self, parent, text, *, title=False, **options):
        return tk.Label(parent, text=text, **({'bg': self['bg'], 'fg': '#eff7ff', 'anchor': 'w',
                                               'font': ('Microsoft YaHei UI', 16 if title else 10,
                                                        'bold' if title else 'normal')} | options))

    def photo(self, preview, size):
        if ImageTk is not None and preview:
            try:
                with Image.open(BytesIO(preview)) as image:
                    if max(image.size) <= 640:
                        image.thumbnail(size, Image.Resampling.LANCZOS)
                        return ImageTk.PhotoImage(image, master=self)
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        return None

    def show_save(self, title, callback, name, preview):
        self.on_save, self.on_load = callback, None
        self.preview = preview
        self.name.set(name)
        self.draft_image = self.photo(preview, (192, 108))
        self.draft.configure(image=self.draft_image or '', text='' if self.draft_image else '无预览图')
        self.form.grid()
        self.show(title)
        self.name_entry.focus_set()
        self.name_entry.select_range(0, 'end')

    def show_load(self, title, callback):
        self.on_save, self.on_load = None, callback
        self.form.grid_remove()
        self.show(title)
        self.list.focus_set()

    def show(self, title):
        self.title.configure(text=title)
        self.message.set('')
        self.list.delete(*self.list.get_children())
        self.entries.clear()
        self.images.clear()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            rows = [(path.stat().st_mtime, path) for path in self.directory.iterdir()
                    if path.suffix.lower() == self.suffix and not path.is_symlink() and path.is_file()]
        except OSError as exc:
            rows = []
            self.message.set(f'无法读取存档目录 · {exc}')
        if not rows and not self.message.get():
            self.message.set('暂无存档')
        for modified, path in sorted(rows, reverse=True):
            date = datetime.fromtimestamp(modified).strftime('%Y-%m-%d %H:%M')
            try:
                preview, stage = self.read_entry(path)
            except (OSError, ValueError, KeyError, BadZipFile, RuntimeError):
                preview, stage = None, '无法读取存档信息'
            thumbnail = self.photo(preview, (152, 86))
            if thumbnail:
                self.images.append(thumbnail)
            values = (stage, date) if len(self.list['columns']) == 2 else (date,)
            item = self.list.insert('', 'end', text=path.stem, image=thumbnail or self.blank, values=values)
            self.entries[item] = path
        self.place(relwidth=1, relheight=1)
        self.lift()

    def name_changed(self, *_):
        self.overwrite = None
        self.confirm.configure(text='保存')
        self.message.set('')

    def activate(self, event):
        if event.type == tk.EventType.ButtonRelease:
            if self.list.identify_region(event.x, event.y) not in ('tree', 'cell'):
                return
            item = self.list.identify_row(event.y)
        else:
            item = self.list.focus()
        path = self.entries.get(item)
        if path is None:
            return
        if self.on_save:
            self.name.set(path.stem)
            self.name_entry.focus_set()
        elif self.on_load:
            try:
                self.on_load(path)
            except (OSError, ValueError) as exc:
                self.message.set(f'读取失败 · {exc}')
            else:
                self.close()
        return 'break'

    def submit(self, _event=None):
        name = self.name.get().strip()
        if name.lower().endswith(self.suffix):
            name = name[:-len(self.suffix)]
        reserved = getattr(os.path, 'isreserved', None)
        if (not name or name.endswith(('.', ' ')) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or (reserved(name) if reserved else PureWindowsPath(name).is_reserved())):
            self.message.set('请输入有效的存档名称')
            return 'break'
        path = self.directory / (name + self.suffix)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.is_dir():
                raise ValueError('该名称已被占用')
            if path.exists() and self.overwrite != path:
                self.overwrite = path
                self.confirm.configure(text='覆盖保存')
                self.message.set(f'「{name}」已存在，再按一次覆盖')
                return 'break'
            self.on_save(path, self.preview)
        except (OSError, ValueError) as exc:
            self.message.set(f'保存失败 · {exc}')
        else:
            self.close()
        return 'break'

    def close(self):
        if not self.winfo_manager():
            return False
        self.place_forget()
        self.on_save = self.on_load = None
        self.preview = None
        self.draft.configure(image='')
        self.draft_image = None
        self.list.delete(*self.list.get_children())
        self.entries.clear()
        self.images.clear()
        self.master.focus_set()
        return True
