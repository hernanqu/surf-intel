from flask import Flask, jsonify, render_template, request
import requests
import time

app = Flask(__name__)

# Simple in-memory cache to avoid excessive upstream API requests.
MARINE_CACHE = {
    "data": None,
    "timestamp": 0,
}

CACHE_TTL = 600  # 10 minutes

# --------------------------------------------------
# SURFER PROFILE
# --------------------------------------------------

SURFER = {
    "board": '6\'8" Haydenshape Hypto Krypto Soft',
    "volume_liters": 52,
    "level": "beginner / progressing intermediate",
    "objective": "skill progression",
}

# --------------------------------------------------
# SPOT
# --------------------------------------------------

VENICE = {
    "name": "Venice Breakwater",
    "latitude": 33.9832,
    "longitude": -118.4743,
    "wind_profile": {
        "offshore": [(45, 135)],
        "onshore": [(225, 315)],
    },
}

OPEN_METEO_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

NOAA_TIDE_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_TIDE_STATION = "9410840"


# --------------------------------------------------
# MARINE DATA
# --------------------------------------------------

HOURLY = ",".join([
    "wave_height",
    "wave_direction",
    "wave_period",
    "wave_peak_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "tertiary_swell_wave_height",
    "tertiary_swell_wave_direction",
    "tertiary_swell_wave_period",
    "sea_surface_temperature",
])

CURRENT = ",".join([
    "wave_height",
    "wave_direction",
    "wave_period",
    "wave_peak_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "tertiary_swell_wave_height",
    "tertiary_swell_wave_direction",
    "tertiary_swell_wave_period",
    "sea_surface_temperature",
])


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def meters_to_feet(meters):
    if meters is None:
        return None
    return round(meters * 3.28084, 1)


def celsius_to_fahrenheit(celsius):
    if celsius is None:
        return None
    return round((celsius * 9 / 5) + 32, 1)


def compass_direction(degrees):
    if degrees is None:
        return "Unknown"

    directions = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]

    index = round(degrees / 22.5) % 16
    return directions[index]


def classify_wind(wind_direction, wind_profile):
    if wind_direction is None:
        return "UNKNOWN"

    direction = wind_direction % 360

    for start, end in wind_profile.get("offshore", []):
        if start <= direction <= end:
            return "OFFSHORE"

    for start, end in wind_profile.get("onshore", []):
        if start <= direction <= end:
            return "ONSHORE"

    return "CROSS-SHORE"


# --------------------------------------------------
# SURF DECISION ENGINE
# --------------------------------------------------

def score_wave_size(height_ft):
    """
    Score based on suitability for a 6'8", ~52L softboard
    and a progressing surfer.

    This is deliberately conservative.
    """

    if height_ft is None:
        return 50

    if height_ft < 1.5:
        return 45

    if height_ft < 2.0:
        return 65

    if height_ft < 3.0:
        return 90

    if height_ft < 4.0:
        return 78

    if height_ft < 5.0:
        return 55

    if height_ft < 6.0:
        return 30

    return 10


def score_period(period_s):
    """
    Longer period generally means more organized wave energy.
    But very long-period swell can become powerful quickly.
    """

    if period_s is None:
        return 50

    if period_s < 7:
        return 40

    if period_s < 9:
        return 65

    if period_s < 12:
        return 90

    if period_s < 14:
        return 75

    return 55


def score_combination(height_ft, period_s):
    """
    Penalize combinations that can become disproportionately
    powerful for the current surfer profile.
    """

    if height_ft is None or period_s is None:
        return 50

    # Small + short period = weak/poor progression
    if height_ft < 2.0 and period_s < 8:
        return 45

    # Moderate size + moderate period = ideal zone
    if 2.0 <= height_ft < 4.0 and 8 <= period_s <= 12:
        return 90

    # Larger waves with longer periods become increasingly serious
    if height_ft >= 4.0 and period_s >= 12:
        return 40

    if height_ft >= 5.0:
        return 25

    return 65


