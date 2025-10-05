const API_BASE = window.API_BASE_URL || `${window.location.origin}/api`;

const state = {
  scenes: [],
  currentScene: null,
  overlays: null,
  map: null,
  previewCache: new Map(),
  cropOverlay: null,
  mapContainer: null,
  mapPlaceholder: null,
  previewEndpointAvailable: null,
};

document.addEventListener("DOMContentLoaded", () => {
  setupMap();
  bindUI();
  loadScenes();
});

function setupMap() {
  state.mapContainer = document.getElementById("map-container");
  state.mapPlaceholder = document.getElementById("map-placeholder");

  state.map = L.map("map", {
    worldCopyJump: false,
    center: [0, 0],
    zoom: 2,
    minZoom: 1,
    maxZoom: 12,
  });

  state.overlays = L.layerGroup().addTo(state.map);
  setMapAvailability(false, "Select a scene with map data to enable geospatial view.");
}

function bindUI() {
  document.getElementById("init-btn").addEventListener("click", initializeDataset);
  document.getElementById("search-form").addEventListener("submit", onSearch);
  document.getElementById("crop-form").addEventListener("submit", onCropRequest);
  document.getElementById("download-full").addEventListener("click", downloadFullScene);
  ["min-lat", "min-lon", "max-lat", "max-lon"].forEach((id) => {
    const input = document.getElementById(id);
    input.addEventListener("input", previewLatLon);
    input.addEventListener("blur", previewLatLon);
  });
}

async function initializeDataset() {
  const button = document.getElementById("init-btn");
  const logEl = document.getElementById("init-log");
  button.disabled = true;
  logEl.textContent = "Preparing tiling jobs...";
  try {
    const response = await fetch(`${API_BASE}/init`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data.detail || data.message || `Initialisation failed (${response.status})`;
      throw new Error(message);
    }
    if (data.status === "skipped") {
      logEl.textContent = data.message || "Tiles already present.";
    } else {
      logEl.textContent = (data.stderr && data.stderr.trim()) || data.stdout || "Initialisation completed.";
    }
    await loadScenes();
  } catch (err) {
    console.error(err);
    logEl.textContent = err.message || "Failed to initialise dataset.";
  } finally {
    button.disabled = false;
  }
}

async function loadScenes() {
  try {
    const response = await fetch(`${API_BASE}/images`);
    if (!response.ok) {
      throw new Error("Failed to load scenes");
    }
    const payload = await response.json();
    state.scenes = payload.items || [];
    renderSceneList();
  } catch (err) {
    console.error(err);
  }
}

function renderSceneList() {
  const listEl = document.getElementById("scene-list");
  listEl.innerHTML = "";
  if (!state.scenes.length) {
    const empty = document.createElement("li");
    empty.textContent = "No scenes indexed yet. Initialise the dataset.";
    empty.classList.add("empty");
    listEl.appendChild(empty);
    return;
  }

  state.scenes.forEach((scene) => {
    const item = document.createElement("li");
    item.innerHTML = `<button>${scene.scene_id || scene.source}</button>`;
    item.addEventListener("click", () => selectScene(scene, item));
    if (state.currentScene && state.currentScene.scene_id === scene.scene_id) {
      item.classList.add("active");
    }
    listEl.appendChild(item);
  });

  if (!state.currentScene && state.scenes.length) {
    const firstItem = listEl.firstElementChild;
    selectScene(state.scenes[0], firstItem);
  }
}

function selectScene(scene, listItem) {
  state.currentScene = scene;
  document
    .querySelectorAll("#scene-list li")
    .forEach((element) => element.classList.remove("active"));
  if (listItem) {
    listItem.classList.add("active");
  }
  updateSceneDetails(scene);
  plotScene(scene);
  loadScenePreview(scene);
  const cropImg = document.getElementById("crop-image");
  if (cropImg.dataset.url) {
    URL.revokeObjectURL(cropImg.dataset.url);
    delete cropImg.dataset.url;
  }
  cropImg.src = "";
  cropImg.alt = "Crop preview";
}

