import hashlib
import os
import queue
import threading
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

CATEGORIES = {
    '이미지': 'jpg jpeg png gif bmp webp svg heic tiff ico',
    '동영상': 'mp4 mkv avi mov wmv webm m4v',
    '문서': 'pdf doc docx hwp hwpx txt xls xlsx ppt pptx csv md',
    '음악': 'mp3 wav flac aac ogg m4a',
    '압축파일': 'zip rar 7z tar gz bz2',
    '프로그램': 'exe msi bat py',
}
EXT = {e: c for c, es in CATEGORIES.items() for e in es.split()}
QUARANTINE = '_중복파일_격리'


def size_text(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:,.1f} {unit}'
        n /= 1024


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_path(path, root):
    return path.resolve().is_relative_to(root) and not any(
        p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
        for p in (path, *path.parents) if p != root and p.is_relative_to(root))


def scan(root):
    files, errors = [], []
    for folder, dirs, names in os.walk(root, onerror=lambda e: errors.append(str(e))):
        base = Path(folder)
        dirs[:] = sorted(d for d in dirs if d != QUARANTINE and safe_path(base / d, root))
        for name in sorted(names):
            p = base / name
            try:
                if safe_path(p, root) and p.is_file():
                    files.append((p, p.stat().st_size))
            except OSError as e:
                errors.append(str(e))
    by_size = defaultdict(list)
    for p, n in files:
        by_size[n].append(p)
    groups = []
    for paths in by_size.values():
        if len(paths) < 2:
            continue
        by_hash = defaultdict(list)
        for p in paths:
            try:
                by_hash[digest(p)].append(p)
            except OSError as e:
                errors.append(str(e))
        groups.extend(sorted(g) for g in by_hash.values() if len(g) > 1)
    return files, groups, errors


def unique_path(p):
    candidate, i = p, 1
    while os.path.lexists(candidate):
        candidate = p.with_name(f'{p.stem} ({i}){p.suffix}')
        i += 1
    return candidate


def move_file(src, dst, root):
    if not safe_path(src, root) or not safe_path(dst, root):
        raise OSError('안전하지 않은 경로')
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Hard link creation fails if destination exists; never overwrites.
    os.link(src, dst)
    try:
        src.unlink()
    except OSError:
        dst.unlink()
        raise


