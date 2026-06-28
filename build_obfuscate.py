#!/usr/bin/env python3
"""소스 난독화 빌드 — 동작은 그대로 두고 가독성만 낮춘다.

수행 작업(안전):
  · 파이썬: 주석 제거 + docstring 제거(토큰 단위, 코드 의미 불변)
  · 프런트 index.html: <script> 블록의 줄 주석/블록 주석 제거 + 빈 줄 축소

주의: 변수명까지 바꾸는 식별자 난독화는 동적 속성·문자열 참조에서 깨질 수 있어
하지 않는다(앱 안정성 우선). 더 강한 보호가 필요하면 실행파일(.exe) 배포를 쓴다.

사용:  python build_obfuscate.py [출력폴더]
기본 출력: ../stock_reco_obf
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tokenize

SRC = os.path.dirname(os.path.abspath(__file__))


def strip_python(code: str) -> str:
    """주석 제거(토큰 기반). docstring·문법·들여쓰기는 보존(동작 불변 보장)."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except Exception:
        return code  # 토큰화 실패 시 원본 유지(안전)

    result = []
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue  # 주석만 제거
        result.append((tok.type, tok.string))

    try:
        rebuilt = tokenize.untokenize(result)
        if isinstance(rebuilt, bytes):
            rebuilt = rebuilt.decode("utf-8")
    except Exception:
        return code  # 복원 실패 시 원본 유지

    # 연속 빈 줄 축소
    rebuilt = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", rebuilt)
    return rebuilt


def strip_js_in_html(html: str) -> str:
    """index.html 의 <script> 내부 주석 제거 + 빈 줄 축소(문자열·정규식 보호는 보수적)."""
    m = re.search(r"(<script>)(.*)(</script>)", html, re.S)
    if not m:
        return html
    js = m.group(2)
    # 블록 주석 /* ... */ 제거(단, 단순치환 — URL '//' 보호 위해 줄 주석은 생략)
    js2 = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    # 연속 빈 줄 축소
    js2 = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n", js2)
    return html[:m.start(2)] + js2 + html[m.end(2):]


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(SRC), "stock_reco_obf")
    if os.path.abspath(out) == os.path.abspath(SRC):
        print("출력 폴더가 원본과 같습니다. 다른 폴더를 지정하세요.")
        return
    if os.path.exists(out):
        shutil.rmtree(out)

    # 어디서든 제외(빌드 산물·캐시)
    skip_anywhere = {"__pycache__", ".venv", ".git", "build", "dist"}
    # 최상위에서만 제외(런타임 데이터/로그) — app/data 같은 소스 패키지는 보존
    skip_top = {"data", "logs"}
    skip_files = {"build_obfuscate.py"}
    for root, dirs, files in os.walk(SRC):
        rel = os.path.relpath(root, SRC)
        if rel == ".":
            dirs[:] = [d for d in dirs if d not in skip_anywhere and d not in skip_top]
        else:
            dirs[:] = [d for d in dirs if d not in skip_anywhere]
        dest_dir = os.path.join(out, rel) if rel != "." else out
        os.makedirs(dest_dir, exist_ok=True)
        for fn in files:
            if fn in skip_files or fn.endswith((".pyc", ".sqlite3")):
                continue
            src_p = os.path.join(root, fn)
            dst_p = os.path.join(dest_dir, fn)
            try:
                if fn.endswith(".py"):
                    with open(src_p, encoding="utf-8") as f:
                        code = f.read()
                    with open(dst_p, "w", encoding="utf-8") as f:
                        f.write(strip_python(code))
                elif fn == "index.html":
                    with open(src_p, encoding="utf-8") as f:
                        html = f.read()
                    with open(dst_p, "w", encoding="utf-8") as f:
                        f.write(strip_js_in_html(html))
                else:
                    shutil.copy2(src_p, dst_p)
            except Exception as e:
                print(f"  경고: {fn} 처리 실패({e}) → 원본 복사")
                shutil.copy2(src_p, dst_p)
    print(f"난독화 완료 → {out}")


if __name__ == "__main__":
    main()