function updateSceneDetails(scene) {
  document.getElementById("scene-title").textContent = scene.scene_id || scene.source;
  const details = [];
  if (scene.target_code) {
    details.push(`Target code: <strong>${scene.target_code}</strong>`);
  }
  if (scene.orbit_number) {
    details.push(`Orbit: <strong>${scene.orbit_number}</strong>`);
  }
  if (scene.image_size) {
    details.push(
      `Dimensions: ${scene.image_size.width.toLocaleString()} × ${scene.image_size.height.toLocaleString()} px`
    );
  }
  if (scene.tile_size) {
    details.push(`Tile size: ${scene.tile_size}px`);
  }
  if (!scene.bounds) {
    details.push("No geospatial metadata embedded — install pvl and re-run init for lat/lon support.");
  }

  document.getElementById("scene-meta").innerHTML = details.join(" · ");
  document.getElementById("download-full").disabled = false;
  document.getElementById("crop-btn").disabled = !scene.bounds;

  if (scene.bounds) {
    document.getElementById("min-lat").value = scene.bounds.min_lat;
    document.getElementById("max-lat").value = scene.bounds.max_lat;
    document.getElementById("min-lon").value = scene.bounds.west_lon;
    document.getElementById("max-lon").value = scene.bounds.east_lon;
  } else {
    document.getElementById("min-lat").value = "";
    document.getElementById("max-lat").value = "";
    document.getElementById("min-lon").value = "";
    document.getElementById("max-lon").value = "";
  }
}

function plotScene(scene) {
  state.overlays.clearLayers();
  if (state.cropOverlay) {
    state.map.removeLayer(state.cropOverlay);
    state.cropOverlay = null;
  }
  if (!scene.bounds) {
    setMapAvailability(false, "Map preview unavailable — geospatial metadata missing for this scene.");
    return;
  }
  setMapAvailability(true);
  const width = scene.image_size.width;
  const height = scene.image_size.height;
  const latRange = scene.bounds.max_lat - scene.bounds.min_lat;
  const lonRange = scene.bounds.east_lon - scene.bounds.west_lon;

  scene.tiles.forEach((tile) => {
    const tileTop = scene.bounds.max_lat - (tile.y / height) * latRange;
    const tileBottom = scene.bounds.max_lat - ((tile.y + tile.height) / height) * latRange;
    const tileWest = scene.bounds.west_lon + (tile.x / width) * lonRange;
    const tileEast = scene.bounds.west_lon + ((tile.x + tile.width) / width) * lonRange;

    const bounds = [
      [tileBottom, tileWest],
      [tileTop, tileEast],
    ];
    const tileUrl = `${API_BASE}/images/${encodeURIComponent(scene.scene_id)}/tiles/${tile.row}/${tile.col}`;
    const overlay = L.imageOverlay(tileUrl, bounds, { opacity: 0.92 });
    state.overlays.addLayer(overlay);
  });

  const sw = [scene.bounds.min_lat, scene.bounds.west_lon];
  const ne = [scene.bounds.max_lat, scene.bounds.east_lon];
  state.map.fitBounds([sw, ne], { padding: [20, 20] });
  setTimeout(() => state.map.invalidateSize(), 60);
}

async function loadScenePreview(scene) {
  const img = document.getElementById("scene-preview");
  if (!scene) {
    clearScenePreview(img, "Select a scene");
    return;
  }
  const sceneId = scene.scene_id || scene.source;
  if (!sceneId) {
    clearScenePreview(img, "Preview unavailable");
    return;
  }
  img.dataset.loadingScene = sceneId;
  const cachedUrl = state.previewCache.get(sceneId);
  if (cachedUrl) {
    applyScenePreview(img, sceneId, cachedUrl);
    return;
  }
  clearScenePreview(img, "Loading preview...", { preserveLoading: true });
  const endpoints = [];
  if (scene.preview_available && state.previewEndpointAvailable !== false) {
    endpoints.push("preview");
  }
  endpoints.push("full");
  try {
    const url = await fetchScenePreview(sceneId, endpoints);
    if (img.dataset.loadingScene !== sceneId) {
      URL.revokeObjectURL(url);
      return;
    }
    state.previewCache.set(sceneId, url);
    applyScenePreview(img, sceneId, url);
  } catch (err) {
    console.error(err);
    if (img.dataset.loadingScene === sceneId) {
      const fallbackUrl = buildTilePreviewUrl(scene);
      if (fallbackUrl) {
        state.previewCache.set(sceneId, fallbackUrl);
        applyScenePreview(img, sceneId, fallbackUrl);
      } else {
        clearScenePreview(img, "Preview unavailable");
      }
    }
  }
}