def make_assessment(current):

    wave_height_ft = meters_to_feet(
        current.get("wave_height")
    )

    wave_height_range = None
    if wave_height_ft is not None:
        lower = int(wave_height_ft)
        upper = lower + 1
        wave_height_range = f"{lower}–{upper} FT"


    wave_direction = current.get("wave_direction")

    swell_height_ft = meters_to_feet(
        current.get("swell_wave_height")
    )
    swell_period_s = current.get(
        "swell_wave_period"
    )
    swell_direction = current.get(
        "swell_wave_direction"
    )

    wave_period_s = swell_period_s

    wind_speed_kmh = current.get(
        "wind_speed_10m"
    )

    wind_direction = current.get(
        "wind_direction_10m"
    )

    is_day = current.get("is_day")

    wind_speed_kt = (
        round(wind_speed_kmh * 0.539957, 1)
        if wind_speed_kmh is not None
        else None
    )

    wind_quality = classify_wind(
        wind_direction,
        VENICE.get("wind_profile", {})
    )

    if wind_speed_kt is None:
        wind_penalty = 0
    elif wind_speed_kt >= 16:
        wind_penalty = 100
    elif wind_quality == "OFFSHORE":
        if wind_speed_kt <= 7:
            wind_penalty = -5
        elif wind_speed_kt <= 11:
            wind_penalty = -2
        else:
            wind_penalty = 0
    elif wind_quality == "ONSHORE":
        if wind_speed_kt <= 4:
            wind_penalty = 5
        elif wind_speed_kt <= 7:
            wind_penalty = 12
        elif wind_speed_kt <= 11:
            wind_penalty = 20
        else:
            wind_penalty = 35
    else:
        if wind_speed_kt <= 4:
            wind_penalty = 0
        elif wind_speed_kt <= 7:
            wind_penalty = 3
        elif wind_speed_kt <= 11:
            wind_penalty = 8
        else:
            wind_penalty = 15

    if wind_speed_kt is None:
        wind_label = "UNKNOWN"
    elif wind_speed_kt >= 16:
        wind_label = "BLOWN TF OUT"
    elif wind_quality == "ONSHORE":
        wind_label = "ONSHORE"
    elif wind_quality == "OFFSHORE":
        wind_label = "OFFSHORE"
    else:
        wind_label = "CROSS-SHORE"

    size_score = score_wave_size(wave_height_ft)

    period_score = score_period(wave_period_s)

    combination_score = score_combination(
        wave_height_ft,
        wave_period_s
    )

    # Weighted score

    score = round(
        (size_score * 0.40)
        + (period_score * 0.25)
        + (combination_score * 0.35)
    )

    # Apply directional wind influence
    score = max(0, min(100, score - wind_penalty))

    # Hard safety limits

    if wind_speed_kt is not None and wind_speed_kt >= 22:
        status = "DANGEROUS"

    elif wave_height_ft is not None and wave_height_ft >= 7:
        status = "DANGEROUS"

    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 4
        and wave_period_s >= 16
    ):
        status = "DANGEROUS"

    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 3
        and wave_period_s >= 18
    ):
        status = "DANGEROUS"

    elif is_day == 0:
        status = "NO-GO"

    elif wind_speed_kt is not None and wind_speed_kt >= 16:
        status = "NO-GO"

    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 2
        and wave_period_s >= 16
    ):
        status = "NO-GO"

    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 3
        and wave_period_s >= 14
    ):
        status = "NO-GO"

    elif wave_height_ft is not None and wave_height_ft >= 6:
        status = "NO-GO"

    elif wave_height_ft is not None and wave_height_ft >= 5:
        status = "NO-GO"

    elif wave_height_ft is not None and wave_height_ft < 2:
        status = "NO-GO"

    elif score >= 75:
        status = "GO"

    else:
        status = "NO-GO"

    # Explanation
    if status == "DANGEROUS":
        if wind_speed_kt is not None and wind_speed_kt >= 22:
            reason = (
                "Wind conditions are severe enough to make the session unsafe."
            )
        elif wave_height_ft is not None and wave_height_ft >= 7:
            reason = (
                "Surf size has crossed into a dangerous range."
            )
        else:
            reason = (
                "Long-period swell is creating dangerous surf energy "
                "for the current wave height."
            )

    elif is_day == 0:
        reason = "It's dark. Check back after dawn."

    elif status == "NO-GO":
        reason_candidates = []

        if wind_speed_kt is not None and wind_speed_kt >= 16:
            reason_candidates.append((
                100,
                "Wind is too strong and is degrading the surface conditions."
            ))

        if (
            wave_height_ft is not None
            and wave_period_s is not None
            and (
                (wave_height_ft >= 2 and wave_period_s >= 16)
                or (wave_height_ft >= 3 and wave_period_s >= 14)
            )
        ):
            reason_candidates.append((
                90,
                "Long-period swell is making the surf more powerful "
                "than the wave height suggests."
            ))

        if wave_height_ft is not None and wave_height_ft >= 5:
            reason_candidates.append((
                95,
                "Wave size is outside the preferred range. "
                "The surf is too powerful for a productive session."
            ))

        if wave_height_ft is not None and wave_height_ft < 2:
            reason_candidates.append((
                75,
                "The surf is too small and lacks enough energy "
                "for a productive session."
            ))

        if wave_period_s is not None and wave_period_s < 8:
            reason_candidates.append((
                65,
                "The short period is limiting wave energy "
                "and consistency."
            ))

        if wind_quality == "ONSHORE":
            onshore_severity = (
                70 if wind_speed_kt is not None and wind_speed_kt >= 10
                else 60 if wind_speed_kt is not None and wind_speed_kt >= 7
                else 45
            )

            reason_candidates.append((
                onshore_severity,
                "Onshore wind is degrading the surface conditions."
            ))

        if reason_candidates:
            reason = max(
                reason_candidates,
                key=lambda item: item[0]
            )[1]
        else:
            reason = (
                "Conditions are outside the preferred range "
                "for a productive session."
            )
    elif status == "GO" and wind_quality == "OFFSHORE":
        reason = (
            "Offshore wind is helping clean up the surface, while wave size "
            "and energy remain within a workable range."
        )
    elif status == "GO" and wind_speed_kt is not None and wind_speed_kt <= 7:
        reason = (
            "Clean surface conditions with workable wave size and energy. "
            "Good conditions for a productive session."
        )
    elif status == "GO":
        reason = (
            "Wave size, energy, and surface conditions are workable. "
            "Good conditions for a productive session."
        )
    else:
        reason = (
            "Conditions are marginal. Wave size, energy, or surface quality "
            "is limiting the overall quality of the session."
        )

    return {
        "status": status,
        "score": score,
        "reason": reason,
        "wave_height_ft": wave_height_ft,
        "wave_height_range": wave_height_range,
        "wave_period_s": (
            round(wave_period_s, 1)
            if wave_period_s is not None
            else None
        ),
        "wave_direction": wave_direction,
        "swell_height_ft": (
            round(swell_height_ft, 1)
            if swell_height_ft is not None
            else None
        ),
        "swell_period_s": (
            round(swell_period_s, 1)
            if swell_period_s is not None
            else None
        ),
        "swell_direction": swell_direction,
        "swell_compass": compass_direction(swell_direction),
        "wave_compass": compass_direction(wave_direction),
        "size_score": size_score,
        "period_score": period_score,
        "combination_score": combination_score,
        "wind_speed_kt": wind_speed_kt,
        "wind_direction": wind_direction,
        "wind_compass": compass_direction(wind_direction),
        "wind_label": wind_label,
    }


