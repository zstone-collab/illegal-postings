// SF Pastebin — app.js

const THEME_COLORS = {
  "🗳️ Political":           "#c1272d",
  "🎨 Art & Culture":       "#9b59b6",
  "🎵 Events":              "#3498db",
  "🚀 Startups":            "#1abc9c",
  "🔧 Services":            "#e67e22",
  "💊 Drugs":               "#27ae60",
  "💕 Dating":              "#e84393",
  "🐾 Lost & Found":        "#f39c12",
  "👁️ Weird & Unexplained": "#ff6fd8",
  "❓ Unclear":              "#95a5a6",
};

const THEME_EMOJI = {
  "🗳️ Political":           "🗳️",
  "🎨 Art & Culture":       "🎨",
  "🎵 Events":              "🎵",
  "🚀 Startups":            "🚀",
  "🔧 Services":            "🔧",
  "💊 Drugs":               "💊",
  "💕 Dating":              "💕",
  "🐾 Lost & Found":        "🐾",
  "👁️ Weird & Unexplained": "👁️",
  "❓ Unclear":              "❓",
};

function formatDate(ticket) {
  if (ticket.date_iso) {
    try {
      const d = new Date(ticket.date_iso);
      return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    } catch {}
  }
  return ticket.date || "";
}

function themeKey(themeStr) {
  if (!themeStr) return null;
  for (const k of Object.keys(THEME_COLORS)) {
    const emoji = k.split(" ")[0];
    if (themeStr.startsWith(emoji)) return k;
  }
  const lower = themeStr.toLowerCase();
  if (lower.includes("politic") || lower.includes("activism") || lower.includes("social justice") || lower.includes("environment")) return "🗳️ Political";
  if (lower.includes("art") || lower.includes("culture") || lower.includes("mural")) return "🎨 Art & Culture";
  if (lower.includes("event") || lower.includes("music") || lower.includes("concert") || lower.includes("party")) return "🎵 Events";
  if (lower.includes("startup") || lower.includes("tech") || lower.includes("app")) return "🚀 Startups";
  if (lower.includes("service") || lower.includes("housing") || lower.includes("rent")) return "🔧 Services";
  if (lower.includes("drug") || lower.includes("cannabis") || lower.includes("weed") || lower.includes("dispensary")) return "💊 Drugs";
  if (lower.includes("dating") || lower.includes("personal") || lower.includes("missed connection") || lower.includes("escort")) return "💕 Dating";
  if (lower.includes("lost") || lower.includes("found") || lower.includes("pet") || lower.includes("missing")) return "🐾 Lost & Found";
  if (lower.includes("weird") || lower.includes("unexplained") || lower.includes("conspiracy")) return "👁️ Weird & Unexplained";
  if (lower.includes("unclear") || lower.includes("mysterious")) return "❓ Unclear";
  return null;
}

// ── Admin mode ────────────────────────────────────────────
const params = new URLSearchParams(location.search);
const ADMIN_PW = params.get("admin"); // ?admin=yourpassword
const IS_ADMIN = !!ADMIN_PW;
const selectedIds = new Set();

let allTickets = [];
let activeFilter = "all";
let currentView = "map";
let gridDensity = localStorage.getItem("gridDensity") || "large";
let map, markerLayer;

function setDensity(d) {
  gridDensity = d;
  localStorage.setItem("gridDensity", d);
  document.querySelectorAll(".density-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.density === d)
  );
  document.getElementById("grid").classList.toggle("dense", d === "small");
}

// ── Boot ──────────────────────────────────────────────────
async function init() {
  try {
    const res = await fetch("/data/tickets.json?t=" + Date.now(), { cache: "no-store" });
    const raw = await res.json();
    allTickets = raw.filter(t => t.analyzed && !t.skip && t.lat && t.lng && themeKey(t.theme));
  } catch {
    allTickets = getSampleData();
  }

  if (IS_ADMIN) setupAdminBar();
  buildFilters();
  buildStats();
  initMap();
  renderMap(allTickets);
  setDensity(gridDensity);
}

