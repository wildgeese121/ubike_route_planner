import streamlit as st
import os
import json
import math
import requests
import urllib.parse
import re
from typing import Dict, List, Tuple
import google.generativeai as genai

st.set_page_config(page_title="新竹 Ubike 路線規劃助手", page_icon="🚲", layout="centered")

GOOGLE_MAPS_API_KEY = st.secrets["GOOGLE_MAPS_API_KEY"] 
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]    

UBIKE_JSON = "HsinChu_Ubike.json"
DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

@st.cache_data
def load_ubike_data(path=UBIKE_JSON) -> List[Dict]:
    if not os.path.exists(path):
        st.error(f"找不到檔案：{path}，請確認檔案位置。")
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    normalized = []
    for item in data:
        try:
            normalized.append({
                "name": item.get("站點名稱"),
                "lat": float(item.get("緯度")),
                "lng": float(item.get("經度")),
                "addr": item.get("站點位置"),
                "img": item.get("圖片")
            })
        except Exception:
            continue
    return normalized

def find_nearest_ubike(user_lat: float, user_lng: float, ubike_list: List[Dict], top_k=1):
    distances = []
    for ub in ubike_list:
        d = haversine(user_lat, user_lng, ub["lat"], ub["lng"])
        distances.append((d, ub))
    distances.sort(key=lambda x: x[0])
    return [u[1] for u in distances[:top_k]]

# Google Maps API
def google_distance_matrix(origins: List[str], destinations: List[str], mode: str="walking") -> Dict:
    params = {
        "origins": "|".join(origins),
        "destinations": "|".join(destinations),
        "mode": mode,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
    }
    resp = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()