def calculate_window(data, current_status, sunset_status=None):

    if current_status != "GO":
        return None

    daylight_minutes = None

    if sunset_status:
        daylight_minutes = sunset_status.get(
            "minutes_remaining"
        )

    if (
        daylight_minutes is not None
        and daylight_minutes < 30
    ):
        return None

    hourly = data.get("hourly", {})
    wind_hourly = data.get("wind_hourly", {})

    marine_times = hourly.get("time", [])
    wind_times = wind_hourly.get("time", [])

    if not marine_times or not wind_times:
        return None

    wind_index = {
        timestamp: index
        for index, timestamp in enumerate(wind_times)
    }

    current_time = data.get("current", {}).get("time")

    if not current_time:
        return None

    start_index = None

    for index, timestamp in enumerate(marine_times):
        if timestamp <= current_time:
            start_index = index
        else:
            break

    if start_index is None:
        return None

    hours = 0

    for index in range(start_index + 1, len(marine_times)):
        timestamp = marine_times[index]

        if timestamp not in wind_index:
            break

        wind_i = wind_index[timestamp]

        future = {
            "wave_height": hourly.get(
                "wave_height", [None]
            )[index],
            "wave_direction": hourly.get(
                "wave_direction", [None]
            )[index],
            "swell_wave_height": hourly.get(
                "swell_wave_height", [None]
            )[index],
            "swell_wave_direction": hourly.get(
                "swell_wave_direction", [None]
            )[index],
            "swell_wave_period": hourly.get(
                "swell_wave_period", [None]
            )[index],
            "wind_speed_10m": wind_hourly.get(
                "wind_speed_10m", [None]
            )[wind_i],
            "wind_direction_10m": wind_hourly.get(
                "wind_direction_10m", [None]
            )[wind_i],
            "is_day": wind_hourly.get(
                "is_day", [None]
            )[wind_i],
        }

        future_assessment = make_assessment(future)

        if future_assessment.get("status") != "GO":
            break

        hours += 1

    # Sunset always caps the usable session window.
    if (
        daylight_minutes is not None
        and daylight_minutes < 60
    ):
        return "<1 HR"

    if hours == 0:
        return "<1 HR"

    return "~1 HR+"

