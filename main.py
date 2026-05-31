import os
import math
import httpx
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import urllib.parse

load_dotenv()

app = FastAPI(title="지각 방지 스케줄러")

@app.on_event("startup")
async def startup_event():
    # 빌드 캐시 방지를 위한 정적 파일 체크 및 로그
    import os
    if os.path.exists("static/index.html"):
        size = os.path.getsize("static/index.html")
        print(f"--- STATIC FILE LOADED: static/index.html ({size} bytes) ---")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

KAKAO_REST_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_JS_KEY   = os.getenv("KAKAO_JS_API_KEY", "")
ODSAY_API_KEY  = os.getenv("ODSAY_API_KEY", "")

# ── 모델 ─────────────────────────────────────────────────
class CalcRequest(BaseModel):
    origin: str
    destination: str
    origin_lat: Optional[float] = None
    origin_lng: Optional[float] = None
    dest_lat: Optional[float] = None
    dest_lng: Optional[float] = None
    appointment_time: str
    prep_time: int = 20
    lateness_bias: int = 10
    mode: str = "car"
    detour_weight: Optional[float] = 1.0
    transit_prefs: Optional[dict] = None

    class Config:
        extra = "allow"

class SimRequest(BaseModel):
    travel_time: int
    prep_time: int
    lateness_bias: int
    appointment_time: str
    offsets: list[int] = [0, 5, 10, 15, 20]

# ── 유틸 ─────────────────────────────────────────────────
def sigmoid(x): 
    if x < -100: return 0.0
    if x > 100: return 1.0
    return 1 / (1 + math.exp(-x))
def calc_late_prob(travel, prep, bias, remaining):
    return round(sigmoid((travel + prep + bias - remaining) / 10), 4)
def rec_depart(appt, travel, prep, bias, safety=7):
    return appt - timedelta(minutes=travel + prep + bias + safety)
def late_msg(p):
    if p < .10: return "😊 여유롭습니다! 천천히 준비하세요."
    if p < .30: return "🟡 살짝 빠듯합니다. 서두르는 게 좋겠어요."
    if p < .60: return "🟠 지각 위험! 지금 당장 움직이세요."
    if p < .85: return "🔴 지각 거의 확실합니다. 연락을 권장해요."
    return "🚨 이미 늦었습니다. 양해를 구하세요."

# 하버사인 공식 (직선거리 계산)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0 # 지구 반지름 (km)
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── 카카오 API ─────────────────────────────────────────────
async def addr_to_coords(address: str) -> Optional[tuple[float, float]]:
    print(f"Searching address: {address}")
    if "," in address:
        parts = address.split(",")
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except: pass
        
    # 1. 카카오 Local API 시도
    print(f"Using Kakao Key: {KAKAO_REST_KEY[:5]}...")
    if KAKAO_REST_KEY:
        url = "https://dapi.kakao.com/v2/local/search/address.json"
        headers = {
            "Authorization": f"KakaoAK {KAKAO_REST_KEY}",
            "Referer": "https://late-scheduler-final.onrender.com"
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                res = await c.get(url, headers=headers, params={"query": address})
                print(f"Kakao Addr Status: {res.status_code}")
                if res.status_code == 200:
                    docs = res.json().get("documents", [])
                    if docs:
                        return float(docs[0]["y"]), float(docs[0]["x"])
                    
                # 키워드 검색(장소명) 백업
                url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
                res_kw = await c.get(url_kw, headers=headers, params={"query": address})
                print(f"Kakao KW Status: {res_kw.status_code}")
                if res_kw.status_code == 200:
                    docs_kw = res_kw.json().get("documents", [])
                    if docs_kw:
                        return float(docs_kw[0]["y"]), float(docs_kw[0]["x"])
        except Exception as e:
            print(f"Kakao API Connection Error: {e}")

    # 2. 카카오 실패 시 Nominatim Fallback
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": address, "format": "json", "limit": 1}
        headers = {"User-Agent": "LateScheduler/1.0", "Accept-Language": "ko-KR,ko"}
        async with httpx.AsyncClient() as c:
            res = await c.get(url, headers=headers, params=params)
            docs = res.json()
            if docs:
                return float(docs[0]["lat"]), float(docs[0]["lon"])
    except: pass
    
    return None

async def get_kakao_route(o_lat, o_lng, d_lat, d_lng):
    """카카오 자동차 길찾기 (car 모드일 때만 사용)"""
    if not KAKAO_REST_KEY:
        return None, [], 0
    url = "https://apis-navi.kakaomobility.com/v1/directions"
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_KEY}",
        "Referer": "https://late-scheduler-final.onrender.com"
    }
    params = {"origin": f"{o_lng},{o_lat}", "destination": f"{d_lng},{d_lat}", "priority": "RECOMMEND"}
    async with httpx.AsyncClient() as c:
        try:
            res = await c.get(url, headers=headers, params=params)
            data = res.json()
            routes = data.get("routes", [])
            if routes and routes[0].get("result_code") == 0:
                dur = routes[0]["summary"]["duration"]
                dist = round(routes[0]["summary"]["distance"] / 1000, 1) # km
                pts = []
                for sec in routes[0].get("sections", []):
                    for road in sec.get("roads", []):
                        vx = road.get("vertexes", [])
                        for i in range(0, len(vx)-1, 2):
                            pts.append({"lng": vx[i], "lat": vx[i+1]})
                return max(1, round(dur/60)), pts, dist
        except Exception as e:
            print("Kakao API Error:", e)
    return None, [], 0