def google_directions(origin: str, destination: str, mode: str="bicycling") -> Dict:
    params = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
    }
    resp = requests.get(DIRECTIONS_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()

def generate_maps_link(origin: str, destination: str, mode: str) -> str:
    base_url = "https://www.google.com/maps/dir/?api=1" 
    safe_origin = urllib.parse.quote(origin)
    safe_dest = urllib.parse.quote(destination)
    return f"{base_url}&origin={safe_origin}&destination={safe_dest}&travelmode={mode}"

#Google Gemini API
def call_gemini(summary):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    summary_for_ai = summary.copy()
    if 'links' in summary_for_ai:
        del summary_for_ai['links']

    prompt = f"""
    請你用中文，把以下交通路線資訊整理成清楚易懂的自然語言，給出建議：
    - 比較「Ubike + 步行」與「純公車」的總時間
    - 推薦理由（時間、轉乘、舒適度）
    - 語氣友善簡潔，適合一般民眾閱讀。
    輸入資料：
    {json.dumps(summary_for_ai, ensure_ascii=False, indent=2)}
    """
    response = model.generate_content(prompt)
    return response.text

def parse_dm(dm):
    try:
        el = dm["rows"][0]["elements"][0]
        if el.get("status") != "OK":
            return {}
        return {
            "distance_text": el.get("distance", {}).get("text"),
            "distance_m": el.get("distance", {}).get("value"),
            "duration_text": el.get("duration", {}).get("text"),
            "duration_s": el.get("duration", {}).get("value"),
            "status": el.get("status"),
        }
    except Exception:
        return {}

def plan_route(user_origin: Tuple[float,float], user_destination: Tuple[float,float], ubike_list: List[Dict]) -> Dict:
    origin_lat, origin_lng = user_origin
    dest_lat, dest_lng = user_destination

    nearest_from = find_nearest_ubike(origin_lat, origin_lng, ubike_list, top_k=3)
    nearest_to = find_nearest_ubike(dest_lat, dest_lng, ubike_list, top_k=3)

    ubike_start = nearest_from[0]
    ubike_end = nearest_to[0]

    ori_str = f"{origin_lat},{origin_lng}"
    start_str = f"{ubike_start['lat']},{ubike_start['lng']}"
    dest_str = f"{dest_lat},{dest_lng}"
    end_str = f"{ubike_end['lat']},{ubike_end['lng']}"

    dm1 = google_distance_matrix([ori_str], [start_str], mode="walking")
    dm2 = google_distance_matrix([start_str], [end_str], mode="bicycling") 
    dm3 = google_distance_matrix([end_str], [dest_str], mode="walking")
    
    transit = google_directions(ori_str, dest_str, mode="transit")

    link_walk_to_station = generate_maps_link(ori_str, start_str, "walking")
    link_bike_ride = generate_maps_link(start_str, end_str, "bicycling")
    link_walk_to_dest = generate_maps_link(end_str, dest_str, "walking")
    link_transit = generate_maps_link(ori_str, dest_str, "transit")

    walk_to_ubike = parse_dm(dm1)
    bike_leg = parse_dm(dm2)
    walk_from_ubike = parse_dm(dm3)

    transit_info = {}
    try:
        troute = transit["routes"][0]
        tlegs = troute.get("legs", [])
        total_seconds = sum([leg.get("duration", {}).get("value", 0) for leg in tlegs])
        transit_info = {"duration_s": total_seconds, "summary": troute.get("summary", "")}
    except Exception:
        transit_info = {}

    summary = {
        "origin_coords": (origin_lat, origin_lng),
        "dest_coords": (dest_lat, dest_lng),
        "ubike_start": ubike_start,
        "ubike_end": ubike_end,
        "walk_to_ubike": walk_to_ubike,
        "bike_leg": bike_leg,
        "walk_from_ubike": walk_from_ubike,
        "transit_option": transit_info,
        "links": {
            "walk1": link_walk_to_station,
            "bike": link_bike_ride,
            "walk2": link_walk_to_dest,
            "transit": link_transit
        }
    }
    return summary

def input_latlng(s):
    if not s:
        return None
    
    pattern = r"^(-?\d+(\.\d+)?),\s*(-?\d+(\.\d+)?)$"
    match = re.match(pattern, s.strip())

    if match:
        try:
            return float(match.group(1)), float(match.group(3))
        except ValueError:
            pass
    
    # Google Geocoding
    try:
        geocode_resp = google_directions(s, s, mode="walking")
        loc = geocode_resp["routes"][0]["legs"][0]["start_location"]
        return loc["lat"], loc["lng"]
    except Exception:
        return None


@st.cache_data(ttl=600)
def scrape_weather_final():
    try:
        url = "https://wttr.in/Hsinchu?m&format=%t|%C"
        
        resp = requests.get(url, timeout=10)
        
        if resp.status_code == 200:
            data = resp.text.strip().split("|")
            if len(data) == 2:
                return data[0], data[1]
        
        return "N/A", "N/A"
    except Exception as e:
        print(f"Scraping failed: {e}")
        return "N/A", "N/A"


def main():
    st.title("🚲 新竹 Ubike 智慧導航")

    # 載入資料
    ubike_list = load_ubike_data()
    if not ubike_list:
        return

    col1, col2 = st.columns(2)
    with col1:
        origin_input = st.text_input("📍 起點 (地址或 lat,lng)", "國立陽明交通大學第二餐廳")
    with col2:
        dest_input = st.text_input("🏁 終點 (地址或 lat,lng)", "新竹火車站")

    # weather
    with st.sidebar:
        st.header("🌤️ 新竹即時天氣")
        temp, condition = scrape_weather_final()
        st.info(f"🌡️ **{temp}** |  ☁️ **{condition}**")
        st.caption("資料來源：wttr.in")

    use_gemini = st.checkbox("使用 Gemini 分析路線", value=False)

    # 開始規劃
    if st.button("🚀 開始規劃", type="primary"):
        with st.spinner("正在搜尋最佳站點並計算路徑..."):
            origin = input_latlng(origin_input)
            destination = input_latlng(dest_input)

            if not origin or not destination:
                st.error("❌ 無法解析地址，請嘗試輸入更完整的地址或經緯度。")
                return
            try:
                summary = plan_route(origin, destination, ubike_list)
                
                st.success("✅ 計算完成！")
                
                # 顯示地圖
                map_data = [
                    {"lat": summary['origin_coords'][0], "lon": summary['origin_coords'][1], "color": "#FF0000"},
                    {"lat": summary['ubike_start']['lat'], "lon": summary['ubike_start']['lng'], "color": "#00FF00"},
                    {"lat": summary['ubike_end']['lat'], "lon": summary['ubike_end']['lng'], "color": "#00FF00"},
                    {"lat": summary['dest_coords'][0], "lon": summary['dest_coords'][1], "color": "#0000FF"},
                ]
                st.map(data=map_data, latitude="lat", longitude="lon", color="color", size=20, zoom=13)

                st.subheader("📋 Ubike 路線詳情")
                c1, c2, c3 = st.columns(3)
                
                links = summary.get("links", {})
                
                # 計算 Ubike 總時間 (步行1 + 騎車 + 步行2)
                t1 = summary['walk_to_ubike'].get('duration_s', 0)
                t2 = summary['bike_leg'].get('duration_s', 0)
                t3 = summary['walk_from_ubike'].get('duration_s', 0)
                total_ubike_min = int((t1 + t2 + t3) / 60)

                with c1:
                    st.markdown("**1. 步行前往借車**")
                    st.write(f"📍 {summary['ubike_start']['name']}")
                    st.write(f"⏱️ {summary['walk_to_ubike'].get('duration_text','N/A')}")
                    st.link_button("步行導航", links.get('walk1'))
                
                with c2:
                    st.markdown("**2. Ubike 騎乘**")
                    st.write(f"📍 往 {summary['ubike_end']['name']}")
                    bike_min = int(t2 / 60)
                    st.write(f"⏱️ 約 {bike_min} 分鐘")
                    st.link_button("騎車導航", links.get('bike'))

                with c3:
                    st.markdown("**3. 步行前往終點**")
                    st.write("🏁 到達目的地")
                    st.write(f"⏱️ {summary['walk_from_ubike'].get('duration_text','N/A')}")
                    st.link_button("步行導航", links.get('walk2'))
                
                st.info(f"🚲 **Ubike 方案總時間：約 {total_ubike_min} 分鐘**")
                st.divider()

                # 大眾運輸
                st.subheader("🚌 大眾運輸替代方案")
                
                transit_sec = summary['transit_option'].get('duration_s', 0)
                transit_link = links.get('transit')

                if transit_sec > 0:
                    transit_min = int(transit_sec / 60)
                    t_col1, t_col2 = st.columns([3, 1])
                    
                    with t_col1:
                        st.write(f"⏱️ **預估時間：約 {transit_min} 分鐘**")
                        
                        diff = transit_min - total_ubike_min
                        if diff > 0:
                            st.caption(f"💡 Ubike 方案比大眾運輸快約 {diff} 分鐘")
                        elif diff < 0:
                            st.caption(f"💡 大眾運輸比 Ubike 方案快約 {abs(diff)} 分鐘")
                        else:
                            st.caption("💡 兩種方式時間差不多")

                    with t_col2:
                        st.link_button("🚌 大眾運輸導航", transit_link)
                else:
                    st.warning("⚠️ 查無大眾運輸路線資料")

                st.divider()

                if use_gemini:
                    st.subheader("🤖 Gemini 路線分析與建議")
                    with st.spinner("Gemini 正在撰寫分析報告..."):
                        gemini_resp = call_gemini(summary)
                        st.markdown(gemini_resp)
                else:
                    st.info("💡 您未勾選 AI 助理，已跳過路線分析。")

            except Exception as e:
                st.error(f"發生錯誤: {str(e)}")

if __name__ == "__main__":
    main()