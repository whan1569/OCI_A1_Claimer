# OCI A1 Claimer

Oracle Cloud Tokyo 리전에서 `VM.Standard.A1.Flex` 4 OCPU / 24 GB 인스턴스 생성을 자동 재시도하는 작은 Python 도구입니다.

## 동작

- OCI API로 A1 인스턴스 생성 시도
- `Out of capacity` 계열 오류면 10분 대기 후 재시도
- 생성 성공 시 RUNNING 상태까지 기다린 뒤 Public IP 출력
- 같은 `INSTANCE_DISPLAY_NAME`의 살아있는 인스턴스가 이미 있으면 중복 생성하지 않고 종료
- capacity 외 오류는 즉시 중단

## 준비

1. Python 3.11+
2. OCI 사용자 API Signing Key
3. 생성할 VM에 넣을 SSH public key
4. OCI Console에서 필요한 OCID 확인
   - Tenancy
   - User
   - Compartment
   - Subnet
   - Ubuntu 24.04 Minimal aarch64 Image

## 설치

```powershell
git clone https://github.com/whan1569/OCI_A1_Claimer.git
cd OCI_A1_Claimer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

그 다음 `.env`에 실제 OCI 값을 입력합니다.

## 실행

```powershell
python main.py
```

기본값은 다음과 같습니다.

- Region: `ap-tokyo-1`
- Shape: `VM.Standard.A1.Flex`
- OCPU: `4`
- Memory: `24 GB`
- Retry: `600초`
- Public IPv4: 할당

중단은 `Ctrl+C`입니다.

## 보안

- `.env`와 API private key는 Git에 올리지 않습니다.
- 이 저장소의 `.gitignore`가 `.env` 및 일반적인 private-key 확장자를 제외합니다.
