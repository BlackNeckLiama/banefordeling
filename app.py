import re
import requests

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from starlette.requests import Request

app = FastAPI()

templates = Jinja2Templates(directory="templates")

CURRENT_YEAR = datetime.now().year

AVAILABLE_WEEKS = list(range(1, 53))

NORWEGIAN_MONTHS = {

    1: "Januar",
    2: "Februar",
    3: "Mars",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


def norwegian_date(date_obj):

    month_name = NORWEGIAN_MONTHS[date_obj.month]

    return f"{date_obj.day} {month_name}"


def get_week_dates(year, week):

    start_date = datetime.fromisocalendar(
        year,
        week,
        1
    )

    end_date = start_date + timedelta(days=6)

    return (
        f"{norwegian_date(start_date)} - "
        f"{norwegian_date(end_date)} {year}"
    )


def build_url(week):

    return (
        "https://www.s2s.net/show.php?"
        f"club_id=1829"
        f"&color=blue"
        f"&skey=aa2a77371374094fe9e0bc1de3f94ed9"
        f"&c_l=no_no"
        f"&team_id=-1"
        f"&pitch_id=-1"
        f"&week={week}%2C+{CURRENT_YEAR}"
    )


def clean_pitch_name(pitch):

    pitch = pitch.split("Anlegg:")[0]

    replacements = {

        "A: Konnerud stadion A og B (undervarme) A:":
            "Konnerud stadion A og B",

        "B: Konnerud kunstgress A og B (Isbanen) B:":
            "Konnerud kunstgress A og B",

        "C: Konnerud Kunstgress C 7-er C:":
            "Konnerud Kunstgress C 7-er",

        "D: Høyden 9'er m/undervarme D:":
            "Høyden 9'er m/undervarme",

        "G: Kjeldaas Arena (Sletta) G:":
            "Kjeldaas Arena (Sletta)",
    }

    for old, new in replacements.items():

        pitch = pitch.replace(old, new)

    pitch = " ".join(pitch.split())

    return pitch


def convert_day(day_text):

    months = {

        "Jan": "Januar",
        "Feb": "Februar",
        "Mar": "Mars",
        "Apr": "April",
        "Mai": "Mai",
        "Jun": "Juni",
        "Jul": "Juli",
        "Aug": "August",
        "Sep": "September",
        "Okt": "Oktober",
        "Nov": "November",
        "Des": "Desember",
    }

    for short, full in months.items():

        day_text = day_text.replace(short, full)

    return day_text


def get_activities(week):

    url = build_url(week)

    response = requests.get(url)

    soup = BeautifulSoup(response.text, "lxml")

    tables = soup.find_all("table")

    if len(tables) < 4:
        return []

    main_table = tables[3]

    rows = main_table.find_all("tr")

    header_cells = rows[0].find_all(["td", "th"])

    days = []

    for cell in header_cells[1:]:

        day_text = cell.get_text(" ", strip=True)

        if day_text:

            day_text = convert_day(day_text)

            days.append(day_text)

    activity_pattern = r"(\d{2}:\d{2})-(\d{2}:\d{2})"

    activities = []

    seen = set()

    for row in rows[2:]:

        cells = row.find_all("td")

        if len(cells) < 2:
            continue

        raw_pitch = cells[0].get_text(
            " ",
            strip=True
        )

        if not raw_pitch:
            continue

        if "En annen bane" in raw_pitch:
            continue

        if "Aktive baner" in raw_pitch:
            continue

        pitch = clean_pitch_name(raw_pitch)

        for day_index, cell in enumerate(cells[1:]):

            if day_index >= len(days):
                continue

            day = days[day_index]

            cell_text = cell.get_text(
                "\n",
                strip=True
            )

            if not cell_text:
                continue

            lines = [

                line.strip()

                for line in cell_text.split("\n")

                if line.strip()
            ]

            current_activity = None

            last_label = None

            for line in lines:

                time_match = re.search(
                    activity_pattern,
                    line
                )

                if time_match:

                    if current_activity:

                        unique_key = (

                            current_activity["day"],

                            current_activity["pitch"],

                            current_activity["start"],

                            current_activity["end"],

                            current_activity["team"]
                        )

                        if unique_key not in seen:

                            seen.add(unique_key)

                            activities.append(
                                current_activity
                            )

                    current_activity = {

                        "day": day,

                        "pitch": pitch,

                        "start": time_match.group(1),

                        "end": time_match.group(2),

                        "team": None,

                        "opponent": None,

                        "type": "training"
                    }

                    last_label = None

                    continue

                if not current_activity:
                    continue

                if line.startswith("Lagnavn"):

                    last_label = "team"

                    continue

                if line.startswith("Motstander"):

                    last_label = "opponent"

                    current_activity["type"] = "match"

                    continue

                invalid_lines = [

                    "Bane:",
                    "Anlegg:",
                    "Adresse:",
                    "Årskull",
                ]

                if any(
                    text in line
                    for text in invalid_lines
                ):
                    continue

                if last_label == "team":

                    current_activity["team"] = line

                    continue

                if last_label == "opponent":

                    current_activity["opponent"] = line

                    continue

                if not current_activity["team"]:

                    current_activity["team"] = line

                elif not current_activity["opponent"]:

                    current_activity["opponent"] = line

                    current_activity["type"] = "match"

            if current_activity:

                unique_key = (

                    current_activity["day"],

                    current_activity["pitch"],

                    current_activity["start"],

                    current_activity["end"],

                    current_activity["team"]
                )

                if unique_key not in seen:

                    seen.add(unique_key)

                    activities.append(
                        current_activity
                    )

    return activities


def to_minutes(time_string):

    hours, minutes = map(
        int,
        time_string.split(":")
    )

    return hours * 60 + minutes


def get_conflicts(activities):

    conflicts = []

    seen_conflicts = set()

    for i in range(len(activities)):

        a = activities[i]

        for j in range(i + 1, len(activities)):

            b = activities[j]

            if a["day"] != b["day"]:
                continue

            if a["pitch"] != b["pitch"]:
                continue

            if not a["team"] or not b["team"]:
                continue

            if a.get("opponent") == b["team"]:
                continue

            if b.get("opponent") == a["team"]:
                continue

            start_a = to_minutes(a["start"])
            end_a = to_minutes(a["end"])

            start_b = to_minutes(b["start"])
            end_b = to_minutes(b["end"])

            overlap = (
                start_a < end_b and
                start_b < end_a
            )

            if overlap:

                sorted_teams = sorted([
                    a["team"],
                    b["team"]
                ])

                unique_conflict = (

                    a["day"],
                    a["pitch"],
                    a["start"],
                    a["end"],
                    sorted_teams[0],
                    sorted_teams[1]
                )

                if unique_conflict in seen_conflicts:
                    continue

                seen_conflicts.add(unique_conflict)

                conflicts.append({

                    "day": a["day"],
                    "pitch": a["pitch"],
                    "start": a["start"],
                    "end": a["end"],
                    "team_a": sorted_teams[0],
                    "team_b": sorted_teams[1]
                })

    return conflicts


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    week: int = None
):

    DEFAULT_WEEK = 20

    current_week = datetime.now().isocalendar()[1]

    if week is None:
        week = current_week

    activities = get_activities(week)

    if not activities:

        week = DEFAULT_WEEK

        activities = get_activities(week)

    conflicts = get_conflicts(activities)

    available_days = sorted(
        list(set([
            activity["day"]
            for activity in activities
        ]))
    )

    weeks = []

    for w in AVAILABLE_WEEKS:

        weeks.append({

            "week": w,

            "dates": get_week_dates(
                CURRENT_YEAR,
                w
            )
        })

    return templates.TemplateResponse(

    request=request,

    name="index.html",

    context={

        "request": request,

        "activities": activities,

        "conflicts": conflicts,

        "selected_week": week,

        "weeks": weeks,

        "current_year": CURRENT_YEAR,

        "available_days": available_days

    }

)