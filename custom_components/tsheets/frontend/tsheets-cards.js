/*
 * TSheets (QuickBooks Time) dashboard cards for Home Assistant.
 * Loaded automatically by the integration - nothing to add under Resources.
 *
 *   type: custom:tsheets-time-clock-card   PIN keypad time clock (pop-up, or inline on a tablet)
 *   type: custom:tsheets-whos-in-card      who's on the clock right now
 */
const CARDS_VERSION = "0.0.2";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";

const haptic = (type = "light") =>
  window.dispatchEvent(new CustomEvent("haptic", { detail: type, bubbles: true, composed: true }));

/** Finds our entity even if the user renamed it (falls back to the default id). */
const findEntity = (hass, wanted, test) => {
  if (!hass) return wanted;
  if (hass.states[wanted]) return wanted;
  const hit = Object.keys(hass.states).find((id) => id.startsWith("sensor.") && test(hass.states[id]));
  return hit || wanted;
};
const isKiosk = (s) => s.attributes && "display" in s.attributes && "employee_name" in s.attributes;
const isClockedIn = (s) => s.attributes && Array.isArray(s.attributes.employees);

/* ---------------------------------------------------------------- avatars */

function avatarSvg(gender) {
  const stroke = gender === "female" ? "#8C3B5E" : gender === "male" ? "#26547C" : "#4B5B6B";
  const fill = gender === "female" ? "#F6C1D2" : gender === "male" ? "#8FD0EF" : "#C9D3DD";
  const head =
    gender === "female"
      ? `<path d="M20.5 37 V25 a11.5 11.5 0 0 1 23 0 V37 q-3.5 2.5-7 0 H27.5 q-3.5 2.5-7 0z"
               fill="${fill}" stroke="${stroke}" stroke-width="2.4" stroke-linejoin="round"/>
         <circle cx="32" cy="25" r="8" fill="#fff" stroke="${stroke}" stroke-width="2.6"/>`
      : `<circle cx="34.5" cy="26.5" r="8.5" fill="${fill}"/>
         <circle cx="32" cy="24.5" r="8.5" fill="none" stroke="${stroke}" stroke-width="2.6"/>`;
  return `<svg viewBox="0 0 64 64" width="100%" height="100%" style="display:block">
    <circle cx="32" cy="32" r="31" fill="#fff"/>
    <path d="M45.5 8.6 A27 27 0 1 0 56.5 20.6" fill="none" stroke="${stroke}" stroke-width="2.6" stroke-linecap="round"/>
    <path d="M20 53.5 q2.5-12.5 15-12.5 t15 12.5 a27 27 0 0 1-30 0z" fill="${fill}"/>
    ${head}
    <path d="M16.5 50.5 q2.5-13 15.5-13 t15.5 13" fill="none" stroke="${stroke}" stroke-width="2.6" stroke-linecap="round"/>
  </svg>`;
}

/* ======================================================= Who's In card */

class TSheetsWhosInCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "theme",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "auto", label: "Follow my dashboard theme" },
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark glass" },
              ],
            },
          },
        },
        { name: "hide_when_empty", selector: { boolean: {} } },
        { name: "show_job", selector: { boolean: {} } },
        { name: "show_time", selector: { boolean: {} } },
        { name: "entity", selector: { entity: { domain: "sensor" } } },
      ],
      computeLabel: (s) =>
        ({
          title: "Title (optional)",
          theme: "Style",
          hide_when_empty: "Hide the card when nobody is on the clock",
          show_job: "Show job / customer",
          show_time: "Show first clock-in time today",
          entity: "Clocked-in sensor (leave empty for automatic)",
        })[s.name],
    };
  }

  setConfig(config) {
    this._config = {
      theme: "auto",
      hide_when_empty: true,
      show_job: true,
      show_time: true,
      ...config,
    };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._last = undefined;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _setHidden(hidden) {
    if (this.hidden === hidden) return;
    this.hidden = hidden;
    this.style.display = hidden ? "none" : "";
    this.dispatchEvent(new CustomEvent("card-visibility-changed", { detail: { value: !hidden }, bubbles: true, composed: true }));
  }

  _render() {
    if (!this._config || !this._hass) return;
    const id = findEntity(this._hass, this._config.entity || "sensor.tsheets_clocked_in", isClockedIn);
    const st = this._hass.states[id];
    if (st === this._last && this._rendered) return;
    this._last = st;
    this._rendered = true;

    const c = this._config;
    const list = ((st && st.attributes.employees) || [])
      .slice()
      .sort((a, b) => String(a.first_in || a.since).localeCompare(String(b.first_in || b.since)));

    const editing = this.editMode || this.preview || this.closest?.("hui-card-preview");
    this._setHidden(!list.length && c.hide_when_empty && !editing && !!st);

    const person = (e) => {
      const parts = String(e.name || "").trim().split(/\s+/);
      const first = parts.shift() || "";
      const last = parts.join(" ");
      const pic = e.picture ? `<img src="${esc(e.picture)}" alt="">` : avatarSvg(e.gender);
      const where = e.job || e.customer;
      return `
        <div class="p" title="${esc(e.name)}">
          <div class="nm">${esc(first)}</div>
          <div class="nm">${last ? esc(last) : "&nbsp;"}</div>
          <div class="av">${pic}<span class="dot"></span></div>
          ${c.show_job ? `<div class="job ${where ? "" : "none"}">${esc(where || "No job")}</div>` : ""}
          ${c.show_time ? `<div class="in">In ${esc(fmtTime(e.first_in || e.since))}</div>` : ""}
        </div>`;
    };

    let body;
    if (!st) body = `<div class="empty">Entity ${esc(id)} not found - is the QuickBooks Time integration set up?</div>`;
    else if (!list.length) body = `<div class="empty">No one is clocked in yet</div>`;
    else body = `<div class="row">${list.map(person).join("")}</div>`;

    this.shadowRoot.innerHTML = `
      <style>${WHOS_IN_CSS}</style>
      <ha-card class="t-${esc(c.theme)}">
        ${c.title ? `<div class="title">${esc(c.title)}<span class="count">${list.length}</span></div>` : ""}
        ${body}
      </ha-card>`;
  }
}

const WHOS_IN_CSS = `
  ha-card { display: block; padding: 12px 15px; box-sizing: border-box; overflow: hidden;
    --wi-name: var(--primary-text-color); --wi-job: var(--success-color, #2e7d4f);
    --wi-sub: var(--secondary-text-color); --wi-ring: rgba(76,175,120,0.85); --wi-dotborder: var(--card-background-color, #fff); }
  ha-card.t-light { background: #fff; color: #253746; border: 1px solid rgba(0,0,0,0.08);
    --wi-name: #253746; --wi-job: #2e7d4f; --wi-sub: #6b7a86; --wi-dotborder: #fff; }
  ha-card.t-dark { background: rgba(18,37,32,0.72); border: 1px solid rgba(255,255,255,0.25); box-shadow: none;
    --wi-name: #fff; --wi-job: #a6e8cb; --wi-sub: rgba(255,255,255,0.7); --wi-ring: rgba(111,211,160,0.9); --wi-dotborder: #16302a; }
  .title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 15px; font-weight: 700; color: var(--wi-name); }
  .count { padding: 1px 9px; border-radius: 999px; background: rgba(76,175,120,0.18); color: var(--wi-job); font-size: 12px; }
  .row { display: flex; gap: 4px; overflow-x: auto; padding: 2px; scrollbar-width: none; }
  .row::-webkit-scrollbar { display: none; }
  .p { flex: 1 0 60px; max-width: 92px; min-width: 0; display: flex; flex-direction: column; align-items: center; text-align: center; }
  .nm { max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-size: 11.5px; font-weight: 700; line-height: 1.15; color: var(--wi-name); }
  .av { position: relative; width: 44px; height: 44px; margin: 5px 0 4px; border-radius: 50%; padding: 2px;
    background: #fff; box-shadow: 0 0 0 2px var(--wi-ring); }
  .av img { display: block; width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
  .dot { position: absolute; right: -2px; bottom: 0; width: 10px; height: 10px; border-radius: 50%;
    background: #3ddc84; border: 2px solid var(--wi-dotborder); }
  .job, .in { max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 10px; line-height: 1.3; }
  .job { color: var(--wi-job); }
  .job.none, .in { color: var(--wi-sub); }
  .empty { padding: 18px 0; text-align: center; font-size: 14px; color: var(--wi-sub); }
`;

