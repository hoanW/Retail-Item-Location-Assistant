const $ = (id) => document.getElementById(id);
const template = $('item-card-template');

function fmtDate(value) {
  if (!value) return '';
  return new Date(value).toLocaleString();
}

function statusBadge(status) {
  return `<span class="status-badge status-${status}">${status}</span>`;
}

function lifecycleBadge(lifecycle) {
  return `<span class="lifecycle-badge lifecycle-${lifecycle}">${lifecycle}</span>`;
}

function lifecycleHint(item) {
  if (item.lifecycle_state === 'archived') {
    return '<p class="archive-warning">Archived item: this is historical data. Assign it to a location to reactivate it.</p>';
  }
  if (item.lifecycle_state === 'aging') {
    return '<p class="aging-warning">Old item: location may be outdated because it has not been updated recently.</p>';
  }
  return '';
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      message = data.detail || message;
    } catch (_) {}
    throw new Error(message);
  }
  return res.json();
}

function renderFindItem(item) {
  const node = template.content.cloneNode(true);
  node.querySelector('.location').textContent = item.current_location;
  node.querySelector('.article').textContent = item.article_number;
  node.querySelector('.status').innerHTML = statusBadge(item.status);
  node.querySelector('.lifecycle').innerHTML = lifecycleBadge(item.lifecycle_state);
  node.querySelector('.failures').textContent = item.failure_count;
  node.querySelector('.updated').textContent = fmtDate(item.last_updated);
  node.querySelector('.not-there').addEventListener('click', async () => {
    try {
      const updated = await api(`/items/${encodeURIComponent(item.article_number)}/not-there`, { method: 'POST' });
      $('find-result').innerHTML = `<p class="success">Marked not there. Status is now ${updated.status}.</p>`;
      renderFindItem({ ...item, ...updated });
      refreshOutdated();
    } catch (err) {
      $('find-result').innerHTML = `<p class="error">${err.message}</p>`;
    }
  });
  $('find-result').replaceChildren(node);
  $('find-result').insertAdjacentHTML('afterbegin', lifecycleHint(item));
}

$('find-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const article = $('find-article').value.trim();
  if (!article) return;
  $('find-result').textContent = 'Searching...';
  try {
    const item = await api(`/items/${encodeURIComponent(article)}`);
    renderFindItem(item);
    $('find-article').select();
  } catch (err) {
    $('find-result').innerHTML = `<p class="error">${err.message}</p><p class="hint">Use Assign / Update Location to register it.</p>`;
  }
});

$('assign-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const article_number = $('assign-article').value.trim();
  const location = $('assign-location').value.trim();
  if (!article_number || !location) return;
  try {
    const item = await api('/items/assign', {
      method: 'POST',
      body: JSON.stringify({ article_number, location }),
    });
    $('assign-result').innerHTML = `<p class="success">${item.article_number} assigned to ${item.current_location}.</p>`;
    $('assign-article').value = '';
    $('assign-article').focus();
    refreshOutdated();
  } catch (err) {
    $('assign-result').innerHTML = `<p class="error">${err.message}</p>`;
  }
});

