#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

NBA_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
WORLD_CUP_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
WORLD_CUP_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings?season=2026"
WORLD_CUP_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
DEFAULT_STATE_DIR = Path.home() / "Library/Application Support/SportsIsland"
STATE_DIR = Path(os.environ.get("SPORTS_ISLAND_STATE_DIR", DEFAULT_STATE_DIR))
STATE_FILE = STATE_DIR / "channel"
CACHE_FILE = STATE_DIR / "score-cache.json"
SCRIPT_PATH = Path(__file__).resolve()
TITLE_WIDTH = 24
MESSAGE_SECONDS = 10
SCROLL_PAUSE_SECONDS = 2
DATA_CACHE_SECONDS = 30
TITLE_FONT = "Menlo"
TITLE_FONT_SIZE = 13

TEAM_NAMES_ZH = {
    "Algeria": "阿尔及利亚", "Argentina": "阿根廷", "Australia": "澳大利亚",
    "Austria": "奥地利", "Belgium": "比利时", "Bosnia-Herzegovina": "波黑",
    "Brazil": "巴西", "Canada": "加拿大", "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚", "Congo DR": "民主刚果", "Croatia": "克罗地亚",
    "Curaçao": "库拉索", "Czechia": "捷克", "Ecuador": "厄瓜多尔",
    "Egypt": "埃及", "England": "英格兰", "France": "法国", "Germany": "德国",
    "Ghana": "加纳", "Haiti": "海地", "Iran": "伊朗", "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦", "Japan": "日本", "Jordan": "约旦",
    "Mexico": "墨西哥", "Morocco": "摩洛哥", "Netherlands": "荷兰",
    "New Zealand": "新西兰", "Norway": "挪威", "Panama": "巴拿马",
    "Paraguay": "巴拉圭", "Portugal": "葡萄牙", "Qatar": "卡塔尔",
    "Russia": "俄罗斯",
    "Saudi Arabia": "沙特阿拉伯", "Scotland": "苏格兰", "Senegal": "塞内加尔",
    "South Africa": "南非", "South Korea": "韩国", "Spain": "西班牙",
    "Sweden": "瑞典", "Switzerland": "瑞士", "Tunisia": "突尼斯",
    "Türkiye": "土耳其", "United States": "美国", "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
}