/* ===================================================== Time Clock card */

const KIOSK_CSS = `
  .k { text-align: center; color: #253746; font-family: var(--paper-font-body1_-_font-family, inherit); }
  .time { font-size: 56px; font-weight: 700; line-height: 1; color: #29483d; }
  .date { font-size: 16px; color: #5f7a70; margin-top: 6px; }
  .dots { margin-top: 22px; }
  .dots span { display: inline-block; width: 20px; height: 20px; margin: 0 9px; border-radius: 50%;
    border: 2px solid #285B42; transition: background .15s; }
  .dots span.f { background: #285B42; }
  .msg { font-size: 17px; margin-top: 14px; min-height: 24px; color: #5f7a70; }
  .msg.err { color: #b5443c; font-weight: 600; }
  .msg.busy { color: #345778; }
  .screen { border-radius: 20px; padding: 16px 8px 18px; transition: background .3s; }
  .screen.in { background: #e3f4e8; border: 1px solid rgba(40,91,66,.25); }
  .screen.out { background: #fbe6e3; border: 1px solid rgba(156,65,56,.25); }
  .screen.in.res { background: #d4efdc; border: 2px solid rgba(40,91,66,.45); }
  .screen.out.res { background: #f9dcd8; border: 2px solid rgba(156,65,56,.45); }
  .photo { display: block; margin: 0 auto; width: 132px; height: 132px; border-radius: 50%; object-fit: cover;
    border: 5px solid #fff; box-shadow: 0 6px 18px rgba(0,0,0,.18); box-sizing: border-box; background: #fff; }
  .initials { width: 132px; height: 132px; margin: 0 auto; border-radius: 50%; background: #fff; color: #285B42;
    font-size: 50px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 6px 18px rgba(0,0,0,.12); }
  .ename { font-size: 26px; font-weight: 700; margin: 12px 0 8px; }
  .status { display: inline-block; margin-top: 14px; padding: 8px 18px; border-radius: 18px; max-width: 88%;
    line-height: 1.35; background: rgba(255,255,255,.75); font-size: 16px; font-weight: 600; }
  .pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
  .acts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
  button { font: inherit; cursor: pointer; border-radius: 18px; height: 62px; border: 1px solid rgba(40,91,66,.14);
    background: rgba(40,91,66,.08); color: #285B42; font-size: 26px; font-weight: 600; -webkit-tap-highlight-color: transparent;
    display: flex; align-items: center; justify-content: center; gap: 10px; transition: transform .08s, filter .15s; }
  button:active { transform: scale(.96); }
  button.clr { background: rgba(156,65,56,.08); border-color: rgba(156,65,56,.14); color: #9C4138; }
  button.bk { background: rgba(52,87,120,.08); border-color: rgba(52,87,120,.14); color: #345778; }
  button.cin, button.cout { height: 54px; border: none; color: #fff; font-size: 17px; font-weight: 700; }
  button.cin { background: #285B42; }
  button.cout { background: #9C4138; }
  button.cancel { grid-column: 1 / -1; height: 48px; font-size: 16px; background: rgba(0,0,0,.05); border-color: rgba(0,0,0,.08); color: #5f6b75; }
  button[disabled] { opacity: .3; pointer-events: none; }
  ha-icon { --mdc-icon-size: 24px; }
`;

