// HullParam web UI  (three.js r160, ES module, no build step)
import * as THREE from 'three';
import { OrbitControls } from '/web/vendor/OrbitControls.js';

// ------------------------------------------------------------------ utils
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clone = o => JSON.parse(JSON.stringify(o ?? null));
const fmt = (v, d = 1) => (typeof v === 'number' && isFinite(v)) ? (+v.toFixed(d)).toString() : String(v ?? '');

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (e) { j = { ok: false, error: `서버 응답 오류 (${r.status})` }; }
  if (!r.ok && j && j.ok === undefined) j.ok = false;
  return j;
}
function b64f32(s) { const b = atob(s); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return new Float32Array(u.buffer); }
function b64u32(s) { const b = atob(s); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return new Uint32Array(u.buffer); }

// ------------------------------------------------------------------ state
const S = {
  state: null, W: null, yaml: '', dirty: false, mesh: null, meshVer: -1,
  objs: new Map(),          // name -> {obj, member, visible}
  selected: null, tab: 'layout', mtab: 'check', polling: false, lastJob: null,
  preview: [], extentX: [0, 1],
};

const CAT = {
  hull: { label: '외판 (Hull)', color: 0xc8ccd4 },
  bulkhead: { label: '격벽 (Bulkhead)', color: 0xd9734d },
  frame: { label: '늑골 (Web frame)', color: 0xf2b33d },
  girder: { label: '거더 (Girder)', color: 0x5a9bd8 },
  plate: { label: '수평판 (Plate)', color: 0x8cbf72 },
  fixed: { label: '고정 부재', color: 0x999999 },
  stringer_bottom: { label: '선저 Stringer', color: 0xc0392b },
  stringer_side: { label: '측면 Stringer', color: 0x8e44ad },
  stringer_deck: { label: '갑판 Stringer', color: 0x16a085 },
  frame_flange: { label: '늑골 플랜지', color: 0xd35400 },
  girder_flange: { label: '거더 플랜지', color: 0x2c3e90 },
  bulkhead_stiffener: { label: '격벽 보강재', color: 0x7b241c },
  transom_stiffener: { label: '트랜섬 보강재', color: 0x6c3483 },
  centerline: { label: '중심선 보강재', color: 0x111111 },
};
const SHELL_ORDER = ['hull', 'bulkhead', 'frame', 'girder', 'plate', 'fixed'];
const BEAM_ORDER = ['stringer_bottom', 'stringer_side', 'stringer_deck', 'frame_flange', 'girder_flange', 'bulkhead_stiffener', 'transom_stiffener', 'centerline'];

// ------------------------------------------------------------------ 3D
const vp = $('#viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.localClippingEnabled = true;
vp.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 10, 1e6);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.15;
controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); camera.add(sun); sun.position.set(0.3, 0.6, 1); scene.add(camera);
const root = new THREE.Group(); scene.add(root);
const outlineGroup = new THREE.Group(); scene.add(outlineGroup);
const issueGroup = new THREE.Group(); scene.add(issueGroup);
const previewGroup = new THREE.Group(); scene.add(previewGroup);
const clipPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
let clipOn = false;

// triad
const triScene = new THREE.Scene();
const triCam = new THREE.OrthographicCamera(-1.6, 1.6, 1.6, -1.6, 0.1, 10); triCam.up.set(0, 0, 1);
(function buildTriad() {
  const mk = (dir, col, lab) => {
    const a = new THREE.ArrowHelper(dir, new THREE.Vector3(), 1.0, col, 0.25, 0.14); triScene.add(a);
    const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
    g.fillStyle = '#' + col.toString(16).padStart(6, '0'); g.font = 'bold 44px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(lab, 32, 34);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
    sp.position.copy(dir.clone().multiplyScalar(1.35)); sp.scale.set(0.5, 0.5, 0.5); triScene.add(sp);
  };
  mk(new THREE.Vector3(1, 0, 0), 0xd62728, 'X'); mk(new THREE.Vector3(0, 1, 0), 0x2ca02c, 'Y'); mk(new THREE.Vector3(0, 0, 1), 0x1f77b4, 'Z');
})();

function resize() {
  const w = vp.clientWidth, h = vp.clientHeight;
  renderer.setSize(w, h); camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(vp);
function loop() {
  requestAnimationFrame(loop);
  controls.update();
  const w = vp.clientWidth, h = vp.clientHeight;
  renderer.setViewport(0, 0, w, h); renderer.setScissorTest(false);
  renderer.clippingPlanes = clipOn ? [clipPlane] : [];
  renderer.render(scene, camera);
  // triad bottom-left
  const s = 110;
  renderer.clippingPlanes = [];
  renderer.setScissorTest(true); renderer.setScissor(6, 6, s, s); renderer.setViewport(6, 6, s, s);
  const d = new THREE.Vector3().subVectors(camera.position, controls.target).normalize().multiplyScalar(4);
  triCam.position.copy(d); triCam.up.copy(camera.up); triCam.lookAt(0, 0, 0);
  renderer.autoClear = false; renderer.clearDepth(); renderer.render(triScene, triCam); renderer.autoClear = true;
  renderer.setScissorTest(false);
}
loop();

function bboxOfScene() {
  const b = new THREE.Box3();
  root.children.forEach(o => { if (o.visible) b.expandByObject(o); });
  if (b.isEmpty() && S.mesh && S.mesh.bbox) { const a = S.mesh.bbox; b.set(new THREE.Vector3(a[0], a[1], a[2]), new THREE.Vector3(a[3], a[4], a[5])); }
  return b;
}
function setView(kind) {
  const b = bboxOfScene(); if (b.isEmpty()) return;
  const c = b.getCenter(new THREE.Vector3()), r = b.getSize(new THREE.Vector3()).length() / 2;
  const dirs = { iso: [-1.0, -1.25, 0.8], '+x': [1, 0, 0.0001], '-x': [-1, 0, 0.0001], '-y': [0, -1, 0.0001], '+y': [0, 1, 0.0001], '+z': [0.0001, -0.0001, 1], '-z': [0.0001, 0.0001, -1] };
  const d = kind === 'fit' ? new THREE.Vector3().subVectors(camera.position, controls.target).normalize()
    : new THREE.Vector3(...(dirs[kind] || dirs.iso)).normalize();
  if (!isFinite(d.x) || d.lengthSq() === 0) d.set(...dirs.iso).normalize();
  const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
  controls.target.copy(c); camera.position.copy(c.clone().add(d.multiplyScalar(dist)));
  camera.near = dist / 200; camera.far = dist * 20; camera.updateProjectionMatrix(); controls.update();
}
function focusPoint(p, member) {
  const b = bboxOfScene(); const r = b.getSize(new THREE.Vector3()).length() / 10 || 1000;
  const v = new THREE.Vector3(...p);
  const d = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
  controls.target.copy(v); camera.position.copy(v.clone().add(d.multiplyScalar(r * 2.2)));
  if (member && S.objs.has(member)) select(member, false);
  flash(v, r * 0.06);
}
function flash(v, rad) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(rad, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff00ff, transparent: true, opacity: 0.8, depthTest: false }));
  m.position.copy(v); scene.add(m);
  let t = 0; const iv = setInterval(() => { t++; m.material.opacity = 0.8 * (1 - t / 30); if (t >= 30) { clearInterval(iv); scene.remove(m); } }, 50);
}

// ------------------------------------------------------------------ build scene from mesh
function hullOpacity() { return 1 - (+$('#rngHull').value) / 100; }
function colorFor(m) {
  if ($('#selColor').value === 'thk') return thkColor(m);
  return (CAT[m.category] || { color: 0x888888 }).color;
}
let thkRange = [0, 1], secRange = [0, 1];
function secArea(sec) {
  if (!sec) return 0;
  sec = normSec(sec);
  if (sec.shape === 'FLAT') return sec.height * sec.thickness;
  return (sec.height - sec.t_flange) * sec.t_web + sec.width * sec.t_flange;
}
function ramp(t) { // blue -> cyan -> green -> yellow -> red
  t = Math.min(1, Math.max(0, t));
  const stops = [[0.12, 0.3, 0.85], [0.1, 0.75, 0.85], [0.3, 0.75, 0.3], [0.95, 0.85, 0.2], [0.85, 0.2, 0.15]];
  const k = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(k)), f = k - i;
  const c = stops[i].map((a, j) => a + (stops[i + 1][j] - a) * f);
  return new THREE.Color(c[0], c[1], c[2]).getHex();
}
function thkColor(m) {
  if (m.kind === 'shell') { const t = m.props.thickness; return t ? ramp((t - thkRange[0]) / Math.max(1e-9, thkRange[1] - thkRange[0])) : 0x999999; }
  const a = secArea(m.props.section); return ramp((a - secRange[0]) / Math.max(1e-9, secRange[1] - secRange[0]));
}
function updateLegend() {
  const lg = $('#legend');
  if ($('#selColor').value !== 'thk' || !S.mesh) { lg.hidden = true; return; }
  const grad = [0, .25, .5, .75, 1].map(t => '#' + ramp(t).toString(16).padStart(6, '0')).join(',');
  lg.innerHTML = `<b>쉘 두께 [mm]</b><div class="bar" style="background:linear-gradient(90deg,${grad})"></div><div style="display:flex;justify-content:space-between"><span>${fmt(thkRange[0], 2)}</span><span>${fmt(thkRange[1], 2)}</span></div>
  <b>빔 단면적 [mm²]</b><div class="bar" style="background:linear-gradient(90deg,${grad})"></div><div style="display:flex;justify-content:space-between"><span>${fmt(secRange[0], 0)}</span><span>${fmt(secRange[1], 0)}</span></div>`;
  lg.hidden = false;
}

