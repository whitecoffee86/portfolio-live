# 🌳 장기포트 라이브 — 세팅 가이드

토스증권(미국 ETF) + 업비트(BTC·ETH) 잔고를 15분마다 읽어서, 비밀번호로 암호화한 뒤 GitHub Pages 대시보드에 올리는 구조예요.

```
[오라클 무료 VM · 고정 IP]  ──15분마다──▶  토스 Open API / 업비트 Open API
        │  collector.py  (API 키는 여기에만)
        ▼  AES 암호화
[GitHub: portfolio-live/data/portfolio.enc.json]
        ▼
[whitecoffee86.github.io/portfolio-live]  ← 휴대폰에서 비밀번호로 열기
```

저장소는 공개여도 괜찮아요. 올라가는 건 암호문뿐이고, 비밀번호 없이는 금액이 안 보여요.

---

## 1. 오라클 클라우드 VM 만들기 (약 15분)

1. https://www.oracle.com/kr/cloud/free/ 가입. 홈 리전은 **서울** 또는 **춘천** 선택 (나중에 못 바꿔요).
2. 콘솔 → **컴퓨트 > 인스턴스 > 인스턴스 생성**
   - 이미지: **Ubuntu 22.04 또는 24.04**
   - 모양: **VM.Standard.E2.1.Micro** (Always Free 표시 확인) — A1(ARM)도 OK
   - SSH 키: "키 쌍 생성" → 개인키 다운로드
3. 생성 후 **고정 IP로 바꾸기**: 인스턴스 → 연결된 VNIC → IPv4 주소 → 편집 → 공용 IP를 **예약된 공용 IP**로 변경.
   이걸 해야 재부팅해도 IP가 안 바뀌어서 토스·업비트 재등록이 필요 없어요.
4. 접속: `ssh -i 다운받은키.key ubuntu@공인IP` (윈도우는 PowerShell에서 동일)

> 💡 무료 계정은 오래 놀고 있는 VM을 회수할 수 있어요. 걱정되면 계정을 "종량제(PAYG)"로 업그레이드해 두면 Always Free 범위 안에선 여전히 0원이고 회수 대상에서 빠져요.

## 2. 수집기 설치

```bash
# VM에서 (저장소에 이 파일들을 먼저 올려둔 뒤)
git clone https://github.com/whitecoffee86/portfolio-live.git
cd portfolio-live/collector && bash setup.sh
```
마지막 줄에 **공인 IP**가 찍혀요. 이걸 3번에서 등록해요.

## 3. API 키 발급 (IP 등록 필수)

**토스증권** — PC에서 tossinvest.com 로그인 → 설정 → **Open API**
- 클라이언트 생성 → `client_id`, `client_secret` 복사
- **허용 IP 관리**에 VM 공인 IP 등록
- 권한은 조회만. 주문 권한은 켜지 마세요.

**업비트** — 업비트 → MY → **Open API 관리**
- 기능: **자산조회만** 체크 (주문·출금 ❌)
- IP 주소: VM 공인 IP
- Access Key / Secret Key 복사 (Secret은 한 번만 보여요)

## 4. GitHub 저장소 + 토큰

1. GitHub에서 새 저장소 **`portfolio-live`** (Public) 생성
2. 이 폴더 내용 전부 업로드 (`.gitignore` 포함 — `.env`·`config.json`이 올라가지 않게 막아줘요)
3. Settings → **Pages** → Branch: `main` / `(root)` → Save
4. 토큰: Settings → Developer settings → **Fine-grained tokens** → Generate
   - Repository access: **Only select → portfolio-live**
   - Permissions → Contents: **Read and write**
   - 만료일은 1년 정도로

## 5. .env · config.json 채우기

```bash
nano .env          # 키 4개, DASH_PASSPHRASE(대시보드 비번), GH_TOKEN
nano config.json   # 목표비중·월 적립금·이사 목표·원화 원금
```

**`krw_cost_seed` 꼭 채우기** — 환율 분해의 기준이에요.
토스 앱 → 종목 → 내 투자 → **원화 기준**으로 바꾸면 보이는 원금을 종목별로 넣어요.
비워두면 첫 수집일 환율을 매입 환율로 가정해서 환율 효과가 0부터 시작해요. (첫 실행 **전에** 넣어야 반영돼요)

## 6. 연결 확인 → 자동 실행

```bash
python3 collector.py --check     # 공인 IP, 토스 환율·계좌, 업비트 자산 목록
python3 collector.py             # 한 번 수집 + 업로드
crontab -e
```
맨 아래에 추가:
```
*/15 * * * * cd ~/portfolio-live/collector && /usr/bin/python3 collector.py >> run.log 2>&1
```
이제 `https://whitecoffee86.github.io/portfolio-live/` 를 열고 비밀번호 입력 → 끝.
홈 화면에 추가하면 앱처럼 쓸 수 있어요.

---

## 자주 막히는 곳

| 증상 | 원인 / 해결 |
|---|---|
| `토스: 허용되지 않은 IP` | WTS 허용 IP 목록에 VM IP가 없음. `--check`로 IP 다시 확인 |
| `업비트: no_authorization_ip` | 업비트 > Open API 관리 > **[변경]** 으로 허용 IP 수정 |
| 토스 현금이 0으로 나옴 | 키 권한에 매수가능금액 조회가 빠졌을 수 있음 → `config.json`의 `cash_override`에 직접 입력 |
| 대시보드에 "수집기 확인" 주황 칩 | 45분 넘게 새 데이터 없음 → `tail run.log` |
| 비밀번호를 바꾸고 싶음 | `.env`의 `DASH_PASSPHRASE` 수정 → 다음 수집부터 새 비번 |

## 계산 방식

- **기준가(좌수법)**: 시작일=1,000. 적립·입금은 좌수가 늘어날 뿐 기준가에 영향 없음 → 순수 운용 성과와 **고점 대비 낙폭**이 정확해요. 입출금은 "총자산 변화 − 시장 움직임"으로 자동 감지.
- **환율 분해**: 원화 손익 = 주가 효과 `(평가$ − 원금$) × 매입환율` + 환율 효과 `평가$ × (현재환율 − 매입환율)`. 추가 매수분은 그날 환율로 원금에 누적.
- **리밸런싱 밴드**: 목표 비중 ±25%(상대). 예) IAU 8.5% → 6.4~10.6%. 미달 종목은 DCA 때 2배 가중.
- **이사자금 예상**: 월 적립금을 매달 넣고 연 4/7/10% 복리 가정. 참고용 시뮬레이션이에요.

## 파일

```
index.html                 대시보드 (GitHub Pages)
data/portfolio.enc.json    수집기가 15분마다 덮어씀 (암호문)
collector/collector.py     수집기
collector/config.example.json · .env.example · setup.sh · requirements.txt
```