const DIALOG_CSS = `
  :host { all: initial; }
  .bg { position: fixed; inset: 0; z-index: 9999; background: rgba(0,0,0,.45); display: flex;
    align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; animation: f .15s ease-out;
    font-family: var(--paper-font-body1_-_font-family, Roboto, "Noto Sans", sans-serif); }
  .dlg { width: 100%; max-width: 440px; max-height: calc(100vh - 32px); overflow: auto; box-sizing: border-box;
    background: #fff; border-radius: 28px; padding: 14px 20px 22px; box-shadow: 0 20px 60px rgba(0,0,0,.3); animation: u .18s ease-out; }
  .hd { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 20px; color: #253746; }
  .x { width: 40px; height: 40px; border: none; background: none; cursor: pointer; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; color: #253746; }
  .x:hover { background: rgba(0,0,0,.06); }
  @keyframes f { from { opacity: 0; } }
  @keyframes u { from { transform: translateY(14px) scale(.98); opacity: 0; } }
`;

class TSheetsTimeClockCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "name", selector: { text: {} } },
        { name: "subtitle", selector: { text: {} } },
        { name: "icon", selector: { icon: {} } },
        { name: "inline", selector: { boolean: {} } },
        { name: "auto_close", selector: { number: { min: 0, max: 120, mode: "box", unit_of_measurement: "s" } } },
        { name: "entity", selector: { entity: { domain: "sensor" } } },
      ],
      computeLabel: (s) =>
        ({
          name: "Name",
          subtitle: "Subtitle",
          icon: "Icon",
          inline: "Show the keypad right on the dashboard (tablet / kiosk) instead of a button",
          auto_close: "Close the pop-up when untouched for (seconds, 0 = never)",
          entity: "Kiosk sensor (leave empty for automatic)",
        })[s.name],
    };
  }

  setConfig(config) {
    this._config = {
      name: "Time Clock",
      subtitle: "Clock in / out",
      icon: "mdi:clock-check-outline",
      inline: false,
      auto_close: 8,
      ...config,
    };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._built = false;
    if (this._hass) this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return this._config && this._config.inline ? 9 : 1;
  }

  getGridOptions() {
    return this._config && this._config.inline
      ? { columns: 12, min_columns: 6, rows: "auto" }
      : { columns: 12, min_columns: 3, rows: "auto" };
  }

  connectedCallback() {
    // Only the clock text changes - never rebuild the buttons under someone's finger.
    this._tick = setInterval(() => {
      const r = this._kioskRoot;
      if (!r) return;
      const now = new Date();
      const t = r.querySelector(".time");
      const d = r.querySelector(".date");
      if (t) t.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      if (d) d.textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    }, 10000);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
    if (!this._config || !this._config.inline) this._close(false);
  }

  get _entityId() {
    return findEntity(this._hass, this._config.entity || "sensor.tsheets_kiosk", isKiosk);
  }

  get _state() {
    return this._hass && this._hass.states[this._entityId];
  }

  _key(key) {
    if (!this._hass) return;
    this._hass.callService("tsheets", "keypad", { key }).catch((e) => console.error("tsheets keypad", e));
  }

  /* ---------- tile / inline shell ---------- */

  _update() {
    if (!this._config || !this._hass) return;
    const c = this._config;
    if (!this._built) {
      this._built = true;
      if (c.inline) {
        this.shadowRoot.innerHTML = `<style>${KIOSK_CSS} ha-card { display: block; padding: 16px; }</style>
          <ha-card><div class="k" id="kiosk"></div></ha-card>`;
        this._kioskRoot = this.shadowRoot.getElementById("kiosk");
        this._bindKiosk(this._kioskRoot);
      } else {
        this.shadowRoot.innerHTML = `
          <style>
            ha-card { height: 64px; display: flex; align-items: center; gap: 14px; padding: 0 16px; cursor: pointer;
              box-sizing: border-box; background: var(--tsheets-tile-bg, #eef7f0); border: 1px solid rgba(40,91,66,.18);
              -webkit-tap-highlight-color: transparent; transition: transform .08s; }
            ha-card:active { transform: scale(.985); }
            .b { width: 40px; height: 40px; flex: none; border-radius: 50%; background: var(--tsheets-accent, #285B42);
              color: #fff; display: flex; align-items: center; justify-content: center; }
            .b ha-icon { --mdc-icon-size: 22px; }
            .n { font-size: 16px; font-weight: 600; color: #1f3a2e; line-height: 1.2; }
            .s { font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: #4f6b5f; }
            .off .s { color: #b5443c; }
          </style>
          <ha-card id="tile" role="button" tabindex="0">
            <div class="b"><ha-icon icon="${esc(c.icon)}"></ha-icon></div>
            <div><div class="n">${esc(c.name)}</div><div class="s" id="sub"></div></div>
          </ha-card>`;
        const tile = this.shadowRoot.getElementById("tile");
        tile.addEventListener("click", () => this._open());
        tile.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && this._open());
      }
    }
    if (!c.inline) {
      const sub = this.shadowRoot.getElementById("sub");
      const ok = !!this._state && this._state.state !== "unavailable";
      sub.textContent = ok ? c.subtitle : "Offline";
      this.shadowRoot.getElementById("tile").classList.toggle("off", !ok);
    }
    this._paintKiosk();
  }

  /* ---------- pop-up ---------- */

  _open() {
    if (this._dlg) return;
    haptic();
    this._key("wake");
    const host = document.createElement("div");
    host.attachShadow({ mode: "open" }).innerHTML = `
      <style>${DIALOG_CSS}${KIOSK_CSS}</style>
      <div class="bg" id="bg">
        <div class="dlg" role="dialog" aria-modal="true" aria-label="${esc(this._config.name)}">
          <div class="hd"><button class="x" id="x" aria-label="Close"><ha-icon icon="mdi:close"></ha-icon></button>${esc(this._config.name)}</div>
          <div class="k" id="kiosk"></div>
        </div>
      </div>`;
    document.body.appendChild(host);
    this._dlg = host;
    const sr = host.shadowRoot;
    sr.getElementById("x").addEventListener("click", () => this._close(true));
    sr.getElementById("bg").addEventListener("click", (e) => e.target.id === "bg" && this._close(true));
    this._onKeydown = (e) => {
      if (/^[0-9]$/.test(e.key)) this._press(e.key);
      else if (e.key === "Backspace") this._press("back");
      else if (e.key === "Escape") this._close(true);
    };
    window.addEventListener("keydown", this._onKeydown);
    this._kioskRoot = sr.getElementById("kiosk");
    this._bindKiosk(this._kioskRoot);
    this._lastMode = undefined;
    this._paintKiosk(true);
    this._armIdle();
  }

  _close(userAction) {
    if (!this._dlg) return;
    const mode = this._state && this._state.state;
    if (userAction && ["employee", "typing", "error"].includes(mode)) this._key("cancel");
    clearTimeout(this._idleTimer);
    clearTimeout(this._resultTimer);
    window.removeEventListener("keydown", this._onKeydown);
    this._dlg.remove();
    this._dlg = null;
    this._kioskRoot = null;
  }

  _armIdle() {
    clearTimeout(this._idleTimer);
    const secs = Number(this._config.auto_close) || 0;
    if (this._config.inline || !secs) return;
    this._idleTimer = setTimeout(() => {
      const mode = this._state && this._state.state;
      if (["employee", "working", "checking", "result"].includes(mode)) this._armIdle();
      else this._close(false);
    }, secs * 1000);
  }

  /* ---------- kiosk screen ---------- */

  _bindKiosk(root) {
    root.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-key]");
      if (b) this._press(b.dataset.key);
    });
  }

  _press(key) {
    haptic(key === "clock_in" || key === "clock_out" ? "medium" : "light");
    this._armIdle();
    this._key(key);
  }

  _paintKiosk(force) {
    const root = this._kioskRoot;
    if (!root || !this._hass) return;
    const st = this._state;
    if (!force && st === this._lastState) return;
    this._lastState = st;
    const mode = st ? st.state : "unavailable";
    const k = st ? st.attributes : {};

    // After a punch, show the confirmation briefly, then close the pop-up.
    if (this._dlg && mode !== this._lastMode) {
      if (mode === "result") {
        clearTimeout(this._resultTimer);
        this._resultTimer = setTimeout(() => this._close(false), 2200);
      }
      this._lastMode = mode;
    }

    const now = new Date();
    const clock = `<div class="time">${now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</div>
      <div class="date">${now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</div>`;

    if (["employee", "working", "result"].includes(mode)) {
      const name = k.employee_name || "";
      const initials = name.split(" ").map((p) => p[0] || "").join("").slice(0, 2).toUpperCase();
      const photo = k.picture
        ? `<img class="photo" src="${esc(k.picture)}" alt="">`
        : `<div class="initials">${esc(initials)}</div>`;
      const on = k.on_the_clock === true;
      const fg = mode === "working" ? "#345778" : on ? "#285B42" : "#9C4138";
      const text = mode === "working" ? "Working…" : esc(k.status || "").split(" · ").join("<br>");
      const busy = mode !== "employee";
      root.innerHTML = `
        <div class="screen ${on ? "in" : "out"} ${mode === "result" ? "res" : ""}">
          ${photo}
          <div class="ename">${esc(name)}</div>
          ${clock}
          <div class="status" style="color:${fg}">${text}</div>
        </div>
        ${mode === "result" ? "" : `
        <div class="acts">
          <button class="cin" data-key="clock_in" ${on || busy ? "disabled" : ""}><ha-icon icon="mdi:login"></ha-icon>Clock In</button>
          <button class="cout" data-key="clock_out" ${!on || busy ? "disabled" : ""}><ha-icon icon="mdi:logout"></ha-icon>Clock Out</button>
          <button class="cancel" data-key="cancel" ${busy ? "disabled" : ""}>Cancel</button>
        </div>`}`;
      return;
    }

    const typed = ((k.display || "").match(/●/g) || []).length;
    const filled = mode === "checking" ? 4 : typed;
    const dots = [0, 1, 2, 3].map((i) => `<span class="${i < filled ? "f" : ""}"></span>`).join("");
    let msg = `<div class="msg">Enter your PIN</div>`;
    if (mode === "typing") msg = `<div class="msg">&nbsp;</div>`;
    if (mode === "checking") msg = `<div class="msg busy">Checking…</div>`;
    if (mode === "error") msg = `<div class="msg err">${esc(k.display || "")}</div>`;
    if (mode === "unavailable" || !st) msg = `<div class="msg err">Time clock is offline</div>`;
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
      .map((n) => `<button data-key="${n}">${n}</button>`)
      .join("");
    root.innerHTML = `
      <div class="screen">${clock}<div class="dots">${dots}</div>${msg}</div>
      <div class="pad">
        ${keys}
        <button class="clr" data-key="clear" aria-label="Clear"><ha-icon icon="mdi:close"></ha-icon></button>
        <button data-key="0">0</button>
        <button class="bk" data-key="back" aria-label="Back"><ha-icon icon="mdi:backspace-outline"></ha-icon></button>
      </div>`;
  }
}

if (!customElements.get("tsheets-whos-in-card")) customElements.define("tsheets-whos-in-card", TSheetsWhosInCard);
if (!customElements.get("tsheets-time-clock-card")) customElements.define("tsheets-time-clock-card", TSheetsTimeClockCard);

window.customCards = window.customCards || [];
for (const c of [
  {
    type: "tsheets-time-clock-card",
    name: "QuickBooks Time Clock",
    description: "PIN keypad for employees to clock in and out of QuickBooks Time.",
  },
  {
    type: "tsheets-whos-in-card",
    name: "QuickBooks Time Who's In",
    description: "Photos, jobs and clock-in times of everyone on the clock.",
  },
]) {
  if (!window.customCards.some((x) => x.type === c.type))
    window.customCards.push({ ...c, preview: true, documentationURL: "https://github.com/powertbs/ha-quickbooks-time" });
}

console.info(`%c TSHEETS-CARDS %c ${CARDS_VERSION} `, "background:#285B42;color:#fff;border-radius:3px 0 0 3px", "background:#eef7f0;color:#285B42;border-radius:0 3px 3px 0");
