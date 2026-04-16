from __future__ import annotations

from flask import Flask, render_template, request, jsonify
import os
import re
import time
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# ===== 카테고리 전체 =====
CATEGORIES = {
    "전체": "https://www.smu.ac.kr/kor/life/notice.do",
    "메인공지": {
        "글로벌": "https://www.smu.ac.kr/kor/life/notice.do?mode=list&srCategoryId1=190",
        "진로취업": "https://www.smu.ac.kr/kor/life/notice.do?mode=list&srCategoryId1=162",
        "등록/장학": "https://www.smu.ac.kr/kor/life/notice.do?mode=list&srCategoryId1=22",
        "사회 봉사": "https://www.smu.ac.kr/kor/life/notice.do?mode=list&srCategoryId1=21&srCampus=&srSearchKey=&srSearchVal=",
        "비교과 일반": "https://www.smu.ac.kr/kor/life/notice.do?mode=list&srCategoryId1=420",
    },
    "학부(과)/전공": {
        "컴퓨터과학전공": "https://cs.smu.ac.kr/cs/community/notice.do",
        "자유전공학부대학": "https://sls.smu.ac.kr/sls/community/notice.do",
        "역사콘텐츠전공": "https://www.smu.ac.kr/history/community/notice.do",
        "영어교육과": "https://www.smu.ac.kr/engedu/community/notice.do",
        "게임전공": "https://www.smu.ac.kr/game01/community/notice.do",
        "애니메이션전공": "https://animation.smu.ac.kr/animation/community/notice.do",
        "스포츠건강관리전공": "https://sports.smu.ac.kr/smpe/admission/notice.do",
        "경영학부": "https://smubiz.smu.ac.kr/smubiz/community/notice.do",
        "휴먼AI공학전공": "https://hi.smu.ac.kr/hi/community/notice.do",
        "식품영양학전공": "https://food.smu.ac.kr/foodnutrition/community/notice.do",
        "국가안보학과": "https://ns.smu.ac.kr/sdms/community/notice.do",
        "가족복지학과": "https://www.smu.ac.kr/smfamily/community/notice.do",
        "화공신소재전공": "https://icee.smu.ac.kr/ichemistry/community/notice.do",
        "국어교육과": "https://www.smu.ac.kr/koredu/community/notice.do",
        "글로벌경영학과": "https://gbiz.smu.ac.kr/newmajoritb/board/notice.do",
    },
    "기숙사": {
        "상명 행복생활관": "https://dormitory.smu.ac.kr/dormi/happy/happy_notice.do",
        "스뮤하우스": "https://dormitory.smu.ac.kr/dormi/smu/smu_notice.do",
    },
    "대학원": "https://grad.smu.ac.kr/grad/board/notice.do",
    "공학교육인증센터": "https://icee.smu.ac.kr/icee/community/notice.do",
}

SESSION = requests.Session()
HEADERS_DEFAULT = {"User-Agent": "Mozilla/5.0"}

NOTICE_SELECTORS = [
    "table.board_list tbody tr",
    "table.boardList tbody tr",
    "table tbody tr",
    "tbody tr",
    "ul.board-list li",
    "ul.board-thumb-wrap li",
    "table.board-table tbody tr",
    "div.board_list li",
]

RE_DATE_WRITTEN = re.compile(r"작성일\s*[:：]?\s*(20\d{2}[./-]\d{2}[./-]\d{2})")
RE_DATE_PUBLISHED = re.compile(r"게시일\s*[:：]?\s*(20\d{2}[./-]\d{2}[./-]\d{2})")
RE_DATE_ANY = re.compile(r"\b(20\d{2}[./-]\d{2}[./-]\d{2})\b")
RE_AUTHOR = re.compile(r"(?:글쓴이|작성자)\s*([^\s/|]+)")
RE_PREFIX = re.compile(r"^(?:상명|서울|천안)\s*\[[^\]]+\]\s*")
ATTACHMENT_EXT_RE = re.compile(r"\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|zip)$", re.I)

_MEM_CACHE: dict[str, tuple[float, list[dict]]] = {}


def cache_get(url: str):
    now = time.time()
    value = _MEM_CACHE.get(url)
    if not value:
        return None
    expires_at, items = value
    if now <= expires_at:
        return items
    _MEM_CACHE.pop(url, None)
    return None


