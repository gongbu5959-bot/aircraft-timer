import asyncio
import html
import os
import time
from collections import OrderedDict

import gradio as gr
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse


# 링크 재생에는 키가 필요 없습니다.
# 앱 내부 유튜브 검색을 사용하려면 두 번째 ""에 키를 넣으세요.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

GEOCODING_URL = os.getenv(
    "GEOCODING_URL",
    "https://geocoding-api.open-meteo.com/v1/search",
)

server = FastAPI()
request_lock = asyncio.Lock()
cache = OrderedDict()
last_request = 0.0


async def cached_request(key, url, params, ttl):
    global last_request

    async with request_lock:
        saved = cache.get(key)

        if saved and time.monotonic() - saved[0] < ttl:
            cache.move_to_end(key)
            return saved[1]

        await asyncio.sleep(
            max(0, 1.1 - (time.monotonic() - last_request))
        )
        last_request = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=18) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"User-Agent": "StudyFlightTimer/3.0"},
                )
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as exc:
            message = "검색 서비스 오류입니다. 잠시 후 다시 시도하세요."

            if exc.response.status_code in (403, 429):
                message = (
                    "요청이 제한되었습니다. "
                    "유튜브라면 API 키와 할당량도 확인하세요."
                )

            raise HTTPException(502, message) from None

        except (httpx.RequestError, ValueError):
            raise HTTPException(
                502,
                "검색 서버에 연결하지 못했습니다. 인터넷 연결을 확인하세요.",
            ) from None

        cache[key] = (time.monotonic(), data)
        cache.move_to_end(key)

        while len(cache) > 300:
            cache.popitem(last=False)

        return data


@server.get("/search")
async def search_cities(
    q: str = Query(min_length=2, max_length=100),
):
    query = q.strip()

    if len(query) < 2:
        raise HTTPException(400, "도시 이름을 두 글자 이상 입력하세요.")

    data = await cached_request(
        ("city", query.casefold()),
        GEOCODING_URL,
        {
            "name": query,
            "count": 10,
            "language": "ko",
            "format": "json",
        },
        86400,
    )

    results = []

    for item in data.get("results", []):
        parts = list(
            dict.fromkeys(
                item[k]
                for k in ("name", "admin1", "country")
                if item.get(k)
            )
        )

        results.append({
            "name": " · ".join(parts),
            "lat": item["latitude"],
            "lng": item["longitude"],
        })

    return results


@server.get("/youtube-search")
async def youtube_search(
    q: str = Query(min_length=1, max_length=150),
):
    if not YOUTUBE_API_KEY:
        raise HTTPException(
            503,
            "YouTube API 키가 설정되지 않았습니다.",
        )

    query = q.strip()

    if not query:
        raise HTTPException(400, "검색어를 입력하세요.")

    data = await cached_request(
        ("youtube", query.casefold()),
        "https://www.googleapis.com/youtube/v3/search",
        {
            "key": YOUTUBE_API_KEY,
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoEmbeddable": "true",
            "maxResults": 6,
            "relevanceLanguage": "ko",
        },
        900,
    )

    return [
        {
            "id": item["id"]["videoId"],
            "title": html.unescape(
                item["snippet"].get("title", "")
            ),
            "channel": html.unescape(
                item["snippet"].get("channelTitle", "")
            ),
        }
        for item in data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]