async function fetchScenePreview(sceneId, endpoints) {
  const sequence = Array.isArray(endpoints) && endpoints.length ? endpoints : ["full"];
  let lastError;
  for (const endpoint of sequence) {
    try {
      const response = await fetch(`${API_BASE}/images/${encodeURIComponent(sceneId)}/${endpoint}`);
      if (!response.ok) {
        lastError = new Error(`Endpoint ${endpoint} responded with ${response.status}`);
        if (endpoint === "preview" && response.status === 404) {
          state.previewEndpointAvailable = false;
        }
        continue;
      }
      if (endpoint === "preview") {
        state.previewEndpointAvailable = true;
      }
      const blob = await response.blob();
      return URL.createObjectURL(blob);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Preview request failed");
}

function applyScenePreview(img, sceneId, url) {
  img.src = url;
  img.alt = `${sceneId} preview`;
  img.dataset.url = url;
  img.dataset.displayedScene = sceneId;
  delete img.dataset.loadingScene;
}

function clearScenePreview(img, altText, options = {}) {
  const { preserveLoading = false } = options;
  if (img.dataset.url && !Array.from(state.previewCache.values()).includes(img.dataset.url)) {
    URL.revokeObjectURL(img.dataset.url);
  }
  delete img.dataset.displayedScene;
  if (!preserveLoading) {
    delete img.dataset.loadingScene;
  }
  delete img.dataset.url;
  img.removeAttribute("src");
  if (altText) {
    img.alt = altText;
  }
}

function buildTilePreviewUrl(scene) {
  if (!scene?.tiles?.length) {
    return null;
  }
  const firstTile = scene.tiles.find((tile) => typeof tile.row === "number" && typeof tile.col === "number");
  if (!firstTile || !scene.scene_id) {
    return null;
  }
  return `${API_BASE}/images/${encodeURIComponent(scene.scene_id)}/tiles/${firstTile.row}/${firstTile.col}`;
}

function setMapAvailability(enabled, message) {
  if (!state.mapContainer || !state.mapPlaceholder) {
    return;
  }
  if (message) {
    state.mapPlaceholder.textContent = message;
  }
  const wasDisabled = state.mapContainer.classList.contains("map-disabled");
  if (enabled) {
    state.mapContainer.classList.remove("map-disabled");
    if (wasDisabled && state.map) {
      setTimeout(() => state.map.invalidateSize(), 0);
    }
  } else {
    state.mapContainer.classList.add("map-disabled");
  }
}

function highlightCrop(minLat, minLon, maxLat, maxLon) {
  if (!state.map) {
    return;
  }
  if (state.mapContainer?.classList.contains("map-disabled")) {
    return;
  }
  const boundsInfo = state.currentScene?.bounds;
  const south = Math.min(minLat, maxLat);
  const north = Math.max(minLat, maxLat);
  const west = Math.min(minLon, maxLon);
  const east = Math.max(minLon, maxLon);
  const latSpan = Math.abs(maxLat - minLat);
  const lonSpan = Math.abs(maxLon - minLon);
  const minLatPadding = boundsInfo ? Math.abs(boundsInfo.max_lat - boundsInfo.min_lat) * 0.01 : 0.05;
  const minLonPadding = boundsInfo ? Math.abs(boundsInfo.east_lon - boundsInfo.west_lon) * 0.01 : 0.05;
  if (state.cropOverlay) {
    state.map.removeLayer(state.cropOverlay);
  }
  if (latSpan < 1e-6 && lonSpan < 1e-6) {
    state.cropOverlay = L.circleMarker([south, west], {
      radius: 8,
      color: "#f48c06",
      weight: 2,
      fillColor: "#f48c06",
      fillOpacity: 0.75,
    }).addTo(state.map);
    state.map.setView([south, west], Math.max(state.map.getZoom(), 8));
    return;
  }
  const adjustedSouth = latSpan < 1e-6 ? south - minLatPadding / 2 : south;
  const adjustedNorth = latSpan < 1e-6 ? north + minLatPadding / 2 : north;
  const adjustedWest = lonSpan < 1e-6 ? west - minLonPadding / 2 : west;
  const adjustedEast = lonSpan < 1e-6 ? east + minLonPadding / 2 : east;
  const bounds = [
    [adjustedSouth, adjustedWest],
    [adjustedNorth, adjustedEast],
  ];
  state.cropOverlay = L.rectangle(bounds, {
    color: "#f48c06",
    weight: 2,
    fillOpacity: 0.18,
  }).addTo(state.map);
  state.map.fitBounds(bounds, { padding: [20, 20] });
}

function previewLatLon() {
  if (!state.currentScene || !state.currentScene.bounds) {
    return;
  }
  if (state.mapContainer?.classList.contains("map-disabled")) {
    return;
  }
  const minLat = parseFloat(document.getElementById("min-lat").value);
  const minLon = parseFloat(document.getElementById("min-lon").value);
  const maxLat = parseFloat(document.getElementById("max-lat").value);
  const maxLon = parseFloat(document.getElementById("max-lon").value);
  const latValues = [minLat, maxLat].filter((value) => !Number.isNaN(value));
  const lonValues = [minLon, maxLon].filter((value) => !Number.isNaN(value));
  if (!latValues.length || !lonValues.length) {
    if (state.cropOverlay) {
      state.map.removeLayer(state.cropOverlay);
      state.cropOverlay = null;
    }
    return;
  }
  let normMinLat = Math.min(...latValues);
  let normMaxLat = Math.max(...latValues);
  let normMinLon = Math.min(...lonValues);
  let normMaxLon = Math.max(...lonValues);
  if (latValues.length === 1) {
    normMaxLat = normMinLat;
  }
  if (lonValues.length === 1) {
    normMaxLon = normMinLon;
  }
  highlightCrop(normMinLat, normMinLon, normMaxLat, normMaxLon);
}

async function onSearch(event) {
  event.preventDefault();
  const input = document.getElementById("search-input");
  const resultsEl = document.getElementById("search-results");
  const query = input.value.trim();
  if (!query) {
    resultsEl.innerHTML = "";
    return;
  }
  resultsEl.innerHTML = "Searching index...";
  try {
    const response = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
    if (!response.ok) {
      throw new Error("Search failed");
    }
    const data = await response.json();
    if (!data.count) {
      resultsEl.innerHTML = "No matching scenes in HiRISE index.";
      return;
    }
    resultsEl.innerHTML = data.items
      .map(
        (item) => `
        <div class="card">
          <div><strong>${item.filename}</strong></div>
          <div>Target: ${item.target_code} | Orbit folder: ${item.orbit_folder || "N/A"}</div>
          <div><a href="${item.url}" target="_blank" rel="noopener">Open in PDS</a> ${item.verified ? "✅" : "⚠️"}</div>
        </div>`
      )
      .join("");
  } catch (err) {
    console.error(err);
    resultsEl.innerHTML = "Search request failed.";
  }
}

async function onCropRequest(event) {
  event.preventDefault();
  if (!state.currentScene || !state.currentScene.bounds) {
    return;
  }
  const minLat = parseFloat(document.getElementById("min-lat").value);
  const minLon = parseFloat(document.getElementById("min-lon").value);
  const maxLat = parseFloat(document.getElementById("max-lat").value);
  const maxLon = parseFloat(document.getElementById("max-lon").value);

  if ([minLat, minLon, maxLat, maxLon].some(Number.isNaN)) {
    return;
  }

  const normMinLat = Math.min(minLat, maxLat);
  const normMaxLat = Math.max(minLat, maxLat);
  const normMinLon = Math.min(minLon, maxLon);
  const normMaxLon = Math.max(minLon, maxLon);

  highlightCrop(normMinLat, normMinLon, normMaxLat, normMaxLon);

  const url = `${API_BASE}/images/${encodeURIComponent(
    state.currentScene.scene_id
  )}/crop?min_lat=${normMinLat}&min_lon=${normMinLon}&max_lat=${normMaxLat}&max_lon=${normMaxLon}`;
  const img = document.getElementById("crop-image");
  img.alt = "Loading crop...";
  img.src = "";

  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("Crop request failed");
    }
    const blob = await response.blob();
    const previewUrl = URL.createObjectURL(blob);
    if (img.dataset.url) {
      URL.revokeObjectURL(img.dataset.url);
    }
    img.dataset.url = previewUrl;
    img.src = previewUrl;
    img.alt = "Crop preview";
  } catch (err) {
    console.error(err);
    img.alt = "Failed to load crop.";
  }
}

async function downloadFullScene() {
  if (!state.currentScene) {
    return;
  }
  try {
    const response = await fetch(`${API_BASE}/images/${encodeURIComponent(state.currentScene.scene_id)}/full`);
    if (!response.ok) {
      throw new Error("Download failed");
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `${state.currentScene.scene_id}_full.jpg`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error(err);
  }
}
