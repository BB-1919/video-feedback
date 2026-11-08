#!/usr/bin/env python3
import glob
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIN = os.name == 'nt'
if not WIN:
    import fcntl

VERSION = '1.5.0'
MAKER = 'BB'
LANG0 = 'en'
LANG = LANG0


def L(ko, en):
    return en if LANG == 'en' else ko


CHANGES = [('1.5.0', '2026-11-08', 'English version: every screen, message, export and the picture guide in English or Korean. '
            'Switch language in this panel (ⓘ). Runs on the Python 3.9 that comes with macOS.'),
           ('1.4.7', '2026-11-07', 'Drag the lines between panels to resize them (push all the way to fold a side panel). '
            'Pop the video out into its own window (W) and drag it to a second monitor; the other panels can pop out too (⧉). '
            'Shortcuts work from any window.'),
           ('1.4.6', '2026-11-07', 'Pick several videos on the left (⇧G) and leave the same whole-video note on all of them at once. '
                                   'Separate downloads for Mac and Windows, each with the picture guide.'),
           ('1.4.5', '2026-11-07', 'Claude windows are listed by the name on their terminal tab. '
                                   'Messages to a window that runs without permission prompts no longer ask "accept?" each time.'),
           ('1.4.4', '2026-11-07', 'Choose how the left list is sorted: newest video, name, or oldest (remembered). '
                                   'The list now appears at once; folder dates fill in a moment later.'),
           ('1.4.3', '2026-11-07', 'Folders are sorted by their newest video, so the channel you just finished is on top. '
                                   'Each folder shows when its latest video arrived.'),
           ('1.4.2', '2026-11-06', 'When a new version arrives, the server restarts itself within a minute and the page shows a reload button. '
                                   'Picture guide (GUIDE.html) in the zip.'),
           ('1.4.1', '2026-11-06', 'The middle column now fits the window height: the video shrinks to the space left, so typing a note never scrolls '
                                   'the top of the video (e.g. a Shorts title) out of view. Slightly smaller buttons; text size unchanged.'),
           ('1.4', '2026-11-06', 'Runs on Windows and Mac (zip with a start file for each). "Done" copies the request at once, '
                                 'then: send each video to its own window automatically, everything to one window, or pick per video. '
                                 'Delete selected notes or all notes. The page keeps working after a server restart (just reload).'),
           ('1.3.1', '2026-11-06', 'Folder buttons (find, up, Finder) and the current path moved into the folder panel. ⌫ goes up a folder.'),
           ('1.3', '2026-11-06', '"Done" hands every unsent note to an AI in one go, grouped by channel: into an open Claude window '
                                 'you pick (it never types into a plain terminal), a new AI window, or just the clipboard. '
                                 'The request is always copied too.'),
           ('1.2', '2026-11-06', 'Export menu, in this order: AI → person → editing app (DaVinci Resolve, Final Cut Pro, Premiere Pro, CapCut). '
                                 'Whole-video notes (color/LUT, filters, quality, frame rate …). About panel.'),
           ('1.1', '2026-11-06', 'Full keyboard control, also with Korean input on. Quick file finder (F).'),
           ('1.0', '2026-11-06', 'First version: pause and note, time ranges, done marks.')]
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.splitext(os.path.abspath(__file__))[0] + '.html'
HOME = os.path.expanduser('~')
VDIR = os.path.join(HOME, '.video-feedback')
ST = f'{VDIR}/피드백_서버.json'
TOKF = f'{VDIR}/피드백_출입표'
LASTF = f'{VDIR}/피드백_마지막.txt'
RECENT = f'{VDIR}/피드백_최근.json'
LOCKF = f'{VDIR}/피드백.lock'
LOG = f'{HOME}/Library/Logs/video-feedback.log' if os.path.isdir(f'{HOME}/Library/Logs') else f'{VDIR}/피드백_기록.log'
LABEL = 'local.video-feedback'
PLIST = f'{HOME}/Library/LaunchAgents/{LABEL}.plist'
PORTS = (8765, 8766, 8767)
IDLE = 30 * 60
VEXT = ('.mp4', '.mov', '.m4v', '.webm')
TOKEN = ''
PORT = 0
LAST = time.time()


def now():
    return time.strftime('%Y-%m-%d %H:%M')


def tf(s):
    m, r = divmod(float(s), 60)
    return f'{int(m)}:{r:04.1f}'


def fb_path(v):
    stem = os.path.splitext(v)[0]
    for s in ('.피드백.json', '.feedback.json'):
        if os.path.exists(stem + s):
            return stem + s
    return stem + L('.피드백.json', '.feedback.json')


def allowed(p):
    p = os.path.realpath(p)
    if WIN:
        return os.path.isabs(p)
    return p.startswith(HOME + '/') or p == HOME or p.startswith('/Volumes/')


def alive(pid):
    if WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def reveal(p):
    if WIN:
        subprocess.run(f'explorer /select,"{os.path.normpath(p)}"')
    else:
        subprocess.run(['open', '-R', p])


def open_url(u):
    if WIN:
        webbrowser.open(u)
    else:
        subprocess.run(['open', u])


def file_uri(p):
    return pathlib.Path(os.path.abspath(p)).as_uri()


def timed(fn, *a, t=8):
    box = {}

    def run():
        try:
            box['r'] = fn(*a)
        except Exception as e:
            box['e'] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(t)
    if th.is_alive():
        raise TimeoutError(L('디스크(외장하드)가 8초 안에 답하지 않습니다', 'The drive did not answer within 8 seconds'))
    if 'e' in box:
        raise box['e']
    return box.get('r')


class Lock:
    def __enter__(self):
        os.makedirs(VDIR, exist_ok=True)
        self.f = open(LOCKF, 'a+')
        if WIN:
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self.f, fcntl.LOCK_EX)

    def __exit__(self, *a):
        if WIN:
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def load_json(p):
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {}


def write_json(p, d):
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load(v):
    d = load_json(fb_path(v)) or {'영상': v, '메모': []}
    d.setdefault('메모', [])
    return d


