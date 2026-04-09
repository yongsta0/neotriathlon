"""
대한철인3종협회 (triathlon.or.kr) 기록 스크래퍼
사용법: python3 scraper.py [연도 ...] [--append]
        예) python3 scraper.py 2018 2019 2020 --append
        기본 연도: 2025
--append 플래그: 기존 DB를 drop하지 않고 지정 연도만 추가
출력: results.sqlite + data.json (검색 페이지에서 사용)

시간 포맷은 저장 시 HH:MM:SS (3단)으로 정규화됨.
원본이 HH:MM:SS:ms (4단)이거나 H:MM:SS (1자리시)여도 통일.
"""
import json
import re
import sqlite3
import sys
import time
import urllib.request
import ssl
from pathlib import Path

BASE = "https://www.triathlon.or.kr"
LIST_URL = BASE + "/results/results/?sYear={year}"
RECORD_URL = BASE + "/results/results/record/?mode=record&tourcd={tourcd}"
RECORD_PART_URL = BASE + "/results/results/record/?mode=record&tourcd={tourcd}&sPart={part}"

DELAY = 1.0  # 요청 간 1초 대기 (서버 매너)
RETRIES = 3
TIMEOUT = 30

HERE = Path(__file__).parent
DB_PATH = HERE / "results.sqlite"
JSON_PATH = HERE / "data.json"
JSON_MIN_PATH = HERE / "data.min.json"
JSON_ARCHIVE_PATH = HERE / "data.archive.min.json"
SPLIT_YEAR_CUTOFF = 2022  # rows with year >= this go in the "recent" file loaded first

# 협회 사이트 인증서 체인 문제 우회 (최소 범위)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch(url: str) -> str:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "TriResultIndexer/1.0 (personal record lookup)",
                    "Accept-Language": "ko,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            print(f"  ! retry {attempt}/{RETRIES}: {e}", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"failed: {url} ({last})")


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_time(s: str) -> str:
    """HH:MM:SS[:ms] 또는 H:MM:SS 등을 통일된 HH:MM:SS로 변환."""
    if not s or s == "00:00:00":
        return s
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return s
    if len(nums) == 4:
        h, m, sec, ms = nums
        total = h * 3600 + m * 60 + sec + round(ms / 1000)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    if len(nums) == 3:
        h, m, sec = nums
        return f"{h:02d}:{m:02d}:{sec:02d}"
    if len(nums) == 2:
        m, sec = nums
        return f"00:{m:02d}:{sec:02d}"
    return s


def parse_tournament_list(html: str):
    """대회 목록 페이지에서 (tourcd, name, date, place) 추출"""
    out = []
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if not m:
        return out
    rows = re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)
    for r in rows:
        tc = re.search(r"tourcd=(\d+)", r)
        name = re.search(r"<strong>(.*?)</strong>", r, re.S)
        date = re.search(r'data-title="대회일">([^<]+)<', r)
        place = re.search(r"장소\s*:\s*([^<]+)", r)
        if tc and name:
            out.append(
                {
                    "tourcd": tc.group(1),
                    "name": strip_tags(name.group(1)),
                    "date": date.group(1).strip() if date else "",
                    "place": place.group(1).strip() if place else "",
                }
            )
    return out


def parse_parts(html: str):
    """기록 페이지에서 부문(sPart, label) 추출"""
    parts = []
    seen = set()
    for sp, body in re.findall(r"<a[^>]*sPart=(\d+)[^>]*>(.*?)</a>", html, re.S):
        if sp in seen:
            continue
        seen.add(sp)
        parts.append({"sPart": sp, "label": strip_tags(body)})
    return parts


def parse_records(html: str):
    """기록 테이블의 데이터 행 추출 (10컬럼)"""
    out = []
    tbodies = re.findall(r"<tbody>(.*?)</tbody>", html, re.S)
    if not tbodies:
        return out
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbodies[-1], re.S)
    for r in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        cells = [strip_tags(c) for c in cells]
        if len(cells) == 10 and cells[0].isdigit():
            out.append(
                {
                    "rank": int(cells[0]),
                    "name": cells[1],
                    "bib": cells[2],
                    "club": cells[3],
                    "swim": normalize_time(cells[4]),
                    "t1": normalize_time(cells[5]),
                    "bike": normalize_time(cells[6]),
                    "t2": normalize_time(cells[7]),
                    "run": normalize_time(cells[8]),
                    "total": normalize_time(cells[9]),
                }
            )
    return out