def curl_json(url, referer=None):
    command = [
        "/usr/bin/curl", "-fsSL", "--max-time", "12", "--retry", "2",
        "--retry-all-errors",
        "-H", "User-Agent: Mozilla/5.0 AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "-H", "Accept: application/json,text/plain,*/*",
    ]
    if referer:
        command.extend(["-H", f"Referer: {referer}"])
    command.append(url)
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def selected_channel():
    try:
        channel = STATE_FILE.read_text().strip()
    except OSError:
        channel = "worldcup"
    return channel if channel in {"worldcup", "nba"} else "worldcup"


def set_channel(channel):
    if channel not in {"worldcup", "nba"}:
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(channel)
    return 0


def menu_action(label, channel, selected):
    prefix = "✓ " if selected else ""
    return (
        f"{prefix}{label} | bash={SCRIPT_PATH} param1=--set-channel "
        f"param2={channel} terminal=false refresh=true"
    )


def escape_menu(text):
    return str(text).replace("|", "¦").replace("\n", " ")


def char_width(char):
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def visual_width(text):
    return sum(char_width(char) for char in text)


def visual_slice(text, start, width):
    result = []
    cursor = 0
    used = 0
    for char in text:
        size = char_width(char)
        if cursor + size <= start:
            cursor += size
            continue
        if used + size > width:
            break
        result.append(char)
        used += size
        cursor += size
    return "".join(result), used


def fixed_title(messages):
    messages = [message for message in messages if message]
    if not messages:
        messages = ["比分暂不可用"]

    cycle = int(time.time()) // MESSAGE_SECONDS
    message = messages[cycle % len(messages)]
    width = visual_width(message)

    if width <= TITLE_WIDTH:
        visible = message
        used = width
    else:
        scroll_range = width - TITLE_WIDTH
        scroll_tick = int(time.time()) % MESSAGE_SECONDS
        if scroll_tick < SCROLL_PAUSE_SECONDS:
            offset = 0
        elif scroll_tick >= MESSAGE_SECONDS - SCROLL_PAUSE_SECONDS:
            offset = scroll_range
        else:
            progress = (scroll_tick - SCROLL_PAUSE_SECONDS) / (
                MESSAGE_SECONDS - SCROLL_PAUSE_SECONDS * 2 - 1
            )
            offset = round(scroll_range * progress)
        visible, used = visual_slice(message, offset, TITLE_WIDTH)

    padding = "\u2007" * max(0, TITLE_WIDTH - used)
    return f"{visible}{padding} | font={TITLE_FONT} size={TITLE_FONT_SIZE}"


def load_cache(channel):
    try:
        payload = json.loads(CACHE_FILE.read_text())
        if (
            payload.get("channel") == channel
            and time.time() - payload.get("updatedAt", 0) < DATA_CACHE_SECONDS
        ):
            return payload.get("data")
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_cache(channel, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "channel": channel,
        "updatedAt": time.time(),
        "data": data,
    }, ensure_ascii=False))
    temporary.replace(CACHE_FILE)


def local_time(iso_date):
    try:
        date = local_datetime(iso_date)
        return date.strftime("%H:%M")
    except (ValueError, AttributeError):
        return "--:--"


def local_datetime(iso_date):
    return datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone()


def relative_day_label(iso_date):
    try:
        offset = (local_datetime(iso_date).date() - datetime.now().astimezone().date()).days
    except (ValueError, AttributeError):
        return ""
    return f"+{offset} " if offset > 0 else ""


def soccer_team_name(competitor):
    english = competitor.get("team", {}).get("displayName", "待定")
    return TEAM_NAMES_ZH.get(english, english)


def soccer_competitors(event):
    competitors = event["competitions"][0].get("competitors", [])
    home = next((team for team in competitors if team.get("homeAway") == "home"), competitors[0])
    away = next((team for team in competitors if team.get("homeAway") == "away"), competitors[-1])
    return home, away


def soccer_status(event):
    status = event.get("status", {})
    status_type = status.get("type", {})
    state = status_type.get("state")
    if state == "in":
        clock = status.get("displayClock") or status_type.get("shortDetail") or "直播"
        return clock if clock.endswith("'") else clock
    if state == "post":
        return "完场"
    return local_time(event.get("date"))


def soccer_title(event):
    home, away = soccer_competitors(event)
    state = event.get("status", {}).get("type", {}).get("state")
    home_name, away_name = soccer_team_name(home), soccer_team_name(away)
    if state == "pre":
        return f"{home_name}vs{away_name}·{relative_day_label(event.get('date'))}{soccer_status(event)}"
    return f"{home_name}{home.get('score', '0')}–{away.get('score', '0')}{away_name}·{soccer_status(event)}"


def event_priority(event):
    state = event.get("status", {}).get("type", {}).get("state")
    return {"in": 0, "pre": 1, "post": 2}.get(state, 3), event.get("date", "")


def soccer_situation(event):
    home, away = soccer_competitors(event)
    home_name, away_name = soccer_team_name(home), soccer_team_name(away)
    home_score, away_score = int(home.get("score", 0)), int(away.get("score", 0))
    state = event.get("status", {}).get("type", {}).get("state")

    if state == "pre":
        return None

    if state == "post":
        if home_score == away_score:
            return "赛果·双方战平"
        winner = home_name if home_score > away_score else away_name
        return f"赛果·{winner}取胜"

    if home_score == away_score == 0:
        clock = event.get("status", {}).get("displayClock", "0'").rstrip("'")
        try:
            minute = int(clock.split("+")[0])
        except ValueError:
            minute = 0
        return "双方仍未打破僵局" if minute >= 60 else None
    if home_score == away_score:
        return "双方暂时战平"

    leader = home_name if home_score > away_score else away_name
    return f"{leader}暂时领先"


def clock_minute(clock):
    try:
        return int(str(clock).split("+")[0].rstrip("'"))
    except (TypeError, ValueError):
        return None


def key_event_message(item):
    event_type = item.get("type", {}).get("type", "")
    minute = item.get("clock", {}).get("displayValue", "")
    minute_prefix = f"{minute}·" if minute else ""
    team = item.get("team", {})
    team_name = TEAM_NAMES_ZH.get(team.get("displayName"), team.get("displayName", ""))
    participants = item.get("participants") or []
    player = participants[0].get("athlete", {}).get("displayName", "") if participants else ""
    subject = " ".join(part for part in (team_name, player) if part)

    if "goal" in event_type:
        action = "乌龙球" if "own-goal" in event_type else "破门"
        return f"{minute_prefix}{subject}{action}", "goal"
    if "red-card" in event_type or "second-yellow" in event_type:
        return f"{minute_prefix}{subject}染红", "red"
    if "yellow-card" in event_type:
        return f"{minute_prefix}{subject}黄牌", "yellow"
    return None, None


def stoppage_message(event, summary):
    display_clock = event.get("status", {}).get("displayClock", "")
    current_minute = clock_minute(display_clock)
    if current_minute not in {45, 90, 105, 120} and "+" not in display_clock:
        return None

    for item in reversed(summary.get("commentary", [])):
        text = item.get("text") or ""
        match = re.search(r"announced (\d+) minutes? of added time", text, re.IGNORECASE)
        if not match:
            continue
        period = "上半场" if current_minute == 45 else "下半场"
        if current_minute in {105, 120}:
            period = "加时赛"
        return f"{period}补时{match.group(1)}分钟"
    return None


def soccer_event_messages(event, summary):
    if not summary:
        return []

    state = event.get("status", {}).get("type", {}).get("state")
    current_minute = clock_minute(event.get("status", {}).get("displayClock"))
    latest = {}

    for item in summary.get("keyEvents", []):
        message, category = key_event_message(item)
        if not message:
            continue
        event_minute = clock_minute(item.get("clock", {}).get("displayValue"))
        if (
            state == "in"
            and category == "yellow"
            and current_minute is not None
            and event_minute is not None
            and current_minute - event_minute > 5
        ):
            continue
        latest[category] = message

    messages = [latest[key] for key in ("goal", "red") if key in latest]
    stoppage = stoppage_message(event, summary) if state == "in" else None
    if stoppage:
        messages.append(stoppage)
    if "yellow" in latest:
        messages.append(latest["yellow"])
    return messages


def all_key_event_messages(summary):
    messages = []
    for item in summary.get("keyEvents", []):
        message, _ = key_event_message(item)
        if message:
            messages.append(message)
    return messages


def standings_index(payload):
    result = {}
    for group in payload.get("children", []):
        entries = group.get("standings", {}).get("entries", [])
        for entry in entries:
            stats = {
                stat.get("name"): stat.get("value")
                for stat in entry.get("stats", [])
            }
            team = entry.get("team", {})
            note = entry.get("note", {})
            rank = int(stats.get("rank", 0))
            result[str(team.get("id"))] = {
                "group": group.get("name", "").replace("Group ", "") + "组",
                "name": TEAM_NAMES_ZH.get(team.get("displayName"), team.get("displayName", "待定")),
                "rank": rank,
                "points": int(stats.get("points", 0)),
                "played": int(stats.get("gamesPlayed", 0)),
                "advance": "advance" in note.get("description", "").lower(),
                "note": note.get("description", ""),
            }
    return result


def outlook_lines(event, table):
    if event.get("season", {}).get("slug") != "group-stage":
        stage = event.get("season", {}).get("slug", "淘汰赛").replace("-", " ")
        return [f"阶段：{stage}"]

    home, away = soccer_competitors(event)
    lines = []
    for team in (home, away):
        info = table.get(str(team.get("id")))
        if not info:
            continue
        name = soccer_team_name(team)
        if info["played"] == 0 and info["points"] == 0:
            continue
        if info["rank"] <= 2:
            outlook = "处于直接晋级区"
        elif info["rank"] == 3:
            outlook = "正在竞争最佳第三名"
        else:
            outlook = "需要追分改善出线前景"
        lines.append(f"{name}当前{info['points']}分，暂列{info['group']}第{info['rank']}，{outlook}")
    return lines


def fetch_world_cup():
    today = datetime.now().astimezone().date()
    dates = [(today + timedelta(days=offset)).strftime("%Y%m%d") for offset in (-1, 0, 1)]
    events = []
    seen = set()
    for date in dates:
        payload = curl_json(f"{WORLD_CUP_SCOREBOARD_URL}?dates={date}")
        for event in payload.get("events", []):
            if event.get("id") not in seen:
                events.append(event)
                seen.add(event.get("id"))
    events = sorted(events, key=event_priority)
    standings = standings_index(curl_json(WORLD_CUP_STANDINGS_URL))
    summaries = {}
    if events:
        featured = events[0]
        state = featured.get("status", {}).get("type", {}).get("state")
        if state in {"in", "post"}:
            try:
                summaries[str(featured.get("id"))] = curl_json(
                    f"{WORLD_CUP_SUMMARY_URL}?event={featured.get('id')}"
                )
            except Exception:
                pass
    return events, standings, summaries


def world_cup_messages(events, table, summaries):
    featured = events[0] if events else None
    score = soccer_title(featured) if featured else "世界杯 · 今日无比赛"
    if not featured:
        return [score]
    event_messages = soccer_event_messages(
        featured, summaries.get(str(featured.get("id")), {})
    )
    situation = None if event_messages else soccer_situation(featured)
    return [score, *event_messages, situation]


def print_world_cup(events, table, summaries):
    print(fixed_title(world_cup_messages(events, table, summaries)))
    print("---")
    print("当前频道：世界杯")
    print("---")

    if not events:
        print("今日和明日暂无世界杯比赛")
    else:
        for event in events:
            print(escape_menu(soccer_title(event)))
            key_events = all_key_event_messages(summaries.get(str(event.get("id")), {}))
            if key_events:
                print("--关键事件")
                for message in key_events:
                    print(f"----{escape_menu(message)}")
            for outlook in outlook_lines(event, table):
                print(f"--{escape_menu(outlook)}")

    print("---")
    relevant_groups = {
        table[str(team.get("id"))]["group"]
        for event in events
        for team in soccer_competitors(event)
        if str(team.get("id")) in table
    }
    print("相关小组积分榜")
    for team_id, info in sorted(table.items(), key=lambda item: (item[1]["group"], item[1]["rank"])):
        if info["group"] not in relevant_groups:
            continue
        if info["rank"] == 1:
            print(f"--{info['group']}")
        print(f"----第{info['rank']}名 · {info['name']} · {info['points']}分 · {info['played']}场")
    print("---")
    print("打开世界杯赛程 | href=https://www.espn.com/soccer/schedule/_/league/fifa.world")


def nba_team_label(team):
    return team.get("teamTricode") or team.get("teamName") or "TBD"


def nba_title(game):
    away, home = game["awayTeam"], game["homeTeam"]
    if game.get("gameStatus") == 1:
        date = game.get("gameTimeUTC")
        time_text = local_time(date) if date else game.get("gameStatusText", "待开始")
        return f"{nba_team_label(away)}vs{nba_team_label(home)}·{relative_day_label(date)}{time_text}"
    return (
        f"{nba_team_label(away)}{away.get('score', 0)}–{home.get('score', 0)}"
        f"{nba_team_label(home)}·{game.get('gameStatusText', '')}"
    )


def fetch_nba():
    payload = curl_json(NBA_URL, "https://www.nba.com/games")
    games = payload.get("scoreboard", {}).get("games", [])
    return sorted(games, key=lambda game: (game.get("gameStatus", 0), game.get("gameTimeUTC", "")))


def nba_messages(games):
    featured = next((game for game in games if game.get("gameStatus") == 2), None)
    featured = featured or next((game for game in games if game.get("gameStatus") == 1), None)
    featured = featured or (games[0] if games else None)
    return [nba_title(featured) if featured else "NBA · 今日无比赛"]


def print_nba(games):
    print(fixed_title(nba_messages(games)))
    print("---")
    print("当前频道：NBA")
    print("---")
    if not games:
        print("今日暂无 NBA 比赛")
    else:
        for game in games:
            print(escape_menu(nba_title(game)))
    print("---")
    print("打开 NBA 赛程 | href=https://www.nba.com/games")


def print_footer(channel):
    print("---")
    print("选择频道")
    print(f"--{menu_action('世界杯', 'worldcup', channel == 'worldcup')}")
    print(f"--{menu_action('NBA', 'nba', channel == 'nba')}")
    print("---")
    print(f"更新于 {datetime.now().strftime('%H:%M:%S')}")
    print("刷新 | refresh=true")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--set-channel":
        return set_channel(sys.argv[2])

    channel = selected_channel()
    try:
        if channel == "nba":
            games = load_cache(channel)
            if games is None:
                games = fetch_nba()
                save_cache(channel, games)
            print_nba(games)
        else:
            cached = load_cache(channel)
            if cached is None:
                events, table, summaries = fetch_world_cup()
                cached = {"events": events, "table": table, "summaries": summaries}
                save_cache(channel, cached)
            print_world_cup(
                cached["events"], cached["table"], cached.get("summaries", {})
            )
    except Exception as exc:
        print("比分暂不可用")
        print("---")
        print(f"加载失败：{escape_menu(exc)}")

    print_footer(channel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
