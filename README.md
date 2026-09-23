# 역사 속 오늘

영어 위키백과 [On this day API](https://api.wikimedia.org/wiki/Feed_API/Reference/On_this_day)의 데이터를 Gemini로 번역해서, 역사 속 오늘 일어난 일을 보여 주는 사이트입니다.

- 날짜 기준은 **UTC**입니다. GitHub Actions가 매일 00:05 UTC(KST 09:05)에 갱신합니다.
- `scripts/build.py`: 데이터를 받아 번역한 뒤 `site/data/YYYY-MM-DD.json`과 `latest.json`으로 저장
- `site/`: 정적 페이지 (GitHub Pages로 배포)

## 설정
- Secret `GEMINI_API_KEY`: Gemini API 키 (없으면 영어 원문만 표시)
- Variable `GEMINI_MODEL` (선택): 번역 모델, 기본값 `gemini-2.5-flash`

## 로컬 실행
```bash
python scripts/build.py
python -m http.server 8765 --directory site
```