function clearScene() {
  for (const g of [root, outlineGroup, issueGroup]) {
    while (g.children.length) { const o = g.children.pop(); o.geometry?.dispose(); o.material?.dispose?.(); }
  }
  S.objs.clear();
}
function applyMaterial(entry) {
  const m = entry.member, o = entry.obj, sel = S.selected === m.name;
  if (m.kind === 'shell') {
    const op = m.category === 'hull' ? hullOpacity() : 1;
    o.material.color.setHex(sel ? 0x2f86ea : colorFor(m));
    o.material.emissive.setHex(sel ? 0x0b2f66 : 0x000000);
    o.material.opacity = sel ? Math.max(op, 0.85) : op;
    o.material.transparent = o.material.opacity < 0.999;
    o.material.depthWrite = !o.material.transparent;
    o.renderOrder = o.material.transparent ? 2 : 0;
  } else {
    o.material.color.setHex(sel ? 0x00b7ff : colorFor(m));
  }
}
function buildScene(mesh) {
  clearScene();
  S.mesh = mesh;
  const ths = mesh.members.filter(m => m.kind === 'shell' && m.props.thickness).map(m => m.props.thickness);
  thkRange = ths.length ? [Math.min(...ths), Math.max(...ths)] : [0, 1];
  const sas = mesh.members.filter(m => m.kind === 'beam').map(m => secArea(m.props.section));
  secRange = sas.length ? [Math.min(...sas), Math.max(...sas)] : [0, 1];
  for (const m of mesh.members) {
    let obj;
    if (m.kind === 'shell') {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(b64f32(m.v), 3));
      g.setIndex(new THREE.BufferAttribute(b64u32(m.t), 1));
      g.computeVertexNormals();
      const mat = new THREE.MeshPhongMaterial({ side: THREE.DoubleSide, shininess: 20, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      obj = new THREE.Mesh(g, mat);
    } else {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(b64f32(m.l), 3));
      obj = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xff0000 }));
      obj.renderOrder = 3;
    }
    obj.userData.name = m.name;
    root.add(obj);
    const e = { obj, member: m, visible: true };
    S.objs.set(m.name, e); applyMaterial(e);
  }
  if (mesh.outline) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(b64f32(mesh.outline), 3));
    outlineGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0x1b2533, transparent: true, opacity: 0.35 })));
  }
  buildIssues(mesh.issues || []);
  const a = mesh.bbox; S.extentX = [a[0], a[3]];
  updateClip(); updateVisibility(); updateLegend();
  $('#empty').hidden = mesh.members.length > 0;
  buildTree();
}
function buildIssues(list) {
  while (issueGroup.children.length) issueGroup.children.pop();
  const b = S.mesh?.bbox; const r = b ? Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) / 250 : 20;
  for (const i of list) {
    if (!i.location || i.level === 'INFO') continue;
    const col = i.level === 'ERROR' ? 0xd32f2f : 0xf0a000;
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8), new THREE.MeshBasicMaterial({ color: col, depthTest: false, transparent: true, opacity: 0.85 }));
    m.position.set(...i.location); m.renderOrder = 5; issueGroup.add(m);
  }
}
function updateVisibility() {
  const beamsOn = $('#chkBeams').checked;
  for (const e of S.objs.values()) e.obj.visible = e.visible && (e.member.kind === 'shell' || beamsOn);
  outlineGroup.visible = $('#chkEdges').checked;
  issueGroup.visible = $('#chkIssues').checked;
  previewGroup.visible = $('#chkPreview').checked;
}
function updateClip() {
  const on = $('#chkClip').checked; clipOn = on; $('#rngClip').disabled = !on;
  const [a, b] = S.extentX; const x = a + (b - a) * (+$('#rngClip').value) / 1000;
  clipPlane.constant = x; $('#clipVal').textContent = on ? `x ≤ ${fmt(x, 0)} mm` : '';
}

// ------------------------------------------------------------------ station preview (before generation)
function drawPreview(stations) {
  while (previewGroup.children.length) previewGroup.children.pop();
  S.preview = stations || [];
  const b = S.mesh?.bbox; if (!b || !stations) return;
  const ymin = b[1], ymax = b[4], zmin = b[2], zmax = b[5];
  for (const s of stations) {
    const isB = s.name.startsWith('BH');
    const g = new THREE.PlaneGeometry(ymax - ymin, zmax - zmin);
    const col = s.sel ? 0x1f6fd1 : (isB ? 0xe0603a : 0xf2b33d);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: s.sel ? 0.4 : 0.18, side: THREE.DoubleSide, depthWrite: false }));
    m.rotation.y = Math.PI / 2; m.position.set(s.x, (ymin + ymax) / 2, (zmin + zmax) / 2);
    previewGroup.add(m);
    const eg = new THREE.EdgesGeometry(g); const l = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: s.sel ? 0x1f6fd1 : (isB ? 0xc0392b : 0xc98a00) }));
    l.rotation.copy(m.rotation); l.position.copy(m.position); previewGroup.add(l);
  }
  updateVisibility();
}

// ------------------------------------------------------------------ picking
const ray = new THREE.Raycaster();
function pick(ev) {
  const r = renderer.domElement.getBoundingClientRect();
  const v = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(v, camera);
  const b = bboxOfScene(); ray.params.Line.threshold = b.getSize(new THREE.Vector3()).length() / 400;
  const vis = root.children.filter(o => o.visible);
  let hits = ray.intersectObjects(vis, false);
  if (clipOn) hits = hits.filter(h => h.point.x <= clipPlane.constant + 1e-6);
  // prefer non-hull / opaque members when the hull is transparent
  if (hullOpacity() < 0.6) { const nh = hits.filter(h => S.objs.get(h.object.userData.name)?.member.category !== 'hull'); if (nh.length) hits = nh; }
  return hits[0];
}
let downAt = null;
renderer.domElement.addEventListener('pointerdown', e => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', e => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4 || e.button !== 0) return;
  const h = pick(e); if (h) select(h.object.userData.name, true); else select(null);
});
renderer.domElement.addEventListener('pointermove', e => {
  if (e.buttons) { $('#hover').hidden = true; return; }
  const h = pick(e); const hv = $('#hover');
  if (h) { const r = vp.getBoundingClientRect(); hv.textContent = h.object.userData.name; hv.style.left = (e.clientX - r.left + 14) + 'px'; hv.style.top = (e.clientY - r.top + 10) + 'px'; hv.hidden = false; }
  else hv.hidden = true;
});

// ------------------------------------------------------------------ tree + details
function buildTree() {
  const tree = $('#tree'); tree.innerHTML = '';
  if (!S.mesh || !S.mesh.members.length) { tree.innerHTML = '<p class="muted">모델이 없습니다.</p>'; return; }
  const groups = {};
  for (const m of S.mesh.members) (groups[m.category] ||= []).push(m);
  const top = (title, cats) => {
    const items = cats.filter(c => groups[c]);
    if (!items.length) return;
    const n = items.reduce((a, c) => a + groups[c].length, 0);
    const node = treeNode(title, n, null, items.flatMap(c => groups[c].map(m => m.name)));
    for (const c of items) {
      const sub = treeNode(CAT[c]?.label || c, groups[c].length, CAT[c]?.color, groups[c].map(m => m.name));
      for (const m of groups[c]) sub.kids.appendChild(treeLeaf(m));
      node.kids.appendChild(sub.el);
    }
    tree.appendChild(node.el);
  };
  top('쉘 (Shell bodies)', SHELL_ORDER);
  top('빔 (Line bodies)', BEAM_ORDER);
  if (S.mesh.preview) tree.insertAdjacentHTML('afterbegin', '<p class="muted">외판 미리보기 — 생성(▶) 후 전체 부재가 표시됩니다.</p>');
}
function treeNode(title, count, color, names) {
  const el = document.createElement('div'); el.className = 'tnode';
  const row = document.createElement('div'); row.className = 'trow';
  row.innerHTML = `<span class="tw">▾</span><input type="checkbox" checked>${color != null ? `<span class="sw" style="background:#${color.toString(16).padStart(6, '0')}"></span>` : ''}<span>${esc(title)}</span> <span class="tcount">(${count})</span>`;
  const kids = document.createElement('div'); kids.className = 'tchildren';
  row.querySelector('.tw').onclick = () => { kids.hidden = !kids.hidden; row.querySelector('.tw').textContent = kids.hidden ? '▸' : '▾'; };
  row.querySelector('input').onchange = ev => {
    for (const n of names) { const e = S.objs.get(n); if (e) e.visible = ev.target.checked; }
    $$('input', kids).forEach(i => i.checked = ev.target.checked);
    updateVisibility();
  };
  el.append(row, kids);
  return { el, kids };
}
function treeLeaf(m) {
  const row = document.createElement('div'); row.className = 'trow'; row.dataset.name = m.name;
  const extra = m.kind === 'shell' ? (m.props.thickness ? ` t=${fmt(m.props.thickness, 2)}` : '') : '';
  row.innerHTML = `<span class="tw"></span><input type="checkbox" checked><span>${esc(m.name)}</span><span class="tcount">${esc(extra)}</span>`;
  row.querySelector('input').onchange = ev => { const e = S.objs.get(m.name); if (e) { e.visible = ev.target.checked; updateVisibility(); } };
  row.onclick = ev => { if (ev.target.tagName !== 'INPUT') select(m.name, false); };
  row.ondblclick = () => zoomTo(m.name);
  return row;
}
function zoomTo(name) {
  const e = S.objs.get(name); if (!e) return;
  const b = new THREE.Box3().setFromObject(e.obj); if (b.isEmpty()) return;
  const c = b.getCenter(new THREE.Vector3()), r = Math.max(b.getSize(new THREE.Vector3()).length() / 2, 200);
  const d = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
  controls.target.copy(c); camera.position.copy(c.clone().add(d.multiplyScalar(r * 2.6)));
}
function select(name, scrollTree) {
  const prev = S.selected; S.selected = name;
  if (prev && S.objs.has(prev)) applyMaterial(S.objs.get(prev));
  if (name && S.objs.has(name)) applyMaterial(S.objs.get(name));
  $$('#tree .trow.sel').forEach(r => r.classList.remove('sel'));
  if (name) {
    const row = $(`#tree .trow[data-name="${CSS.escape(name)}"]`);
    if (row) { row.classList.add('sel'); if (scrollTree) row.scrollIntoView({ block: 'nearest' }); }
  }
  showDetails(name ? S.objs.get(name)?.member : null);
}
function secProps(sec) {
  if (!sec) return {};
  sec = normSec(sec); const s = sec.shape;
  if (s === 'FLAT') { const h = sec.height, t = sec.thickness; return { A: h * t, Iyy: t * h ** 3 / 12, Izz: h * t ** 3 / 12 }; }
  const h = sec.height, b = sec.width, tw = sec.t_web, tf = sec.t_flange, hw = h - tf, Aw = hw * tw, Af = b * tf, A = Aw + Af;
  const zc = (Aw * hw / 2 + Af * (hw + tf / 2)) / A;
  const yw = s === 'L' ? tw / 2 : 0, yf = s === 'L' ? b / 2 : 0, yc = (Aw * yw + Af * yf) / A;
  return { A, Iyy: tw * hw ** 3 / 12 + Aw * (hw / 2 - zc) ** 2 + b * tf ** 3 / 12 + Af * (hw + tf / 2 - zc) ** 2,
    Izz: hw * tw ** 3 / 12 + Aw * (yw - yc) ** 2 + tf * b ** 3 / 12 + Af * (yf - yc) ** 2, zc };
}
function showDetails(m) {
  const d = $('#details');
  if (!m) { d.innerHTML = '<p class="muted">부재를 선택하세요 (트리 또는 3D 화면 클릭, 더블클릭: 확대).</p>'; return; }
  const p = m.props, rows = [['이름', m.name], ['종류', m.kind === 'shell' ? '쉘 (Surface body)' : '빔 (Line body)'], ['분류', CAT[m.category]?.label || m.category]];
  if (m.kind === 'shell') {
    if (p.thickness != null) rows.push(['두께', fmt(p.thickness, 3) + ' mm']);
    if (p.area_m2 != null) rows.push(['면적', fmt(p.area_m2, 4) + ' m²']);
    if (p.faces != null) rows.push(['면 개수', p.faces]);
    for (const k of ['x', 'y', 'z']) if (p[k] != null) rows.push([k + ' 위치', fmt(p[k], 3) + ' mm']);
    if (p.web_depth != null) rows.push(['웨브 깊이', fmt(p.web_depth, 1) + ' mm'], ['갑판보 깊이', fmt(p.deck_beam_depth, 1) + ' mm'], ['모서리 반경', fmt(p.corner_radius, 1) + ' mm']);
    if (p.skin_class) rows.push(['외판 구분', p.skin_class]);
  } else {
    const s = normSec(p.section);
    rows.push(['단면 형상', { T: 'T형 (T-bar)', L: '앵글 (L)', FLAT: '평강 (Flat bar)' }[s.shape] || s.shape]);
    if (s.shape !== 'FLAT') rows.push(['높이 h', fmt(s.height, 2) + ' mm'], ['플랜지 폭 b', fmt(s.width, 2) + ' mm'], ['웨브 두께 tw', fmt(s.t_web, 2) + ' mm'], ['플랜지 두께 tf', fmt(s.t_flange, 2) + ' mm']);
    else rows.push(['높이 h', fmt(s.height, 2) + ' mm'], ['두께 t', fmt(s.thickness, 2) + ' mm']);
    const sp = secProps(s);
    if (sp.A) rows.push(['단면적 A', fmt(sp.A, 1) + ' mm²'], ['Iyy', fmt(sp.Iyy, 0) + ' mm⁴'], ['Izz', fmt(sp.Izz, 0) + ' mm⁴']);
    if (p.length_m != null) rows.push(['길이', fmt(p.length_m, 3) + ' m']);
    if (p.edges != null) rows.push(['선 개수', p.edges]);
    if (p.host) rows.push(['부착 위치', p.host]);
    for (const k of ['x', 'y', 'z']) if (p[k] != null) rows.push([k + ' 위치', fmt(p[k], 3) + ' mm']);
  }
  d.innerHTML = '<table class="kv">' + rows.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join('') + '</table>' +
    `<p><button class="mini" id="btnZoomSel">확대</button> <button class="mini" id="btnHideSel">숨기기</button></p>`;
  $('#btnZoomSel').onclick = () => zoomTo(m.name);
  $('#btnHideSel').onclick = () => { const e = S.objs.get(m.name); e.visible = false; updateVisibility(); const r = $(`#tree .trow[data-name="${CSS.escape(m.name)}"] input`); if (r) r.checked = false; };
}

