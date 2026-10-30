"""使用用户自己的登录 Cookie 获取可播放音频并保存；不调用客户端下载接口。"""
import logging
import os
import re
import unicodedata
from urllib.parse import urlparse

import requests

BASE = 'https://music.163.com'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Referer': BASE + '/',
}


class MusicClient:
    def __init__(self, cookie):
        # 只接受网易云登录凭据，不把用户输入的 Cookie 转发给第三方域名。
        cookie = cookie.strip()
        match = re.search(r'(?:^|;\s*)MUSIC_U=([^;\s]+)', cookie)
        # 也允许直接粘贴浏览器 Cookies 表里 MUSIC_U 的 Value。
        value = match.group(1) if match else cookie if cookie and not re.search(r'[=;\s]', cookie) else None
        if not value:
            raise ValueError('请输入 MUSIC_U 的值，或包含 MUSIC_U=值 的 Cookie 字符串。')
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.cookies.set('MUSIC_U', value, domain='music.163.com', path='/')
        csrf = re.search(r'(?:^|;\s*)__csrf=([^;\s]+)', cookie)
        if csrf:
            self.session.cookies.set('__csrf', csrf.group(1), domain='music.163.com', path='/')

    def _json(self, path, params):
        response = self.session.get(BASE + path, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get('code') not in (200, None):
            raise ValueError('接口返回错误码 %s；请检查 Cookie 是否有效及歌单访问权限。' % data.get('code'))
        return data

    def playlist(self, url):
        match = re.search(r'(?:[?&#]id=|^)(\d+)(?:\D|$)', url.strip())
        if not match:
            raise ValueError('请输入包含 id=数字 的网易云音乐歌单链接或歌单 ID。')
        pid = match.group(1)
        data = self._json('/api/v6/playlist/detail', {'id': pid, 'n': 100000, 's': 0})
        playlist = data.get('playlist') or data.get('result')
        if not playlist:
            raise ValueError('歌单不存在、无权访问或 Cookie 已失效。')
        tracks = playlist.get('tracks') or []
        ids = [str(t['id']) for t in playlist.get('trackIds', []) if t.get('id')]
        if not ids:
            ids = [str(t['id']) for t in tracks if t.get('id')]
        if not ids:
            raise ValueError('歌单没有歌曲。')
        by_id = {str(t['id']): t for t in tracks}
        # 部分歌单的 tracks 只返回前 1000 首，按 trackIds 补足剩余曲目。
        missing = [sid for sid in ids if sid not in by_id]
        for start in range(0, len(missing), 200):
            batch = missing[start:start + 200]
            details = self._json('/api/song/detail/', {'ids': '[' + ','.join(batch) + ']'})
            by_id.update({str(t['id']): t for t in details.get('songs', [])})
        songs = []
        for sid in ids:
            track = by_id.get(sid, {})
            artists = track.get('ar') or track.get('artists') or []
            songs.append((track.get('name') or sid,
                          ' / '.join(a.get('name', '') for a in artists) or '未知歌手', sid))
        return playlist.get('name') or 'playlist_' + pid, songs

    def audio(self, sid):
        # 播放接口而非客户端的下载接口；VIP 资源仍由服务端按该 Cookie 的权限判断。
        for bitrate in (999000, 320000, 128000):
            data = self._json('/api/song/enhance/player/url',
                              {'ids': '[%s]' % sid, 'br': bitrate})
            items = data.get('data') or []
            if items and items[0] and items[0].get('url'):
                return items[0]
        return None


def safe_name(name):
    name = unicodedata.normalize('NFKC', str(name))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    return name[:100] or '未知'


def download(client, song, folder, stop=None):
    name, artist, sid = song
    info = client.audio(sid)
    if not info:
        raise ValueError('该账号无可播放音频（会员权限、版权、地区或 Cookie 失效）')
    url = info['url']
    if urlparse(url).scheme not in ('https', 'http'):
        raise ValueError('不支持的音频链接协议')
    ext = (info.get('type') or 'mp3').lower()
    if ext not in ('mp3', 'flac', 'm4a', 'aac', 'ogg', 'wav'):
        raise ValueError('不支持的音频格式: %s' % ext)
    filename = '%s-%s-%s.%s' % (safe_name(name), safe_name(artist), sid, ext)
    filepath = os.path.join(folder, filename)
    temp = filepath + '.part'
    try:
        # CDN 签名链接可能是 HTTP；使用独立请求，绝不向 CDN 发送 MUSIC_U。
        with requests.get(url, headers=HEADERS, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/' in content_type or 'json' in content_type:
                raise ValueError('返回了网页/错误信息，而非音频')
            with open(temp, 'wb') as output:
                head = b''
                for chunk in response.iter_content(chunk_size=65536):
                    if stop is not None and stop.is_set():
                        raise InterruptedError('已取消')
                    if chunk:
                        if len(head) < 16:
                            head = (head + chunk)[:16]
                        output.write(chunk)
        # 防止过期链接返回 HTML、空文件或受保护的加密缓存文件被误认为歌曲。
        valid = (head.startswith((b'ID3', b'fLaC', b'OggS', b'RIFF')) or
                 head[4:8] == b'ftyp' or
                 (len(head) >= 2 and head[0] == 0xff and (head[1] & 0xf0) == 0xf0))
        if not valid:
            raise ValueError('服务器未返回可识别的原始音频（可能是过期链接或加密内容）')
        if os.path.getsize(temp) == 0:
            raise ValueError('空音频文件')
        os.replace(temp, filepath)
        logging.info('已保存: %s', filename)
        return filename
    finally:
        if os.path.exists(temp):
            os.remove(temp)
