# ⏰ 지각 방지 스케줄러 (Late Prevention Scheduler)

실시간 대중교통 데이터와 개인별 준비 시간을 반영하여 지각 확률을 계산하고 최적의 출발 시간을 추천해주는 웹 애플리케이션입니다.

## 🚀 주요 기능
- **실시간 경로 계산**: 카카오 및 ODsay API를 활용한 정확한 경로 탐색
- **지각 확률 분석**: 거리, 교통 상황, 개인별 '지각 성향'을 반영한 확률 제공
- **실시간 대중교통 정보**: 버스/지하철 도착 정보 실시간 연동 및 갱신
- **지도 시각화**: 상세한 이동 경로를 지도로 확인
- **출발 알림**: 브라우저 알림을 통한 출발 시간 안내

## 🛠 실행 방법 (로컬)

1. **라이브러리 설치**
   ```bash
   pip install -r requirements.txt
   ```

2. **환경 변수 설정**
   `.env` 파일을 생성하고 다음 키를 입력하세요:
   ```env
   ODSAY_API_KEY=YOUR_ODSAY_KEY
   KAKAO_REST_API_KEY=YOUR_KAKAO_REST_KEY
   KAKAO_JS_API_KEY=YOUR_KAKAO_JS_KEY
   ```

3. **서버 실행**
   ```bash
   python main.py
   ```
   이후 `http://localhost:8000` 접속

## ⚙️ 배포 방법 (Render.com)

1. GitHub 저장소(Private 추천)를 생성하고 소스 코드를 푸시합니다. (**.env는 제외**)
2. [Render](https://render.com/)에서 **Web Service**를 생성합니다.
3. 배포 설정:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. **Environment Variables** 메뉴에서 위 3개의 API 키를 등록합니다.