// ------------------------------------------------------------------ parameter form
function getP(path) { return path.reduce((o, k) => (o == null ? undefined : o[k]), S.W); }
function setP(path, v) {
  let o = S.W; for (let i = 0; i < path.length - 1; i++) { if (o[path[i]] == null) o[path[i]] = {}; o = o[path[i]]; }
  o[path[path.length - 1]] = v; markDirty();
}
function markDirty() { S.dirty = true; $('#dirty').hidden = false; }
const parseNum = s => { const v = Number(String(s).trim()); return String(s).trim() === '' || !isFinite(v) ? null : v; };
function parseList(s) { const t = String(s).split(/[,\s;]+/).filter(Boolean); const v = t.map(Number); return v.some(x => !isFinite(x)) ? null : v; }
function numTabToText(v) { if (v == null) return ''; if (typeof v === 'number') return fmt(v, 4); if (v.x) return v.x.map((x, i) => `${fmt(x, 1)}:${fmt(v.value[i], 3)}`).join(', '); return JSON.stringify(v); }
function parseNumTab(s) {
  s = String(s).trim(); if (!s) return null;
  if (!s.includes(':')) return parseNum(s);
  const x = [], value = [];
  for (const part of s.split(',')) { const [a, b] = part.split(':').map(t => Number(t.trim())); if (!isFinite(a) || !isFinite(b)) return undefined; x.push(a); value.push(b); }
  return { x, value };
}
function refToText(v) { return v == null ? '' : typeof v === 'number' ? fmt(v, 2) : String(v); }
function parseRef(s) { s = String(s).trim(); const n = Number(s); return s !== '' && isFinite(n) ? n : s; }

function frow(label, input, hint) {
  const r = document.createElement('div'); r.className = 'frow';
  const l = document.createElement('label'); l.textContent = label; r.append(l, input);
  if (hint) { const h = document.createElement('div'); h.className = 'hint'; h.textContent = hint; r.append(h); }
  return r;
}
function inp(type, value, onchange, attrs = {}) {
  const i = document.createElement('input'); i.type = type === 'bool' ? 'checkbox' : (type === 'num' || type === 'int') ? 'number' : 'text';
  if (type === 'num') i.step = 'any'; if (type === 'int') i.step = '1';
  if (type === 'bool') i.checked = !!value; else i.value = value ?? '';
  Object.assign(i, attrs);
  i.onchange = () => {
    const row = i.closest('.frow');
    const ok = onchange(type === 'bool' ? i.checked : i.value);
    if (row) row.classList.toggle('err', ok === false);
  };
  return i;
}
function F(label, path, type, hint, extra = {}) {
  const v = getP(path);
  if (type === 'select') {
    const s = document.createElement('select');
    for (const [k, t] of extra.options) { const o = document.createElement('option'); o.value = k; o.textContent = t; s.appendChild(o); }
    s.value = v ?? extra.options[0][0];
    s.onchange = () => { setP(path, s.value); if (extra.rerender) renderForm(); };
    return frow(label, s, hint);
  }
  const conv = {
    num: [x => fmt(x, 4), s => parseNum(s)], int: [x => x ?? '', s => { const n = parseNum(s); return n == null || !Number.isInteger(n) ? null : n; }],
    bool: [x => x, s => s], text: [x => x ?? '', s => s], list: [x => (x || []).map(a => fmt(a, 2)).join(', '), s => parseList(s)],
    intlist: [x => (x || []).join(', '), s => { const l = parseList(s); return l && l.every(Number.isInteger) ? l : null; }],
    numtab: [numTabToText, parseNumTab], ref: [refToText, parseRef],
    numlistopt: [x => Array.isArray(x) ? x.map(a => fmt(a, 3)).join(', ') : fmt(x, 3), s => { const l = parseList(s); return !l ? null : l.length === 1 ? l[0] : l; }],
  }[type];
  const i = inp(type === 'numtab' || type === 'ref' || type === 'list' || type === 'intlist' || type === 'numlistopt' ? 'text' : type, conv[0](v), s => {
    const val = conv[1](s); if (val === null || val === undefined) return false; setP(path, val); if (extra.rerender) renderForm(); return true;
  });
  if (type === 'numtab') i.title = '숫자 하나 또는  x:값, x:값, ...  (x 위치별 선형보간)';
  return frow(label, i, hint);
}
const SEC_TYPES = [['T', 'T형'], ['L', '앵글 (L)'], ['FLAT', '평강 (Flat)']];
const SEC_FIELDS = { T: [['height', '높이 h'], ['width', '플랜지 폭 b'], ['t_web', '웨브 두께 tw'], ['t_flange', '플랜지 두께 tf']],
  L: [['height', '높이 h'], ['width', '플랜지 폭 b'], ['t_web', '웨브 두께 tw'], ['t_flange', '플랜지 두께 tf']],
  FLAT: [['height', '높이 h'], ['thickness', '두께 t']] };
function normSec(s) {
  if (!s || typeof s !== 'object') return { shape: 'T', height: 60, width: 50, t_web: 6, t_flange: 6 };
  let sh = String(s.shape || 'T').toUpperCase(); if (sh === 'ANGLE') sh = 'L';
  if (sh === 'RECT' || (sh === 'FLAT' && s.thickness == null)) { const h = +s.height || 100, w = +s.width || 10; return { shape: 'FLAT', height: Math.max(h, w), thickness: Math.min(h, w) }; }
  if (sh === 'FLAT') return { shape: 'FLAT', height: s.height, thickness: s.thickness };
  return { shape: sh, height: s.height, width: s.width, t_web: s.t_web, t_flange: s.t_flange };
}
function convertSec(s, shape) {
  s = normSec(s);
  if (shape === 'FLAT') return { shape: 'FLAT', height: s.height || 100, thickness: s.thickness || s.t_web || 10 };
  return { shape, height: s.height || 60, width: s.width || Math.round((s.height || 60) * 0.8), t_web: s.t_web || s.thickness || 6, t_flange: s.t_flange || s.thickness || 6 };
}
function secLabel(s) { s = normSec(s); return s.shape === 'FLAT' ? `Flat ${fmt(s.height, 1)}×${fmt(s.thickness, 1)}` : `${s.shape} ${fmt(s.height, 1)}×${fmt(s.width, 1)}×${fmt(s.t_web, 1)}/${fmt(s.t_flange, 1)}`; }
// inline section editor bound to get/set callbacks (used in tables and boxes)
function secInline(get, set, opts = {}) {
  const d = document.createElement('div'); d.className = 'secrow';
  const sec = normSec(get());
  const sel = document.createElement('select');
  for (const [k, t] of SEC_TYPES) { const o = document.createElement('option'); o.value = k; o.textContent = t; sel.append(o); }
  sel.value = sec.shape;
  sel.onchange = () => { set(convertSec(get(), sel.value)); markDirty(); (opts.rerender || renderForm)(); };
  d.append(sel);
  for (const [k, lab] of SEC_FIELDS[sec.shape]) {
    const i = document.createElement('input'); i.type = 'number'; i.step = 'any'; i.value = fmt(sec[k], 2); i.title = lab + ' [mm]'; i.placeholder = lab.split(' ').pop();
    i.onchange = () => { const n = parseNum(i.value); if (!(n > 0)) { i.style.background = '#fff0f0'; return; } i.style.background = ''; const c = normSec(get()); c[k] = n; set(c); markDirty(); if (opts.onchange) opts.onchange(); };
    const w = document.createElement('label'); w.className = 'muted sm'; w.append(lab.split(' ').pop(), i); d.append(w);
  }
  return d;
}
function sectionEditor(path, title) {
  const box = document.createElement('div'); box.className = 'item';
  box.innerHTML = `<div class="item-h">${esc(title)}</div>`;
  const v = getP(path);
  if (Array.isArray(v)) {
    const p = document.createElement('p'); p.className = 'muted'; p.textContent = `부재별 단면 ${v.length}개가 지정되어 있습니다.`;
    const b = document.createElement('button'); b.className = 'mini'; b.textContent = '첫 번째 단면으로 통일';
    b.onclick = () => { setP(path, normSec(v[0])); renderForm(); };
    box.append(p, b); return box;
  }
  if (!v) setP(path, normSec(null));
  box.append(secInline(() => getP(path), x => setP(path, x)));
  return box;
}
function fs(title, ...children) { const f = document.createElement('fieldset'); const l = document.createElement('legend'); l.textContent = title; f.append(l, ...children.filter(Boolean)); return f; }
function listEditor(path, title, itemFn, newItem) {
  const wrap = document.createElement('div');
  const arr = getP(path) || [];
  arr.forEach((it, i) => {
    const box = document.createElement('div'); box.className = 'item';
    const h = document.createElement('div'); h.className = 'item-h'; h.textContent = `${title} ${i + 1}${it.name ? ' – ' + it.name : ''}`;
    const del = document.createElement('button'); del.className = 'mini'; del.textContent = '삭제';
    del.onclick = () => { arr.splice(i, 1); setP(path, arr); renderForm(); };
    h.append(del); box.append(h, ...itemFn([...path, i], it)); wrap.append(box);
  });
  const add = document.createElement('button'); add.className = 'mini'; add.textContent = `+ ${title} 추가`;
  add.onclick = () => { const a = getP(path) || []; a.push(newItem()); setP(path, a); renderForm(); };
  wrap.append(add); return wrap;
}
function seriesFields(path, coordLabel, girderSkip) {
  const c = getP(path) || {};
  const out = [F('입력 방식', [...path, 'mode'], 'select', null, { options: [['list', '위치 직접 입력'], ['spacing', '개수 + 간격']], rerender: true })];
  if ((c.mode || 'list') === 'list') out.push(F(`${coordLabel} 목록 [mm]`, [...path, 'positions'], 'list', '쉼표로 구분, 한쪽만 (대칭 생성)'));
  else {
    out.push(F('시작 위치 [mm]', [...path, 'start'], 'num'), F('간격 [mm]', [...path, 'spacing'], 'num'), F('개수 (한쪽)', [...path, 'count'], 'int'));
    if (girderSkip) out.push(F('거더 근처 생략 거리 [mm]', [...path, 'skip_near_girders'], 'num', '거더와 이 거리 이내인 위치는 만들지 않음'));
  }
  return out;
}
function stiffGroups(path, what) {
  const st = getP(path) || {}; if (!st.vertical) st.vertical = []; if (!st.horizontal) st.horizontal = [];
  const v = listEditor([...path, 'vertical'], `수직 보강재 그룹`, (p) => [
    ...seriesFields(p, '|y| 위치', false),
    F('하단 z [mm] (hull=선체까지)', [...p, 'z_from'], 'ref'), F('상단 z [mm] (deck=갑판까지)', [...p, 'z_to'], 'ref'),
    F('좌우 대칭', [...p, 'symmetric'], 'bool'), sectionEditor([...p, 'section'], '단면')],
    () => ({ mode: 'spacing', positions: [], start: 0, spacing: 300, count: 5, z_from: 'hull', z_to: 'deck', symmetric: true, section: { shape: 'T', height: 60, width: 50, t_web: 6, t_flange: 6 } }));
  const h = listEditor([...path, 'horizontal'], `수평 보강재`, (p) => [F('z 위치 [mm]', [...p, 'z'], 'num'), F('|y| 최대 [mm] (full=전폭)', [...p, 'y_max'], 'ref'), sectionEditor([...p, 'section'], '단면')],
    () => ({ z: 1000, y_max: 'full', section: { shape: 'T', height: 60, width: 50, t_web: 6, t_flange: 6 } }));
  return [h3(`${what} – 수직`), v, h3(`${what} – 수평`), h];
}
function h3(t) { const d = document.createElement('div'); d.style.cssText = 'font-weight:600;margin:8px 0 2px;color:#2a4a72'; d.textContent = t; return d; }