# ── 모드별 이동시간 추정 모델 ──
def estimate_by_mode(mode: str, straight_dist_km: float, detour_weight: float = 1.0):
    # 직선거리에 가중치 적용 (기본 1.3배 * 사용자 설정 가중치)
    real_dist = straight_dist_km * 1.3 * detour_weight
    
    if mode == "transit":
        # 대중교통: 평균 18km/h + 대기/환승 기본 10분
        mins = (real_dist / 18.0) * 60 + 10
    elif mode == "walk":
        # 도보: 평균 4.5km/h
        mins = (real_dist / 4.5) * 60
    elif mode == "bike":
        # 자전거: 평균 15km/h
        mins = (real_dist / 15.0) * 60
    else: # car (API 실패 시 폴백)
        # 자동차 추정: 도심 평균 25km/h + 신호대기 등
        mins = (real_dist / 25.0) * 60 + 5

    return max(1, int(round(mins))), round(real_dist, 1)

async def get_bus_realtime(station_id: int, bus_no: str):
    if not ODSAY_API_KEY: return None
    try:
        url = f"https://api.odsay.com/v1/api/realtimeStation?stationID={station_id}&apiKey={urllib.parse.quote(ODSAY_API_KEY)}"
        # ODsay API 키가 http://localhost/ 도메인으로 등록되어 있음 - 서버사이드 호출이므로 고정
        headers = {'Referer': 'http://localhost/'}
        async with httpx.AsyncClient(timeout=8.0) as c:
            res = await c.get(url, headers=headers)
            data = res.json()
            if "error" in data:
                print(f"ODsay realtime error: {data['error']}")
                return None
            if "result" in data and "bus" in data["result"]:
                for b in data["result"]["bus"]:
                    if b.get("busNo") == bus_no:
                        return b.get("arrmsg1", b.get("arrmsg2", "정보 없음"))
    except Exception as e:
        print(f"Bus realtime error: {e}")
    return None