def init_db(db_path: Path, drop: bool = True):
    conn = sqlite3.connect(db_path)
    if drop:
        conn.executescript("DROP TABLE IF EXISTS records;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS records (
            year INTEGER,
            tourcd TEXT,
            tour_name TEXT,
            tour_date TEXT,
            tour_place TEXT,
            part_label TEXT,
            rank INTEGER,
            name TEXT,
            bib TEXT,
            club TEXT,
            swim TEXT,
            t1 TEXT,
            bike TEXT,
            t2 TEXT,
            run TEXT,
            total TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_name ON records(name);
        """
    )
    conn.commit()
    return conn


def scrape_year(conn, year: int):
    print(f"== Scraping year {year} ==", flush=True)
    list_html = fetch(LIST_URL.format(year=year))
    tournaments = parse_tournament_list(list_html)
    print(f"  found {len(tournaments)} tournaments", flush=True)

    cur = conn.cursor()
    total_records = 0
    for ti, t in enumerate(tournaments, 1):
        print(f"[{year} {ti}/{len(tournaments)}] {t['name']} ({t['date']})", flush=True)
        time.sleep(DELAY)
        try:
            tour_html = fetch(RECORD_URL.format(tourcd=t["tourcd"]))
        except Exception as e:
            print(f"  ! skip tournament: {e}")
            continue
        parts = parse_parts(tour_html)
        if not parts:
            print(f"  (no parts found, skipping)")
            continue
        print(f"  {len(parts)} parts")

        for pi, p in enumerate(parts, 1):
            time.sleep(DELAY)
            try:
                rec_html = fetch(
                    RECORD_PART_URL.format(tourcd=t["tourcd"], part=p["sPart"])
                )
            except Exception as e:
                print(f"    ! skip part {p['label']}: {e}")
                continue
            records = parse_records(rec_html)
            for r in records:
                cur.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        year,
                        t["tourcd"],
                        t["name"],
                        t["date"],
                        t["place"],
                        p["label"],
                        r["rank"],
                        r["name"],
                        r["bib"],
                        r["club"],
                        r["swim"],
                        r["t1"],
                        r["bike"],
                        r["t2"],
                        r["run"],
                        r["total"],
                    ),
                )
            total_records += len(records)
            print(f"    [{pi}/{len(parts)}] {p['label']}: {len(records)} rows", flush=True)
        conn.commit()

    print(f"  → {year}년 {total_records}개 기록 추가\n", flush=True)
    return total_records


def export_json(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT tour_name, tour_date, tour_place, part_label, rank, name, club,"
        " swim, t1, bike, t2, run, total FROM records ORDER BY tour_date, name"
    )
    rows = [
        {
            "tour": r[0],
            "date": r[1],
            "place": r[2],
            "part": r[3],
            "rank": r[4],
            "name": r[5],
            "club": r[6],
            "swim": r[7],
            "t1": r[8],
            "bike": r[9],
            "t2": r[10],
            "run": r[11],
            "total": r[12],
        }
        for r in cur.fetchall()
    ]
    JSON_PATH.write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    print(f"JSON export: {JSON_PATH} ({len(rows)} rows)")

    # Compact columnar format — split by year for progressive loading on mobile
    def _build_compact(rows_subset):
        tours_list, parts_list, clubs_list = [], [], []
        tour_idx, part_idx, club_idx = {}, {}, {}
        def _idx(s, lst, mp):
            s = s or ""
            if s not in mp:
                mp[s] = len(lst); lst.append(s)
            return mp[s]
        compact_rows = []
        for r in rows_subset:
            compact_rows.append([
                _idx(r["tour"], tours_list, tour_idx),
                _idx(r["part"], parts_list, part_idx),
                _idx(r.get("club") or "", clubs_list, club_idx),
                r["rank"], r["name"], r.get("date", ""),
                r.get("swim", ""), r.get("t1", ""),
                r.get("bike", ""), r.get("t2", ""),
                r.get("run", ""), r.get("total", ""),
            ])
        return {"t": tours_list, "p": parts_list, "c": clubs_list, "r": compact_rows}

    def _year_of(r): return (r.get("date") or "")[:4]
    recent = [r for r in rows if _year_of(r) >= str(SPLIT_YEAR_CUTOFF)]
    archive = [r for r in rows if _year_of(r) < str(SPLIT_YEAR_CUTOFF) or not r.get("date")]

    JSON_MIN_PATH.write_text(
        json.dumps(_build_compact(recent), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    JSON_ARCHIVE_PATH.write_text(
        json.dumps(_build_compact(archive), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Compact export: {JSON_MIN_PATH} ({JSON_MIN_PATH.stat().st_size/1024/1024:.1f}MB, {len(recent)} rows {SPLIT_YEAR_CUTOFF}+)")
    print(f"Archive export: {JSON_ARCHIVE_PATH} ({JSON_ARCHIVE_PATH.stat().st_size/1024/1024:.1f}MB, {len(archive)} rows <{SPLIT_YEAR_CUTOFF})")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    append_mode = "--append" in flags
    years = [int(a) for a in args] if args else [2025]
    conn = init_db(DB_PATH, drop=not append_mode)
    # append 모드에서는 해당 연도 기록만 삭제 후 다시 삽입 (중복 방지)
    if append_mode:
        for y in years:
            conn.execute("DELETE FROM records WHERE year=?", (y,))
        conn.commit()
    grand = 0
    for y in years:
        try:
            grand += scrape_year(conn, y)
        except Exception as e:
            print(f"!! year {y} failed: {e}", flush=True)
    print(f"\n=== 이번 실행 {grand}개 기록 추가 → {DB_PATH} ===", flush=True)
    # 최종 JSON export (전체 레코드 포함)
    export_json(conn)
    conn.close()