// =================================================================== helpers
function interpJS(v, x) {            // number | {x:[], value:[]}  (same rule as params.interp)
  if (v == null) return null;
  if (typeof v === 'number') return v;
  if (v.x && v.value) {
    const pr = v.x.map((a, i) => [a, v.value[i]]).sort((a, b) => a[0] - b[0]);
    if (x <= pr[0][0]) return pr[0][1];
    if (x >= pr[pr.length - 1][0]) return pr[pr.length - 1][1];
    for (let i = 1; i < pr.length; i++) if (x <= pr[i][0]) { const t = (x - pr[i - 1][0]) / (pr[i][0] - pr[i - 1][0]); return pr[i - 1][1] + t * (pr[i][1] - pr[i - 1][1]); }
  }
  return null;
}
function el(tag, attrs = {}, ...kids) {
  const e = tag === 'svg' ? document.createElementNS('http://www.w3.org/2000/svg', 'svg') : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'class') e.setAttribute('class', v); else if (k.startsWith('on')) e[k] = v; else if (k === 'html') e.innerHTML = v; else e.setAttribute(k, v); }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
}
function numInput(value, onset, attrs = {}) {
  const i = el('input', { type: 'number', step: 'any', ...attrs });
  i.value = value == null || value === '' ? '' : fmt(value, attrs.dp ?? 2);
  i.onchange = () => {
    const t = i.value.trim();
    if (t === '' && attrs.allowEmpty) { onset(null); i.style.background = ''; return; }
    const n = Number(t);
    if (!isFinite(n) || (attrs.positive && !(n > 0))) { i.style.background = '#fff0f0'; return; }
    i.style.background = ''; onset(n);
  };
  return i;
}
function btn(text, onclick, cls = 'mini', title = '') { return el('button', { class: cls, onclick, title }, text); }

// =================================================================== stations (frames / bulkheads)
function stTab() {
  S.W.stations ||= { auto: {}, list: [] };
  S.W.stations.auto ||= {};
  S.W.stations.list ||= [];
  return S.W.stations;
}
function stRows() { return stTab().list; }
function sortRows() { stRows().sort((a, b) => a.x - b.x); }
function rowNames() {      // F1.. / BH1.. in x order (same as the server)
  let f = 0, b = 0;
  return stRows().map(r => r.type === 'bulkhead' ? `BH${++b}` : `F${++f}`);
}
function inherited(r, key) {          // value used when the cell is empty
  if (r.type === 'bulkhead') {
    if (key !== 'thickness') return null;
    const t = S.W.bulkheads.thickness; return Array.isArray(t) ? t[0] : t;
  }
  return interpJS(S.W.frames[key], r.x);
}
function autoPositions(first, last, spacing) {
  if (!(last > first) || !(spacing > 0)) return null;
  const n = Math.max(1, Math.round((last - first) / spacing));
  return { n, step: (last - first) / n, xs: Array.from({ length: n + 1 }, (_, i) => first + (last - first) * i / n) };
}
function applyAutoLayout() {
  const a = stTab().auto, old = clone(stRows());
  const r = autoPositions(+a.first, +a.last, +a.spacing);
  if (!r) { toast('첫 위치 < 마지막 위치, 간격 > 0 이어야 합니다.', true); return; }
  const rows = r.xs.map(x => ({ x: +x.toFixed(2), type: 'frame' }));
  if (a.keep_bulkheads) {
    for (const b of old.filter(o => o.type === 'bulkhead')) {
      let k = 0, d = 1e18;
      rows.forEach((q, i) => { const dd = Math.abs(q.x - b.x); if (dd < d && q.type !== 'bulkhead') { d = dd; k = i; } });
      rows[k] = { ...clone(b) };
    }
  }
  stTab().list = rows; sortRows(); markDirty(); S.selRow = null; renderForm(); drawRowPreview();
  toast(`${rows.length}개 배치 (간격 ${fmt(r.step, 1)} mm)`);
}
function drawRowPreview() {
  const names = rowNames();
  drawPreview(stRows().map((r, i) => ({ name: names[i], x: r.x, sel: i === S.selRow })));
}
async function convertToTable() {
  const r = await api('/api/validate', { params: S.W });
  if (!r.ok) { msgParams(r); toast('먼저 입력 오류를 고치세요', true); return; }
  stTab().list = r.stations.map(s => s.kind === 'bulkhead' ? { x: s.x, type: 'bulkhead', thickness: s.thickness }
    : { x: s.x, type: 'frame', thickness: s.thickness, web_depth: s.web_depth, deck_beam_depth: s.deck_beam_depth, corner_radius: s.corner_radius });
  const xs = r.stations.map(s => s.x);
  Object.assign(stTab().auto, { first: Math.min(...xs), last: Math.max(...xs) });
  markDirty(); renderForm(); drawRowPreview();
}

