"""UTC 기준 오늘 날짜의 Wikipedia 'On this day' 데이터를 받아 한국어로 번역해 저장한다.

환경 변수
  GEMINI_API_KEY  번역용 키. 없으면 번역을 건너뛰고 영어 원문만 저장한다.
  GEMINI_MODEL    번역 모델, 쉼표로 여러 개 지정하면 순서대로 시도 (기본값: DEFAULT_MODELS)
  TARGET_DATE     YYYY-MM-DD 형식으로 날짜를 강제 지정 (테스트용, 기본값: 오늘 UTC)
  FORCE           1이면 이미 번역된 파일이 있어도 처음부터 다시 번역한다.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "site" / "data"

# Wikimedia 정책상 연락 가능한 User-Agent가 필요하다. Actions에서는 저장소 주소를 넣는다.
USER_AGENT = "OnThisDayKo/1.0 (https://github.com/{})".format(os.environ.get("GITHUB_REPOSITORY", "local-test"))
WIKI_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{mm}/{dd}"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 본문과 관련 문서 정보까지 번역하는 분류 / 본문만 번역하는 분류
FULL_SECTIONS = ["selected", "events", "holidays"]
TEXT_ONLY_SECTIONS = ["births", "deaths"]
SECTIONS = FULL_SECTIONS + TEXT_ONLY_SECTIONS

# 앞 모델이 과부하·한도 초과면 다음 모델로 넘어간다. Actions 변수 GEMINI_MODEL(쉼표로 구분)로 바꿀 수 있다.
DEFAULT_MODELS = "gemini-2.5-flash,gemini-2.5-flash-lite"

CHUNK_SIZE = 120  # 요청 한 번에 보낼 문장 수
REQUEST_GAP = 6  # 요청 사이 대기(초). 무료 등급 분당 요청 한도 대비


def http_json(url, data=None, headers=None, timeout=120):
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_wiki(date):
    raw = http_json(WIKI_URL.format(mm=f"{date.month:02d}", dd=f"{date.day:02d}"))
    result = {}
    for section in SECTIONS:
        items = []
        for item in raw.get(section, []):
            pages = []
            for p in item.get("pages", []):
                pages.append({
                    "title": p.get("normalizedtitle") or p.get("titles", {}).get("normalized") or p.get("title", ""),
                    "description": p.get("description", ""),
                    "thumbnail": (p.get("thumbnail") or {}).get("source"),
                    "url": p.get("content_urls", {}).get("desktop", {}).get("page"),
                })
            items.append({"year": item.get("year"), "text": item.get("text", ""), "pages": pages})
        if section != "holidays":
            items.sort(key=lambda x: x["year"] if x["year"] is not None else 0, reverse=True)
        result[section] = items
    return result


def collect_strings(sections):
    """번역할 문장을 중복 없이 모은다."""
    strings = []
    seen = set()

    def add(s):
        if s and s not in seen:
            seen.add(s)
            strings.append(s)

    for section in SECTIONS:
        for item in sections[section]:
            add(item["text"])
            if section in FULL_SECTIONS:
                for p in item["pages"]:
                    add(p["title"])
                    add(p["description"])
    return strings


def gemini_translate(chunk, api_key, model):
    source = {str(i): s for i, s in enumerate(chunk)}
    prompt = (
        "다음 JSON 객체의 각 값(영어 위키백과 문장, 문서 제목, 짧은 설명)을 자연스러운 한국어로 번역하라.\n"
        "- 키는 그대로 두고 값만 번역한 JSON 객체 하나만 출력한다.\n"
        "- 인명·지명은 한국어 위키백과/국립국어원 외래어 표기법에 맞춘 통용 표기를 쓴다.\n"
        "- 문서 제목은 한국어 위키백과에서 쓰는 제목을 우선한다.\n"
        "- 평서문은 '~했다'체로, 짧은 설명은 명사구로 번역한다.\n\n"
        + json.dumps(source, ensure_ascii=False)
    )
    config = {"temperature": 0.2, "responseMimeType": "application/json"}
    if model.startswith("gemini-2.5-flash"):
        config["thinkingConfig"] = {"thinkingBudget": 0}  # 번역에는 추론이 필요 없어 꺼서 속도를 올린다
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
    res = http_json(
        GEMINI_URL.format(model=model),
        data=payload,
        headers={"x-goog-api-key": api_key},
        timeout=180,
    )
    text = res["candidates"][0]["content"]["parts"][0]["text"]
    translated = json.loads(text)
    return {chunk[int(k)]: v for k, v in translated.items() if k.isdigit() and int(k) < len(chunk) and isinstance(v, str)}


def translate_all(strings, api_key, models):
    """청크마다 모델을 순서대로 시도한다. 과부하(503)·한도(429)면 다음 모델로 넘어간다."""
    table = {}
    used = []
    dead = set()  # 존재하지 않는 모델(404)
    fail_streak = 0
    chunks = [strings[i:i + CHUNK_SIZE] for i in range(0, len(strings), CHUNK_SIZE)]
    for n, chunk in enumerate(chunks, 1):
        done = False
        for model in [m for m in models if m not in dead]:
            for attempt in range(2):
                try:
                    table.update(gemini_translate(chunk, api_key, model))
                    print(f"  번역 {n}/{len(chunks)} 완료 ({len(chunk)}개, {model})")
                    if model not in used:
                        used.append(model)
                    done = True
                    break
                except urllib.error.HTTPError as e:
                    detail = " ".join(e.read().decode("utf-8", "replace").split())[:200]
                    print(f"  번역 {n}/{len(chunks)} 실패 ({model}, HTTP {e.code}): {detail}", file=sys.stderr)
                    if e.code == 404:
                        dead.add(model)
                        break
                    if e.code not in (429, 500, 503) or attempt == 1:
                        break
                    time.sleep(15)
                except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
                    print(f"  번역 {n}/{len(chunks)} 실패 ({model}): {e!r}", file=sys.stderr)
                    if attempt == 1:
                        break
                    time.sleep(5)
            if done:
                break
        if done:
            fail_streak = 0
        else:
            fail_streak += 1
            if fail_streak >= 2:
                print("  모든 모델이 연속으로 실패해 번역을 중단함 (다음 실행에서 이어서 번역)", file=sys.stderr)
                break
        if n < len(chunks):
            time.sleep(REQUEST_GAP)
    return table, used


def existing_translations(data):
    """이전 실행에서 저장한 번역을 원문→번역 표로 되돌린다."""
    table = {}
    for section in SECTIONS:
        for item in data.get(section, []):
            if item.get("text_ko"):
                table[item["text"]] = item["text_ko"]
            for p in item.get("pages", []):
                if p.get("title_ko"):
                    table[p["title"]] = p["title_ko"]
                if p.get("description_ko"):
                    table[p["description"]] = p["description_ko"]
    return table


def apply_translation(sections, table):
    for section in SECTIONS:
        for item in sections[section]:
            item["text_ko"] = table.get(item["text"])
            for p in item["pages"]:
                p["title_ko"] = table.get(p["title"])
                p["description_ko"] = table.get(p["description"]) if p["description"] else None


def main():
    if os.environ.get("TARGET_DATE"):
        date = datetime.strptime(os.environ["TARGET_DATE"], "%Y-%m-%d").date()
    else:
        date = datetime.now(timezone.utc).date()
    out_path = DATA_DIR / f"{date.isoformat()}.json"
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    models = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS).split(",") if m.strip()]

    table = {}
    prev_models = []
    if out_path.exists() and os.environ.get("FORCE") != "1":
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("translation", {}).get("complete") or not api_key:
            print(f"{out_path.name} 이미 있음, 건너뜀")
            write_latest(existing)
            return
        table = existing_translations(existing)
        prev_models = existing.get("translation", {}).get("models") or []

    print(f"{date} (UTC) 데이터 가져오는 중")
    sections = fetch_wiki(date)
    strings = collect_strings(sections)

    used = []
    if api_key:
        todo = [s for s in strings if s not in table]
        print(f"{len(strings)}개 중 {len(todo)}개 번역 필요 (모델: {', '.join(models)})")
        new, used = translate_all(todo, api_key, models)
        table.update(new)
    else:
        print("GEMINI_API_KEY 없음: 번역 없이 영어 원문만 저장")
    apply_translation(sections, table)

    data = {
        "date": date.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Wikipedia (en) — On this day",
        "translation": {
            "models": prev_models + [m for m in used if m not in prev_models],
            "translated": sum(1 for s in strings if s in table),
            "total": len(strings),
            "complete": bool(strings) and all(s in table for s in strings),
        },
        **sections,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_latest(data)
    t = data["translation"]
    print(f"저장: {out_path.relative_to(ROOT)} (번역 {t['translated']}/{t['total']})")


def write_latest(data):
    (DATA_DIR / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