async def get_odsay_transit(o_lat, o_lng, d_lat, d_lng, prefs=None):
    if not ODSAY_API_KEY:
        return None

    # API 레벨 필터는 너무 깐깐해서 0(전체)으로 가져온 뒤 파이썬에서 고릅니다.
    enc_key = urllib.parse.quote(ODSAY_API_KEY)
    url = f"https://api.odsay.com/v1/api/searchPubTransPathT?SX={o_lng}&SY={o_lat}&EX={d_lng}&EY={d_lat}&apiKey={enc_key}&SearchType=0"
    try:
        headers = {'Referer': 'http://localhost/'}
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            res = await c.get(url, headers=headers)
            data = res.json()
            if "error" in data: return None
            
            if "result" in data and data["result"].get("path"):
                original_paths = data["result"]["path"]
                
                # 프론트엔드 설정값 확인
                subway_on = prefs.get("subway_priority", True) if prefs else True
                bus_on = prefs.get("bus_include", True) if prefs else True
                no_village = prefs.get("no_village_bus", False) if prefs else False
                
                paths = []
                for p in original_paths:
                    is_village = False
                    has_bus = False
                    has_subway = False
                    
                    for sub in p.get("subPath", []):
                        t_type = sub.get("trafficType") # 1:subway, 2:bus, 3:walk
                        if t_type == 2: 
                            has_bus = True
                            bus_no = ""
                            for lane in sub.get("lane", []):
                                bus_no += lane.get("busNo", "")
                            if "마을버스" in bus_no: is_village = True
                        if t_type == 1: has_subway = True

                    # 필터링 로직
                    if no_village and is_village: continue
                    if not bus_on and has_bus and has_subway: continue # 버스 끄면 환승 경로 제외
                    if not bus_on and has_bus and not has_subway: continue # 버스만 있는 경로 제외
                    
                    paths.append(p)

                # 만약 필터링해서 아무것도 안 남으면, 사용자에게 보여주기 위해 다시 상위 결과 사용
                if not paths: paths = original_paths[:3]
                
                paths = paths[:3] # 최대 3개
                options = []
                best_time = None
                
                for idx, path in enumerate(paths):
                    total_time = path["info"]["totalTime"]
                    if best_time is None: best_time = total_time
                    ptype = path["pathType"]
                    pt_str = "지하철" if ptype == 1 else "버스" if ptype == 2 else "지하철+버스"
                    
                    steps = []
                    polyline = []
                    
                    for sub in path.get("subPath", []):
                        ttype = sub.get("trafficType")
                        time = sub.get("sectionTime", 0)
                        dist = sub.get("distance", 0)
                        sid = sub.get("startID")
                        
                        step_poly = []
                        # 정류장 좌표 수집
                        if sub.get("passStopList"):
                            for st in sub["passStopList"].get("stations", []):
                                if st.get("x") and st.get("y"):
                                    pt = {"lat": float(st["y"]), "lng": float(st["x"])}
                                    step_poly.append(pt)
                                    polyline.append(pt)
                                    
                        # 도보 혹은 좌표 부족 시 보완
                        if not step_poly:
                            if sub.get("startY") and sub.get("startX"):
                                step_poly.append({"lat": float(sub["startY"]), "lng": float(sub["startX"])})
                            if sub.get("endY") and sub.get("endX"):
                                step_poly.append({"lat": float(sub["endY"]), "lng": float(sub["endX"])})
                        
                        if ttype == 3: # 걷기
                            steps.append({"type": "walk", "time": time, "dist": dist, "desc": "걷기", "polyline": step_poly})
                        elif ttype == 1: # 지하철
                            lane = sub["lane"][0].get("name", "지하철") if sub.get("lane") else "지하철"
                            steps.append({
                                "type": "subway", "time": time, "lane": lane,
                                "start": sub.get("startName", ""), "end": sub.get("endName", ""),
                                "stationCount": sub.get("stationCount", 0),
                                "startLat": sub.get("startY"), "startLng": sub.get("startX"),
                                "endLat": sub.get("endY"), "endLng": sub.get("endX"),
                                "sid": sid,
                                "polyline": step_poly
                            })
                        elif ttype == 2: # 버스
                            bus = sub["lane"][0].get("busNo", "버스") if sub.get("lane") else "버스"
                            steps.append({
                                "type": "bus", "time": time, "lane": bus,
                                "start": sub.get("startName", ""), "end": sub.get("endName", ""),
                                "stationCount": sub.get("stationCount", 0),
                                "startLat": sub.get("startY"), "startLng": sub.get("startX"),
                                "endLat": sub.get("endY"), "endLng": sub.get("endX"),
                                "sid": sid,
                                "polyline": step_poly
                            })

                    options.append({
                        "id": idx + 1,
                        "type": pt_str,
                        "totalTime": total_time,
                        "steps": steps,
                        "polyline": polyline,
                        "realtime": None
                    })

                # 실시간 정보 추가 (첫 번째 탑승 수단 기준)
                for opt in options:
                    try:
                        first_pt = next((s for s in opt["steps"] if s["type"] in ["bus", "subway"]), None)
                        if first_pt:
                            if first_pt["type"] == "bus" and first_pt.get("sid"):
                                rt = await get_bus_realtime(first_pt["sid"], first_pt["lane"])
                                opt["realtime"] = rt
                            elif first_pt["type"] == "subway":
                                opt["realtime"] = "운행 중 (정시성 높음)"
                    except: pass

                return {"time": best_time, "options": options}
    except Exception as e:
        print("ODsay Error:", e)
    return None