function renderLayoutTab(box) {
  const W = S.W, h = W.hull || {};
  box.append(el('div', { class: 'infobox', html: `<b>선체</b> x ${fmt(h.x_range?.[0])} ~ ${fmt(h.x_range?.[1])} mm, 반폭 ${fmt(h.half_beam)} mm, 깊이 ${fmt(h.z_keel)} ~ ${fmt(h.z_deck)} mm` }));
  const st = stTab();
  if (!st.list.length) {
    box.append(fs('횡부재 (늑골·격벽)', el('p', {}, '이 프로젝트는 예전 형식(격벽/늑골 개수 방식)입니다. 위치별 표로 바꾸면 늑골·격벽을 하나씩 편집할 수 있습니다.'),
      btn('표 형식으로 전환', convertToTable, 'primary')));
    return;
  }
  // ---- auto layout
  const a = st.auto;
  if (a.first == null) a.first = st.list[0].x;
  if (a.last == null) a.last = st.list[st.list.length - 1].x;
  if (a.spacing == null) a.spacing = 1000;
  const calc = el('div', { class: 'calc' });
  const upd = () => { const r = autoPositions(+a.first, +a.last, +a.spacing); calc.textContent = r ? `→ 횡부재 ${r.n + 1}개, 실제 간격 ${fmt(r.step, 1)} mm` : '입력값을 확인하세요'; };
  const keep = el('input', { type: 'checkbox' }); keep.checked = a.keep_bulkheads !== false; keep.onchange = () => { a.keep_bulkheads = keep.checked; markDirty(); };
  box.append(fs('① 자동 배치 (첫 위치 ~ 마지막 위치, 목표 간격)',
    frow('첫 횡부재 x [mm]', numInput(a.first, v => { a.first = v; markDirty(); upd(); })),
    frow('마지막 횡부재 x [mm]', numInput(a.last, v => { a.last = v; markDirty(); upd(); })),
    frow('목표 간격 [mm]', numInput(a.spacing, v => { a.spacing = v; markDirty(); upd(); }, { positive: true })),
    frow('기존 격벽 유지', el('label', {}, keep, ' 원래 위치에 두고, 가장 가까운 늑골을 대체')),
    calc, el('div', { class: 'bar' }, btn('자동 배치 적용 → 아래 표 갱신', applyAutoLayout, 'primary'),
      el('span', { class: 'muted sm' }, '간격은 (마지막-첫)/개수로 균등하게 맞춰집니다'))));
  upd();
  // ---- shape preview (selected row)
  box.append(fs('② 선택한 횡부재 단면 미리보기', el('div', { id: 'secinfo', class: 'muted sm' }, S.selRow == null ? '아래 표에서 행을 클릭하세요 (빨간 선 = 늑골 개구부).' : ''),
    el('svg', { id: 'secprev', xmlns: 'http://www.w3.org/2000/svg' })));
  // ---- table
  const names = rowNames();
  const tbl = el('table', { class: 'grid' });
  tbl.append(el('tr', {}, ...['이름', '종류', 'x [mm]', '앞 간격', '두께', '웨브 깊이', '갑판보', 'R', ''].map(t => el('th', {}, t))));
  const rows = st.list;
  rows.forEach((r, i) => {
    const isB = r.type === 'bulkhead';
    const tr = el('tr', { class: (isB ? 'bh ' : '') + (i === S.selRow ? 'sel' : '') });
    const typeSel = el('select', {}, el('option', { value: 'frame' }, '늑골'), el('option', { value: 'bulkhead' }, '격벽'));
    typeSel.value = r.type || 'frame';
    typeSel.onchange = () => { r.type = typeSel.value; markDirty(); renderForm(); drawRowPreview(); };
    const xIn = numInput(r.x, v => { r.x = v; sortRows(); markDirty(); renderForm(); drawRowPreview(); }, { class: 'w2', dp: 1 });
    const gapIn = i === 0 ? el('span', { class: 'muted' }, '—') : numInput(r.x - rows[i - 1].x, v => {
      if (!(v > 0)) { toast('간격은 0보다 커야 합니다', true); return; }
      const d = rows[i - 1].x + v - r.x; for (let k = i; k < rows.length; k++) rows[k].x = +(rows[k].x + d).toFixed(3);
      markDirty(); renderForm(); drawRowPreview();
    }, { title: '수정하면 이 부재부터 뒤쪽 부재가 함께 이동합니다', dp: 1 });
    const cell = (key) => {
      if (isB && key !== 'thickness') return el('td', { class: 'muted' }, '—');
      const inh = inherited(r, key);
      const i2 = numInput(r[key], v => { if (v == null) delete r[key]; else r[key] = v; markDirty(); if (i === S.selRow) framePreview(); }, { allowEmpty: true, positive: key !== 'corner_radius', placeholder: inh != null ? fmt(inh, 1) : '' });
      i2.title = '비우면 공통값(회색) 사용';
      return el('td', {}, i2);
    };
    const acts = el('td', { class: 'acts' },
      btn('◉', () => { S.selRow = i; renderForm(); drawRowPreview(); framePreview(); }, 'mini rowbtn', '단면 미리보기 / 3D 강조'),
      btn('＋', () => { const nx = i < rows.length - 1 ? (r.x + rows[i + 1].x) / 2 : r.x + (+a.spacing || 1000); rows.splice(i + 1, 0, { x: +nx.toFixed(2), type: 'frame' }); markDirty(); renderForm(); drawRowPreview(); }, 'mini rowbtn', '뒤에 늑골 추가 (중간 위치)'),
      btn('✕', () => { rows.splice(i, 1); if (S.selRow === i) S.selRow = null; markDirty(); renderForm(); drawRowPreview(); }, 'mini rowbtn', '삭제'));
    tr.append(el('td', { class: 'nm' }, names[i]), el('td', {}, typeSel), el('td', {}, xIn), el('td', {}, gapIn),
      cell('thickness'), cell('web_depth'), cell('deck_beam_depth'), cell('corner_radius'), acts);
    tr.onclick = ev => { if (['INPUT', 'SELECT', 'BUTTON'].includes(ev.target.tagName)) return; S.selRow = i; $$('tr', tbl).forEach(t => t.classList.remove('sel')); tr.classList.add('sel'); drawRowPreview(); framePreview(); };
    tbl.append(tr);
  });
  const nF = rows.filter(r => r.type !== 'bulkhead').length, nB = rows.length - nF;
  box.append(fs(`③ 횡부재 표 — 늑골 ${nF}개, 격벽 ${nB}개`,
    el('div', { class: 'muted sm' }, '종류를 바꾸면 늑골↔격벽 전환. "앞 간격"을 고치면 그 뒤 부재들이 함께 이동합니다. 회색 값 = 공통값(비워 두면 사용). 행을 클릭하면 단면 미리보기·3D 강조.'),
    el('div', { class: 'tblwrap' }, tbl)));
  // ---- bulk edit
  const bulk = { thickness: null, web_depth: null, deck_beam_depth: null, corner_radius: null };
  const bulkRow = (lab, key) => frow(lab, numInput(null, v => { bulk[key] = v; }, { allowEmpty: true, positive: key !== 'corner_radius', placeholder: '변경 안 함' }));
  box.append(fs('④ 모든 늑골 일괄 수정',
    bulkRow('두께 [mm]', 'thickness'), bulkRow('웨브 깊이 [mm]', 'web_depth'), bulkRow('갑판보 깊이 [mm]', 'deck_beam_depth'), bulkRow('라운드 반경 R [mm]', 'corner_radius'),
    el('div', { class: 'bar' },
      btn('입력한 값을 모든 늑골에 적용', () => { let n = 0; for (const r of rows) if (r.type !== 'bulkhead') { for (const k in bulk) if (bulk[k] != null) { r[k] = bulk[k]; n++; } } markDirty(); renderForm(); toast(n ? '적용됨' : '입력한 값이 없습니다', !n); }, 'primary'),
      btn('개별값 모두 지우기 (공통값 사용)', () => { for (const r of rows) if (r.type !== 'bulkhead') for (const k in bulk) delete r[k]; markDirty(); renderForm(); })),
    frow('격벽 기본 두께 [mm]', numInput(Array.isArray(W.bulkheads.thickness) ? W.bulkheads.thickness[0] : W.bulkheads.thickness, v => { W.bulkheads.thickness = v; markDirty(); }, { positive: true })),
    el('div', { class: 'muted sm' }, '공통값(비운 칸에 쓰이는 값): 두께·웨브·갑판보·R 은 "YAML" 탭의 frames 항목 (위치별 표 보간).')));
  box.append(fs('⑤ 늑골 플랜지 (개구부 테두리 빔)', F('플랜지 생성', ['frames', 'flange', 'enabled'], 'bool'), sectionEditor(['frames', 'flange', 'section'], '플랜지 단면 (모든 늑골)')));
  if (S.selRow != null) setTimeout(framePreview, 0);
}

let fpSeq = 0;
async function framePreview() {
  const i = S.selRow, r = stRows()[i]; const svg = $('#secprev'), info = $('#secinfo');
  if (!svg || r == null) return;
  const v = k => (r[k] != null ? r[k] : inherited(r, k));
  const q = `x=${r.x}&type=${r.type || 'frame'}&web=${v('web_depth') ?? 200}&deck=${v('deck_beam_depth') ?? 100}&r=${v('corner_radius') ?? 0}`;
  const seq = ++fpSeq; info.textContent = '계산 중…';
  const d = await api('/api/frame_preview?' + q);
  if (seq !== fpSeq) return;
  if (!d.ok) { info.textContent = d.error || '미리보기 실패'; return; }
  const [y0, z0, y1, z1] = d.bounds, W = 520, H = 260, pad = 14;
  const sc = Math.min((W - 2 * pad) / (y1 - y0), (H - 2 * pad) / (z1 - z0));
  const P = ([y, z]) => `${(pad + (y - y0) * sc).toFixed(1)},${(H - pad - (z - z0) * sc).toFixed(1)}`;
  const path = rings => rings.map(rg => 'M' + rg.map(P).join('L') + 'Z').join(' ');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const isB = (r.type || 'frame') === 'bulkhead';
  svg.innerHTML = `<path d="${path(d.section)}" fill="${isB ? '#e9a184' : '#f2c66d'}" stroke="#555" stroke-width="1"/>` +
    (d.opening ? `<path d="${path(d.opening)}" fill="#fbfcfe" stroke="#c0392b" stroke-width="1.2"/>` : '');
  const nm = rowNames()[i];
  info.textContent = `${nm}  x=${fmt(r.x, 1)} mm — ` + (isB ? `격벽 (막힌 판) 면적 ${fmt(d.section_area_m2, 3)} m²` :
    `웨브 ${fmt(v('web_depth'), 1)} / 갑판보 ${fmt(v('deck_beam_depth'), 1)} / R ${fmt(v('corner_radius'), 0)} mm → 늑골 면적 ${fmt(d.frame_area_m2, 3)} m² (단면 ${fmt(d.section_area_m2, 3)} m²)`) + (d.note ? '  ⚠ ' + d.note : '');
}