# --------------------------------------------------
# API
# --------------------------------------------------

def get_tide_data():
    params = {
        "product": "predictions",
        "application": "surf-intel",
        "station": NOAA_TIDE_STATION,
        "date": "today",
        "datum": "MLLW",
        "time_zone": "lst_ldt",
        "units": "english",
        "interval": "h",
        "format": "json",
    }

    try:
        response = requests.get(
            NOAA_TIDE_URL,
            params=params,
            timeout=10
        )
        response.raise_for_status()

        return response.json().get("predictions", [])

    except requests.RequestException:
        return []


def make_tide_summary(predictions):
    if len(predictions) < 2:
        return None

    from datetime import datetime

    now = datetime.now()

    points = []

    for prediction in predictions:
        try:
            timestamp = datetime.strptime(
                prediction["t"],
                "%Y-%m-%d %H:%M"
            )
            height = float(prediction["v"])
            points.append((timestamp, height))
        except (KeyError, ValueError, TypeError):
            continue

    if len(points) < 2:
        return None

    previous_point = None
    next_point = None

    for point in points:
        if point[0] <= now:
            previous_point = point
        elif point[0] > now:
            next_point = point
            break

    if previous_point is None:
        previous_point = points[0]

    if next_point is None:
        next_point = points[-1]

    previous_time, previous_height = previous_point
    next_time, next_height = next_point

    total_seconds = (
        next_time - previous_time
    ).total_seconds()

    if total_seconds > 0:
        elapsed_seconds = (
            now - previous_time
        ).total_seconds()

        progress = max(
            0,
            min(1, elapsed_seconds / total_seconds)
        )

        height = previous_height + (
            (next_height - previous_height) * progress
        )
    else:
        height = previous_height

    if next_height > previous_height:
        direction = "↑"
        trend = "RISING"
    elif next_height < previous_height:
        direction = "↓"
        trend = "FALLING"
    else:
        direction = "~"
        trend = "SLACK"

    return {
        "height_ft": round(height, 1),
        "direction": direction,
        "trend": trend,
    }


def get_marine_data():
    # Return cached data if it is still fresh.
    now = time.time()
    if (
        MARINE_CACHE["data"] is not None
        and now - MARINE_CACHE["timestamp"] < CACHE_TTL
    ):
        return MARINE_CACHE["data"]

    marine_params = {
        "latitude": VENICE["latitude"],
        "longitude": VENICE["longitude"],
        "hourly": HOURLY,
        "current": CURRENT,
        "timezone": "America/Los_Angeles",
        "forecast_days": 7,
    }

    marine_response = requests.get(
        OPEN_METEO_URL,
        params=marine_params,
        timeout=10
    )
    marine_response.raise_for_status()
    marine_data = marine_response.json()

    weather_params = {
        "latitude": VENICE["latitude"],
        "longitude": VENICE["longitude"],
        "current": "wind_speed_10m,wind_direction_10m,is_day",
        "hourly": "wind_speed_10m,wind_direction_10m,is_day,precipitation",
        "daily": "sunrise,sunset",
        "timezone": "America/Los_Angeles",
        "past_days": 3,
        "forecast_days": 2,
    }

    try:
        weather_response = requests.get(
            OPEN_METEO_WEATHER_URL,
            params=weather_params,
            timeout=10
        )
        weather_response.raise_for_status()
        weather_data = weather_response.json()
        marine_data["wind"] = weather_data.get("current", {})
        marine_data["wind_hourly"] = weather_data.get("hourly", {})
        marine_data["weather_daily"] = weather_data.get("daily", {})
    except requests.RequestException:
        marine_data["wind"] = {}
        marine_data["wind_hourly"] = {}
        marine_data["weather_daily"] = {}

    # Store the successful marine result.
    MARINE_CACHE["data"] = marine_data
    MARINE_CACHE["timestamp"] = now

    return marine_data