def save(v, d, r=None):
    d['영상'] = v
    d['고친때'] = now()
    d['메모'].sort(key=lambda m: (not m.get('전체'), m['시작']))
    p = fb_path(v)
    write_json(p, d)
    own = r is None
    if own:
        r = load_json(RECENT)
    r[p] = {'영상': v, '전체': len(d['메모']), '남음': sum(not m['반영'] for m in d['메모']), '고친때': d['고친때']}
    if own:
        write_recent(r)
    return d


def write_recent(r):
    items = sorted(r.items(), key=lambda kv: kv[1]['고친때'], reverse=True)
    write_json(RECENT, dict(kv for i, kv in enumerate(items) if i < 50 or (kv[1].get('남음') and i < 300)))


def apply_many(vs, op):
    out = []
    with Lock():
        r = load_json(RECENT)
        for v in vs:
            try:
                d = load(v)
                new_memo(d['메모'], {'전체': True, '분류': op.get('분류'), '글': op['글']})
                save(v, d, r)
                out.append({'v': v, 'ok': True})
            except Exception as e:
                out.append({'v': v, 'ok': False, '오류': str(e)})
        write_recent(r)
    return out


def new_memo(M, op):
    whole = bool(op.get('전체'))
    s = 0.0 if whole else round(float(op['시작']), 2)
    e = None if whole or op.get('끝') in (None, '') else round(float(op['끝']), 2)
    if e is not None and e < s:
        s, e = e, s
    if e is not None and e - s < 0.05:
        e = None
    M.append({'번호': max([m['번호'] for m in M], default=0) + 1, '전체': whole, '분류': str(op.get('분류') or ''),
              '시작': s, '끝': e, '시간': L('영상 전체', 'Whole video') if whole else tf(s) + (f' ~ {tf(e)}' if e is not None else ''),
              '글': str(op['글']).strip(), '적은때': now(), '반영': False, '반영내용': '', '반영때': ''})


def apply(v, op):
    with Lock():
        d = load(v)
        M = d['메모']
        k = op.get('op')
        if k == 'add':
            new_memo(M, op)
            if op.get('길이'):
                d['길이'] = round(float(op['길이']), 2)
        elif k == 'clear':
            M.clear()
        elif k == 'delmany':
            nums = {int(x) for x in op.get('번호들', [])}
            M[:] = [m for m in M if m['번호'] not in nums]
        else:
            m = next((m for m in M if m['번호'] == int(op['번호'])), None)
            if m is None:
                raise ValueError(L('그 번호의 메모가 없습니다', 'There is no note with that number'))
            if k == 'edit':
                m['글'] = str(op['글']).strip()
                m['넘긴때'] = m['넘긴곳'] = ''
            elif k == 'del':
                M.remove(m)
            elif k == 'toggle':
                m['반영'] = bool(op['반영'])
                m['반영때'] = now() if m['반영'] else ''
                if m['반영'] and not m['반영내용']:
                    m['반영내용'] = L('직접 확인', 'Checked by hand')
                if not m['반영']:
                    m['넘긴때'] = m['넘긴곳'] = ''
            else:
                raise ValueError(L('모르는 동작', 'Unknown action'))
        return save(v, d)


def vtime(st):
    return max(st.st_mtime, getattr(st, 'st_birthtime', st.st_ctime if os.name == 'nt' else 0))


def newest(q, depth=4, budget=3000, until=0):
    best, stack = 0, [(q, 0)]
    while stack and budget > 0:
        if until and time.time() > until:
            return None
        d, lv = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    budget -= 1
                    if e.name.startswith('.'):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if lv < depth:
                            stack.append((e.path, lv + 1))
                    elif e.name.lower().endswith(VEXT):
                        best = max(best, vtime(e.stat()))
        except OSError:
            pass
    return best


def newest_all(p):
    until, out = time.time() + 6, {}
    for n in os.listdir(p):
        q = os.path.join(p, n)
        if not n.startswith('.') and os.path.isdir(q):
            out[n] = newest(q, until=until) if time.time() < until else None
    return out


def ls_dir(p):
    names = timed(os.listdir, p)
    dirs, vids = [], []
    for n in names:
        if n.startswith('.'):
            continue
        q = os.path.join(p, n)
        if os.path.isdir(q):
            dirs.append({'이름': n})
        elif n.lower().endswith(VEXT):
            st = os.stat(q)
            f = load_json(fb_path(q)).get('메모', [])
            vids.append({'이름': n, '크기': st.st_size, '고친때': vtime(st),
                         '메모': len(f), '반영': sum(bool(m.get('반영')) for m in f)})
    dirs.sort(key=lambda x: x['이름'])
    vids.sort(key=lambda x: -x['고친때'])
    with open(LASTF, 'w', encoding='utf-8') as f:
        f.write(p)
    up = os.path.dirname(p)
    return {'폴더': p, '위': up if up != p else None, '폴더들': dirs, '영상들': vids,
            '정렬': load_json(SETF).get('정렬', '최근'), '배치': load_json(SETF).get('배치', {})}


def pick_folder():
    ask = L('피드백할 영상이 있는 폴더를 고르세요', 'Choose the folder with the videos to review')
    if WIN:
        ps = ("Add-Type -AssemblyName System.Windows.Forms; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              f"$f=New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description='{ask}'; "
              "if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}")
        r = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', ps], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=300)
        return r.stdout.strip() or None
    r = subprocess.run(['osascript', '-e', 'activate', '-e',
                        f'POSIX path of (choose folder with prompt "{ask}")'],
                       capture_output=True, text=True, timeout=300)
    p = r.stdout.strip().rstrip('/')
    return p or None


def probe(v):
    info = {'fps': 30.0, 'num': 1, 'den': 30, 'w': 1080, 'h': 1920, 'dur': 0.0}
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                            'stream=r_frame_rate,width,height:format=duration', '-of', 'json', v],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
        j = json.loads(r.stdout)
        st = j['streams'][0]
        a, b = (int(x) for x in st['r_frame_rate'].split('/'))
        info.update(fps=a / b, num=b, den=a, w=int(st['width']), h=int(st['height']), dur=float(j['format']['duration']))
    except Exception:
        pass
    return info