// =================================================================== stiffeners
const GROUPS = {
  bottom: { title: '선저 Stringer', coord: '|y|', cl: 'keel', modes: [['auto', '자동: 중심선부터 간격 × 한쪽 개수'], ['spacing', '시작 위치 + 간격 × 개수'], ['list', '위치 직접 입력']] },
  deck: { title: '갑판 Stringer', coord: '|y|', cl: 'deck', modes: [['auto', '자동: 중심선부터 간격 × 한쪽 개수'], ['spacing', '시작 위치 + 간격 × 개수'], ['list', '위치 직접 입력']] },
  side: { title: '측면 Stringer', coord: 'z', cl: null, modes: [['spacing', '시작 높이 + 간격 × 개수'], ['list', '높이 직접 입력']] },
};
function girderAbs() { return (S.W.girders || []).map(g => Math.abs(+g.y)).filter(isFinite); }
function seriesJS(c, skipGirders) {   // same rule as params._series
  const mode = c.mode || 'list';
  let v = [];
  if (mode === 'list') v = (c.positions || []).map(Number);
  else if (mode === 'auto') v = Array.from({ length: Math.max(0, c.count | 0) }, (_, i) => (+c.spacing) * (i + 1));
  else v = Array.from({ length: Math.max(0, c.count | 0) }, (_, i) => (+c.start) + (+c.spacing) * i);
  const skip = +c.skip_near_girders || 0;
  if (skipGirders && mode !== 'list' && skip > 0) { const g = girderAbs(); v = v.filter(y => g.every(q => Math.abs(y - q) > skip)); }
  return v.filter(isFinite).sort((a, b) => a - b);
}
function secList(c, n) {             // make c.section a list of length n (keeps values)
  let s = c.section;
  if (!Array.isArray(s)) s = Array.from({ length: n }, () => normSec(s));
  else { s = s.map(normSec); while (s.length < n) s.push(clone(s[s.length - 1] || normSec(null))); s.length = n; }
  c.section = s; return s;
}
function renderGroup(k) {
  const G = GROUPS[k], c = S.W.stringers[k];
  if (!G.modes.some(m => m[0] === c.mode)) c.mode = G.modes[0][0];
  const parts = [];
  if (G.cl) {
    const clc = S.W.centerline[G.cl] ||= { enabled: false, section: normSec(null) };
    const cb = el('input', { type: 'checkbox' }); cb.checked = !!clc.enabled;
    cb.onchange = () => { clc.enabled = cb.checked; markDirty(); renderForm(); };
    parts.push(frow('중심선 (y = 0) 보강재', el('label', {}, cb, ' 넣기')));
  }
  const modeSel = el('select'); for (const [v, t] of G.modes) modeSel.append(el('option', { value: v }, t));
  modeSel.value = c.mode;
  modeSel.onchange = () => {
    const cur = seriesJS(c, G.cl);
    c.mode = modeSel.value;
    if (c.mode === 'list') c.positions = cur;
    if (c.mode === 'auto' && !(c.spacing > 0)) c.spacing = 200;
    if (c.mode !== 'list' && !(c.count > 0)) c.count = cur.length || 4;
    c.extents = null; markDirty(); renderForm();
  };
  parts.push(frow('배치 방식', modeSel));
  const re = () => { markDirty(); renderForm(); };
  if (c.mode === 'list') {
    parts.push(F(`${G.coord} 위치 목록 [mm]`, ['stringers', k, 'positions'], 'list', '쉼표로 구분, 한쪽만 입력 (반대쪽은 대칭 생성)', { rerender: true }));
  } else {
    if (c.mode === 'spacing') parts.push(frow(`시작 ${G.coord} [mm]`, numInput(c.start, v => { c.start = v; c.extents = null; re(); })));
    parts.push(frow('간격 [mm]', numInput(c.spacing, v => { c.spacing = v; c.extents = null; re(); }, { positive: true })));
    parts.push(frow(G.cl ? '한쪽(half) 개수' : '개수', numInput(c.count, v => { c.count = Math.max(0, Math.round(v)); c.extents = null; re(); }, { dp: 0 })));
    if (G.cl) parts.push(frow('거더 근처 생략 [mm]', numInput(c.skip_near_girders ?? 0, v => { c.skip_near_girders = v; re(); }, { title: '거더와 이 거리 이내의 위치는 만들지 않음' })));
  }
  const pos = seriesJS(c, G.cl);
  const sec = secList(c, pos.length);
  const tbl = el('table', { class: 'grid' });
  tbl.append(el('tr', {}, el('th', {}, '#'), el('th', {}, G.coord + ' [mm]'), el('th', {}, '종류 · 치수 [mm]')));
  if (G.cl && S.W.centerline[G.cl]?.enabled) {
    const clc = S.W.centerline[G.cl];
    tbl.append(el('tr', {}, el('td', { class: 'nm' }, 'CL'), el('td', {}, '0'), el('td', {}, secInline(() => clc.section, x => { clc.section = x; }))));
  }
  pos.forEach((p, i) => tbl.append(el('tr', {}, el('td', { class: 'nm' }, String(i + 1)), el('td', {}, fmt(p, 1)),
    el('td', {}, secInline(() => c.section[i], x => { c.section[i] = x; })))));
  const ext = Array.isArray(c.extents) && c.extents.length;
  parts.push(el('div', { class: 'calc' }, `→ 한쪽 ${pos.length}개` + (G.cl ? ` (양쪽 ${pos.length * 2}개${S.W.centerline[G.cl]?.enabled ? ' + 중심선 1개' : ''})` : ` (좌우 각각)`)),
    el('div', { class: 'tblwrap' }, tbl),
    el('div', { class: 'bar' },
      btn('이 그룹 단면 모두 같게 (1번 기준)', () => { const s0 = normSec(c.section[0] || (G.cl && S.W.centerline[G.cl]?.section)); c.section = c.section.map(() => clone(s0)); if (G.cl && S.W.centerline[G.cl]) S.W.centerline[G.cl].section = clone(s0); markDirty(); renderForm(); })),
    F('시작 x (aft/BHn/Fn/숫자)', ['stringers', k, 'x_from'], 'ref'), F('끝 x (fwd/BHn/Fn/숫자)', ['stringers', k, 'x_to'], 'ref'),
    ext ? el('div', { class: 'hint' }, `원본 CAD의 개별 길이 ${c.extents.length}개 사용 중 `, btn('해제 (전체 길이)', () => { c.extents = null; re(); })) : null);
  return fs(G.title, ...parts);
}
function allSectionRefs(opts) {        // [getter/setter] for every beam section in the model
  const W = S.W, out = [];
  const add = (obj, key) => { if (obj && obj[key] !== undefined) out.push([obj, key]); };
  if (opts.stringers) for (const k of ['bottom', 'side', 'deck']) { const c = W.stringers[k]; if (Array.isArray(c.section)) c.section.forEach((_, i) => add(c.section, i)); else add(c, 'section'); }
  if (opts.centerline) for (const k of ['keel', 'deck']) add(W.centerline[k], 'section');
  const pat = p => { for (const g of [...(p?.vertical || []), ...(p?.horizontal || [])]) add(g, 'section'); };
  if (opts.bulkhead) { pat(W.bulkheads.stiffeners); for (const r of stRows()) if (r.stiffeners) pat(r.stiffeners); for (const p of (W.bulkheads.per_bulkhead || [])) pat(p); }
  if (opts.transom) pat(W.transom);
  if (opts.flanges) { add(W.frames.flange, 'section'); for (const g of W.girders || []) add(g.flange, 'section'); }
  return out;
}
function renderStiffTab(box) {
  const W = S.W;
  S.refSec ||= normSec(Array.isArray(W.stringers.bottom.section) ? W.stringers.bottom.section[0] : W.stringers.bottom.section);
  const opt = S.unifyOpt ||= { stringers: true, centerline: true, bulkhead: true, transom: true, flanges: false };
  const cbs = [['stringers', '종보강재(선저·측면·갑판)'], ['centerline', '중심선'], ['bulkhead', '격벽 보강재'], ['transom', '트랜섬 보강재'], ['flanges', '늑골·거더 플랜지']].map(([k, t]) => {
    const c = el('input', { type: 'checkbox' }); c.checked = !!opt[k]; c.onchange = () => { opt[k] = c.checked; }; return el('label', { style: 'margin-right:8px' }, c, ' ' + t);
  });
  box.append(fs('모든 보강재 단면 같게',
    el('div', { class: 'muted sm' }, '아래 기준 단면을 선택한 그룹의 모든 보강재에 적용합니다.'),
    secInline(() => S.refSec, x => { S.refSec = x; }, { rerender: () => renderForm() }),
    el('div', { class: 'bar' }, ...cbs),
    btn('적용', () => { const refs = allSectionRefs(opt); for (const [o, k] of refs) o[k] = clone(S.refSec); markDirty(); renderForm(); toast(`보강재 단면 ${refs.length}곳 통일`); }, 'primary')));
  box.append(renderGroup('bottom'), renderGroup('side'), renderGroup('deck'));
  // bulkhead stiffeners: per bulkhead row or common pattern
  const names = rowNames(), rows = stRows();
  const bIdx = rows.map((r, i) => r.type === 'bulkhead' ? i : -1).filter(i => i >= 0);
  const f = fs('격벽 보강재');
  if (rows.length) {
    S.bhSel = S.bhSel ?? 'common';
    const sel = el('select'); sel.append(el('option', { value: 'common' }, '공통 패턴 (전용 패턴이 없는 격벽)'));
    for (const i of bIdx) sel.append(el('option', { value: String(i) }, `${names[i]} (x=${fmt(rows[i].x, 1)})${rows[i].stiffeners ? ' – 전용' : ''}`));
    if (S.bhSel !== 'common' && !bIdx.includes(+S.bhSel)) S.bhSel = 'common';
    sel.value = String(S.bhSel); sel.onchange = () => { S.bhSel = sel.value; renderForm(); };
    f.append(frow('편집할 격벽', sel));
    if (S.bhSel === 'common') f.append(...stiffGroups(['bulkheads', 'stiffeners'], '공통'));
    else {
      const r = rows[+S.bhSel];
      if (!r.stiffeners) f.append(el('p', { class: 'muted' }, '이 격벽은 공통 패턴을 사용합니다. '), btn('이 격벽 전용 패턴 만들기 (공통 패턴 복사)', () => { r.stiffeners = clone(W.bulkheads.stiffeners || { vertical: [], horizontal: [] }); markDirty(); renderForm(); }));
      else f.append(btn('전용 패턴 삭제 → 공통 패턴 사용', () => { delete r.stiffeners; markDirty(); renderForm(); }), ...stiffGroups(['stations', 'list', +S.bhSel, 'stiffeners'], names[+S.bhSel]));
    }
  } else f.append(...stiffGroups(['bulkheads', 'stiffeners'], '격벽'));
  box.append(f);
  box.append(fs('트랜섬 보강재', ...stiffGroups(['transom'], '트랜섬')));
}