class App:
    def __init__(self, win):
        self.win, self.root = win, None
        self.files, self.groups, self.history = [], [], []
        self.plan, self.duplicates = {}, {}
        self.busy = False
        self.results = queue.Queue()
        win.title('Smart File Manager | 스마트 파일 관리')
        win.geometry('1050x680')
        win.minsize(760, 480)
        self.style = ttk.Style(win)
        self.style.theme_use('clam')
        self.theme = tk.StringVar(value='라이트 모드')
        self.apply_theme()
        outer = ttk.Frame(win, padding=22)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='Smart File Manager', font=('Malgun Gothic', 24, 'bold')).pack(side='left')
        theme_box = ttk.Combobox(header, textvariable=self.theme, values=('라이트 모드', '다크 모드'), state='readonly', width=13)
        theme_box.pack(side='right')
        theme_box.bind('<<ComboboxSelected>>', lambda e: self.apply_theme())
        ttk.Label(outer, text='파일 자동 정리 · 내용 기반 중복 탐지 · 안전한 복구').pack(anchor='w', pady=(0, 16))
        bar = ttk.Frame(outer)
        bar.pack(fill='x')
        self.path = tk.StringVar(value='관리할 폴더를 선택하세요')
        ttk.Entry(bar, textvariable=self.path, state='readonly').pack(side='left', fill='x', expand=True)
        self.buttons = []
        self.button(bar, '폴더 선택', self.choose).pack(side='left', padx=6)
        self.button(bar, '다시 검사', self.start_scan).pack(side='left')
        self.stats = ttk.Label(outer, text='전체 파일  0개     |     전체 용량  0 B     |     중복 후보  0개', font=('Malgun Gothic', 13, 'bold'))
        self.stats.pack(anchor='w', pady=18)
        self.status = tk.StringVar(value='준비 완료 · 영구 삭제 기능 없음')
        ttk.Label(outer, textvariable=self.status).pack(side='bottom', anchor='w', pady=(10, 0))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        sort = ttk.Frame(tabs, padding=12)
        dup = ttk.Frame(tabs, padding=12)
        log = ttk.Frame(tabs, padding=12)
        for frame, title in ((sort, '파일 정리'), (dup, '중복 파일 탐지'), (log, '작업 기록')):
            tabs.add(frame, text=title)
        ttk.Label(sort, text='선택 폴더 바로 아래의 파일만 분류합니다. 아래는 이동 예정 목록입니다.').pack(anchor='w', pady=(0, 8))
        self.button(sort, '파일 정리 실행', self.organize, True).pack(side='bottom', anchor='e', pady=(10, 0))
        self.sort_tree = self.tree(sort, ('파일명', '분류', '용량'), (360, 130, 100))
        ttk.Label(dup, text='하위 폴더까지 검사합니다. 각 그룹의 첫 파일은 유지하며 나머지만 격리할 수 있습니다.').pack(anchor='w', pady=(0, 8))
        row = ttk.Frame(dup)
        row.pack(side='bottom', fill='x', pady=(10, 0))
        self.button(row, '중복 후보 전체 선택', self.select_duplicates).pack(side='left')
        self.button(row, '선택 파일 격리', self.quarantine, 'Warning').pack(side='right')
        self.dup_tree = self.tree(dup, ('그룹', '파일 경로', '용량', '상태'), (65, 420, 90, 110))
        self.button(log, '마지막 작업 되돌리기', self.undo, 'Restore').pack(side='bottom', anchor='e', pady=(10, 0))
        self.log_tree = self.tree(log, ('작업', '내용'), (140, 540))
        win.protocol('WM_DELETE_WINDOW', self.close)
        win.after(100, self.poll)

    def apply_theme(self):
        dark = self.theme.get() == '다크 모드'
        bg, panel, fg, muted, edge = ('#18212f', '#222e3e', '#edf2f7', '#aebbd0', '#43536a') if dark else ('#eff4fa', '#ffffff', '#203047', '#53657c', '#c8d3e1')
        st = self.style
        self.win.configure(bg=bg)
        st.configure('.', font=('Malgun Gothic', 10), background=bg, foreground=fg, bordercolor=edge, lightcolor=edge, darkcolor=edge, troughcolor=bg, arrowcolor=fg)
        st.configure('TFrame', background=bg)
        st.configure('TLabel', background=bg, foreground=fg)
        st.configure('TButton', background=panel, foreground=fg, padding=(12, 8))
        st.map('TButton', background=[('disabled', bg), ('pressed', edge), ('active', edge)], foreground=[('disabled', muted), ('!disabled', fg)])
        for name, normal, hover in [('Accent', '#2563eb', '#1d4ed8'), ('Warning', '#a34b12', '#873b0c'), ('Restore', '#087c64', '#06634f')]:
            key = name + '.TButton'
            st.configure(key, background=normal, foreground='#ffffff', padding=(16, 9), bordercolor=normal)
            st.map(key, background=[('disabled', '#52647b'), ('pressed', hover), ('active', hover), ('!disabled', normal)], foreground=[('disabled', '#ffffff'), ('!disabled', '#ffffff')])
        for key in ('TEntry', 'TCombobox'):
            st.configure(key, fieldbackground=panel, foreground=fg, padding=5, insertcolor=fg)
            st.map(key, fieldbackground=[('readonly', panel)], foreground=[('readonly', fg)], selectbackground=[('readonly', panel)], selectforeground=[('readonly', fg)])
        self.win.option_add('*TCombobox*Listbox.background', panel)
        self.win.option_add('*TCombobox*Listbox.foreground', fg)
        st.configure('TNotebook', background=bg, bordercolor=edge)
        st.configure('TNotebook.Tab', background=bg, foreground=muted, padding=(15, 9))
        st.map('TNotebook.Tab', background=[('selected', panel), ('active', edge)], foreground=[('selected', fg), ('active', fg)])
        st.configure('Treeview', rowheight=32, background=panel, fieldbackground=panel, foreground=fg)
        st.map('Treeview', background=[('selected', '#2563eb')], foreground=[('selected', '#ffffff')])
        st.configure('Treeview.Heading', background=bg, foreground=fg, padding=6)
        st.map('Treeview.Heading', background=[('active', edge)])
        st.configure('Vertical.TScrollbar', background=edge, troughcolor=bg, arrowcolor=fg)

    def button(self, parent, label, command, accent=False):
        b = ttk.Button(parent, text=label, command=command, style=(('Accent' if accent is True else accent) + '.TButton') if accent else 'TButton')
        self.buttons.append(b)
        return b

    def tree(self, parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended')
        for col, width in zip(columns, widths):
            tree.heading(col, text=col)
            tree.column(col, width=width, minwidth=55)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def record(self, action, detail):
        self.log_tree.insert('', 0, values=(action, detail))

    def choose(self):
        folder = filedialog.askdirectory()
        if folder:
            self.root = Path(folder).resolve()
            self.path.set(str(self.root))
            self.start_scan()

    def start_scan(self):
        if self.busy or not self.root:
            return
        self.busy = True
        for b in self.buttons:
            b.state(['disabled'])
        self.status.set('파일 검사 중... 큰 파일이 많으면 시간이 걸립니다.')
        root = self.root
        def worker():
            try:
                self.results.put((scan(root), None))
            except Exception as e:
                self.results.put((None, str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for b in self.buttons:
                b.state(['!disabled'])
            if error:
                self.files, self.groups = [], []
                self.refresh()
                self.status.set('검사 실패')
                messagebox.showerror('검사 오류', error)
            else:
                self.files, self.groups, errors = result
                self.refresh()
                self.status.set(f'검사 완료 · 접근 오류 {len(errors)}건')
                for e in errors[:20]:
                    self.record('검사 오류', e)
        self.win.after(100, self.poll)

    def refresh(self):
        self.plan, self.duplicates = {}, {}
        for tree in (self.sort_tree, self.dup_tree):
            tree.delete(*tree.get_children())
        for p, n in self.files:
            if p.parent == self.root:
                category = EXT.get(p.suffix.lower().lstrip('.'), '기타')
                iid = self.sort_tree.insert('', 'end', values=(p.name, category, size_text(n)))
                self.plan[iid] = (p, self.root / category / p.name)
        sizes = dict(self.files)
        for i, group in enumerate(self.groups, 1):
            for j, p in enumerate(group):
                iid = self.dup_tree.insert('', 'end', values=(i, str(p.relative_to(self.root)), size_text(sizes[p]), '유지' if j == 0 else '격리 가능'))
                if j:
                    self.duplicates[iid] = (p, group[0])
        self.stats.config(text=f'전체 파일  {len(self.files):,}개     |     전체 용량  {size_text(sum(n for _, n in self.files))}     |     중복 후보  {len(self.duplicates):,}개')

    def select_duplicates(self):
        self.dup_tree.selection_set(list(self.duplicates))

    def organize(self):
        self.apply(list(self.plan.values()), '파일 정리')

    def quarantine(self):
        pairs = []
        for iid in self.dup_tree.selection():
            if iid in self.duplicates:
                p, keep = self.duplicates[iid]
                pairs.append((p, self.root / QUARANTINE / p.relative_to(self.root), keep))
        self.apply(pairs, '중복 격리')

    def apply(self, pairs, action):
        if not pairs:
            messagebox.showinfo('안내', '처리할 파일이 없습니다.')
            return
        if not messagebox.askyesno('작업 확인', f'{len(pairs)}개 파일을 이동할까요?\n파일을 영구 삭제하지 않습니다.'):
            return
        done, errors = [], 0
        for pair in pairs:
            src, target = pair[:2]
            try:
                if len(pair) == 3:
                    keep = pair[2]
                    if not safe_path(keep, self.root) or digest(src) != digest(keep):
                        raise OSError('중복 내용이 변경되었습니다.')
                dst = unique_path(target)
                move_file(src, dst, self.root)
                done.append((src, dst))
                self.record(action, f'{src.name} → {dst.relative_to(self.root)}')
            except OSError as e:
                errors += 1
                self.record('이동 실패', f'{src.name}: {e}')
        if done:
            self.history.append((self.root, done))
        messagebox.showinfo('작업 결과', f'성공 {len(done)}개 / 실패 {errors}개')
        self.start_scan()

    def undo(self):
        if not self.history:
            messagebox.showinfo('안내', '현재 실행 중에 되돌릴 작업이 없습니다.')
            return
        root, moves = self.history[-1]
        if not messagebox.askyesno('복구 확인', f'{len(moves)}개 파일을 원래 위치로 복구할까요?'):
            return
        failed = []
        for src, dst in reversed(moves):
            try:
                move_file(dst, src, root)
                self.record('복구', str(src))
            except OSError as e:
                failed.append((src, dst))
                self.record('복구 실패', f'{src}: {e}')
        self.history.pop()
        if failed:
            self.history.append((root, list(reversed(failed))))
        messagebox.showinfo('복구 결과', f'성공 {len(moves)-len(failed)}개 / 실패 {len(failed)}개')
        self.start_scan()

    def close(self):
        if self.history and not messagebox.askyesno('종료', '종료하면 작업 기록과 자동 복구 목록이 사라집니다. 종료할까요?'):
            return
        self.win.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    App(window)
    window.mainloop()