// ── Admin bar ─────────────────────────────────────────────
function setupAdminBar() {
  const bar = document.createElement("div");
  bar.id = "admin-bar";
  bar.innerHTML = `
    <span>🔐 Admin</span>
    <button onclick="openReview()">Review queue</button>
    <span id="admin-selected">0 selected</span>
    <button id="admin-delete-btn" onclick="bulkDelete()" disabled>Delete</button>
    <button onclick="selectAll()">Select all</button>
    <button onclick="clearSelection()">Clear</button>
  `;
  document.querySelector("header").after(bar);
}

async function openReview() {
  const modal = document.getElementById("review-modal");
  const list = document.getElementById("review-list");
  list.innerHTML = "<p class='review-empty'>Loading…</p>";
  modal.classList.remove("hidden");
  try {
    const res = await fetch(`/api/tickets/review-queue?pw=${ADMIN_PW}`);
    const items = await res.json();
    if (!items.length) {
      list.innerHTML = "<p class='review-empty'>✨ Nothing to review — Claude was confident about everything.</p>";
      return;
    }
    list.innerHTML = items.map(renderReviewItem).join("");
  } catch (e) {
    list.innerHTML = `<p class='review-empty'>Error: ${e.message}</p>`;
  }
}

function renderReviewItem(ticket) {
  const themes = Object.keys(THEME_COLORS);
  const buttons = themes.map(t =>
    `<button class="review-cat-btn" data-theme="${t}" onclick="categorize('${ticket.id}', '${t.replace(/'/g, "\\'")}', this)">${t}</button>`
  ).join("") +
  `<button class="review-cat-btn review-skip" onclick="categorize('${ticket.id}', '🚫 Skip', this)">🚫 Skip</button>`;

  return `
    <div class="review-item" data-id="${ticket.id}">
      <img src="${ticket.image_url}" alt="" loading="lazy" />
      <div class="review-details">
        <div class="review-meta">
          <span>${ticket.address || "?"}</span>
          <span class="review-confidence">AI guess: ${ticket.theme} · ${Math.round((ticket.confidence||0)*100)}% confident</span>
        </div>
        <blockquote class="review-text">"${ticket.extracted_text}"</blockquote>
        <div class="review-categories">${buttons}</div>
      </div>
    </div>
  `;
}

async function categorize(id, theme, btn) {
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await fetch(`/api/tickets/${id}/categorize?pw=${ADMIN_PW}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({theme, pw: ADMIN_PW}),
    });
    if (!res.ok) throw new Error(await res.text());
    const item = document.querySelector(`.review-item[data-id="${id}"]`);
    if (item) item.classList.add("review-done");
    // Update in-memory and rerender
    const local = allTickets.find(t => t.id === id);
    if (local) { local.theme = theme; local.skip = theme === "🚫 Skip"; }
    rerenderCurrent();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = theme;
    alert("Error: " + e.message);
  }
}

function closeReview(e) {
  if (e && e.target.id !== "review-modal" && !e.target.classList.contains("lb-close")) return;
  document.getElementById("review-modal").classList.add("hidden");
}

function updateAdminBar() {
  const el = document.getElementById("admin-selected");
  const btn = document.getElementById("admin-delete-btn");
  if (!el) return;
  el.textContent = `${selectedIds.size} selected`;
  btn.disabled = selectedIds.size === 0;
}

function toggleSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  updateAdminBar();
  document.querySelectorAll(`.card[data-id="${id}"]`).forEach(c =>
    c.classList.toggle("selected", selectedIds.has(id))
  );
}

function selectAll() {
  const filtered = getFiltered();
  filtered.forEach(t => selectedIds.add(t.id));
  updateAdminBar();
  document.querySelectorAll(".card").forEach(c =>
    c.classList.add("selected")
  );
}

function clearSelection() {
  selectedIds.clear();
  updateAdminBar();
  document.querySelectorAll(".card").forEach(c => c.classList.remove("selected"));
}

async function deleteSingle(id) {
  if (!confirm("Delete this posting?")) return;
  const res = await fetch(`/api/tickets/${id}?pw=${ADMIN_PW}`, { method: "DELETE" });
  if (res.ok) {
    allTickets = allTickets.filter(t => t.id !== id);
    selectedIds.delete(id);
    updateAdminBar();
    rerenderCurrent();
  } else {
    alert("Delete failed: " + (await res.text()));
  }
}

async function bulkDelete() {
  if (!selectedIds.size) return;
  if (!confirm(`Delete ${selectedIds.size} postings?`)) return;
  const res = await fetch(`/api/tickets/bulk-delete?pw=${ADMIN_PW}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [...selectedIds], pw: ADMIN_PW }),
  });
  if (res.ok) {
    const { deleted } = await res.json();
    allTickets = allTickets.filter(t => !selectedIds.has(t.id));
    selectedIds.clear();
    updateAdminBar();
    rerenderCurrent();
    alert(`Deleted ${deleted} postings.`);
  } else {
    alert("Bulk delete failed: " + (await res.text()));
  }
}