function renderForm() {
  const box = $('#form'); const keep = box.scrollTop; box.innerHTML = '';
  if (!S.W) { box.innerHTML = '<p class="muted">프로젝트가 없습니다. CAD를 가져오거나 프로젝트를 여세요.</p>'; return; }
  const W = S.W;
  if (S.tab === 'layout') {
    renderLayoutTab(box);
  } else if (S.tab === 'long') {
    box.append(fs('거더 (Girders, y 평면)', listEditor(['girders'], '거더', (p, it) => [
      F('이름', [...p, 'name'], 'text'), F('|y| 위치 [mm]', [...p, 'y'], 'num'), F('좌우 대칭', [...p, 'symmetric'], 'bool'),
      F('두께 [mm]', [...p, 'thickness'], 'num'),
      listEditor([...p, 'segments'], '구간', (q) => [
        F('시작 x (aft/BHn/Fn/숫자)', [...q, 'x_from'], 'ref'), F('끝 x (fwd/BHn/Fn/숫자)', [...q, 'x_to'], 'ref'),
        F('상단 높이 z_top [mm]', [...q, 'z_top'], 'numtab', '숫자 또는  x:z, x:z ... (경사 상단)')],
        () => ({ x_from: 'aft', x_to: 'fwd', z_top: 600 })),
      F('상단 플랜지(빔)', [...p, 'flange', 'enabled'], 'bool'), sectionEditor([...p, 'flange', 'section'], '거더 플랜지 단면')],
      () => ({ name: 'Girder', y: 500, symmetric: true, thickness: 8, segments: [{ x_from: 'aft', x_to: 'BH1', z_top: 600 }], flange: { enabled: false, section: { shape: 'FLAT', height: 100, thickness: 12 } } }))));
    box.append(fs('수평판 (Plates, z 평면)', listEditor(['plates'], '수평판', (p) => [
      F('이름', [...p, 'name'], 'text'), F('z 높이 [mm]', [...p, 'z'], 'num'),
      F('x 시작', [...p, 'x_from'], 'ref'), F('x 끝', [...p, 'x_to'], 'ref'),
      F('y 시작 [mm]', [...p, 'y_from'], 'num'), F('y 끝 [mm]', [...p, 'y_to'], 'num'), F('두께 [mm]', [...p, 'thickness'], 'num')],
      () => ({ name: 'Plate', z: 600, x_from: 'aft', x_to: 'BH1', y_from: -400, y_to: 400, thickness: 10 }))));
  } else if (S.tab === 'stiff') {
    renderStiffTab(box);
  } else if (S.tab === 'thk') {
    box.append(fs('외판 두께 [mm]', ...['deck', 'side', 'bottom', 'chine', 'transom'].map(k => F({ deck: '갑판 (deck)', side: '측판 (side)', bottom: '선저 (bottom)', chine: '스프레이 스트립 (chine)', transom: '트랜섬 (transom)' }[k], ['plating', k], 'num'))));
    box.append(fs('재료', F('이름', ['material', 'name'], 'text'), F('탄성계수 E [MPa]', ['material', 'E_MPa'], 'num'), F('푸아송비 ν', ['material', 'nu'], 'num'), F('밀도 [kg/m³]', ['material', 'density_kg_m3'], 'num')));
    box.append(fs('고정 부재', F('두께 [mm]', ['fixed_members', 'thickness'], 'num', '자동 인식되지 않아 그대로 유지되는 부재')));
    box.append(fs('생성·검사 설정',
      F('기존 분할선 맞춤 공차 [mm]', ['build', 'snap_tolerance'], 'num', '이 거리 이내의 부재는 기존 외판 분할선에 정확히 맞춤 (sliver 방지)'),
      F('불리언 fuzzy 공차 [mm]', ['build', 'fuzzy_tolerance'], 'num'),
      F('늑골/격벽 최소 간격 [mm]', ['build', 'min_station_gap'], 'num'),
      F('평행 부재 최소 간격 [mm]', ['build', 'min_member_gap'], 'num'),
      F('짧은 모서리 경고 기준 [mm]', ['build', 'check_small_edge'], 'num'),
      F('작은 면 경고 기준 [mm²]', ['build', 'check_small_face'], 'num'),
      F('화면 표시 정밀도 [mm]', ['build', 'viewer_deflection'], 'num')));
  } else if (S.tab === 'yaml') {
    const ta = document.createElement('textarea'); ta.className = 'yaml'; ta.value = S.yaml; ta.spellcheck = false;
    const b1 = document.createElement('button'); b1.textContent = 'YAML 적용 (검증 후 저장)'; b1.className = 'primary';
    b1.onclick = async () => {
      const r = await api('/api/params', { yaml: ta.value });
      if (r.ok) { S.W = r.params; S.yaml = r.yaml; S.dirty = false; $('#dirty').hidden = true; msgParams(r); toast('YAML 저장됨'); renderForm(); }
      else msgParams(r);
    };
    const b2 = document.createElement('button'); b2.textContent = '폼 내용으로 갱신';
    b2.onclick = async () => { const r = await api('/api/validate', { params: S.W }); if (r.ok) { ta.value = r.yaml; } msgParams(r); };
    const p = document.createElement('p'); p.className = 'muted'; p.textContent = '모든 파라미터를 직접 편집할 수 있습니다 (격벽별 보강재, 부재별 단면, 거더 상단 표 등).';
    box.append(p, ta, b1, document.createTextNode(' '), b2);
  }
  box.scrollTop = keep;
}
function renderChips() {
  const c = $('#stchips'); if (!c) return;
  c.innerHTML = S.preview.length ? S.preview.map(s => `<span class="chip ${s.name.startsWith('BH') ? 'bh' : 'fr'}">${esc(s.name)} ${fmt(s.x, 1)}</span>`).join('') : '<span class="muted">배치 확인을 누르면 늑골/격벽 위치가 표시됩니다.</span>';
}
$$('#tabs button').forEach(b => b.onclick = async () => {
  $$('#tabs button').forEach(x => x.classList.toggle('on', x === b)); S.tab = b.dataset.tab;
  if (S.tab === 'yaml' && S.dirty) { const r = await api('/api/validate', { params: S.W }); if (r.ok) S.yaml = r.yaml; }
  renderForm();
});

// ------------------------------------------------------------------ messages
function setMtab(t) { S.mtab = t; $$('#mtabs button').forEach(x => x.classList.toggle('on', x.dataset.mtab === t)); renderMsgs(); }
$$('#mtabs button').forEach(b => b.onclick = () => setMtab(b.dataset.mtab));
let paramMsgs = null;
function msgParams(r) {
  paramMsgs = r; if (r.stations) { drawPreview(r.stations); renderChips(); }
  setMtab('check');
}
function renderMsgs() {
  const box = $('#msgs');
  const ck = S.state?.check;
  const badge = $('#badge'); badge.innerHTML = ck ? `<span class="lv ${ck.status}">${ck.status}</span>` : '';
  if (S.mtab === 'check') {
    let html = '';
    if (paramMsgs) {
      if (!paramMsgs.ok) html += (paramMsgs.errors || [paramMsgs.error]).map(e => `<tr><td><span class="lv ERROR">입력</span></td><td colspan="2">${esc(e)}</td></tr>`).join('');
      else {
        const c = paramMsgs.counts || {};
        html += `<tr><td><span class="lv OK">입력</span></td><td colspan="2">파라미터 정상 — 격벽 ${c.bulkheads}, 늑골 ${c.frames}, 거더 ${c.girders}, 수평판 ${c.plates}, 보강재 ${c.beams}${paramMsgs.saved ? ' (저장됨)' : ''}</td></tr>`;
        html += (paramMsgs.warnings || []).map(w => `<tr><td><span class="lv WARN">입력</span></td><td colspan="2">${esc(w)}</td></tr>`).join('');
      }
    }
    if (ck) {
      const st = ck.stats;
      html += `<tr><td><span class="lv ${ck.status}">${ck.status}</span></td><td colspan="2"><b>무결성 검사</b> — 면 ${st.faces}, 빔선 ${st.beam_edges}, 분리된 부분 ${st.components - 1}, 틈 ${st.gaps}, 떠 있는 빔 ${st.floating_beam_edges}, 무효 ${st.invalid}${st.tangent_faces ? ` (+접선겹침 ${st.tangent_faces})` : ''}, 겹침 ${st.duplicate_faces}, 짧은 모서리 ${st.small_edges}, 작은 면 ${st.small_faces}, T-접합 ${st.t_joints} | 쉘 ${st.shell_area_m2} m², 빔 ${st.beam_length_m} m</td></tr>`;
      ck.issues.forEach((i, k) => {
        html += `<tr class="${i.location ? 'click' : ''}" data-k="${k}"><td><span class="lv ${i.level}">${i.level}</span></td><td>${esc(i.code)}</td><td>${esc(i.message)}${i.location ? ' <span class="muted">📍</span>' : ''}</td></tr>`;
      });
    }
    if (!html) html = '<tr><td class="muted">아직 결과가 없습니다. ✓ 검증 또는 ▶ 생성을 실행하세요.</td></tr>';
    box.innerHTML = `<table>${html}</table>`;
    $$('#msgs tr.click').forEach(tr => tr.onclick = () => { const i = ck.issues[+tr.dataset.k]; focusPoint(i.location, i.member); });
  } else if (S.mtab === 'log') {
    const j = S.lastJob;
    box.innerHTML = `<pre class="log">${esc(j ? (j.log || []).join('\n') : '작업 기록이 없습니다.')}</pre>`;
    box.scrollTop = box.scrollHeight;
  } else {
    const ex = S.state?.extract;
    if (!ex) { box.innerHTML = '<p class="muted">추출 리포트가 없습니다.</p>'; return; }
    let h = '';
    if (ex.frames_rule) h += `<p><b>늑골 배치 규칙:</b> ${esc(ex.frames_rule)}</p>`;
    const ff = ex.members?.frames_fit || [];
    if (ff.length) h += '<table><tr><td><b>늑골</b></td><td><b>x</b></td><td><b>웨브</b></td><td><b>갑판보</b></td><td><b>반경</b></td><td><b>CAD 일치도(IoU)</b></td></tr>' + ff.map(f => `<tr><td>${f.name}</td><td>${fmt(f.x)}</td><td>${fmt(f.web_depth)}</td><td>${fmt(f.deck_beam_depth)}</td><td>${fmt(f.corner_radius)}</td><td>${fmt(f.iou, 3)}</td></tr>`).join('') + '</table>';
    const gf = ex.members?.girders_fit || [];
    if (gf.length) h += '<p>' + gf.map(g => `거더 y=${fmt(g.y)}: IoU ${fmt(g.iou, 3)}`).join(' · ') + '</p>';
    if (ex.warnings?.length) h += '<p><b>주의</b></p><ul>' + ex.warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>';
    if (ex.defaults_used?.length) h += `<p><b>기본값이 사용된 항목</b> (필요 시 수정): ${ex.defaults_used.map(esc).join(', ')}</p>`;
    box.innerHTML = h;
  }
}

// ------------------------------------------------------------------ jobs
function setJobUI(j) {
  $('#jobfill').style.width = Math.round(100 * (j.progress || 0)) + '%';
  $('#jobmsg').textContent = j.running ? `${j.message} (${fmt(j.elapsed, 0)} s)` : (j.error ? '오류: ' + j.error : (j.name ? `${j.name} 완료` : '준비'));
  $('#jobfill').style.background = j.error ? '#e05555' : '#59d185';
  for (const id of ['#btnBuild', '#btnImport', '#btnExport', '#btnOpen']) $(id).disabled = !!j.running;
}
async function poll(onDone) {
  if (S.polling) return; S.polling = true;
  const tick = async () => {
    const j = await api('/api/job'); S.lastJob = j; setJobUI(j);
    if (S.mtab === 'log') renderMsgs();
    if (j.running) setTimeout(tick, 600);
    else { S.polling = false; onDone && onDone(j); }
  };
  tick();
}
async function refresh(loadMesh = true) {
  const st = await api('/api/state'); S.state = st;
  $('#ver').textContent = 'v' + st.version;
  $('#projname').textContent = st.project ? '프로젝트: ' + st.project : '프로젝트 없음';
  $('#projname').title = st.project || '';
  if (st.params) { if (!S.dirty) { S.W = clone(st.params); S.yaml = st.yaml; } }
  else { S.W = null; }
  if (st.params_error) toast('params.yaml 오류: ' + st.params_error, true);
  renderForm(); renderMsgs();
  if (loadMesh && (st.mesh_ver !== S.meshVer || !S.mesh)) {
    S.meshVer = st.mesh_ver;
    if (st.has_envelope) { const m = await api('/api/mesh'); buildScene(m); if (!S.viewSet) { setView('iso'); S.viewSet = true; } }
    else { clearScene(); S.mesh = null; buildTree(); $('#empty').hidden = false; }
  }
  if (st.job) { S.lastJob = st.job; setJobUI(st.job); if (st.job.running) poll(onJobDone); }
}
async function onJobDone(j) {
  if (j.error) { toast(j.error, true); setMtab('log'); }
  else if (j.name === 'build') toast(`생성 완료 — 검사 ${j.result?.status}`);
  else if (j.name === 'extract') { toast('파라미터 추출 완료'); S.dirty = false; $('#dirty').hidden = true; S.viewSet = false; }
  else if (j.name === 'export') { showExportResult(j.result); }
  await refresh(true);
  if (j.name === 'build' && !j.error) { drawPreview([]); setMtab('check'); }
  if (j.name === 'extract' && !j.error) { setMtab('extract'); setView('iso'); }
}

