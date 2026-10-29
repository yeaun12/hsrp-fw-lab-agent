"""탐지 검증용 트래픽 생성기 (격리 랩 전용, 우리 테스트 서버 대상).

목적: 에이전트의 웹 공격 탐지·분석이 실제로 동작하는지 확인한다. 정상 요청 사이에 교과서적인
공격 시그니처(로그인 우회, UNION, 반사형 XSS)를 섞어 보내, 탐지기가 이를 걸러내고 AI가
성공 여부를 판정하는지 검증한다. 표준 라이브러리만 사용한다.

    python3 gen_traffic.py http://211.0.1.10
"""
import sys
import time
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://211.0.1.10"
UA = "Mozilla/5.0 (traffic-gen; lab detection test)"


def get(path):
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": UA}), timeout=5).read()
    except Exception as e:
        print("  (get err)", e)


def post(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers={"User-Agent": UA}), timeout=5).read()
    except Exception as e:
        print("  (post err)", e)


def q(s):
    return "/search?q=" + urllib.parse.quote(s)


# 1) 정상 트래픽 (탐지기가 오탐하지 않아야 하는 대조군)
NORMAL_SEARCH = ["USB", "키보드", "모니터", "웹캠", "거치대"]
NORMAL_LOGIN = [("hong", "wrongpw"), ("kim", "sales99"), ("hong", "pw1234")]

# 2) 공격 시그니처 (탐지기가 반드시 잡아야 하는 교과서 예시)
ATTACK_SEARCH = [
    "' OR '1'='1",                                   # boolean 기반
    "' UNION SELECT user, pass FROM users --",       # UNION, 계정 유출 시도
    "<script>alert(1)</script>",                     # 반사형 XSS
    "%27%20OR%201=1--",                              # URL 인코딩 우회 변형
    "x'; DROP TABLE products --",                    # 스택 쿼리(미지원이라 실패해야 정상)
]
ATTACK_LOGIN = [
    ("admin' --", "x"),                              # 로그인 우회
    ("' OR 1=1 --", "x"),                            # 로그인 우회
]


def main():
    print("[1] 정상 트래픽")
    for s in NORMAL_SEARCH:
        get(q(s)); time.sleep(0.3)
    for u, p in NORMAL_LOGIN:
        post("/login", {"user": u, "pw": p}); time.sleep(0.3)

    print("[2] 공격 시그니처 (탐지 검증용)")
    for s in ATTACK_SEARCH:
        print("  search:", s); get(q(s)); time.sleep(0.4)
    for u, p in ATTACK_LOGIN:
        print("  login :", u); post("/login", {"user": u, "pw": p}); time.sleep(0.4)
    print("done")


if __name__ == "__main__":
    main()