async function refreshOutdated() {
  const body = $('outdated-body');
  body.innerHTML = '<tr><td colspan="6" class="muted">Loading...</td></tr>';
  try {
    const includeArchived = $('include-archived')?.checked ? '&include_archived=true' : '';
    const items = await api(`/items?unreliable=true${includeArchived}`);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">No outdated or suspect items.</td></tr>';
      return;
    }
    body.innerHTML = items.map(item => `
      <tr>
        <td>${item.article_number}</td>
        <td><strong>${item.current_location}</strong></td>
        <td>${statusBadge(item.status)}</td>
        <td>${lifecycleBadge(item.lifecycle_state)}</td>
        <td>${item.failure_count}</td>
        <td>${fmtDate(item.last_updated)}</td>
      </tr>`).join('');
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="error">${err.message}</td></tr>`;
  }
}

$('refresh-outdated').addEventListener('click', refreshOutdated);
$('include-archived')?.addEventListener('change', refreshOutdated);
refreshOutdated();

// Camera barcode scanning for phones.
// Uses the browser BarcodeDetector API when available and falls back to ZXing if the CDN script loads.
let activeStream = null;
let activeDetector = null;
let zxingReader = null;
let scannerMode = null;
let scannerBusy = false;

function setScannerStatus(message, className = 'muted') {
  $('scanner-status').className = `result ${className}`;
  $('scanner-status').textContent = message;
}

async function handleScannedBarcode(code) {
  if (scannerBusy) return;
  scannerBusy = true;
  const article = String(code || '').trim();
  if (!article) {
    scannerBusy = false;
    return;
  }
  setScannerStatus(`Scanned ${article}`);

  if (scannerMode === 'find') {
    $('find-article').value = article;
    closeScanner();
    $('find-form').requestSubmit();
    return;
  }

  if (scannerMode === 'assign') {
    const location = $('assign-location').value.trim();
    if (!location) {
      setScannerStatus('Choose an area before scanning.', 'error');
      scannerBusy = false;
      return;
    }
    try {
      const item = await api('/items/assign', {
        method: 'POST',
        body: JSON.stringify({ article_number: article, location }),
      });
      $('assign-result').innerHTML = `<p class="success">${item.article_number} assigned to ${item.current_location}.</p>`;
      $('assign-article').value = '';
      refreshOutdated();
      setScannerStatus(`Assigned ${item.article_number} to ${item.current_location}. Ready for next scan.`, 'success');
      // Keep scanner open for fast batch assignment. Small delay prevents duplicate reads of same barcode.
      setTimeout(() => { scannerBusy = false; }, 1200);
    } catch (err) {
      setScannerStatus(err.message, 'error');
      setTimeout(() => { scannerBusy = false; }, 1200);
    }
  }
}

async function startNativeBarcodeDetector(video) {
  if (!('BarcodeDetector' in window)) return false;

  const formats = await window.BarcodeDetector.getSupportedFormats?.();
  const preferred = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code'];
  const selected = Array.isArray(formats) ? preferred.filter(format => formats.includes(format)) : preferred;
  activeDetector = new window.BarcodeDetector({ formats: selected });

  const loop = async () => {
    if (!activeDetector || video.readyState < 2) {
      if (activeDetector) requestAnimationFrame(loop);
      return;
    }
    try {
      const barcodes = await activeDetector.detect(video);
      if (barcodes.length) await handleScannedBarcode(barcodes[0].rawValue);
    } catch (_) {
      // Detection can fail on some frames; keep scanning.
    }
    if (activeDetector) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  setScannerStatus('Camera ready. Point it at a barcode.');
  return true;
}

async function startZxingScanner(video) {
  if (!window.ZXing?.BrowserMultiFormatReader) return false;
  zxingReader = new window.ZXing.BrowserMultiFormatReader();
  await zxingReader.decodeFromVideoDevice(null, video, (result, err) => {
    if (result) handleScannedBarcode(result.getText());
  });
  setScannerStatus('Camera ready. Point it at a barcode.');
  return true;
}

async function openScanner(mode) {
  scannerMode = mode;
  scannerBusy = false;
  $('scanner-modal').hidden = false;
  setScannerStatus('Starting camera…');

  const video = $('scanner-video');
  try {
    activeStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: false,
    });
    video.srcObject = activeStream;
    await video.play();

    const nativeStarted = await startNativeBarcodeDetector(video);
    if (!nativeStarted) {
      const zxingStarted = await startZxingScanner(video);
      if (!zxingStarted) {
        setScannerStatus('Barcode scanning is not supported in this browser. Try Chrome/Edge on Android, or use manual entry.', 'error');
      }
    }
  } catch (err) {
    setScannerStatus(`Camera error: ${err.message}. Allow camera permission and use HTTPS or localhost.`, 'error');
  }
}

function closeScanner() {
  activeDetector = null;
  if (zxingReader) {
    zxingReader.reset();
    zxingReader = null;
  }
  if (activeStream) {
    activeStream.getTracks().forEach(track => track.stop());
    activeStream = null;
  }
  $('scanner-video').srcObject = null;
  $('scanner-modal').hidden = true;
  scannerBusy = false;
}

$('scan-find').addEventListener('click', () => openScanner('find'));
$('scan-assign').addEventListener('click', () => openScanner('assign'));
$('scanner-close').addEventListener('click', closeScanner);
$('scanner-modal').addEventListener('click', (event) => {
  if (event.target === $('scanner-modal')) closeScanner();
});