// ------------------------------------------------------------------ actions
async function validate() {
  if (!S.W) return;
  const r = await api('/api/validate', { params: S.W }); msgParams(r);
  if (r.ok) toast(`검증 통과: 격벽 ${r.counts.bulkheads}, 늑골 ${r.counts.frames}`); else toast('입력 오류가 있습니다 (아래 메시지 확인)', true);
}
async function save() {
  if (!S.W) return;
  const r = await api('/api/params', { params: S.W }); msgParams(r);
  if (r.ok) { S.yaml = r.yaml; S.W = r.params; S.dirty = false; $('#dirty').hidden = true; toast('저장됨: params.yaml'); renderForm(); }
  else toast('저장 실패: 입력 오류', true);
}
async function buildNow() {
  if (!S.W) { toast('먼저 CAD를 가져오거나 프로젝트를 여세요.', true); return; }
  const r = await api('/api/build', { params: S.W });
  if (r.ok === false) { msgParams(r); toast(r.error || '입력 오류로 생성할 수 없습니다', true); return; }
  S.dirty = false; $('#dirty').hidden = true;
  setMtab('log'); poll(onJobDone);
}
$('#btnValidate').onclick = validate;
$('#btnSave').onclick = save;
$('#btnBuild').onclick = buildNow;

function toast(msg, err) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = `position:fixed;right:16px;top:56px;z-index:20;padding:8px 14px;border-radius:4px;color:#fff;background:${err ? '#c62828' : '#2e7d32'};box-shadow:0 4px 14px rgba(0,0,0,.25);max-width:420px`;
  document.body.appendChild(t); setTimeout(() => t.remove(), err ? 6000 : 3000);
}

// ---------------- modal helpers
function modal(html) { $('#modalBox').innerHTML = html; $('#modal').hidden = false; }
function closeModal() { $('#modal').hidden = true; }
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });
async function browse(start, ext, pickDir, onPick) {
  const st = { dir: start || '' };
  const render = async () => {
    const r = await api(`/api/browse?dir=${encodeURIComponent(st.dir)}&ext=${encodeURIComponent(ext || '')}`);
    st.dir = r.dir;
    const drives = (r.drives || []).map(d => `<button class="mini" data-drive="${esc(d)}">${esc(d)}</button>`).join(' ');
    $('#brw').innerHTML = `<div class="mrow" style="grid-template-columns:1fr auto"><input type="text" id="brwPath" value="${esc(r.dir)}"><button class="mini" id="brwGo">이동</button></div>${drives}
      <div class="browser"><div class="d" data-p="${esc(r.parent)}">..</div>${r.items.map(i => `<div class="${i.dir ? (i.project ? 'p' : 'd') : 'f'}" data-p="${esc(r.dir + (r.dir.endsWith('/') || r.dir.endsWith('\\') ? '' : (r.dir.includes('\\') ? '\\' : '/')) + i.name)}" data-dir="${i.dir ? 1 : 0}">${esc(i.name)}${i.project ? ' (프로젝트)' : ''}</div>`).join('')}</div>
      ${pickDir ? '<button class="primary" id="brwSel">이 폴더 선택</button>' : ''}`;
    $$('#brw .browser div').forEach(d => d.onclick = () => { if (d.dataset.dir === '0') { onPick(d.dataset.p); } else { st.dir = d.dataset.p; render(); } });
    $$('#brw [data-drive]').forEach(b => b.onclick = () => { st.dir = b.dataset.drive; render(); });
    $('#brwGo').onclick = () => { st.dir = $('#brwPath').value; render(); };
    if (pickDir) $('#brwSel').onclick = () => onPick(st.dir);
  };
  await render();
}
function importDialog() {
  modal(`<h3>CAD 가져오기 (자동 파라미터 추출)</h3>
  <p class="muted">선체 외판 + 내부 부재가 모두 들어있는 전체 CAD(STEP 권장, IGES 가능)를 선택하세요. COMSOL .mph 를 함께 지정하면 두께·보강재 단면·격벽 보강재 위치도 가져옵니다.</p>
  <div class="mrow"><label>CAD 파일 *</label><input type="text" id="imCad" placeholder="예) C:\\data\\GPPH_A4-B1_All.step"><button id="imCadB">찾기</button></div>
  <div class="mrow"><label>COMSOL .mph (선택)</label><input type="text" id="imMph" placeholder="예) C:\\data\\GPPH_A4-B1.mph"><button id="imMphB">찾기</button></div>
  <div class="mrow"><label>프로젝트 폴더 (선택)</label><input type="text" id="imProj" placeholder="비우면 CAD 옆에 <이름>_project 생성"><button id="imProjB">찾기</button></div>
  <div class="mrow"><label>외판 꿰매기 공차 [mm]</label><input type="number" id="imSew" value="0.5" step="0.1"><span></span></div>
  <div id="brw"></div>
  <div class="mfoot"><button id="imCancel">취소</button><button class="primary" id="imGo">추출 시작</button></div>`);
  const last = localStorage.getItem('hp_lastdir') || '';
  $('#imCadB').onclick = () => browse(last, '.step,.stp,.igs,.iges,.brep', false, p => { $('#imCad').value = p; localStorage.setItem('hp_lastdir', p.replace(/[\\/][^\\/]*$/, '')); $('#brw').innerHTML = ''; });
  $('#imMphB').onclick = () => browse(last, '.mph', false, p => { $('#imMph').value = p; $('#brw').innerHTML = ''; });
  $('#imProjB').onclick = () => browse(last, '', true, p => { $('#imProj').value = p; $('#brw').innerHTML = ''; });
  $('#imCancel').onclick = closeModal;
  $('#imGo').onclick = async () => {
    const r = await api('/api/extract', { cad: $('#imCad').value, mph: $('#imMph').value, project: $('#imProj').value, sew_tol: +$('#imSew').value });
    if (r.ok === false) { toast((r.errors || [r.error]).join(' '), true); return; }
    closeModal(); toast('추출을 시작했습니다 (1~2분)'); setMtab('log'); poll(onJobDone);
  };
}
function openDialog() {
  modal(`<h3>프로젝트 열기</h3><p class="muted">⭐ 표시된 폴더가 HullParam 프로젝트입니다 (envelope.brep, params.yaml 포함).</p><div id="brw"></div><div class="mfoot"><button id="opCancel">취소</button></div>`);
  $('#opCancel').onclick = closeModal;
  browse(S.state?.project || localStorage.getItem('hp_lastdir') || '', '-', true, async p => {
    const r = await api('/api/project', { project: p });
    if (!r.ok) { toast((r.errors || [r.error]).join(' '), true); return; }
    closeModal(); S.dirty = false; S.viewSet = false; await refresh(true);
  });
}
function exportDialog() {
  if (!S.state?.check) { toast('먼저 ▶ 생성을 실행하세요.', true); return; }
  const st = S.state.check.status;
  modal(`<h3>내보내기</h3>
  ${st === 'ERROR' ? '<p style="color:#c62828"><b>무결성 검사에 ERROR가 있습니다.</b> 내보낼 수는 있지만 해석 전에 반드시 확인하세요.</p>' : ''}
  <div class="mrow"><label>STEP</label><label><input type="checkbox" id="exStep" checked> 어셈블리(이름·색상) + 쉘 + 빔</label><span></span></div>
  <div class="mrow"><label>IGES</label><label><input type="checkbox" id="exIges" checked> 빔(선) — DesignModeler 라인 바디용</label><span></span></div>
  <div class="mrow"><label></label><label><input type="checkbox" id="exIgesS"> 쉘도 IGES로 (권장하지 않음, STEP 사용 권장)</label><span></span></div>
  <div class="mrow"><label>BREP / 표</label><label>항상 출력: .brep(연결 유지), 물성표 CSV, 모델 JSON, 검사 JSON, 파라미터</label><span></span></div>
  <div class="mrow"><label>출력 폴더 (선택)</label><input type="text" id="exDir" placeholder="비우면 프로젝트/output/<시각>"><button id="exDirB">찾기</button></div>
  <div id="brw"></div>
  <div class="mfoot"><button id="exCancel">취소</button><button class="primary" id="exGo">내보내기</button></div>`);
  $('#exCancel').onclick = closeModal;
  $('#exDirB').onclick = () => browse(S.state.project, '-', true, p => { $('#exDir').value = p; $('#brw').innerHTML = ''; });
  $('#exGo').onclick = async () => {
    const formats = ['brep']; if ($('#exStep').checked) formats.push('step'); if ($('#exIges').checked || $('#exIgesS').checked) formats.push('iges');
    const r = await api('/api/export', { formats, iges_shells: $('#exIgesS').checked, out_dir: $('#exDir').value || null });
    if (r.ok === false) { toast((r.errors || [r.error]).join(' '), true); return; }
    closeModal(); setMtab('log'); poll(onJobDone);
  };
}
function showExportResult(res) {
  if (!res) return;
  modal(`<h3>내보내기 완료</h3><p>${esc(res.out_dir)}</p><div class="filelist">${res.files.map(f => esc(f.split(/[\\/]/).pop())).join('<br>')}</div>
  <p class="muted">ANSYS: <b>*_assembly.step</b> 을 SpaceClaim/DesignModeler로 열고 <i>Share Topology</i> 를 적용하세요. 빔 단면·쉘 두께는 *_properties.csv 에 있습니다.</p>
  <div class="mfoot"><button id="exOpen">폴더 열기</button><button class="primary" id="exOk">확인</button></div>`);
  $('#exOk').onclick = closeModal;
  $('#exOpen').onclick = () => api('/api/open', { path: res.out_dir });
}
$('#btnImport').onclick = importDialog; $('#btnImport2').onclick = importDialog;
$('#btnOpen').onclick = openDialog; $('#btnOpen2').onclick = openDialog;
$('#btnExport').onclick = exportDialog;

// ---------------- view toolbar
$$('#viewtools [data-view]').forEach(b => b.onclick = () => setView(b.dataset.view));
$('#chkEdges').onchange = updateVisibility; $('#chkBeams').onchange = updateVisibility;
$('#chkIssues').onchange = updateVisibility; $('#chkPreview').onchange = updateVisibility;
$('#rngHull').oninput = () => S.objs.forEach(applyMaterial);
$('#selColor').onchange = () => { S.objs.forEach(applyMaterial); updateLegend(); };
$('#chkClip').onchange = updateClip; $('#rngClip').oninput = updateClip;
$('#btnShowAll').onclick = () => { S.objs.forEach(e => e.visible = true); $$('#tree input').forEach(i => i.checked = true); updateVisibility(); };
$('#btnIsolate').onclick = () => {
  if (!S.selected) { toast('먼저 부재를 선택하세요', true); return; }
  S.objs.forEach((e, n) => e.visible = n === S.selected);
  $$('#tree .trow[data-name] input').forEach(i => i.checked = i.closest('.trow').dataset.name === S.selected);
  updateVisibility();
};
window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'f' || e.key === 'F') setView('fit');
  if (e.key === 'Escape') { select(null); closeModal(); }
  if (e.key === 'h' && S.selected) $('#btnHideSel')?.click();
});
window.addEventListener('beforeunload', e => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });

// ---------------- start
resize();
refresh(true).catch(e => toast('서버 연결 실패: ' + e, true));
window.__hp = { S, setView, select, refresh };   // for debugging / tests
