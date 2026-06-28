#!/usr/bin/env python3
"""DART corp_code 매핑 생성 (1회 실행) -> dart_corp_map.json

DART 는 종목코드가 아닌 8자리 corp_code 로 조회한다. 이 스크립트는 DART 의 corpCode.xml
(전체 기업 고유번호 파일)을 받아 {종목코드: corp_code} 매핑을 만든다.

사용:
  DART_API_KEY=발급키 python tools/build_dart_corpmap.py
  -> 프로젝트 루트에 dart_corp_map.json 생성 (live 모드 DART provider 가 자동 사용)

네트워크 필요. 표준 라이브러리만 사용.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

URL = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"


def build_corpmap(key: str) -> tuple[dict, dict]:
    """DART corpCode.xml 다운로드 → (종목코드→corp_code, 종목코드→이름) 반환 + 파일 저장.
    실패 시 ({}, {}) 반환. 네트워크/키 필요. 실패 원인을 콘솔에 출력."""
    import urllib.request as _u
    try:
        try:
            with _u.urlopen(URL.format(key=key), timeout=30) as r:
                raw = r.read()
        except Exception as e:
            print(f"[corp_map] 오류: DART corpCode.xml 다운로드 실패 — {type(e).__name__}: {e}")
            print("[corp_map] → opendart.fss.or.kr 접속이 방화벽에 막혔거나 키가 잘못됐을 수 있습니다.")
            return {}, {}
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            xml_bytes = zf.read(zf.namelist()[0])
        except zipfile.BadZipFile:
            # 키 오류 등은 XML/JSON 에러 메시지가 그대로 옴
            msg = raw[:300].decode("utf-8", "ignore")
            print(f"[corp_map] 오류: 응답이 zip이 아닙니다(키 확인). 응답 일부: {msg}")
            return {}, {}
        root = ET.fromstring(xml_bytes)
        mapping: dict[str, str] = {}
        names: dict[str, str] = {}
        for el in root.iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            corp = (el.findtext("corp_code") or "").strip()
            cname = (el.findtext("corp_name") or "").strip()
            if stock and corp:
                mapping[stock] = corp
                if cname:
                    names[stock] = cname
        if mapping:
            with open("dart_corp_map.json", "w", encoding="utf-8") as fh:
                json.dump(mapping, fh, ensure_ascii=False)
            with open("dart_corp_names.json", "w", encoding="utf-8") as fh:
                json.dump(names, fh, ensure_ascii=False)
        else:
            print("[corp_map] 경고: corpCode.xml 파싱 결과 상장종목이 0개입니다(형식 변경 가능).")
        return mapping, names
    except Exception as e:
        print(f"[corp_map] 오류: {type(e).__name__}: {e}")
        return {}, {}


def main() -> int:
    key = os.getenv("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 환경변수를 설정하세요. 예: DART_API_KEY=xxxx python tools/build_dart_corpmap.py")
        return 1
    print("[1/3] corpCode.xml 다운로드 중...")
    with urllib.request.urlopen(URL.format(key=key), timeout=30) as r:
        raw = r.read()
    print("[2/3] 압축 해제 및 파싱...")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        xml_bytes = zf.read(zf.namelist()[0])
    except zipfile.BadZipFile:
        # 키 오류 등은 XML 에러 메시지가 그대로 옴
        print("실패: 응답이 zip 이 아닙니다. (키 확인)\n", raw[:300].decode("utf-8", "ignore"))
        return 1
    root = ET.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    names: dict[str, str] = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        cname = (el.findtext("corp_name") or "").strip()
        if stock and corp:                 # 상장사(종목코드 있는 것)만
            mapping[stock] = corp
            if cname:
                names[stock] = cname
    out = "dart_corp_map.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False)
    out2 = "dart_corp_names.json"
    with open(out2, "w", encoding="utf-8") as fh:
        json.dump(names, fh, ensure_ascii=False)
    print(f"[3/3] 완료: {out} ({len(mapping)} 종목), {out2} ({len(names)} 종목명)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