def get_rain_lockout(data):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    hourly = data.get("wind_hourly", {})
    times = hourly.get("time", [])
    precipitation = hourly.get("precipitation", [])

    if not times or not precipitation:
        return {
            "active": False,
            "last_rain": None,
            "safe_after": None,
        }

    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)
    last_rain = None

    for time_value, precip in zip(times, precipitation):
        if precip is None or precip <= 0:
            continue

        try:
            rain_time = datetime.fromisoformat(time_value)
            rain_time = rain_time.replace(tzinfo=pacific)
        except ValueError:
            continue

        # Ignore forecast rain that has not happened yet.
        if rain_time <= now:
            last_rain = rain_time

    if last_rain is None:
        return {
            "active": False,
            "last_rain": None,
            "safe_after": None,
        }

    safe_after = last_rain + timedelta(hours=72)

    return {
        "active": now < safe_after,
        "last_rain": last_rain.isoformat(),
        "safe_after": safe_after.strftime("%a %-I:%M %p").upper(),
    }


def get_is_daylight(data):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    daily = data.get("weather_daily", {})
    sunrise_times = daily.get("sunrise", [])
    sunset_times = daily.get("sunset", [])

    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)

    sunrise_dt = None
    sunset_dt = None

    for sunrise in sunrise_times:
        try:
            candidate = datetime.fromisoformat(sunrise)
            candidate = candidate.replace(tzinfo=pacific)
        except ValueError:
            continue

        if candidate.date() == now.date():
            sunrise_dt = candidate
            break

    for sunset in sunset_times:
        try:
            candidate = datetime.fromisoformat(sunset)
            candidate = candidate.replace(tzinfo=pacific)
        except ValueError:
            continue

        if candidate.date() == now.date():
            sunset_dt = candidate
            break

    if sunrise_dt is None or sunset_dt is None:
        return None

    return 1 if sunrise_dt <= now < sunset_dt else 0


def get_sunset_status(data):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    daily = data.get("weather_daily", {})
    sunset_times = daily.get("sunset", [])

    if not sunset_times:
        return {
            "sunset": None,
            "minutes_remaining": None,
        }

    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)

    for sunset in sunset_times:
        try:
            sunset_dt = datetime.fromisoformat(sunset)
            sunset_dt = sunset_dt.replace(tzinfo=pacific)
        except ValueError:
            continue

        if sunset_dt.date() != now.date():
            continue

        minutes_remaining = max(
            0,
            int((sunset_dt - now).total_seconds() // 60)
        )

        return {
            "sunset": sunset_dt.strftime("%-I:%M %p"),
            "minutes_remaining": minutes_remaining,
        }

    return {
        "sunset": None,
        "minutes_remaining": None,
    }

def get_next_sunrise(data):
    daily = data.get("weather_daily", {})
    sunrise_times = daily.get("sunrise", [])

    if not sunrise_times:
        return None

    from datetime import datetime
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)

    for sunrise in sunrise_times:
        try:
            sunrise_dt = datetime.fromisoformat(sunrise)
            sunrise_dt = sunrise_dt.replace(tzinfo=pacific)
        except ValueError:
            continue

        if sunrise_dt > now:
            return sunrise_dt.strftime("%-I:%M %p")

    return None


