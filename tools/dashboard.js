/* Dashboard front-end. Plain JS, no external dependencies. */
"use strict";

const state = new Map(); // gameId -> {moves: [], ply: number|null, live: bool, follow: bool}
let liveKey = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function resultLabel(g) {
  return g.outcome === "win" ? "победа" :
         g.outcome === "loss" ? "поражение" : "ничья";
}

function statusLabel(s) {
  const map = {
    mate: "мат", resign: "сдача", outoftime: "время", timeout: "время",
    stalemate: "пат", draw: "ничья", "insufficient material": "материал",
    "50-move rule": "50 ходов", "threefold repetition": "повторение",
    aborted: "отменена", "time forfeit": "время", "cheat detected": "дисквалификация"
  };
  return map[s] || s || "";
}

function fmtClock(sec) {
  if (sec === null || sec === undefined) return "";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return m + ":" + String(s).padStart(2, "0");
}

function boardUrl(id, ply) {
  let u = "/board?game=" + encodeURIComponent(id);
  if (ply !== null && ply !== undefined) u += "&ply=" + ply;
  return u + "&t=" + Date.now();
}

function getState(id, live) {
  let st = state.get(id);
  if (!st) {
    st = { moves: [], ply: null, live: !!live, follow: true };
    state.set(id, st);
  }
  st.live = !!live;
  return st;
}

function navHtml(id) {
  const g = esc(id);
  return `<div class="nav" data-navbox="${g}">
    <button data-nav="start" data-game="${g}" title="В начало">⏮</button>
    <button data-nav="prev" data-game="${g}" title="Назад">◀</button>
    <span class="plylabel" data-plylabel="${g}">…</span>
    <button data-nav="next" data-game="${g}" title="Вперёд">▶</button>
    <button data-nav="end" data-game="${g}" title="В конец / live">⏭</button>
  </div>`;
}

function liveCard(g) {
  const side = g.color === "white" ? "белые" : "чёрные";
  return `<div class="card" data-id="${esc(g.gameId)}">
    <div class="card-head">
      <span class="who">${esc(g.opponent)}</span>
      <span class="meta">${esc(g.oppRating ?? "")} · ${esc(g.speed)} · ${g.rated ? "рейтинг" : "casual"}</span>
      <span class="turn ${g.isMyTurn ? "me" : "opp"}" data-clock="${esc(g.gameId)}">
        ${g.isMyTurn ? "твой ход" : "ход соперника"}${fmtClock(g.secondsLeft) ? " · " + fmtClock(g.secondsLeft) : ""}</span>
    </div>
    <img class="board" data-board="${esc(g.gameId)}" src="${boardUrl(g.gameId)}" alt="board">
    ${navHtml(g.gameId)}
    <div class="moves" data-moves="${esc(g.gameId)}">загрузка ходов…</div>
    <div class="card-head"><span class="meta">${side} · <a href="https://lichess.org/${esc(g.gameId)}" target="_blank">открыть на Lichess ↗</a></span></div>
  </div>`;
}

function lastCard(g) {
  if (!g) return `<div class="empty">Пока нет партий.</div>`;
  return `<div class="card" data-id="${esc(g.id)}">
    <div class="card-head">
      <span class="who">${esc(g.opponent)}</span>
      <span class="meta">${esc(g.oppRating ?? "")} · ${esc(g.speed)} · ${esc(g.clock)}</span>
      <span class="turn ${g.outcome === "win" ? "me" : "opp"}">${resultLabel(g)}</span>
    </div>
    <img class="board" data-board="${esc(g.id)}" src="${boardUrl(g.id)}" alt="board">
    ${navHtml(g.id)}
    <div class="moves" data-moves="${esc(g.id)}">загрузка ходов…</div>
    <div class="card-head"><span class="meta">последняя партия · <a href="https://lichess.org/${esc(g.id)}" target="_blank">открыть ↗</a></span></div>
  </div>`;
}

function plyLabel(st) {
  if (st.follow) return st.live ? "live" : "конец";
  if (st.ply === 0) return "начало";
  return `${st.ply} / ${st.moves.length}`;
}

function renderMoves(id) {
  const st = state.get(id);
  const el = document.querySelector(`[data-moves="${id}"]`);
  if (!st || !el) return;
  if (!st.moves.length) {
    el.textContent = "ходов пока нет";
    return;
  }
  const cur = st.follow ? st.moves.length : st.ply;
  let rows = "";
  for (let i = 0; i < st.moves.length; i += 2) {
    const w = st.moves[i];
    const b = st.moves[i + 1] || "";
    const wCur = (i + 1 === cur) ? "mv cur" : "mv";
    const bCur = (i + 2 === cur) ? "mv cur" : "mv";
    rows += `<div class="moverow"><span class="moveno">${i / 2 + 1}.</span>` +
      `<span class="${wCur}" data-move="${i + 1}" data-game="${esc(id)}">${esc(w)}</span>` +
      `<span class="${bCur}" data-move="${i + 2}" data-game="${esc(id)}">${esc(b)}</span></div>`;
  }
  el.innerHTML = `<div class="movetable">${rows}</div>`;
  const current = el.querySelector(".mv.cur");
  if (current) current.scrollIntoView({ block: "nearest" });
}

function syncBoard(id) {
  const st = state.get(id);
  if (!st) return;
  const img = document.querySelector(`img[data-board="${id}"]`);
  if (img) img.src = boardUrl(id, st.follow ? null : st.ply);
  const label = document.querySelector(`[data-plylabel="${id}"]`);
  if (label) label.textContent = plyLabel(st);
}

