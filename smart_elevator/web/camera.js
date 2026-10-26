const $ = (id) => document.getElementById(id);
const TARGET_FPS = 20;
const MAX_WIDTH = 960;

const canvas = document.createElement('canvas');
let stream = null;
let running = false;
let wakeLock = null;
let sent = 0;
let statusTimer = null;

async function init() {
  $('insecure').hidden = window.isSecureContext;
  const status = await fetch('/api/status').then((r) => r.json()).catch(() => ({num_floors: 5}));
  const wanted = Number(new URLSearchParams(location.search).get('floor')) || 1;
  $('floor').innerHTML = Array.from({length: status.num_floors}, (_, i) => i + 1)
    .map((f) => `<option value="${f}" ${f === wanted ? 'selected' : ''}>Floor ${f}</option>`).join('');
}

function setLink(text, ok) {
  $('link').textContent = text;
  $('link').className = ok ? 'true' : 'false';
}

async function start() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setLink('NO CAMERA API', false);
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode: $('facing').value, width: {ideal: 1280}, height: {ideal: 720}},
      audio: false,
    });
  } catch (error) {
    setLink('DENIED', false);
    console.error(error);
    return;
  }
  $('preview').srcObject = stream;
  await $('preview').play();
  running = true;
  $('toggle').textContent = 'Stop streaming';
  $('toggle').classList.add('live');
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch (_) { /* optional */ }
  statusTimer = setInterval(pollStatus, 500);
  sendLoop();
}

function stop() {
  running = false;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  wakeLock?.release?.();
  clearInterval(statusTimer);
  $('toggle').textContent = 'Start streaming';
  $('toggle').classList.remove('live');
  setLink('IDLE', false);
}

function grab() {
  const video = $('preview');
  const scale = Math.min(1, MAX_WIDTH / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.7));
}

async function sendLoop() {
  while (running) {
    const began = performance.now();
    const video = $('preview');
    if (video.videoWidth) {
      try {
        const blob = await grab();
        const response = await fetch(`/api/cameras/${$('floor').value}/frame`, {
          method: 'POST', headers: {'Content-Type': 'image/jpeg'}, body: blob,
        });
        if (!response.ok) throw new Error(await response.text());
        sent += 1;
        setLink('LIVE', true);
      } catch (error) {
        setLink('ERROR', false);
        console.error(error);
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
    const wait = 1000 / TARGET_FPS - (performance.now() - began);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  }
}

async function pollStatus() {
  try {
    const cameras = await fetch('/api/cameras', {cache: 'no-store'}).then((r) => r.json());
    const mine = cameras.find((c) => c.floor === Number($('floor').value)) || {};
    $('waiting').textContent = mine.waiting_person ? 'TRUE' : 'FALSE';
    $('waiting').className = mine.waiting_person ? 'true' : 'false';
    $('zone').textContent = mine.people_in_zone ?? 0;
  } catch (_) { /* transient */ }
}

setInterval(() => { $('fps').textContent = sent.toFixed(1); sent = 0; }, 1000);
$('toggle').addEventListener('click', () => (running ? stop() : start()));
document.addEventListener('visibilitychange', async () => {
  if (running && document.visibilityState === 'visible') {
    try { wakeLock = await navigator.wakeLock?.request('screen'); } catch (_) { /* optional */ }
  }
});
init();
