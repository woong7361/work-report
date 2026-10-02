# -*- coding: utf-8 -*-
"""비밀값을 config.json 밖에, 이 계정에서만 풀리는 형태로 둔다.

config.json 은 보고서를 쓰는 에이전트가 읽는 폴더 안에 있다(러너가 --add-dir 로
보고 폴더를 열어 준다). 토큰을 거기 평문으로 두면 에이전트의 시야에 들어오고,
한 번 들어온 값은 대화 기록에도 남는다. 그래서 따로 둔다.

Windows DPAPI(CryptProtectData)로 묶는다. 같은 PC의 같은 사용자 계정에서만
풀린다. 파일을 복사해 가도 다른 계정에서는 열리지 않고, 비밀번호를 따로
외울 필요도 없다. 추가 설치물도 없다 - 운영체제가 들고 있는 기능이다.

읽는 쪽은 뷰어와 pms 스크립트뿐이다. 보고서를 쓰는 에이전트는 이 값을 쓰지 않는다.
"""

import base64
import ctypes
import io
import json
import os
from ctypes import wintypes

STORE = 'secrets.dat'


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob):
    out = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return out


def _protect(text):
    src, _keep = _blob(text.encode('utf-8'))
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src), u'work-report', None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError('CryptProtectData failed')
    return base64.b64encode(_take(out)).decode('ascii')


def _unprotect(packed):
    src, _keep = _blob(base64.b64decode(packed.encode('ascii')))
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError('CryptUnprotectData failed')
    return _take(out).decode('utf-8')


def _path(root):
    return os.path.join(root, 'pms', STORE)


def _load(root):
    try:
        with io.open(_path(root), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def put(root, name, value):
    """값을 넣는다. 빈 값을 주면 지운다."""
    store = _load(root)
    if value:
        store[name] = _protect(value)
    else:
        store.pop(name, None)
    folder = os.path.dirname(_path(root))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(_path(root), 'w', encoding='utf-8', newline='') as fh:
        fh.write(json.dumps(store, ensure_ascii=False, indent=1))
    return True


def get(root, name):
    """값을 꺼낸다. 없거나 다른 계정에서 만든 것이면 빈 문자열."""
    packed = _load(root).get(name)
    if not packed:
        return ''
    try:
        return _unprotect(packed)
    except Exception:
        return ''


def has(root, name):
    """값이 있는지만. 값을 꺼내지 않고 묻고 싶을 때 쓴다."""
    return bool(_load(root).get(name))
