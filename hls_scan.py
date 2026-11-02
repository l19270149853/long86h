# -*- coding: utf-8 -*-
# HLS 直播源自动扫描器 v2 (重写版)
# 名单里是一批同型 HLS 服务器 (频道地址统一为 /hls/<编号>/index.m3u8),
# 本脚本: 拉名单 -> 抠服务器地址 -> 并发探活 -> 并发爆破频道编号 -> 输出清单。
#
# 相比旧版的改动 (旧版慢的原因全在串行):
#   1. 爆破改全程多线程 (旧版 process_line 是单线程硬扫, 一个服务器最坏几分钟)
#   2. 验真: 只认返回内容带 #EXTM3U 的地址 (旧版只看状态码+测速, 假页面也算活,
#      而且它测的 1~2KB 播放列表"速度"其实只是连接延迟, 测了个寂寞)
#   3. 砍掉旧版结尾对 h1 的重复第二遍扫描 (同一个服务器有几个频道就被重扫几遍)
#   4. 地址正则放宽: https 也认, 端口 1~5 位 (旧版 5 位端口如 10088 会被漏掉)
#   5. 两段式扫描: 先并发试每台的 1~20 号, 全灭的服务器直接放弃, 不浪费后面的爆破
#
# 输出: h1.txt, 每行 "服务器序号A频道号,地址" (占位名, 台名需对着画面认)
import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

# ---------------- 配置 ----------------
SRC_URL = "https://10694.kstore.space/hlstvid.txt"   # 名单地址
OUT_FILE = "h1.txt"                                  # 输出文件
WORKERS = 100          # 并发线程数 (名单大可调高, 服务器弱可调低)
TIMEOUT = (2, 3)       # (连接超时, 读取超时) 秒
FIRST_RANGE = 20       # 探活段: 先试每台的 1~20 号
SCAN_RANGE = 100       # 爆破段: 活服务器扫到 100 号
EXT_STEP = 50          # 延伸段: 上一段有命中就再往后扫 50 个
MAX_I = 300            # 频道编号上限
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

BASE_RE = re.compile(
    r'https?://(?:(?:\d{1,3}\.){3}\d{1,3}|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})'
    r'(?::\d{1,5})?')

_local = threading.local()


def _session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.trust_env = False  # 扫描器直连, 不吃环境变量里的代理设置
        s.headers.update({"User-Agent": UA})
        _local.s = s
    return s


def probe(url):
    """命中返回 url, 否则 None。只认真 m3u8: 状态 200 且内容带 #EXTM3U。"""
    try:
        r = _session().get(url, stream=True, timeout=TIMEOUT)
        try:
            if r.status_code != 200:
                return None
            head = b""
            for chunk in r.iter_content(chunk_size=2048):
                if chunk:
                    head += chunk
                if len(head) >= 2048:
                    break
            return url if b"#EXTM3U" in head else None
        finally:
            r.close()
    except requests.RequestException:
        return None


def chan_url(base, i):
    return "%s/hls/%d/index.m3u8" % (base, i)


def sweep(tasks):
    """tasks: [(base, i), ...] -> {base: set(i)} 全部并发跑完"""
    hits = {}
    if not tasks:
        return hits
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = ex.map(lambda t: (t[0], t[1], probe(chan_url(t[0], t[1]))),
                         tasks)
        for base, i, ok in results:
            if ok:
                hits.setdefault(base, set()).add(i)
    return hits


def main():
    t0 = time.time()
    logging.info("下载名单: %s", SRC_URL)
    try:
        text = requests.get(SRC_URL, timeout=15,
                            headers={"User-Agent": UA}).text
    except requests.RequestException as e:
        logging.error("名单下载失败: %s", e)
        return

    bases, seen = [], set()
    for m in BASE_RE.finditer(text):
        b = m.group(0).rstrip("/")
        if b not in seen:
            seen.add(b)
            bases.append(b)
    logging.info("名单里共 %d 台服务器", len(bases))
    if not bases:
        return

    # 第一段: 每台试 1~20 号, 全灭的直接放弃
    hits = sweep([(b, i) for b in bases for i in range(1, FIRST_RANGE + 1)])
    alive = [b for b in bases if b in hits]
    logging.info("探活: %d 台有频道 (%.1fs)", len(alive), time.time() - t0)

    # 第二段: 活服务器扫 21~100 号
    more = sweep([(b, i) for b in alive
                  for i in range(FIRST_RANGE + 1, SCAN_RANGE + 1)])
    for b, s in more.items():
        hits.setdefault(b, set()).update(s)

    # 延伸段: 上一段 (最后 50 个号) 还有命中就再往后扫一段, 到 MAX_I 为止
    start = SCAN_RANGE + 1
    while start <= MAX_I:
        end = min(start + EXT_STEP - 1, MAX_I)
        cand = [b for b in alive
                if any(start - EXT_STEP <= i < start for i in hits.get(b, ()))]
        if not cand:
            break
        more = sweep([(b, i) for b in cand for i in range(start, end + 1)])
        for b, s in more.items():
            hits.setdefault(b, set()).update(s)
        logging.info("延伸 %d~%d: %d 台仍在出频道", start, end, len(more))
        start = end + 1

    lines = []
    for idx, b in enumerate(bases, start=1):
        for i in sorted(hits.get(b, ())):
            lines.append("%dA%d,%s" % (idx, i, chan_url(b, i)))
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    logging.info("完成: %d 台活服务器, %d 个频道 -> %s (总耗时 %.1fs)",
                 len(alive), len(lines), OUT_FILE, time.time() - t0)


if __name__ == "__main__":
    main()

