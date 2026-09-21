/* 이거사죠 — 프런트엔드 (의존성 없음) */
(() => {
  "use strict";

  const TOKEN_KEY = "igeosajo.tokens";
  const TIERS = {
    low: { label: "소액", test: (p) => p < 30000 },
    mid: { label: "중액", test: (p) => p >= 30000 && p < 100000 },
    high: { label: "고액", test: (p) => p >= 100000 },
  };

  const state = {
    slug: null,
    data: null,
    sort: "price_asc",
    tier: "all",
    hideGifted: false,
    pending: null, // { itemId, reserveToken, title }
  };

  const app = document.getElementById("app");

  /* ---------- 유틸 ---------- */
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const won = (price) => (price > 0 ? `${price.toLocaleString("ko-KR")}원` : "가격 미정");

  const tierOf = (price) => (TIERS.low.test(price) ? "low" : TIERS.mid.test(price) ? "mid" : "high");

  const dateOf = (sec) =>
    sec ? new Date(sec * 1000).toLocaleDateString("ko-KR", { month: "long", day: "numeric" }) : "";

  function readTokens() {
    try {
      return JSON.parse(localStorage.getItem(TOKEN_KEY) || "{}");
    } catch (_) {
      return {};
    }
  }

  function saveToken(slug, token, ownerName) {
    try {
      const tokens = readTokens();
      tokens[slug] = { token, owner_name: ownerName || (tokens[slug] || {}).owner_name || "" };
      localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
    } catch (_) {
      /* 시크릿 모드 등에서는 그냥 넘어간다 */
    }
  }

  const tokenFor = (slug) => (readTokens()[slug] || {}).token || null;

  async function callApi(path, { method = "GET", body, slug } = {}) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const token = slug ? tokenFor(slug) : null;
    if (token) headers["X-Edit-Token"] = token;
    const res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await res.json();
    } catch (_) {
      /* 본문이 없을 수도 있다 */
    }
    if (!res.ok) throw new Error(payload.error || "요청이 실패했어요. 잠시 후 다시 시도해주세요.");
    return payload;
  }

  let toastTimer = null;
  function toast(message) {
    document.querySelectorAll(".toast").forEach((el) => el.remove());
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), 2600);
  }

  /* ---------- 홈 ---------- */
  function renderHome() {
    const mine = Object.entries(readTokens());
    app.innerHTML = `
      <section class="hero">
        <h1>“뭐 갖고 싶어?”<br>이제 링크 하나만 보내세요.</h1>
        <p class="lede">가격을 말하기도, 갖고 싶은 걸 콕 집어 말하기도 민망하잖아요.
          소액부터 고액까지 적어두면, 사주는 사람이 마음 편히 고를 수 있어요.</p>
        <ul class="pill-list">
          <li>가나다·가격순 정렬</li><li>상품 링크 바로 연결</li>
          <li>중복 선물 방지</li><li>감사 인사 자동 발사 🎉</li>
        </ul>
      </section>

      <section class="card stack" style="margin-top:20px">
        <h2>내 위시리스트 만들기</h2>
        <form id="create-form" class="stack">
          <div class="form-grid">
            <label class="field"><span>이름 / 닉네임</span>
              <input name="owner_name" maxlength="40" required placeholder="예) 지은"></label>
            <label class="field"><span>링크 주소 (선택)</span>
              <input name="slug" maxlength="32" pattern="[a-z0-9][a-z0-9\\-]{2,31}"
                     placeholder="jieun-birthday"></label>
          </div>
          <label class="field"><span>한 줄 소개 (선택)</span>
            <input name="intro" maxlength="200" placeholder="10월 생일이에요. 부담 없는 것부터 골라주세요!"></label>
          <p class="form-error" id="create-error" hidden></p>
          <div class="row"><button class="primary-btn" type="submit">위시리스트 만들기</button></div>
        </form>
      </section>

      ${mine.length ? `
      <section class="mylists">
        <h2>이 기기에 저장된 내 위시리스트</h2>
        <ul>${mine.map(([slug, info]) => `
          <li class="card"><a href="#/w/${esc(slug)}">${esc(info.owner_name || slug)}님의 위시리스트</a>
            <span class="muted small"> /w/${esc(slug)}</span></li>`).join("")}
        </ul>
      </section>` : ""}
    `;

    document.getElementById("create-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const error = document.getElementById("create-error");
      const button = form.querySelector("button[type=submit]");
      error.hidden = true;
      button.disabled = true;
      try {
        const body = Object.fromEntries(new FormData(form).entries());
        const created = await callApi("/api/wishlists", { method: "POST", body });
        saveToken(created.slug, created.edit_token, created.owner_name);
        location.hash = `#/w/${created.slug}`;
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ---------- 위시리스트 ---------- */
  function sortedItems() {
    const items = state.data.items.filter((item) => {
      if (state.hideGifted && item.status === "gifted") return false;
      if (state.tier !== "all" && !TIERS[state.tier].test(item.price)) return false;
      return true;
    });
    const byName = (a, b) => a.title.localeCompare(b.title, "ko");
    const sorters = {
      name_asc: byName,
      price_asc: (a, b) => a.price - b.price || byName(a, b),
      price_desc: (a, b) => b.price - a.price || byName(a, b),
      newest: (a, b) => b.created_at - a.created_at,
    };
    return items.sort(sorters[state.sort] || sorters.price_asc);
  }

  function itemHtml(item, isOwner) {
    const gifted = item.status === "gifted";
    const reserved = item.status === "reserved";
    const stateBadge = gifted
      ? (item.verified
          ? '<span class="badge state-gifted">선물 완료 🎁</span>'
          : '<span class="badge state-pending">확인 중 ⏳</span>')
      : reserved
      ? '<span class="badge state-reserved">준비 중…</span>'
      : "";
    return `
      <li class="item ${gifted ? "is-gifted" : ""}" data-item="${item.id}">
        ${item.image_url ? `<img class="item-thumb" src="${esc(item.image_url)}" alt="" loading="lazy">` : ""}
        <div class="item-top">
          <h3>${esc(item.title)}</h3>
          ${stateBadge}
        </div>
        <div class="row">
          <span class="price">${esc(won(item.price))}</span>
          <span class="badge tier-${tierOf(item.price)}">${TIERS[tierOf(item.price)].label}</span>
          ${item.category ? `<span class="badge">${esc(item.category)}</span>` : ""}
          ${item.trackable ? '<span class="badge tracked" title="결제가 자동으로 확인돼요">자동확인</span>' : ""}
        </div>
        ${item.note ? `<p class="note">${esc(item.note)}</p>` : ""}
        ${gifted && (item.gifter_label || item.gifter_message) ? `
          <p class="gifted-by"><strong>${esc(item.gifter_label)}</strong>님이 선물해 주셨어요
            ${item.gifted_at ? `<span class="muted small">· ${esc(dateOf(item.gifted_at))}</span>` : ""}
            ${item.gifter_message ? `<br>“${esc(item.gifter_message)}”` : ""}</p>` : ""}
        ${gifted && !item.verified ? `
          <p class="note small">아직 확인 전이라 랭킹에는 올라가지 않았어요.</p>` : ""}
        <div class="item-actions">
          ${item.url ? `<a class="ghost-btn" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">상품 보기 ↗</a>` : ""}
          ${gifted ? "" : `<button class="primary-btn" data-gift="${item.id}">이거 사줄게요</button>`}
          ${isOwner && gifted && !item.verified
            ? `<button class="primary-btn" data-confirm="${item.id}">받았어요, 확인 ✅</button>` : ""}
          ${isOwner ? `<button class="danger-btn" data-delete="${item.id}">삭제</button>` : ""}
        </div>
      </li>`;
  }

  function rankingHtml(ranking) {
    if (!ranking) return "";
    const pending = ranking.pending_count
      ? `<span class="muted small">확인 대기 ${ranking.pending_count}건</span>` : "";
    if (!ranking.visible) {
      return `<section class="ranking">
          <h2>🎅 산타 랭킹</h2>
          <p class="muted small">이 위시리스트는 랭킹을 공개하지 않았어요. ${pending}</p>
        </section>`;
    }
    if (!ranking.ranks.length && !ranking.anonymous.count) {
      return `<section class="ranking">
          <h2>🎅 산타 랭킹</h2>
          <p class="muted small">확인된 선물이 아직 없어요. ${pending}</p>
        </section>`;
    }
    return `
      <section class="ranking">
        <h2>🎅 산타 랭킹</h2>
        <p class="muted small">
          결제가 확인된 선물만 집계해요 · 누적 ${esc(won(ranking.verified_total))} ${pending}</p>
        <ol class="rank-list">
          ${ranking.ranks.map((entry) => `
            <li class="rank-row rank-${entry.rank <= 3 ? entry.rank : "n"}">
              <span class="rank-no">${entry.rank}</span>
              <span class="rank-tier" title="${esc(entry.tier.label)}">${entry.tier.emoji}</span>
              <span class="rank-name">${esc(entry.label)}
                <span class="muted small">${esc(entry.tier.label)}</span></span>
              <span class="rank-total">${esc(won(entry.total))}
                <span class="muted small">· ${entry.count}건</span></span>
            </li>`).join("")}
        </ol>
        ${ranking.anonymous.count ? `
          <p class="muted small">그리고 익명의 산타 ${ranking.anonymous.count}분이
            ${esc(won(ranking.anonymous.total))}어치를 조용히 놓고 가셨어요. 🤫</p>` : ""}
      </section>`;
  }

  function renderWishlist() {
    const data = state.data;
    const isOwner = data.is_owner;
    const shareUrl = `${location.origin}/#/w/${data.slug}`;
    const items = sortedItems();
    const giftedCount = data.items.filter((i) => i.status === "gifted").length;

    app.innerHTML = `
      <section class="wl-head">
        <h1>${esc(data.owner_name)}님의 위시리스트</h1>
        ${data.intro ? `<p class="intro">${esc(data.intro)}</p>` : ""}
        <p class="muted small">아이템 ${data.items.length}개 · 선물 완료 ${giftedCount}개</p>
        <div class="share-box">
          <code id="share-url">${esc(shareUrl)}</code>
          <button class="ghost-btn" id="copy-link">링크 복사</button>
        </div>
      </section>

      ${isOwner ? `
      <section class="card stack" style="margin-bottom:18px">
        <h2>갖고 싶은 것 추가하기</h2>
        <form id="item-form" class="stack">
          <div class="form-grid">
            <label class="field"><span>상품 이름</span>
              <input name="title" maxlength="80" required placeholder="예) 무선 이어폰"></label>
            <label class="field"><span>가격 (원)</span>
              <input name="price" inputmode="numeric" placeholder="39000"></label>
            <label class="field"><span>상품 링크</span>
              <input name="url" type="url" id="item-url"
                     placeholder="쿠팡에서 복사한 주소를 그대로 붙여넣으세요"></label>
            <label class="field"><span>카테고리 (선택)</span>
              <input name="category" maxlength="20" placeholder="가전"></label>
          </div>
          <p class="link-hint" id="link-hint" hidden></p>
          <label class="field"><span>한마디 (선택)</span>
            <input name="note" maxlength="200" placeholder="색상은 아무거나 좋아요!"></label>
          <p class="form-error" id="item-error" hidden></p>
          <div class="row"><button class="primary-btn" type="submit">추가하기</button></div>
        </form>
        <label class="field">
          <span>산타 랭킹 공개 범위</span>
          <select id="ranking-visibility">
            <option value="public">모두에게 공개</option>
            <option value="owner">나만 보기</option>
            <option value="off">랭킹 끄기</option>
          </select>
        </label>
      </section>` : ""}

      <div class="controls">
        <label class="field" style="gap:0">
          <select id="sort-select" aria-label="정렬">
            <option value="price_asc">가격 낮은순</option>
            <option value="price_desc">가격 높은순</option>
            <option value="name_asc">가나다순</option>
            <option value="newest">최근 추가순</option>
          </select>
        </label>
        <div class="row" role="group" aria-label="가격대 필터">
          ${["all", "low", "mid", "high"].map((key) => `
            <button class="chip" data-tier="${key}" aria-pressed="${state.tier === key}">
              ${key === "all" ? "전체" : TIERS[key].label}</button>`).join("")}
        </div>
        <button class="chip" id="hide-gifted" aria-pressed="${state.hideGifted}">받은 선물 숨기기</button>
      </div>

      ${items.length
        ? `<ul class="items">${items.map((item) => itemHtml(item, isOwner)).join("")}</ul>`
        : `<p class="empty">${data.items.length ? "이 조건에 맞는 아이템이 없어요." : "아직 아이템이 없어요."}</p>`}

      ${rankingHtml(data.ranking)}

      ${giftedCount ? `
      <section class="thanks-wall">
        <h2>감사의 벽 💌</h2>
        <ul>${data.items.filter((i) => i.status === "gifted").map((i) => `
          <li><strong>${esc(i.gifter_label)}</strong> → ${esc(i.title)}
            ${i.gifter_message ? `<br><span class="muted">“${esc(i.gifter_message)}”</span>` : ""}</li>`).join("")}
        </ul>
      </section>` : ""}
    `;

    document.getElementById("sort-select").value = state.sort;
    if (isOwner) document.getElementById("ranking-visibility").value = data.ranking_visibility;
    wireWishlistEvents(isOwner);
  }

  function wireWishlistEvents(isOwner) {
    document.getElementById("sort-select").addEventListener("change", (event) => {
      state.sort = event.currentTarget.value;
      renderWishlist();
    });

    document.querySelectorAll("[data-tier]").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.tier = chip.dataset.tier;
        renderWishlist();
      });
    });

    document.getElementById("hide-gifted").addEventListener("click", () => {
      state.hideGifted = !state.hideGifted;
      renderWishlist();
    });

    document.getElementById("copy-link").addEventListener("click", async () => {
      const url = document.getElementById("share-url").textContent;
      try {
        await navigator.clipboard.writeText(url);
        toast("링크를 복사했어요!");
      } catch (_) {
        toast("복사가 안 되면 주소를 직접 선택해 주세요.");
      }
    });

    document.querySelectorAll("[data-gift]").forEach((button) => {
      button.addEventListener("click", () => startGift(Number(button.dataset.gift)));
    });

    if (isOwner) {
      const form = document.getElementById("item-form");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const error = document.getElementById("item-error");
        const button = form.querySelector("button[type=submit]");
        error.hidden = true;
        button.disabled = true;
        try {
          const body = Object.fromEntries(new FormData(form).entries());
          await callApi(`/api/wishlists/${state.slug}/items`, { method: "POST", body, slug: state.slug });
          form.reset();
          await loadWishlist();
        } catch (err) {
          error.textContent = err.message;
          error.hidden = false;
        } finally {
          button.disabled = false;
        }
      });

      wireLinkHint();

      document.getElementById("ranking-visibility").addEventListener("change", async (event) => {
        try {
          await callApi(`/api/wishlists/${state.slug}`, {
            method: "PATCH",
            slug: state.slug,
            body: {
              owner_name: state.data.owner_name,
              intro: state.data.intro,
              ranking_visibility: event.currentTarget.value,
            },
          });
          await loadWishlist();
        } catch (err) {
          toast(err.message);
        }
      });

      document.querySelectorAll("[data-confirm]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await callApi(`/api/items/${button.dataset.confirm}/confirm`, {
              method: "POST",
              slug: state.slug,
            });
            toast("확인했어요! 산타 랭킹에 반영됩니다 🎅");
            await loadWishlist();
          } catch (err) {
            toast(err.message);
          }
        });
      });

      document.querySelectorAll("[data-delete]").forEach((button) => {
        button.addEventListener("click", async () => {
          if (!confirm("이 아이템을 지울까요?")) return;
          try {
            await callApi(`/api/items/${button.dataset.delete}`, { method: "DELETE", slug: state.slug });
            await loadWishlist();
          } catch (err) {
            toast(err.message);
          }
        });
      });
    }
  }

  function wireLinkHint() {
    const input = document.getElementById("item-url");
    const hint = document.getElementById("link-hint");
    let timer = null;

    const check = async () => {
      const url = input.value.trim();
      if (!url) {
        hint.hidden = true;
        return;
      }
      try {
        const info = await callApi("/api/link/inspect", { method: "POST", body: { url } });
        hint.className = "link-hint " + (info.trackable ? "ok" : "plain");
        hint.innerHTML =
          `${info.trackable ? "🔗" : "🛍️"} ${esc(info.label)}` +
          (info.changed ? '<br><span class="muted small">추적용 주소는 정리해서 저장할게요.</span>' : "");
        hint.hidden = false;
        if (info.changed) input.value = info.url;
      } catch (_) {
        hint.hidden = true;
      }
    };

    ["input", "paste", "change"].forEach((event) =>
      input.addEventListener(event, () => {
        clearTimeout(timer);
        timer = setTimeout(check, 400);
      }));
  }

  /* ---------- 선물 플로우 ---------- */
  const modal = document.getElementById("gift-modal");
  const giftForm = document.getElementById("gift-form");
  const giftError = document.getElementById("gift-error");
  const stepGo = document.getElementById("gift-step-go");
  const stepDone = document.getElementById("gift-step-done");

  function showStep(which) {
    stepGo.hidden = which !== "go";
    stepDone.hidden = which !== "done";
    if (which === "done") giftForm.querySelector("input[name=gifter_name]").focus();
  }

  async function startGift(itemId) {
    const item = state.data.items.find((i) => i.id === itemId);
    if (!item) return;
    let reserved;
    try {
      reserved = await callApi(`/api/items/${itemId}/reserve`, { method: "POST" });
      state.pending = {
        itemId,
        reserveToken: reserved.reserve_token,
        title: item.title,
        tracked: reserved.tracked,
      };
    } catch (err) {
      toast(err.message);
      await loadWishlist();
      return;
    }

    document.getElementById("gift-modal-item").textContent = `${item.title} · ${won(item.price)}`;
    // 제휴 링크면 subId 가 붙어 있어 나중에 실제 결제와 맞춰볼 수 있다
    const url = reserved.tracking_url || "";
    for (const id of ["gift-modal-link", "gift-reopen"]) {
      const link = document.getElementById(id);
      link.href = url || "#";
      link.hidden = !url;
    }
    document.getElementById("gift-track-note").textContent = reserved.tracked
      ? "이 링크로 결제하시면 결제 내역이 자동으로 확인돼 랭킹에 반영돼요."
      : "이 쇼핑몰은 자동 확인이 안 돼요. 받는 분이 확인해주면 랭킹에 올라갑니다.";

    giftForm.reset();
    document.getElementById("gift-name-field").hidden = false;
    giftError.hidden = true;
    // 링크가 없는 아이템이면 보낼 데가 없으니 바로 2단계로
    showStep(url ? "go" : "done");
    modal.hidden = false;
    await loadWishlist({ keepModal: true });
  }

  // 상품 페이지가 새 창으로 열리면, 원래 화면은 '결제 완료' 단계로 넘어간다
  document.getElementById("gift-modal-link").addEventListener("click", () => {
    setTimeout(() => showStep("done"), 300);
  });
  document.getElementById("gift-skip").addEventListener("click", () => showStep("done"));

  async function cancelGift() {
    const pending = state.pending;
    modal.hidden = true;
    state.pending = null;
    if (!pending) return;
    try {
      await callApi(`/api/items/${pending.itemId}/cancel`, {
        method: "POST",
        body: { reserve_token: pending.reserveToken },
      });
    } catch (_) {
      /* 이미 만료되었으면 그대로 둔다 */
    }
    await loadWishlist();
  }

  document.querySelectorAll("[data-gift-cancel]").forEach((button) =>
    button.addEventListener("click", cancelGift));

  modal.addEventListener("click", (event) => {
    if (event.target === modal) cancelGift();
  });

  giftForm.addEventListener("change", () => {
    const anon = giftForm.querySelector("input[name=display]:checked").value === "anon";
    document.getElementById("gift-name-field").hidden = anon;
  });

  giftForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.pending) return;
    const button = giftForm.querySelector("button[type=submit]");
    giftError.hidden = true;
    button.disabled = true;
    try {
      const form = Object.fromEntries(new FormData(giftForm).entries());
      const result = await callApi(`/api/items/${state.pending.itemId}/gift`, {
        method: "POST",
        body: {
          reserve_token: state.pending.reserveToken,
          display: form.display,
          gifter_name: form.gifter_name,
          message: form.message,
        },
      });
      modal.hidden = true;
      state.pending = null;
      celebrate(result);
      await loadWishlist();
    } catch (err) {
      giftError.textContent = err.message;
      giftError.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  /* ---------- 감사 이모션 ---------- */
  const celebration = document.getElementById("celebration");
  document.getElementById("celebration-close").addEventListener("click", () => {
    celebration.hidden = true;
  });

  function celebrate(result) {
    const item = result.item;
    const verified = item.verified;
    document.getElementById("celebration-title").textContent =
      verified ? "너무 감사합니다!!" : "감사합니다!! 🎉";
    document.getElementById("celebration-body").innerHTML =
      `${esc(item.gifter_label)}님이 “${esc(item.title)}”을(를) 선물했어요.<br>` +
      `${esc(result.owner_name)}님의 위시리스트에 감사 인사가 남았습니다. 🎁` +
      (verified ? "" : `<br><span class="muted small">${esc(
        result.tracked
          ? "결제가 확인되면 산타 랭킹에 자동으로 올라가요."
          : `${result.owner_name}님이 선물을 받고 확인해주면 산타 랭킹에 올라가요.`
      )}</span>`);
    celebration.hidden = false;
    fireConfetti();
  }

  /* ---------- 축포 ---------- */
  const canvas = document.getElementById("confetti");
  const ctx = canvas.getContext("2d");
  const COLORS = ["#e0553f", "#c98a27", "#2f8f5b", "#ff9f7a", "#f5d76e", "#6ec1e4", "#e46ea8"];
  const GRAVITY = 0.28;
  const DRAG = 0.985;
  let pieces = [];
  let rafId = null;

  function piece(x, y, vx, vy) {
    const streamer = Math.random() < 0.25;
    return {
      x, y, vx, vy,
      size: streamer ? 3 + Math.random() * 3 : 7 + Math.random() * 7,
      length: streamer ? 18 + Math.random() * 20 : 0,
      rot: Math.random() * Math.PI * 2,
      vr: -0.25 + Math.random() * 0.5,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      life: 1,
    };
  }

  /** 바닥 모서리에서 비스듬히 쏘아 올리는 축포 한 발 */
  function cannon(x, y, angle, count) {
    for (let i = 0; i < count; i += 1) {
      const spread = angle + (Math.random() - 0.5) * 0.7;
      const speed = 13 + Math.random() * 12;
      pieces.push(piece(x, y, Math.cos(spread) * speed, Math.sin(spread) * speed));
    }
  }

  function fireConfetti() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    pieces = [];

    const w = canvas.width;
    const h = canvas.height;
    // 양쪽 아래에서 펑, 펑
    cannon(0, h, -Math.PI / 3.2, 70);
    cannon(w, h, -Math.PI + Math.PI / 3.2, 70);
    // 위에서 흩뿌려 내려오는 조각들
    for (let i = 0; i < 90; i += 1) {
      pieces.push(piece(Math.random() * w, -20 - Math.random() * h * 0.5,
                        -1.5 + Math.random() * 3, 1 + Math.random() * 3));
    }
    // 잠시 뒤 한 발 더 쏴서 여운을 남긴다
    setTimeout(() => {
      if (celebration.hidden) return;
      cannon(w * 0.5, h, -Math.PI / 2, 60);
      if (!rafId) rafId = requestAnimationFrame(tick);
    }, 450);

    cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tick);
  }

  function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    pieces = pieces.filter((p) => p.y < canvas.height + 60 && p.life > 0);
    pieces.forEach((p) => {
      p.vx *= DRAG;
      p.vy = p.vy * DRAG + GRAVITY;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      if (p.length) {
        ctx.fillRect(-p.size / 2, -p.length / 2, p.size, p.length);
      } else {
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      }
      ctx.restore();
    });
    if (pieces.length) {
      rafId = requestAnimationFrame(tick);
    } else {
      rafId = null;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  /* ---------- 라우팅 ---------- */
  async function loadWishlist({ keepModal = false } = {}) {
    const token = tokenFor(state.slug);
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    state.data = await callApi(`/api/wishlists/${state.slug}${query}`, { slug: state.slug });
    if (state.data.is_owner) saveToken(state.slug, token, state.data.owner_name);
    const modalWasOpen = !modal.hidden;
    renderWishlist();
    if (keepModal && modalWasOpen) modal.hidden = false;
  }

  async function route() {
    const hash = location.hash.replace(/^#/, "") || "/";
    const [path, rawQuery] = hash.split("?");
    const match = path.match(/^\/w\/([a-z0-9-]{3,32})$/);

    if (!match) {
      state.slug = null;
      state.data = null;
      renderHome();
      return;
    }

    state.slug = match[1];
    const params = new URLSearchParams(rawQuery || "");
    if (params.get("token")) {
      saveToken(state.slug, params.get("token"));
      location.replace(`#/w/${state.slug}`);
      return;
    }

    app.innerHTML = '<p class="empty">불러오는 중…</p>';
    try {
      await loadWishlist();
    } catch (err) {
      app.innerHTML = `<p class="empty">${esc(err.message)}<br><a href="#/">처음으로</a></p>`;
    }
  }

  window.addEventListener("hashchange", route);
  route();
})();
