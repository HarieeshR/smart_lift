const $ = (id) => document.getElementById(id);
const ARROWS = {UP: '▲', DOWN: '▼', IDLE: ''};
const PERSON_SVG = `<svg class="person" viewBox="0 0 26 58" aria-hidden="true"><circle cx="13" cy="6" r="5.5" fill="currentColor"/><path fill="currentColor" d="M6 14h14a3 3 0 0 1 3 3v15a2 2 0 0 1-4 0V20h-1v36a2.5 2.5 0 0 1-5 0V36h-0v20a2.5 2.5 0 0 1-5 0V20H7v12a2 2 0 0 1-4 0V17a3 3 0 0 1 3-3z"/></svg>`;
const CAMERA_SVG = `<svg viewBox="0 0 26 16" aria-hidden="true"><rect x="1" y="3" width="17" height="10" rx="2" fill="currentColor"/><path d="M18 6l7-3v10l-7-3z" fill="currentColor"/></svg>`;

let state = null;
let numFloors = 0;
const view = {position: 1, door: 0, last: performance.now()};

function post(path) {
  return fetch(path, {method: 'POST'}).then((response) => {
    if (!response.ok) throw new Error(response.statusText);
    return response.json();
  });
}

function build(floors) {
  numFloors = floors;
  document.documentElement.style.setProperty('--floors', floors);
  const lobbies = [];
  for (let floor = floors; floor >= 1; floor -= 1) {
    const up = floor < floors
      ? `<button class="hall-btn" data-hall="up" data-floor="${floor}" aria-label="Floor ${floor} up">▲</button>`
      : '<span class="hall-btn spacer"></span>';
    const down = floor > 1
      ? `<button class="hall-btn" data-hall="down" data-floor="${floor}" aria-label="Floor ${floor} down">▼</button>`
      : '<span class="hall-btn spacer"></span>';
    lobbies.push(`
      <div class="lobby" data-floor="${floor}">
        <div class="floor-plate">${floor}</div>
        <div class="lobby-wall"><div class="cam-badge" hidden>${CAMERA_SVG}CAM</div><div class="occupant"></div></div>
        <div class="call-station">
          <div class="hall-head">
            <div class="hall-indicator"><span class="arrow"></span><span class="num">1</span></div>
            <div class="lantern"><i class="up"></i><i class="down"></i></div>
          </div>
          <div class="hall-buttons">${up}${down}</div>
        </div>
      </div>`);
  }
  $('lobbies').innerHTML = lobbies.join('');

  const floorsDescending = Array.from({length: floors}, (_, i) => floors - i);
  $('carButtons').innerHTML = floorsDescending
    .map((floor) => `<button class="car-btn" data-car="${floor}" aria-label="Car call floor ${floor}">${floor}</button>`)
    .join('');
  $('cameraFloorButtons').innerHTML = floorsDescending.reverse()
    .map((floor) => `<button data-camera="${floor}">${floor}</button>`)
    .join('');
}

function flashDenied(button) {
  button.classList.add('denied');
  setTimeout(() => button.classList.remove('denied'), 900);
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  try {
    if (button.dataset.hall) {
      const floor = Number(button.dataset.floor);
      const direction = button.dataset.hall.toUpperCase();
      const next = await post(`/api/hall/${floor}/${button.dataset.hall}`);
      render(next);
      const registered = next.hall_calls.some((c) => c.floor === floor && c.direction === direction);
      if (!registered && next.events[0]?.message.includes('rejected')) flashDenied(button);
    } else if (button.dataset.car) {
      render(await post(`/api/car/${button.dataset.car}`));
    } else if (button.dataset.camera) {
      render(await post(`/api/camera-floor/${button.dataset.camera}`));
    }
  } catch (error) {
    console.error(error);
  }
});

