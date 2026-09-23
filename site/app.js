const SECTIONS = [
  { key: "selected", label: "주요 사건" },
  { key: "events", label: "사건" },
  { key: "births", label: "탄생" },
  { key: "deaths", label: "사망" },
  { key: "holidays", label: "기념일" },
];

const $ = (id) => document.getElementById(id);
let data = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c);
  }
  return node;
}

function formatYear(year) {
  if (year == null) return "";
  return year < 0 ? `기원전 ${-year}년` : `${year}년`;
}

function yearsAgo(year, currentYear) {
  if (year == null) return null;
  const diff = currentYear - year - (year < 0 ? 1 : 0); // 0년이 없으므로 기원전은 1을 뺀다
  return diff > 0 ? `${diff.toLocaleString("ko-KR")}년 전` : null;
}

function renderPage(p) {
  const title = p.title_ko || p.title;
  const desc = p.description_ko || p.description;
  const img = p.thumbnail
    ? el("img", { src: p.thumbnail, alt: "", loading: "lazy" })
    : el("div", { class: "ph", "aria-hidden": "true" }, title.slice(0, 1));
  return el(
    "a",
    { class: "page", href: p.url, target: "_blank", rel: "noopener", title: p.title },
    img,
    el("span", {}, el("b", {}, title), desc ? el("i", {}, desc) : null),
  );
}

function renderItem(item, section, currentYear) {
  const isHoliday = section === "holidays";
  const text = item.text_ko || item.text;
  const body = el("div", {}, el("p", { class: "text" }, text));
  if (item.text_ko) {
    body.append(
      el("details", { class: "original" }, el("summary", {}, "원문"), el("p", { lang: "en" }, item.text)),
    );
  }
  if (item.pages.length) {
    body.append(el("div", { class: "pages" }, ...item.pages.slice(0, 4).map(renderPage)));
  }
  if (isHoliday) return el("li", { class: "entry holiday" }, body);
  const ago = yearsAgo(item.year, currentYear);
  return el(
    "li",
    { class: "entry" },
    el("div", { class: "year" }, formatYear(item.year), ago ? el("small", {}, ago) : null),
    body,
  );
}

function show(section) {
  for (const btn of $("tabs").children) {
    btn.setAttribute("aria-selected", String(btn.dataset.key === section));
  }
  const currentYear = Number(data.date.slice(0, 4));
  const list = $("list");
  list.replaceChildren(...data[section].map((item) => renderItem(item, section, currentYear)));
  try { localStorage.setItem("otd-tab", section); } catch {}
}

function renderTabs() {
  const tabs = $("tabs");
  for (const s of SECTIONS) {
    const btn = el(
      "button",
      { class: "tab", role: "tab", "data-key": s.key },
      s.label,
      el("span", { class: "count" }, String(data[s.key].length)),
    );
    btn.addEventListener("click", () => show(s.key));
    tabs.append(btn);
  }
}

function renderHeader() {
  const [y, m, d] = data.date.split("-").map(Number);
  $("date-title").textContent = `${m}월 ${d}일`;
  document.title = `역사 속 오늘 · ${m}월 ${d}일 (UTC)`;

  // 방문자 현지 날짜가 UTC 날짜와 다르면 알려준다
  const now = new Date();
  const local = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const utc = now.toISOString().slice(0, 10);
  const warn = $("tz-warning");
  if (utc !== data.date) {
    warn.textContent = `지금은 UTC 기준 ${utc.slice(5).replace("-", "월 ")}일이지만, 아직 갱신 전이라 ${m}월 ${d}일 내용을 보여 주고 있습니다.`;
    warn.hidden = false;
  } else if (local !== utc) {
    const [, lm, ld] = local.split("-").map(Number);
    warn.textContent = `현재 계신 곳의 날짜(${lm}월 ${ld}일)는 UTC 날짜(${m}월 ${d}일)와 다릅니다. 이 페이지는 UTC 날짜를 따릅니다.`;
    warn.hidden = false;
  }

  const generated = new Date(data.generated_at);
  const t = data.translation;
  const parts = [
    `기준 날짜: ${y}년 ${m}월 ${d}일 (UTC)`,
    `마지막 갱신: ${data.generated_at.replace("T", " ").replace("Z", " UTC")} (${generated.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST)`,
  ];
  const models = t.models || (t.model ? [t.model] : []);
  if (t.translated) parts.push(`번역: ${models.join(", ")} (${t.translated}/${t.total})`);
  $("meta").textContent = parts.join(" · ");
  if (!t.complete) {
    $("status").textContent = t.translated
      ? "일부 항목은 번역이 실패해 영어 원문으로 표시됩니다."
      : "번역 데이터가 없어 영어 원문으로 표시됩니다.";
  }
}

async function main() {
  try {
    const res = await fetch("data/latest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
  } catch (e) {
    $("date-title").textContent = "데이터를 불러오지 못했습니다";
    return;
  }
  renderHeader();
  renderTabs();
  let saved = null;
  try { saved = localStorage.getItem("otd-tab"); } catch {}
  show(SECTIONS.some((s) => s.key === saved) ? saved : "selected");
}

main();
