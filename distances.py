"""
대한철인3종협회 페이지의 컬럼 헤더(Swim0.75km, Bike40km, Run10km 등)에서
대회/부문별 실제 거리를 수집한다.

부문 라벨의 첫 단어(예: '스탠다드', '하프코스', '아쿠아슬론')로 그룹화하여
그룹당 1개 sPart 페이지만 받아 헤더를 읽어 같은 그룹의 모든 부문에 적용한다.
→ 5년치 약 300~500 요청, 5~8분 소요.

출력: distances.json  =  { "대회명||부문라벨": {swim_km, bike_km, run_km}, ... }
"""
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.triathlon.or.kr"
LIST_URL = BASE + "/results/results/?sYear={year}"
RECORD_URL = BASE + "/results/results/record/?mode=record&tourcd={tourcd}"
RECORD_PART_URL = (
    BASE + "/results/results/record/?mode=record&tourcd={tourcd}&sPart={part}"
)

DELAY = 1.0
TIMEOUT = 30
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HERE = Path(__file__).parent
OUT = HERE / "distances.json"


def fetch(url: str, retries: int = 3) -> str:
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "TriDistanceFetcher/1.0"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            print(f"  retry {a+1}: {e}", file=sys.stderr, flush=True)
            time.sleep(2 * (a + 1))
    raise RuntimeError(f"failed: {url} ({last})")


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_tournaments(html: str):
    out = []
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if not m:
        return out
    for r in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
        tc = re.search(r"tourcd=(\d+)", r)
        nm = re.search(r"<strong>(.*?)</strong>", r, re.S)
        if tc and nm:
            out.append({"tourcd": tc.group(1), "name": strip_tags(nm.group(1))})
    return out


def parse_parts(html: str):
    parts = []
    seen = set()
    for sp, b in re.findall(r"<a[^>]*sPart=(\d+)[^>]*>(.*?)</a>", html, re.S):
        if sp in seen:
            continue
        seen.add(sp)
        parts.append({"sPart": sp, "label": strip_tags(b)})
    return parts


def parse_headers(html: str):
    ths = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
    return [strip_tags(t) for t in ths]


def parse_km(th_text: str):
    m = re.search(r"([0-9.]+)\s*km", th_text, re.I)
    return float(m.group(1)) if m else None


def parse_meters(th_text: str):
    """Sometimes 'Swim750m'."""
    m = re.search(r"([0-9.]+)\s*m\b", th_text, re.I)
    if m:
        return float(m.group(1)) / 1000.0
    return None


def parse_distance(th_text: str):
    return parse_km(th_text) or parse_meters(th_text)


def first_word(label: str) -> str:
    return label.split()[0] if label else ""


def run(years):
    distances = {}
    if OUT.exists():
        try:
            distances = json.loads(OUT.read_text(encoding="utf-8"))
            print(f"loaded {len(distances)} existing distance entries", flush=True)
        except Exception:
            distances = {}

    for year in years:
        print(f"== year {year} ==", flush=True)
        try:
            list_html = fetch(LIST_URL.format(year=year))
        except Exception as e:
            print(f"  skip year: {e}", flush=True)
            continue
        tours = parse_tournaments(list_html)
        print(f"  {len(tours)} tournaments", flush=True)
        time.sleep(DELAY)

        for ti, t in enumerate(tours, 1):
            print(f"  [{year} {ti}/{len(tours)}] {t['name'][:50]}", flush=True)
            try:
                idx = fetch(RECORD_URL.format(tourcd=t["tourcd"]))
            except Exception as e:
                print(f"    skip: {e}", flush=True)
                continue
            time.sleep(DELAY)
            parts = parse_parts(idx)
            if not parts:
                continue

            # 첫 단어로 그룹화
            groups = {}
            for p in parts:
                groups.setdefault(first_word(p["label"]), []).append(p)

            for gk, gps in groups.items():
                rep = gps[0]
                try:
                    h = fetch(
                        RECORD_PART_URL.format(tourcd=t["tourcd"], part=rep["sPart"])
                    )
                except Exception as e:
                    print(f"      skip part {gk}: {e}", flush=True)
                    continue
                time.sleep(DELAY)
                ths = parse_headers(h)
                if len(ths) < 10:
                    continue
                swim_k = parse_distance(ths[4])
                bike_k = parse_distance(ths[6])
                run_k = parse_distance(ths[8])
                for p in gps:
                    key = t["name"] + "||" + p["label"]
                    distances[key] = {
                        "swim_km": swim_k,
                        "bike_km": bike_k,
                        "run_km": run_k,
                        "raw": [ths[4], ths[6], ths[8]],
                    }
                print(
                    f"      [{gk}] swim={swim_k} bike={bike_k} run={run_k}", flush=True
                )

        OUT.write_text(
            json.dumps(distances, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  saved {len(distances)} entries → {OUT}", flush=True)

    print(f"\n== done ==  total {len(distances)} entries", flush=True)


if __name__ == "__main__":
    yrs = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [2021, 2022, 2023, 2024, 2025]
    run(yrs)