function formatTime(date) {
  return date.toLocaleTimeString([], {hour12: false});
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function render(next) {
  state = next;
  if (next.num_floors !== numFloors) build(next.num_floors);
  const camera = next.camera || {};
  const waiting = Boolean(camera.waiting_person);
  const direction = next.direction;
  const doorsOpen = next.door_state !== 'CLOSED';

  const pill = $('globalStatus');
  pill.className = `status-pill ${waiting ? 'active' : 'idle'}`;
  pill.querySelector('span').textContent = waiting ? 'WAITING PERSON DETECTED' : 'NO WAITING PERSON';

  // CCTV panel
  $('waitingValue').textContent = waiting ? 'TRUE' : 'FALSE';
  $('waitingValue').className = waiting ? 'true' : 'false';
  $('personValue').textContent = camera.person_id ?? '—';
  $('dwellValue').textContent = `${Number(camera.dwell_time || 0).toFixed(1)}s`;
  $('trackCount').textContent = (camera.tracks || []).length;
  $('cameraFloorLabel').textContent = `FLOOR ${next.camera_floor}`;
  $('cctvCam').textContent = `CAM 01 · FLOOR ${next.camera_floor}`;
  $('noSignal').hidden = !camera.camera_error;
  $('cameraError').textContent = camera.camera_error || '';
  document.querySelectorAll('[data-camera]').forEach((b) => {
    b.classList.toggle('active', Number(b.dataset.camera) === next.camera_floor);
  });

  // Building
  $('directionLabel').textContent = next.moving ? `MOVING ${direction}` : (doorsOpen ? `DOORS ${next.door_state}` : direction);
  $('directionLabel').className = `dir-chip ${next.moving ? direction.toLowerCase() : ''}`;
  $('sheave').className = `sheave ${next.moving ? direction.toLowerCase() : ''}`;
  $('cabinDisplay').textContent = next.current_floor;
  const hallLit = new Set(next.hall_calls.map((c) => `${c.floor}-${c.direction}`));
  document.querySelectorAll('.lobby').forEach((lobby) => {
    const floor = Number(lobby.dataset.floor);
    const isCamera = floor === next.camera_floor;
    const atFloor = !next.moving && doorsOpen && next.current_floor === floor;
    lobby.classList.toggle('camera', isCamera);
    lobby.querySelector('.cam-badge').hidden = !isCamera;
    lobby.querySelector('.num').textContent = next.current_floor;
    lobby.querySelector('.arrow').textContent = next.moving ? ARROWS[direction] : '';
    lobby.querySelector('.lantern .up').classList.toggle('on', atFloor && direction === 'UP');
    lobby.querySelector('.lantern .down').classList.toggle('on', atFloor && direction === 'DOWN');
    lobby.querySelectorAll('[data-hall]').forEach((b) => {
      b.classList.toggle('lit', hallLit.has(`${floor}-${b.dataset.hall.toUpperCase()}`));
    });
    const occupant = lobby.querySelector('.occupant');
    const showPerson = isCamera && waiting;
    if (occupant.dataset.shown !== String(showPerson)) {
      occupant.dataset.shown = String(showPerson);
      occupant.innerHTML = showPerson ? `<div class="person-tag">WAITING</div>${PERSON_SVG}` : '';
      occupant.querySelector('.person')?.classList.add('waiting');
    }
    const tag = occupant.querySelector('.person-tag');
    if (tag) tag.textContent = `ID ${camera.person_id ?? '?'} · ${Number(camera.dwell_time || 0).toFixed(1)}s`;
  });

  // Car operating panel
  $('copFloor').textContent = next.current_floor;
  $('copArrow').textContent = ARROWS[direction];
  $('copArrow').className = `cop-arrow ${next.moving ? 'moving' : ''}`;
  $('doorLabel').textContent = `DOORS ${next.door_state}`;
  const carLit = new Set(next.car_calls);
  document.querySelectorAll('[data-car]').forEach((b) => {
    b.classList.toggle('lit', carLit.has(Number(b.dataset.car)));
  });
  $('plan').innerHTML = next.planned_stops.length
    ? next.planned_stops.map((f) => `<span class="chip">${f}</span>`).join('<span class="sep">→</span>')
    : '<span class="empty">Idle — no pending calls</span>';
  $('events').innerHTML = next.events.map((e) => {
    const tone = /cancelled|rejected/.test(e.message) ? 'bad' : (/Stopped/.test(e.message) ? 'good' : '');
    return `<li class="${tone}"><time>${formatTime(new Date(e.time * 1000))}</time><span>${escapeHtml(e.message)}</span></li>`;
  }).join('');
}

function animate(now) {
  const dt = Math.min((now - view.last) / 1000, 0.1);
  view.last = now;
  if (state) {
    const ease = 1 - Math.exp(-dt * 8);
    view.position += (state.position - view.position) * ease;
    view.door += (state.door_progress - view.door) * (1 - Math.exp(-dt * 14));
    const shaft = $('shaft');
    const floorH = shaft.clientHeight / numFloors;
    const cabinTop = (numFloors - view.position) * floorH + 6;
    const cabin = $('cabin');
    cabin.style.top = `${cabinTop}px`;
    cabin.style.setProperty('--door', view.door.toFixed(3));
    $('cable').style.height = `${cabinTop}px`;
    const cwRange = shaft.clientHeight - 76;
    const cwTop = ((view.position - 1) / (numFloors - 1)) * cwRange;
    $('counterweight').style.top = `${cwTop}px`;
    $('cwCable').style.height = `${cwTop}px`;
  }
  requestAnimationFrame(animate);
}

function tickClock() {
  const now = new Date();
  $('clock').textContent = formatTime(now);
  $('cctvTime').textContent = `${now.toISOString().slice(0, 10)} ${formatTime(now)}`;
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    const next = await response.json();
    if (!state) view.position = next.position;
    render(next);
  } catch (error) {
    $('cameraError').textContent = 'Simulation server unavailable';
    $('noSignal').hidden = false;
  }
}

refresh();
setInterval(refresh, 200);
setInterval(tickClock, 1000);
tickClock();
requestAnimationFrame(animate);