async function loadMoves(id, live) {
  const st = getState(id, live);
  const prevLen = st.moves.length;
  const prevLast = st.moves[st.moves.length - 1];
  try {
    const r = await fetch("/api/moves?game=" + encodeURIComponent(id), { cache: "no-store" });
    const d = await r.json();
    const next = d.moves || [];
    const changed = next.length !== prevLen || next[next.length - 1] !== prevLast;
    st.moves = next;
    if (st.ply !== null) st.ply = Math.min(st.ply, st.moves.length);
    renderMoves(id);
    if (changed) syncBoard(id);
  } catch (e) {
    // Keep the last known position.
  }
}

document.addEventListener("click", e => {
  const nav = e.target.closest("[data-nav]");
  if (nav) {
    const id = nav.dataset.game;
    const st = state.get(id);
    if (!st) return;
    const len = st.moves.length;
    const cur = st.follow ? len : st.ply;
    switch (nav.dataset.nav) {
      case "start":
        st.follow = false; st.ply = 0; break;
      case "prev":
        st.follow = false; st.ply = Math.max(0, cur - 1); break;
      case "next": {
        const n = Math.min(len, cur + 1);
        if (st.live && n === len) { st.follow = true; }
        else { st.follow = false; st.ply = n; }
        break;
      }
      case "end":
        if (st.live) { st.follow = true; }
        else { st.follow = false; st.ply = len; }
        break;
    }
    renderMoves(id);
    syncBoard(id);
    return;
  }
  const mv = e.target.closest("[data-move]");
  if (mv) {
    const id = mv.dataset.game;
    const st = state.get(id);
    if (!st) return;
    st.follow = false;
    st.ply = parseInt(mv.dataset.move, 10);
    renderMoves(id);
    syncBoard(id);
  }
});

function renderHeader(d) {
  const title = document.getElementById("title");
  if (d.title) { title.style.display = ""; title.textContent = d.title; }
  const ratings = Object.entries(d.ratings || {})
    .filter(([, v]) => v && (v.games === undefined || v.games > 0))
    .map(([k, v]) =>
      `<span class="rating" title="${v.prov ? "предварительный рейтинг" : ""}">${esc(k)} <b>${esc(v.rating)}${v.prov ? "?" : ""}</b>${v.prog ? ` <span class="${v.prog > 0 ? "win" : "loss"}">${v.prog > 0 ? "+" : ""}${v.prog}</span>` : ""}</span>`
    ).join("");
  document.getElementById("ratings").innerHTML = ratings;
  const status = document.getElementById("status");
  status.textContent = d.online === true ? "онлайн" : d.online === false ? "офлайн" : "Lichess";
  status.style.color = d.online === true ? "var(--green)" : "var(--dim)";
  document.getElementById("error").innerHTML = d.error
    ? `<div class="err">${esc(d.error)}</div>` : "";
}

function renderRecent(games) {
  const tbody = document.getElementById("recent");
  if (!games || !games.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="dim">партий пока нет</td></tr>`;
    return;
  }
  tbody.innerHTML = games.map(g => {
    const cls = g.outcome === "win" ? "win" : g.outcome === "loss" ? "loss" : "draw";
    const diff = g.ratingDiff === null || g.ratingDiff === undefined ? "" :
      ` <span class="${g.ratingDiff >= 0 ? "win" : "loss"}">${g.ratingDiff >= 0 ? "+" : ""}${g.ratingDiff}</span>`;
    const when = g.createdAt ? new Date(g.createdAt).toLocaleString("ru-RU",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
    return `<tr>
      <td class="dim">${esc(when)}</td>
      <td><a href="https://lichess.org/${esc(g.id)}" target="_blank">${esc(g.opponent)}</a>
        <span class="dim">${esc(g.oppRating ?? "")}</span>${diff}</td>
      <td class="dim">${g.color === "white" ? "белые" : "чёрные"}</td>
      <td class="${cls}">${resultLabel(g)}</td>
      <td class="dim">${esc(g.clock)} ${g.rated ? "" : " · casual"}</td>
      <td class="dim">${esc(statusLabel(g.status))}</td>
    </tr>`;
  }).join("");
}

async function tick() {
  try {
    const d = await (await fetch("/api/data", { cache: "no-store" })).json();
    renderHeader(d);
    const live = d.playing || [];
    const title = document.getElementById("live-title");
    const box = document.getElementById("live");
    const key = live.length ? live.map(g => g.gameId).join(",") :
      (d.recent && d.recent[0] ? "last:" + d.recent[0].id : "none");

    if (key !== liveKey) {
      liveKey = key;
      title.textContent = live.length ? "Сейчас играет" : "Последняя партия";
      box.innerHTML = live.length ? live.map(liveCard).join("") : lastCard(d.recent && d.recent[0]);
      if (live.length) {
        live.forEach(g => { getState(g.gameId, true); loadMoves(g.gameId, true); });
      } else if (d.recent && d.recent[0]) {
        const st = getState(d.recent[0].id, false);
        st.follow = true;
        st.ply = null;
        loadMoves(d.recent[0].id, false);
      }
    } else {
      for (const g of live) {
        const el = document.querySelector(`[data-clock="${g.gameId}"]`);
        if (el) {
          const clock = fmtClock(g.secondsLeft);
          el.textContent = (g.isMyTurn ? "твой ход" : "ход соперника") + (clock ? " · " + clock : "");
        }
        loadMoves(g.gameId, true);
      }
    }
    renderRecent(d.recent);
  } catch (e) {
    document.getElementById("status").textContent = "нет связи с дашбордом";
  }
}

tick();
setInterval(tick, 3000);
