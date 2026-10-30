"""网易云歌单音频保存 GUI（需要本人账号的 MUSIC_U Cookie）。"""
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from music_core import MusicClient, download, safe_name


class QueueHandler(logging.Handler):
    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        self.messages.put(self.format(record))


class App:
    def __init__(self, root):
        self.root = root
        root.title('网易云音乐歌单音频保存')
        root.geometry('760x570')
        self.messages = queue.Queue()
        handler = QueueHandler(self.messages)
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%H:%M:%S'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        self.stop_event = threading.Event()
        self.worker = None
        self.folder = ''

        top = ttk.Frame(root, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text='歌单链接或 ID:').pack(anchor=tk.W)
        self.url = ttk.Entry(top)
        self.url.pack(fill=tk.X, pady=(3, 8))
        ttk.Label(top, text='MUSIC_U 的值或 MUSIC_U=值（仅在内存中使用）:').pack(anchor=tk.W)
        self.cookie = ttk.Entry(top, show='*')
        self.cookie.pack(fill=tk.X, pady=(3, 8))
        ttk.Label(top, text='保存位置:').pack(anchor=tk.W)
        row = ttk.Frame(top)
        row.pack(fill=tk.X, pady=(3, 8))
        self.directory = tk.StringVar(value=os.path.join(os.getcwd(), 'songs_dir'))
        ttk.Entry(row, textvariable=self.directory).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text='浏览…', command=self.choose_folder).pack(side=tk.LEFT, padx=5)
        ttk.Label(top, text='按登录账号的可播放权限获取音频；无法播放的歌曲会记录失败，不承诺全部可下载。').pack(anchor=tk.W)

        buttons = ttk.Frame(root, padding=(10, 0))
        buttons.pack(fill=tk.X)
        self.start = ttk.Button(buttons, text='下载整个歌单', command=self.begin)
        self.start.pack(side=tk.LEFT)
        self.cancel = ttk.Button(buttons, text='取消', command=self.stop, state=tk.DISABLED)
        self.cancel.pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text='打开保存位置', command=self.open_folder).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(root, mode='determinate')
        self.progress.pack(fill=tk.X, padx=10, pady=(10, 2))
        self.status = tk.StringVar(value='就绪')
        ttk.Label(root, textvariable=self.status).pack(fill=tk.X, padx=10)
        self.log = scrolledtext.ScrolledText(root, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.directory.get())
        if path:
            self.directory.set(path)

    def poll(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                self.log.configure(state=tk.NORMAL)
                self.log.insert(tk.END, msg + '\n')
                self.log.see(tk.END)
                self.log.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def begin(self):
        url, cookie = self.url.get().strip(), self.cookie.get().strip()
        if not url or not cookie:
            messagebox.showwarning('缺少信息', '请填写歌单链接和登录 Cookie。')
            return
        try:
            client = MusicClient(cookie)
            self.folder = os.path.abspath(self.directory.get().strip() or 'songs_dir')
            os.makedirs(self.folder, exist_ok=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法开始', str(exc))
            return
        self.stop_event.clear()
        self.start.configure(state=tk.DISABLED)
        self.cancel.configure(state=tk.NORMAL)
        self.progress['value'] = 0
        self.worker = threading.Thread(target=self.run, args=(client, url), daemon=True)
        self.worker.start()

    def run(self, client, url):
        ok = failed = 0
        start = time.monotonic()
        try:
            title, songs = client.playlist(url)
            folder = os.path.join(self.folder, safe_name(title))
            os.makedirs(folder, exist_ok=True)
            logging.info('歌单「%s」共 %d 首', title, len(songs))
            for index, song in enumerate(songs, 1):
                if self.stop_event.is_set():
                    break
                try:
                    download(client, song, folder, self.stop_event)
                    ok += 1
                except InterruptedError:
                    break
                except Exception as exc:
                    failed += 1
                    logging.warning('%s - %s (%s) 失败: %s', song[0], song[1], song[2], exc)
                self.root.after(0, self.update_progress, index, len(songs))
            summary = '完成：成功 %d 首，失败 %d 首%s；耗时 %.1f 秒' % (
                ok, failed, '（已取消）' if self.stop_event.is_set() else '', time.monotonic() - start)
            logging.info(summary)
            self.root.after(0, self.finish, summary, False)
        except Exception as exc:
            logging.error('任务失败: %s', exc)
            self.root.after(0, self.finish, str(exc), True)
        finally:
            client.session.close()

    def update_progress(self, index, total):
        self.progress['value'] = 100 * index / total
        self.status.set('已处理 %d / %d 首' % (index, total))

    def finish(self, summary, error):
        self.start.configure(state=tk.NORMAL)
        self.cancel.configure(state=tk.DISABLED)
        self.status.set(summary)
        if error:
            messagebox.showerror('下载失败', summary)
        else:
            messagebox.showinfo('任务结束', summary)

    def stop(self):
        self.stop_event.set()
        self.cancel.configure(state=tk.DISABLED)
        self.status.set('正在取消…')

    def open_folder(self):
        path = self.folder or self.directory.get()
        if os.path.isdir(path):
            if os.name == 'nt':
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(['xdg-open', path])

    def close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('确认退出', '下载尚未完成，确定退出吗？'):
                return
            self.stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