def _xml(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _one(m):
    tag = f"[{m['분류']}] " if m.get('분류') else (L('[영상 전체] ', '[Whole video] ') if m.get('전체') else '')
    return f"{m['번호']}. {tag}" + ' '.join(str(m['글']).split())


def ex_davinci(v, d, P):
    fb = round(P['fps'])

    def tc(fr):
        fr += 3600 * fb
        h, fr = divmod(fr, 3600 * fb)
        mi, fr = divmod(fr, 60 * fb)
        s, f = divmod(fr, fb)
        return f'{h:02d}:{mi:02d}:{s:02d}:{f:02d}'
    out = [f'TITLE: {os.path.splitext(os.path.basename(v))[0]}{L(" 수정요청", " changes")}', 'FCM: NON-DROP FRAME', '']
    for i, m in enumerate(d['메모'], 1):
        a = round(m['시작'] * fb)
        dur = max(1, round((m['끝'] - m['시작']) * fb)) if m.get('끝') is not None else 1
        col = 'ResolveColorGreen' if m['반영'] else ('ResolveColorPurple' if m.get('전체') else 'ResolveColorRed')
        out += [f'{i:03d}  001      V     C        {tc(a)} {tc(a + 1)} {tc(a)} {tc(a + 1)}  ',
                f" |C:{col} |M:{_one(m).replace('|', '/')} |D:{dur}", '']
    return L('_다빈치표시.edl', '_DaVinci_markers.edl'), '\n'.join(out)


def ex_premiere(v, d, P):
    ntsc = abs(P['fps'] - round(P['fps'])) > 0.01
    tb = round(P['fps'])
    rate = f'<rate><timebase>{tb}</timebase><ntsc>{"TRUE" if ntsc else "FALSE"}</ntsc></rate>'
    n = max(1, round((P['dur'] or d.get('길이') or 0) * P['fps']))
    name = _xml(os.path.basename(v))
    url = 'file://localhost' + file_uri(v)[len('file://'):]
    fileel = (f'<file id="file-1"><name>{name}</name><pathurl>{url}</pathurl>{rate}<duration>{n}</duration>'
              f'<media><video><samplecharacteristics><width>{P["w"]}</width><height>{P["h"]}</height></samplecharacteristics></video>'
              f'<audio><channelcount>2</channelcount></audio></media></file>')
    clip = lambda cid, f, extra='': (f'<clipitem id="{cid}"><name>{name}</name><duration>{n}</duration>{rate}'
                                     f'<start>0</start><end>{n}</end><in>0</in><out>{n}</out>{f}{extra}</clipitem>')
    mk = ''.join(f'<marker><name>{_xml(_one(m)[:60])}</name><comment>{_xml(m["글"])}</comment>'
                 f'<in>{round(m["시작"] * P["fps"])}</in><out>{round(m["끝"] * P["fps"]) if m.get("끝") is not None else -1}</out></marker>'
                 for m in d['메모'])
    aclip = clip('clip-a', '<file id="file-1"/>', '<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1">'
           f'<name>{_xml(os.path.splitext(os.path.basename(v))[0])}{L(" 수정요청", " changes")}</name><duration>{n}</duration>{rate}'
           f'<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>'
           f'<media><video><format><samplecharacteristics>{rate}<width>{P["w"]}</width><height>{P["h"]}</height></samplecharacteristics></format>'
           f'<track>{clip("clip-v", fileel)}</track></video>'
           f'<audio><track>{aclip}</track></audio></media>'
           f'{mk}</sequence></xmeml>\n')
    return L('_프리미어표시.xml', '_Premiere_markers.xml'), xml


def ex_fcp(v, d, P):
    num, den = P['num'], P['den']
    t = lambda sec: f'{round(sec * den / num) * num}/{den}s'
    n_dur = t(P['dur'] or d.get('길이') or 0)
    name = _xml(os.path.splitext(os.path.basename(v))[0])
    src = file_uri(v)
    mk = ''.join(f'<marker start="{t(m["시작"])}" duration="{t(m["끝"] - m["시작"]) if m.get("끝") is not None else f"{num}/{den}s"}" '
                 f'value="{_xml(_one(m)[:60])}" note="{_xml(m["글"])}"/>' for m in d['메모'])
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10"><resources>'
           f'<format id="r1" frameDuration="{num}/{den}s" width="{P["w"]}" height="{P["h"]}"/>'
           f'<asset id="r2" name="{name}" start="0s" duration="{n_dur}" hasVideo="1" hasAudio="1" format="r1" audioSources="1" audioChannels="2">'
           f'<media-rep kind="original-media" src="{src}"/></asset></resources>'
           f'<library><event name="{L("수정 요청", "Change requests")}"><project name="{name}{L(" 수정요청", " changes")}"><sequence format="r1" duration="{n_dur}" tcStart="0s" tcFormat="NDF">'
           f'<spine><asset-clip ref="r2" offset="0s" name="{name}" duration="{n_dur}" format="r1" tcFormat="NDF">{mk}</asset-clip></spine>'
           '</sequence></project></event></library></fcpxml>\n')
    return L('_파이널컷표시.fcpxml', '_FinalCut_markers.fcpxml'), xml


def ex_capcut(v, d, P):
    fb = round(P['fps'])

    def cc(sec):
        f = round(sec * fb)
        return f'{f // (60 * fb):02d}:{f // fb % 60:02d}:{f % fb:02d}'
    out = [L(f'캡컷 수정 시간표 — {os.path.basename(v)}', f'CapCut change list — {os.path.basename(v)}'),
           L(f'(캡컷은 표시 파일을 불러오는 기능이 없어 시간표로 드립니다 · 시간 = 분:초:칸, 1초 = {fb}칸)',
             f"(CapCut can't import marker files, so here is a time list · time = min:sec:frame, 1 sec = {fb} frames)"), '']
    for m in d['메모']:
        when = L('영상 전체', 'Whole video') if m.get('전체') else cc(m['시작']) + (f' ~ {cc(m["끝"])}' if m.get('끝') is not None else '')
        out.append(f"{'✓ ' if m['반영'] else ''}{m['번호']}. [{when}] {(m['분류'] + ' · ') if m.get('분류') else ''}{m['글']}")
    return L('_캡컷시간표.txt', '_CapCut_times.txt'), '\n'.join(out) + '\n'