async def estimate_travel(o: tuple, d: tuple, mode: str, detour_weight: float = 1.0, transit_prefs: dict = None):
    st_dist = haversine(o[0], o[1], d[0], d[1])
    transit_info = None

    if mode == "car":
        t, pts, dist = await get_kakao_route(*o, *d)
        if t:
            # 카카오 API는 자체적으로 경로 우회율이 반영되어 있음
            return t, True, o, d, pts, dist, None
            
    if mode == "transit":
        od = await get_odsay_transit(o[0], o[1], d[0], d[1], transit_prefs)
        if od:
            transit_info = od["options"]
            # 대중교통도 API 결과 시간에 가중치 적용
            est_t = round(od["time"] * detour_weight)
            est_dist = round(st_dist * 1.3, 1)
        else:
            est_t, est_dist = estimate_by_mode(mode, st_dist, detour_weight)
    else:
        # 도보, 자전거 등
        est_t, est_dist = estimate_by_mode(mode, st_dist, detour_weight)
    
    # 지도의 파란 선을 위해 OSRM에서 경로 점(pts)만 가져옵니다!
    pts = []
    if mode != "transit":
        try:
            # OSRM 프로필 선택 (driving, walking, cycling)
            profile = "walking" if mode == "walk" else "cycling" if mode == "bike" else "driving"
            url = f"http://router.project-osrm.org/route/v1/{profile}/{o[1]},{o[0]};{d[1]},{d[0]}?overview=full&geometries=geojson"
            async with httpx.AsyncClient() as c:
                res = await c.get(url)
                data = res.json()
                if data.get("code") == "Ok":
                    coords = data["routes"][0]["geometry"]["coordinates"]
                    pts = [{"lat": pt[1], "lng": pt[0]} for pt in coords]
        except: pass
    
    return est_t, False, o, d, pts, est_dist, transit_info

# ── 엔드포인트 ─────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/api/config")
async def config():
    return {"kakao_js_key": KAKAO_JS_KEY}

@app.post("/api/reverse_geocode")
async def reverse_geocode(body: dict):
    """좌표 → 주소"""
    lat, lng = body.get("lat"), body.get("lng")
    
    # 1. 카카오 시도 (Local API)
    if KAKAO_REST_KEY:
        url = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
        headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
        try:
            async with httpx.AsyncClient() as c:
                res = await c.get(url, headers=headers, params={"x": lng, "y": lat})
                if res.status_code == 200:
                    docs = res.json().get("documents", [])
                    if docs:
                        addr = docs[0]["address"]["address_name"] if docs[0].get("address") else ""
                        road = docs[0].get("road_address", {})
                        road_name = road["address_name"] if road else ""
                        return {"address": road_name or addr, "found": True}
        except: pass
        
    # 2. 실패 시 Nominatim Fallback
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lng, "format": "json"}
        headers = {"User-Agent": "LateScheduler/1.0", "Accept-Language": "ko-KR,ko"}
        async with httpx.AsyncClient() as c:
            res = await c.get(url, headers=headers, params=params)
            data = res.json()
            if "display_name" in data:
                return {"address": data["display_name"].split(",")[0], "found": True}
    except: pass
            
    return {"found": False}

class OSRMRequest(BaseModel):
    origin: dict
    dest: dict

@app.post("/api/osrm_route")
async def osrm_route(req: OSRMRequest):
    """OSRM 무료 라우팅 API를 이용해 경로의 좌표 배열 반환"""
    o, d = req.origin, req.dest
    url = f"http://router.project-osrm.org/route/v1/driving/{o['lng']},{o['lat']};{d['lng']},{d['lat']}?overview=full&geometries=geojson"
    try:
        async with httpx.AsyncClient() as c:
            res = await c.get(url)
            data = res.json()
            if data.get("code") == "Ok":
                route = data["routes"][0]
                dist = round(route["distance"] / 1000, 1)
                dur = round(route["duration"] / 60)
                coords = route["geometry"]["coordinates"]
                pts = [{"lat": pt[1], "lng": pt[0]} for pt in coords]
                return {"found": True, "points": pts, "distance_km": dist, "duration_minutes": dur}
    except Exception as e:
        print("OSRM Error:", e)
    return {"found": False}

