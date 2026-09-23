"""UTC 기준 오늘 날짜의 Wikipedia 'On this day' 데이터를 받아 한국어로 번역해 저장한다.

환경 변수
  GEMINI_API_KEY  번역용 키. 없으면 번역을 건너뛰고 영어 원문만 저장한다.
  GEMINI_MODEL    번역 모델 (기본값: gemini-2.5-flash)
  TARGET_DATE     YYYY-MM-DD 형식으로 날짜를 강제 지정 (테스트용, 기본값: 오늘 UTC)
  FORCE           1이면 이미 번역된 파일이 있어도 다시 만든다.
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
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    res = http_json(
        GEMINI_URL.format(model=model),
        data=payload,
        headers={"x-goog-api-key": api_key},
        timeout=300,
    )
    text = res["candidates"][0]["content"]["parts"][0]["text"]
    translated = json.loads(text)
    return {chunk[int(k)]: v for k, v in translated.items() if k.isdigit() and int(k) < len(chunk) and isinstance(v, str)}


def translate_all(strings, api_key, model):
    table = {}
    chunks = [strings[i:i + CHUNK_SIZE] for i in range(0, len(strings), CHUNK_SIZE)]
    for n, chunk in enumerate(chunks, 1):
        for attempt in range(4):
            try:
                table.update(gemini_translate(chunk, api_key, model))
                print(f"  번역 {n}/{len(chunks)} 완료 ({len(chunk)}개)")
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                print(f"  번역 {n}/{len(chunks)} 실패 (HTTP {e.code}): {detail}", file=sys.stderr)
                if e.code not in (429, 500, 503) or attempt == 3:
                    break
                time.sleep(30 * (attempt + 1))
            except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
                print(f"  번역 {n}/{len(chunks)} 실패: {e!r}", file=sys.stderr)
                if attempt == 3:
                    break
                time.sleep(10)
        if n < len(chunks):
            time.sleep(REQUEST_GAP)
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
    model = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"

    if out_path.exists() and os.environ.get("FORCE") != "1":
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("translation", {}).get("complete") or not api_key:
            print(f"{out_path.name} 이미 있음, 건너뜀")
            write_latest(existing)
            return

    print(f"{date} (UTC) 데이터 가져오는 중")
    sections = fetch_wiki(date)
    strings = collect_strings(sections)

    table = {}
    if api_key:
        print(f"{len(strings)}개 문장 번역 중 (모델: {model})")
        table = translate_all(strings, api_key, model)
    else:
        print("GEMINI_API_KEY 없음: 번역 없이 영어 원문만 저장")
    apply_translation(sections, table)

    data = {
        "date": date.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Wikipedia (en) — On this day",
        "translation": {
            "model": model if api_key else None,
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
