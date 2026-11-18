from pathlib import Path
from collections import Counter
from tempfile import TemporaryDirectory
import json, math, os, sqlite3, subprocess, uuid
from flask import Flask, abort, jsonify, request, send_from_directory
import ezdxf
from shapely.geometry import Polygon

app = Flask(__name__, static_folder='web', static_url_path='/assets')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
DATA = Path(os.environ.get('DATA_DIR', 'data')); DATA.mkdir(exist_ok=True)
def db():
    return sqlite3.connect(DATA/'plans.sqlite3')
with db() as c:
    c.execute('CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, drawing TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS labels(plan TEXT, room TEXT, label TEXT, PRIMARY KEY(plan,room))')

def extract(path, scale):
    drawing = ezdxf.readfile(path); parts = []; skipped = Counter(); total = 0
    for e in drawing.modelspace():
        typ = e.dxftype(); closed = False
        if typ == 'LINE':
            pts = [e.dxf.start, e.dxf.end]
            if abs(pts[0].z-pts[1].z)>1e-9: skipped['3D']+=1; continue
        elif typ == 'LWPOLYLINE' or (typ=='POLYLINE' and e.is_2d_polyline):
            if tuple(e.dxf.get('extrusion', (0,0,1))) != (0,0,1): skipped['tilted']+=1; continue
            if typ == 'LWPOLYLINE':
                if e.has_arc: skipped['curved']+=1; continue
                pts = e.get_points('xy'); closed = e.closed
            else:
                if any(v.dxf.get('bulge',0) for v in e.vertices) or e.dxf.flags & 6: skipped['curved']+=1; continue
                pts = [v.dxf.location for v in e.vertices]; closed = e.is_closed
        else: skipped[typ]+=1; continue
        pts = [[float(p[0]),float(p[1])] for p in pts]; total += len(pts)
        if total>150000: raise ValueError('نقشه برای این نسخه بیش از حد بزرگ است.')
        if len(pts)<2 or not all(math.isfinite(x) for p in pts for x in p): skipped['invalid']+=1; continue
        if closed and pts[-1]==pts[0]: pts.pop()
        part = dict(id=str(len(parts)+1),layer=e.dxf.layer,points=pts,closed=bool(closed))
        if closed and len(pts)>=3:
            poly = Polygon(pts)
            if poly.is_valid and poly.area>0:
                sides=[math.dist(p,pts[(i+1)%len(pts)])*scale for i,p in enumerate(pts)]
                a=poly.representative_point(); part.update(area=poly.area*scale**2, perimeter=sum(sides), sides=sides, anchor=[a.x,a.y],label='')
            else: skipped['invalid boundary']+=1
        parts.append(part)
    if not parts: raise ValueError('هندسه دوبعدی پشتیبانی‌شده پیدا نشد.')
    return dict(parts=parts, skipped=dict(skipped))

@app.get('/')
def index(): return send_from_directory('web','index.html')
@app.post('/api/plans')
def upload():
    f=request.files.get('file'); unit=request.form.get('unit'); scales={'mm':.001,'cm':.01,'m':1.}
    if not f or unit not in scales: abort(400)
    ext=Path(f.filename or '').suffix.lower()
    if ext not in ('.dwg','.dxf'): abort(400)
    with TemporaryDirectory() as tmp:
        src=Path(tmp)/('input'+ext); f.save(src); dxf=src.with_suffix('.dxf')
        if ext=='.dwg':
            try:
                subprocess.run(['dwg2dxf','-o',str(dxf),str(src)],check=True,timeout=45,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except (OSError,subprocess.SubprocessError): return 'تبدیل DWG انجام نشد؛ نصب مبدل و نسخه فایل را بررسی کنید.',422
        try: plan=extract(dxf,scales[unit])
        except ValueError as err: return str(err)[:200],422
        except Exception: return 'فایل DXF قابل خواندن نیست.',422
    key=uuid.uuid4().hex; plan.update(id=key,unit=unit)
    with db() as c: c.execute('INSERT INTO plans VALUES(?,?)',(key,json.dumps(plan)))
    return jsonify(plan),201

def load(key):
    with db() as c:
        row=c.execute('SELECT drawing FROM plans WHERE id=?',(key,)).fetchone()
        if not row: abort(404)
        plan=json.loads(row[0]); labels=dict(c.execute('SELECT room,label FROM labels WHERE plan=?',(key,)))
    for p in plan['parts']:
        if p['id'] in labels: p['label']=labels[p['id']]
    return plan
@app.get('/api/plans/<key>')
def get_plan(key): return jsonify(load(key))
@app.put('/api/plans/<key>/rooms/<room>')
def save(key,room):
    label=(request.get_json() or {}).get('label'); plan=load(key)
    if not isinstance(label,str) or len(label)>120: abort(400)
    if not any(p['id']==room and 'area' in p for p in plan['parts']): abort(404)
    with db() as c: c.execute('INSERT OR REPLACE INTO labels VALUES(?,?,?)',(key,room,label.strip()))
    return jsonify(label=label.strip())
