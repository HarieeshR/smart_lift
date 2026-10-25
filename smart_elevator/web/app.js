const $ = (id) => document.getElementById(id);
const buttons = [...document.querySelectorAll('[data-floor]')];

function render(state) {
  const camera = state.camera || {};
  const active = Boolean(camera.waiting_person);
  $('globalStatus').textContent = active ? 'WAITING PERSON DETECTED' : 'NO WAITING PERSON';
  $('globalStatus').className = `status-pill ${active ? 'active' : 'idle'}`;
  $('waitingValue').textContent = active ? 'TRUE' : 'FALSE';
  $('waitingValue').style.color = active ? '#28a86b' : '#d95f45';
  $('personValue').textContent = camera.person_id ?? '—';
  $('dwellValue').textContent = `${Number(camera.dwell_time || 0).toFixed(1)}s`;
  $('cameraFloor').textContent = `CAMERA / FLOOR ${state.camera_floor}`;
  $('currentFloor').textContent = state.current_floor;
  $('targetFloor').textContent = state.target_floor ?? '—';
  $('direction').textContent = state.direction;
  $('liftDirection').textContent = state.direction;
  $('lift').className = `lift ${state.direction.toLowerCase()}`;
  const top = ((5 - (state.lift_position ?? state.current_floor)) / 4) * 380 + 18;
  $('lift').style.top = `${top}px`;
  $('cameraError').textContent = camera.camera_error || '';
  $('eventLog').textContent = camera.event || 'Monitoring camera';
  $('eventLog').className = `event-log ${
    camera.event && camera.event.includes('cancelled') ? 'cancelled' : ''
  }`;
  buttons.forEach((button) => {
    button.classList.toggle('active', state.requests.includes(Number(button.dataset.floor)));
  });
  $('requestList').innerHTML = state.requests.length
    ? state.requests.map((floor) => `<div class="request-row"><span>Floor ${floor}</span><small>REQUEST ACTIVE</small></div>`).join('')
    : '<span class="muted">No active requests</span>';
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    render(await response.json());
  } catch (error) {
    $('cameraError').textContent = 'Simulation server unavailable';
  }
}

buttons.forEach((button) => {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const response = await fetch(`/api/request/${button.dataset.floor}`, {method: 'POST'});
      render(await response.json());
    } finally {
      button.disabled = false;
    }
  });
});

refresh();
setInterval(refresh, 250);