PAGE = r"""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    background: #101827;
    color: #edf3fc;
    font-family: system-ui, -apple-system, sans-serif;
}

main { max-width: 1400px; margin: auto; padding: 24px; }

header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 22px;
}

h1 { margin: 6px 0; font-size: 28px; }

.eyebrow {
    font-size: 11px;
    letter-spacing: 3px;
    color: #a6bdd7;
}

.badge {
    padding: 9px 12px;
    background: #203e43;
    border-radius: 30px;
    color: #b7e8df;
    font-size: 12px;
    white-space: nowrap;
}

.card, aside {
    border: 1px solid #33435a;
    background: #192438;
    border-radius: 18px;
    padding: 18px;
}

fieldset {
    border: 0;
    padding: 0;
    margin: 0;
    min-width: 0;
}

.fields {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}

label {
    display: block;
    color: #b6c8dc;
    font-size: 13px;
}

input, select, button { font: inherit; }

input, select {
    width: 100%;
    padding: 12px;
    border: 1px solid #4b5e76;
    border-radius: 10px;
    background: #172439;
    color: white;
    font-size: 16px;
    min-width: 0;
    color-scheme: dark;
}

.search-row {
    display: flex;
    gap: 7px;
    margin-top: 8px;
}

select {
    margin-top: 8px;
    font-size: 13px;
}

.selected {
    margin-top: 8px;
    color: #b9e7d9;
    font-size: 12px;
    overflow-wrap: anywhere;
}

button {
    min-height: 46px;
    padding: 11px 15px;
    border: 0;
    border-radius: 10px;
    background: #2b3a52;
    color: white;
    cursor: pointer;
}

button:disabled { opacity: .4; cursor: default; }

button:focus-visible,
input:focus-visible,
select:focus-visible,
a:focus-visible {
    outline: 3px solid #ffc978;
    outline-offset: 2px;
}

.primary, button.chosen {
    background: #b9e7d9;
    color: #173d36;
    font-weight: 700;
}

.mode-buttons {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 8px;
    margin: 18px 0 14px;
}

.time-panel {
    padding: 14px;
    border-radius: 12px;
    background: #111e31;
}

.time-panel input {
    margin-top: 8px;
    max-width: 370px;
}

.controls {
    display: flex;
    flex-wrap: wrap;
    gap: 9px;
    margin-top: 18px;
}

#showVideo { margin-left: auto; }

.error {
    color: #ffb9a8;
    font-size: 13px;
    line-height: 1.6;
}

.error:empty { display: none; }

.dashboard {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    margin: 22px 0 16px;
}

#routeLabel {
    font-size: 17px;
    font-weight: 700;
    overflow-wrap: anywhere;
}

#status, #arrivalLabel {
    margin-top: 8px;
    color: #abc0d8;
    font-size: 13px;
}

.clock { text-align: right; flex-shrink: 0; }
.clock small { color: #a6bdd7; font-size: 11px; }

#timer {
    font-size: clamp(27px, 4vw, 42px);
    font-variant-numeric: tabular-nums;
}

.workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
}

.workspace.with-video {
    grid-template-columns: minmax(0, 1fr) 360px;
}

.map-shell {
    overflow: hidden;
    border-radius: 18px;
    border: 1px solid #42536b;
}

#map { height: 470px; background: #263c51; }

.map-footer {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding: 13px;
    color: #bfd0e1;
    background: #192438;
    font-size: 12px;
}

progress {
    display: block;
    width: 100%;
    height: 7px;
    accent-color: #a9dfcf;
}

aside { min-width: 0; }
[hidden] { display: none !important; }

.panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.panel-header h2 { font-size: 17px; }

.tabs {
    display: flex;
    gap: 8px;
    margin: 12px 0 16px;
}

.tabs button { flex: 1; }

#youtubeLink { margin-top: 8px; }
#playLink { width: 100%; margin-top: 10px; }

#player {
    width: 100%;
    height: 220px;
    border: 0;
    border-radius: 12px;
    margin-top: 14px;
    background: #090e17;
}

#videoResults {
    max-height: 320px;
    overflow: auto;
    margin-top: 12px;
}

.video-result {
    display: flex;
    gap: 10px;
    width: 100%;
    margin-bottom: 8px;
    text-align: left;
    align-items: center;
}

.video-result img { width: 95px; border-radius: 7px; }

.video-result span {
    min-width: 0;
    font-size: 12px;
    line-height: 1.5;
}

.video-result small {
    display: block;
    color: #b7c6d8;
    margin-top: 5px;
}

.note {
    font-size: 12px;
    color: #9bb0c9;
    line-height: 1.8;
}

a { color: #b9e7d9; }

.aircraft-icon { background: none; border: 0; }

.aircraft {
    width: 42px;
    height: 42px;
    filter: drop-shadow(0 2px 2px white);
    transform-origin: center;
}

.aircraft svg { width: 100%; height: 100%; display: block; }

/* 실제 사진을 사용하는 창밖 풍경 */
#scenery {
    height: 470px;
    padding: 24px;
    background: linear-gradient(120deg, #536071, #1f2938);
}

.scenery-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    max-width: 630px;
    margin: 0 auto 16px;
}

#sceneLabel { font-size: 13px; }

.window-frame {
    width: min(570px, 100%);
    height: 335px;
    margin: auto;
    padding: 13px;
    border-radius: 45% 45% 35% 35% / 25% 25% 30% 30%;
    background: linear-gradient(130deg, #b3bdc7, #4e5967);
    box-shadow: 0 0 0 5px #273440, 0 15px 35px #0005;
}

.sky {
    height: 100%;
    position: relative;
    overflow: hidden;
    border-radius: inherit;
    background: #172439;
    isolation: isolate;
}

#flightPhoto {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    z-index: 0;
    filter: var(--photo-filter, none);
    transition: filter 2.5s;
    animation: photo-flight 45s ease-in-out infinite alternate paused;
}

.sky[data-time="day"] {
    --photo-filter: brightness(1.02) saturate(1.05);
}

.sky[data-time="dawn"] {
    --photo-filter: brightness(.85) sepia(.2) saturate(.85);
}

.sky[data-time="sunset"] {
    --photo-filter: brightness(.86) sepia(.25) saturate(1.35);
}

.sky[data-time="night"] {
    --photo-filter: brightness(.28) saturate(.6);
}

.weather-veil {
    position: absolute;
    inset: 0;
    background: #263344;
    opacity: 0;
    z-index: 1;
    transition: opacity 2s;
    pointer-events: none;
}

.sky[data-weather="cloudy"] .weather-veil { opacity: .18; }
.sky[data-weather="rain"] .weather-veil { opacity: .35; }

.precipitation {
    position: absolute;
    inset: -100%;
    z-index: 2;
    pointer-events: none;
    opacity: 0;
    animation: falling 1.3s linear infinite paused;
}

.sky[data-weather="rain"] .precipitation {
    opacity: .55;
    background: repeating-linear-gradient(
        110deg,
        transparent 0 25px,
        #d9eeff70 26px 27px,
        transparent 28px 65px
    );
}

.sky[data-weather="snow"] .precipitation {
    opacity: .8;
    background-image: radial-gradient(
        2px 2px, white 98%, transparent
    );
    background-size: 45px 50px;
    animation-duration: 6s;
}

.sky::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 3;
    pointer-events: none;
    background: linear-gradient(
        120deg,
        #ffffff14,
        transparent 28%,
        transparent 70%,
        #ffffff0d
    );
    box-shadow: inset 0 0 25px #0006;
}

#photoNotice {
    position: absolute;
    inset: 0;
    z-index: 4;
    display: grid;
    place-items: center;
    padding: 24px;
    background: #101827d9;
    color: #e6edf6;
    text-align: center;
    font-size: 13px;
    line-height: 1.8;
}

body.flying #flightPhoto,
body.flying .precipitation {
    animation-play-state: running;
}

@keyframes photo-flight {
    from { transform: scale(1.08) translate(-1%, -.5%); }
    to { transform: scale(1.17) translate(1%, .5%); }
}

@keyframes falling {
    from { transform: translateY(-30px); }
    to { transform: translateY(70px); }
}

/* 탑승권 뜯기 */
#ticketOverlay {
    position: fixed;
    inset: 0;
    z-index: 10000;
    display: grid;
    place-items: center;
    padding: 20px;
    background: #07101bd9;
}

.ticket {
    display: flex;
    width: min(540px, 100%);
    color: #1b3448;
}

.ticket-main {
    flex: 1;
    min-width: 0;
    padding: 28px 22px;
    background: #fff8e9;
    border-radius: 18px 0 0 18px;
}

.ticket-main small {
    color: #687887;
    letter-spacing: 2px;
}

#ticketRoute { line-height: 1.7; overflow-wrap: anywhere; }

.ticket-stub {
    display: grid;
    place-items: center;
    width: 85px;
    border-left: 2px dashed #8a9bab;
    border-radius: 0 18px 18px 0;
    background: #b9e7d9;
    writing-mode: vertical-rl;
    letter-spacing: 3px;
}

.tearing .ticket-main { animation: tear-left 1.2s forwards; }
.tearing .ticket-stub { animation: tear-right 1.2s forwards; }

@keyframes tear-left {
    0%, 30% { transform: none; opacity: 1; }
    100% {
        transform: translateX(-65px) rotate(-9deg);
        opacity: 0;
    }
}

@keyframes tear-right {
    0%, 30% { transform: none; opacity: 1; }
    100% {
        transform: translate(85px, 45px) rotate(22deg);
        opacity: 0;
    }
}

@media (max-width: 850px) {
    .workspace.with-video {
        grid-template-columns: minmax(0, 1fr);
    }
    #player { height: 260px; }
}

@media (max-width: 600px) {
    main { padding: 16px 12px; }
    h1 { font-size: 23px; }
    .badge { font-size: 10px; }
    .fields { grid-template-columns: minmax(0, 1fr); }
    .mode-buttons { grid-template-columns: 1fr; }
    .controls button { flex: 1; }
    #showVideo { flex-basis: 100%; margin-left: 0; }
    #routeLabel { font-size: 14px; }
    #map, #scenery { height: 390px; }
    #scenery { padding: 18px 13px; }
    .window-frame { height: 280px; }
}

@media (prefers-reduced-motion: reduce) {
    #flightPhoto,
    .precipitation,
    .tearing .ticket-main,
    .tearing .ticket-stub {
        animation: none !important;
        transition: none;
    }
}
</style>
</head>

<body>
<main>
    <header>
        <div>
            <div class="eyebrow">STUDY FLIGHT</div>
            <h1>항공기 타이머</h1>
        </div>
        <div class="badge">나만의 공부 여행</div>
    </header>

    <section class="card">
        <fieldset id="places">
            <div class="fields">
                <div>
                    <label for="originQuery">출발지 도시 검색</label>
                    <div class="search-row">
                        <input id="originQuery" maxlength="100"
                               placeholder="예: 서울, 런던">
                        <button id="originSearch">검색</button>
                    </div>
                    <select id="originResults"
                            aria-label="출발지 검색 결과" hidden></select>
                    <div id="originSelected" class="selected">
                        선택됨: 인천국제공항
                    </div>
                </div>

                <div>
                    <label for="destinationQuery">목적지 도시 검색</label>
                    <div class="search-row">
                        <input id="destinationQuery" maxlength="100"
                               placeholder="예: 파리, 뉴욕, 도쿄">
                        <button id="destinationSearch">검색</button>
                    </div>
                    <select id="destinationResults"
                            aria-label="목적지 검색 결과" hidden></select>
                    <div id="destinationSelected" class="selected">
                        검색 결과에서 선택하세요.
                    </div>
                </div>
            </div>
        </fieldset>

        <div class="mode-buttons">
            <button id="manualMode" class="chosen" aria-pressed="true">
                도착 시간 직접 설정
            </button>
            <button id="flightMode" aria-pressed="false">
                운항 시간 따르기
            </button>
            <button id="viewScenery" aria-pressed="false">
                바깥 풍경 보기
            </button>
        </div>

        <div id="manualPanel" class="time-panel">
            <label for="arrivalInput">
                원하는 도착 날짜와 시각 · 현재 기기 시간 기준
            </label>
            <input id="arrivalInput" type="datetime-local">
        </div>

        <div id="flightPanel" class="time-panel" hidden>
            <strong id="estimatedDuration">목적지를 선택하세요.</strong>
            <div class="note">
                거리 ÷ 850km/h + 이착륙 여유 30분으로 계산한 예상 시간입니다.
                실제 항공편 시간표는 아닙니다.
            </div>
        </div>

        <div class="controls">
            <button id="depart" class="primary" disabled>출발</button>
            <button id="pause" disabled>일시정지</button>
            <button id="reset" disabled>종료</button>
            <button id="showVideo" aria-expanded="false">
                영상 찾아보기
            </button>
        </div>

        <p id="error" class="error" role="alert"></p>
    </section>

    <div class="dashboard">
        <div>
            <div id="routeLabel">
                인천국제공항 → 목적지를 선택하세요
            </div>
            <div id="status" role="status">
                목적지를 검색해 주세요.
            </div>
            <div id="arrivalLabel"></div>
        </div>
        <div class="clock">
            <small>도착까지 남은 시간</small>
            <div id="timer" role="timer">00:25:00</div>
        </div>
    </div>

    <section id="workspace" class="workspace">
        <div class="map-shell">
            <div id="map"></div>

            <div id="scenery" hidden>
                <div class="scenery-header">
                    <span id="sceneLabel"></span>
                    <button id="randomScene">풍경 바꾸기</button>
                </div>

                <div class="window-frame">
                    <div id="sky" class="sky"
                         data-time="day" data-weather="clear">
                        <img id="flightPhoto"
                             alt="비행기에서 촬영한 실제 창밖 풍경" hidden>
                        <div class="weather-veil"></div>
                        <div class="precipitation"></div>
                        <div id="photoNotice" role="status">
                            실제 풍경 사진을 불러오는 중이에요.
                        </div>
                    </div>
                </div>

                <p class="note" style="text-align:center">
                    실제 촬영 사진 · 시간대 색감과 날씨는 가상 효과
                </p>
            </div>

            <progress id="progress" value="0" max="100"
                      aria-label="운항 진행률"></progress>

            <div class="map-footer">
                <span id="distance">목적지 선택 후 경로 표시</span>
                <span id="percent">0% 운항</span>
            </div>
        </div>

        <aside id="videoPanel" hidden>
            <div class="panel-header">
                <h2>기내 엔터테인먼트</h2>
                <button id="closeVideo" aria-label="영상 패널 닫기">
                    ✕
                </button>
            </div>

            <div id="videoTabs" class="tabs"
                 role="tablist" aria-label="유튜브 이용 방식">
                <button id="searchTab" role="tab"
                        aria-controls="searchPane"
                        aria-selected="false" tabindex="-1">
                    유튜브 검색
                </button>
                <button id="linkTab" class="chosen" role="tab"
                        aria-controls="linkPane" aria-selected="true">
                    링크 붙여넣기
                </button>
            </div>

            <div id="searchPane" role="tabpanel"
                 aria-labelledby="searchTab" hidden>
                <label for="youtubeQuery">듣고 싶은 음악이나 영상</label>
                <div class="search-row">
                    <input id="youtubeQuery" maxlength="150"
                           placeholder="예: 공부할 때 듣는 재즈">
                    <button id="searchVideo" class="primary">검색</button>
                </div>
                <p id="youtubeHint" class="note"></p>
                <p id="videoError" class="error" role="alert"></p>
                <div id="videoResults"></div>
            </div>

            <div id="linkPane" role="tabpanel"
                 aria-labelledby="linkTab">
                <label for="youtubeLink">유튜브 영상 주소</label>
                <input id="youtubeLink" type="url"
                       placeholder="https://www.youtube.com/watch?v=...">
                <button id="playLink" class="primary">
                    영상 불러오기 · 재생
                </button>
                <p id="linkError" class="error" role="alert"></p>
                <p class="note">
                    일반 영상 · 공유 링크 · Shorts · 라이브 주소 지원.
                    링크 재생에는 API 키가 필요 없어요.
                </p>
            </div>

            <iframe id="player" title="유튜브 플레이어"
                    allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
                    allowfullscreen
                    referrerpolicy="strict-origin-when-cross-origin"
                    hidden></iframe>

            <p class="note">
                자동 재생이 안 되면 영상의 ▶ 버튼을 누르세요.
                외부 재생이 제한된 영상은 다른 영상을 선택하세요.
                패널을 닫으면 재생이 멈춥니다.
            </p>
        </aside>
    </section>

    <p class="note">
        지도 클릭으로 목적지를 지정할 수도 있어요.
        도시 검색 결과는 공항이 아닌 도시 중심 좌표일 수 있어요.<br>
        화면 잠금 후 돌아오면 시간을 다시 계산합니다.
        일시정지 후 계속하면 쉰 만큼 도착이 늦춰집니다.
        새로고침하면 초기화됩니다.<br>

        도시 검색:
        <a href="https://open-meteo.com/"
           target="_blank" rel="noopener">Open-Meteo</a> /
        <a href="https://www.geonames.org/"
           target="_blank" rel="noopener">GeoNames</a> ·

        지도:
        <a href="https://www.openstreetmap.org/copyright"
           target="_blank" rel="noopener">
            © OpenStreetMap contributors
        </a>
    </p>

    <p class="note">
        사진:
        <a href="https://commons.wikimedia.org/wiki/File:Airplane_wing_sky_and_clouds.jpg"
           target="_blank" rel="noopener">
            Airplane wing sky and clouds — Tobias1984
        </a> /
        <a href="https://commons.wikimedia.org/wiki/File:Clouds_from_aircraft.jpg"
           target="_blank" rel="noopener">
            Clouds from aircraft — Eivind Mikkelsen (Nivix)
        </a> ·
        <a href="https://creativecommons.org/licenses/by-sa/3.0/"
           target="_blank" rel="noopener">CC BY-SA 3.0</a><br>

        사진에 자르기·색감·이동·날씨 효과를 적용했습니다.
        사진을 변형한 표현에도 같은 라이선스를 적용합니다.
    </p>
</main>

<div id="ticketOverlay" role="dialog"
     aria-modal="true" aria-label="탑승권 확인" hidden>
    <div id="ticket" class="ticket">
        <div class="ticket-main">
            <small>STUDY FLIGHT · BOARDING PASS</small>
            <h2>탑승을 환영합니다 ✈</h2>
            <div id="ticketRoute"></div>
            <p id="ticketDuration"></p>
        </div>
        <div class="ticket-stub">BOARDING ✈</div>
    </div>
</div>

<script>
class FlightTimer {
    constructor() {
        this.reset(25 * 60000);
    }

    reset(ms) {
        this.state = "idle";
        this.total = Math.max(1, ms);
        this.remaining = ms;
        this.deadline = 0;
    }

    start(deadline) {
        this.deadline = deadline;
        this.total = this.remaining = Math.max(
            0, deadline - Date.now()
        );
        this.state = this.remaining > 0 ? "running" : "done";
    }

    tick() {
        if (this.state !== "running") return;

        this.remaining = Math.max(
            0, this.deadline - Date.now()
        );

        if (!this.remaining) this.state = "done";
    }

    pause() {
        this.tick();

        if (this.state === "running") {
            this.state = "paused";
        }
    }

    resume() {
        if (this.state === "paused") {
            this.deadline = Date.now() + this.remaining;
            this.state = "running";
        }
    }
}


function parseYoutubeUrl(value) {
    let text = value.trim();

    if (
        /^(?:(?:www\.|m\.)?youtube\.com|youtu\.be)\//i.test(text)
    ) {
        text = "https://" + text;
    }

    try {
        const url = new URL(text);
        const host = url.hostname.toLowerCase();

        if (!["http:", "https:"].includes(url.protocol)) {
            return null;
        }

        const parts = url.pathname.split("/").filter(Boolean);
        let id = null;

        if (host === "youtu.be") {
            id = parts[0];
        } else if (
            ["youtube.com", "www.youtube.com", "m.youtube.com"].includes(host)
        ) {
            id = url.pathname === "/watch"
                ? url.searchParams.get("v")
                : ["shorts", "embed", "live"].includes(parts[0])
                    ? parts[1]
                    : null;
        }

        if (!/^[\w-]{11}$/.test(id || "")) return null;

        const raw =
            url.searchParams.get("start") ||
            url.searchParams.get("t") ||
            "";

        const match = raw.match(
            /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/
        );

        const start = /^\d+$/.test(raw)
            ? Number(raw)
            : match
                ? Number(match[1] || 0) * 3600 +
                  Number(match[2] || 0) * 60 +
                  Number(match[3] || 0)
                : 0;

        return {
            id,
            start: Math.min(604800, Math.max(0, start))
        };
    } catch {
        return null;
    }
}


// 구면 경로를 계산하고 날짜 변경선 부근의 경도를 연결합니다.
function makeRoute(a, b) {
    const rad = Math.PI / 180;

    const vector = p => [
        Math.cos(p.lat * rad) * Math.cos(p.lng * rad),
        Math.cos(p.lat * rad) * Math.sin(p.lng * rad),
        Math.sin(p.lat * rad)
    ];

    const u = vector(a);
    const v = vector(b);

    const angle = Math.acos(
        Math.max(
            -1,
            Math.min(
                1,
                u.reduce((sum, n, i) => sum + n * v[i], 0)
            )
        )
    );

    const sine = Math.sin(angle);
    const points = [];
    let lastLng = a.lng;
    const delta = ((b.lng - a.lng + 540) % 360) - 180;

    for (let i = 0; i <= 240; i++) {
        const t = i / 240;
        let lat, lng;

        if (Math.abs(sine) < 0.00001) {
            lat = a.lat + (b.lat - a.lat) * t;
            lng = a.lng + delta * t;
        } else {
            const x = Math.sin((1 - t) * angle) / sine;
            const y = Math.sin(t * angle) / sine;
            const p = u.map((n, j) => n * x + v[j] * y);

            lat = Math.atan2(
                p[2], Math.hypot(p[0], p[1])
            ) / rad;

            lng = Math.atan2(p[1], p[0]) / rad;
        }

        while (lng - lastLng > 180) lng -= 360;
        while (lng - lastLng < -180) lng += 360;

        points.push([
            Math.max(-85, Math.min(85, lat)),
            lng
        ]);

        lastLng = lng;
    }

    return points;
}


// 화면 초기화
(() => {
    const $ = selector => document.querySelector(selector);
    const HAS_YOUTUBE_KEY = __HAS_YOUTUBE_KEY__;

    if (!window.L) {
        $("#error").textContent =
            "지도를 불러오지 못했습니다. 인터넷을 확인하세요.";
        return;
    }

    const map = L.map("map").setView(
        [37.4602, 126.4407], 4
    );

    L.tileLayer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            maxZoom: 19,
            attribution:
                '&copy; <a href="https://www.openstreetmap.org/copyright">' +
                'OpenStreetMap</a> contributors'
        }
    ).addTo(map).on("tileerror", () => {
        $("#error").textContent =
            "지도 일부를 불러오지 못했습니다. 인터넷을 확인하세요.";
    });

    const timer = new FlightTimer();

    let origin = {
        name: "인천국제공항",
        lat: 37.4602,
        lng: 126.4407
    };

    let destination = null;
    let mode = "manual";
    let searching = false;
    let sceneryOpen = false;
    let routePoints = [];

    const active = () =>
        ["boarding", "running", "paused"].includes(timer.state);

    const dateText = ms =>
        new Date(ms).toLocaleString("ko-KR");

    const durationText = ms =>
        `${Math.floor(Math.ceil(ms / 60000) / 60)}시간 ` +
        `${Math.ceil(ms / 60000) % 60}분`;

    const initial = new Date(
        Math.ceil((Date.now() + 25 * 60000) / 60000) * 60000
    );

    initial.setMinutes(
        initial.getMinutes() - initial.getTimezoneOffset()
    );

    $("#arrivalInput").value =
        initial.toISOString().slice(0, 16);

    const distanceKm = () =>
        destination
            ? map.distance(
                [origin.lat, origin.lng],
                [destination.lat, destination.lng]
            ) / 1000
            : 0;

    const estimatedMs = () =>
        destination
            ? Math.ceil(distanceKm() / 850 * 60 + 30) * 60000
            : 0;

    function plannedMs() {
        if (mode === "flight") return estimatedMs();

        const target = new Date(
            $("#arrivalInput").value
        ).getTime();

        return Number.isFinite(target)
            ? Math.max(0, target - Date.now())
            : 0;
    }

    const startMarker = L.circleMarker(
        [origin.lat, origin.lng],
        {
            radius: 7,
            color: "#2563eb",
            fillOpacity: 1
        }
    ).addTo(map);

    const endMarker = L.circleMarker(
        [0, 0],
        {
            radius: 7,
            color: "#e66d50",
            fillOpacity: 1
        }
    );

    const routeLine = L.polyline(
        [],
        {
            color: "#337baf",
            weight: 3,
            dashArray: "8 8"
        }
    ).addTo(map);

    const plane = L.marker(
        [origin.lat, origin.lng],
        {
            interactive: false,
            zIndexOffset: 1000,
            icon: L.divIcon({
                className: "aircraft-icon",
                iconSize: [42, 42],
                iconAnchor: [21, 21],
                html: `
                    <div class="aircraft">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path
                                fill="#17354b"
                                stroke="white"
                                stroke-width=".7"
                                d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0
                                V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1
                                3.5 1v-1.5L13 19v-5.5Z"/>
                        </svg>
                    </div>
                `
            })
        }
    ).addTo(map);

    function fitRoute() {
        if (routePoints.length && !sceneryOpen) {
            map.fitBounds(
                routeLine.getBounds(),
                {
                    padding: [35, 35],
                    maxZoom: 10
                }
            );
        }
    }

    function previewRoute() {
        startMarker.setLatLng([origin.lat, origin.lng]);
        plane.setLatLng([origin.lat, origin.lng]);

        $("#routeLabel").textContent =
            origin.name + " → " +
            (destination?.name || "목적지를 선택하세요");

        if (!destination) return;

        routePoints = makeRoute(origin, destination);
        routeLine.setLatLngs(routePoints);

        endMarker.setLatLng(
            routePoints[routePoints.length - 1]
        ).addTo(map);

        fitRoute();

        $("#distance").textContent =
            "가상 비행 거리 " +
            Math.round(distanceKm()).toLocaleString("ko-KR") +
            " km";

        $("#estimatedDuration").textContent =
            "예상 운항 시간: " + durationText(estimatedMs());
    }

    function movePlane(progress) {
        if (!routePoints.length) return;

        const index = progress * (routePoints.length - 1);
        const i = Math.min(
            Math.floor(index),
            routePoints.length - 2
        );

        const t = index - i;
        const a = routePoints[i];
        const b = routePoints[i + 1];

        plane.setLatLng([
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t
        ]);

        const p1 = map.project(a);
        const p2 = map.project(b);

        const element = plane.getElement()?.querySelector(
            ".aircraft"
        );

        if (element) {
            const angle = Math.atan2(
                p2.x - p1.x,
                -(p2.y - p1.y)
            ) * 180 / Math.PI;

            element.style.transform = `rotate(${angle}deg)`;
        }
    }

    function render() {
        const seconds = Math.ceil(
            Math.max(0, timer.remaining) / 1000
        );

        const pad = n => String(n).padStart(2, "0");

        $("#timer").textContent =
            `${pad(Math.floor(seconds / 3600))}:` +
            `${pad(Math.floor(seconds / 60) % 60)}:` +
            `${pad(seconds % 60)}`;

        const progress =
            ["idle", "boarding"].includes(timer.state)
                ? 0
                : Math.max(
                    0,
                    Math.min(
                        1,
                        1 - timer.remaining / timer.total
                    )
                );

        $("#progress").value = progress * 100;

        $("#percent").textContent =
            Math.floor(progress * 100) + "% 운항";

        $("#places").disabled = active() || searching;

        for (const id of [
            "arrivalInput",
            "manualMode",
            "flightMode"
        ]) {
            $("#" + id).disabled = active();
        }

        $("#depart").disabled =
            active() ||
            searching ||
            !destination ||
            plannedMs() < 10000;

        $("#depart").textContent =
            timer.state === "done" ? "다시 출발" : "출발";

        $("#pause").disabled =
            !["running", "paused"].includes(timer.state);

        $("#pause").textContent =
            timer.state === "paused" ? "계속하기" : "일시정지";

        $("#reset").disabled =
            ["idle", "boarding"].includes(timer.state);

        document.body.classList.toggle(
            "flying",
            timer.state === "running"
        );

        if (["running", "done"].includes(timer.state)) {
            $("#arrivalLabel").textContent =
                "도착 시각: " + dateText(timer.deadline);
        } else if (timer.state === "paused") {
            $("#arrivalLabel").textContent =
                "계속하기를 누르면 도착 시각을 다시 계산해요.";
        } else {
            const target = mode === "manual"
                ? new Date($("#arrivalInput").value).getTime()
                : Date.now() + estimatedMs();

            $("#arrivalLabel").textContent =
                !Number.isFinite(target) ||
                (mode === "flight" && !destination)
                    ? ""
                    : "예정 도착: " + dateText(target);
        }

        movePlane(progress);
    }

    function refreshPlan() {
        if (active()) return;
        timer.reset(plannedMs());
        render();
    }

    function selectPlace(type, place) {
        if (active()) return;

        if (type === "origin") {
            origin = place;
        } else {
            destination = place;
        }

        $("#" + type + "Selected").textContent =
            "선택됨: " + place.name;

        $("#error").textContent = "";

        $("#status").textContent =
            "도착 방식을 선택하고 출발하세요.";

        previewRoute();
        refreshPlan();
    }

    function setupSearch(type) {
        const input = $("#" + type + "Query");
        const button = $("#" + type + "Search");
        const select = $("#" + type + "Results");

        let results = [];

        async function search() {
            if (active() || searching) return;

            const query = input.value.trim();

            if (query.length < 2) {
                $("#error").textContent =
                    "도시 이름을 두 글자 이상 입력하세요.";
                return;
            }

            searching = true;
            button.textContent = "검색 중";
            select.hidden = true;
            $("#error").textContent = "";
            render();

            try {
                const response = await fetch(
                    "/search?q=" + encodeURIComponent(query)
                );

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(
                        typeof data.detail === "string"
                            ? data.detail
                            : "검색 오류"
                    );
                }

                results = data;

                select.replaceChildren(
                    new Option("검색 결과를 선택하세요", "")
                );

                results.forEach((place, index) => {
                    select.add(
                        new Option(place.name, String(index))
                    );
                });

                select.hidden = !results.length;

                if (!results.length) {
                    $("#error").textContent =
                        "결과가 없습니다. 다른 도시 표기를 쓰거나 " +
                        "지도에서 선택하세요.";
                }
            } catch (error) {
                $("#error").textContent = error.message;
            } finally {
                searching = false;
                button.textContent = "검색";
                render();
            }
        }

        button.onclick = search;

        input.onkeydown = event => {
            if (event.key === "Enter") {
                event.preventDefault();
                search();
            }
        };

        select.onchange = () => {
            if (select.value !== "") {
                selectPlace(
                    type,
                    results[Number(select.value)]
                );
            }
        };
    }

    setupSearch("origin");
    setupSearch("destination");

    map.on("click", event => {
        if (active() || searching) return;

        const point = event.latlng.wrap();

        selectPlace("destination", {
            name:
                `지도 선택 (${point.lat.toFixed(3)}, ` +
                `${point.lng.toFixed(3)})`,
            lat: point.lat,
            lng: point.lng
        });

        $("#destinationResults").hidden = true;
    });

    function setMode(value) {
        if (active()) return;

        mode = value;

        $("#manualPanel").hidden = mode !== "manual";
        $("#flightPanel").hidden = mode !== "flight";

        for (const [id, selected] of [
            ["manualMode", mode === "manual"],
            ["flightMode", mode === "flight"]
        ]) {
            $("#" + id).classList.toggle(
                "chosen",
                selected
            );

            $("#" + id).setAttribute(
                "aria-pressed",
                String(selected)
            );
        }

        refreshPlan();
    }

    $("#manualMode").onclick = () => setMode("manual");
    $("#flightMode").onclick = () => setMode("flight");
    $("#arrivalInput").onchange = refreshPlan;

    function tick() {
        if (timer.state !== "running") return;

        timer.tick();

        const progress = 1 - timer.remaining / timer.total;

        $("#status").textContent =
            timer.state === "done"
                ? "도착 완료! 오늘의 공부 비행을 마쳤어요 ✈"
                : progress < .05
                    ? "이륙 중 · 집중 여행을 시작합니다."
                    : progress > .9
                        ? "착륙 준비 중 · 목적지가 가까워지고 있어요."
                        : "운항 중 · 편안하게 공부에 집중하세요.";

        render();
    }

    $("#depart").onclick = async () => {
        if (active() || searching || !destination) return;

        const duration = plannedMs();

        const manualDeadline = new Date(
            $("#arrivalInput").value
        ).getTime();

        if (duration < 10000) {
            $("#error").textContent =
                "도착 시각을 현재보다 10초 이상 뒤로 설정하세요.";
            return;
        }

        if (distanceKm() < .1) {
            $("#error").textContent =
                "출발지와 다른 목적지를 선택하세요.";
            return;
        }

        $("#error").textContent = "";

        timer.reset(duration);
        timer.state = "boarding";

        previewRoute();
        render();

        $("#ticketRoute").textContent =
            origin.name + " → " + destination.name;

        $("#ticketDuration").textContent =
            durationText(duration) + " · 좌석 01A";

        $("#status").textContent =
            "탑승권을 확인하고 있습니다.";

        $("#ticketOverlay").hidden = false;
        $("#ticket").classList.remove("tearing");

        void $("#ticket").offsetWidth;

        $("#ticket").classList.add("tearing");

        await new Promise(resolve => {
            setTimeout(
                resolve,
                matchMedia(
                    "(prefers-reduced-motion: reduce)"
                ).matches ? 300 : 1250
            );
        });

        $("#ticketOverlay").hidden = true;

        timer.start(
            mode === "manual"
                ? manualDeadline
                : Date.now() + estimatedMs()
        );

        if (timer.state === "done") {
            $("#error").textContent =
                "설정한 시각이 지났습니다. 다시 설정하세요.";
            refreshPlan();
            return;
        }

        tick();
        $("#pause").focus();
    };

    $("#pause").onclick = () => {
        if (timer.state === "running") {
            tick();

            if (timer.state === "done") return;

            timer.pause();

            $("#status").textContent =
                "잠시 쉬는 중 · 운항 일시정지";
        } else if (timer.state === "paused") {
            timer.resume();
            tick();
        }

        render();
    };

    $("#reset").onclick = () => {
        if (timer.state === "boarding") return;

        timer.reset(plannedMs());

        $("#status").textContent =
            "운항을 종료했어요. 다시 출발할 수 있습니다.";

        previewRoute();
        render();
    };

    // 실제 사진 + 독립적으로 바뀌는 시간대/날씨 효과
    const photos = [
        {
            url:
                "https://upload.wikimedia.org/wikipedia/commons/" +
                "a/a3/Airplane_wing_sky_and_clouds.jpg",
            alt: "비행기에서 촬영한 날개와 하늘, 구름"
        },
        {
            url:
                "https://upload.wikimedia.org/wikipedia/commons/" +
                "c/c3/Clouds_from_aircraft.jpg",
            alt: "비행기에서 촬영한 구름과 날개"
        }
    ];

    const times = {
        dawn: "새벽",
        day: "낮",
        sunset: "노을",
        night: "밤"
    };

    const weather = {
        clear: "맑음",
        cloudy: "흐림",
        rain: "비",
        snow: "눈"
    };

    let loadedUrl = "";
    let photoVersion = 0;

    async function loadPhoto() {
        const version = ++photoVersion;

        const index = ["dawn", "sunset"].includes(
            $("#sky").dataset.time
        ) ? 1 : 0;

        if (!loadedUrl) {
            $("#photoNotice").hidden = false;
            $("#photoNotice").textContent =
                "실제 풍경 사진을 불러오는 중이에요.";
        }

        for (const item of [
            photos[index],
            photos[1 - index]
        ]) {
            if (version !== photoVersion) return;

            if (loadedUrl === item.url) {
                $("#photoNotice").hidden = true;
                return;
            }

            try {
                await new Promise((resolve, reject) => {
                    const image = new Image();

                    const timeout = setTimeout(() => {
                        image.onload = null;
                        image.onerror = null;
                        reject(new Error("timeout"));
                    }, 15000);

                    image.onload = () => {
                        clearTimeout(timeout);
                        resolve();
                    };

                    image.onerror = () => {
                        clearTimeout(timeout);
                        reject(new Error("load"));
                    };

                    image.src = item.url;
                });

                if (version !== photoVersion) return;

                $("#flightPhoto").src = item.url;
                $("#flightPhoto").alt = item.alt;
                $("#flightPhoto").hidden = false;

                loadedUrl = item.url;
                $("#photoNotice").hidden = true;
                return;
            } catch {
                // 실패하면 다른 사진을 시도합니다.
            }
        }

        if (version === photoVersion && !loadedUrl) {
            $("#photoNotice").textContent =
                "사진을 불러오지 못했어요. 인터넷 연결을 확인한 뒤 " +
                "‘풍경 바꾸기’를 눌러주세요.";
        }
    }

    function changeRandom(attribute, options) {
        const choices = Object.keys(options).filter(
            key => key !== $("#sky").dataset[attribute]
        );

        $("#sky").dataset[attribute] =
            choices[Math.floor(Math.random() * choices.length)];

        $("#sceneLabel").textContent =
            times[$("#sky").dataset.time] + " · " +
            weather[$("#sky").dataset.weather];
    }

    function randomScene() {
        changeRandom("time", times);
        changeRandom("weather", weather);
        loadPhoto();
    }

    $("#viewScenery").onclick = () => {
        sceneryOpen = !sceneryOpen;

        $("#scenery").hidden = !sceneryOpen;
        $("#map").hidden = sceneryOpen;

        $("#viewScenery").textContent =
            sceneryOpen ? "지도 보기" : "바깥 풍경 보기";

        $("#viewScenery").setAttribute(
            "aria-pressed",
            String(sceneryOpen)
        );

        if (sceneryOpen) {
            randomScene();
        } else {
            requestAnimationFrame(() => {
                map.invalidateSize();
                fitRoute();
            });
        }
    };

    $("#randomScene").onclick = randomScene;

    // 시간대는 45초마다 변경
    setInterval(() => {
        if (sceneryOpen && !document.hidden) {
            changeRandom("time", times);
            loadPhoto();
        }
    }, 45000);

    // 날씨는 70초마다 별도로 변경
    setInterval(() => {
        if (sceneryOpen && !document.hidden) {
            changeRandom("weather", weather);
        }
    }, 70000);

    // 검색/링크 탭이 하나의 플레이어를 공유합니다.
    function setVideoPanel(open) {
        $("#videoPanel").hidden = !open;

        $("#workspace").classList.toggle(
            "with-video",
            open
        );

        $("#showVideo").textContent =
            open ? "영상 패널 닫기" : "영상 찾아보기";

        $("#showVideo").setAttribute(
            "aria-expanded",
            String(open)
        );

        if (!open) {
            $("#player").src = "about:blank";
            $("#player").hidden = true;
        }

        requestAnimationFrame(() => {
            if (!sceneryOpen) map.invalidateSize();
        });
    }

    $("#showVideo").onclick = () => {
        setVideoPanel($("#videoPanel").hidden);
    };

    $("#closeVideo").onclick = () => {
        setVideoPanel(false);
        $("#showVideo").focus();
    };

    function selectTab(name, focus = false) {
        for (const type of ["search", "link"]) {
            const selected = name === type;

            $("#" + type + "Pane").hidden = !selected;

            const tab = $("#" + type + "Tab");

            tab.classList.toggle("chosen", selected);
            tab.setAttribute(
                "aria-selected",
                String(selected)
            );

            tab.tabIndex = selected ? 0 : -1;
        }

        if (focus) $("#" + name + "Tab").focus();
    }

    $("#searchTab").onclick = () => selectTab("search");
    $("#linkTab").onclick = () => selectTab("link");

    $("#videoTabs").onkeydown = event => {
        if (![
            "ArrowLeft",
            "ArrowRight",
            "Home",
            "End"
        ].includes(event.key)) {
            return;
        }

        event.preventDefault();

        const selected =
            $("#searchTab").getAttribute("aria-selected") === "true";

        selectTab(
            event.key === "Home"
                ? "search"
                : event.key === "End"
                    ? "link"
                    : selected ? "link" : "search",
            true
        );
    };

    function playVideo(id, start = 0, autoplay = false) {
        if (!/^[\w-]{11}$/.test(id)) return;

        const params = new URLSearchParams({
            playsinline: "1",
            rel: "0",
            start: String(start),
            autoplay: autoplay ? "1" : "0"
        });

        $("#player").hidden = false;

        $("#player").src =
            "https://www.youtube.com/embed/" +
            id + "?" + params;
    }

    function playLink() {
        const video = parseYoutubeUrl(
            $("#youtubeLink").value
        );

        $("#linkError").textContent = video
            ? ""
            : "올바른 유튜브 영상 주소를 입력하세요. " +
              "채널이나 검색 결과 주소는 재생할 수 없어요.";

        if (video) {
            playVideo(video.id, video.start, true);
        }
    }

    $("#playLink").onclick = playLink;

    $("#youtubeLink").onkeydown = event => {
        if (event.key === "Enter") {
            event.preventDefault();
            playLink();
        }
    };

    $("#youtubeHint").textContent = HAS_YOUTUBE_KEY
        ? "검색 결과를 선택하면 아래에서 재생할 수 있어요."
        : "현재는 검색어로 유튜브 새 탭을 열어요. " +
          "링크 탭에서는 바로 재생할 수 있어요.";

    async function searchVideos() {
        const query = $("#youtubeQuery").value.trim();

        if (!query) {
            $("#videoError").textContent = "검색어를 입력하세요.";
            return;
        }

        $("#videoError").textContent = "";

        if (!HAS_YOUTUBE_KEY) {
            window.open(
                "https://www.youtube.com/results?search_query=" +
                encodeURIComponent(query),
                "_blank",
                "noopener,noreferrer"
            );
            return;
        }

        const button = $("#searchVideo");

        if (button.disabled) return;

        button.disabled = true;
        button.textContent = "검색 중";
        $("#videoResults").replaceChildren();

        try {
            const response = await fetch(
                "/youtube-search?q=" +
                encodeURIComponent(query)
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    typeof data.detail === "string"
                        ? data.detail
                        : "검색 오류"
                );
            }

            if (!data.length) {
                $("#videoError").textContent =
                    "검색 결과가 없습니다.";
            }

            for (const video of data) {
                if (!/^[\w-]{11}$/.test(video.id)) continue;

                const row = document.createElement("button");
                const image = document.createElement("img");
                const text = document.createElement("span");
                const channel = document.createElement("small");

                row.className = "video-result";

                image.src =
                    `https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`;

                image.alt = "";
                image.loading = "lazy";

                text.textContent = video.title;
                channel.textContent = video.channel;

                text.appendChild(channel);
                row.append(image, text);

                row.onclick = () => playVideo(video.id);

                $("#videoResults").appendChild(row);
            }
        } catch (error) {
            $("#videoError").textContent = error.message;
        } finally {
            button.disabled = false;
            button.textContent = "검색";
        }
    }

    $("#searchVideo").onclick = searchVideos;

    $("#youtubeQuery").onkeydown = event => {
        if (event.key === "Enter") {
            event.preventDefault();
            searchVideos();
        }
    };

    new ResizeObserver(() => {
        if (!sceneryOpen) map.invalidateSize();
    }).observe($("#map"));

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) tick();
    });

    window.addEventListener("pageshow", tick);

    setInterval(() => {
        if (timer.state === "running") {
            tick();
        } else if (timer.state === "idle") {
            timer.remaining = plannedMs();
            render();
        }
    }, 100);

    refreshPlan();
})();
</script>
</body>
</html>
"""


@server.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/app/")


@server.get("/flight", response_class=HTMLResponse)
def flight_page():
    return HTMLResponse(
        PAGE.replace(
            "__HAS_YOUTUBE_KEY__",
            "true" if YOUTUBE_API_KEY else "false",
        ),
        headers={
            "Referrer-Policy": "strict-origin-when-cross-origin"
        },
    )


with gr.Blocks(title="항공기 타이머") as demo:
    gr.HTML(
        '<iframe src="/flight" title="항공기 타이머" '
        'style="width:100%;height:2100px;border:0;" '
        'allow="autoplay; fullscreen; picture-in-picture" '
        'allowfullscreen></iframe>'
    )


app = gr.mount_gradio_app(
    server,
    demo,
    path="/app",
)


if __name__ == "__main__":
    print("PC: http://127.0.0.1:7860")
    print("모바일: 같은 Wi-Fi에서 http://PC의_IP주소:7860")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7860,
    )