EXPORTERS = {'davinci': ex_davinci, 'premiere': ex_premiere, 'fcp': ex_fcp, 'capcut': ex_capcut}


def export(v, kind, show=True):
    d = load(v)
    if not d['메모']:
        raise ValueError(L('메모가 없습니다', 'There are no notes'))
    suffix, body = EXPORTERS[kind](v, d, probe(v))
    out = os.path.splitext(v)[0] + suffix
    with open(out, 'w', encoding='utf-8') as f:
        f.write(body)
    if show:
        reveal(out)
    return out


def browser_bin():
    if WIN:
        c = [os.path.join(os.environ.get(e, ''), *s) for e in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')
             for s in (('Google', 'Chrome', 'Application', 'chrome.exe'), ('Microsoft', 'Edge', 'Application', 'msedge.exe'))]
    else:
        c = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
             '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    return next((x for x in c if os.path.isfile(x)), None)


def report(v, html, show=True):
    base = os.path.splitext(v)[0] + L('_수정요청', '_changes')
    with open(base + '.html', 'w', encoding='utf-8') as f:
        f.write(html)
    out = base + '.html'
    chrome = browser_bin()
    if chrome:
        tmp = f'{VDIR}/피드백_임시'
        os.makedirs(tmp, exist_ok=True)
        th, tp = f'{tmp}/정리본.html', f'{tmp}/정리본.pdf'
        with open(th, 'w', encoding='utf-8') as f:
            f.write(html)
        if os.path.exists(tp):
            os.remove(tp)
        try:
            subprocess.run([chrome, '--headless=new', '--disable-gpu', '--no-first-run', f'--user-data-dir={tmp}/크롬',
                            '--no-pdf-header-footer', f'--print-to-pdf={tp}', file_uri(th)],
                           capture_output=True, timeout=90)
            if os.path.exists(tp) and os.path.getsize(tp) > 1000:
                shutil.move(tp, base + '.pdf')
                out = base + '.pdf'
        except subprocess.TimeoutExpired:
            pass
    if show:
        reveal(out)
    return out


TMPD = f'{VDIR}/피드백_임시'
HANDOFF = f'{VDIR}/피드백_넘김'
SETF = f'{VDIR}/피드백_설정.json'
NOTEL = f'{TMPD}/텔레그램_없음'
def newcmds():
    return [('claude', L('클로드 코드', 'Claude Code')), ('gemini -i', L('제미나이 CLI', 'Gemini CLI')), ('codex', L('코덱스', 'Codex'))]


def request_text(items):
    if LANG == 'en':
        top = ['Each video folder below has a feedback file (<video name>.feedback.json). Please read it, make the changes and carry on with the work.', '']
        for i, (v, j, n) in enumerate(items, 1):
            top += [f'{i}) {os.path.dirname(v)}', f'   video {os.path.basename(v)} · feedback {os.path.basename(j)} · notes to fix: {n}']
        return '\n'.join(['[Video change request]', *top, '',
                          '- The keys in the file are Korean: 메모 = notes, 번호 = number, 시작/끝 = start/end (seconds in the video), '
                          '전체 = true for a note about the whole video (color, quality …), 분류 = category, 글 = the note, '
                          '반영 = done, 반영내용 = what you changed, 반영때 = when.',
                          '- Fix only the notes with "반영": false.',
                          '- For each note you fix, set "반영": true, "반영내용": "what you changed", "반영때": "YYYY-MM-DD HH:MM" in that file.',
                          '- Save the fixed video under the same name. Do not delete the old one; move it into a "previous_versions/" folder next to it.'])
    top = ['아래 영상 폴더마다 피드백 파일(<영상 이름>.피드백.json)이 있습니다. 확인해서 반영하고 작업을 진행해 주세요.', '']
    for i, (v, j, n) in enumerate(items, 1):
        top += [f'{i}) {os.path.dirname(v)}', f'   영상 {os.path.basename(v)} · 피드백 {os.path.basename(j)} · 고칠 메모 {n}개']
    return '\n'.join(['[영상 수정 요청]', *top, '',
                      '- "반영": false 인 메모만 고치면 됩니다. "시작"·"끝"은 영상 속 초, "전체": true 는 영상 전체(색감·화질 등)에 대한 요청입니다.',
                      '- 고친 메모는 그 파일에서 "반영": true, "반영내용": "무엇을 고쳤는지", "반영때": "YYYY-MM-DD HH:MM" 으로 바꿔 주세요.',
                      '- 고친 영상은 같은 이름으로 저장하고, 고치기 전 영상은 지우지 말고 같은 폴더의 이전버전/ 에 옮겨 주세요.'])


def channel_map():
    out = {}
    return out


def group_of(v, cmap):
    d = os.path.dirname(v)
    return d, os.path.basename(d)


def proc_args(pid):
    if sys.platform != 'darwin':
        return []
    try:
        import ctypes
        import ctypes.util
        import struct
        libc = ctypes.CDLL(ctypes.util.find_library('c'))
        mib, size = (ctypes.c_int * 3)(1, 49, int(pid)), ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0):
            return []
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0):
            return []
        raw = buf.raw[:size.value]
        argc, rest = struct.unpack('i', raw[:4])[0], raw[4:]
        rest = rest[rest.index(b'\0'):].lstrip(b'\0')
        return [a.decode('utf-8', 'replace') for a in rest.split(b'\0')[:argc]]
    except Exception:
        return []


def sessions():
    cfgs = {os.path.realpath(c) for c in [os.environ.get('CLAUDE_CONFIG_DIR'), f'{HOME}/.claude', f'{HOME}/.claude-2',
                                          *glob.glob(f'{HOME}/.claude-accounts/*')] if c and os.path.isdir(c)}
    out = []
    for cfg in cfgs:
        for f in glob.glob(f'{cfg}/sessions/*.json'):
            try:
                s = load_json(f)
                if not alive(s['pid']):
                    continue
            except Exception:
                continue
            if s.get('parkedJobId') or s.get('kind') not in ('interactive', 'bg') or not s.get('name') \
                    or not s.get('messagingSocketPath'):
                continue
            a = proc_args(s['pid'])
            rc = a[a.index('--remote-control') + 1] if '--remote-control' in a[:-1] else ''
            out.append({'이름': s['name'], 'cfg': cfg, '폴더': s.get('cwd', ''), '상태': s.get('status', ''),
                        '종류': s['kind'], '때': s.get('updatedAt', 0), '창이름': rc if rc and not rc.startswith('-') else '',
                        '우회': '--dangerously-skip-permissions' in a or 'bypassPermissions' in a})
    return sorted(out, key=lambda x: -x['때'])


