# neotriathlon

대한철인3종협회 공개 기록을 이름으로 검색하고, 본인의 기록 추이와 부문 내 위치를 확인하는 정적 웹사이트.

## 사용

검색 사이트: https://yongsta0.github.io/neotriathlon/

이름을 입력 → (동명이인이면) 소속 선택 → 5년치 출전 기록, 추이 그래프, 부문 내 백분위 표시.

## 데이터

- 출처: [대한철인3종협회](https://www.triathlon.or.kr/) 공개 기록 페이지
- 범위: 최근 5년 (2021–2025)
- 컬럼: 순위·이름·소속·수영·T1·사이클·T2·런·총기록
- 동명이인은 **이름 + 소속 + 부문** 조합으로 구분 (협회 공개 데이터에 생년월일 없음)

## 데이터 갱신

```bash
python3 scraper.py 2021 2022 2023 2024 2025
git add data.json
git commit -m "data refresh"
git push
```

`scraper.py`는 Python 표준 라이브러리만 사용하며, 협회 서버 부담을 줄이기 위해 요청 간 1초 딜레이를 둡니다.

## 파일

- `index.html` — 검색 UI (Chart.js CDN 사용)
- `scraper.py` — 협회 사이트 스크래퍼
- `data.json` — 검색 페이지가 읽는 데이터