def cache_set(url: str, items: list[dict], ttl_sec: int = 60):
    _MEM_CACHE[url] = (time.time() + ttl_sec, items)


def clean_notice_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    title = RE_PREFIX.sub("", title)
    title = re.sub(r"\s*NEW$", "", title, flags=re.I).strip()
    return title


def sort_key(item: dict) -> tuple[str, str]:
    date_text = (item.get("date") or "").replace(".", "-").replace("/", "-")
    return (date_text, item.get("title") or "")


def dedupe_items(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for item in sorted(items, key=sort_key, reverse=True):
        key = ((item.get("link") or "").strip(), (item.get("title") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_notice_list(html: str, base: str, source: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    elems = []

    for sel in NOTICE_SELECTORS:
        elems = soup.select(sel)
        if elems:
            break
    if not elems:
        return []

    items: list[dict] = []
    for el in elems[:80]:
        anchors = [
            a for a in el.find_all("a", href=True)
            if not ATTACHMENT_EXT_RE.search(a.get("href", ""))
        ]
        if not anchors:
            continue

        notice_anchor = None
        for cand in anchors:
            cand_text = cand.get_text(" ", strip=True)
            if not cand_text:
                continue
            if RE_PREFIX.match(cand_text):
                continue
            notice_anchor = cand
            break

        if notice_anchor is None:
            notice_anchor = anchors[-1]

        title = clean_notice_title(notice_anchor.get_text(" ", strip=True))
        if not title:
            continue

        link = urljoin(base, notice_anchor.get("href") or "")

        author = ""
        writer_el = (
            el.find(class_="writer")
            or el.find("td", {"data-role": "writer"})
            or el.find("td", class_="writer")
        )
        if writer_el:
            author = writer_el.get_text(strip=True)

        date = ""
        date_el = (
            el.find(class_="date")
            or el.find("td", {"data-role": "date"})
            or el.find("td", class_="date")
        )
        if date_el:
            date = date_el.get_text(strip=True)

        text_all = " ".join(el.stripped_strings)
        if not author:
            author_match = RE_AUTHOR.search(text_all)
            if author_match:
                author = author_match.group(1).strip()

        if not date:
            date_match = RE_DATE_WRITTEN.search(text_all) or RE_DATE_PUBLISHED.search(text_all) or RE_DATE_ANY.search(text_all)
            if date_match:
                date = date_match.group(1).replace(".", "-").replace("/", "-")

        items.append({
            "title": title,
            "link": link,
            "author": author,
            "date": date,
            "source": source,
        })

    return dedupe_items(items)


def fetch_one(url: str, source: str = "") -> list[dict]:
    if not url:
        return []

    cache_key = f"{source}|{url}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        response = SESSION.get(url, headers=HEADERS_DEFAULT, timeout=6)
        response.raise_for_status()
        items = parse_notice_list(response.text or "", url, source=source)
        cache_set(cache_key, items, ttl_sec=60)
        return items
    except Exception as e:
        print(f"[FETCH ERR] url={url} source={source} err={e}")
        return []


@app.route("/fetch")
def fetch_api():
    group = request.args.get("group")
    sub = request.args.get("sub")

    flat = {}
    for g, v in CATEGORIES.items():
        if isinstance(v, dict):
            flat.update(v)
        else:
            flat[g] = v

    if sub:
        return jsonify({"items": fetch_one(flat.get(sub, ""), source=sub)})

    if group:
        val = CATEGORIES.get(group)
        if isinstance(val, dict):
            results: list[dict] = []
            with ThreadPoolExecutor(max_workers=6) as ex:
                futures = {
                    ex.submit(fetch_one, url, sub_name): sub_name
                    for sub_name, url in val.items()
                }
                try:
                    for future in as_completed(futures, timeout=8):
                        try:
                            results.extend(future.result())
                        except Exception as e:
                            print(f"[GROUP ERR] group={group} source={futures[future]} err={e}")
                except TimeoutError:
                    pass
            return jsonify({"items": dedupe_items(results)})

        return jsonify({"items": fetch_one(val, source=group)})

    return jsonify({"items": []})


@app.route("/")
def index():
    groups = {g: (list(v.keys()) if isinstance(v, dict) else []) for g, v in CATEGORIES.items()}
    return render_template("index.html", groups=groups)


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