def get_dawn_patrol_forecast(data):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)

    daily = data.get("weather_daily", {})
    sunrise_times = daily.get("sunrise", [])
    marine_hourly = data.get("hourly", {})
    wind_hourly = data.get("wind_hourly", {})

    marine_times = marine_hourly.get("time", [])
    wind_times = wind_hourly.get("time", [])

    if not sunrise_times or not marine_times:
        return None

    sunrise_dt = None

    for sunrise in sunrise_times:
        try:
            candidate = datetime.fromisoformat(sunrise)
            candidate = candidate.replace(tzinfo=pacific)
        except ValueError:
            continue

        if candidate > now:
            sunrise_dt = candidate
            break

    if sunrise_dt is None:
        return None

    marine_index = min(
        range(len(marine_times)),
        key=lambda i: abs(
            (
                datetime.fromisoformat(marine_times[i]).replace(
                    tzinfo=pacific
                )
                - sunrise_dt
            ).total_seconds()
        )
    )

    wind_index = None

    if wind_times:
        wind_index = min(
            range(len(wind_times)),
            key=lambda i: abs(
                (
                    datetime.fromisoformat(wind_times[i]).replace(
                        tzinfo=pacific
                    )
                    - sunrise_dt
                ).total_seconds()
            )
        )

    forecast = {}

    marine_fields = [
        "wave_height",
        "wave_direction",
        "wave_period",
        "wave_peak_period",
        "wind_wave_height",
        "wind_wave_direction",
        "wind_wave_period",
        "swell_wave_height",
        "swell_wave_direction",
        "swell_wave_period",
        "secondary_swell_wave_height",
        "secondary_swell_wave_direction",
        "secondary_swell_wave_period",
        "tertiary_swell_wave_height",
        "tertiary_swell_wave_direction",
        "tertiary_swell_wave_period",
        "sea_surface_temperature",
    ]

    for field in marine_fields:
        values = marine_hourly.get(field, [])

        if marine_index < len(values):
            forecast[field] = values[marine_index]

    if wind_index is not None:
        wind_speeds = wind_hourly.get("wind_speed_10m", [])
        wind_directions = wind_hourly.get(
            "wind_direction_10m",
            []
        )

        if wind_index < len(wind_speeds):
            forecast["wind_speed_10m"] = wind_speeds[wind_index]

        if wind_index < len(wind_directions):
            forecast["wind_direction_10m"] = (
                wind_directions[wind_index]
            )

    forecast["is_day"] = 1

    assessment = make_assessment(forecast)

    if assessment["status"] == "DANGEROUS":
        outlook = "DANGEROUS"
    elif assessment["status"] == "GO":
        outlook = "LOOKING GOOD"
    else:
        outlook = "NOT LOOKING GOOD"

    return {
        "time": sunrise_dt.strftime("%-I:%M %p"),
        "outlook": outlook,
        "assessment": assessment,
        "forecast": forecast,
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        spot=VENICE,
        surfer=SURFER
    )


@app.route("/api/marine")
def marine():

    force_refresh = (
        request.args.get("refresh") == "1"
    )

    if force_refresh:
        MARINE_CACHE["data"] = None
        MARINE_CACHE["timestamp"] = 0

    data = get_marine_data()

    current = data.get("current", {})

    wind = data.get("wind", {})

    current["wind_speed_10m"] = wind.get(
        "wind_speed_10m"
    )

    current["wind_direction_10m"] = wind.get(
        "wind_direction_10m"
    )

    solar_is_day = get_is_daylight(data)

    if solar_is_day is not None:
        current["is_day"] = solar_is_day
    else:
        current["is_day"] = wind.get("is_day")

    assessment = make_assessment(current)
    dawn_patrol = get_next_sunrise(data)
    dawn_forecast = get_dawn_patrol_forecast(data)
    sunset_status = get_sunset_status(data)
    rain_lockout = get_rain_lockout(data)

    if (
        rain_lockout["active"]
        and assessment["status"] != "DANGEROUS"
    ):
        assessment["status"] = "NO-GO"
        assessment["reason"] = (
            "Recent rain. Water quality risk. "
            "Safe after: " + rain_lockout["safe_after"] + "."
        )

    if (
        assessment["status"] != "DANGEROUS"
        and current.get("is_day") == 1
        and sunset_status["minutes_remaining"] is not None
        and sunset_status["minutes_remaining"] < 30
    ):
        assessment["status"] = "NO-GO"
        assessment["reason"] = (
            "Sunset is too close for a productive session. "
            "Only "
            + str(sunset_status["minutes_remaining"])
            + " minutes of daylight remain."
        )

    window = calculate_window(
        data,
        assessment.get("status"),
        sunset_status
    )

    tide = make_tide_summary(
        get_tide_data()
    )

    water_temp_c = current.get(
        "sea_surface_temperature"
    )

    return jsonify({
        "spot": VENICE,
        "surfer": SURFER,
        "assessment": assessment,
        "dawn_patrol": dawn_patrol,
        "dawn_forecast": dawn_forecast,
        "sunset": sunset_status,
        "rain_lockout": rain_lockout,
        "window": window,
        "tide": tide,
        "water_temperature_f": celsius_to_fahrenheit(
            water_temp_c
        ),
        "current": current,
        "wind": wind,
    })


# --------------------------------------------------
# RUN
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