def same(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def common_dir(folders):
    try:
        c = os.path.commonpath(folders)
    except ValueError:
        return HOME
    return HOME if os.path.dirname(c) == c else c


def handoff_plan(again=False):
    cmap, vids = channel_map(), []
    for j, info in load_json(RECENT).items():
        if not info.get('남음'):
            continue
        try:
            d = timed(load_json, j)
        except Exception:
            continue
        todo = [m for m in d.get('메모', []) if not m.get('반영')]
        if not todo:
            continue
        folder, label = group_of(info['영상'], cmap)
        vids.append({'v': info['영상'], 'json': j, '남음': len(todo), '새로': sum(not m.get('넘긴때') for m in todo),
                     '폴더': folder, '이름': label})
    again = again or not any(x['새로'] for x in vids)
    vids = [x for x in vids if again or x['새로']]
    S = sessions()
    for x in vids:
        m = next((s for s in S if same(s['폴더'], x['폴더'])), None)
        x['자동'] = {'어디': '창', '창': m['이름'], 'cfg': m['cfg']} if m else {'어디': '새창'}
    return {'영상': vids, '창': S, '다시': again, '공통': common_dir([x['폴더'] for x in vids]) if vids else HOME,
            '글': request_text([(x['v'], x['json'], x['남음']) for x in vids]) if vids else '',
            '새창': load_json(SETF).get('새창', 'claude'), '새창목록': newcmds()}


def claude_bin():
    exe = 'claude.exe' if WIN else 'claude'
    for c in (shutil.which('claude'), os.path.join(HOME, '.local', 'bin', exe), '/opt/homebrew/bin/claude', '/usr/local/bin/claude'):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError(L('이 컴퓨터에서 클로드 코드를 찾지 못했습니다', 'Claude Code was not found on this computer'))


def ai_env(cfg):
    os.makedirs(NOTEL, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT')}
    env['TELEGRAM_STATE_DIR'] = NOTEL
    if not WIN:
        env['PATH'] = f'{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:' + env.get('PATH', '/usr/bin:/bin')
    if cfg and os.path.realpath(cfg) != os.path.realpath(f'{HOME}/.claude'):
        env['CLAUDE_CONFIG_DIR'] = cfg
    else:
        env.pop('CLAUDE_CONFIG_DIR', None)
    return env


NOTOOLS = 'Bash,Edit,Write,MultiEdit,NotebookEdit,Read,Glob,Grep,WebFetch,WebSearch,Agent,Task,Skill'


def deliver(name, cfg, line, bypass=False):
    ask = (f'ToolSearch 로 SendMessage 도구를 불러온 뒤, to "{name}" 에게 아래 <글> 안의 내용을 한 글자도 바꾸지 말고 보내라. '
           f'보낸 뒤 "보냄" 한 단어만 답하라. 다른 일은 하지 마라.\n<글>\n{line}\n</글>')
    mode = ['--permission-mode', 'bypassPermissions', '--disallowedTools', NOTOOLS] if bypass else []
    try:
        r = subprocess.run([claude_bin(), '-p', '--allowedTools', 'ToolSearch,SendMessage', '--model', 'haiku',
                            '--strict-mcp-config', *mode], input=ask, cwd=TMPD, env=ai_env(cfg), capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError(L('2분 안에 배달하지 못했습니다', 'Could not deliver within 2 minutes')) from None
    if '보냄' not in r.stdout:
        raise RuntimeError(L('배달하지 못했습니다 — ', 'Could not deliver — ') + (r.stdout or r.stderr).strip()[-160:])


def fresh(d, stem, ext):
    p = os.path.join(d, stem + ext)
    for i in range(2, 100):
        if not os.path.exists(p):
            return p
        p = os.path.join(d, f'{stem}-{i}{ext}')
    raise RuntimeError(L('새 파일 이름을 만들지 못했습니다', 'Could not make a new file name'))


def launch_new(folder, line, label, cfg, show=True):
    os.makedirs(TMPD, exist_ok=True)
    exe = shlex.split(load_json(SETF).get('새창', 'claude'), posix=not WIN)
    if exe[0] == 'claude':
        exe = [claude_bin(), *exe[1:], '--name', (L('피드백_', 'feedback_') + label)[:60]]
    env = ai_env(cfg)
    if WIN:
        q = lambda s: "'" + str(s).replace("'", "''") + "'"
        sh = fresh(TMPD, '새창_' + time.strftime('%m%d-%H%M%S'), '.ps1')
        body = [f'$env:TELEGRAM_STATE_DIR = {q(NOTEL)}']
        if 'CLAUDE_CONFIG_DIR' in env:
            body.append(f'$env:CLAUDE_CONFIG_DIR = {q(env["CLAUDE_CONFIG_DIR"])}')
        body += [f'Set-Location -LiteralPath {q(folder)}', '& ' + ' '.join(q(x) for x in exe) + ' ' + q(line)]
        with open(sh, 'w', encoding='utf-8-sig') as f:
            f.write('\r\n'.join(body) + '\r\n')
        if show:
            subprocess.Popen(['powershell', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', sh],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        return sh
    sh = fresh(TMPD, '새창_' + time.strftime('%m%d-%H%M%S'), '.command')
    lines = ['#!/bin/zsh -l', f'export PATH={shlex.quote(env["PATH"])}', f'export TELEGRAM_STATE_DIR={shlex.quote(NOTEL)}']
    if 'CLAUDE_CONFIG_DIR' in env:
        lines.append(f'export CLAUDE_CONFIG_DIR={shlex.quote(env["CLAUDE_CONFIG_DIR"])}')
    lines += [f'cd {shlex.quote(folder)} || exit 1', ' '.join(map(shlex.quote, [*exe, line]))]
    with open(sh, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(sh, 0o755)
    if show:
        subprocess.run(['open', '-a', 'Terminal', sh])
    return sh


def mark_sent(vids, where):
    for x in vids:
        with Lock():
            d = load(x['v'])
            for m in d['메모']:
                if not m.get('반영'):
                    m['넘긴때'], m['넘긴곳'] = now(), where
            save(x['v'], d)


def handoff(body):
    plan = handoff_plan(bool(body.get('다시')))
    P = {x['v']: x for x in plan['영상']}
    S = {(s['이름'], s['cfg']): s for s in plan['창']}
    cfg_new = next((s['cfg'] for s in plan['창'] if s['종류'] == 'interactive'), None)
    dest = {}
    for c in body.get('보내기', []):
        x = P.get(c.get('v'))
        if not x:
            continue
        k = c.get('어디')
        key = ('창', c.get('창'), c.get('cfg')) if k == '창' else ('새창', '한곳' if c.get('한곳') else x['폴더']) if k == '새창' \
            else ('복사',)
        dest.setdefault(key, []).append(x)
    os.makedirs(HANDOFF, exist_ok=True)
    res = []
    for key, xs in dest.items():
        label = ' · '.join(dict.fromkeys(x['이름'] for x in xs))
        n = sum(x['남음'] for x in xs)
        try:
            if key[0] == '복사':
                where = '복사'
            else:
                req = fresh(HANDOFF, f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^0-9A-Za-z가-힣._-]+', '_', label)[:40]}", '.md')
                with open(req, 'w', encoding='utf-8') as f:
                    f.write(request_text([(x['v'], x['json'], x['남음']) for x in xs]) + '\n')
                line = L(f'영상 수정 요청서 {req} 를 읽고 그대로 진행해 주세요 (영상 {len(xs)}편 · 고칠 메모 {n}개).',
                         f'Please read the video change request {req} and carry it out ({len(xs)} video(s) · {n} note(s) to fix).')
                if key[0] == '창':
                    s = S.get(key[1:])
                    if not s:
                        raise RuntimeError(L('그 창이 닫혔습니다', 'That window has closed'))
                    deliver(s['이름'], s['cfg'], L('[영상 피드백] ', '[Video feedback] ') + line, s.get('우회', False))
                    where = s.get('창이름') or s['이름']
                else:
                    folder = key[1] if key[1] != '한곳' else common_dir([x['폴더'] for x in xs])
                    launch_new(folder, line, label, cfg_new, body.get('보여주기', True) is not False)
                    where = '새 창'
            mark_sent(xs, where)
            res.append({'이름': label, 'ok': True, '어디': where, '편': len(xs), '메모': n})
        except Exception as e:
            res.append({'이름': label, 'ok': False, '오류': str(e), '편': len(xs), '메모': n})
    return res


class H(BaseHTTPRequestHandler):
    server_version = 'feedback'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def _gate(self):
        global LAST
        LAST = time.time()
        if self.headers.get('Host', '') not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
            self._send(403, {'오류': L('이 컴퓨터 안에서만 엽니다', 'This page only opens on this computer')})
            return None
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if q.get('t') != TOKEN:
            self._send(403, {'오류': L('출입표가 맞지 않습니다 — 영상피드백을 다시 여세요', 'The access key does not match — open Video Feedback again')})
            return None
        return u.path, q

    def do_GET(self):
        g = self._gate()
        if not g:
            return
        path, q = g
        try:
            if path == '/':
                with open(PAGE, encoding='utf-8') as f:
                    page = f.read()
                ch = ''.join(f'<li><b>v{a}</b> <small>{b}</small> — {_xml(c)}</li>' for a, b, c in CHANGES)
                page = (page.replace('{{버전}}', VERSION).replace('{{만든이}}', MAKER).replace('{{언어}}', LANG)
                        .replace('{{바뀐것}}', ch).replace('{{폴더}}', _xml(HERE)))
                return self._send(200, page.encode(), 'text/html; charset=utf-8')
            if path == '/api/ping':
                return self._send(200, {'ok': 1, 'v': VERSION})
            if path == '/api/ls':
                p = q.get('p') or default_dir()
                if not allowed(p) or not os.path.isdir(p):
                    return self._send(400, {'오류': L('열 수 없는 폴더입니다', 'This folder cannot be opened')})
                return self._send(200, ls_dir(os.path.realpath(p)))
            if path == '/api/newest':
                p = q.get('p') or ''
                if not allowed(p) or not os.path.isdir(p):
                    return self._send(400, {'오류': L('열 수 없는 폴더입니다', 'This folder cannot be opened')})
                p = os.path.realpath(p)
                return self._send(200, {'폴더': p, '최근': timed(newest_all, p)})
            if path == '/api/handoff':
                return self._send(200, handoff_plan(q.get('다시') == '1'))
            v = q.get('v', '')
            if not (allowed(v) and v.lower().endswith(VEXT) and timed(os.path.isfile, v)):
                return self._send(400, {'오류': L('영상을 찾을 수 없습니다', 'Video not found')})
            if path == '/api/fb':
                st, d = os.stat(v), load(v)
                ai = request_text([(v, fb_path(v), sum(not m.get('반영') for m in d['메모']))])
                return self._send(200, {'json': fb_path(v), 'data': d, 'vm': st.st_mtime, '크기': st.st_size, 'ai': ai})
            if path == '/v':
                return self._video(v)
            self._send(404, {'오류': L('없는 주소', 'No such address')})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(500, {'오류': str(e)})

    def _video(self, v):
        size = os.path.getsize(v)
        start, end, code = 0, size - 1, 200
        rng = self.headers.get('Range', '')
        if rng.startswith('bytes='):
            a, _, b = rng[6:].split(',')[0].strip().partition('-')
            if a == '':
                start = max(0, size - int(b or 0))
            else:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            if start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            code = 206
        ext = os.path.splitext(v)[1].lower()
        self.send_response(code)
        self.send_header('Content-Type', 'video/webm' if ext == '.webm' else 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with open(v, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                c = f.read(min(1 << 20, left))
                if not c:
                    break
                self.wfile.write(c)
                left -= len(c)

    def do_POST(self):
        g = self._gate()
        if not g:
            return
        path, q = g
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(n, 30 << 20)) or b'{}')
            if path in ('/api/op', '/api/export', '/api/report'):
                v = body.get('v', '')
                if not (allowed(v) and v.lower().endswith(VEXT) and os.path.isfile(v)):
                    return self._send(400, {'오류': L('영상을 찾을 수 없습니다', 'Video not found')})
                if path == '/api/op':
                    d = apply(v, body)
                    ai = request_text([(v, fb_path(v), sum(not m.get('반영') for m in d['메모']))])
                    return self._send(200, {'data': d, 'ai': ai})
                show = body.get('보여주기', True) is not False
                if path == '/api/export':
                    if body.get('kind') not in EXPORTERS:
                        return self._send(400, {'오류': L('모르는 프로그램', 'Unknown app')})
                    return self._send(200, {'파일': export(v, body['kind'], show)})
                return self._send(200, {'파일': report(v, str(body.get('html', '')), show)})
            if path == '/api/opmany':
                vs, g = body.get('vs'), str(body.get('글') or '').strip()
                if not isinstance(vs, list) or not vs or len(vs) > 300 or not g:
                    return self._send(400, {'오류': L('고른 영상과 메모를 확인하세요', 'Check the picked videos and the note')})
                vs = list(dict.fromkeys(str(v) for v in vs))
                ok = [v for v in vs if allowed(v) and v.lower().endswith(VEXT) and os.path.isfile(v)]
                res = apply_many(ok, {'분류': body.get('분류'), '글': g}) if ok else []
                okset = set(ok)
                return self._send(200, {'결과': res + [{'v': v, 'ok': False, '오류': L('영상을 찾을 수 없습니다', 'Video not found')} for v in vs if v not in okset]})
            if path == '/api/handoff':
                return self._send(200, {'결과': handoff(body)})
            if path == '/api/set':
                if '언어' in body:
                    if body['언어'] not in ('ko', 'en'):
                        return self._send(400, {'오류': L('모르는 말입니다', 'Unknown language')})
                    set_lang(body['언어'])
                    return self._send(200, {'언어': LANG})
                if '배치' in body:
                    lay = body['배치']
                    if not isinstance(lay, dict) or len(json.dumps(lay)) > 4000:
                        return self._send(400, {'오류': L('배치를 확인해 주세요', 'Check the layout')})
                    with Lock():
                        s = load_json(SETF)
                        s['배치'] = lay
                        write_json(SETF, s)
                    return self._send(200, {'배치': True})
                if '정렬' in body:
                    if body['정렬'] not in ('최근', '이름', '오래된'):
                        return self._send(400, {'오류': L('모르는 정렬입니다', 'Unknown sort order')})
                    with Lock():
                        s = load_json(SETF)
                        s['정렬'] = body['정렬']
                        write_json(SETF, s)
                    return self._send(200, {'정렬': body['정렬']})
                cmd = str(body.get('새창', '')).strip()
                if not cmd or len(cmd) > 200 or '\n' in cmd:
                    return self._send(400, {'오류': L('명령을 확인해 주세요', 'Check the command')})
                with Lock():
                    s = load_json(SETF)
                    s['새창'] = cmd
                    write_json(SETF, s)
                return self._send(200, {'새창': cmd})
            if path == '/api/pick':
                return self._send(200, {'폴더': pick_folder()})
            if path == '/api/quit':
                self._send(200, {'ok': 1})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._send(404, {'오류': L('없는 주소', 'No such address')})
        except Exception as e:
            self._send(500, {'오류': str(e)})


def load_lang():
    global LANG
    LANG = load_json(SETF).get('언어') if os.path.exists(SETF) else None
    if LANG not in ('ko', 'en'):
        LANG = LANG0


def set_lang(x):
    global LANG
    with Lock():
        s = load_json(SETF)
        s['언어'] = x
        write_json(SETF, s)
    LANG = x


def running():
    try:
        st = load_json(ST)
        urllib.request.urlopen(f'http://127.0.0.1:{st["port"]}/api/ping?t={st["token"]}', timeout=2)
        return st
    except Exception:
        return None


def start_dir(arg):
    if arg:
        a = os.path.realpath(os.path.expanduser(arg))
        if os.path.isfile(a):
            return os.path.dirname(a), a
        if os.path.isdir(a):
            return a, ''
    return '', ''


def default_dir():
    cands = []
    if os.path.exists(LASTF):
        cands.append(open(LASTF, encoding='utf-8').read().strip())
    cands += [os.path.join(HOME, 'Movies'), os.path.join(HOME, 'Videos')]
    for c in cands:
        try:
            if c and timed(os.path.isdir, c):
                return c
        except TimeoutError:
            continue
    return HOME


def open_page(st, d, v):
    q = {'t': st['token'], 'd': d}
    if v:
        q['v'] = v
    open_url(f'http://127.0.0.1:{st["port"]}/?' + urllib.parse.urlencode(q))


def token():
    try:
        t = open(TOKF, encoding='utf-8').read().strip()
        if len(t) >= 16:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(16)
    os.makedirs(VDIR, exist_ok=True)
    with open(TOKF, 'w', encoding='utf-8') as f:
        f.write(t)
    os.chmod(TOKF, 0o600)
    return t


def serve():
    global TOKEN, PORT
    TOKEN = token()
    srv = None
    for p in PORTS:
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), H)
            PORT = p
            break
        except OSError:
            continue
    if not srv:
        sys.exit(L('포트 8765~8767 이 모두 쓰이는 중이라 멈췄습니다', 'Ports 8765–8767 are all in use, so it stopped'))
    os.makedirs(VDIR, exist_ok=True)
    write_json(ST, {'port': PORT, 'token': TOKEN, 'pid': os.getpid(), '켠때': now()})
    os.chmod(ST, 0o600)

    me = os.path.abspath(__file__)
    born, again = os.path.getmtime(me), []

    def watch():
        while True:
            time.sleep(60)
            try:
                if os.path.getmtime(me) != born:
                    again.append(1)
                    srv.shutdown()
                    return
            except OSError:
                pass
            if time.time() - LAST > IDLE:
                srv.shutdown()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        if load_json(ST).get('pid') == os.getpid():
            os.remove(ST)
    if again:
        if WIN:
            py = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            subprocess.Popen([py if os.path.isfile(py) else sys.executable, me, '--serve'], creationflags=0x00000008 | 0x00000200,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        else:
            os.execv(sys.executable, [sys.executable, me, '--serve'])


def main():
    a = sys.argv[1:]
    load_lang()
    if a[:1] in (['--서버'], ['--serve']):
        return serve()
    if a[:1] in (['--lang'], ['--언어']):
        if a[1:2] not in (['ko'], ['en']):
            sys.exit('--lang ko | en')
        set_lang(a[1])
        st = running()
        if st:
            req = urllib.request.Request(f'http://127.0.0.1:{st["port"]}/api/set?t={st["token"]}', data=json.dumps({'언어': a[1]}).encode(),
                                         method='POST', headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=3)
        print(L('화면 말: 한국어', 'Language: English'))
        return
    if a[:1] in (['--남은것'], ['--todo']):
        r = load_json(RECENT)
        left = 0
        for p, info in r.items():
            if not info.get('남음'):
                continue
            try:
                d = timed(load_json, p)
            except Exception as e:
                print(L(f'⚠ {p} 못 읽음: {e}', f'⚠ could not read {p}: {e}'))
                continue
            todo = [m for m in d.get('메모', []) if not m['반영']]
            if not todo:
                continue
            print(f'\n■ {d.get("영상")}\n  ' + L(f'파일: {p} · 남음 {len(todo)} / {len(d["메모"])}', f'file: {p} · left {len(todo)} / {len(d["메모"])}'))
            for m in todo:
                print(f'  {m["번호"]}. [{m["시간"]}] {m["글"]}')
            left += len(todo)
        print(L(f'\n반영 안 된 메모 {left}개', f'\n{left} note(s) not done') if left else L('반영 안 된 메모가 없습니다', 'No notes left to do'))
        return
    if a[:1] in (['--반영'], ['--done']):
        if len(a) < 4:
            sys.exit(L('쓰는 법: --반영 <json> <번호[,번호]> "무엇을 고쳤나"', 'usage: --done <json> <number[,number]> "what you changed"'))
        p, nums, what = a[1], [int(x) for x in a[2].split(',')], a[3]
        with Lock():
            d = load_json(p)
            for m in d.get('메모', []):
                if m['번호'] in nums:
                    m.update({'반영': True, '반영내용': what, '반영때': now()})
            save(d['영상'], d)
        left = sum(not m['반영'] for m in d['메모'])
        print(L(f'반영 표시 {len(nums)}개 · 남음 {left}', f'marked done: {len(nums)} · left: {left}'))
        return
    if a[:1] in (['--끄기'], ['--stop']):
        st = running()
        if st:
            req = urllib.request.Request(f'http://127.0.0.1:{st["port"]}/api/quit?t={st["token"]}', data=b'{}', method='POST',
                                         headers={'Host': f'127.0.0.1:{st["port"]}'})
            urllib.request.urlopen(req, timeout=3)
        print(L('껐습니다', 'Stopped') if st else L('켜져 있지 않습니다', 'It was not running'))
        return
    if a[:1] in (['--앱만들기'], ['--app']):
        name = L('영상피드백', 'Video Feedback')
        app = f'{HOME}/Applications/{name}.app'
        me = os.path.abspath(__file__)
        cmd = f'/bin/zsh -lc "python3 \'{me}\'" > /dev/null 2>&1 &'
        script = 'do shell script "' + cmd.replace('\\', '\\\\').replace('"', '\\"') + '"'
        subprocess.run(['osacompile', '-o', app, '-e', script], check=True)
        pb = f'{app}/Contents/Info.plist'
        subprocess.run(['/usr/libexec/PlistBuddy', '-c', f'Add :CFBundleIdentifier string {LABEL}-app', pb], capture_output=True)
        subprocess.run(['/usr/libexec/PlistBuddy', '-c', f'Set :CFBundleName {name}', pb], capture_output=True)
        subprocess.run(['codesign', '--force', '-s', '-', app], capture_output=True)
        import plistlib
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        with open(PLIST, 'wb') as f:
            plistlib.dump({'Label': LABEL, 'ProgramArguments': ['/bin/zsh', '-lc', f"python3 '{me}' --서버"],
                           'RunAtLoad': False, 'StandardErrorPath': LOG}, f)
        dom = f'gui/{os.getuid()}'
        subprocess.run(['launchctl', 'bootout', f'{dom}/{LABEL}'], capture_output=True)
        subprocess.run(['launchctl', 'bootstrap', dom, PLIST], check=True)
        print(L(f'만들었습니다: {app} — Spotlight 에서 «{name}» · 서버 {PLIST}', f'Made {app} — open it from Spotlight: "{name}" · server {PLIST}'))
        return
    d, v = start_dir(a[0] if a else '')
    st = running()
    if not st:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(PLIST):
            dom = f'gui/{os.getuid()}'
            if subprocess.run(['launchctl', 'kickstart', f'{dom}/{LABEL}'], capture_output=True).returncode:
                subprocess.run(['launchctl', 'bootstrap', dom, PLIST], capture_output=True)
                subprocess.run(['launchctl', 'kickstart', f'{dom}/{LABEL}'], capture_output=True)
        elif WIN:
            py = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            subprocess.Popen([py if os.path.isfile(py) else sys.executable, os.path.abspath(__file__), '--serve'],
                             creationflags=0x00000008 | 0x00000200,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(LOG, 'a'), close_fds=True)
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), '--serve'], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(LOG, 'a'))
        for _ in range(3):
            time.sleep(1.5)
            st = running()
            if st:
                break
        if not st:
            sys.exit(L(f'페이지를 켜지 못했습니다 — 기록 {LOG}', f'Could not start the page — log: {LOG}'))
    open_page(st, d, v)
    print(L('열었습니다: ', 'Opened: ') + (d or L('(마지막 폴더)', '(last folder)')) + (f' · {os.path.basename(v)}' if v else ''))


if __name__ == '__main__':
    main()