@app.post("/api/calculate")
async def calculate(req: CalcRequest):
    try:
        now = datetime.now()
        
        # 날짜 형식 처리 (더 튼튼하게)
        appt = now + timedelta(hours=1) # 기본값
        try:
            val = req.appointment_time.strip()
            if len(val) <= 5: # "17:55" 형태
                h, m = map(int, val.split(':'))
                appt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            else:
                appt = datetime.fromisoformat(val.replace(' ', 'T'))
        except Exception as e:
            print(f"Time parsing error: {e}")

        # 타임존 전쟁 종결: 둘 다 Naive로 강제 통일해서 뺍니다.
        remaining = (appt.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 60

        if req.origin_lat is not None and req.origin_lng is not None:
            o = (req.origin_lat, req.origin_lng)
        else:
            o = await addr_to_coords(req.origin)

        if req.dest_lat is not None and req.dest_lng is not None:
            d = (req.dest_lat, req.dest_lng)
        else:
            d = await addr_to_coords(req.destination)

        if not o or not d:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="주소를 좌표로 변환할 수 없습니다. 지도 화면에서 위치를 직접 클릭하거나, 주소를 확인해주세요.")

        travel, is_kakao, o_coords, d_coords, pts, dist, transit_info = await estimate_travel(
            o, d, req.mode, 
            detour_weight=req.detour_weight if req.detour_weight is not None else 1.0, 
            transit_prefs=req.transit_prefs
        )
        
        prob    = calc_late_prob(travel, req.prep_time, req.lateness_bias, remaining)
        rec_dep = rec_depart(appt, travel, req.prep_time, req.lateness_bias)
        arrival = now + timedelta(minutes=travel)

        return {
            "late_probability": prob,
            "late_percent": round(prob * 100, 1),
            "recommended_departure_time": rec_dep.isoformat(timespec="seconds"),
            "expected_arrival_time": arrival.isoformat(timespec="seconds"),
            "travel_time": travel,
            "prep_time": req.prep_time,
            "remaining_minutes": round(remaining, 1),
            "kakao_api_used": is_kakao,
            "distance_km": dist,
            "message": late_msg(prob),
            "mode": req.mode,
            "origin_coords":  {"lat": o_coords[0], "lng": o_coords[1]} if o_coords else None,
            "dest_coords":    {"lat": d_coords[0], "lng": d_coords[1]} if d_coords else None,
            "route_points":   pts,
            "transit_steps":  transit_info
        }
    except Exception as e:
        print(f"CALCULATION ERROR: {e}")
        import traceback
        traceback.print_exc()
        from fastapi import HTTPException
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"서버 오류가 발생했습니다: {str(e)}")


@app.post("/api/simulate")
async def simulate(req: SimRequest):
    try:
        now = datetime.now()
        val = req.appointment_time.strip()
        if len(val) <= 5: # "17:55"
            h, m = map(int, val.split(':'))
            appt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            appt = datetime.fromisoformat(val.replace(' ', 'T'))
            
        now = now.replace(tzinfo=None)
        appt = appt.replace(tzinfo=None)
        
        results = []
        for offset in req.offsets:
            dep = now + timedelta(minutes=offset)
            remaining = (appt - dep).total_seconds() / 60
        prob = calc_late_prob(req.travel_time, req.prep_time, req.lateness_bias, remaining)
        results.append({
            "offset_minutes": offset,
            "depart_at": dep.isoformat(timespec="seconds"),
            "arrival_at": (dep + timedelta(minutes=req.travel_time)).isoformat(timespec="seconds"),
            "late_probability": prob,
            "late_percent": round(prob * 100, 1),
            "message": late_msg(prob),
        })
        return {"simulations": results}
    except Exception as e:
        print(f"SIMULATION ERROR: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/realtime")
async def api_realtime(req: dict):
    sid = req.get("sid")
    lane = req.get("lane")
    ttype = req.get("type") # 'bus' or 'subway'
    
    if not sid or not lane:
        return {"realtime": "정보 부족"}
        
    if ttype == "bus":
        rt = await get_bus_realtime(sid, lane)
        return {"realtime": rt or "정보 없음"}
    elif ttype == "subway":
        # 지하철은 현재 정시성 안내로 대체 (필요시 시간표 API 연동 가능)
        return {"realtime": "운행 중 (정시)"}
        
    return {"realtime": None}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
