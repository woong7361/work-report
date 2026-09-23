# -*- coding: utf-8 -*-
"""뜻밖의 실패를 한 곳에 모은다.

뷰어는 창 없이 뜨고 수집기는 에이전트가 부르므로, 터지면 화면에 아무것도 남지
않는다. 나중에 "그때 왜 안 됐지"를 물을 곳이 필요하다.

화면에는 보이지 않는다. 보고 폴더의 `runlog\\error.log`에 쌓이고 한 달이 지난
줄은 버린다. 실행 기록(runlog\\<월>\\)과 달리 보관 설정과 무관하게 한 달이다.
"""
import io
import os
import time
import traceback
from datetime import datetime, timedelta

KEEP_DAYS = 30
MAX_LINES = 5000        # 같은 오류가 되풀이돼도 파일이 무한정 자라지 않게


def _path(root):
    folder = os.path.join(root, 'runlog')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return os.path.join(folder, 'error.log')


def trim(root):
    """한 달이 지난 줄을 버린다. 실패해도 조용히 넘어간다."""
    path = _path(root)
    if not os.path.isfile(path):
        return
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    try:
        with io.open(path, encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except OSError:
        return
    keep, keeping = [], False
    for line in lines:
        head = line[:10]
        dated = len(line) > 10 and head[4:5] == '-' and head[7:8] == '-'
        if dated:
            keeping = head >= cutoff
        if keeping:
            keep.append(line)
    if len(keep) > MAX_LINES:
        keep = keep[-MAX_LINES:]
    if len(keep) == len(lines):
        return
    try:
        with io.open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.writelines(keep)
    except OSError:
        pass


def log_error(root, where, message, exc=None):
    """오류 한 건을 남긴다. 이 함수는 절대 예외를 올리지 않는다."""
    try:
        if not root:
            return
        text = '%s  %-8s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), where, message)
        if exc is not None:
            detail = ''.join(traceback.format_exception_only(type(exc), exc)).strip()
            text += '    %s\n' % detail
        with io.open(_path(root), 'a', encoding='utf-8', newline='') as fh:
            fh.write(text)
        trim(root)
    except Exception:
        pass        # 기록에 실패했다고 본래 작업을 멈추지는 않는다
