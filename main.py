#!/usr/bin/env python3
"""命令行批量保存本人网易云账号可播放的歌单音频。"""
import argparse
import getpass
import logging
import os
import sys

from music_core import MusicClient, download, safe_name


def main():
    parser = argparse.ArgumentParser(description='使用已登录账号的播放地址保存歌单音频')
    parser.add_argument('playlist_url', help='歌单链接或 ID')
    parser.add_argument('-o', '--output', default='songs_dir', help='保存根目录')
    parser.add_argument('--cookie-env', default='NCM_COOKIE', help='Cookie 环境变量名（默认 NCM_COOKIE）')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cookie = os.environ.get(args.cookie_env) or getpass.getpass('请输入登录 Cookie（至少包含 MUSIC_U，不回显）: ')
    try:
        client = MusicClient(cookie)
        try:
            name, songs = client.playlist(args.playlist_url)
            folder = os.path.join(args.output, safe_name(name))
            os.makedirs(folder, exist_ok=True)
            ok = failed = 0
            for i, song in enumerate(songs, 1):
                try:
                    download(client, song, folder)
                    ok += 1
                except Exception as exc:
                    failed += 1
                    logging.warning('[%d/%d] %s (%s) 失败: %s', i, len(songs), song[0], song[2], exc)
            logging.info('总计 %d 首，成功 %d 首，失败 %d 首；目录：%s', len(songs), ok, failed, folder)
            return 1 if failed else 0
        finally:
            client.session.close()
    except (ValueError, OSError) as exc:
        logging.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