function rerenderCurrent() {
  const filtered = getFiltered();
  if (currentView === "map") renderMap(filtered);
  else renderGrid(filtered);
}

// ── Filters ───────────────────────────────────────────────
function buildFilters() {
  const container = document.getElementById("filters");
  // Keep the existing "All" button, remove everything after it
  const allBtn = container.querySelector('[data-theme="all"]');
  container.innerHTML = "";
  container.appendChild(allBtn);

  // Count tickets per theme; only show categories with at least 1 posting
  const counts = {};
  allTickets.forEach(t => {
    const k = themeKey(t.theme);
    if (!k || k === "🚫 Skip") return;
    counts[k] = (counts[k] || 0) + 1;
  });

  // Sort by count descending, then alphabetical
  const themes = Object.entries(counts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  themes.forEach(([theme, count]) => {
    const btn = document.createElement("button");
    btn.className = "filter-btn";
    btn.dataset.theme = theme;
    btn.innerHTML = `${theme} <span class="filter-count">${count}</span>`;
    btn.onclick = () => setFilter(theme);
    container.appendChild(btn);
  });

  // Wire the "All" button (it wasn't before)
  allBtn.onclick = () => setFilter("all");
  allBtn.innerHTML = `All <span class="filter-count">${allTickets.length}</span>`;
}

function getFiltered() {
  return activeFilter === "all"
    ? allTickets
    : allTickets.filter(t => themeKey(t.theme) === activeFilter);
}

function setFilter(theme) {
  activeFilter = theme;
  document.querySelectorAll(".filter-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.theme === theme)
  );
  rerenderCurrent();
}

function buildStats() {
  const el = document.getElementById("stats");
  const byTheme = {};
  allTickets.forEach(t => { const k = themeKey(t.theme); byTheme[k] = (byTheme[k] || 0) + 1; });
  const top = Object.entries(byTheme).sort((a, b) => b[1] - a[1]).slice(0, 3);
  el.innerHTML = `<b>${allTickets.length}</b> postings mapped<br>` +
    top.map(([k, v]) => `${k.split(" ")[0]} ${v}`).join(" · ");
}

// ── View toggle ───────────────────────────────────────────
function setView(v) {
  currentView = v;
  document.getElementById("map-view").classList.toggle("hidden", v !== "map");
  document.getElementById("grid-view").classList.toggle("hidden", v !== "grid");
  document.getElementById("btn-map").classList.toggle("active", v === "map");
  document.getElementById("btn-grid").classList.toggle("active", v === "grid");
  if (v === "map") { map.invalidateSize(); renderMap(getFiltered()); }
  else renderGrid(getFiltered());
}

// ── Map ───────────────────────────────────────────────────
function initMap() {
  // Tight bounds on SF proper (excludes Marin, East Bay, Peninsula south of Daly City)
  const SF_BOUNDS = L.latLngBounds([37.705, -122.525], [37.835, -122.355]);

  map = L.map("map", {
    center: [37.7749, -122.435],
    zoom: 13,
    minZoom: 12,
    maxZoom: 18,
    maxBounds: SF_BOUNDS,
    maxBoundsViscosity: 1.0,
  });
  map.fitBounds(SF_BOUNDS);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/">CARTO</a>',
    bounds: SF_BOUNDS,
    minZoom: 12,
    maxZoom: 18,
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}

function makeIcon(ticket) {
  const key = themeKey(ticket.theme);
  const color = THEME_COLORS[key] || "#888";
  const emoji = THEME_EMOJI[key] || "📌";
  return L.divIcon({
    className: "",
    html: `<div class="custom-marker" style="background:${color}"><span>${emoji}</span></div>`,
    iconSize: [36, 36],
    iconAnchor: [10, 36],
    popupAnchor: [8, -36],
  });
}

function renderMap(tickets) {
  markerLayer.clearLayers();
  tickets.forEach(ticket => {
    const marker = L.marker([ticket.lat, ticket.lng], { icon: makeIcon(ticket) });
    const imgHtml = ticket.image_url
      ? `<img class="popup-img" src="${ticket.image_url}" alt="" loading="lazy" />`
      : `<div class="popup-img-placeholder">${THEME_EMOJI[themeKey(ticket.theme)] || "📌"}</div>`;
    const textPreview = ticket.extracted_text
      ? `<div class="popup-text">"${ticket.extracted_text.slice(0, 80)}${ticket.extracted_text.length > 80 ? "…" : ""}"</div>`
      : "";
    marker.bindPopup(`
      <div class="popup-wrap" onclick="openLightbox('${ticket.id}')">
        ${imgHtml}
        <div class="popup-body">
          <div class="popup-theme">${ticket.theme || "❓ Mysterious"}</div>
          <div class="popup-addr">${ticket.address || "Unknown location"}</div>
          ${textPreview}
          <div class="popup-tap">Tap to expand →</div>
        </div>
      </div>
    `, { maxWidth: 240 });
    markerLayer.addLayer(marker);
  });
}

// ── Grid ──────────────────────────────────────────────────
function renderGrid(tickets) {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  tickets.forEach(ticket => {
    const card = document.createElement("div");
    card.className = "card" + (selectedIds.has(ticket.id) ? " selected" : "");
    card.dataset.id = ticket.id;

    const imgHtml = ticket.image_url
      ? `<img class="card-img" src="${ticket.image_url}" alt="" loading="lazy" />`
      : `<div class="card-img-placeholder">${THEME_EMOJI[themeKey(ticket.theme)] || "📌"}</div>`;

    // Text extraction — shown below image as main content
    const hasText = ticket.extracted_text && ticket.extracted_text.trim();
    const textBlock = hasText
      ? `<div class="card-extracted">"${ticket.extracted_text}"</div>`
      : `<div class="card-extracted muted">No text detected</div>`;

    const adminControls = IS_ADMIN ? `
      <div class="admin-controls">
        <label class="admin-check" onclick="event.stopPropagation()">
          <input type="checkbox" ${selectedIds.has(ticket.id) ? "checked" : ""}
            onchange="toggleSelect('${ticket.id}')" />
          Select
        </label>
        <button class="admin-del-btn" onclick="event.stopPropagation(); deleteSingle('${ticket.id}')">🗑 Delete</button>
      </div>` : "";

    const dateLabel = formatDate(ticket);
    const cleanTheme = themeKey(ticket.theme) || ticket.theme || "";
    card.innerHTML = `
      ${imgHtml}
      <div class="card-body">
        <div class="card-meta">
          <span class="card-theme">${cleanTheme}</span>
          ${dateLabel ? `<span class="card-date">${dateLabel}</span>` : ""}
        </div>
        <div class="card-addr">${ticket.address || "Unknown location"}</div>
        ${textBlock}
        ${adminControls}
      </div>
    `;

    if (!IS_ADMIN) card.onclick = () => openLightbox(ticket.id);
    grid.appendChild(card);
  });
}

// ── Lightbox ──────────────────────────────────────────────
function openLightbox(id) {
  const ticket = allTickets.find(t => t.id === id);
  if (!ticket) return;
  const img = document.getElementById("lb-img");
  img.src = ticket.image_url || "";
  img.style.display = ticket.image_url ? "block" : "none";
  img.style.cursor = ticket.image_url ? "zoom-in" : "default";
  img.onclick = (e) => {
    e.stopPropagation();
    if (ticket.image_url) window.open(ticket.image_url, "_blank", "noopener,noreferrer");
  };
  document.getElementById("lb-theme").textContent = ticket.theme || "";
  document.getElementById("lb-address").textContent = ticket.address || "Unknown location";
  document.getElementById("lb-text").textContent = ticket.extracted_text || "(no text detected)";
  document.getElementById("lb-text-wrap").style.opacity = ticket.extracted_text ? "1" : "0.4";
  document.getElementById("lb-status").textContent = `${ticket.status || ""} ${ticket.date || ""}`.trim();
  const link = document.getElementById("lb-link");
  link.href = ticket.url || `https://san-francisco2-production.spotmobile.net/tickets/${ticket.id}`;
  link.onclick = (e) => e.stopPropagation();
  document.getElementById("lightbox").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeLightbox(e) {
  if (e && e.target !== document.getElementById("lightbox") && !e.target.classList.contains("lb-close")) return;
  document.getElementById("lightbox").classList.add("hidden");
  document.body.style.overflow = "";
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeLightbox({ target: document.getElementById("lightbox") });
});

// ── Sample data ───────────────────────────────────────────
function getSampleData() {
  return [
    { id: "sample1", address: "Valencia St & 16th St", lat: 37.7649, lng: -122.4215,
      image_url: "https://spot-sf-res.cloudinary.com/image/upload/v1769490778/san-francisco/production/t2zlmu7dsjyaucavoofv.jpg",
      theme: "🗳️ Political", extracted_text: "The United Front Against Fascism",
      commentary: "A classic Bay Area power move — wheat paste first, ask questions later.",
      status: "CLOSED", date: "2 days ago", url: "#", analyzed: true, skip: false },
    { id: "sample2", address: "Mission St & 24th St", lat: 37.7523, lng: -122.4182,
      image_url: null, theme: "👁️ Weird & Unexplained",
      extracted_text: "THE OWLS ARE NOT WHAT THEY SEEM",
      commentary: "Twin Peaks: San Francisco edition. The city's pigeons are also suspects.",
      status: "OPEN", date: "1 hour ago", url: "#", analyzed: true, skip: false },
    { id: "sample3", address: "Market St & Castro St", lat: 37.7620, lng: -122.4350,
      image_url: null, theme: "🎵 Events & Music",
      extracted_text: "DRAG EXTRAVAGANZA · FREE · SAT 9PM · THE STUD",
      commentary: "San Francisco's weekly reminder that life is short and sequins are forever.",
      status: "CLOSED", date: "3 days ago", url: "#", analyzed: true, skip: false },
    { id: "sample4", address: "Folsom St & 7th St", lat: 37.7751, lng: -122.4072,
      image_url: null, theme: "🚀 Startups & Tech",
      extracted_text: "DISRUPTING HOMELESSNESS · DOWNLOAD THE APP",
      commentary: "Nothing says Silicon Valley like VC-funded solutions to problems Silicon Valley helped create.",
      status: "OPEN", date: "5h ago", url: "#", analyzed: true, skip: false },
    { id: "sample5", address: "Haight St & Ashbury St", lat: 37.7692, lng: -122.4481,
      image_url: null, theme: "🐾 Lost & Found",
      extracted_text: "MISSING: LUIGI · Orange tabby · Answers to 'Weege' · REWARD",
      commentary: "Luigi's been missing since Tuesday. The neighborhood raccoons have no comment.",
      status: "OPEN", date: "2h ago", url: "#", analyzed: true, skip: false },
  ];
}

init();
